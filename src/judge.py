"""Cascade de jugement : verdict par niveaux, traçable.

Séparée de l'inférence pour être rejouable : ajuster un seuil ou le prompt du
juge ne doit pas coûter une seconde d'inférence. Deux taux sont produits, strict
et permissif ; l'écart entre les deux, ventilé par match_method, est un résultat
en soi plutôt qu'un biais caché.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src.io_utils import atomic_write_dataframe
from src.llm_client import LLMClient
from src.scoring import (
    boolean_label,
    fuzzy_score,
    matches_single_choice,
    normalize,
    resolve_choice_reference,
)

STRICT_METHODS = {"boolean", "exact", "choice_letter", "choice_match"}

JUDGMENT_COLUMNS = [
    "question_id",
    "model_slug",
    "prompt_variant",
    "ai_answer_norm",
    "match_method",
    "ai_correct_strict",
    "ai_correct",
    "fuzzy_score",
    "judge_model",
    "judgment_version",
    "judged_at",
]

# Imposés à l'écriture : une partition entièrement en erreur ne porte que des
# None, parquet typerait alors ai_correct en `null` et la lecture du dataset
# échouerait dès qu'une autre partition la type `bool`.
JUDGMENT_DTYPES = {
    "question_id": "string",
    "model_slug": "string",
    "prompt_variant": "string",
    "ai_answer_norm": "string",
    "match_method": "string",
    "ai_correct_strict": "boolean",
    "ai_correct": "boolean",
    "fuzzy_score": "float64",
    "judge_model": "string",
    "judgment_version": "string",
    "judged_at": "string",
}

_JUDGE_SYSTEM = (
    "You decide whether a candidate answer means the same as the reference answer "
    "for a trivia question. Reply with exactly one word: YES or NO."
)


class LLMJudge:
    def __init__(self, client: Any = None) -> None:
        self.client = client or LLMClient(config.JUDGE_MODEL_NAME)

    def is_equivalent(self, question: str, expected: str, given: str) -> bool:
        prompt = (
            f"Question: {question}\n"
            f"Reference answer: {expected}\n"
            f"Candidate answer: {given}\n"
            "Does the candidate answer mean the same as the reference answer? YES or NO."
        )
        result = self.client.complete(_JUDGE_SYSTEM, prompt)
        if result.status != "ok":
            return False
        return normalize(result.text).split(" ")[0] == "yes"


def _verdict(method: str, correct: bool, answer_norm: str, score: float) -> dict[str, Any]:
    return {
        "match_method": method,
        "ai_correct_strict": correct if method in STRICT_METHODS else False,
        "ai_correct": correct,
        "ai_answer_norm": answer_norm,
        "fuzzy_score": score,
    }


def _resolve_reference(given: str, choices: list[str], answer_is_numeric: bool) -> str | None:
    """Étage choice_letter, résolution par rang neutralisée sur les numériques.

    `resolve_choice_reference` accepte aussi bien « B » que « 3 ». Face à une
    question dont la bonne réponse est numérique, « 3 » est très probablement
    la réponse elle-même et non un rang : le résoudre en troisième option
    rendrait un verdict correct par accident si la position coïncide. Une
    référence par rang porte toujours un chiffre, une référence par lettre
    jamais — le test sur les chiffres sépare donc exactement les deux branches.
    """
    if answer_is_numeric and any(char.isdigit() for char in given):
        return None
    return resolve_choice_reference(given, choices)


def judge_one(
    answer_row: Mapping[str, Any],
    question_row: Mapping[str, Any],
    llm_judge: Any = None,
) -> dict[str, Any]:
    if str(answer_row.get("status", "ok")) != "ok":
        return {
            "match_method": "error",
            "ai_correct_strict": None,
            "ai_correct": None,
            "ai_answer_norm": "",
            "fuzzy_score": 0.0,
        }

    given = str(answer_row.get("ai_answer") or "")
    answer_norm = normalize(given)
    expected_norm = str(question_row["correct_answer_norm"])
    choices = list(question_row["choices"])
    answer_is_numeric = bool(question_row.get("answer_is_numeric"))

    if not answer_norm:
        return _verdict("no_match", False, answer_norm, 0.0)

    if question_row["type"] == "boolean":
        given_bool = boolean_label(given)
        expected_bool = boolean_label(str(question_row["correct_answer"]))
        if given_bool is not None and expected_bool is not None:
            return _verdict("boolean", given_bool is expected_bool, answer_norm, 0.0)

    if answer_norm == expected_norm:
        return _verdict("exact", True, answer_norm, 100.0)

    referenced = _resolve_reference(given, choices, answer_is_numeric)
    if referenced is not None:
        return _verdict("choice_letter", normalize(referenced) == expected_norm, answer_norm, 0.0)

    single = matches_single_choice(answer_norm, choices)
    if single is not None:
        return _verdict("choice_match", normalize(single) == expected_norm, answer_norm, 0.0)

    score = fuzzy_score(answer_norm, expected_norm)
    if not answer_is_numeric and score >= config.FUZZY_RATIO_THRESHOLD:
        return _verdict("fuzzy", True, answer_norm, score)

    if llm_judge is not None:
        equivalent = llm_judge.is_equivalent(
            str(question_row["question"]), str(question_row["correct_answer"]), given
        )
        if equivalent:
            return _verdict("llm_judge", True, answer_norm, score)

    return _verdict("no_match", False, answer_norm, score)


def run_judge(model_slug: str | None = None, llm_judge: Any = None) -> list[Path]:
    if not config.SILVER_QUESTIONS.exists():
        raise FileNotFoundError(f"Silver introuvable: {config.SILVER_QUESTIONS}")

    questions = pd.read_parquet(config.SILVER_QUESTIONS).set_index("question_id", drop=False)
    pattern = f"model={model_slug}" if model_slug else "model=*"
    written: list[Path] = []

    for variant_dir in sorted(config.SILVER_ANSWERS_DIR.glob(f"{pattern}/prompt_variant=*")):
        parts = sorted(variant_dir.glob("part-*.parquet"))
        if not parts:
            continue
        # Relus un par un : les clés de partition du chemin portent les mêmes
        # noms que des colonnes du fichier, l'inférence hive échouerait.
        answers = pd.concat([pd.read_parquet(part) for part in parts], ignore_index=True)
        slug = variant_dir.parent.name.split("=", 1)[1]
        variant_id = variant_dir.name.split("=", 1)[1]

        records = []
        now = datetime.now(timezone.utc).isoformat()
        for answer_row in tqdm(
            answers.to_dict(orient="records"), desc=f"{slug}/{variant_id}", unit="a"
        ):
            question_row = questions.loc[answer_row["question_id"]]
            verdict = judge_one(answer_row, question_row, llm_judge)
            records.append(
                {
                    "question_id": answer_row["question_id"],
                    "model_slug": slug,
                    "prompt_variant": variant_id,
                    "judge_model": config.JUDGE_MODEL_NAME,
                    "judgment_version": config.JUDGMENT_VERSION,
                    "judged_at": now,
                    **verdict,
                }
            )

        target = config.SILVER_JUDGMENTS_DIR / f"model={slug}" / f"prompt_variant={variant_id}"
        path = target / f"judgments-{config.JUDGMENT_VERSION}.parquet"
        frame = pd.DataFrame(records, columns=JUDGMENT_COLUMNS).astype(JUDGMENT_DTYPES)
        atomic_write_dataframe(frame, path, "parquet")
        written.append(path)

    print(f"[judge] {len(written)} partitions écrites sous {config.SILVER_JUDGMENTS_DIR}")
    return written


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Jugement des réponses silver.")
    parser.add_argument("--model-slug", default=None)
    parser.add_argument(
        "--no-llm-judge", action="store_true", help="Cascade sans le dernier niveau."
    )
    args = parser.parse_args()
    run_judge(args.model_slug, None if args.no_llm_judge else LLMJudge())


if __name__ == "__main__":
    main()

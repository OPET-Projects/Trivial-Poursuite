"""Fabrique un silver de test pour exercer le projet dbt.

Les questions et les réponses sont synthétiques, mais les jugements sont
produits par le vrai `run_judge` : le schéma de `judgments` correspond donc
exactement à ce que le pipeline écrira en production, et les verdicts suivent
la vraie cascade plutôt qu'une imitation.

Le jeu couvre délibérément les cas qui rendent les marts interprétables — sans
eux, les agrégats sont dégénérés et un mart peut compiler en mentant :

- quatre catégories et trois difficultés, sinon `mart_performance_by_category`
  et `mart_performance_by_difficulty` tiennent sur une ligne par modèle ;
- les quatre positions possibles de bonne réponse, sinon `mart_position_bias`
  ne peut pas montrer de biais ;
- deux `model_slug` et trois `prompt_variant`, qui exercent réellement la garde
  anti-collision hive des sources dbt ;
- une réponse vide avec `status = 'ok'` (troncature du raisonnement) et une
  réponse en `status = 'error'` (panne de transport) : deux choses distinctes
  que les marts doivent compter séparément ;
- le piège du rang numérique, neutralisé par la garde de la tâche 11 ;
- une réponse lettrée en mode ouvert, où le modèle n'a jamais vu les options et
  tombe juste par accident — le biais que `mart_matching_impact` doit exposer ;
- un appel de chauffe, pour que les clauses `filter (where not is_warmup)`
  soient réellement exercées ;
- les trois issues de l'arbitre LLM — accepté, refusé, illisible — rendues
  par un juge scripté, pour que `llm_judge`, `llm_judge_rejected` et
  `llm_judge_failed` traversent réellement le contrôle d'énumération de dbt.

Usage :
    python tests/fixtures/make_fixture_silver.py [--force]

Le générateur écrit aux chemins canoniques du silver, parce que `run_judge` les
lit depuis `config`. Il refuse donc d'écraser un silver existant sans `--force`,
pour ne pas détruire un run de production qui a coûté des heures.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src.enrich_llm import ANSWER_COLUMNS, _ANSWER_DTYPES
from src.io_utils import atomic_write_dataframe
from src.judge import JUDGE_FAILED, JUDGE_NO, JUDGE_YES, run_judge
from src.scoring import normalize
from src.transform_silver import SILVER_COLUMNS

# (id, categorie, type, difficulte, question, correct, choices, position, numeric)
QUESTIONS = [
    ("q1", "General Knowledge", "multiple", "easy",
     "Who painted the Mona Lisa?", "Leonardo da Vinci",
     ["Raphael", "Leonardo da Vinci", "Titian", "Donatello"], 1, False),
    ("q2", "Science & Nature", "boolean", "easy",
     "The Earth is flat.", "False", ["True", "False"], 1, False),
    ("q3", "History", "multiple", "hard",
     "In which year did the French Revolution begin?", "1789",
     ["1789", "1798", "1801", "1812"], 0, True),
    ("q4", "Geography", "multiple", "medium",
     "What is the capital of Australia?", "Canberra",
     ["Sydney", "Melbourne", "Canberra", "Perth"], 2, False),
    ("q5", "Science & Nature", "multiple", "easy",
     "Which planet is known as the Red Planet?", "Mars",
     ["Venus", "Jupiter", "Mercury", "Mars"], 3, False),
    ("q6", "Science & Nature", "boolean", "medium",
     "Water boils at 100 degrees Celsius at sea level.", "True",
     ["False", "True"], 1, False),
]

# (question_id, ai_answer, status, finish_reason, response_time, completion_tokens)
BASE_ANSWERS = {
    "p1_constrained_mcq": [
        # Lettre juste : la cascade doit conclure en choice_letter.
        ("q1", "B", "ok", "eosFound", 12.4, 180),
        ("q2", "B", "ok", "eosFound", 8.1, 96),
        # Piège du rang numérique : la garde de la tâche 11 doit refuser de
        # résoudre "3" en troisième option sur une question à réponse numérique.
        ("q3", "3", "ok", "eosFound", 19.6, 254),
        # Troncature : l'appel aboutit, la réponse est vide. N'est pas une erreur.
        ("q4", "", "ok", "maxPredictedTokensReached", 18.9, 256),
        ("q5", "D", "ok", "eosFound", 11.2, 164),
        ("q6", "A", "ok", "eosFound", 7.9, 88),
    ],
    "p2_open_minimal": [
        ("q1", "Leonardo da Vinci", "ok", "eosFound", 13.7, 201),
        ("q2", "False", "ok", "eosFound", 9.4, 104),
        ("q3", "1789", "ok", "eosFound", 15.1, 223),
        # Panne de transport : sort du dénominateur, alimente mart_errors.
        ("q4", "", "error", "", 0.4, None),
        ("q5", "Mars", "ok", "eosFound", 10.8, 158),
        ("q6", "true", "ok", "eosFound", 8.6, 92),
    ],
    "p3_open_guided": [
        # Variante de nom : rattrapée par l'étage fuzzy.
        ("q1", "da Vinci", "ok", "eosFound", 14.2, 210),
        ("q2", "False", "ok", "eosFound", 9.1, 101),
        # Faux proche : le fuzzy est désactivé sur réponse numérique, donc
        # no_match, et c'est le comportement voulu — 1789 et 1798 sont deux
        # réponses différentes malgré un score de similarité élevé.
        ("q3", "1798", "ok", "eosFound", 16.4, 238),
        ("q4", "Canberra", "ok", "eosFound", 12.9, 186),
        # Réponse lettrée en mode ouvert : le modèle n'a jamais vu les options,
        # mais la cascade résout "D" en quatrième option et tombe juste par
        # accident. C'est le biais que mart_matching_impact doit rendre visible.
        ("q5", "D", "ok", "eosFound", 11.9, 172),
        ("q6", "True", "ok", "eosFound", 8.8, 95),
    ],
}

# Écarts par modèle, pour que les deux ne rendent pas des marts identiques.
# (variante, question_id) -> (ai_answer, status, finish_reason)
OVERRIDES = {
    "google_gemma-3-12b": {
        # Faux hors des options, qu'aucun étage lexical ne tranche : l'arbitre le refuse.
        ("p3_open_guided", "q4"): ("Brisbane", "ok", "eosFound"),
    },
    "liquid_lfm2-24b-a2b": {
        ("p1_constrained_mcq", "q5"): ("A", "ok", "eosFound"),
        ("p1_constrained_mcq", "q1"): ("C", "ok", "eosFound"),
        ("p2_open_minimal", "q1"): ("Leonardo", "ok", "eosFound"),
        ("p3_open_guided", "q4"): ("", "ok", "maxPredictedTokensReached"),
        # Paraphrase juste qu'aucun étage lexical ne rattrape : l'arbitre l'accepte.
        ("p3_open_guided", "q1"): ("The artist from Vinci", "ok", "eosFound"),
    },
}

MODELS = [
    ("google/gemma-3-12b", "google_gemma-3-12b", "poste-1", "Apple M1 Pro"),
    ("liquid/lfm2-24b-a2b", "liquid_lfm2-24b-a2b", "poste-2", "Apple M3 Max"),
]

# Un seul appel de chauffe par partition, pour exercer les clauses is_warmup
# sans vider les agrégats de latence.
WARMUP = ("p1_constrained_mcq", "q1")


def _silver_exists() -> bool:
    return config.SILVER_QUESTIONS.exists() or any(
        directory.exists() and any(directory.iterdir())
        for directory in (config.SILVER_ANSWERS_DIR, config.SILVER_JUDGMENTS_DIR)
    )


def clear_silver() -> None:
    """Repart d'un silver vide, pour que le jeu écrit soit le jeu lu."""
    config.SILVER_QUESTIONS.unlink(missing_ok=True)
    for directory in (config.SILVER_ANSWERS_DIR, config.SILVER_JUDGMENTS_DIR):
        if directory.exists():
            shutil.rmtree(directory)


def build_questions() -> None:
    rows = []
    for qid, category, qtype, difficulty, question, correct, choices, position, numeric in QUESTIONS:
        rows.append({
            "question_id": qid,
            "category": category,
            "category_group": category.split(":")[0].strip(),
            "category_name": category,
            "type": qtype,
            "difficulty": difficulty,
            "question": question,
            "correct_answer": correct,
            "incorrect_answers": [choice for choice in choices if choice != correct],
            "choices": choices,
            "correct_answer_position": position,
            "n_choices": len(choices),
            "correct_answer_norm": correct.casefold(),
            "answer_is_numeric": numeric,
            "question_len": len(question),
            "answer_len": len(correct),
            "cleaned_at": "2026-09-11T00:00:00",
        })
    frame = pd.DataFrame(rows, columns=SILVER_COLUMNS)
    atomic_write_dataframe(frame, config.SILVER_QUESTIONS, "parquet")
    print(f"[fixture] questions -> {config.SILVER_QUESTIONS} ({len(frame)} lignes)")


def build_answers() -> None:
    for model_name, slug, host, hardware in MODELS:
        overrides = OVERRIDES.get(slug, {})
        for variant, specs in BASE_ANSWERS.items():
            rows = []
            for qid, answer, status, finish, elapsed, tokens in specs:
                answer, status, finish = overrides.get((variant, qid), (answer, status, finish))
                ok = status == "ok"
                rows.append({
                    "question_id": qid,
                    "model_slug": slug,
                    "prompt_variant": variant,
                    "model_name": model_name,
                    "prompt_hash": "abc123def456",
                    "prompt_text": "Question: ...\nAnswer:",
                    "raw_answer": answer,
                    "ai_answer": answer,
                    "response_time": elapsed,
                    "prompt_tokens": 31 if ok else None,
                    "completion_tokens": tokens if ok else None,
                    "finish_reason": finish,
                    "status": status,
                    "error": "" if ok else "RuntimeError: backend down",
                    "attempt": 1,
                    "is_warmup": (variant, qid) == WARMUP,
                    "answered_at": "2026-09-11T00:00:00",
                    "run_id": f"run-fixture-{slug}",
                    "host": host,
                    "hardware": hardware,
                    "os_version": "Darwin 25.6.0",
                    "python_version": "3.12",
                    "runtime_version": "lmstudio-1.5.0",
                    "temperature": 0.0,
                    "max_tokens": 256,
                })
            frame = pd.DataFrame(rows, columns=ANSWER_COLUMNS).astype(_ANSWER_DTYPES)
            target = config.SILVER_ANSWERS_DIR / f"model={slug}" / f"prompt_variant={variant}"
            atomic_write_dataframe(frame, target / "part-fixture-0.parquet", "parquet")
            print(f"[fixture] answers -> {target} ({len(frame)} lignes)")


class ScriptedJudge:
    """Arbitre déterministe : une issue fixée par réponse résiduelle.

    Le vrai juge demanderait LM Studio. Celui-ci rend les trois issues possibles
    sur les résidus du jeu, et « non » pour tout résidu imprévu.
    """

    OUTCOMES = {
        "artist from vinci": JUDGE_YES,
        "brisbane": JUDGE_NO,
        # Rang neutralisé sur question numérique : l'arbitre rend un verdict
        # illisible, comme un juge tronqué.
        "3": JUDGE_FAILED,
    }

    def verdict(self, question: str, expected: str, given: str) -> str:
        return self.OUTCOMES.get(normalize(given), JUDGE_NO)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="écraser un silver existant (protège un run de production)",
    )
    args = parser.parse_args(argv)

    if _silver_exists() and not args.force:
        print(
            "[fixture] refus : le silver contient déjà des données.\n"
            "          Relancer avec --force pour les écraser.",
            file=sys.stderr,
        )
        return 1

    clear_silver()
    build_questions()
    build_answers()
    # Jugements produits par le vrai code, avec un arbitre scripté à la place du
    # modèle : le jeu reste déterministe et n'exige pas LM Studio.
    written = run_judge(llm_judge=ScriptedJudge())
    print(f"[fixture] judgments -> {len(written)} partitions")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

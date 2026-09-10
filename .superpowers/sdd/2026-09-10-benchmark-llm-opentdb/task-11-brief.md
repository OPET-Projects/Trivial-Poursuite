### Task 11: Cascade de jugement

**Files:**
- Create: `src/judge.py`
- Test: `tests/test_judge.py`

**Interfaces:**
- Consumes: `src.scoring`, `src.llm_client`, `src.io_utils`, `config`.
- Produces: `judge_one(answer_row: Mapping, question_row: Mapping, llm_judge=None) -> dict`, `run_judge(model_slug: str | None = None, llm_judge=None) -> list[Path]`, `LLMJudge(client)` avec `is_equivalent(question: str, expected: str, given: str) -> bool`.

Le dictionnaire renvoyé par `judge_one` contient `match_method`, `ai_correct_strict`, `ai_correct`, `fuzzy_score`, `ai_answer_norm`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_judge.py` :

```python
import pandas as pd
import pytest

from src.judge import LLMJudge, judge_one, run_judge


def question(**overrides):
    row = {
        "question_id": "q1",
        "question": "Who painted the Mona Lisa?",
        "type": "multiple",
        "correct_answer": "Leonardo da Vinci",
        "correct_answer_norm": "leonardo da vinci",
        "choices": ["Raphael", "Leonardo da Vinci", "Titian", "Donatello"],
        "answer_is_numeric": False,
    }
    row.update(overrides)
    return row


def answer(text, status="ok"):
    return {"question_id": "q1", "ai_answer": text, "status": status}


class AlwaysYesJudge:
    def __init__(self):
        self.calls = 0

    def is_equivalent(self, question, expected, given):
        self.calls += 1
        return True


class AlwaysNoJudge:
    def __init__(self):
        self.calls = 0

    def is_equivalent(self, question, expected, given):
        self.calls += 1
        return False


def test_error_status_is_excluded():
    verdict = judge_one(answer("", status="error"), question())
    assert verdict["match_method"] == "error"
    assert verdict["ai_correct"] is None
    assert verdict["ai_correct_strict"] is None


def test_exact_match():
    verdict = judge_one(answer("Leonardo da Vinci"), question())
    assert verdict["match_method"] == "exact"
    assert verdict["ai_correct_strict"] is True
    assert verdict["ai_correct"] is True


def test_exact_match_ignores_case_and_articles():
    verdict = judge_one(answer("the beatles"), question(correct_answer="The Beatles",
                                                        correct_answer_norm="beatles"))
    assert verdict["match_method"] == "exact"


def test_boolean_true_false():
    row = question(type="boolean", correct_answer="True", correct_answer_norm="true",
                   choices=["True", "False"])
    assert judge_one(answer("True"), row)["match_method"] == "boolean"
    assert judge_one(answer("True"), row)["ai_correct"] is True
    assert judge_one(answer("False"), row)["ai_correct"] is False


def test_choice_letter_is_resolved():
    verdict = judge_one(answer("B"), question())
    assert verdict["match_method"] == "choice_letter"
    assert verdict["ai_correct"] is True


def test_choice_letter_pointing_elsewhere_is_wrong():
    verdict = judge_one(answer("A"), question())
    assert verdict["match_method"] == "choice_letter"
    assert verdict["ai_correct"] is False


def test_fuzzy_rescues_a_name_variant():
    judge = AlwaysNoJudge()
    verdict = judge_one(answer("Da Vinci"), question(), llm_judge=judge)
    assert verdict["match_method"] == "fuzzy"
    assert verdict["ai_correct"] is True
    assert verdict["ai_correct_strict"] is False
    assert judge.calls == 0


def test_fuzzy_is_disabled_for_numeric_answers():
    row = question(correct_answer="1789", correct_answer_norm="1789", answer_is_numeric=True,
                   choices=["1789", "1798", "1801", "1812"])
    judge = AlwaysNoJudge()
    verdict = judge_one(answer("1798"), row, llm_judge=judge)
    assert verdict["match_method"] != "fuzzy"
    assert verdict["ai_correct"] is False


def test_llm_judge_is_the_last_resort():
    judge = AlwaysYesJudge()
    verdict = judge_one(answer("the painter from Vinci"), question(), llm_judge=judge)
    assert verdict["match_method"] == "llm_judge"
    assert verdict["ai_correct"] is True
    assert verdict["ai_correct_strict"] is False
    assert judge.calls == 1


def test_no_match_when_judge_declines():
    verdict = judge_one(answer("Pablo Picasso"), question(), llm_judge=AlwaysNoJudge())
    assert verdict["match_method"] == "no_match"
    assert verdict["ai_correct"] is False


def test_permissive_never_below_strict():
    for text in ["Leonardo da Vinci", "Da Vinci", "B", "Pablo Picasso"]:
        verdict = judge_one(answer(text), question(), llm_judge=AlwaysNoJudge())
        if verdict["ai_correct_strict"]:
            assert verdict["ai_correct"]


def test_llm_judge_parses_yes_and_no():
    class Stub:
        def __init__(self, reply):
            self.reply = reply

        def complete(self, system_prompt, user_prompt):
            from src.llm_client import LLMResult

            return LLMResult(self.reply, self.reply, 0.1, None, None, "stop", "ok", "", 1)

    assert LLMJudge(Stub("YES")).is_equivalent("q", "a", "b") is True
    assert LLMJudge(Stub("no")).is_equivalent("q", "a", "b") is False
    assert LLMJudge(Stub("perhaps")).is_equivalent("q", "a", "b") is False


def test_run_judge_writes_partitions(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "SILVER_ANSWERS_DIR", tmp_path / "answers")
    monkeypatch.setattr(config, "SILVER_JUDGMENTS_DIR", tmp_path / "judgments")
    monkeypatch.setattr(config, "SILVER_QUESTIONS", tmp_path / "questions.parquet")

    pd.DataFrame([question()]).to_parquet(tmp_path / "questions.parquet", index=False)
    part = tmp_path / "answers" / "model=m1" / "prompt_variant=p1_constrained_mcq"
    part.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "question_id": "q1",
                "model_slug": "m1",
                "prompt_variant": "p1_constrained_mcq",
                "ai_answer": "Leonardo da Vinci",
                "status": "ok",
            }
        ]
    ).to_parquet(part / "part-001.parquet", index=False)

    run_judge()
    judged = pd.read_parquet(tmp_path / "judgments")
    assert len(judged) == 1
    assert judged.loc[0, "match_method"] == "exact"
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_judge.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'src.judge'`.

- [ ] **Step 3: Implémenter**

Créer `src/judge.py` :

```python
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

    if not answer_norm:
        return _verdict("no_match", False, answer_norm, 0.0)

    if question_row["type"] == "boolean":
        given_bool = boolean_label(given)
        expected_bool = boolean_label(str(question_row["correct_answer"]))
        if given_bool is not None and expected_bool is not None:
            return _verdict("boolean", given_bool is expected_bool, answer_norm, 0.0)

    if answer_norm == expected_norm:
        return _verdict("exact", True, answer_norm, 100.0)

    referenced = resolve_choice_reference(given, choices)
    if referenced is not None:
        return _verdict("choice_letter", normalize(referenced) == expected_norm, answer_norm, 0.0)

    single = matches_single_choice(answer_norm, choices)
    if single is not None:
        return _verdict("choice_match", normalize(single) == expected_norm, answer_norm, 0.0)

    score = fuzzy_score(answer_norm, expected_norm)
    if not bool(question_row.get("answer_is_numeric")) and score >= config.FUZZY_RATIO_THRESHOLD:
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
        answers = pd.concat([pd.read_parquet(part) for part in parts], ignore_index=True)
        slug = variant_dir.parent.name.split("=", 1)[1]
        variant_id = variant_dir.name.split("=", 1)[1]

        records = []
        now = datetime.now(timezone.utc).isoformat()
        for answer_row in tqdm(answers.to_dict(orient="records"), desc=f"{slug}/{variant_id}", unit="a"):
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
        atomic_write_dataframe(pd.DataFrame(records, columns=JUDGMENT_COLUMNS), path, "parquet")
        written.append(path)

    print(f"[judge] {len(written)} partitions écrites sous {config.SILVER_JUDGMENTS_DIR}")
    return written


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Jugement des réponses silver.")
    parser.add_argument("--model-slug", default=None)
    parser.add_argument("--no-llm-judge", action="store_true", help="Cascade sans le dernier niveau.")
    args = parser.parse_args()
    run_judge(args.model_slug, None if args.no_llm_judge else LLMJudge())


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_judge.py -v`
Expected: PASS, 15 tests.

---


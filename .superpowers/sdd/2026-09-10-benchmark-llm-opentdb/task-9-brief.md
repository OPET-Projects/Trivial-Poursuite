### Task 9: Inférence reprenable et partitionnée

Cœur du plan. Corrige la clé de reprise, l'écriture quadratique, l'absence de provenance et le traitement des erreurs.

**Files:**
- Modify: `src/enrich_llm.py` (réécriture complète)
- Test: `tests/test_enrich_llm.py`

**Interfaces:**
- Consumes: `src.prompts`, `src.llm_client`, `src.runmeta`, `src.io_utils`, `config`.
- Produces: `partition_dir(model_slug: str, variant_id: str) -> Path`, `load_done_keys(model_slug: str, variant_id: str) -> set[str]`, `select_pending(questions: pandas.DataFrame, done: set[str], shard: tuple[int, int] | None) -> pandas.DataFrame`, `stratified_sample(df, sample_size: int, seed: int) -> pandas.DataFrame`, `run_enrich(model_name: str, variant_ids: list[str], sample_size: int = 0, shard: tuple[int, int] | None = None, client=None) -> list[Path]`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_enrich_llm.py` :

```python
import pandas as pd
import pytest

from src.enrich_llm import (
    load_done_keys,
    partition_dir,
    run_enrich,
    select_pending,
    stratified_sample,
)
from src.llm_client import LLMResult


@pytest.fixture
def questions():
    rows = []
    for i in range(6):
        rows.append(
            {
                "question_id": f"q{i:04d}",
                "category": "Art",
                "category_group": "General",
                "category_name": "Art",
                "type": "multiple",
                "difficulty": ["easy", "medium", "hard"][i % 3],
                "question": f"Question {i}?",
                "correct_answer": "A",
                "incorrect_answers": ["B", "C", "D"],
                "choices": ["B", "A", "C", "D"],
                "correct_answer_position": 1,
                "n_choices": 4,
                "correct_answer_norm": "a",
                "answer_is_numeric": False,
                "question_len": 12,
                "answer_len": 1,
                "cleaned_at": "2026-09-10T10:00:00+00:00",
            }
        )
    return pd.DataFrame(rows)


class StubClient:
    def __init__(self, model_name="fake/model", failures=()):
        self.model_name = model_name
        self.calls = []
        self.failures = set(failures)

    def complete(self, system_prompt, user_prompt):
        self.calls.append(user_prompt)
        index = len(self.calls) - 1
        if index in self.failures:
            return LLMResult("", "", 0.1, None, None, "", "error", "boom", 3)
        return LLMResult("A", "A", 0.2, 30, 2, "stop", "ok", "", 1)


@pytest.fixture(autouse=True)
def silver_dirs(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "SILVER_ANSWERS_DIR", tmp_path / "answers")
    monkeypatch.setattr(config, "SILVER_RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(config, "SILVER_QUESTIONS", tmp_path / "questions.parquet")
    monkeypatch.setattr(config, "FLUSH_EVERY", 2)
    return tmp_path


def test_partition_path_encodes_model_and_variant():
    path = partition_dir("google_gemma", "p1_constrained_mcq")
    assert path.parts[-2:] == ("model=google_gemma", "prompt_variant=p1_constrained_mcq")


def test_two_models_do_not_share_a_partition():
    assert partition_dir("a", "p1_constrained_mcq") != partition_dir("b", "p1_constrained_mcq")


def test_select_pending_excludes_done_keys(questions):
    pending = select_pending(questions, {"q0000", "q0001"}, None)
    assert set(pending["question_id"]) == {"q0002", "q0003", "q0004", "q0005"}


def test_select_pending_shards_deterministically(questions):
    first = select_pending(questions, set(), (0, 3))
    second = select_pending(questions, set(), (1, 3))
    third = select_pending(questions, set(), (2, 3))
    ids = set(first["question_id"]) | set(second["question_id"]) | set(third["question_id"])
    assert ids == set(questions["question_id"])
    assert not set(first["question_id"]) & set(second["question_id"])


def test_run_enrich_writes_one_partition_per_variant(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq", "p2_open_minimal"], client=StubClient())
    frame = pd.read_parquet(silver_dirs / "answers")
    assert len(frame) == 12
    assert set(frame["prompt_variant"]) == {"p1_constrained_mcq", "p2_open_minimal"}


def test_run_enrich_flushes_several_parts(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    parts = list(partition_dir("fake_model", "p1_constrained_mcq").glob("part-*.parquet"))
    assert len(parts) == 3


def test_rerun_is_idempotent(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    second_client = StubClient()
    run_enrich("fake/model", ["p1_constrained_mcq"], client=second_client)
    assert second_client.calls == []
    assert len(pd.read_parquet(silver_dirs / "answers")) == 6


def test_a_second_model_does_not_overwrite_the_first(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("model/one", ["p1_constrained_mcq"], client=StubClient("model/one"))
    run_enrich("model/two", ["p1_constrained_mcq"], client=StubClient("model/two"))
    frame = pd.read_parquet(silver_dirs / "answers")
    assert len(frame) == 12
    assert set(frame["model_name"]) == {"model/one", "model/two"}


def test_errored_rows_are_retried_on_the_next_run(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient(failures={0, 1}))
    done = load_done_keys("fake_model", "p1_constrained_mcq")
    assert len(done) == 4
    retry_client = StubClient()
    run_enrich("fake/model", ["p1_constrained_mcq"], client=retry_client)
    assert len(retry_client.calls) == 2


def test_first_row_of_a_run_is_flagged_as_warmup(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    frame = pd.read_parquet(silver_dirs / "answers")
    assert frame["is_warmup"].sum() == 1


def test_provenance_columns_are_populated(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    frame = pd.read_parquet(silver_dirs / "answers")
    for column in ("host", "hardware", "run_id", "prompt_hash", "model_slug"):
        assert frame[column].notna().all()
        assert (frame[column].astype(str).str.len() > 0).all()


def test_run_metadata_file_is_written(questions, silver_dirs):
    questions.to_parquet(silver_dirs / "questions.parquet", index=False)
    run_enrich("fake/model", ["p1_constrained_mcq"], client=StubClient())
    assert list((silver_dirs / "runs").glob("run-*.json"))


def test_stratified_sample_respects_proportions(questions):
    sampled = stratified_sample(questions, 3, 42)
    assert len(sampled) == 3
    assert sampled["difficulty"].nunique() == 3
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_enrich_llm.py -v`
Expected: FAIL, `ImportError: cannot import name 'partition_dir'`.

- [ ] **Step 3: Réécrire `src/enrich_llm.py`**

```python
"""Inférence : interroge un modèle sur les questions silver.

Trois propriétés non négociables à cette échelle, environ 31 800 appels :

- la clé fonctionnelle est le triplet (question_id, model_slug, prompt_variant),
  sans quoi un second modèle écrase les résultats du premier ;
- l'écriture est incrémentale par lots, sans jamais relire l'existant, sans quoi
  le coût d'entrée-sortie dépasse le coût d'inférence ;
- une erreur d'infrastructure ne vaut pas mauvaise réponse : la ligne est
  conservée pour traçabilité mais repasse dans la file au run suivant.
"""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src.io_utils import atomic_write_dataframe, atomic_write_json
from src.llm_client import LLMClient
from src.prompts import get_variant
from src.runmeta import host_info, model_slug, new_run_id

ANSWER_COLUMNS = [
    "question_id",
    "model_slug",
    "prompt_variant",
    "model_name",
    "prompt_hash",
    "prompt_text",
    "raw_answer",
    "ai_answer",
    "response_time",
    "prompt_tokens",
    "completion_tokens",
    "finish_reason",
    "status",
    "error",
    "attempt",
    "is_warmup",
    "answered_at",
    "run_id",
    "host",
    "hardware",
    "os_version",
    "python_version",
    "runtime_version",
    "temperature",
    "max_tokens",
]


def _shard_of(question_id: str, total: int) -> int:
    """Répartition déterministe et indépendante du format de l'identifiant."""
    digest = hashlib.sha256(question_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % total


def partition_dir(slug: str, variant_id: str) -> Path:
    return config.SILVER_ANSWERS_DIR / f"model={slug}" / f"prompt_variant={variant_id}"


def load_done_keys(slug: str, variant_id: str) -> set[str]:
    """question_id déjà traités avec succès pour ce couple modèle/variante."""
    directory = partition_dir(slug, variant_id)
    parts = sorted(directory.glob("part-*.parquet"))
    if not parts:
        return set()
    frames = [pd.read_parquet(part, columns=["question_id", "status"]) for part in parts]
    frame = pd.concat(frames, ignore_index=True)
    return set(frame.loc[frame["status"] == "ok", "question_id"].astype(str))


def select_pending(
    questions: pd.DataFrame, done: set[str], shard: tuple[int, int] | None
) -> pd.DataFrame:
    pending = questions[~questions["question_id"].astype(str).isin(done)]
    if shard is not None:
        index, total = shard
        keep = pending["question_id"].astype(str).map(lambda qid: _shard_of(qid, total) == index)
        pending = pending[keep]
    return pending.reset_index(drop=True)


def stratified_sample(df: pd.DataFrame, sample_size: int, seed: int) -> pd.DataFrame:
    """Échantillon proportionnel par category × difficulty × type.

    Réservé aux smoke tests. L'élagage final tire une ligne par strate à tour
    de rôle plutôt qu'au hasard, ce qui préserve les proportions.
    """
    if sample_size <= 0 or sample_size >= len(df):
        return df.reset_index(drop=True)

    work = df.copy()
    work["_stratum"] = (
        work["category"].astype(str) + "|" + work["difficulty"].astype(str) + "|" + work["type"].astype(str)
    )
    groups = [group.sample(frac=1.0, random_state=seed) for _, group in work.groupby("_stratum", sort=True)]

    picked: list[pd.DataFrame] = []
    taken = 0
    position = 0
    while taken < sample_size:
        progressed = False
        for group in groups:
            if position < len(group) and taken < sample_size:
                picked.append(group.iloc[[position]])
                taken += 1
                progressed = True
        if not progressed:
            break
        position += 1

    return pd.concat(picked, ignore_index=True).drop(columns=["_stratum"]).reset_index(drop=True)


def _flush(buffer: list[dict[str, Any]], directory: Path, run_id: str, sequence: int) -> None:
    if not buffer:
        return
    frame = pd.DataFrame(buffer, columns=ANSWER_COLUMNS)
    atomic_write_dataframe(frame, directory / f"part-{run_id}-{sequence:05d}.parquet", "parquet")


def run_enrich(
    model_name: str = config.MODEL_NAME,
    variant_ids: list[str] | None = None,
    *,
    sample_size: int = 0,
    shard: tuple[int, int] | None = None,
    client: Any = None,
) -> list[Path]:
    if not config.SILVER_QUESTIONS.exists():
        raise FileNotFoundError(f"Silver introuvable: {config.SILVER_QUESTIONS}")

    variant_ids = variant_ids or list(("p1_constrained_mcq", "p2_open_minimal", "p3_open_guided"))
    client = client or LLMClient(model_name)
    slug = model_slug(model_name)
    run_id = new_run_id()
    provenance = host_info()
    questions = pd.read_parquet(config.SILVER_QUESTIONS)
    if sample_size:
        questions = stratified_sample(questions, sample_size, config.SAMPLE_SEED)

    written: list[Path] = []
    first_call_of_run = True
    counters = {"ok": 0, "error": 0}

    for variant_id in variant_ids:
        variant = get_variant(variant_id)
        directory = partition_dir(slug, variant_id)
        done = load_done_keys(slug, variant_id)
        pending = select_pending(questions, done, shard)
        print(f"[enrich] {model_name} / {variant_id}: {len(pending)} à traiter, {len(done)} déjà faites.")

        buffer: list[dict[str, Any]] = []
        sequence = 0
        for row in tqdm(pending.to_dict(orient="records"), desc=variant_id, unit="q"):
            user_prompt = variant.build_user_prompt(row)
            result = client.complete(variant.system_prompt, user_prompt)
            counters["ok" if result.status == "ok" else "error"] += 1

            buffer.append(
                {
                    "question_id": row["question_id"],
                    "model_slug": slug,
                    "prompt_variant": variant_id,
                    "model_name": model_name,
                    "prompt_hash": variant.prompt_hash,
                    "prompt_text": user_prompt,
                    "raw_answer": result.raw_text,
                    "ai_answer": result.text,
                    "response_time": round(result.response_time, 4),
                    "prompt_tokens": result.prompt_tokens,
                    "completion_tokens": result.completion_tokens,
                    "finish_reason": result.finish_reason,
                    "status": result.status,
                    "error": result.error,
                    "attempt": result.attempt,
                    "is_warmup": first_call_of_run,
                    "answered_at": datetime.now(timezone.utc).isoformat(),
                    "run_id": run_id,
                    **provenance,
                    "temperature": config.LLM_TEMPERATURE,
                    "max_tokens": config.LLM_MAX_TOKENS,
                }
            )
            first_call_of_run = False

            if len(buffer) >= config.FLUSH_EVERY:
                _flush(buffer, directory, run_id, sequence)
                written.append(directory / f"part-{run_id}-{sequence:05d}.parquet")
                buffer.clear()
                sequence += 1

        if buffer:
            _flush(buffer, directory, run_id, sequence)
            written.append(directory / f"part-{run_id}-{sequence:05d}.parquet")

    atomic_write_json(
        config.SILVER_RUNS_DIR / f"run-{run_id}.json",
        {
            "run_id": run_id,
            "model_name": model_name,
            "model_slug": slug,
            "variants": variant_ids,
            "shard": list(shard) if shard else None,
            "sample_size": sample_size,
            "counters": counters,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            **provenance,
        },
    )
    print(f"[enrich] {counters['ok']} réponses, {counters['error']} erreurs, run {run_id}.")
    return written


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Inférence LM Studio sur les questions silver.")
    parser.add_argument("--model", default=config.MODEL_NAME)
    parser.add_argument("--variants", nargs="*", default=None)
    parser.add_argument("--sample-size", type=int, default=0)
    parser.add_argument("--shard", default=None, help="Format i/n, par exemple 0/3.")
    args = parser.parse_args()

    shard = None
    if args.shard:
        index, total = args.shard.split("/")
        shard = (int(index), int(total))

    run_enrich(args.model, args.variants, sample_size=args.sample_size, shard=shard)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_enrich_llm.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Smoke test réel**

Run: `python -m src.enrich_llm --sample-size 20 --variants p1_constrained_mcq`
Expected: 20 réponses écrites dans `data/silver/answers/model=…/prompt_variant=p1_constrained_mcq/`, un fichier de run dans `data/silver/runs/`. Relancer la même commande ne doit produire aucun appel supplémentaire.

---


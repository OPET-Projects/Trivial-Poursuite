### Task 12: CLI d'orchestration

**Files:**
- Modify: `run_pipeline.py` (réécriture complète)
- Test: `tests/test_run_pipeline.py`

**Interfaces:**
- Consumes: `src.ingest_opentdb.run_ingest`, `src.transform_silver.run_transform`, `src.enrich_llm.run_enrich`, `src.judge.run_judge`.
- Produces: `parse_args(argv) -> argparse.Namespace`, `parse_shard(value: str | None) -> tuple[int, int] | None`, `main(argv) -> None`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_run_pipeline.py` :

```python
import pytest

from run_pipeline import parse_args, parse_shard


def test_default_runs_every_stage():
    args = parse_args([])
    assert args.stages == ["ingest", "transform", "enrich", "judge"]


def test_stage_selection():
    args = parse_args(["--stages", "enrich", "judge"])
    assert args.stages == ["enrich", "judge"]


def test_unknown_stage_is_rejected():
    with pytest.raises(SystemExit):
        parse_args(["--stages", "gold"])


def test_variants_default_to_all_three():
    args = parse_args([])
    assert args.variants == ["p1_constrained_mcq", "p2_open_minimal", "p3_open_guided"]


def test_parse_shard():
    assert parse_shard("1/3") == (1, 3)
    assert parse_shard(None) is None


def test_parse_shard_rejects_out_of_range():
    with pytest.raises(ValueError):
        parse_shard("3/3")


def test_parse_shard_rejects_garbage():
    with pytest.raises(ValueError):
        parse_shard("abc")
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_run_pipeline.py -v`
Expected: FAIL, `ImportError: cannot import name 'parse_shard'`.

- [ ] **Step 3: Réécrire `run_pipeline.py`**

```python
#!/usr/bin/env python3
"""Orchestration : ingest → transform → enrich → judge.

Les étages sont sélectionnables individuellement : l'inférence dure plusieurs
heures et se répartit entre postes, alors que le jugement se rejoue en minutes.
"""

from __future__ import annotations

import argparse
import sys

import config

STAGES = ["ingest", "transform", "enrich", "judge"]
DEFAULT_VARIANTS = ["p1_constrained_mcq", "p2_open_minimal", "p3_open_guided"]


def parse_shard(value: str | None) -> tuple[int, int] | None:
    if value is None:
        return None
    try:
        index_text, total_text = value.split("/")
        index, total = int(index_text), int(total_text)
    except ValueError as exc:
        raise ValueError(f"Shard invalide: {value!r}. Format attendu i/n, par exemple 0/3.") from exc
    if total < 1 or not 0 <= index < total:
        raise ValueError(f"Shard hors bornes: {value!r}. Attendu 0 <= i < n.")
    return index, total


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pipeline de benchmark LLM sur OpenTDB.")
    parser.add_argument("--stages", nargs="+", choices=STAGES, default=list(STAGES))
    parser.add_argument("--model", default=config.MODEL_NAME)
    parser.add_argument("--variants", nargs="+", default=list(DEFAULT_VARIANTS))
    parser.add_argument("--sample-size", type=int, default=0, help="0 = tout le dataset.")
    parser.add_argument("--shard", default=None, help="Découpe le travail: i/n.")
    parser.add_argument("--force-ingest", action="store_true")
    parser.add_argument("--no-llm-judge", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    shard = parse_shard(args.shard)

    if "ingest" in args.stages:
        from src.ingest_opentdb import run_ingest

        run_ingest(force=args.force_ingest)

    if "transform" in args.stages:
        from src.transform_silver import run_transform

        run_transform()

    if "enrich" in args.stages:
        from src.enrich_llm import run_enrich

        run_enrich(args.model, args.variants, sample_size=args.sample_size, shard=shard)

    if "judge" in args.stages:
        from src.judge import LLMJudge, run_judge

        run_judge(llm_judge=None if args.no_llm_judge else LLMJudge())


if __name__ == "__main__":
    main(sys.argv[1:])
```

- [ ] **Step 4: Lancer la suite complète**

Run: `python -m pytest -v`
Expected: PASS, tous les tests des tâches 1 à 12.

---


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
from src.prompts import PROMPT_VARIANTS, get_variant
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


# Types imposés à l'écriture. Un lot entièrement en erreur ne porte que des
# None dans les colonnes de jetons : laissé à l'inférence, parquet les typerait
# `null` et le dataset deviendrait illisible dès qu'un autre lot les type
# `int64`. Les entiers nullables gardent l'absence de valeur sans changer de
# type.
_ANSWER_DTYPES = {
    "response_time": "float64",
    "prompt_tokens": "Int64",
    "completion_tokens": "Int64",
    "attempt": "int64",
    "is_warmup": "bool",
    "temperature": "float64",
    "max_tokens": "int64",
}


def _shard_of(question_id: str, total: int) -> int:
    """Répartition déterministe et indépendante du format de l'identifiant."""
    digest = hashlib.sha256(question_id.encode("utf-8")).hexdigest()
    return int(digest, 16) % total


def partition_dir(slug: str, variant_id: str) -> Path:
    return config.SILVER_ANSWERS_DIR / f"model={slug}" / f"prompt_variant={variant_id}"


def load_done_keys(slug: str, variant_id: str) -> set[str]:
    """question_id déjà traités avec succès pour ce couple modèle/variante.

    Les fichiers sont relus un par un plutôt que comme un dataset : les clés de
    partition du chemin porteraient les mêmes noms que des colonnes du fichier.
    """
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
        work["category"].astype(str)
        + "|"
        + work["difficulty"].astype(str)
        + "|"
        + work["type"].astype(str)
    )
    groups = [
        group.sample(frac=1.0, random_state=seed)
        for _, group in work.groupby("_stratum", sort=True)
    ]

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


def _flush(buffer: list[dict[str, Any]], directory: Path, run_id: str, sequence: int) -> Path | None:
    """Écrit un lot et rend son chemin, ou None si le lot est vide."""
    if not buffer:
        return None
    path = directory / f"part-{run_id}-{sequence:05d}.parquet"
    frame = pd.DataFrame(buffer, columns=ANSWER_COLUMNS).astype(_ANSWER_DTYPES)
    atomic_write_dataframe(frame, path, "parquet")
    return path


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

    variant_ids = variant_ids or list(PROMPT_VARIANTS)
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
        print(
            f"[enrich] {model_name} / {variant_id}: "
            f"{len(pending)} à traiter, {len(done)} déjà faites."
        )

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
                path = _flush(buffer, directory, run_id, sequence)
                if path is not None:
                    written.append(path)
                buffer.clear()
                sequence += 1

        path = _flush(buffer, directory, run_id, sequence)
        if path is not None:
            written.append(path)

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

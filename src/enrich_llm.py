"""Échantillon stratifié + appels LM Studio → silver/questions_enriched.parquet."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from src.scoring import is_ai_correct


def build_user_prompt(row: pd.Series) -> str:
    header = (
        f"Category: {row['category']}\n"
        f"Difficulty: {row['difficulty']}\n"
        f"Question: {row['question']}\n"
    )
    if row["type"] == "boolean":
        return (
            header
            + "This is a True/False question.\n"
            + "Answer True or False only.\n"
            + "Answer:"
        )
    options = "\n".join(f"- {answer}" for answer in row["all_answers"])
    return (
        header
        + "Options:\n"
        + f"{options}\n"
        + "Copy one option verbatim.\n"
        + "Answer:"
    )


def stratified_sample(df: pd.DataFrame, sample_size: int, seed: int) -> pd.DataFrame:
    """Échantillon stratifié par category × difficulty × type.

    sample_size <= 0 : tout le dataset.
    """
    if sample_size <= 0 or sample_size >= len(df):
        return df.copy().reset_index(drop=True)

    work = df.copy()
    work["_stratum"] = (
        work["category"].astype(str)
        + "|"
        + work["difficulty"].astype(str)
        + "|"
        + work["type"].astype(str)
    )
    frac = sample_size / len(work)
    parts: list[pd.DataFrame] = []
    for _, group in work.groupby("_stratum", sort=False):
        n = min(len(group), max(1, int(round(len(group) * frac))))
        parts.append(group.sample(n=n, random_state=seed))
    sampled = pd.concat(parts, ignore_index=True)

    if len(sampled) > sample_size:
        sampled = sampled.sample(n=sample_size, random_state=seed)
    elif len(sampled) < sample_size:
        leftover = work[~work["question_id"].isin(sampled["question_id"])]
        missing = sample_size - len(sampled)
        if len(leftover) > 0 and missing > 0:
            extra = leftover.sample(n=min(missing, len(leftover)), random_state=seed)
            sampled = pd.concat([sampled, extra], ignore_index=True)

    return (
        sampled.drop(columns=["_stratum"], errors="ignore")
        .drop_duplicates(subset=["question_id"])
        .reset_index(drop=True)
    )


def _extract_text(result: object) -> str:
    if result is None:
        return ""
    content = getattr(result, "content", None)
    if content:
        return str(content).strip()
    return str(result).strip()


def _load_done_ids() -> set[str]:
    if not config.SILVER_ENRICHED.exists():
        return set()
    existing = pd.read_parquet(config.SILVER_ENRICHED, columns=["question_id"])
    return set(existing["question_id"].astype(str))


def _append_result(row: dict) -> None:
    new_df = pd.DataFrame([row])
    if config.SILVER_ENRICHED.exists():
        current = pd.read_parquet(config.SILVER_ENRICHED)
        combined = pd.concat([current, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["question_id"], keep="last")
    else:
        combined = new_df
    combined.to_parquet(config.SILVER_ENRICHED, index=False)


def _get_model():
    try:
        import lmstudio as lms
    except ImportError as exc:
        raise RuntimeError(
            "Le package 'lmstudio' n'est pas installé. pip install -r requirements.txt"
        ) from exc

    lms.set_sync_api_timeout(config.LMSTUDIO_TIMEOUT_SECONDS)
    try:
        return lms, lms.llm(config.MODEL_NAME)
    except Exception as exc:  # noqa: BLE001 — message utilisateur
        raise RuntimeError(
            "Impossible de joindre LM Studio. Vérifiez que l'application est ouverte, "
            "que le serveur (onglet Developer) est démarré, et que le modèle "
            f"{config.MODEL_NAME} est chargé."
        ) from exc


def run_enrich(*, sample_size: int = config.SAMPLE_SIZE) -> Path:
    if not config.SILVER_CLEAN.exists():
        raise FileNotFoundError(
            f"Silver propre introuvable: {config.SILVER_CLEAN}. Lancez d'abord le transform."
        )
    config.SILVER_DIR.mkdir(parents=True, exist_ok=True)

    clean = pd.read_parquet(config.SILVER_CLEAN)
    target = stratified_sample(clean, sample_size, config.SAMPLE_SEED)
    done = _load_done_ids()
    pending = target[~target["question_id"].isin(done)].reset_index(drop=True)

    print(
        f"[enrich] Cible {len(target)} questions "
        f"(sample-size={sample_size if sample_size > 0 else 'all'}), "
        f"{len(done)} déjà faites, {len(pending)} à interroger."
    )
    if pending.empty:
        print(f"[enrich] Rien à faire → {config.SILVER_ENRICHED}")
        return config.SILVER_ENRICHED

    lms, model = _get_model()

    n_ok = 0
    n_fail = 0
    for _, row in tqdm(pending.iterrows(), total=len(pending), desc="LM Studio", unit="q"):
        user_prompt = build_user_prompt(row)
        chat = lms.Chat(config.SYSTEM_PROMPT)
        chat.add_user_message(user_prompt)

        started = time.perf_counter()
        try:
            result = model.respond(
                chat,
                config={
                    "temperature": config.LLM_TEMPERATURE,
                    "maxTokens": config.LLM_MAX_TOKENS,
                },
            )
            ai_answer = _extract_text(result)
            error = ""
        except Exception as exc:  # noqa: BLE001 — on continue question par question
            ai_answer = ""
            error = str(exc)
        response_time = time.perf_counter() - started

        correct = bool(
            ai_answer
            and is_ai_correct(
                ai_answer,
                row["correct_answer"],
                row["type"],
                list(row["all_answers"]),
            )
        )
        n_ok += int(correct)
        n_fail += int(not correct)

        record = {
            "question_id": row["question_id"],
            "category": row["category"],
            "type": row["type"],
            "difficulty": row["difficulty"],
            "question": row["question"],
            "correct_answer": row["correct_answer"],
            "incorrect_answers": row["incorrect_answers"],
            "all_answers": row["all_answers"],
            "prompt_id": config.PROMPT_ID,
            "prompt_text": user_prompt,
            "model_name": config.MODEL_NAME,
            "ai_answer": ai_answer,
            "ai_correct": correct,
            "response_time": round(response_time, 4),
            "error": error,
        }
        _append_result(record)

    enriched = pd.read_parquet(config.SILVER_ENRICHED)
    accuracy = float(enriched["ai_correct"].mean()) if len(enriched) else 0.0
    mean_rt = float(enriched["response_time"].mean()) if len(enriched) else 0.0
    print(
        f"[enrich] Session {n_ok} correctes / {n_ok + n_fail} "
        f"| dataset enrichi: {len(enriched)} lignes, "
        f"accuracy={accuracy:.1%}, temps moyen={mean_rt:.2f}s "
        f"→ {config.SILVER_ENRICHED}"
    )
    return config.SILVER_ENRICHED


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Enrichissement LLM via LM Studio.")
    parser.add_argument(
        "--sample-size",
        type=int,
        default=config.SAMPLE_SIZE,
        help="Taille de l'échantillon (0 = tout le dataset).",
    )
    args = parser.parse_args()
    run_enrich(sample_size=args.sample_size)


if __name__ == "__main__":
    main()

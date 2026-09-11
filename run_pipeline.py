#!/usr/bin/env python3
"""Orchestration : ingest → transform → enrich → judge.

Les étages sont sélectionnables individuellement, et c'est la propriété qui
porte tout le reste : l'inférence dure des heures et se répartit entre postes
par `--shard`, alors que le jugement se rejoue en minutes sur les parquets
déjà écrits, sans jamais rappeler le modèle.

Les étages sont importés au moment de leur exécution et non au chargement du
module : `--help` et la validation des arguments restent instantanés, et une
dépendance manquante ne se manifeste que dans l'étage qui la réclame.
"""

from __future__ import annotations

import argparse
import sys

import config
from src.prompts import PROMPT_VARIANTS

STAGES = ["ingest", "transform", "enrich", "judge"]

# Dérivé du registre plutôt que recopié : une variante ajoutée à src.prompts
# doit entrer dans le run sans qu'on pense à la déclarer ici.
DEFAULT_VARIANTS = list(PROMPT_VARIANTS)


def parse_shard(value: str | None) -> tuple[int, int] | None:
    """Interprète `i/n`, la découpe du travail entre postes."""
    if value is None:
        return None
    try:
        index_text, total_text = value.split("/")
        index, total = int(index_text), int(total_text)
    except ValueError as exc:
        raise ValueError(
            f"Shard invalide: {value!r}. Format attendu i/n, par exemple 0/3."
        ) from exc
    if total < 1 or not 0 <= index < total:
        raise ValueError(f"Shard hors bornes: {value!r}. Attendu 0 <= i < n.")
    return index, total


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pipeline de benchmark LLM sur OpenTDB.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--stages",
        nargs="+",
        choices=STAGES,
        default=list(STAGES),
        help="Étages à exécuter, dans l'ordre du pipeline.",
    )
    parser.add_argument("--model", default=config.MODEL_NAME, help="Modèle interrogé.")
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=DEFAULT_VARIANTS,
        default=list(DEFAULT_VARIANTS),
        help="Variantes de prompt à soumettre.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=config.SAMPLE_SIZE,
        help="0 = tout le dataset.",
    )
    parser.add_argument("--shard", default=None, help="Découpe le travail: i/n.")
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        help="Repart de zéro au lieu de reprendre le bronze existant.",
    )
    parser.add_argument(
        "--no-llm-judge",
        action="store_true",
        help="Coupe l'étage llm_judge, les résidus tombent en no_match.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    # Validé avant le premier étage : un shard fautif ne doit pas se découvrir
    # après l'ingest, soit vingt minutes de scrape plus tard.
    try:
        shard = parse_shard(args.shard)
    except ValueError as exc:
        raise SystemExit(f"[pipeline] {exc}") from exc

    if "ingest" in args.stages:
        from src.ingest_opentdb import run_ingest

        print("[pipeline] étage ingest")
        run_ingest(force=args.force_ingest)

    if "transform" in args.stages:
        from src.transform_silver import run_transform

        print("[pipeline] étage transform")
        run_transform()

    if "enrich" in args.stages:
        from src.enrich_llm import run_enrich

        scope = f"shard {args.shard}" if shard else "dataset complet"
        print(f"[pipeline] étage enrich — {args.model}, {len(args.variants)} variantes, {scope}")
        run_enrich(
            args.model,
            args.variants,
            sample_size=args.sample_size,
            shard=shard,
        )

    if "judge" in args.stages:
        from src.judge import LLMJudge, run_judge

        # Sans filtre de modèle volontairement : le jugement est rejouable et
        # coûte des minutes, le restreindre à --model ferait silencieusement
        # sauter les autres modèles quand l'étage est lancé seul.
        print("[pipeline] étage judge — tous modèles")
        run_judge(llm_judge=None if args.no_llm_judge else LLMJudge())


if __name__ == "__main__":
    main(sys.argv[1:])

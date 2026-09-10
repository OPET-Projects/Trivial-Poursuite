#!/usr/bin/env python3
"""Orchestre ingest OpenTDB → silver → enrichissement LM Studio."""

from __future__ import annotations

import argparse
import sys

import config
from src.enrich_llm import run_enrich
from src.ingest_opentdb import run_ingest
from src.transform_silver import run_transform


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pipeline bronze / silver / LLM (OpenTDB + LM Studio)."
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Ne pas scraper OpenTDB (bronze déjà présent).",
    )
    parser.add_argument(
        "--skip-transform",
        action="store_true",
        help="Ne pas reconstruire questions_clean.parquet.",
    )
    parser.add_argument(
        "--skip-enrich",
        action="store_true",
        help="Arrêter après la couche silver propre (pas d'appel LLM).",
    )
    parser.add_argument(
        "--force-ingest",
        action="store_true",
        help="Re-télécharger OpenTDB même si le bronze est déjà complet.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=config.SAMPLE_SIZE,
        help="Nombre de questions à envoyer au modèle (0 = tout le dataset).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if not args.skip_ingest:
        run_ingest(force=args.force_ingest)
    elif not config.BRONZE_CSV.exists():
        raise SystemExit(
            f"Bronze introuvable ({config.BRONZE_CSV}). Relancez sans --skip-ingest."
        )

    if not args.skip_transform:
        run_transform()
    elif not config.SILVER_CLEAN.exists():
        raise SystemExit(
            f"Silver introuvable ({config.SILVER_CLEAN}). Relancez sans --skip-transform."
        )

    if not args.skip_enrich:
        run_enrich(sample_size=args.sample_size)


if __name__ == "__main__":
    main(sys.argv[1:])

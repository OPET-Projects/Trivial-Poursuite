"""Accès en lecture seule au DuckDB gold.

Le nom de table est validé contre un catalogue fermé : une interpolation de
chaîne dans une requête, même dans une application locale, reste une injection.

La connexion est ouverte en lecture seule pour une raison de fond : le gold est
un artefact reconstructible par `dbt build`, jamais une source de vérité. Une
écriture depuis le dashboard produirait un état que le pipeline ne sait pas
reproduire.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

MARTS = (
    "mart_model_performance",
    "mart_performance_by_category",
    "mart_performance_by_difficulty",
    "mart_prompt_performance",
    "mart_latency",
    "mart_question_hardness",
    "mart_matching_impact",
    "mart_position_bias",
    "mart_errors",
)


def connect(path: Path) -> duckdb.DuckDBPyConnection:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Base gold introuvable: {path}. "
            "Lancez `cd dbt_project && dbt build --profiles-dir .`"
        )
    return duckdb.connect(str(path), read_only=True)


def load(conn: duckdb.DuckDBPyConnection, mart_name: str) -> pd.DataFrame:
    if mart_name not in MARTS:
        raise ValueError(f"Table inconnue: {mart_name!r}. Connues: {list(MARTS)}")
    return conn.execute(f"select * from {mart_name}").fetch_df()


def available_marts(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Les marts réellement présents, dans l'ordre du catalogue.

    Un `dbt build` partiel ou interrompu laisse une base incomplète : les pages
    du dashboard s'appuient sur cette liste plutôt que de supposer les neuf.
    """
    existing = {row[0] for row in conn.execute("show tables").fetchall()}
    return [name for name in MARTS if name in existing]

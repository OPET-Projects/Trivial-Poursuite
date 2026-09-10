"""Écritures atomiques : on n'écrase jamais un fichier en place.

Un Ctrl-C au mauvais moment laisserait sinon un artefact tronqué qu'aucun
checkpoint ne signale.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

_FORMATS = {"csv", "parquet"}


def _replace_atomically(path: Path, write_to_temp) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        write_to_temp(temp_path)
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def atomic_write_text(path: Path, content: str) -> None:
    _replace_atomically(path, lambda tmp: tmp.write_text(content, encoding="utf-8"))


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False))


def atomic_write_dataframe(df: pd.DataFrame, path: Path, fmt: str) -> None:
    if fmt not in _FORMATS:
        raise ValueError(f"Format non supporté: {fmt!r}. Attendu: {sorted(_FORMATS)}")

    def write(tmp: Path) -> None:
        if fmt == "csv":
            df.to_csv(tmp, index=False)
        else:
            df.to_parquet(tmp, index=False)

    _replace_atomically(path, write)


def append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

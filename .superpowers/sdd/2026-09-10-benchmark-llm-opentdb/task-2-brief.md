### Task 2: Écritures atomiques

**Files:**
- Create: `src/io_utils.py`
- Test: `tests/test_io_utils.py`

**Interfaces:**
- Consumes: rien.
- Produces: `atomic_write_text(path: Path, content: str) -> None`, `atomic_write_json(path: Path, payload: dict) -> None`, `atomic_write_dataframe(df: pandas.DataFrame, path: Path, fmt: str) -> None` avec `fmt` valant `"csv"` ou `"parquet"`, `append_jsonl(path: Path, records: list[dict]) -> None`.

- [ ] **Step 1: Écrire les tests**

Créer `tests/test_io_utils.py` :

```python
import json

import pandas as pd
import pytest

from src.io_utils import (
    append_jsonl,
    atomic_write_dataframe,
    atomic_write_json,
    atomic_write_text,
)


def test_atomic_write_text_creates_parent(tmp_path):
    target = tmp_path / "nested" / "file.txt"
    atomic_write_text(target, "bonjour")
    assert target.read_text(encoding="utf-8") == "bonjour"


def test_atomic_write_leaves_no_temp_file(tmp_path):
    target = tmp_path / "file.txt"
    atomic_write_text(target, "x")
    assert [p.name for p in tmp_path.iterdir()] == ["file.txt"]


def test_atomic_write_json_roundtrip(tmp_path):
    target = tmp_path / "meta.json"
    atomic_write_json(target, {"a": 1, "accent": "é"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": 1, "accent": "é"}


def test_atomic_write_dataframe_parquet(tmp_path):
    target = tmp_path / "df.parquet"
    atomic_write_dataframe(pd.DataFrame({"a": [1, 2]}), target, "parquet")
    assert len(pd.read_parquet(target)) == 2


def test_atomic_write_dataframe_rejects_unknown_format(tmp_path):
    with pytest.raises(ValueError):
        atomic_write_dataframe(pd.DataFrame({"a": [1]}), tmp_path / "x.txt", "txt")


def test_append_jsonl_accumulates(tmp_path):
    target = tmp_path / "log.jsonl"
    append_jsonl(target, [{"i": 1}])
    append_jsonl(target, [{"i": 2}, {"i": 3}])
    lines = target.read_text(encoding="utf-8").strip().split("\n")
    assert [json.loads(line)["i"] for line in lines] == [1, 2, 3]
```

- [ ] **Step 2: Lancer et vérifier l'échec**

Run: `python -m pytest tests/test_io_utils.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'src.io_utils'`.

- [ ] **Step 3: Implémenter**

Créer `src/io_utils.py` :

```python
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
```

- [ ] **Step 4: Lancer les tests**

Run: `python -m pytest tests/test_io_utils.py -v`
Expected: PASS, 6 tests.


---


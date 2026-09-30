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

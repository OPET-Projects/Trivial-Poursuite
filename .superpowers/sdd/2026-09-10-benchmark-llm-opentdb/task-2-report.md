# Task 2 Report: Écritures atomiques

## What was implemented

Two files were created as specified in the brief:

### 1. `src/io_utils.py`
A module providing atomic write utilities to prevent truncated artifacts after interruptions. Contains:
- `atomic_write_text(path: Path, content: str)` - writes text atomically to a file
- `atomic_write_json(path: Path, payload: dict)` - writes JSON atomically with nice formatting and UTF-8 support
- `atomic_write_dataframe(df, path, fmt)` - writes pandas DataFrames as CSV or Parquet atomically
- `append_jsonl(path, records)` - appends JSON lines to a file (non-atomic append)

Core implementation uses `_replace_atomically()` helper which:
1. Creates parent directories if needed
2. Creates a temp file with a distinct name via `tempfile.mkstemp()`
3. Writes content to temp file
4. Atomically renames temp file to target via `os.replace()` (atomic at OS level)
5. Cleans up temp file in finally block if errors occur

### 2. `tests/test_io_utils.py`
Six comprehensive test cases covering:
- Parent directory creation
- No temporary file leakage
- JSON round-trip with accented characters
- Parquet DataFrame writing
- Invalid format rejection
- JSONL accumulation across multiple calls

## Test Results

**All 6 tests pass:**

```
tests/test_io_utils.py::test_atomic_write_text_creates_parent PASSED     [ 16%]
tests/test_io_utils.py::test_atomic_write_leaves_no_temp_file PASSED     [ 33%]
tests/test_io_utils.py::test_atomic_write_json_roundtrip PASSED          [ 50%]
tests/test_io_utils.py::test_atomic_write_dataframe_parquet PASSED       [ 66%]
tests/test_io_utils.py::test_atomic_write_dataframe_rejects_unknown_format PASSED [ 83%]
tests/test_io_utils.py::test_append_jsonl_accumulates PASSED             [100%]

============================== 6 passed in 2.79s =======================================
```

## TDD Proof

### RED Phase (before implementation)

**Command:**
```bash
python -m pytest tests/test_io_utils.py -v
```

**Output:**
```
ImportError while importing test module '/Users/periicles/Dev/Trivial-Poursuite/tests/test_io_utils.py'.
...
tests/test_io_utils.py:6: in <module>
    from src.io_utils import (
E   ModuleNotFoundError: No module named 'src.io_utils'
```

**Why expected:** The module `src/io_utils.py` did not exist yet, so tests cannot collect and run.

### GREEN Phase (after implementation)

**Command:**
```bash
source .venv/bin/activate && python -m pytest tests/test_io_utils.py -v
```

**Output:** All 6 tests PASSED (see Test Results section above)

## Files Changed

- **Created:** `src/io_utils.py` (59 lines)
  - Contains 4 public functions + 1 private helper
  - Type hints, proper error handling, clear docstrings

- **Created:** `tests/test_io_utils.py` (45 lines)
  - 6 test functions covering happy paths and error cases
  - Uses pytest tmp_path fixture for isolated test environments

## Commits

**SHA:** `042b01a` (short form of full commit hash)
**Message:** `feat: écritures atomiques pour les artefacts de données`

## Self-Review Findings

### Strengths
1. **Completeness**: All 4 required functions implemented as specified
2. **Atomicity guarantee**: Core `_replace_atomically()` uses OS-level atomic rename (`os.replace()`) which cannot be partially interrupted
3. **Error safety**: Try/finally block ensures temp files are cleaned up even if write fails
4. **Encoding**: Uses `ensure_ascii=False` to preserve French accents in JSON
5. **Test isolation**: Each test uses pytest's `tmp_path` fixture, ensuring no cross-test contamination
6. **Code quality**: Private helper (`_replace_atomically`) follows conventions, clear variable names, self-documenting

### Edge Cases Covered
- Parent directory creation (nested paths)
- No leftover temp files (verify directory contains only target file)
- JSON with Unicode characters (é, etc.)
- DataFrame formats (validates only csv/parquet supported)
- Multiple JSONL appends (accumulation not truncation)
- Invalid format error handling

### No Concerns
- Implementation matches brief exactly
- Tests validate the atomicity guarantee (temp file cleanup)
- Proper exception handling with helpful error messages
- Modern Python syntax (`from __future__ import annotations`)

## Summary

Task completed successfully with TDD methodology. All 6 tests pass. The atomic write utilities will prevent data corruption in the pipeline when processes are interrupted. The implementation is minimal, focused, and production-ready.

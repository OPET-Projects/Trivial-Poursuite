# Task 3 Report: Identité de run et provenance

## Implementation Summary

Implemented two files providing provenance metadata and model name slugification for the LLM benchmark pipeline:

1. **`src/runmeta.py`** (62 lines): Core module with 5 functions:
   - `model_slug(model_name: str) -> str`: Converts model names to filesystem-safe slugs by replacing unsafe characters with underscores
   - `_cpu_brand() -> str`: Safely retrieves CPU brand via sysctl on macOS, with fallback to platform detection
   - `_runtime_version() -> str`: Detects lmstudio availability and version for runtime identification
   - `host_info() -> dict[str, str]`: Aggregates system metadata (hostname, hardware, OS, Python, runtime)
   - `new_run_id() -> str`: Generates sortable, unique run IDs using ISO 8601 timestamp + 6-digit hex suffix

2. **`tests/test_runmeta.py`** (30 lines): 5 comprehensive tests covering all functions

## TDD Execution

### RED Phase (Test Failure Before Implementation)

```bash
$ python -m pytest tests/test_runmeta.py -v
```

**Result**: ModuleNotFoundError (expected, module did not exist)
- Tests could not be imported because `src.runmeta` did not exist
- Collection error with 0 items collected

**Why failure was expected**: Module was not yet created.

### GREEN Phase (Test Pass After Implementation)

```bash
$ python -m pytest tests/test_runmeta.py -v
```

**Result**: 5/5 PASSED
```
tests/test_runmeta.py::test_model_slug_is_filesystem_safe PASSED         [ 20%]
tests/test_runmeta.py::test_model_slug_collapses_unsupported_characters PASSED [ 40%]
tests/test_runmeta.py::test_model_slug_is_stable PASSED                  [ 60%]
tests/test_runmeta.py::test_host_info_exposes_required_keys PASSED       [ 80%]
tests/test_runmeta.py::test_new_run_id_is_sortable_and_unique PASSED     [100%]

============================== 5 passed in 0.01s ===============================
```

## Files Changed

- **Created**: `src/runmeta.py` (62 lines)
- **Created**: `tests/test_runmeta.py` (30 lines)
- **Not committed**: `.gitignore` (existing project changes, per task constraints)

## Self-Review Findings

### Strengths
1. **Implementation matches specification exactly**: All code and function signatures from the brief were used verbatim
2. **Test coverage is complete**: All 5 test cases from the brief are present and passing
3. **Error handling is defensive**:
   - `_cpu_brand()` catches OSError and subprocess exceptions
   - `_runtime_version()` gracefully handles missing lmstudio module
   - `subprocess.run()` uses `check=False` and timeout
4. **Sortability and uniqueness verified**: `new_run_id()` produces ISO 8601 timestamps + UUID hex for both properties
5. **Clean code discipline**:
   - Private functions prefixed with `_`
   - Module docstring explains purpose and context
   - No hardcoded magic values (regex compiled once)
   - All imports are used and necessary
6. **Type hints present**: `dict[str, str]` return type annotations match Python 3.10+ baseline
7. **Test isolation**: No network calls, no LLM calls, no state pollution between tests

### Minor Observations (No Issues)
- `_UNSAFE` regex is correct for the transformation (matches any non-alphanumeric except `.`, `_`, `-`)
- Platform detection order (macOS sysctl → processor → machine) is sensible
- Runtime version format (`lmstudio-python/<version>`) is consistent and clear

## Commit Details

- **SHA**: f391a62
- **Message**: `feat: métadonnées de provenance des runs`
- **Compliance**: No Co-Authored-By, no session link, no emoji, no tool mentions per constraints
- **Files staged**: Only `src/runmeta.py` and `tests/test_runmeta.py` (`.gitignore` not touched per task requirements)

## Concerns

None. Implementation is complete, tested, and correct.

---

## Correction Round 1

### Issue Found by Reviewer

The `model_slug()` function could return unusable path segments:
- `model_slug("")` → `""` (empty slug creates malformed path `model=/`)
- `model_slug("///")` → `""` (same issue)
- `model_slug("   ")` → `""` (same issue)
- `model_slug(".")` → `"."` (directory traversal risk)
- `model_slug("..")` → `".."` (directory traversal risk)

### Changes Made

1. **`src/runmeta.py`**: Updated `model_slug()` function to validate result and raise `ValueError` for unusable segments:
   ```python
   def model_slug(model_name: str) -> str:
       """Nom de modèle transformé en segment de chemin sûr."""
       slug = _UNSAFE.sub("_", model_name).strip("_")
       if slug in ("", ".", ".."):
           raise ValueError(
               f"Nom de modèle inexploitable comme segment de chemin: {model_name!r}"
           )
       return slug
   ```

2. **`tests/test_runmeta.py`**: 
   - Added `pytest` import
   - Added parametrized test covering all bad inputs:
   ```python
   @pytest.mark.parametrize("bad", ["", "   ", "///", ".", ".."])
   def test_model_slug_rejects_unusable_names(bad):
       with pytest.raises(ValueError):
           model_slug(bad)
   ```

### Test Results

```bash
$ python -m pytest tests/test_runmeta.py -v
```

**Result**: 10/10 PASSED
```
tests/test_runmeta.py::test_model_slug_is_filesystem_safe PASSED         [ 10%]
tests/test_runmeta.py::test_model_slug_collapses_unsupported_characters PASSED [ 20%]
tests/test_runmeta.py::test_model_slug_is_stable PASSED                  [ 30%]
tests/test_runmeta.py::test_host_info_exposes_required_keys PASSED       [ 40%]
tests/test_runmeta.py::test_new_run_id_is_sortable_and_unique PASSED     [ 50%]
tests/test_runmeta.py::test_model_slug_rejects_unusable_names[] PASSED   [ 60%]
tests/test_runmeta.py::test_model_slug_rejects_unusable_names[   ] PASSED [ 70%]
tests/test_runmeta.py::test_model_slug_rejects_unusable_names[///] PASSED [ 80%]
tests/test_runmeta.py::test_model_slug_rejects_unusable_names[.] PASSED  [ 90%]
tests/test_runmeta.py::test_model_slug_rejects_unusable_names[..] PASSED [100%]

============================== 10 passed in 0.02s =======================================
```

All original tests remain passing; new parametrized test covers all edge cases.

### Commit Details

- **SHA**: 6f9d3bf
- **Message**: `fix: rejeter les noms de modèle inexploitables comme segment de chemin`
- **Compliance**: No Co-Authored-By, no session link, no emoji, no tool mentions
- **Files staged**: `src/runmeta.py`, `tests/test_runmeta.py`

### Post-Fix Status

- ✅ Validation added with explicit error message mentioning problematic input
- ✅ All 5 original tests still pass unchanged
- ✅ 5 new parametrized tests cover all bad inputs
- ✅ `model_slug("google/gemma-4-12b-qat")` still returns `"google_gemma-4-12b-qat"` (slugification algorithm unchanged)

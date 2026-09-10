import re

from src.runmeta import host_info, model_slug, new_run_id


def test_model_slug_is_filesystem_safe():
    assert model_slug("google/gemma-4-12b-qat") == "google_gemma-4-12b-qat"


def test_model_slug_collapses_unsupported_characters():
    assert model_slug("prism-ml/Bonsai-27B GGUF:Q4_0") == "prism-ml_Bonsai-27B_GGUF_Q4_0"


def test_model_slug_is_stable():
    assert model_slug("a/b") == model_slug("a/b")


def test_host_info_exposes_required_keys():
    info = host_info()
    for key in ("host", "hardware", "os_version", "python_version", "runtime_version"):
        assert key in info
        assert isinstance(info[key], str)


def test_new_run_id_is_sortable_and_unique():
    first, second = new_run_id(), new_run_id()
    assert re.match(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$", first)
    assert first != second

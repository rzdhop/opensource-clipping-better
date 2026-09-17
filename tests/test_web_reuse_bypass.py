"""Reusing a job must default to its cached AI output.

`POST /api/jobs` with `reuse_job_id` means "run this again against the analysis
it already has", so `load_gemini_json` should default on and the run should need
no API key.

That auto-enable was dead for a while. The route tested
`"load_gemini_json" not in payload`, but `payload` comes from `model_dump()`,
which always includes every *declared* field -- so once `load_gemini_json` was
declared on the model, the key was always present (as False) and the condition
could never fire. A rerun then demanded a key it did not need:

    NVIDIA_API_KEY not found (active provider: nvidia)

The fix tests `model_fields_set`, which holds only what the client actually
sent. These tests pin the distinction between "not sent" and "sent as false",
which is the whole subtlety.

Needs pydantic, so it skips in CI (pytest only -- see DEC-012).
"""

import pytest


def _model():
    pytest.importorskip("pydantic", reason="CI installs pytest only")
    from web.api.models import JobCreateRequest

    return JobCreateRequest


def test_omitted_field_is_not_in_fields_set():
    """The mechanism the route depends on."""
    req = _model()(upload_filename="v.mp4")

    assert "load_gemini_json" not in req.model_fields_set
    # ...while model_dump still reports it, which is what broke the old check.
    assert "load_gemini_json" in req.model_dump()


def test_explicitly_false_is_in_fields_set():
    """A client that deliberately asks for a fresh analysis must be obeyed."""
    req = _model()(upload_filename="v.mp4", load_gemini_json=False)

    assert "load_gemini_json" in req.model_fields_set
    assert req.load_gemini_json is False


def test_explicitly_true_is_in_fields_set():
    req = _model()(upload_filename="v.mp4", load_gemini_json=True)

    assert "load_gemini_json" in req.model_fields_set
    assert req.load_gemini_json is True


# The route's decision, reproduced exactly so it cannot drift from the test.
def _resolve(req, reuse_job_id):
    payload = req.model_dump()
    payload.pop("reuse_job_id", None)
    if reuse_job_id and "load_gemini_json" not in req.model_fields_set:
        payload["load_gemini_json"] = True
    return payload["load_gemini_json"]


def test_reuse_without_the_field_enables_the_bypass():
    """The regression: this returned False and cost an API key."""
    req = _model()(upload_filename="v.mp4", reuse_job_id="abc123")

    assert _resolve(req, "abc123") is True


def test_reuse_with_explicit_false_still_reanalyses():
    req = _model()(upload_filename="v.mp4", reuse_job_id="abc123", load_gemini_json=False)

    assert _resolve(req, "abc123") is False


def test_a_fresh_job_does_not_get_the_bypass():
    """Without reuse there is no cached analysis to load."""
    req = _model()(upload_filename="v.mp4")

    assert _resolve(req, None) is False


def test_the_old_check_would_have_failed_this():
    """Pin why the old condition was dead, so nobody reinstates it."""
    req = _model()(upload_filename="v.mp4", reuse_job_id="abc123")
    payload = req.model_dump()

    # The old guard: `"load_gemini_json" not in payload` -- always False.
    assert "load_gemini_json" in payload, (
        "a declared field is always in model_dump(), which is exactly why the "
        "old check could never enable the bypass"
    )

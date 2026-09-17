"""Guards that every job setting the backend reads is declared on the schema.

``JobCreateRequest`` is a default-strict Pydantic model, and
``routes/jobs.py`` turns it into the job payload with ``model_dump()``. Any key
the model does not declare is dropped there, so a ``payload.get("x", default)``
in the adapter silently returns ``default`` forever and the setting is
unreachable over the API. That bug shipped twice: once for the provider fields
(``load_gemini_json``, ``nvidia_model``, ``gemini_fallback_model``) and once for
21 render/split/subtitle fields.

CI installs pytest and nothing else -- see .github/workflows/ci.yml, which keeps
this suite stdlib-only on purpose. So the regression guard reads the declared
field names out of ``models.py`` with ``ast`` rather than importing it, which
would pull in pydantic and abort collection. The handful of tests that genuinely
need a live model are marked with ``importorskip`` and simply do not run in CI.
``build_config_from_payload`` takes a plain dict, so the round-trip tests need no
model at all.
"""

import ast
import pathlib
import re
import shutil
import uuid

import pytest

from clipping.config import VIDEO_QUALITY_CQ
from web.api.config_adapter import build_config_from_payload

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

# The two modules that consume the job payload.
CONSUMERS = ("web/api/config_adapter.py", "web/api/worker.py")
MODELS = "web/api/models.py"

_PAYLOAD_GET = re.compile(r"""payload\.get\(\s*['"]([a-zA-Z0-9_]+)['"]""")


def _keys_read_by_backend() -> set[str]:
    src = "".join(
        (PROJECT_ROOT / rel).read_text(encoding="utf-8") for rel in CONSUMERS
    )
    return set(_PAYLOAD_GET.findall(src))


def _declared_fields() -> set[str]:
    """Field names on ``JobCreateRequest``, read without importing pydantic."""
    tree = ast.parse((PROJECT_ROOT / MODELS).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "JobCreateRequest":
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError(f"JobCreateRequest not found in {MODELS}")


@pytest.fixture
def job_id():
    jid = f"test_{uuid.uuid4().hex[:8]}"
    yield jid
    shutil.rmtree(PROJECT_ROOT / "outputs" / jid, ignore_errors=True)


# ---------------------------------------------------------------- the guard

def test_every_payload_key_is_declared():
    """Every key the backend reads must exist on the request model."""
    undeclared = _keys_read_by_backend() - _declared_fields()

    assert undeclared == set(), (
        "These keys are read via payload.get() but are not declared on "
        "JobCreateRequest, so Pydantic drops them and they always fall back to "
        f"their default: {sorted(undeclared)}"
    )


def test_the_guard_can_actually_see_the_fields():
    """Stop the ast reader silently returning an empty set, which would make
    the guard above pass no matter what."""
    declared = _declared_fields()

    assert len(declared) > 30, f"only found {len(declared)} fields -- ast reader broken"
    assert {"video_cq", "split_trigger", "yolo_size", "clips"} <= declared


# -------------------------------------------------- values survive the trip
# build_config_from_payload takes a plain dict, so these need no pydantic.

def test_video_cq_reaches_the_config(job_id):
    """Non-default video_cq must land on cfg, not fall back to the constant."""
    non_default = VIDEO_QUALITY_CQ + 7

    cfg = build_config_from_payload(
        {"upload_filename": "video.mp4", "video_cq": non_default}, job_id
    )

    assert cfg.video_quality_cq == non_default
    assert cfg.video_quality_cq != VIDEO_QUALITY_CQ


def test_split_trigger_reaches_the_config(job_id):
    """split_trigger is compared against "face" in the render layer."""
    cfg = build_config_from_payload(
        {"upload_filename": "video.mp4", "split_trigger": "face"}, job_id
    )

    assert cfg.split_trigger == "face"


def test_split_trigger_defaults_to_diarization(job_id):
    cfg = build_config_from_payload({"upload_filename": "video.mp4"}, job_id)

    assert cfg.split_trigger == "diarization"


def test_yolo_size_is_interpolated_as_a_plain_string(job_id):
    """yolo_size reaches a filename and a URL. routes/jobs.py unwraps the enum
    to its .value first; an un-unwrapped member would render as
    "face_yolovYoloSize.V9C.pt"."""
    cfg = build_config_from_payload(
        {"upload_filename": "video.mp4", "yolo_size": "9c"}, job_id
    )

    assert cfg.file_yolo_model.endswith("face_yolov9c.pt")
    assert cfg.url_yolo_model.endswith("face_yolov9c.pt")
    assert "YoloSize" not in cfg.url_yolo_model


def test_diarization_speakers_accepts_auto_or_an_int(job_id):
    """Mirrors _parse_speakers (clipping/config.py:169)."""
    auto = build_config_from_payload({"upload_filename": "video.mp4"}, job_id)
    assert auto.diarization_num_speakers == "auto"

    counted = build_config_from_payload(
        {"upload_filename": "video.mp4", "diarization_speakers": 3}, job_id
    )
    assert counted.diarization_num_speakers == 3


# ------------------------------------------- model-level (needs pydantic)
# CI is stdlib-only, so these skip there. They still run locally, where the
# real dependencies are installed.

def _model():
    pytest.importorskip("pydantic", reason="CI installs pytest only")
    from web.api.models import JobCreateRequest

    return JobCreateRequest


def _payload(**overrides) -> dict:
    """Build a payload the way routes/jobs.py does: dump, then replace each
    enum member with its .value."""
    dumped = _model()(upload_filename="video.mp4", **overrides).model_dump()
    return {
        key: (value.value if hasattr(value, "value") else value)
        for key, value in dumped.items()
    }


def test_declared_defaults_match_the_adapter_fallbacks():
    """A declared field whose default differs from its ``payload.get`` fallback
    changes behaviour depending on which entry point is used."""
    defaults = _payload()
    adapter = (PROJECT_ROOT / "web/api/config_adapter.py").read_text(encoding="utf-8")
    pairs = re.findall(
        r"""payload\.get\(\s*['"]([a-zA-Z0-9_]+)['"]\s*,\s*([^)]+?)\s*\)""", adapter
    )
    consts = {
        "VIDEO_QUALITY_CQ": 23,
        "VIDEO_QUALITY_CRF": 20,
        "VIDEO_PRESET": "auto",
        "VIDEO_SCALE_ALGO": "lanczos",
        "GEMINI_FALLBACK_MODEL": "gemini-2.5-flash",
    }

    mismatches = []
    for name, raw in pairs:
        if name not in defaults:
            continue
        if raw in consts:
            expected = consts[raw]
        else:
            try:
                expected = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                continue  # not a literal (e.g. str(RENDER_OUTPUT_HEIGHT))
        if defaults[name] != expected:
            mismatches.append((name, defaults[name], expected))

    assert mismatches == [], f"model default != adapter fallback: {mismatches}"


def test_undeclared_key_is_dropped_by_the_model():
    """Pin the mechanism itself, so the guard above cannot rot into a no-op."""
    dumped = _model()(
        upload_filename="video.mp4", not_a_real_setting=123
    ).model_dump()

    assert "not_a_real_setting" not in dumped


@pytest.mark.parametrize(
    "field, bad",
    [
        ("yolo_size", "../../etc/passwd"),
        ("split_trigger", "nonsense"),
        ("video_scale_algo", "nonsense"),
    ],
)
def test_constrained_fields_reject_values_the_cli_rejects(field, bad):
    """These three mirror argparse choices= lists in clipping/config.py."""
    model = _model()

    with pytest.raises(Exception):
        model(upload_filename="video.mp4", **{field: bad})

"""Guards that every job setting the backend reads is declared on the schema.

``JobCreateRequest`` is a default-strict Pydantic model, and
``routes/jobs.py`` turns it into the job payload with ``model_dump()``. Any key
the model does not declare is dropped there, so a ``payload.get("x", default)``
in the adapter silently returns ``default`` forever and the setting is
unreachable over the API. That bug shipped twice: once for the provider fields
(``load_gemini_json``, ``nvidia_model``, ``gemini_fallback_model``) and once for
21 render/split/subtitle fields.

``test_every_payload_key_is_declared`` is the regression guard -- it re-runs the
audit that found them. The rest prove the values actually survive the round trip,
which the audit alone cannot show.
"""

import pathlib
import re
import shutil
import uuid

import pytest

from clipping.config import VIDEO_QUALITY_CQ
from web.api.config_adapter import build_config_from_payload
from web.api.models import JobCreateRequest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

# The two modules that consume the job payload.
CONSUMERS = ("web/api/config_adapter.py", "web/api/worker.py")

_PAYLOAD_GET = re.compile(r"""payload\.get\(\s*['"]([a-zA-Z0-9_]+)['"]""")


def _keys_read_by_backend() -> set[str]:
    src = "".join(
        (PROJECT_ROOT / rel).read_text(encoding="utf-8") for rel in CONSUMERS
    )
    return set(_PAYLOAD_GET.findall(src))


@pytest.fixture
def job_id():
    jid = f"test_{uuid.uuid4().hex[:8]}"
    yield jid
    shutil.rmtree(PROJECT_ROOT / "outputs" / jid, ignore_errors=True)


def _payload(**overrides) -> dict:
    """Build a payload the way ``routes/jobs.py`` does.

    That route dumps the model and then replaces every enum member with its
    ``.value`` before the payload reaches the adapter. Tests must reproduce
    both steps: a bare ``model_dump()`` leaves enum members in place, and those
    format as ``YoloSize.V8M`` rather than ``8m`` when interpolated.
    """
    dumped = JobCreateRequest(upload_filename="video.mp4", **overrides).model_dump()
    return {
        key: (value.value if hasattr(value, "value") else value)
        for key, value in dumped.items()
    }


# ---------------------------------------------------------------- the guard

def test_every_payload_key_is_declared():
    """Every key the backend reads must exist on the request model."""
    undeclared = _keys_read_by_backend() - set(JobCreateRequest.model_fields)

    assert undeclared == set(), (
        "These keys are read via payload.get() but are not declared on "
        "JobCreateRequest, so Pydantic drops them and they always fall back to "
        f"their default: {sorted(undeclared)}"
    )


def test_declared_defaults_match_the_adapter_fallbacks():
    """A declared field whose default differs from its ``payload.get`` fallback
    changes behaviour depending on which entry point is used."""
    adapter = (PROJECT_ROOT / "web/api/config_adapter.py").read_text(encoding="utf-8")
    pairs = re.findall(
        r"""payload\.get\(\s*['"]([a-zA-Z0-9_]+)['"]\s*,\s*([^)]+?)\s*\)""", adapter
    )
    # Constants the adapter imports, resolved to their literal values.
    consts = {
        "VIDEO_QUALITY_CQ": 23,
        "VIDEO_QUALITY_CRF": 20,
        "VIDEO_PRESET": "auto",
        "VIDEO_SCALE_ALGO": "lanczos",
        "GEMINI_FALLBACK_MODEL": "gemini-2.5-flash",
    }
    defaults = _payload()

    mismatches = []
    for name, raw in pairs:
        if name not in defaults:
            continue
        if raw in consts:
            expected = consts[raw]
        else:
            try:
                expected = eval(raw, {"__builtins__": {}}, {})  # literals only
            except Exception:
                continue  # not a literal (e.g. str(RENDER_OUTPUT_HEIGHT))
        if defaults[name] != expected:
            mismatches.append((name, defaults[name], expected))

    assert mismatches == [], f"model default != adapter fallback: {mismatches}"


# -------------------------------------------------- values survive the trip

def test_video_cq_reaches_the_config(job_id):
    """Non-default video_cq must land on cfg, not fall back to the constant."""
    non_default = VIDEO_QUALITY_CQ + 7

    cfg = build_config_from_payload(_payload(video_cq=non_default), job_id)

    assert cfg.video_quality_cq == non_default
    assert cfg.video_quality_cq != VIDEO_QUALITY_CQ


def test_split_trigger_reaches_the_config(job_id):
    """split_trigger is compared against "face" in the render layer."""
    cfg = build_config_from_payload(_payload(split_trigger="face"), job_id)

    assert cfg.split_trigger == "face"


def test_split_trigger_defaults_to_diarization(job_id):
    cfg = build_config_from_payload(_payload(), job_id)

    assert cfg.split_trigger == "diarization"


def test_undeclared_key_is_dropped_by_the_model():
    """Pin the mechanism itself, so the guard above cannot rot into a no-op."""
    dumped = JobCreateRequest(
        upload_filename="video.mp4", not_a_real_setting=123
    ).model_dump()

    assert "not_a_real_setting" not in dumped


# --------------------------------------------------------- enum constraints

def test_yolo_size_is_interpolated_as_a_plain_string(job_id):
    """yolo_size reaches a filename and a URL, so it must format as "9c" --
    an un-unwrapped enum member would yield "face_yolovYoloSize.V9C.pt"."""
    cfg = build_config_from_payload(_payload(yolo_size="9c"), job_id)

    assert cfg.yolo_size == "9c"
    assert cfg.file_yolo_model.endswith("face_yolov9c.pt")
    assert cfg.url_yolo_model.endswith("face_yolov9c.pt")
    assert "YoloSize" not in cfg.url_yolo_model


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
    with pytest.raises(Exception):
        JobCreateRequest(upload_filename="video.mp4", **{field: bad})


def test_diarization_speakers_accepts_auto_or_an_int(job_id):
    """Mirrors _parse_speakers (clipping/config.py:169)."""
    auto = build_config_from_payload(_payload(), job_id)
    assert auto.diarization_num_speakers == "auto"

    counted = build_config_from_payload(_payload(diarization_speakers=3), job_id)
    assert counted.diarization_num_speakers == 3

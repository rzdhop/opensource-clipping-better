"""Clip length is chosen in the UI, not silently fixed at "auto".

`platform` selects the duration window every clip is snapped into
(clipping/analysis/presets.py -> clipping/analysis/snap.py): auto 20-75s,
tiktok/reels 15-90s, shorts 15-59s, long 60-179s. The API has accepted the field
since the snapper landed and it reaches the pipeline -- but NewJob.jsx never sent
it, so every job created from the dashboard was "auto" whatever the user wanted.
The same class of gap as the AI provider select, which could not reach "chain".

Two things are deliberately NOT asserted here, because
tests/test_dashboard_payload_contract.py already guards them for every key in the
`jobFields` literal: that the page actually sends `platform` (an undeclared key
is dropped by model_dump() and the control silently does nothing), and that
Clone & Rerun restores it. Verified non-vacuous: deleting the restore line turns
that file red.
"""

import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
NEW_JOB = PROJECT_ROOT / "web" / "dashboard" / "src" / "pages" / "NewJob.jsx"


def new_job_source():
    return NEW_JOB.read_text(encoding="utf-8")


def test_the_page_offers_every_preset_the_pipeline_knows():
    """A preset added to presets.py without an option here is unreachable from
    the UI, which is exactly the state this test was written to end."""
    from clipping.analysis.presets import PRESET_NAMES

    source = new_job_source()
    for name in PRESET_NAMES:
        assert f'<option value="{name}">' in source, f"no Clip Length option for {name!r}"


def test_the_default_is_still_auto():
    """Adding the control must not silently re-cut everyone's clips."""
    from clipping.analysis.presets import DEFAULT_PRESET
    from web.api.models import JobCreateRequest

    assert DEFAULT_PRESET == "auto"
    assert JobCreateRequest.model_fields["platform"].default.value == "auto"
    assert "const [platform, setPlatform] = useState('auto')" in new_job_source()


def test_the_api_carries_platform_through_to_the_pipeline():
    from web.api.config_adapter import build_config_from_payload

    cfg = build_config_from_payload({"platform": "long"}, "jobid", env_overrides={})
    assert cfg.platform == "long"

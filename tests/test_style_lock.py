"""Tests for clipping.aistory.stylelock (spec 2.2)."""

from __future__ import annotations

import copy

import pytest

from clipping.aistory import schemas, stylelock, templates

NOW = "2026-01-01T00:00:00Z"
LATER = "2026-01-02T00:00:00Z"


# ------------------------------------------------------------------ building

@pytest.mark.parametrize("style_id", templates.list_style_ids())
def test_build_style_lock_validates_for_every_shipped_template(style_id):
    template = templates.load_style(style_id)
    lock = stylelock.build_style_lock(template, now=NOW)
    assert schemas.style_lock_errors(lock) == []


def test_build_style_lock_copies_schema_and_template_identity():
    template = templates.load_style("fruit_drama")
    lock = stylelock.build_style_lock(template, now=NOW)
    assert lock["$schema"] == "style_lock_v1"
    assert lock["template_id"] == "fruit_drama"
    assert lock["template_version"] == template["version"]
    assert lock["template_name"] == template["name"]


def test_build_style_lock_drops_notes():
    template = templates.load_style("fruit_drama")
    assert "notes" in template
    lock = stylelock.build_style_lock(template, now=NOW)
    assert "notes" not in lock


def test_build_style_lock_sets_lock_bookkeeping_fields():
    template = templates.load_style("fruit_drama")
    lock = stylelock.build_style_lock(template, now=NOW)
    assert lock["locked_at"] is None
    assert lock["updated_at"] == NOW
    assert lock["overrides"] == {}


def test_build_style_lock_does_not_mutate_input_template():
    template = templates.load_style("fruit_drama")
    before = copy.deepcopy(template)
    stylelock.build_style_lock(template, {"palette.accents": ["#ABCDEF"]}, now=NOW)
    assert template == before


# ---------------------------------------------------------------- overrides

def test_valid_override_is_applied_and_recorded():
    template = templates.load_style("fruit_drama")
    lock = stylelock.build_style_lock(template, {"palette.accents": ["#ABCDEF"]}, now=NOW)
    assert lock["palette"]["accents"] == ["#ABCDEF"]
    assert lock["overrides"] == {"palette.accents": ["#ABCDEF"]}


def test_bad_hex_override_raises_and_lists_the_problem():
    template = templates.load_style("fruit_drama")
    with pytest.raises(stylelock.StyleLockError) as exc_info:
        stylelock.build_style_lock(template, {"typography.highlight_colour": "not-a-hex"}, now=NOW)
    assert any("typography.highlight_colour" in e for e in exc_info.value.errors)


@pytest.mark.parametrize("bad_path", ["camera", "palette.nope", "__class__"])
def test_unknown_override_path_raises(bad_path):
    template = templates.load_style("fruit_drama")
    with pytest.raises(stylelock.StyleLockError) as exc_info:
        stylelock.build_style_lock(template, {bad_path: "whatever"}, now=NOW)
    assert any(bad_path in e for e in exc_info.value.errors)


def test_wrong_type_override_raises():
    template = templates.load_style("fruit_drama")
    with pytest.raises(stylelock.StyleLockError) as exc_info:
        stylelock.build_style_lock(template, {"typography.ai_label": "yes"}, now=NOW)
    assert any("typography.ai_label" in e for e in exc_info.value.errors)


def test_enum_override_outside_closed_list_raises():
    template = templates.load_style("fruit_drama")
    with pytest.raises(stylelock.StyleLockError) as exc_info:
        stylelock.build_style_lock(
            template, {"typography.subtitle_mode": "scrolling_marquee"}, now=NOW
        )
    assert any("typography.subtitle_mode" in e for e in exc_info.value.errors)


def test_multiple_invalid_overrides_are_all_listed():
    template = templates.load_style("fruit_drama")
    with pytest.raises(stylelock.StyleLockError) as exc_info:
        stylelock.build_style_lock(
            template,
            {
                "camera": "nope",
                "typography.highlight_colour": "not-a-hex",
                "typography.subtitle_mode": "bogus",
            },
            now=NOW,
        )
    errors = exc_info.value.errors
    assert any("camera" in e for e in errors)
    assert any("typography.highlight_colour" in e for e in errors)
    assert any("typography.subtitle_mode" in e for e in errors)


def test_invalid_overrides_leave_nothing_partially_applied():
    template = templates.load_style("fruit_drama")
    original_accents = list(template["palette"]["accents"])
    try:
        stylelock.build_style_lock(
            template,
            {
                "palette.accents": ["#ABCDEF"],
                "typography.highlight_colour": "not-a-hex",
            },
            now=NOW,
        )
    except stylelock.StyleLockError:
        pass
    # The template itself must be untouched regardless.
    assert template["palette"]["accents"] == original_accents


# ------------------------------------------------------------- apply_overrides

def test_apply_overrides_merges_and_rederives_from_template():
    template = templates.load_style("fruit_drama")
    lock = stylelock.build_style_lock(template, {"palette.accents": ["#111111"]}, now=NOW)
    updated = stylelock.apply_overrides(lock, {"typography.ai_label": False}, now=LATER)
    assert updated["palette"]["accents"] == ["#111111"]
    assert updated["typography"]["ai_label"] is False
    assert updated["overrides"] == {
        "palette.accents": ["#111111"],
        "typography.ai_label": False,
    }
    assert updated["updated_at"] == LATER


def test_apply_overrides_repeated_path_keeps_the_newer_value():
    template = templates.load_style("fruit_drama")
    lock = stylelock.build_style_lock(template, {"palette.accents": ["#111111"]}, now=NOW)
    updated = stylelock.apply_overrides(lock, {"palette.accents": ["#222222"]}, now=LATER)
    assert updated["palette"]["accents"] == ["#222222"]
    assert updated["overrides"] == {"palette.accents": ["#222222"]}


def test_apply_overrides_refuses_when_locked():
    template = templates.load_style("fruit_drama")
    lock = stylelock.build_style_lock(template, now=NOW)
    locked = stylelock.lock_style(lock, now=NOW)
    with pytest.raises(stylelock.StyleLockError):
        stylelock.apply_overrides(locked, {"palette.accents": ["#111111"]}, now=LATER)


def test_apply_overrides_refuses_on_template_version_mismatch():
    template = templates.load_style("fruit_drama")
    lock = stylelock.build_style_lock(template, now=NOW)
    lock["template_version"] = template["version"] + 1
    with pytest.raises(stylelock.StyleLockError):
        stylelock.apply_overrides(lock, {"palette.accents": ["#111111"]}, now=LATER)


# ----------------------------------------------------------------- lock_style

def test_lock_style_sets_locked_at():
    template = templates.load_style("fruit_drama")
    lock = stylelock.build_style_lock(template, now=NOW)
    locked = stylelock.lock_style(lock, now=LATER)
    assert locked["locked_at"] == LATER
    # The original lock is untouched.
    assert lock["locked_at"] is None


def test_lock_style_refuses_when_already_locked():
    template = templates.load_style("fruit_drama")
    lock = stylelock.build_style_lock(template, now=NOW)
    locked = stylelock.lock_style(lock, now=NOW)
    with pytest.raises(stylelock.StyleLockError):
        stylelock.lock_style(locked, now=LATER)


# ------------------------------------------------------------------- purity

def test_build_style_lock_is_pure():
    template = templates.load_style("fruit_drama")
    lock1 = stylelock.build_style_lock(template, {"palette.accents": ["#ABCDEF"]}, now=NOW)
    lock2 = stylelock.build_style_lock(template, {"palette.accents": ["#ABCDEF"]}, now=NOW)
    assert lock1 == lock2


def test_apply_overrides_is_pure():
    template = templates.load_style("fruit_drama")
    base = stylelock.build_style_lock(template, {"palette.accents": ["#111111"]}, now=NOW)
    result1 = stylelock.apply_overrides(base, {"typography.ai_label": False}, now=LATER)
    result2 = stylelock.apply_overrides(base, {"typography.ai_label": False}, now=LATER)
    assert result1 == result2


# ==================================================== immutability (RC-M4)
#
# Acceptance (phase 5 stage 12): "a stored style_lock.json never changes
# when its template does." The behaviour already holds -- build_style_lock
# builds every field off ``copy.deepcopy(template)`` -- so this is new
# coverage, not a bug fix. The deliberate-mutation proof (make
# build_style_lock keep a plain reference instead of a deep copy, watch this
# test fail, then revert) is recorded in the stage-12 report, not committed.

@pytest.mark.parametrize("style_id", templates.list_style_ids())
def test_a_lock_never_changes_when_its_own_template_is_mutated_afterwards(style_id):
    template = templates.load_style(style_id)
    lock = stylelock.build_style_lock(template, now=NOW)
    before = copy.deepcopy(lock)

    # Mutate every level of the template a real edit could touch, deeply.
    template["motion_rules"]["tier1"]["pan_pct"] = 999
    template["motion_rules"]["tier1"]["overlays"].append("mutated_overlay")
    template["palette"]["primary"].append("#000000")
    template["palette"]["accents"].clear()
    template["typography"]["font_family"] = "Mutated Family"
    template["episode_defaults"]["hook_style"] = "mutated"
    template["version"] = 999999

    assert lock == before, f"{style_id}: the stored lock moved when its template was mutated afterwards"


def test_a_lock_never_changes_when_a_second_lock_is_built_and_mutated(style_id="fruit_drama"):
    """Two locks built from the SAME template must be fully independent of
    each other too -- mutating one (as a caller holding a reference might)
    must never reach the other."""
    template = templates.load_style(style_id)
    lock_a = stylelock.build_style_lock(template, now=NOW)
    lock_b = stylelock.build_style_lock(template, now=NOW)
    before_a = copy.deepcopy(lock_a)

    lock_b["palette"]["primary"][0] = "#000000"
    lock_b["motion_rules"]["tier1"]["pan_pct"] = 1

    assert lock_a == before_a

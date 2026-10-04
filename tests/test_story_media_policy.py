"""AI Story phase 7, stage 2a (DEC-221): the image link per role of a v2
story (``generation_profile.pipeline: "v2"``).

A v2 story's sheets, plates, props and keyframes run on the links of its
budget profile's ``roles`` table -- never on a draft-quality link -- while a
legacy story keeps exactly today's env chain. A v2 story stays in
``references`` mode, its stop-and-ask never offers prompt-only, and its
keyframes are stored as an exact 9:16. Stdlib + pytest only (DEC-012).
"""

from __future__ import annotations

import json
import struct
import subprocess

import pytest

from clipping.aistory import defaults, imaging, media_policy, refimages, workflow
from clipping.aistory import store as story_store
from clipping.aistory.steps import assets
from clipping.providers import budget as budget_mod
from clipping.providers import generation as gen
from clipping.providers.registry import describe

NOW = "2026-10-01T10:00:00+00:00"

# Every key a link could want, and env chains that list the cheap links first.
CHEAP_FIRST = {
    "IMAGE_CHAIN": ("cloudflare/flux-1-schnell,pollinations/flux,fal/flux-schnell,openai/gpt-image-2-low,"
                    "gemini/nano-banana-2"),
    "IMAGE_EDIT_CHAIN": "openai/gpt-image-2-low,fal/seedream-4-edit,gemini/nano-banana-2-lite",
    "CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ACCOUNT_ID": "a", "POLLINATIONS_API_KEY": "p",
    "FAL_KEY": "fk", "OPENAI_API_KEY": "ok", "GOOGLE_API_KEY": "gk", "GEMINI_PAID_API_KEY": "pk",
}


def v2_story():
    return {"story_id": "0123456789ab", "generation_profile": defaults.quality_generation_profile()}


def legacy_story():
    return {"story_id": "0123456789ab", "generation_profile": defaults.default_generation_profile()}


def labels(chain):
    return [describe(link) for link in chain]


def test_v2_roles_never_use_low_quality_links(monkeypatch, tmp_path):
    story = v2_story()
    for role in media_policy.ROLES:
        for kind in (gen.IMAGE, gen.IMAGE_EDIT):
            chain = labels(media_policy.role_chain(role, kind, CHEAP_FIRST, story))
            assert chain and not set(chain) & media_policy.LOW_QUALITY_LINKS, (role, kind, chain)
    # Re-pinned on purpose (DEC-235): the quality sheet/plate/prop roles moved
    # from gemini/nano-banana-2 to fal (text-to-image on IMAGE, its edit
    # sibling on IMAGE_EDIT).
    # Re-pinned (plan 23 stage A8, DEC-280): lite follows fal as the second link of sheet/plate/prop.
    assert labels(media_policy.role_chain("sheet", gen.IMAGE, CHEAP_FIRST, story)) == [
        "fal/seedream-4.5", "gemini/nano-banana-2-lite"]
    assert labels(media_policy.role_chain("keyframe", gen.IMAGE_EDIT, CHEAP_FIRST, story)) == [
        "fal/seedream-4.5-edit", "gemini/nano-banana-2-lite"]

    # The chain every image site resolves is the role's (RC-V6: the estimate and the run agree).
    _merged, chain, _budget = imaging.resolve(gen.IMAGE, CHEAP_FIRST, error=RuntimeError, role="plate", story=story)
    assert labels(chain) == ["fal/seedream-4.5", "gemini/nano-banana-2-lite"]  # re-pinned (DEC-235, DEC-280)
    estimate = imaging.estimate(gen.IMAGE_EDIT, CHEAP_FIRST, route="api", request=gen.GenRequest(
        kind=gen.IMAGE_EDIT, width=720, height=1280), step="t", what="w", when="w", role="keyframe", story=story,
        adapters={})
    assert [row["link"] for row in estimate["links"]] == ["fal/seedream-4.5-edit", "gemini/nano-banana-2-lite"]

    # Defensive: a roles table that names a draft link never hands it out.
    profiles = budget_mod.load_profiles()
    profiles["profiles"]["quality"]["roles"]["prop"] = ["pollinations/flux", "gemini/nano-banana-2"]
    path = tmp_path / "budget_profiles.json"
    path.write_text(json.dumps(profiles), encoding="utf-8")
    monkeypatch.setattr(budget_mod, "PROFILES_PATH", str(path))
    assert labels(media_policy.role_chain("prop", gen.IMAGE, CHEAP_FIRST, story)) == ["gemini/nano-banana-2"]


def test_legacy_story_keeps_env_chain():
    story = legacy_story()
    for role in media_policy.ROLES:
        for kind in (gen.IMAGE, gen.IMAGE_EDIT):
            assert media_policy.role_chain(role, kind, CHEAP_FIRST, story) == gen.chain_from_env(kind, CHEAP_FIRST)
    assert media_policy.role_chain("keyframe", gen.IMAGE_EDIT, {}, story) == gen.chain_from_env(gen.IMAGE_EDIT, {})
    _merged, chain, _budget = imaging.resolve(gen.IMAGE, CHEAP_FIRST, error=RuntimeError, role="sheet", story=story)
    assert chain == gen.chain_from_env(gen.IMAGE, imaging.gating.merged_env(CHEAP_FIRST))


def test_v2_sheet_role_uses_fal_text_to_image_then_edit():
    """Stage 2c (DEC-235): the quality sheet role names ``[fal/seedream-4.5,
    fal/seedream-4.5-edit]`` -- a text-to-image link next to its edit
    sibling, so one roles list serves both request kinds. ``role_chain``
    keeps only the link each kind can actually run: the edit-only link is
    dropped for gen.IMAGE (it needs references gen.IMAGE never sends), the
    text-only link for gen.IMAGE_EDIT (it has no image_urls field).
    plate/prop are the same roles list and behave identically.

    Re-pinned (plan 23 stage A8, DEC-280): the roles list ends with
    ``gemini/nano-banana-2-lite``, which serves both kinds, so each chain is
    the fal link of its kind, then lite."""
    story = v2_story()
    for role in ("sheet", "plate", "prop"):
        assert labels(media_policy.role_chain(role, gen.IMAGE, CHEAP_FIRST, story)) == [
            "fal/seedream-4.5", "gemini/nano-banana-2-lite"]
        assert labels(media_policy.role_chain(role, gen.IMAGE_EDIT, CHEAP_FIRST, story)) == [
            "fal/seedream-4.5-edit", "gemini/nano-banana-2-lite"]
        for kind in (gen.IMAGE, gen.IMAGE_EDIT):
            chain = labels(media_policy.role_chain(role, kind, CHEAP_FIRST, story))
            assert not set(chain) & media_policy.LOW_QUALITY_LINKS

    # A legacy story never sees this filter: it keeps exactly today's env chain.
    legacy = legacy_story()
    for kind in (gen.IMAGE, gen.IMAGE_EDIT):
        assert media_policy.role_chain("sheet", kind, CHEAP_FIRST, legacy) == gen.chain_from_env(kind, CHEAP_FIRST)


def test_v2_story_refuses_prompt_only(tmp_path):
    stories = story_store.StoryStore(tmp_path / "outputs", on_log=lambda *a: None)
    with pytest.raises(ValueError) as excinfo:
        stories.create(language="fr", generation_profile={**defaults.quality_generation_profile(),
                                                          "consistency_mode": "prompt_only"}, now=NOW)
    assert "references" in str(excinfo.value)
    story = stories.create(language="fr", generation_profile=defaults.quality_generation_profile(), now=NOW)
    assert story["generation_profile"]["pipeline"] == "v2"
    with pytest.raises(workflow.WorkflowError) as excinfo:
        workflow.patch_story(stories, story["story_id"],
                             {"generation_profile": {"consistency_mode": "prompt_only"}}, now=NOW)
    assert excinfo.value.code == workflow.INVALID
    assert stories.get(story["story_id"])["generation_profile"]["consistency_mode"] == "references"


def test_v2_needs_editor_never_offers_prompt_only():
    rows = [{"link": "fal/seedream-4.5-edit", "status": "skipped", "reason": "no API key (FAL_KEY is not set)",
             "paid": True, "est_usd": 0.04},
            {"link": "gemini/nano-banana-2-lite", "status": "skipped",
             "reason": "no API key (GEMINI_PAID_API_KEY is not set)", "paid": True, "est_usd": 0.0336}]
    readiness = imaging.blocked("image_edit", 1, rows, "No link can run.")
    reasons = [f"{row['link']}: {row['reason']}" for row in rows]
    v2 = str(refimages.NeedsEditor(reasons, readiness, subject="Shot sh01", story=v2_story()))
    assert "prompt-only" not in v2 and "prompt_only" not in v2
    assert "FAL_KEY" in v2 and "GEMINI_PAID_API_KEY" in v2 and "$0.040" in v2
    legacy = str(refimages.NeedsEditor(reasons, readiness, subject="Shot sh01", story=legacy_story()))
    assert legacy == str(refimages.NeedsEditor(reasons, readiness, subject="Shot sh01")) == (
        "Shot sh01 needs an editor: No link can run. Nothing was generated or spent. "
        "Start ComfyUI, or allow a paid editor, or switch the story to prompt-only consistency.")


def _png(path, width, height):
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", width, height) + b"\x00" * 8)
    return str(path)


def test_v2_keyframe_is_cropped_to_an_exact_9x16_at_the_source(tmp_path):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        _png(tmp_path / argv[-1].rsplit("/", 1)[-1], 756, 1344)
        return subprocess.CompletedProcess(argv, 0, "", "")

    produced = _png(tmp_path / "shot_01.png", 768, 1376)
    out = assets.v2_keyframe_source(v2_story(), produced, out_dir=str(tmp_path), run=fake_run)
    assert len(calls) == 1
    argv = calls[0]
    assert argv[0] == "ffmpeg" and argv[argv.index("-i") + 1] == produced
    assert argv[argv.index("-vf") + 1] == "crop=756:1344" and argv[argv.index("-frames:v") + 1] == "1"
    assert out == argv[-1] and out.endswith(".png") and out != produced

    calls.clear()
    assert assets.v2_keyframe_source(legacy_story(), produced, out_dir=str(tmp_path), run=fake_run) == produced
    exact = _png(tmp_path / "exact.png", 720, 1280)
    assert assets.v2_keyframe_source(v2_story(), exact, out_dir=str(tmp_path), run=fake_run) == exact
    assert calls == []

    def no_ffmpeg(argv, **kwargs):
        raise FileNotFoundError("ffmpeg")

    with pytest.raises(assets.ShotFailed) as excinfo:
        assets.v2_keyframe_source(v2_story(), produced, out_dir=str(tmp_path), run=no_ffmpeg)
    assert "ffmpeg" in str(excinfo.value)

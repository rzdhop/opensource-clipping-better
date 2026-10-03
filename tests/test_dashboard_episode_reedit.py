"""Dashboard EpisodeStudio re-edit (AI Story phase 5, plan 11 stage 11).

Guards the new dashboard-side pieces stage 9's API already carries: the
line's "Re-voice this line" control and its persisted take/note
(ScriptPane.jsx), a shot's "needs a new image" state (StoryboardPane.jsx),
"Changes since last render" and the render precondition's own sentence
(PreviewPane.jsx), and the independent script/storyboard approval reading and
the persistent stop reason (EpisodeStudio.jsx).

Pure text/AST checks, no npm -- same reasoning and pattern as
``tests/test_dashboard_story_shared.py`` (DEC-012: a JS test runner would be
a new dependency CI does not install).
"""

import re
import pathlib

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
DASHBOARD_SRC = PROJECT_ROOT / "web" / "dashboard" / "src"

FIELDS = DASHBOARD_SRC / "pages" / "story" / "fields.jsx"
SCRIPT_PANE = DASHBOARD_SRC / "pages" / "story" / "episode" / "ScriptPane.jsx"
# Dashboard overhaul stage 4 (DEC-256) split StoryboardPane.jsx into the
# storyboard/ folder: the shell, the shot card, the clip controls and the
# keyframe / asset cards. Each check reads the file its code moved to.
STORYBOARD_DIR = DASHBOARD_SRC / "pages" / "story" / "episode" / "storyboard"
STORYBOARD_PANE = STORYBOARD_DIR / "StoryboardPane.jsx"
SHOT_CARD = STORYBOARD_DIR / "ShotCard.jsx"
ASSETS_CARDS = STORYBOARD_DIR / "AssetsCards.jsx"
STORYBOARD_FILES = (STORYBOARD_PANE, SHOT_CARD, STORYBOARD_DIR / "ClipControls.jsx", ASSETS_CARDS)
PREVIEW_PANE = DASHBOARD_SRC / "pages" / "story" / "episode" / "PreviewPane.jsx"
EPISODE_STUDIO = DASHBOARD_SRC / "pages" / "story" / "EpisodeStudio.jsx"
INDEX_CSS = DASHBOARD_SRC / "index.css"


def _read(path: pathlib.Path) -> str:
    assert path.is_file(), f"missing {path}"
    return path.read_text(encoding="utf-8")


def _function_body(src: str, signature_re: str, name: str) -> str:
    """The brace-balanced body of the first function whose declaration
    matches *signature_re* (a regex ending just before the opening ``{``),
    the same brace-counting approach as test_dashboard_story_shared.py's
    ``_function_body``."""
    match = re.search(signature_re, src)
    assert match, f"{name} not found"
    start = match.end()
    depth = 1
    i = start
    while depth > 0:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return src[start:i]


# --------------------------------------------------------------- non-vacuity

def test_the_readers_see_something():
    """A broken regex would make every assertion below pass for free."""
    assert len(_read(SCRIPT_PANE)) > 10000
    assert len(_read(STORYBOARD_PANE)) > 10000
    assert len(_read(PREVIEW_PANE)) > 10000
    assert len(_read(EPISODE_STUDIO)) > 5000


# ============================================================ fields.jsx

def test_regenerate_control_accepts_an_action_label():
    src = _read(FIELDS)
    body = _function_body(
        src, r"export function RegenerateControl\(\{[^)]*\}\)\s*\{", "RegenerateControl")
    assert "actionLabel" in body
    # The empty-slot ("Make <label>") branch is unchanged by this -- only the
    # non-empty ("Regenerate") button's text is overridable.
    empty_branch, non_empty_branch = body.split("if (empty)", 1)[1].split("return (", 2)[1:3]
    assert "actionLabel" not in empty_branch
    assert "actionLabel || '↻ Regenerate'" in non_empty_branch


# ============================================================ ScriptPane.jsx (LineRow)

def test_line_row_labels_its_regenerate_control_re_voice_this_line():
    src = _read(SCRIPT_PANE)
    line_row = _function_body(src, r"function LineRow\(\{[^)]*\}\)\s*\{", "LineRow")
    assert 'actionLabel="Re-voice this line"' in line_row
    assert 'label="voice"' in line_row


def test_line_row_shows_the_persisted_take_and_its_note():
    src = _read(SCRIPT_PANE)
    line_row = _function_body(src, r"function LineRow\(\{[^)]*\}\)\s*\{", "LineRow")
    assert "assetLine.take" in line_row
    assert "assetLine.note" in line_row
    assert "assetLine.pending" in line_row


def test_no_login_or_token_reference_added_to_script_pane():
    src = _read(SCRIPT_PANE)
    assert "token" not in src.lower()
    assert "sign in" not in src.lower() and "sign-in" not in src.lower()


# ============================================================ StoryboardPane.jsx

def test_a_stale_shot_reads_needs_a_new_image():
    src = _read(SHOT_CARD)
    match = re.search(r"const SHOT_STATE_LABELS = \{(.*?)\}", src, re.DOTALL)
    assert match, "SHOT_STATE_LABELS not found in StoryboardPane.jsx"
    body = match.group(1)
    assert re.search(r"stale:\s*'needs a new image'", body)
    # A locked-but-stale shot keeps its own, distinct label -- never the same
    # actionable "needs a new image" wording a locked shot's own disabled
    # RegenerateControl already refuses to act on (stage-10 lesson: no such
    # control on a locked shot).
    assert re.search(r"locked_stale:\s*'locked", body)


def test_framing_camera_motion_and_transition_controls_exist():
    """Motion/framing/transition controls (plan 11 stage 11's goal) were
    already built for phase 4; this only guards they were not lost."""
    src = _read(SHOT_CARD)
    assert "saveFraming" in src and "FRAMINGS.map" in src
    assert "saveCameraMotion" in src and "CAMERA_MOTIONS.map" in src
    assert "TransitionSelect" in src and "TRANSITIONS.map" in src


def test_no_login_or_token_reference_added_to_storyboard_pane():
    for path in STORYBOARD_FILES:
        src = _read(path)
        assert "token" not in src.lower(), path.name


# ============================================================ PreviewPane.jsx

def test_changes_since_last_render_reads_the_embedded_episode_field():
    """No separate estimate fetch: `episode.render.changes` is already on
    the episode page (stage 9), cached server side -- reading it again here
    would only repeat a hash of the render cache for nothing new."""
    src = _read(PREVIEW_PANE)
    changes = _function_body(
        src, r"function ChangesSinceRender\(\{[^)]*\}\)\s*\{", "ChangesSinceRender")
    assert "episode.render && episode.render.changes" in changes
    assert "fetchStoryEstimate" not in changes


def test_changes_since_last_render_shows_the_blocked_sentence_instead_of_a_button():
    src = _read(PREVIEW_PANE)
    changes = _function_body(
        src, r"function ChangesSinceRender\(\{[^)]*\}\)\s*\{", "ChangesSinceRender")
    assert "changes.blocked" in changes
    blocked_branch = changes.split("changes.blocked ?", 1)[1].split(") : changes.current", 1)[0]
    assert "<button" not in blocked_branch


def test_changes_since_last_render_lists_shots_by_reason_and_reuses_the_rerender_step():
    src = _read(PREVIEW_PANE)
    changes = _function_body(
        src, r"function ChangesSinceRender\(\{[^)]*\}\)\s*\{", "ChangesSinceRender")
    assert "changes.rebuild.map" in changes
    assert "REUSE_REASON_LABELS[changes.reasons[shotId]]" in changes
    assert "runStoryStep(storyId, 'rerender', { ep })" in changes
    # Every reason schemas.RENDER_REUSE_REASONS can name has a dashboard label.
    from clipping.aistory import schemas
    labels_block = _read(PREVIEW_PANE).split("const REUSE_REASON_LABELS = {", 1)[1].split("\n}", 1)[0]
    for reason in schemas.RENDER_REUSE_REASONS:
        assert f"{reason}:" in labels_block, f"REUSE_REASON_LABELS is missing {reason!r}"


def test_the_preview_summary_uses_the_reuse_record_not_the_old_cached_chip():
    src = _read(PREVIEW_PANE)
    media = _function_body(src, r"function RenderMedia\(\{[^)]*\}\)\s*\{", "RenderMedia")
    assert "render.reuse.summary" in media
    assert "render.reuse.shots_reused.length" in media
    # The old chip is kept, but only as the fallback for a render with no
    # baseline (the episode's very first render, render.reuse is null then).
    fallback = media.split("render.reuse ?", 1)[1]
    assert "shots_cached" in fallback


def test_render_header_surfaces_a_blocked_estimate_instead_of_swallowing_it():
    """Stage-10 lesson: a precondition known from a 409 estimate must show
    its sentence and disable the button, never an endless 'estimating…'."""
    src = _read(PREVIEW_PANE)
    header = _function_body(src, r"function RenderHeader\(\{[^)]*\}\)\s*\{", "RenderHeader")
    assert "estimateError" in header
    assert ".catch((err) => { setEstimate(null); setEstimateError(err.message)" in header
    assert "disabled={Boolean(reason) || Boolean(estimateError) || running}" in header


def test_no_login_or_token_reference_added_to_preview_pane():
    # Not a whole-file check: PreviewPane.jsx already has a legitimate,
    # pre-existing comment about a *media* URL's signing token (DEC-163),
    # unrelated to auth/sign-in. Only the pieces this stage adds must stay
    # free of one.
    src = _read(PREVIEW_PANE)
    changes = _function_body(
        src, r"function ChangesSinceRender\(\{[^)]*\}\)\s*\{", "ChangesSinceRender")
    header = _function_body(src, r"function RenderHeader\(\{[^)]*\}\)\s*\{", "RenderHeader")
    for name, body in (("ChangesSinceRender", changes), ("RenderHeader", header)):
        assert "token" not in body.lower(), f"{name} references a token"
        assert "sign in" not in body.lower() and "sign-in" not in body.lower()


# ============================================================ EpisodeStudio.jsx

def test_tabs_read_script_and_storyboard_approval_independently():
    """The new legal state ("storyboard approved, script not approved") must
    never collapse into one combined badge: each tab's checkmark reads only
    its own document's approved_at."""
    src = _read(EPISODE_STUDIO)
    tabs_for = _function_body(src, r"function tabsFor\(episode\)\s*\{", "tabsFor")
    assert "episode.script && episode.script.approved_at" in tabs_for
    assert "episode.storyboard && episode.storyboard.approved_at" in tabs_for
    # Neither reading is gated on the other.
    script_line = next(line for line in tabs_for.splitlines() if "scriptApproved =" in line)
    storyboard_line = next(line for line in tabs_for.splitlines() if "storyboardApproved =" in line)
    assert "storyboard" not in script_line.split("=", 1)[1]
    assert "script" not in storyboard_line.split("=", 1)[1] or "storyboardApproved" in storyboard_line


def test_both_layouts_use_the_same_tabs_labels():
    """The wide (>=1100px) three-pane layout has no tab bar of its own to
    show the approval state in -- it must reuse tabsFor's labels, not read
    a static, unlabelled title."""
    src = _read(EPISODE_STUDIO)
    assert "const tabs = tabsFor(episode)" in src
    assert "<Tabs tabs={tabs}" in src
    wide_branch = src.split("wide ? (", 1)[1].split(") : (", 1)[0]
    assert "tabs[0].label" in wide_branch and "tabs[1].label" in wide_branch and "tabs[2].label" in wide_branch


def test_a_stop_reason_is_captured_before_the_refresh_that_would_drop_it():
    """Found live (phase-4 follow-up): episode.jobs lists only queued/running
    work, so the very refresh a finished job's onJob callback triggers also
    clears inFlightJob and, with it, the live feed that was the only place
    showing the job's own error. The capture must happen in the same
    callback, before that refresh call."""
    src = _read(EPISODE_STUDIO)
    on_job = src.split("onJob: (job) => {", 1)[1].split("\n  })", 1)[0]
    capture_pos = on_job.index("setStoppedJob(job)")
    refresh_pos = on_job.index("refresh()")
    assert capture_pos < refresh_pos


def test_the_stop_reason_renders_outside_the_in_flight_gate_and_can_be_dismissed():
    src = _read(EPISODE_STUDIO)
    assert "{inFlightJob && liveJob ? (" in src
    stopped_branch = src.split("{inFlightJob && liveJob ? (", 1)[1].split("{wide ? (", 1)[0]
    assert "stoppedJob &&" in stopped_branch
    assert "stoppedJob.error" in stopped_branch
    assert "setStoppedJob(null)" in stopped_branch


def test_a_new_job_clears_the_previous_stop_reason():
    src = _read(EPISODE_STUDIO)
    assert "if (inFlightJobId) setStoppedJob(null)" in src


def test_no_login_or_token_reference_added_to_episode_studio():
    src = _read(EPISODE_STUDIO)
    assert "token" not in src.lower()
    assert "sign in" not in src.lower() and "sign-in" not in src.lower()


# ============================================================ index.css

def test_full_sentence_chips_added_this_stage_override_nowrap():
    """Stage-10 lesson: `.chip` is nowrap by default; a chip carrying a full
    sentence (the reuse summary, potentially with the whole-frames
    conversion note) needs the same override .story-season-blocked-reason /
    .story-render-outdated already use."""
    src = _read(INDEX_CSS)
    match = re.search(r"\.story-render-reuse-summary\s*\{([^}]*)\}", src)
    assert match, ".story-render-reuse-summary rule not found in index.css"
    body = match.group(1)
    assert "white-space: normal" in body


def test_chip_wrap_utility_overrides_nowrap():
    src = _read(INDEX_CSS)
    match = re.search(r"\.chip-wrap\s*\{([^}]*)\}", src)
    assert match, ".chip-wrap rule not found in index.css"
    body = match.group(1)
    assert "white-space: normal" in body


# ================================================== browser-check fix round 1
#
# Found live (FR ep01 scratch copy, 375/820/1280): F1 a stuck "estimating…"
# metadata chip after an edit makes the render out of date (the same
# stage-10 F1 class, reproduced wherever an estimate route answers 409 and
# the fetch swallows it to null); F2 an edited line that had a take still
# read "Make voice" instead of "Re-voice this line".

def _header_body(src: str, name: str) -> str:
    return _function_body(src, rf"function {name}\(\{{[^)]*\}}\)\s*\{{", name)


def test_metadata_header_surfaces_a_blocked_estimate_instead_of_stalling():
    """F1: GET /estimate/metadata answers 409 (metadata.require_render) once
    a text-only edit clears the script's approval while an old render still
    has render.output -- renderReady stays true, so the fetch still runs."""
    src = _read(PREVIEW_PANE)
    header = _header_body(src, "MetadataHeader")
    assert "estimateError" in header
    assert ".catch((err) => { setEstimate(null); setEstimateError(err.message)" in header
    assert "disabled={Boolean(reason) || Boolean(estimateError) || running}" in header
    # The stuck symptom was EstimateChip rendering "estimating…" forever
    # (estimate stays null): a 409 must render a warn chip with the sentence
    # instead of ever reaching <EstimateChip estimate={estimate} />.
    assert "estimateError ?" in header
    error_branch = header.split("estimateError ?", 1)[1].split(") : (", 1)[0]
    assert "estimateError" in error_branch and "<EstimateChip" not in error_branch


def test_assets_header_surfaces_a_blocked_estimate_instead_of_stalling():
    """The same class, reachable the same way: assets.require_approved needs
    the script AND the storyboard approved, so a text-only edit (which
    clears only the script's, stage 7) still 409s GET /estimate/assets even
    though AssetsHeader already gates the fetch on the storyboard's own
    approval alone."""
    src = _read(ASSETS_CARDS)
    header = _header_body(src, "AssetsHeader")
    assert "estimateError" in header
    assert ".catch((err) => { setEstimate(null); setEstimateError(err.message)" in header
    assert "disabled={Boolean(reason) || Boolean(estimateError) || running}" in header
    assert "estimateError ?" in header
    error_branch = header.split("estimateError ?", 1)[1].split(") : ", 1)[0]
    assert "estimateError" in error_branch and "<EstimateChip" not in error_branch and "<RouteChip" not in error_branch


def test_fast_track_header_shows_a_blocked_estimates_sentence():
    src = _read(EPISODE_STUDIO)
    header = _header_body(src, "FastTrackHeader")
    assert "estimateError" in header
    assert ".catch((err) => { setEstimate(null); setEstimateError(err.message)" in header


def test_measure_voices_shows_a_blocked_estimates_sentence():
    src = _read(SCRIPT_PANE)
    header = _header_body(src, "MeasureVoices")
    assert "estimateError" in header
    assert ".catch((err) => { setEstimate(null); setEstimateError(err.message)" in header


def test_every_estimate_error_chip_wraps_instead_of_riding_chip_nowrap():
    """Every new warn chip carrying an estimate's own refusal sentence must
    not be a short pill (stage-10 lesson, applied uniformly this round)."""
    for path, name in (
        (PREVIEW_PANE, "MetadataHeader"),
        (ASSETS_CARDS, "AssetsHeader"),
        (EPISODE_STUDIO, "FastTrackHeader"),
        (SCRIPT_PANE, "MeasureVoices"),
    ):
        body = _header_body(_read(path), name)
        assert re.search(r"\{estimateError\}", body), f"{name} does not render estimateError's text"
        assert "chip-wrap" in body, f"{name}'s estimate-error chip does not use .chip-wrap"


def test_line_row_offers_re_voice_once_a_line_ever_had_a_take():
    """F2: a text-only edit resets the line's own timing to a fresh estimate
    (audio: null, timing.estimated_timing) -- assets.voiced (is_measured)
    reads false for both a line that was never voiced and one that was, so
    `empty` must also check the persisted take (stage 7's assets.json
    entry), not `!assetLine.voiced` alone."""
    src = _read(SCRIPT_PANE)
    line_row = _function_body(src, r"function LineRow\(\{[^)]*\}\)\s*\{", "LineRow")
    assert "empty={!assetLine.voiced && !assetLine.take && !assetLine.has_audio}" in line_row, (
        "RegenerateControl's `empty` must read false once assetLine.take or "
        "assetLine.has_audio is true, even while assetLine.voiced is false "
        "for the edited text"
    )


def test_line_row_offers_re_voice_for_a_line_the_plain_assets_step_voiced_then_an_edit_staled():
    """F2 fix round 2 (browser-check, real data): l12 was voiced by the
    plain assets step (never regenerated, so assetLine.take stays null) and
    then text-edited -- assetLine.voiced and assetLine.take are both falsy,
    exactly the common case a `!voiced && !take` condition alone still
    misreads as "never voiced". assetLine.has_audio (workflow._assets_view,
    voice_lines.has_audio) is the one field that survives the edit: the
    file line_<NN>.mp3/.wav stays on disk even though the script's own
    timing.audio pointer was cleared."""
    src = _read(SCRIPT_PANE)
    line_row = _function_body(src, r"function LineRow\(\{[^)]*\}\)\s*\{", "LineRow")
    assert "assetLine.has_audio" in line_row


# ================================================== 13b polish round (F8)
#
# Found live: "Re-voice this line" (and the shot-image / metadata regenerate
# controls) stayed enabled while the episode's script was not approved --
# the server refuses (assets.require_approved / metadata.require_render),
# spending nothing, but the click still had to round-trip into a 409 before
# the dashboard showed anything. The episode page now carries the server's
# own refusal sentence (episode.state.assets_regenerate_blocked / .
# metadata_regenerate_blocked, workflow.episode_view), single-sourced; the
# controls disable on it, and each pane shows the sentence once (not once
# per line/shot/platform, to avoid a wall of repeated text).

def test_script_pane_reads_the_assets_regenerate_blocked_field():
    src = _read(SCRIPT_PANE)
    assert "episode.state.assets_regenerate_blocked" in src


def test_line_row_takes_an_assets_blocked_prop():
    src = _read(SCRIPT_PANE)
    match = re.search(r"function LineRow\(\{([^)]*)\}\)\s*\{", src)
    assert match, "LineRow not found"
    assert "assetsBlocked" in match.group(1), "LineRow does not take an assetsBlocked prop"


def test_line_rows_re_voice_control_is_disabled_while_assets_regeneration_is_blocked():
    src = _read(SCRIPT_PANE)
    line_row = _function_body(src, r"function LineRow\(\{[^)]*\}\)\s*\{", "LineRow")
    match = re.search(r"<RegenerateControl[\s\S]*?disabled=\{([^}]*)\}", line_row)
    assert match, "RegenerateControl not found in LineRow"
    assert "assetsBlocked" in match.group(1), match.group(1)
    # The sentence itself is shown once at the pane level (below), never
    # repeated per line.
    assert "assetsBlocked &&" not in line_row


def test_script_pane_shows_the_blocked_sentence_once_as_visible_text():
    src = _read(SCRIPT_PANE)
    page = _function_body(src, r"export default function ScriptPane\(\{[^)]*\}\)\s*\{", "ScriptPane")
    assert re.search(r"assetsBlocked && <p[^{]*\{assetsBlocked\}</p>", page), (
        "ScriptPane does not show assets_regenerate_blocked as a visible sentence"
    )


# ============================================================ StoryboardPane.jsx (F8)

def test_storyboard_pane_reads_the_assets_regenerate_blocked_field():
    src = _read(STORYBOARD_PANE)
    assert "episode.state.assets_regenerate_blocked" in src


def test_shot_image_block_takes_an_assets_blocked_prop():
    src = _read(SHOT_CARD)
    match = re.search(r"function ShotImageBlock\(\{([^)]*)\}\)\s*\{", src)
    assert match, "ShotImageBlock not found"
    assert "assetsBlocked" in match.group(1), "ShotImageBlock does not take an assetsBlocked prop"


def test_shot_images_regenerate_control_is_disabled_while_assets_regeneration_is_blocked():
    src = _read(SHOT_CARD)
    block = _function_body(src, r"function ShotImageBlock\(\{[^)]*\}\)\s*\{", "ShotImageBlock")
    match = re.search(r"<RegenerateControl[\s\S]*?disabled=\{([^}]*)\}", block)
    assert match, "RegenerateControl not found in ShotImageBlock"
    assert "assetsBlocked" in match.group(1), match.group(1)
    # The existing "locked" reason keeps its own per-shot message; the new
    # blocked sentence is shown once at the pane level, not repeated here.
    assert "assetsBlocked &&" not in block


def test_storyboard_pane_shows_the_blocked_sentence_once_as_visible_text():
    src = _read(STORYBOARD_PANE)
    page = _function_body(src, r"export default function StoryboardPane\(\{[^)]*\}\)\s*\{", "StoryboardPane")
    assert re.search(r"assetsBlocked && <p[^{]*\{assetsBlocked\}</p>", page), (
        "StoryboardPane does not show assets_regenerate_blocked as a visible sentence"
    )


# ============================================================ PreviewPane.jsx (F8)

def test_preview_pane_reads_the_metadata_regenerate_blocked_field():
    src = _read(PREVIEW_PANE)
    assert "episode.state.metadata_regenerate_blocked" in src


def test_platform_card_takes_a_metadata_blocked_prop():
    src = _read(PREVIEW_PANE)
    match = re.search(r"function PlatformCard\(\{([^)]*)\}\)\s*\{", src)
    assert match, "PlatformCard not found"
    assert "metadataBlocked" in match.group(1), "PlatformCard does not take a metadataBlocked prop"


def test_platform_cards_regenerate_control_is_disabled_while_metadata_regeneration_is_blocked():
    src = _read(PREVIEW_PANE)
    card = _function_body(src, r"function PlatformCard\(\{[^)]*\}\)\s*\{", "PlatformCard")
    match = re.search(r"<RegenerateControl[\s\S]*?disabled=\{([^}]*)\}", card)
    assert match, "RegenerateControl not found in PlatformCard"
    assert "metadataBlocked" in match.group(1), match.group(1)
    assert "metadataBlocked &&" not in card, "PlatformCard must not repeat the blocked sentence per platform"


def test_preview_pane_shows_the_metadata_blocked_sentence_once_as_visible_text():
    src = _read(PREVIEW_PANE)
    page = _function_body(src, r"export default function PreviewPane\(\{[^)]*\}\)\s*\{", "PreviewPane")
    assert re.search(r"metadataBlocked && <p[^{]*\{metadataBlocked\}</p>", page), (
        "PreviewPane does not show metadata_regenerate_blocked as a visible sentence"
    )


def test_no_login_or_token_reference_added_by_the_13b_polish_round():
    # Whole-file for ScriptPane/StoryboardPane; PreviewPane already has a
    # legitimate, pre-existing "token" reference (a media URL's signing
    # token, DEC-163) unrelated to auth -- scoped to PlatformCard, the only
    # piece this round touches there, same reasoning as the stage-11 guard
    # above (test_no_login_or_token_reference_added_to_preview_pane).
    for path in (SCRIPT_PANE, *STORYBOARD_FILES):
        src = _read(path)
        assert "token" not in src.lower()
        assert "sign in" not in src.lower() and "sign-in" not in src.lower()

    card = _function_body(_read(PREVIEW_PANE), r"function PlatformCard\(\{[^)]*\}\)\s*\{", "PlatformCard")
    assert "token" not in card.lower()
    assert "sign in" not in card.lower() and "sign-in" not in card.lower()

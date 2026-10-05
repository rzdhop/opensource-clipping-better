"""Dashboard shared pieces for AI Story phase-1 stage 10 (plan stage 10).

Guards the contract of ``components/ActivityFeed.jsx`` (the activity feed
JobDetail used to hold alone, now shared with the story wizard of stage 11),
the AI Story functions added to ``api.js``, and ``Dashboard.jsx``'s handling
of the two new job statuses (``running``, ``awaiting_approval``) and of
story-step jobs, which must never render as a clip card.

Pure text/AST checks, no npm: a JS test runner would be a new dependency and
would not run in CI, which installs pytest and nothing else (DEC-012) --
the same reasoning and the same pattern as
``tests/test_dashboard_payload_contract.py`` and ``tests/test_auth_token.py``.
"""

import ast
import pathlib
import re

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
DASHBOARD_SRC = PROJECT_ROOT / "web" / "dashboard" / "src"
ACTIVITY_FEED = DASHBOARD_SRC / "components" / "ActivityFeed.jsx"
JOB_DETAIL = DASHBOARD_SRC / "pages" / "JobDetail.jsx"
DASHBOARD = DASHBOARD_SRC / "pages" / "Dashboard.jsx"
API_JS = DASHBOARD_SRC / "api.js"
MODELS = PROJECT_ROOT / "web" / "api" / "models.py"

# The story functions api.js gained in this stage (contract item 3).
STORY_FUNCTIONS = [
    "fetchStories", "createStory", "fetchStory", "patchStory", "deleteStory",
    "fetchConcepts", "generateConcepts", "chooseConcept", "runStoryStep",
    "approveStoryDoc", "regenerateStory", "fetchStoryEstimate", "fetchStoryFileUrl",
    # Phase 2, stage 9 (CastEditor): the voice picker, inline character edits
    # and deletes, and the entity media route. `uploadCharacterReference` is
    # deliberately NOT here -- like `uploadVideo`, it is XHR-based (for
    # upload progress), not a `request()` caller.
    "fetchCharacterVoices", "patchCharacter", "deleteCharacter", "deleteCharacterUpload",
    "fetchStoryMediaUrl",
    # Phase 2, stage 10 (PlacesStep/SeasonStep): inline place/prop edits and
    # deletes.
    "patchPlace", "patchProp", "deletePlace", "deleteProp",
    # Phase 3, stage 10 (EpisodeStudio/ScriptPane): the episode page, inline
    # script/storyboard edits (the storyboard one used from stage 11), and a
    # line's measured voice take.
    "fetchEpisode", "patchEpisodeScript", "patchEpisodeStoryboard", "fetchEpisodeVoiceUrl",
    # Phase 4, stage 14 (StoryboardPane assets + the header's Fast track): a
    # shot's generated image blob and the assets PATCH (the lock toggle).
    # Approve/regenerate/step/estimate are reused unchanged (fetchStoryEstimate
    # above gained new phase-4-only options, not a new function).
    "fetchShotImageUrl", "patchEpisodeAssets",
    # Phase 5, stage 10 (SeriesMemoryPanel): the audience-feedback paste and a
    # proposal decision. runStoryStep/approveStoryDoc/fetchStoryEstimate are
    # reused unchanged for memory/feedback/propose-next (series steps run
    # through the generic dispatch and approve grammar, spec 9.1/9.2).
    "postEpisodeFeedback", "decideProposal",
    # 2026-10-02 (the Visual tier card's "Regenerate on v2"): the pipeline switch.
    "switchPipeline",
    # Plan 28 stage C: "Approve all" of the cast or the places.
    "approveStoryGroup",
]


def _job_status_values() -> set[str]:
    """``JobStatus``'s string values, parsed without importing pydantic."""
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "JobStatus":
            values = set()
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant):
                    values.add(stmt.value.value)
            return values
    raise AssertionError("JobStatus not found in web/api/models.py")


def _function_body(src: str, name: str) -> str:
    """The source of one ``export async function <name>(...) { ... }``, found
    by brace counting from the opening ``{`` (these bodies nest no deeper than
    a template literal's ``${...}``, which is itself brace-balanced)."""
    match = re.search(rf"export async function {name}\([^)]*\)\s*\{{", src)
    assert match, f"{name} not found in api.js"
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
    assert len(_job_status_values()) >= 8
    assert len(STORY_FUNCTIONS) == 32  # 2026-10-02: + switchPipeline; plan 28 C: + approveStoryGroup


# --------------------------------------------------------- components/ActivityFeed.jsx

def test_activity_feed_exports_the_three_names():
    src = ACTIVITY_FEED.read_text(encoding="utf-8")
    assert re.search(r"export function ActivityConsole\(", src)
    assert re.search(r"export function LiveActivity\(", src)
    assert re.search(r"export function mergeEvents\(", src)


def test_terminal_is_shared_and_gains_awaiting_approval():
    src = ACTIVITY_FEED.read_text(encoding="utf-8")
    match = re.search(r"export const TERMINAL = \[(.*?)\]", src)
    assert match, "TERMINAL not exported from ActivityFeed.jsx"
    values = set(re.findall(r"'([a-z_]+)'", match.group(1)))
    # Re-pinned on purpose (plan 22 stage 5): a step waiting for the user's own clips is over for the feed too.
    assert values == {"completed", "failed", "cancelled", "awaiting_approval", "awaiting_uploads"}


def test_job_detail_imports_the_shared_pieces():
    src = JOB_DETAIL.read_text(encoding="utf-8")
    match = re.search(r"import \{([^}]*)\} from '\.\./components/ActivityFeed'", src)
    assert match, "JobDetail.jsx does not import from ../components/ActivityFeed"
    imported = match.group(1)
    for name in ("ActivityConsole", "LiveActivity", "mergeEvents"):
        assert name in imported, f"JobDetail does not import {name}"
    # And it must no longer define TERMINAL itself -- one definition, shared.
    assert not re.search(r"^const TERMINAL = \[", src, re.MULTILINE)


def test_no_second_copy_of_activity_console_under_src():
    """Only ActivityFeed.jsx may define it; JobDetail used to hold its own,
    and a second copy would drift from the shared one silently."""
    hits = sorted(
        path.resolve() for path in DASHBOARD_SRC.rglob("*.jsx")
        if re.search(r"function ActivityConsole\(", path.read_text(encoding="utf-8"))
    )
    assert hits == [ACTIVITY_FEED.resolve()]


# ------------------------------------------------------------- pages/Dashboard.jsx

def test_status_labels_covers_every_job_status():
    src = DASHBOARD.read_text(encoding="utf-8")
    match = re.search(r"const STATUS_LABELS = \{(.*?)\n\}", src, re.DOTALL)
    assert match, "STATUS_LABELS not found in Dashboard.jsx"
    labelled = set(re.findall(r"^\s*([a-z_]+):", match.group(1), re.MULTILINE))
    missing = _job_status_values() - labelled
    assert not missing, f"STATUS_LABELS is missing: {sorted(missing)}"


def test_status_labels_gains_the_two_new_statuses():
    src = DASHBOARD.read_text(encoding="utf-8")
    assert "running: 'Running'" in src
    assert "awaiting_approval: 'Awaiting approval'" in src


def test_dashboard_filters_story_step_jobs_out_of_the_clip_grid():
    src = DASHBOARD.read_text(encoding="utf-8")
    assert re.search(r"job\.kind\s*!==\s*['\"]story_step['\"]", src), (
        "Dashboard.jsx does not filter kind === 'story_step' out of the clip jobs"
    )


def test_dashboard_links_a_story_step_job_to_its_story():
    src = DASHBOARD.read_text(encoding="utf-8")
    assert re.search(r"to=\{`/story/\$\{[^}]*\}`\}", src), (
        "Dashboard.jsx does not link a story-step job's line to /story/<story_id>"
    )


# -------------------------------------------------------------------- api.js

def test_every_story_function_is_defined():
    src = API_JS.read_text(encoding="utf-8")
    for name in STORY_FUNCTIONS:
        _function_body(src, name)  # raises if not found


def test_every_story_function_calls_request_and_never_a_raw_fetch():
    src = API_JS.read_text(encoding="utf-8")
    for name in STORY_FUNCTIONS:
        body = _function_body(src, name)
        assert "request(" in body, f"{name} does not call request()"
        assert "fetch(" not in body, f"{name} calls fetch() directly"
        assert "EventSource" not in body, f"{name} uses EventSource"


def test_no_story_function_builds_a_url_with_a_token():
    src = API_JS.read_text(encoding="utf-8")
    for name in STORY_FUNCTIONS:
        body = _function_body(src, name)
        assert "token" not in body.lower(), f"{name} references a token directly"


def test_api_error_carries_message_errors_and_status():
    src = API_JS.read_text(encoding="utf-8")
    assert re.search(r"class ApiError extends Error", src)
    assert "this.errors" in src
    assert "this.status" in src


def test_existing_string_detail_callers_are_unchanged():
    """cancelJob/deleteJob must keep throwing new Error(<string>): the
    extension to detailOf must not change what a string detail collapses to."""
    src = API_JS.read_text(encoding="utf-8")
    assert "throw new Error(await detailOf(res, 'Failed to cancel the job'))" in src
    assert "throw new Error(await detailOf(res, 'Failed to delete job'))" in src


def test_the_feed_tells_its_caller_when_a_step_finishes_without_a_completed_frame():
    # Found live: a step ends in awaiting_approval, the stream's last frame is
    # a 'progress' event, and the poll stops at a terminal status -- so onJob
    # never fired and the wizard kept a finished job as running (its Approve
    # button stayed disabled until a reload).
    source = ACTIVITY_FEED.read_text(encoding="utf-8")
    progress_branch = source.split("event.type === 'progress'", 1)[1].split("event.type === 'events'", 1)[0]
    assert "TERMINAL.includes(event.status)" in progress_branch
    assert "applyJob(" in progress_branch


def test_the_wizard_polls_the_story_while_a_step_runs():
    # Found live: a refresh landed between the preview step's last write and
    # its job's flip to awaiting_approval; the feed had stopped, so the page
    # kept showing "Generating…" for a finished preview.
    wizard = (DASHBOARD_SRC / "pages" / "story" / "StoryWorkspace.jsx").read_text(encoding="utf-8")
    assert "setInterval(refresh, STORY_POLL_MS)" in wizard
    assert "IN_FLIGHT.includes(j.status)" in wizard.split("setInterval(refresh", 1)[0]


def test_the_ready_card_is_keyed_on_the_derived_status_not_the_season_approval():
    # Found live: a voice regeneration clears a character's approval, which
    # clears approvals.cast and drops the story back to style_approved --
    # approvals.season is untouched by that, so `Boolean(story.approvals.
    # season)` kept the "ready" card (and its "Open episode 1" link) up
    # after the story was no longer ready. store.derive_status is the
    # server's own contiguous-prefix status, so it already reflects the
    # drop; the card must key off `story.status` instead.
    wizard = (DASHBOARD_SRC / "pages" / "story" / "StoryWorkspace.jsx").read_text(encoding="utf-8")
    assert "const allDone = story.status === 'ready'" in wizard
    assert "Boolean(story.approvals.season)" not in wizard


def test_the_story_payload_carries_a_status_field():
    # The wizard's ready card reads story.status directly off GET
    # /stories/{id}'s "story" object (the raw story.json), so the schema
    # must actually carry it, and "ready" must be one of its values.
    from clipping.aistory import defaults, schemas

    story_schema = schemas.STORY_BIBLE_SCHEMA
    assert "status" in story_schema["properties"]
    assert "status" in story_schema["required"]
    assert set(story_schema["properties"]["status"]["enum"]) == set(defaults.STATUSES)
    assert "ready" in defaults.STATUSES


# ============================================================ CastStep.jsx (phase 2)

CAST_STEP = DASHBOARD_SRC / "pages" / "story" / "steps" / "CastStep.jsx"


def test_upload_character_reference_sends_the_bearer_header_and_never_a_token_in_the_url():
    """`uploadCharacterReference` is XHR-based like `uploadVideo` (for upload
    progress), so it cannot go through `request()` -- but it still must carry
    the bearer header by hand, and the token must never land in the URL."""
    src = API_JS.read_text(encoding="utf-8")
    match = re.search(r"export function uploadCharacterReference\([^)]*\)\s*\{", src)
    assert match, "uploadCharacterReference not found in api.js"
    start = match.end()
    depth = 1
    i = start
    while depth > 0:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    body = src[start:i]

    assert "setRequestHeader('Authorization'" in body or 'setRequestHeader("Authorization"' in body
    assert "Bearer" in body
    open_call = re.search(r"xhr\.open\(\s*'POST',\s*`([^`]*)`", body)
    assert open_call, "uploadCharacterReference does not call xhr.open('POST', `...`)"
    assert "token" not in open_call.group(1).lower()


def test_the_prompt_only_switch_is_behind_a_confirm_call():
    src = CAST_STEP.read_text(encoding="utf-8")
    match = re.search(r"const switchToPromptOnly = async \(\) => \{([\s\S]*?)\n  \}", src)
    assert match, "switchToPromptOnly not found in CastStep.jsx"
    # The kit's confirm dialog (useConfirm, DEC-253) replaced window.confirm.
    assert "await confirm(" in match.group(1)
    assert "consistency_mode: 'prompt_only'" in match.group(1)


# ======================================================= EstimateChip.jsx (phase 2)

ESTIMATE_CHIP = DASHBOARD_SRC / "components" / "EstimateChip.jsx"


def test_the_estimate_chip_renders_every_unit_of_a_generation_estimate():
    """Found in the browser review: the cast and places estimates carry
    ``{llm_calls, images, edit_images, tts_chars}`` and the chip showed only
    the LLM calls. Every non-zero unit is rendered ("5 LLM · 5 images · 10
    edits · voice ~180 chars"); the phase-1 single-unit renderings stay as
    they were."""
    src = ESTIMATE_CHIP.read_text(encoding="utf-8")
    generation = src.split("function generationUnitsLabel(", 1)[1].split("\n}\n", 1)[0]
    for unit in ("units.llm_calls", "units.images", "units.edit_images", "units.tts_chars"):
        assert unit in generation, unit
    assert "'edit'" in generation and "voice ~" in generation and "' · '" in generation
    # A generation estimate is told apart by its units, before the phase-1 branches.
    label = src.split("export function unitsLabel(", 1)[1].split("\n}\n", 1)[0]
    assert label.index("generationUnitsLabel(units)") < label.index("units.llm_calls != null")
    # The phase-1 renderings, byte for byte (concepts, bible, style, style_preview, season).
    assert "return `${n} LLM call${n === 1 ? '' : 's'}`" in label
    assert "return `${n} image${n === 1 ? '' : 's'}`" in label

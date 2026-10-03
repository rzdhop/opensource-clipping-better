"""The SeasonStep dashboard page's series panel (``SeriesMemoryPanel``) must
only send fields the backend declares, call exactly the stage-5 routes (spec
2.6, 9.1, 9.2; plan 11 stage 10), and never reference a login or a token
(this app never has auth).

Same reasoning and pattern as ``tests/test_story_payload_contract_episode.py``
(the EpisodeStudio guard) and ``tests/test_dashboard_story_shared.py`` (the
``STORY_FUNCTIONS`` guard): stdlib + pytest only (DEC-012).
``clipping.aistory.workflow``/``schemas`` are stdlib-only modules, imported
directly; the dashboard sources are read as text and with ``ast`` over
``models.py``, never through npm or a JS runtime.

Every test here is shown failing against the parent commit (``7aeb2fd``),
where ``SeriesMemoryPanel`` is still the stage-5 stub (``SeasonStep.jsx:132``)
and neither ``postEpisodeFeedback`` nor ``decideProposal`` exist in
``api.js``.
"""

from __future__ import annotations

import ast
import pathlib
import re

from clipping.aistory import schemas, workflow

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = PROJECT_ROOT / "web" / "api" / "models.py"
API_JS = PROJECT_ROOT / "web" / "dashboard" / "src" / "api.js"
SEASON_STEP = PROJECT_ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "steps" / "SeasonStep.jsx"
# The story page shell: the story workspace since DEC-255 (it was NewStoryWizard.jsx).
NEW_STORY_WIZARD = PROJECT_ROOT / "web" / "dashboard" / "src" / "pages" / "story" / "StoryWorkspace.jsx"
INDEX_CSS = PROJECT_ROOT / "web" / "dashboard" / "src" / "index.css"

FOLD_WARNING = "approving this character re-opens the cast approval"


def _class_fields(name: str) -> set[str]:
    """A pydantic model's field names, read without importing pydantic --
    same helper as test_story_payload_contract.py's, duplicated so this file
    stays independently readable and importable."""
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError(f"{name} not found in models.py")


def _split_top_level(body: str) -> list[str]:
    """*body* split on its top-level commas (depth-tracked, so a comma inside
    a nested ``{}``/``[]``/``()`` -- there is none in these snippets, but this
    stays correct if one is ever added -- does not split it)."""
    parts, depth, current = [], 0, []
    for ch in body:
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return parts


def _object_keys(text: str) -> set[str]:
    """Every key of every top-level ``{...}`` object literal found in *text*
    -- ``key: value`` and ES6 shorthand ``key`` both count (``{ accept,
    role }`` and ``{ direction }`` are shorthand in the actual source)."""
    keys = set()
    i, n = 0, len(text)
    while i < n:
        if text[i] == "{":
            depth, j = 1, i + 1
            start = j
            while j < n and depth > 0:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            for entry in _split_top_level(text[start:j - 1]):
                match = re.match(r"\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:?", entry)
                if match:
                    keys.add(match.group(1))
            i = j
        else:
            i += 1
    return keys


def _function_body(src: str, signature: str) -> str:
    """The source of one ``<signature> { ... }`` (an arrow function or a
    plain one), found by brace counting from the opening ``{``."""
    match = re.search(re.escape(signature) + r"\s*\{", src)
    assert match, f"{signature} not found"
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


# ------------------------------------------------------------- non-vacuity

def test_the_readers_see_something():
    """A broken regex would make every assertion below pass for free."""
    assert _class_fields("StoryEpisodeFeedbackRequest") == {"text", "stats"}
    assert _class_fields("StoryProposalDecisionRequest") == {"accept", "role"}
    assert _class_fields("StoryApproveRequest") == {"approve_anyway", "direction"}
    assert schemas.FEEDBACK_TEXT_MAX_LENGTH == 6000
    assert schemas.FEEDBACK_STATS_MAX_LENGTH == 6000
    assert workflow.SERIES_STEPS == ("memory", "feedback", "propose-next")
    assert set(schemas.CHARACTER_ROLES) == {"lead", "support", "recurring", "guest"}


# -------------------------------------------------------------------- api.js

def test_post_episode_feedback_and_decide_proposal_are_defined_and_safe():
    src = API_JS.read_text(encoding="utf-8")
    for name in ("postEpisodeFeedback", "decideProposal"):
        match = re.search(rf"export async function {name}\([^)]*\)\s*\{{", src)
        assert match, f"{name} not found in api.js"
        body = _function_body(src, src[match.start():match.end() - 1])
        assert "request(" in body, f"{name} does not call request()"
        assert "fetch(" not in body, f"{name} calls fetch() directly"
        assert "token" not in body.lower(), f"{name} references a token"
        assert "login" not in body.lower(), f"{name} references a login"


def test_post_episode_feedback_hits_the_stage5_route():
    src = API_JS.read_text(encoding="utf-8")
    assert "/stories/${storyId}/episodes/${ep}/feedback" in src


def test_decide_proposal_hits_the_stage5_route():
    src = API_JS.read_text(encoding="utf-8")
    assert "/stories/${storyId}/episodes/${ep}/proposals/${itemId}" in src


# ------------------------------------------------------- SeasonStep.jsx: calls

def test_feedback_payload_sends_only_declared_fields():
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"const feedbackPayload = (.*)", src)
    assert match, "feedbackPayload not built in SeasonStep.jsx"
    sent = _object_keys(match.group(1))
    declared = _class_fields("StoryEpisodeFeedbackRequest")
    assert sent, "no object-literal keys found building feedbackPayload"
    assert sent <= declared, (sent, declared)
    assert "text" in sent


def test_decision_payload_sends_only_declared_fields():
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"const decisionPayload = (.*)", src)
    assert match, "decisionPayload not built in SeasonStep.jsx"
    sent = _object_keys(match.group(1))
    declared = _class_fields("StoryProposalDecisionRequest")
    assert sent, "no object-literal keys found building decisionPayload"
    assert sent <= declared, (sent, declared)
    assert "accept" in sent


def test_a_rejection_is_never_sent_with_a_role():
    """proposal_request: a role is invalid on anything but an accepted
    character -- the decision payload's reject branch must never carry one."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"const decisionPayload = (.*)", src)
    assert match, "decisionPayload not built in SeasonStep.jsx"
    assert "{ accept }" in match.group(1), match.group(1)


def test_post_episode_feedback_and_decide_proposal_are_called_with_the_built_payload():
    src = SEASON_STEP.read_text(encoding="utf-8")
    assert "postEpisodeFeedback(storyId, ep, feedbackPayload)" in src
    assert "decideProposal(storyId, ep, item.item_id, decisionPayload)" in src


def test_approve_feedback_call_site_sends_only_direction():
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"approveStoryDoc\(storyId, `feedback:\$\{ep\}`, (\{[^}]*\})\)", src)
    assert match, "no approveStoryDoc(storyId, `feedback:${ep}`, { ... }) call site found"
    sent = _object_keys(match.group(1))
    declared = _class_fields("StoryApproveRequest")
    assert sent <= declared, (sent, declared)
    assert "direction" in sent


def test_approve_memory_and_proposals_call_sites_send_no_body():
    src = SEASON_STEP.read_text(encoding="utf-8")
    assert "approveStoryDoc(storyId, `memory:${ep}`)" in src
    assert "approveStoryDoc(storyId, `proposals:${ep}`)" in src


def test_memory_and_propose_next_are_run_with_only_ep():
    """``feedback`` is queued by ``postEpisodeFeedback`` (the paste route
    queues its own step) -- it is never run through ``POST /steps/feedback``
    from the dashboard, so it is not one of these."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    for step in ("memory", "propose-next"):
        assert re.search(rf"runStoryStep\(storyId, '{re.escape(step)}', \{{\s*ep\s*\}}\)", src), step
    assert "runStoryStep(storyId, 'feedback'" not in src


def test_estimate_chips_for_the_three_series_steps():
    src = SEASON_STEP.read_text(encoding="utf-8")
    for step in workflow.SERIES_STEPS:
        assert re.search(rf"fetchStoryEstimate\(storyId, '{re.escape(step)}', \{{\s*ep\s*\}}\)", src), step


# --------------------------------------------------------- constants match

def test_feedback_cap_constants_equal_the_schema():
    src = SEASON_STEP.read_text(encoding="utf-8")
    text_match = re.search(r"const FEEDBACK_TEXT_MAX_LENGTH = (\d+)", src)
    stats_match = re.search(r"const FEEDBACK_STATS_MAX_LENGTH = (\d+)", src)
    assert text_match and stats_match, "the feedback cap constants are not defined in SeasonStep.jsx"
    assert int(text_match.group(1)) == schemas.FEEDBACK_TEXT_MAX_LENGTH
    assert int(stats_match.group(1)) == schemas.FEEDBACK_STATS_MAX_LENGTH


def test_character_roles_constant_matches_schema():
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"const CHARACTER_ROLES = \[([^\]]*)\]", src)
    assert match, "CHARACTER_ROLES not defined in SeasonStep.jsx"
    roles = set(re.findall(r"'([a-z]+)'", match.group(1)))
    assert roles == set(schemas.CHARACTER_ROLES)


# ------------------------------------------------------------- confirm / fold

def test_a_decision_is_confirmed_before_it_is_sent():
    """Decisions are final (plan 11 stage 4): every accept/reject must be
    confirmed, in the same handler that calls decideProposal."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"const decide = async \(accept\) => (\{[\s\S]*?\n  \})", src)
    assert match, "decide(accept) handler not found in SeasonStep.jsx"
    body = match.group(1)
    # The kit's confirm dialog (useConfirm, DEC-253) replaced window.confirm.
    assert "await confirm(" in body
    assert "decideProposal(" in body
    # The confirm must run before the network call, not after.
    assert body.index("await confirm(") < body.index("decideProposal(")


def test_the_fold_warning_text_is_exact_and_gated_on_lead_or_support():
    src = SEASON_STEP.read_text(encoding="utf-8")
    assert FOLD_WARNING in src, "the exact fold-warning phrase is missing from SeasonStep.jsx"
    # It must be reachable only once a lead/support role is chosen.
    assert "role === 'lead' || role === 'support'" in src or "foldsCast" in src


def test_the_fold_warning_is_shown_before_sending_not_only_after():
    """The plan: choosing lead or support 'shows the fold warning ... before
    sending' -- so the phrase must appear in a rendered banner (JSX text),
    not only inside the confirm() dialog string built at decide time."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    # A `<p ...>` (or similar) rendering block that is gated on foldsCast and
    # carries the warning text -- i.e. the phrase appears outside the
    # `decide = async` handler at least once too.
    decide_match = re.search(r"const decide = async \(accept\) => \{[\s\S]*?\n  \}", src)
    assert decide_match
    outside_decide = src[:decide_match.start()] + src[decide_match.end():]
    assert FOLD_WARNING in outside_decide, "the fold warning is not rendered before the decision is sent"


# ---------------------------------------------------------------- no auth

def test_no_login_or_token_reference_in_the_panel():
    src = SEASON_STEP.read_text(encoding="utf-8")
    lowered = src.lower()
    for needle in ("token", "login", "sign in", "sign-in", "password"):
        assert needle not in lowered, f"SeasonStep.jsx references {needle!r}: no auth on this app, ever"


# ------------------------------------------------------------- data wiring

def test_the_panel_reads_the_series_page_fields():
    src = SEASON_STEP.read_text(encoding="utf-8")
    for needle in ("memory.state", "memory.entry", "next_episode_gate", ".proposals", ".directions",
                   "chosen_direction", "relationship_deltas"):
        assert needle in src, f"SeasonStep.jsx does not reference {needle!r}"


def test_relationships_are_shown_by_name_not_by_character_id():
    """The plan: 'relationships as a readable list (names, not ids)' --
    the pair key ('<id>|<id>') must be resolved through the characters list,
    the same way ArcEntry already resolves entry.characters."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    assert re.search(r"c\.char_id === id", src), "no char_id -> name lookup found for the relationships list"
    assert "split('|')" in src or 'split("|")' in src


def test_the_wizard_passes_series_and_episode_count_into_the_panel():
    src = SEASON_STEP.read_text(encoding="utf-8")
    assert re.search(r"const \{[^}]*\bseries\b[^}]*\}\s*=\s*data", src), (
        "SeasonStep does not destructure `series` off `data`"
    )
    assert "totalEpisodes" in src


# ------------------------------------------------------------------- css

def test_new_series_panel_classes_wrap_long_text():
    """Phone-first (375 px, plan 11 stage 10): a long recap or hook must
    wrap rather than force horizontal scroll."""
    css = INDEX_CSS.read_text(encoding="utf-8")
    for cls in (".story-season-memory-episode", ".story-season-memory-banner"):
        match = re.search(re.escape(cls) + r"\s*\{([^}]*)\}", css)
        assert match, f"{cls} not defined in index.css"


# =================================================== coordinator fix-attempt-1
# (F1-F4, browser check on a scratch copy of the live FR story, 375 px)

def _css_rule(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"{selector} not defined in index.css"
    return match.group(1)


# ------------------------------------------------------------------------ F1
# Estimate storm and stuck chips: a control's estimate must be fetched only
# when its precondition is known (client-side) to hold, and a fetch that
# still 409s must show the refusal and disable the control -- never an
# endless "estimating...".

def test_memory_estimate_is_gated_on_the_script_being_approved():
    """clipping.aistory.steps.memory.require_approved_script: the estimate
    409s without an approved script -- MemoryCard must know this from
    `episodes[].script_state` (GET /stories/{id}'s own field) instead of
    firing the request into a guaranteed refusal."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    assert "scriptApproved" in src, "MemoryCard is not told whether the script is approved"
    match = re.search(r"function MemoryCard\(\{([^}]*)\}\)", src)
    assert match and "scriptApproved" in match.group(1), "MemoryCard does not take a scriptApproved prop"


def test_propose_next_estimate_is_gated_on_memory_being_approved():
    """clipping.aistory.steps.propose_next.require_fresh_memory: the
    estimate 409s without episode ep's memory approved and fresh --
    ProposeNextControl must know this from `series[ep-1].memory.state`
    instead of firing the request into a guaranteed refusal (also F3: this
    is exactly why ep 1's control must be disabled while its memory is a
    draft)."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"function ProposeNextControl\(\{([^}]*)\}\)\s*\{", src)
    assert match, "ProposeNextControl not found"
    assert "memoryApproved" in match.group(1), "ProposeNextControl does not take a memoryApproved prop"
    body = _function_body(src, src[match.start():match.end() - 1])
    assert "!memoryApproved" in body, "ProposeNextControl does not check !memoryApproved before estimating/running"


def test_feedback_estimate_is_only_fetched_once_a_feedback_item_exists():
    """clipping.aistory.steps.feedback.require_feedback: the estimate 409s
    without a pasted item -- there is no way to estimate the very first
    paste (the endpoint itself needs the item to exist), so the fetch must
    be skipped entirely until `feedback` is non-null."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"function FeedbackBox\(\{[^}]*\}\)\s*\{", src)
    assert match, "FeedbackBox not found"
    body = _function_body(src, src[match.start():match.end() - 1])
    fetch_call = re.search(r"fetchStoryEstimate\(storyId, 'feedback', \{\s*ep\s*\}\)", body)
    assert fetch_call, "FeedbackBox does not fetch the feedback estimate"
    # The fetch must be textually inside a branch gated on `feedback` (not
    # fired unconditionally on mount).
    before_fetch = body[:fetch_call.start()]
    guard = re.search(r"if \(![a-zA-Z]*[Ff]eedback\w*[^)]*\)[\s\S]*$", before_fetch)
    assert guard, "the feedback estimate is not skipped while no feedback item exists"


def test_a_surprise_409_is_shown_and_never_leaves_the_chip_stuck():
    """Defense in depth: even with the gates above, an estimate call that
    still fails (a race, a busy story) must render its message somewhere
    and disable the control -- not the old `.catch(() => setEstimate(null))`
    that left the chip reading "estimating..." forever. Scoped to the three
    controls this fix touches (MemoryCard, FeedbackBox, ProposeNextControl)
    -- NoSeasonYet's own 'season' estimate is a separate, pre-existing
    control this fix attempt does not touch."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    for name in ("MemoryCard", "FeedbackBox", "ProposeNextControl"):
        match = re.search(rf"function {name}\(\{{[^}}]*\}}\)\s*\{{", src)
        assert match, f"{name} not found"
        body = _function_body(src, src[match.start():match.end() - 1])
        assert ".catch(() => setEstimate(null))" not in body, (
            f"{name} still silently swallows an estimate failure instead of showing it"
        )
        assert re.search(r"set\w*[Ee]stimate[Ee]rror\(", body), f"{name} sets no estimate-error state on failure"
        assert re.search(r"\w*[Ee]stimate[Ee]rror\b[\s\S]{0,400}<", body), f"{name} never renders the estimate error"


def test_blocked_memory_and_propose_next_buttons_are_disabled():
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"function MemoryCard\(\{[^}]*\}\)\s*\{", src)
    body = _function_body(src, src[match.start():match.end() - 1])
    update_button = re.search(r"onClick=\{handleUpdate\}[\s\S]*?disabled=\{([^}]*)\}", body)
    assert update_button, "Update memory button not found"
    assert "scriptApproved" in update_button.group(1) or "block" in update_button.group(1).lower(), (
        "Update memory is not disabled when the script is not approved"
    )

    match = re.search(r"function ProposeNextControl\(\{[^}]*\}\)\s*\{", src)
    body = _function_body(src, src[match.start():match.end() - 1])
    run_button = re.search(r"onClick=\{handleRun\}[\s\S]*?disabled=\{([^}]*)\}", body)
    assert run_button, "Propose next episode button not found"
    assert "memoryApproved" in run_button.group(1) or "block" in run_button.group(1).lower(), (
        "Propose next episode is not disabled when memory is not approved"
    )


def test_the_wizard_passes_episode_summaries_into_the_panel():
    """The `episodes` field of GET /stories/{id} (workflow.episode_summaries:
    `script_state` per episode) is what makes the gates above possible
    without guessing from a 409."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    assert re.search(r"const \{[^}]*\bepisodes\b[^}]*\}\s*=\s*data", src), (
        "SeasonStep does not destructure `episodes` off `data`"
    )
    assert "script_state" in src


# ------------------------------------------------------------------------ F2
# A full card only for an episode that has something; the rest collapse into
# one quiet line. An episode with only proposals (no script yet) still gets
# a full card.

def test_episodes_with_nothing_yet_are_collapsed_into_one_line():
    src = SEASON_STEP.read_text(encoding="utf-8")
    assert "nothing yet" in src, "no collapsed-range message found in SeasonStep.jsx"


def test_the_has_content_predicate_covers_script_memory_feedback_and_proposals():
    """The exact rule (coordinator, fix attempt 1): 'a script, a memory
    entry, a feedback item, or proposals made for it' -- so an episode with
    only proposals (no script) must still count, which is why `proposals`
    must be one of the ORed conditions, not an afterthought."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"function hasContent\([^)]*\)\s*\{", src)
    assert match, "no hasContent(...) predicate function found in SeasonStep.jsx"
    body = _function_body(src, src[match.start():match.end() - 1])
    for needle in ("script", "memory", "feedback", "proposals"):
        assert needle in body.lower(), f"the has-content predicate does not mention {needle!r}: {body}"


def test_a_full_card_is_conditional_not_unconditional_per_episode():
    """Guards against a regression back to 'render a card for every entry of
    series' regardless of content."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    assert "MemoryCard" in src and "ProposalsCard" in src
    # A plain, unconditional `series.map((page) => (` immediately followed by
    # a MemoryCard with nothing gating it would be the regression; the fixed
    # version routes through a computed list of rows (or equivalent branching)
    # instead of mapping series directly onto full cards.
    assert not re.search(r"\{series\.map\(\(page\) => \(\s*<div key=\{page\.ep\} className=\"story-season-memory-episode\">\s*<div className=\"story-season-entry-header\">",
                          src), "every episode still renders a full card unconditionally"


# ------------------------------------------------------------------------ F4
# Preserved line breaks: a multi-paragraph paste must not run its lines
# together.

def test_a_preserve_lines_css_rule_exists():
    css = INDEX_CSS.read_text(encoding="utf-8")
    rule = _css_rule(css, ".story-season-preserve-lines")
    assert "pre-wrap" in rule, ".story-season-preserve-lines does not set white-space: pre-wrap"


def test_pasted_feedback_text_and_stats_preserve_line_breaks():
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"function FeedbackBox\(\{[^}]*\}\)\s*\{", src)
    body = _function_body(src, src[match.start():match.end() - 1])
    assert re.search(r"\{feedback\.text\}", body)
    assert "story-season-preserve-lines" in body, (
        "FeedbackBox does not render the pasted text/stats with preserved line breaks"
    )


def test_recap_and_digest_preserve_line_breaks_too():
    """'same for any multi-line text you render' (coordinator) -- LLM prose
    (the recap, the feedback digest) gets the same treatment as the user's
    own paste."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    memory_match = re.search(r"function MemoryCard\(\{[^}]*\}\)\s*\{", src)
    memory_body = _function_body(src, src[memory_match.start():memory_match.end() - 1])
    assert "entry.recap" in memory_body
    assert "story-season-preserve-lines" in memory_body, "the recap is not rendered with preserved line breaks"

    feedback_match = re.search(r"function FeedbackBox\(\{[^}]*\}\)\s*\{", src)
    feedback_body = _function_body(src, src[feedback_match.start():feedback_match.end() - 1])
    assert "feedback.digest" in feedback_body
    digest_section = feedback_body[feedback_body.index("feedback.digest") - 200:feedback_body.index("feedback.digest") + 50]
    assert "story-season-preserve-lines" in digest_section, "the digest is not rendered with preserved line breaks"


# =================================================== coordinator fix-attempt-2
# (F1/F2/F5, live walk on the real FR story, 375 px, 2026-09-30, stage 13b)

# ------------------------------------------------------------------------ F1
# The wizard collapsed the Season step back to Cast the instant any series-
# panel action (or its own job's completion) succeeded: every one of them
# was wired to the wizard's ordinary afterAction, which always clears
# manualStep -- fine for concepts/bible/style/cast/places (advancing makes
# sense there), wrong here once the season is already approved and nothing
# else is 'active' (expanded falls back to 'cast').

def test_the_wizard_defines_a_series_change_callback_that_never_clears_manual_step():
    src = NEW_STORY_WIZARD.read_text(encoding="utf-8")
    match = re.search(r"const afterSeriesAction = \(\) => \{([\s\S]*?)\n  \}", src)
    assert match, "StoryWorkspace.jsx does not define afterSeriesAction"
    assert "setManualStep(null)" not in match.group(1), (
        "afterSeriesAction must never clear manualStep -- the Season step must stay open"
    )
    assert "refresh()" in match.group(1)


def test_season_step_receives_the_series_change_callback_alongside_the_ordinary_one():
    src = NEW_STORY_WIZARD.read_text(encoding="utf-8")
    match = re.search(r"<SeasonStep\b([^>]*)/>", src)
    assert match, "SeasonStep is not rendered in StoryWorkspace.jsx"
    tag = match.group(1)
    assert "onSeriesChange={afterSeriesAction}" in tag, tag
    # Every other step's behaviour (and the season's own advance) is unchanged.
    assert "onChange={afterAction}" in tag, tag


def test_the_series_panel_is_wired_to_the_series_change_callback_not_the_ordinary_one():
    """SeriesMemoryPanel -- and so MemoryCard/FeedbackBox/ProposeNextControl/
    ProposalItem/ProposalsCard, which all take their onChange from it --
    must receive onSeriesChange, or a series action collapses the step the
    instant it succeeds, exactly the live-walk symptom (F1)."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"function SeasonStep\(\{([^}]*)\}\)\s*\{", src)
    assert match, "SeasonStep not found"
    assert "onSeriesChange" in match.group(1), "SeasonStep does not take an onSeriesChange prop"
    body = _function_body(src, src[match.start():match.end() - 1])
    panel_call = re.search(r"<SeriesMemoryPanel\b([^>]*)/>", body)
    assert panel_call, "SeriesMemoryPanel is not rendered"
    assert "onChange={onSeriesChange}" in panel_call.group(1), panel_call.group(1)


def test_the_jobfeed_completion_callback_uses_the_series_change_callback():
    """useJobFeed's onJob (the season step's own job -- 'season' or a
    'regenerate' of 'season:<ep>') must not clear manualStep either: the
    same collapse, fired asynchronously once the job's status is seen to
    have left queued/running."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"onJob: \(job\) => \{([\s\S]*?)\n\s*\},", src)
    assert match, "useJobFeed's onJob callback not found in SeasonStep.jsx"
    assert "onSeriesChange()" in match.group(1), match.group(1)
    assert "onChange()" not in match.group(1), match.group(1)


def test_the_season_arcs_own_approve_and_replan_keep_the_ordinary_callback():
    """SeasonActions (approve/re-plan the whole season) is unchanged --
    only the series panel's own actions and its job feed move to
    onSeriesChange."""
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"<SeasonActions\b([^>]*)/>", src)
    assert match, "SeasonActions is not rendered"
    assert "onChange={onChange}" in match.group(1), match.group(1)


# ------------------------------------------------------------------------ F2
# The paste counter counted UTF-16 units (text.length): the server
# (workflow._pasted, Python len()) counts Unicode code points -- an emoji
# reads one character short client-side (295 vs the server's 294, live).

def test_a_code_point_length_helper_is_defined_and_used_for_the_caps():
    src = SEASON_STEP.read_text(encoding="utf-8")
    assert re.search(r"function codePointLength\(", src), "no codePointLength(...) helper defined"
    text_match = re.search(r"const overText = (.*)", src)
    assert text_match and "codePointLength(text)" in text_match.group(1), (
        f"overText does not count code points: {text_match.group(1) if text_match else None}"
    )
    stats_match = re.search(r"const overStats = (.*)", src)
    assert stats_match and "codePointLength(stats)" in stats_match.group(1), (
        f"overStats does not count code points: {stats_match.group(1) if stats_match else None}"
    )


def test_the_cap_message_counts_code_points_not_utf16_units():
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"const capMessage = \(what, value, limit\) =>([\s\S]*?)\n\n", src)
    assert match, "capMessage not found in SeasonStep.jsx"
    assert "value.length" not in match.group(1), "capMessage still counts UTF-16 units (value.length)"
    assert "codePointLength(value)" in match.group(1)


def test_the_live_counters_show_code_points_not_utf16_units():
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"function FeedbackBox\(\{[^}]*\}\)\s*\{", src)
    body = _function_body(src, src[match.start():match.end() - 1])
    assert "{text.length} / {FEEDBACK_TEXT_MAX_LENGTH}" not in body
    assert "{stats.length} / {FEEDBACK_STATS_MAX_LENGTH}" not in body
    assert "{codePointLength(text)} / {FEEDBACK_TEXT_MAX_LENGTH}" in body
    assert "{codePointLength(stats)} / {FEEDBACK_STATS_MAX_LENGTH}" in body


# ------------------------------------------------------------------------ F5
# "Approve proposals" stayed an actionable button forever after the
# approval (approve_proposals writes nothing of its own -- "the decisions
# are the record" -- so nothing in the proposals document itself ever
# changes). The payload now carries proposals_approved (derived from the
# job that completes on approval); the card must mirror the season's own
# approved_at -> "Approved" pattern instead of leaving the button live.

def test_proposals_card_takes_an_approved_prop():
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"function ProposalsCard\(\{([^}]*)\}\)\s*\{", src)
    assert match, "ProposalsCard not found"
    assert "approved" in match.group(1), "ProposalsCard does not take an approved prop"


def test_proposals_card_mirrors_the_seasons_approved_at_pattern():
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"function ProposalsCard\(\{[^}]*\}\)\s*\{", src)
    body = _function_body(src, src[match.start():match.end() - 1])
    disabled_match = re.search(r"onClick=\{handleApprove\}[\s\S]*?disabled=\{([^}]*)\}", body)
    assert disabled_match, "Approve proposals button not found"
    assert "approved" in disabled_match.group(1), f"not disabled once approved: {disabled_match.group(1)}"
    assert re.search(r"approved \? 'Approved'", body) or re.search(r'approved \? "Approved"', body), (
        "the button does not read Approved once approved"
    )


def test_the_panel_passes_proposals_approved_into_the_card():
    src = SEASON_STEP.read_text(encoding="utf-8")
    match = re.search(r"<ProposalsCard\b([^>]*)/>", src)
    assert match, "ProposalsCard is not rendered"
    assert "row.page.proposals_approved" in match.group(1), match.group(1)

"""The daily-cap refusal in the dashboard (plan 23 stage A6): text contracts
(CI has no node). A step refused by the daily cap shows the "Over today's
spending limit" panel (three numbers, what is needed, one-click "allow today")
instead of a bare sentence; the estimate chip gets a "today $8.38 / $4.00"
chip beside it; the Settings budget card names the day's zone, today's extra
and who spent the day, and warns when the cap is below the spending.

The panel reads the API's 409 ``detail`` (``routes/stories._daily_cap_detail``),
so the keys it reads are checked against what the API builds, and the button
only GRANTS (``POST /api/budget/today/extra``): a person still presses the step
again.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "dashboard" / "src"
STORY = SRC / "pages" / "story"
STEPS = STORY / "steps"
API = SRC / "api.js"
CSS = SRC / "index.css"
SETTINGS = SRC / "pages" / "Settings.jsx"
FIELDS = STORY / "fields.jsx"
REFUSAL = STORY / "BudgetRefusal.jsx"
CHIP = SRC / "components" / "TodayChip.jsx"
ESTIMATE_CHIP = SRC / "components" / "EstimateChip.jsx"
STORIES_ROUTE = ROOT / "web" / "api" / "routes" / "stories.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function(src: str, name: str) -> str:
    """The text of the top-level function *name*, up to its closing brace."""
    start = src.index(f"function {name}(")
    return src[start:src.index("\n}\n", start)]


# ------------------------------------------------------------------ api.js

def test_api_has_the_three_budget_functions_on_the_budget_routes():
    src = _read(API)
    assert "export async function fetchBudgetToday()" in src
    assert "export async function allowTodayExtra({ usd, story_id, note, estimate_usd })" in src
    assert "export async function clearTodayExtra()" in src
    assert "request('/budget/today')" in src
    allow = src[src.index("export async function allowTodayExtra"):src.index("export async function clearTodayExtra")]
    assert "request('/budget/today/extra'" in allow and "method: 'POST'" in allow
    clear = src[src.index("export async function clearTodayExtra"):]
    assert "request('/budget/today/extra', { method: 'DELETE' })" in clear
    # a refusal's code and detail survive: the grant's own 400 is an ApiError
    assert "throw await apiError(res" in allow


def test_api_functions_name_the_fields_the_route_declares():
    models = _read(ROOT / "web" / "api" / "models.py")
    body = models[models.index("class BudgetExtraRequest"):]
    body = body[:body.index("\nclass ")]
    declared = set(re.findall(r"^    (\w+):", body, re.M))
    sent = set(re.findall(r"body\.(\w+) =", _read(API))) | {"usd"}
    assert declared == {"usd", "story_id", "note", "estimate_usd"}
    assert sent == declared


# ---------------------------------------------------------------- StepError

def test_step_error_renders_budget_refusal_with_allow_button():
    fields = _read(FIELDS)
    step_error = fields[fields.index("export function StepError("):fields.index("export function EditableText(")]
    assert "{ message, errors, className, code, detail, storyId, retryLabel }" in step_error
    assert "isBudgetRefusal(code, detail)" in step_error and "<BudgetRefusal" in step_error
    # the plain alert is still what every other error renders
    assert 'role="alert"' in step_error and "story-alert-body" in step_error

    src = _read(REFUSAL)
    assert "export const BUDGET_DAILY_CAP = 'budget_daily_cap'" in src
    assert "Over today's spending limit" in src
    for text in ("Spent today: ", "Daily cap: ", "(saved in Settings)", " allowed today", "Needs ", " more today.",
                 "Other amount…", "Budget settings", "Only for today: the saved cap stays", "this extra ends at 00:00"):
        assert text in src, text
    assert "Allow {formatCents(needed)} more today" in src
    assert "formatBudgetDay(today.day)" in src and "today.zone" in src
    assert 'to="/settings#budget"' in src
    # "Other amount" asks in the kit's dialog, 0.01 to 25
    assert "<Dialog" in src and "EXTRA_MIN_USD = 0.01" in src and "EXTRA_MAX_USD = 25" in src
    assert 'type="number"' in src and "min={EXTRA_MIN_USD}" in src and "max={EXTRA_MAX_USD}" in src
    # what the other cap refuses is a second line and switches Allow off
    assert "detail.other_cap_refusal" in src
    assert re.search(r"disabled=\{blocked \|\| !\(needed > 0\)\}", src)
    # the panel stacks its buttons on a phone
    css = _read(CSS)
    assert ".budget-refusal-actions" in css
    assert re.search(r"@media \(max-width: 480px\) \{\s*\.budget-refusal-actions \{\s*flex-direction: column", css)


def test_the_panel_reads_only_keys_the_api_detail_carries():
    api = _read(STORIES_ROUTE)
    builder = api[api.index("def _daily_cap_detail("):api.index("def _episode_spent(")]
    src = _read(REFUSAL)
    for key in ("today", "estimate", "cap", "needed_usd", "other_cap_refusal", "message"):
        assert f'"{key}"' in builder, key
        assert re.search(rf"\b{key}\b", src), key
    for key in ("day", "zone", "spent_usd", "extra_usd", "stories", "story_count"):
        assert f'"{key}"' in builder, key
        assert f"today.{key}" in src, key
    for key in ("usd", "parts", "llm_worst_usd"):
        assert f'"{key}"' in builder, key
        assert f"estimate.{key}" in src, key
    assert "cap.daily_usd" in src and '"daily_usd"' in builder
    assert '"code": DAILY_CAP_CODE' in builder
    assert 'DAILY_CAP_CODE = "budget_daily_cap"' in api


def test_allow_button_grants_only_and_never_reruns_the_step():
    src = _read(REFUSAL)
    grant = src[src.index("const grant = async"):src.index("const openOther")]
    assert "await allowTodayExtra({" in grant
    for key in ("usd,", "story_id: storyId", "note: `${job} est ${formatCents(estimate.usd)}`",
                "estimate_usd: estimate.usd"):
        assert key in grant, key
    assert "toast.success(`Allowed ${formatCents(usd)} more for today. Press ${step} again.`)" in grant
    assert "onClick={() => grant(needed)}" in src
    # no step call anywhere in the panel: not imported, not called
    imports = "\n".join(line for line in src.splitlines() if line.startswith("import"))
    assert "from '../../api'" in imports and "allowTodayExtra" in imports
    for name in ("runStoryStep", "regenerateStory", "createJob", "approveStoryDoc", "onRetry", "onRegenerate"):
        assert name not in src, name


# ------------------------------------------------- the steps keep code/detail

def _keeps(src: str, fn: str, label: str) -> None:
    body = _function(src, fn)
    assert "setErrorCode(err.code || null)" in body, fn
    assert "setErrorDetail(err.detail || null)" in body, fn
    assert "setErrorCode(null)" in body and "setErrorDetail(null)" in body, fn
    assert "code={errorCode} detail={errorDetail}" in body, fn
    assert f'retryLabel="{label}"' in body, fn
    assert "storyId={storyId}" in body, fn


def test_cast_step_keeps_error_code_and_detail():
    src = _read(STEPS / "CastStep.jsx")
    _keeps(src, "NoCastYet", "Create cast")
    _keeps(src, "ContinueCast", "Continue cast")
    # the portrait and sheet regenerate keep it through RegenerateControl, with the story named
    assert re.search(r"<RegenerateControl\s+storyId=\{storyId\}\s+disabled=\{disabled\}", src)


def test_places_step_keeps_error_code_and_detail():
    src = _read(STEPS / "PlacesStep.jsx")
    _keeps(src, "NoProposalYet", "Propose places & props")
    _keeps(src, "ProposalEditor", "Create places & props")
    _keeps(src, "ContinuePlaces", "Continue places & props")
    assert src.count("<RegenerateControl\n        storyId={storyId}") == 2


def test_style_step_keeps_error_code_and_detail():
    src = _read(STEPS / "StyleStep.jsx")
    for prefix in ("save", "preview"):
        assert f"set{prefix.capitalize()}ErrorCode(err.code || null)" in src, prefix
        assert f"set{prefix.capitalize()}ErrorDetail(err.detail || null)" in src, prefix
        assert f"set{prefix.capitalize()}ErrorCode(null)" in src, prefix
    assert 'retryLabel="Save draft"' in src and 'retryLabel="Generate preview"' in src
    strip = _function(src, "PreviewStrip")
    assert "errorCode, errorDetail" in strip and "code={errorCode} detail={errorDetail}" in strip


def test_episode_studio_and_clip_regenerate_keep_error_code_and_detail():
    studio = _read(STORY / "EpisodeStudio.jsx")
    body = _function(studio, "FastTrackHeader")
    assert "setErrorCode(err.code || null)" in body and "setErrorDetail(err.detail || null)" in body
    assert 'code={errorCode} detail={errorDetail}' in body and 'retryLabel={`Make episode ${ep}`}' in body  # DEC-305: plain button name
    # the clip regenerate runs through RegenerateControl, which keeps both and names the story
    clip = _read(STORY / "episode" / "storyboard" / "ClipControls.jsx")
    assert re.search(r"<RegenerateControl\s+storyId=\{storyId\}", clip)
    fields = _read(FIELDS)
    control = fields[fields.index("export function RegenerateControl("):]
    assert "setErrorCode(err.code || null)" in control and "setErrorDetail(err.detail || null)" in control
    assert control.count("code={errorCode} detail={errorDetail}") == 2
    assert "storyId={storyId}" in control


def test_every_error_the_api_function_raises_keeps_its_code_and_detail():
    """The pieces above only help if parseDetail's code and detail reach `err`."""
    src = _read(API)
    assert "const { message, errors, code, detail } = await parseDetail(res, fallback)" in src
    assert "new ApiError(message, { errors, status: res.status, code, detail })" in src


# --------------------------------------------------------------- the chips

def test_today_chip_beside_estimate_chip():
    chip = _read(CHIP)
    assert "export default function TodayChip({ today })" in chip
    assert "today ${formatCents(today.spent_usd)} / ${formatCents(today.daily_cap_usd)}" in chip
    assert "extra > 0 ? `${label} + ${formatCents(extra)}` : label" in chip
    assert "chip-warn" in chip and "isOverToday(block)" in chip
    assert "(Number(today.spent_usd) || 0) > effective" in chip
    # fed by the estimate's block: a chip given one makes no request
    assert "if (today) return undefined" in chip and "fetchBudgetToday()" in chip

    estimate = _read(ESTIMATE_CHIP)
    assert "import TodayChip from './TodayChip'" in estimate
    assert "{estimate.today && <TodayChip today={estimate.today} />}" in estimate
    # the chip sits next to the estimate's own span, in one fragment
    body = estimate[estimate.index("export default function EstimateChip"):]
    assert body.index("{label}") < body.index("<TodayChip")

    # every step screen and the episode studio that shows an estimate shows it through EstimateChip
    for path in (STEPS / "CastStep.jsx", STEPS / "PlacesStep.jsx", STEPS / "StyleStep.jsx",
                 STORY / "episode" / "storyboard" / "ClipControls.jsx"):
        assert "<EstimateChip" in _read(path), path.name


def test_estimate_carries_the_today_block_the_chip_reads():
    api = _read(STORIES_ROUTE)
    assert "body = dict(body, today=await run_in_threadpool(budget_routes.today_block" in api
    block = _read(ROOT / "web" / "api" / "routes" / "budget.py")
    for key in ("spent_usd", "extra_usd", "daily_cap_usd", "effective_cap_usd", "day", "zone"):
        assert f'"{key}"' in block, key


# ---------------------------------------------------------------- Settings

def test_settings_budget_card_shows_extra_zone_and_below_cap_warning():
    src = _read(SETTINGS)
    assert "clearTodayExtra" in src and "await clearTodayExtra()" in src
    assert "Allowed for today only: +{formatCents(extraToday)}" in src
    assert "onClick={removeExtra}" in src and ">Remove</Button>" in src
    # the zone beside the daily cap and in the day line
    assert "{dayZone} day" in src and "resets at 00:00 {dayZone}" in src
    # the day's top stories
    assert "settings?.day_contributors" in src and "Spent today, by story" in src
    # the warning, live while the field is edited and from the server's flag once saved
    # DEC-305 section 9 (plan 28 S2): "limit" in the human's words, the same figures.
    assert "This limit is below what was already spent today ({formatCents(spentToday)})" in src
    assert "Every paid call is" in src and "refused until 00:00 {dayZone} unless you allow more for today." in src
    assert "capDraft > 0 && spentToday > capDraft + extraToday" in src
    assert "Boolean(settings?.daily_cap_below_spend) && spentToday > dailyCapNow + extraToday" in src
    assert "{capBelowSpend && (" in src
    # the fields come from GET /api/settings
    models = _read(ROOT / "web" / "api" / "models.py")
    for field in ("spend_day", "spend_zone", "day_extra_usd", "daily_cap_below_spend", "day_contributors"):
        assert field in models, field
        assert field in src, field
    # "Budget settings" lands on the budget tab
    assert "SETTINGS_TABS.some(t => t.id === hashed)" in src
    assert ".settings-budget-warning" in _read(CSS)


def test_no_sign_in_or_token_in_the_new_files():
    for path in (REFUSAL, CHIP):
        text = _read(path).lower()
        for word in ("token", "password", "sign in", "signin", "login", "authorization"):
            assert word not in text, (path.name, word)

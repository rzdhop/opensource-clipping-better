"""Plan 28 stage S2/S3 (DEC-305 section 9, the human, 2026-10-05: "the UI became too
complicated, too much term I do not understand"): the screens the human sees by default
use plain words. The step rail, the episode stepper, the Handoff page, the Settings page,
the new-story form and the story header carry none of the internal terms (T1, J2, tier,
route, budget profile, pipeline, v2, native speech, consistency mode, lipsync, STT, LLM,
link ids) in a string or a text node the human reads -- outside a block marked
``/* advanced */ ... /* /advanced */`` (the Advanced fold and the technical cards), which
may still name them.

Text contract over the JSX (DEC-012: stdlib + pytest only, no JS runner). The scan reads the
text nodes and the sentence-like string literals (one with a space, or a capital first
letter); class names, ids, routes, ``data-*`` and import paths are not text the human reads.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src"

# The default views: each is read whole, minus its ``advanced`` blocks.
DEFAULT_VIEWS = [
    "pages/story/StepRail.jsx",
    "pages/story/storySteps.js",
    "pages/story/StoryWorkspace.jsx",
    "pages/story/StoryHeader.jsx",
    "pages/story/StoriesList.jsx",
    "pages/story/NewStoryWizard.jsx",
    "pages/story/HowMadeControls.jsx",
    "pages/story/GenerationProfileCard.jsx",
    "pages/story/EpisodeStudio.jsx",
    "pages/story/episode/EpisodeStepper.jsx",
    "pages/story/episode/EpisodeApproveAll.jsx",
    "pages/story/episode/HandoffPage.jsx",
    "pages/Settings.jsx",
    "components/EstimateChip.jsx",
    "components/RouteChip.jsx",
]

BANNED = [
    r"\bT[0-9]v?[0-9]?\b", r"\bJ[12]\b", r"\bE[1-4]\b", r"\bK1\b", r"\bD2\b",
    r"\b[Tt]iers?\b", r"\b[Rr]outes?\b", r"[Bb]udget profiles?", r"\b[Pp]ipelines?\b", r"\bv2\b",
    r"[Nn]ative[- ]speech", r"[Cc]onsistency mode", r"\b[Ll]ipsync\b", r"\bSTT\b", r"\bLLMs?\b",
    r"\b(?:fal|gemini|gemini-paid|nvidia|openrouter|groq|mistral|anthropic|local)/[a-z0-9]",
    r"\bjob ids?\b", r"\btemplate ids?\b",
]
BANNED_RE = re.compile("|".join(f"(?:{term})" for term in BANNED))

ADVANCED_RE = re.compile(r"\{?/\*\s*advanced\s*\*/\}?.*?\{?/\*\s*/advanced\s*\*/\}?", re.DOTALL)
BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT_RE = re.compile(r"(?m)(?:^|(?<=[\s;,({]))//.*$")
# Attributes whose value is a name, a class, a path or a key, never a sentence.
NON_TEXT_ATTR_RE = re.compile(
    r"""\b(?:className|id|htmlFor|key|to|href|type|role|name|data-[a-z-]+|aria-controls|aria-labelledby|
        aria-haspopup|as|variant|size|tone|icon|iconRight|rel|target|inputMode|autoComplete|placeholder)
        \s*=\s*(?:"[^"]*"|'[^']*'|\{`[^`]*`\}|\{'[^']*'\}|\{"[^"]*"\})""", re.VERBOSE)
IMPORT_RE = re.compile(r"(?m)^\s*(?:import|export)\b[^\n]*\bfrom\b[^\n]*$|^import\s+['\"][^\n]*$")
STRING_RE = re.compile(r"""'((?:[^'\\\n]|\\.)*)'|"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`""")
TEXT_NODE_RE = re.compile(r">([^<>{}\n][^<>{}]*)<|>\s*\n\s*([^<>{}\n][^<>{}]*)\n")


def _visible_text(path):
    """The sentence-like strings and the JSX text of a file, its advanced blocks, comments and
    non-text attributes removed."""
    src = (SRC / path).read_text(encoding="utf-8")
    src = ADVANCED_RE.sub(" ", src)
    src = BLOCK_COMMENT_RE.sub(" ", src)
    src = LINE_COMMENT_RE.sub("", src)
    src = IMPORT_RE.sub("", src)
    src = NON_TEXT_ATTR_RE.sub(" ", src)
    found = []
    for match in TEXT_NODE_RE.finditer(src):
        text = (match.group(1) or match.group(2) or "").strip()
        if text:
            found.append(text)
    for match in STRING_RE.finditer(src):
        text = next(group for group in match.groups() if group is not None)
        text = re.sub(r"\$\{[^}]*\}", " ", text).strip()
        if len(text) > 1 and (" " in text or text[0].isupper()):
            found.append(text)
    return found


def test_the_default_views_use_none_of_the_internal_terms():
    offenders = []
    for path in DEFAULT_VIEWS:
        for text in _visible_text(path):
            hit = BANNED_RE.search(text)
            if hit:
                offenders.append(f"{path}: {hit.group(0)!r} in {text[:90]!r}")
    assert not offenders, "internal terms outside an advanced block:\n" + "\n".join(offenders)


def test_the_scan_sees_what_it_should():
    """The scan itself: a term in a sentence or a text node is found, one in a comment, a class
    name, a route or an advanced block is not (so the test above cannot pass by reading nothing)."""
    sample = (
        "{/* tier in a comment */}\n"
        "<Chip className=\"story-tier\">Visual tier</Chip>\n"
        "<Route path=\"/story/:id\" to={`/story/${id}/route`} />\n"
        "const note = 'The clips go through the pipeline.'\n"
        "{/* advanced */}\n<label>Budget profile</label>\n{/* /advanced */}\n"
    )
    stripped = BLOCK_COMMENT_RE.sub(" ", ADVANCED_RE.sub(" ", sample))
    assert "Budget profile" not in stripped
    hits = [text for text in re.findall(r">([^<>{}]+)<", stripped) if BANNED_RE.search(text)]
    assert hits == ["Visual tier"]
    assert BANNED_RE.search("The clips go through the pipeline.")
    assert BANNED_RE.search("est. $0.02 on fal/flux-schnell")
    assert not BANNED_RE.search("Make episode 1 — the whole thing")
    for path in DEFAULT_VIEWS:
        assert _visible_text(path), f"{path}: the scan found no text at all"


def test_every_advanced_block_is_closed():
    for path in DEFAULT_VIEWS:
        src = (SRC / path).read_text(encoding="utf-8")
        opened = len(re.findall(r"/\*\s*advanced\s*\*/", src))
        closed = len(re.findall(r"/\*\s*/advanced\s*\*/", src))
        assert opened == closed, f"{path}: {opened} advanced blocks opened, {closed} closed"


# ------------------------------------------------------------- S3: one button per step

def _src(path):
    return (SRC / path).read_text(encoding="utf-8")


def test_each_settings_card_opens_with_one_sentence_on_what_it_is_for():
    page = _src("pages/Settings.jsx")
    headers = re.findall(r"<CardHeader\b.*?/>", page, re.DOTALL)
    assert len(headers) >= 10, "the Settings cards were not found"
    missing = [re.sub(r"\s+", " ", header)[:80] for header in headers if "subtitle=" not in header]
    assert not missing, f"cards with no first line: {missing}"


def test_a_step_shows_its_one_primary_action_and_folds_the_rest_under_more():
    """Plan 28 stage S3: Continue (or Generate) first and Approve all under More while something is
    missing; Re-plan, Save draft and the stop options under More; the bible written again under More."""
    more = _src("pages/story/MoreFold.jsx")
    assert "<summary>{label}</summary>" in more and "label = 'More'" in more
    for path in ("steps/CastStep.jsx", "steps/PlacesStep.jsx"):
        step = _src(f"pages/story/{path}")
        assert "import MoreFold from '../MoreFold'" in step
        assert re.search(r"anyMissing\s*\?\s*<MoreFold><ApproveAllGroup", step), path
    for path in ("steps/SeasonStep.jsx", "steps/StyleStep.jsx", "steps/KnowledgeStep.jsx", "steps/BibleStep.jsx",
                 "EpisodeStudio.jsx"):
        assert "<MoreFold" in _src(f"pages/story/{path}"), path
    workspace = _src("pages/story/StoryWorkspace.jsx")
    assert "Continue\n" in workspace and "Continue to ${nextStep.label}" in workspace


def test_the_episode_studio_leads_with_make_episode_and_approve_all_beside_it():
    studio, approve = _src("pages/story/EpisodeStudio.jsx"), _src("pages/story/episode/EpisodeApproveAll.jsx")
    header = studio[studio.index("function FastTrackHeader"):studio.index("export default function EpisodeStudio")]
    assert "`Make episode ${ep}`" in header and "{approveAll}" in header
    assert header.index("`Make episode ${ep}`") < header.index("{approveAll}") < header.index("<MoreFold>")
    assert "approveAll={<EpisodeApproveAll" in studio and " inline />" in studio
    assert "inline = false" in approve and "Approve all" in approve


def test_every_document_keeps_its_regenerate_behind_a_fold_and_a_shot_its_mode():
    fields = _src("pages/story/fields.jsx")
    control = fields[fields.index("export function RegenerateControl("):]
    assert 'className="story-profile story-regenerate-fold"' in control and "<summary>Regenerate</summary>" in control
    card = _src("pages/story/episode/HandoffCard.jsx")
    assert "modeChip" not in card, "a closed shot shows no mode"
    shot = card[card.index("export function ShotHandoffCard"):card.index("export function EntityHandoffCard")]
    assert 'className="story-profile handoff-mode-fold"' in shot
    assert shot.index("<ManualBody") < shot.index("handoff-mode-fold") < shot.index("<ModeControl")
    assert "Copy prompt" in card and "Upload" in card

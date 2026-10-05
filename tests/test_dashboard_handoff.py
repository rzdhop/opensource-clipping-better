"""The Handoff view (plan 25 stage 3, D-3): text contracts over the JSX (CI has
no node) and the payload contract of the fields the page reads.

``/story/:storyId/episodes/:ep/handoff`` renders ``HandoffPage``: it reads the
handoff document (``GET .../episodes/{ep}/handoff``), remembers the platform
(``PATCH .../handoff``), switches a shot's mode (``PATCH .../shots/{sid}/mode``),
copies prompts with a textarea fallback, downloads a shot's references and
jumps to the next missing item. Every field it reads off the document is one
the server writes. The old Shot list pane is retired (DEC-301): the ``shots``
tab and the ``#shots`` deep link lead to the Handoff, as do the stepper's
Keyframes / Clips nodes and the Agent run card.
"""

from __future__ import annotations

import re
from pathlib import Path

import test_story_assets_step as tas
import test_story_native_speech_plan as nsp
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)

SRC = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src"
STORY = SRC / "pages" / "story"
EPISODE = STORY / "episode"
PAGE = EPISODE / "HandoffPage.jsx"
CARD = EPISODE / "HandoffCard.jsx"
APP = SRC / "App.jsx"
API = SRC / "api.js"
STUDIO = STORY / "EpisodeStudio.jsx"
STEPPER = EPISODE / "EpisodeStepper.jsx"
AGENT = STORY / "AgentRunCard.jsx"
HANDOFF_FUNCTIONS = ("fetchHandoff", "patchHandoff", "patchShotMode", "fetchApiObjectUrl", "downloadApiFile")


def _read(path):
    return path.read_text(encoding="utf-8")


def _handoff_sources():
    return _read(PAGE) + "\n" + _read(CARD)


def _function_body(src, name):
    match = re.search(r"export (?:async )?function " + name + r"\(.*?\n\}\n", src, re.DOTALL)
    assert match, name
    return match.group(0)


def test_the_router_opens_the_handoff_under_story():
    """(a) Fail-first: no such route. The page is routed under /story (the
    two-mode route test keeps every page under /clips or /story)."""
    app = _read(APP)
    assert "import HandoffPage from './pages/story/episode/HandoffPage'" in app
    assert '<Route path="/story/:storyId/episodes/:ep/handoff" element={<HandoffPage />} />' in app


def test_the_page_reads_the_document_and_patches_the_platform_and_the_mode():
    """(b) The page calls the three API functions, and each is a ``request()``
    caller on the routes stage 2 serves -- never a bare ``fetch(`` in the page."""
    src = _handoff_sources()
    assert "fetchHandoff(storyId, ep" in src
    assert "patchHandoff(storyId, ep, " in src
    assert "patchShotMode(storyId, ep, shot.shot_id, " in src
    assert not re.search(r"(?<![A-Za-z_.])fetch\(", src), "a bare fetch( outside api.js"
    api = _read(API)
    for name in HANDOFF_FUNCTIONS:
        body = _function_body(api, name)
        assert "request(" in body and "fetch(" not in body and "token" not in body.lower(), name
    assert "/episodes/${ep}/handoff" in _function_body(api, "fetchHandoff")
    assert "method: 'PATCH'" in _function_body(api, "patchHandoff")
    shot_mode = _function_body(api, "patchShotMode")
    assert "/episodes/${ep}/shots/${shotId}/mode" in shot_mode and "method: 'PATCH'" in shot_mode


def test_the_card_copies_downloads_uploads_and_switches_the_mode():
    """(b) The card's anatomy: Copy prompt with the textarea fallback, the
    per-shot references zip, the Mode control (Auto / My own), the upload slot
    on the entry's own ``upload_slot``, Next missing and the Missing-only filter."""
    src = _handoff_sources()
    assert "Copy prompt" in src and "Copy line" in src and "Copy negative" in src
    assert "import { copyText" in src and "revealForManualCopy" in src
    assert "<textarea" in src and "Copy failed here" in src
    assert "Download all for this shot" in src and "zipUrl={block.zip_url}" in src
    assert "downloadApiFile(zipUrl, zipName)" in src
    assert "Next missing" in src and "doc.next_missing" in src and "Missing only" in src
    assert "Mode" in src and ">Auto<" in src and ">My own<" in src and "block.mode_editable" in src
    assert "<ManualUploadSlot" in src and "slot={block.upload_slot}" in src and "slot={entity.upload_slot}" in src
    assert "Generate this shot" in src and "regenerateStory(storyId, { target" in src
    assert "`shot:${ep}:${shot.shot_id}:video`" in src and "`shot:${ep}:${shot.shot_id}`" in src
    assert "isBudgetRefusal(" in src and "<BudgetRefusal" in src
    assert "export.brief_md" in src and "export.brief_zip" in src and "export.image_brief_zip" in src
    # a stock cutaway keeps its own state (plan 23 stage B8)
    assert "stock: { tone: 'info', label: 'Stock footage' }" in src and "block.stock" in src


def test_the_page_reads_only_what_the_handoff_writes(store):
    """(b) Every ``doc.``, ``shot.``, ``block.`` (a shot's clip or image),
    ``entity.``, ``platformInfo.``, ``gate.`` and ``take.`` field the page
    reads is a key of the document the server composes."""
    from clipping.aistory import workflow
    from clipping.aistory.steps import brief as brief_mod

    story_id = nsp.planned_story(store, profile="native_speech_manual")
    store.update(story_id, lambda doc: doc["generation_profile"].update(images="manual"), now=tas.NOW)
    story = workflow.load(store, story_id)
    doc = brief_mod.handoff(store, story, {}, tas._ec(store, story_id), platform="flow")
    src = _handoff_sources()

    def read(name):
        return set(re.findall(r"\b" + name + r"\.([a-z_]+)", src))

    shots = doc["shots"]
    blocks = [shot["clip"] for shot in shots if shot["clip"]] + [shot["image"] for shot in shots]
    assert read("doc") and read("doc") <= set(doc) | {"export"}, read("doc") - set(doc)
    assert read("shot") <= set().union(*map(set, shots)), read("shot") - set().union(*map(set, shots))
    # Plan 26: ``fit`` and ``prompt_warning`` are written on a v2 story only (stage 4 adds them to
    # the image block and the entity rows too): the page reads them when present.
    optional = {"fit", "prompt_warning"}
    block_keys = set().union(*map(set, blocks)) | optional
    assert read("block") and read("block") <= block_keys, read("block") - block_keys
    entity_keys = set().union(*map(set, doc["entities"])) | {"variant_id", "variant_label", "reference"} | optional
    assert read("entity") <= entity_keys, read("entity") - entity_keys
    assert read("platformInfo") <= set(doc["platform"]), read("platformInfo") - set(doc["platform"])
    assert read("gate") <= {"link", "est_usd", "allowed", "reason"}
    assert read("take") <= {"state", "matched", "heard", "start_s", "end_s", "reason"}
    assert read("counts") <= {"clips", "keyframes", "entities"} | {"total", "done", "missing"}


def test_the_shot_list_pane_is_retired():
    """(c) DEC-301: the Handoff replaces the Shot list; nothing imports it."""
    assert not (EPISODE / "ShotListPane.jsx").exists()
    offenders = [str(path) for path in SRC.rglob("*.js*") if "ShotListPane" in _read(path)]
    assert offenders == []
    assert ".shot-list-row" not in _read(SRC / "index.css")


def test_the_studio_the_stepper_and_the_agent_card_lead_to_the_handoff():
    """(d) The ``shots`` tab and ``#shots`` resolve to the Handoff route; the
    stepper's Keyframes and Clips nodes carry a "Handoff" link when anything is
    the human's; the paused Agent run card opens the Handoff."""
    studio = _read(STUDIO)
    assert "'shots'" in studio and "handoffPath(storyId, ep)" in studio
    assert "window.location.hash === '#shots'" in studio and "<Navigate to={handoffPath(storyId, ep)} replace />" in studio
    assert "fetchHandoff(storyId, ep" in studio and "handoffLinks" in studio
    stepper = _read(STEPPER)
    assert "handoffLinks" in stepper and "Handoff →" in stepper and "<Link" in stepper
    agent = _read(AGENT)
    assert "`/story/${storyId}/episodes/${EPISODE}/handoff`" in agent and "Open the Handoff →" in agent
    assert "#shots" not in agent


def test_one_copy_helper_works_over_plain_http():
    """(plan 26 stage 1) Every Copy button goes through ``lib/clipboard.js``: the
    async Clipboard API where it exists (https / localhost), else a user-gesture
    ``document.execCommand('copy')`` on an off-screen textarea (plain http, a phone
    on the tailnet), else a visible selected textarea. No other file touches
    ``navigator.clipboard``."""
    helper = _read(SRC / "lib" / "clipboard.js")
    for needle in (
        "export async function copyText",
        "export function revealForManualCopy",
        "document.execCommand('copy')",
        "setSelectionRange",
        "navigator.clipboard.writeText",
        "isSecureContext",
    ):
        assert needle in helper, needle
    for rel in (
        "pages/story/episode/HandoffCard.jsx",
        "pages/story/steps/PromptDrawer.jsx",
        "pages/story/episode/PreviewPane.jsx",
        "components/ActivityFeed.jsx",
    ):
        assert "import { copyText" in _read(SRC / rel), rel
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.js*")
        if path != SRC / "lib" / "clipboard.js" and "navigator.clipboard" in _read(path)
    ]
    assert offenders == [], offenders


def test_the_handoff_shows_the_master_prompt_word_counts_and_the_fit_notes():
    """(plan 26 stage 5) The page's "Master prompt" card reads the document's
    ``master_prompt`` (``{text, words, sections}``, null on a v1 story: hidden),
    folded by default, with one "Copy master prompt" and a chip per section.
    Every shot card says its prompt's word count next to Copy prompt, notes a
    fit that dropped something ("Fitted to ...") and shows ``prompt_warning`` as
    a warning row. Everything newer than stage 3 is read defensively: a block
    without ``fit`` shows nothing extra."""
    page = _read(PAGE)
    card = _read(CARD)
    helper = _read(SRC / "lib" / "promptFit.js")
    assert "doc.master_prompt" in page and "function MasterPromptCard" in page
    assert "Master prompt" in page and "Copy master prompt" in page
    assert "master_prompt.sections" in page or "master.sections" in page
    assert "Paste it alone in a chat that keeps context (Gemini), not on Flow." in page
    assert "Every shot prompt below already carries this block." in page
    assert "master prompt" in page  # the toast: Copied — master prompt
    assert "<details" in page.split("function MasterPromptCard", 1)[1].split("\nexport default", 1)[0]
    assert "block.prompt_warning" in card and "block.fit" in card
    assert "entity.fit" in card
    assert "chip chip-warn" in card.split("function FitNote", 1)[1].split("\n}\n", 1)[0]
    assert "words" in helper and "Fitted to" in helper and "fit.dropped" in helper
    assert "full_words" in helper and "fit.limit" in helper
    assert "import { copyLabel, fitNote } from '../../../lib/promptFit'" in card
    # The fetch returns the whole document: nothing strips the new fields.
    assert "return res.json()" in _function_body(_read(API), "fetchHandoff")

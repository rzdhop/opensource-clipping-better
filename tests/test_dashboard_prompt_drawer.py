"""Plan 25 stage 4: the Cast, Places and Props tiles show their prompts.

Text contracts over the JSX (CI has no node). The image brief (``GET
.../image-brief``) already carries, per tile image, the prompt, the size, the
references and the upload slot; ``PromptDrawer`` shows them next to the
upload on a manual-images story, and the tiles render as before without a
brief (a failed fetch, a story whose images the app makes).
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src"
STORY = SRC / "pages" / "story"
STEPS = STORY / "steps"
DRAWER = STEPS / "PromptDrawer.jsx"
SLOT = STORY / "ManualUploadSlot.jsx"
CAST = STEPS / "CastStep.jsx"
PLACES = STEPS / "PlacesStep.jsx"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_the_drawer_copies_the_prompt_and_sits_next_to_the_upload():
    """Fail-first: ``PromptDrawer.jsx`` did not exist. The copy has the hidden
    textarea fallback (plain http has no clipboard API), the size line reads
    the entry's ``size`` and ``min_size``, and the upload is the tile's own
    ``ManualUploadSlot`` on the entry's ``upload_slot``."""
    src = _read(DRAWER)
    assert "Copy prompt" in src and "Copy negative" in src and "Copied" in src
    assert "navigator.clipboard" in src and "isSecureContext" in src
    assert "<textarea" in src and "area.select()" in src
    assert "entry.size" in src and "entry.min_size" in src and "at least" in src
    assert "<details" in src and "entry.prompt" in src
    assert "<ManualUploadSlot" in src and "entry.upload_slot" in src
    assert "entry.state" in src
    # A variant sheet is an edit of the base portrait: the reference is named as such.
    assert "entry.reference" in src and "edit this image" in src.lower()
    assert "import './PromptDrawer.css'" in src


def test_the_tiles_fetch_the_image_brief_once_and_pass_the_entries_down():
    """The two steps load the brief only for a manual-images story, through
    ``api.js``'s ``fetchImageBrief``, and hand each entity its entries."""
    for path in (CAST, PLACES):
        src = _read(path)
        assert "useImageBrief(storyId, imagesManual(story))" in src, path.name
        assert "brief={" in src and "<EntityImageSlots" in src, path.name
    drawer = _read(DRAWER)
    assert "fetchImageBrief(storyId)" in drawer
    assert "export function useImageBrief" in drawer
    # The brief is read without an episode: tile images only, no keyframes.
    assert "fetchImageBrief(storyId, " not in drawer
    for kind in ("characters", "places", "props"):
        assert f'kind="{kind}"' in (_read(CAST) + _read(PLACES))
        assert f"entityBrief(briefImages, '{kind}'" in (_read(CAST) + _read(PLACES))


def test_the_slots_render_a_drawer_per_entry_and_the_bare_tile_without_one():
    """``EntityImageSlots`` takes an optional ``brief`` keyed by slot (and
    variant); an entry renders ``PromptDrawer`` in place of the bare button
    and hint, no entry renders exactly the old button."""
    src = _read(SLOT)
    assert "export function EntityImageSlots({ storyId, kind, entity, disabled, onChange, brief })" in src
    assert "<PromptDrawer" in src and "brief[" in src
    assert "Your own images: make each one from the image brief" in src
    assert "<ManualUploadSlot" in src and "label={`Upload ${slot.replace" in src


def test_variant_rows_take_their_entry_by_variant_id():
    """A variant sheet's entry is found by the sheet slot and ``variant_id``
    (the brief's id is ``<eid>:<vid>``); without it the row keeps its plain tile."""
    cast = _read(CAST)
    row = re.search(r"function VariantRow\(\{(.*?)\n\}\n", cast, re.DOTALL).group(1)
    assert "brief" in row.split("\n", 1)[0]
    assert "entryKey(slot, variant.variant_id)" in row
    assert "<PromptDrawer" in row and "<ManualUploadSlot" in row
    drawer = _read(DRAWER)
    assert "export function entryKey(slot, variantId = null)" in drawer
    assert "entry.variant_id" in drawer


def test_the_drawer_adds_no_bare_fetch_and_no_sign_in():
    """Every request goes through ``api.js``; the component asks for no token."""
    for path in (DRAWER, SLOT):
        src = _read(path)
        assert not re.search(r"(?<![A-Za-z_.])fetch\(", src), path.name
        assert "token" not in src.lower() and "sign in" not in src.lower(), path.name

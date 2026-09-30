"""Tests for ``clipping.aistory.render.fonts`` (AI Story phase 4 stage 5;
plan phase 4 stage 5, "Renderer" -> fonts.py; spec 6.4/6.5; DEC-159).

Sections, in order:

1. portability guard (no PIL import at module load, stdlib + clipping.fonts only)
2. custom_fonts/ wins when a file declares the requested family (PIL path,
   monkeypatched declares check -- see module note below)
3. no PIL installed: filename-match fallback
4. neither: the committed Montserrat Black fallback
5. determinism (first match wins, sorted order)
6. stage()

These tests never build a real, parseable font file (constructing a valid
TTF/OTF byte stream is out of scope for a unit test): the PIL-path tests
monkeypatch ``clipping.fonts.font_file_declares`` itself, exactly the way
the task brief calls for, and exercise real bytes only for sha256/copy
behaviour and for the ONE real font this repo ships
(``assets/fonts/Montserrat-Black.ttf``).

Stdlib + pytest only (DEC-012): this file runs in the CI environment.
"""

from __future__ import annotations

import ast
import hashlib
import os
import sys

import pytest

from clipping.aistory.render import fonts as fonts_mod

REPO_ROOT = fonts_mod.REPO_ROOT
FALLBACK_FILE = fonts_mod.FALLBACK_FONT_FILE


def _sha256_of(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        digest.update(handle.read())
    return digest.hexdigest()


# ======================================================== 1. portability guard

def test_no_pil_import_at_module_load():
    """DEC-159: "there is no PIL in the render path" -- fonts.py may use
    PIL only lazily, inside a call (via ``clipping.fonts.font_file_declares``,
    which itself imports PIL inside its own function body). At MODULE
    LOAD time (top-level imports) nothing beyond the standard library and
    ``clipping.fonts`` may appear."""
    import pathlib

    path = pathlib.Path(fonts_mod.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top == "PIL":
                    offenders.append(alias.name)
                elif top not in sys.stdlib_module_names and top != "clipping":
                    offenders.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level >= 1:
                continue
            top = (node.module or "").split(".")[0]
            if top == "PIL":
                offenders.append(node.module)
            elif top not in sys.stdlib_module_names and top != "clipping":
                offenders.append(node.module)
    assert offenders == []


# ============================================ 2. custom_fonts/ wins (PIL path)

def test_custom_fonts_file_wins_when_it_declares_the_family(tmp_path, monkeypatch):
    custom = tmp_path / "custom_fonts"
    custom.mkdir()
    font_file = custom / "Requested-Family.ttf"
    font_file.write_bytes(b"not a real font, just bytes for hashing/copying")

    monkeypatch.setattr(fonts_mod, "_pil_available", lambda: True)
    monkeypatch.setattr(
        fonts_mod._clipping_fonts, "font_file_declares",
        lambda path, family: os.path.basename(path) == "Requested-Family.ttf" and family == "Requested Family",
    )

    record = fonts_mod.resolve_font("Requested Family", custom_fonts_dir=custom)
    assert record["family"] == "Requested Family"
    assert record["file"].endswith("custom_fonts/Requested-Family.ttf")
    assert record["sha256"] == _sha256_of(font_file)
    assert "declares" in record["reason"]
    assert "Requested Family" in record["reason"]


def test_custom_fonts_file_that_does_not_declare_the_family_is_skipped(tmp_path, monkeypatch):
    custom = tmp_path / "custom_fonts"
    custom.mkdir()
    (custom / "Wrong-Family.ttf").write_bytes(b"bytes")

    monkeypatch.setattr(fonts_mod, "_pil_available", lambda: True)
    monkeypatch.setattr(fonts_mod._clipping_fonts, "font_file_declares", lambda path, family: False)

    record = fonts_mod.resolve_font("Requested Family", custom_fonts_dir=custom)
    assert record["family"] == fonts_mod.FALLBACK_FONT_FAMILY
    assert record["file"] == "assets/fonts/Montserrat-Black.ttf"


# ============================================ 3. no PIL: filename fallback

def test_no_pil_accepts_by_filename_match(tmp_path, monkeypatch):
    custom = tmp_path / "custom_fonts"
    custom.mkdir()
    font_file = custom / "Montserrat-ExtraBold.ttf"
    font_file.write_bytes(b"bytes")

    monkeypatch.setattr(fonts_mod, "_pil_available", lambda: False)

    record = fonts_mod.resolve_font("Montserrat ExtraBold", custom_fonts_dir=custom)
    assert record["family"] == "Montserrat ExtraBold"
    assert record["file"].endswith("custom_fonts/Montserrat-ExtraBold.ttf")
    assert record["sha256"] == _sha256_of(font_file)
    assert "PIL is not installed" in record["reason"]


def test_no_pil_and_no_filename_match_falls_back(tmp_path, monkeypatch):
    custom = tmp_path / "custom_fonts"
    custom.mkdir()
    (custom / "Completely-Unrelated.ttf").write_bytes(b"bytes")

    monkeypatch.setattr(fonts_mod, "_pil_available", lambda: False)

    record = fonts_mod.resolve_font("Montserrat ExtraBold", custom_fonts_dir=custom)
    assert record["family"] == fonts_mod.FALLBACK_FONT_FAMILY


# ============================================ 4. neither: fallback

def test_empty_custom_fonts_dir_falls_back(tmp_path):
    custom = tmp_path / "custom_fonts"
    custom.mkdir()
    record = fonts_mod.resolve_font("Anything", custom_fonts_dir=custom)
    assert record == fonts_mod._fallback_record()


def test_missing_custom_fonts_dir_falls_back(tmp_path):
    missing = tmp_path / "does_not_exist"
    record = fonts_mod.resolve_font("Anything", custom_fonts_dir=missing)
    assert record["family"] == fonts_mod.FALLBACK_FONT_FAMILY


def test_fallback_record_matches_the_real_shipped_file():
    record = fonts_mod.resolve_font("Something Nobody Has", custom_fonts_dir="/no/such/dir")
    assert record["family"] == "Montserrat Black"
    assert record["file"] == "assets/fonts/Montserrat-Black.ttf"
    assert record["sha256"] == _sha256_of(FALLBACK_FILE)
    assert (REPO_ROOT / record["file"]) == FALLBACK_FILE


def test_fallback_reason_names_dec_159():
    record = fonts_mod.resolve_font("Something Nobody Has", custom_fonts_dir="/no/such/dir")
    assert "fallback" in record["reason"]


# ============================================ 5. determinism

def test_first_match_wins_in_sorted_filename_order(tmp_path, monkeypatch):
    custom = tmp_path / "custom_fonts"
    custom.mkdir()
    (custom / "Zeta.ttf").write_bytes(b"z")
    (custom / "Alpha.ttf").write_bytes(b"a")

    monkeypatch.setattr(fonts_mod, "_pil_available", lambda: True)
    monkeypatch.setattr(fonts_mod._clipping_fonts, "font_file_declares", lambda path, family: True)

    # EDITED (stage 12): the always-True monkeypatch now also "matches" the
    # first real file in the committed assets/fonts/ (searched before
    # custom_fonts/, DEC-183) -- isolate the real shipped dir out of this
    # custom_fonts/-only test with an empty one.
    record = fonts_mod.resolve_font("Whatever", custom_fonts_dir=custom,
                                    shipped_fonts_dir=tmp_path / "no_shipped_fonts_here")
    assert record["file"].endswith("Alpha.ttf")


def test_resolve_font_is_deterministic_for_the_same_inputs(tmp_path, monkeypatch):
    custom = tmp_path / "custom_fonts"
    custom.mkdir()
    (custom / "Font.ttf").write_bytes(b"bytes")
    monkeypatch.setattr(fonts_mod, "_pil_available", lambda: True)
    monkeypatch.setattr(fonts_mod._clipping_fonts, "font_file_declares", lambda path, family: True)

    # EDITED (stage 12): isolated from the real shipped dir, same reason as
    # test_first_match_wins_in_sorted_filename_order above -- this test's
    # own point is custom_fonts/ determinism, not the (also-deterministic)
    # shipped dir this module now searches first.
    shipped = tmp_path / "no_shipped_fonts_here"
    r1 = fonts_mod.resolve_font("Family", custom_fonts_dir=custom, shipped_fonts_dir=shipped)
    r2 = fonts_mod.resolve_font("Family", custom_fonts_dir=custom, shipped_fonts_dir=shipped)
    assert r1 == r2


# ======================================================================== 6. stage

def test_stage_copies_the_fallback_font_into_the_fonts_dir(tmp_path):
    record = fonts_mod.resolve_font("Anything", custom_fonts_dir=tmp_path / "empty_custom_fonts")
    fonts_dir = tmp_path / "render" / "fonts"
    staged_path = fonts_mod.stage(record, fonts_dir)

    assert os.path.exists(staged_path)
    assert os.path.basename(staged_path) == "Montserrat-Black.ttf"
    with open(staged_path, "rb") as handle:
        staged_bytes = handle.read()
    with open(FALLBACK_FILE, "rb") as handle:
        original_bytes = handle.read()
    assert staged_bytes == original_bytes


def test_stage_copies_a_custom_fonts_file(tmp_path, monkeypatch):
    custom = tmp_path / "custom_fonts"
    custom.mkdir()
    font_file = custom / "Requested-Family.ttf"
    font_file.write_bytes(b"unique custom font bytes")
    monkeypatch.setattr(fonts_mod, "_pil_available", lambda: True)
    monkeypatch.setattr(fonts_mod._clipping_fonts, "font_file_declares", lambda path, family: True)

    # EDITED (stage 12): same isolation as the two tests above.
    record = fonts_mod.resolve_font("Requested Family", custom_fonts_dir=custom,
                                    shipped_fonts_dir=tmp_path / "no_shipped_fonts_here")
    fonts_dir = tmp_path / "render" / "fonts"
    staged_path = fonts_mod.stage(record, fonts_dir)
    assert open(staged_path, "rb").read() == b"unique custom font bytes"


def test_stage_creates_the_fonts_dir_if_missing(tmp_path):
    record = fonts_mod.resolve_font("Anything", custom_fonts_dir=tmp_path / "empty_custom_fonts")
    fonts_dir = tmp_path / "brand_new" / "fonts"
    assert not fonts_dir.exists()
    fonts_mod.stage(record, fonts_dir)
    assert fonts_dir.exists()


def test_stage_overwrites_a_stale_copy(tmp_path):
    fonts_dir = tmp_path / "render" / "fonts"
    fonts_dir.mkdir(parents=True)
    stale_path = fonts_dir / "Montserrat-Black.ttf"
    stale_path.write_bytes(b"stale contents from a previous render")

    record = fonts_mod.resolve_font("Anything", custom_fonts_dir=tmp_path / "empty_custom_fonts")
    fonts_mod.stage(record, fonts_dir)
    assert stale_path.read_bytes() != b"stale contents from a previous render"
    assert stale_path.read_bytes() == FALLBACK_FILE.read_bytes()


# ======================================================== real fonts_index.json cross-check

def test_fallback_family_and_sha_match_fonts_index_json():
    """assets/fonts/fonts_index.json (committed alongside the .ttf, spec:
    "the committed Montserrat Black") is the independent source of truth
    for the fallback file's own sha256 -- this cross-checks
    :func:`resolve_font`'s fallback record against it directly, so a
    silently-changed/corrupted committed font is caught here too."""
    import json

    index_path = REPO_ROOT / "assets" / "fonts" / "fonts_index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    entry = next(e for e in index["fonts"] if e["file"] == "Montserrat-Black.ttf")
    assert entry["sha256"] == _sha256_of(FALLBACK_FILE)
    assert entry["family"] == "Montserrat"
    assert entry["style"] == "Black"


# ============================================ 7. assets/fonts/ (phase 5 stage 12, DEC-183)
#
# Five per-style OFL/Apache TTFs, approved by the human 2026-09-30 ("All 5
# as named"), ship under assets/fonts/ alongside Montserrat. resolve_font
# must resolve a template family from assets/fonts/ BEFORE custom_fonts/
# (then custom_fonts/, then the Montserrat fallback, exactly as before).

SHIPPED_FONTS_DIR = fonts_mod.SHIPPED_FONTS_DIR

# (template font_family, shipped filename) -- the approved download table.
SHIPPED_TEMPLATE_FONTS = [
    ("Bangers", "Bangers-Regular.ttf"),
    ("Luckiest Guy", "LuckiestGuy-Regular.ttf"),
    ("Bebas Neue", "BebasNeue-Regular.ttf"),
    ("Chewy", "Chewy-Regular.ttf"),
    ("Patrick Hand", "PatrickHand-Regular.ttf"),
]


def test_shipped_fonts_dir_constant_is_assets_fonts():
    assert SHIPPED_FONTS_DIR == REPO_ROOT / "assets" / "fonts"


@pytest.mark.parametrize("family,filename", SHIPPED_TEMPLATE_FONTS)
def test_each_template_family_resolves_to_its_own_shipped_file(family, filename):
    """The real, committed file (real PIL name-table read, no monkeypatch):
    every one of the five templates' own ``typography.font_family`` must
    resolve to exactly its own shipped file, never the Montserrat fallback
    and never another shipped file."""
    record = fonts_mod.resolve_font(family)
    assert record["family"] == family
    assert record["file"] == f"assets/fonts/{filename}"
    assert record["sha256"] == _sha256_of(SHIPPED_FONTS_DIR / filename)
    assert "assets/fonts" in record["reason"]
    assert family in record["reason"]


def test_shipped_font_resolution_is_deterministic():
    r1 = fonts_mod.resolve_font("Bangers")
    r2 = fonts_mod.resolve_font("Bangers")
    assert r1 == r2


@pytest.mark.parametrize("style_id,family", [("fruit_drama", "Montserrat ExtraBold"),
                                              ("family_3d", "Fredoka Bold")])
def test_mvp_styles_still_resolve_to_the_montserrat_fallback_unchanged(style_id, family):
    """Acceptance (stage 12): adding the five new shipped fonts must not
    change either MVP style's own resolution -- neither is shipped, so both
    keep landing on the exact same fallback record as before this stage."""
    record = fonts_mod.resolve_font(family)
    assert record == fonts_mod._fallback_record()
    assert record["family"] == "Montserrat Black"
    assert record["file"] == "assets/fonts/Montserrat-Black.ttf"


def test_shipped_fonts_dir_wins_over_a_custom_fonts_match(tmp_path, monkeypatch):
    """Resolution order: assets/fonts/ BEFORE custom_fonts/. A file in
    custom_fonts/ that would otherwise match must never be picked once the
    real shipped file for the same family exists."""
    custom = tmp_path / "custom_fonts"
    custom.mkdir()
    (custom / "Bangers-FromCustom.ttf").write_bytes(b"a custom override, never picked")
    monkeypatch.setattr(fonts_mod, "_pil_available", lambda: False)  # loose filename match on both sides

    record = fonts_mod.resolve_font("Bangers", custom_fonts_dir=custom)
    assert record["file"] == "assets/fonts/Bangers-Regular.ttf"
    assert record["sha256"] == _sha256_of(SHIPPED_FONTS_DIR / "Bangers-Regular.ttf")


def test_shipped_fonts_dir_search_is_overridable_for_tests(tmp_path, monkeypatch):
    """A caller (this test) may point *shipped_fonts_dir* at a tmp directory
    instead of the real committed assets/fonts/ -- the same seam
    *custom_fonts_dir* already offers, needed to test the ordering/matching
    logic hermetically, without parsing a real font file."""
    shipped = tmp_path / "shipped"
    shipped.mkdir()
    font_file = shipped / "Requested-Family.ttf"
    font_file.write_bytes(b"shipped bytes")
    custom = tmp_path / "custom_fonts"
    custom.mkdir()
    (custom / "Requested-Family.ttf").write_bytes(b"custom bytes, never picked")

    monkeypatch.setattr(fonts_mod, "_pil_available", lambda: False)
    record = fonts_mod.resolve_font("Requested Family", custom_fonts_dir=custom, shipped_fonts_dir=shipped)
    assert record["file"].endswith("shipped/Requested-Family.ttf")
    assert record["sha256"] == _sha256_of(font_file)


def test_shipped_fonts_dir_montserrat_fallback_file_is_never_matched_by_the_generic_search(tmp_path, monkeypatch):
    """The committed Montserrat-Black.ttf sits IN assets/fonts/ too, but it
    is the deliberate last-resort fallback (DEC-159), never a generic
    assets/fonts/ match -- requesting "Montserrat" (not "Montserrat
    ExtraBold"/"Montserrat Black") must still land on the ONE, well-known
    fallback record/reason, not a second, differently-worded "shipped dir"
    match for the same file."""
    monkeypatch.setattr(fonts_mod, "_pil_available", lambda: False)
    record = fonts_mod.resolve_font("Montserrat", custom_fonts_dir=tmp_path / "empty_custom_fonts")
    assert record == fonts_mod._fallback_record()


def test_empty_custom_fonts_dir_with_real_shipped_dir_still_resolves_shipped_fonts(tmp_path):
    """No behaviour change to the existing empty-custom-fonts case (section
    4 above) other than the five new families now resolving before the
    fallback ever runs."""
    record = fonts_mod.resolve_font("Bangers", custom_fonts_dir=tmp_path / "empty_custom_fonts")
    assert record["file"] == "assets/fonts/Bangers-Regular.ttf"


# ================================================ 8. fonts_index.json entries

FONTS_INDEX_PATH = REPO_ROOT / "assets" / "fonts" / "fonts_index.json"

# (filename, family, style, licence, licence_file, git_blob_sha1, source_url
# path) -- the approved table, DEC-183. git_blob_sha1 is `git hash-object
# <file>`, independently verified against github.com/google/fonts before
# download; the source_url path is that same approved table's own column.
_GH_BASE = "https://raw.githubusercontent.com/google/fonts/main/"
EXPECTED_FONT_INDEX = [
    ("Bangers-Regular.ttf", "Bangers", "Regular", "OFL-1.1", "Bangers-OFL.txt",
     "9b0f8c1f1b45e7bb4971b7d6a8604f9aacacdd32", _GH_BASE + "ofl/bangers/Bangers-Regular.ttf"),
    ("LuckiestGuy-Regular.ttf", "Luckiest Guy", "Regular", "Apache-2.0", "LuckiestGuy-LICENSE.txt",
     "5ca663c2f05761356ca4427c084e4443a157f834", _GH_BASE + "apache/luckiestguy/LuckiestGuy-Regular.ttf"),
    ("BebasNeue-Regular.ttf", "Bebas Neue", "Regular", "OFL-1.1", "BebasNeue-OFL.txt",
     "c328c6e08b20a20a1de47d823e007ee73812a438", _GH_BASE + "ofl/bebasneue/BebasNeue-Regular.ttf"),
    ("Chewy-Regular.ttf", "Chewy", "Regular", "Apache-2.0", "Chewy-LICENSE.txt",
     "609eeb393c167df58d434d3d007364944a727894", _GH_BASE + "apache/chewy/Chewy-Regular.ttf"),
    ("PatrickHand-Regular.ttf", "Patrick Hand", "Regular", "OFL-1.1", "PatrickHand-OFL.txt",
     "fb45ccdbd344ab7f9f4ae98792521405e48cda3e", _GH_BASE + "ofl/patrickhand/PatrickHand-Regular.ttf"),
]

LICENCE_FILE_BLOBS = {
    "Bangers-OFL.txt": "bf717d4be410cf7616b9bd345b40ab4817a26e6f",
    "LuckiestGuy-LICENSE.txt": "d645695673349e3947e8e5ae42332d0ac3164cd7",
    "BebasNeue-OFL.txt": "da9571488f44176ef90d7f10c0f402c8be74db67",
    "Chewy-LICENSE.txt": "d645695673349e3947e8e5ae42332d0ac3164cd7",
    "PatrickHand-OFL.txt": "4d5f9447f48357b6a4ac294604954b274059d119",
}


def _load_fonts_index() -> dict:
    import json
    return json.loads(FONTS_INDEX_PATH.read_text(encoding="utf-8"))


def test_fonts_index_still_starts_with_montserrat_unchanged():
    """Untouched, named by the brief: Montserrat's own entry stays first
    and exactly as it was (also proven independently, unedited, by
    ``test_fallback_family_and_sha_match_fonts_index_json`` above)."""
    index = _load_fonts_index()
    assert index["fonts"][0]["file"] == "Montserrat-Black.ttf"
    assert index["fonts"][0]["licence"] == "OFL-1.1"


def test_fonts_index_has_exactly_six_entries():
    index = _load_fonts_index()
    assert len(index["fonts"]) == 6


@pytest.mark.parametrize("filename,family,style,licence,licence_file,git_blob_sha1,source_url", EXPECTED_FONT_INDEX)
def test_fonts_index_entry_matches_the_shipped_file_and_licence(filename, family, style, licence, licence_file,
                                                                 git_blob_sha1, source_url):
    index = _load_fonts_index()
    entry = next((e for e in index["fonts"] if e["file"] == filename), None)
    assert entry is not None, f"no fonts_index.json entry for {filename}"
    assert entry["family"] == family
    assert entry["style"] == style
    assert entry["licence"] == licence
    assert entry["licence_file"] == licence_file
    assert entry["sha256"] == _sha256_of(SHIPPED_FONTS_DIR / filename)
    assert entry["git_blob_sha1"] == git_blob_sha1
    assert entry["source_url"] == source_url
    assert (SHIPPED_FONTS_DIR / filename).is_file()


@pytest.mark.parametrize("licence_file,git_blob_sha1", sorted(LICENCE_FILE_BLOBS.items()))
def test_fonts_index_licence_file_exists_and_matches_its_own_git_blob(licence_file, git_blob_sha1):
    path = SHIPPED_FONTS_DIR / licence_file
    assert path.is_file()
    text = path.read_bytes()
    # git blob sha1: "blob <len>\0<content>"
    header = f"blob {len(text)}\0".encode("ascii")
    assert hashlib.sha1(header + text).hexdigest() == git_blob_sha1


def test_apache_licence_files_are_apache_2_0():
    for filename in ("LuckiestGuy-LICENSE.txt", "Chewy-LICENSE.txt"):
        text = (SHIPPED_FONTS_DIR / filename).read_text(encoding="utf-8")
        assert "Apache License" in text and "Version 2.0" in text


def test_ofl_licence_files_are_sil_open_font_license_1_1():
    for filename in ("Bangers-OFL.txt", "BebasNeue-OFL.txt", "PatrickHand-OFL.txt"):
        text = (SHIPPED_FONTS_DIR / filename).read_text(encoding="utf-8")
        assert "SIL Open Font License, Version 1.1" in text


def test_fonts_index_schema_is_still_fonts_index_v1():
    assert _load_fonts_index()["$schema"] == "fonts_index_v1"

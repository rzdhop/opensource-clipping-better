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

    record = fonts_mod.resolve_font("Whatever", custom_fonts_dir=custom)
    assert record["file"].endswith("Alpha.ttf")


def test_resolve_font_is_deterministic_for_the_same_inputs(tmp_path, monkeypatch):
    custom = tmp_path / "custom_fonts"
    custom.mkdir()
    (custom / "Font.ttf").write_bytes(b"bytes")
    monkeypatch.setattr(fonts_mod, "_pil_available", lambda: True)
    monkeypatch.setattr(fonts_mod._clipping_fonts, "font_file_declares", lambda path, family: True)

    r1 = fonts_mod.resolve_font("Family", custom_fonts_dir=custom)
    r2 = fonts_mod.resolve_font("Family", custom_fonts_dir=custom)
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

    record = fonts_mod.resolve_font("Requested Family", custom_fonts_dir=custom)
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

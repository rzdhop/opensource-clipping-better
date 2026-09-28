"""Font resolution and staging for the AI-Story renderer (spec 6.4/6.5;
plan phase 4 stage 5, "Renderer" -> fonts.py; DEC-159).

Resolution order (:func:`resolve_font`), spec: "the template family's file
in custom_fonts/ ... else the committed Montserrat Black":

1. the first file (sorted, so the search order is deterministic) in
   ``custom_fonts/`` whose declared family matches the style template's own
   requested *family* -- checked with ``clipping.fonts.font_file_declares``
   (RC-A1: that module may be imported, never modified) when PIL is
   importable, else accepted on a loose filename match, since without PIL
   there is no way to read a font file's own name table at all. Either way
   *why* a file was (or was not) accepted is recorded in the returned
   ``reason``, never silently.
2. otherwise the committed ``assets/fonts/Montserrat-Black.ttf`` (DEC-159,
   OFL-licensed, tracked on purpose despite ``.gitignore``'s general
   ``custom_fonts/``/``*.ttf`` exclusion -- see ``assets/fonts/
   fonts_index.json``).

**PIL stays out of the render path at import time** (DEC-159, RC-A1): this
module imports nothing beyond the standard library and ``clipping.fonts``
at module load; ``clipping.fonts.font_file_declares`` itself imports PIL
lazily, inside the call, exactly the way ``clipping/fonts.py`` already
documents.

**The fallback font's own ASS Fontname.** ``assets/fonts/Montserrat-
Black.ttf`` declares family "Montserrat", style "Black" (its name table,
read with PIL; confirmed independently with ``fc-scan``, which also
reports the OS/2-derived alias family "Montserrat Black" / style
"Regular" many toolchains synthesise for a single-weight face). Neither
alias is picked by guesswork here: :data:`FALLBACK_FONT_FAMILY` is pinned
to whichever string a real ``ass=...:fontsdir=...`` libass render actually
matched (this stage's own scratch-render verification step) rather than
assumed from the name table alone.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
from pathlib import Path

from clipping import fonts as _clipping_fonts

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CUSTOM_FONTS_DIR = REPO_ROOT / "custom_fonts"
FALLBACK_FONT_FILE = REPO_ROOT / "assets" / "fonts" / "Montserrat-Black.ttf"

# Pinned against a real libass render (this stage's scratch-proof step),
# not guessed from the name table alone -- see module docstring.
FALLBACK_FONT_FAMILY = "Montserrat Black"

_FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")


# ------------------------------------------------------------------ helpers

def _pil_available() -> bool:
    return importlib.util.find_spec("PIL") is not None


def _sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_relative(path) -> str:
    return os.path.relpath(str(path), str(REPO_ROOT)).replace(os.sep, "/")


def _normalise_filename(name: str) -> str:
    stem = os.path.splitext(name)[0]
    stem = stem.replace("-", " ").replace("_", " ")
    return " ".join(stem.split()).casefold()


def _normalise_family(family: str) -> str:
    return " ".join(str(family or "").split()).casefold()


def _filename_matches(filename: str, family: str) -> bool:
    """A loose, PIL-free fallback check: the requested *family*'s words
    appear in the filename (or vice versa), casefolded and dash/underscore
    -insensitive -- e.g. ``"Montserrat-ExtraBold.ttf"`` accepted for a
    requested family of ``"Montserrat ExtraBold"``. Deliberately looser
    than :func:`clipping.fonts.family_matches` (which reads the file's own
    name table): with no PIL there is nothing to read, so a filename match
    is the best this module can do, and the returned ``reason`` says so."""
    norm_file = _normalise_filename(filename)
    norm_family = _normalise_family(family)
    if not norm_family or not norm_file:
        return False
    return norm_family in norm_file or norm_file in norm_family


def _candidate_files(directory) -> list:
    directory = Path(directory)
    if not directory.is_dir():
        return []
    names = [n for n in os.listdir(directory) if os.path.splitext(n)[1].lower() in _FONT_EXTENSIONS]
    return sorted(names)


def _fallback_record() -> dict:
    return {
        "family": FALLBACK_FONT_FAMILY,
        "file": _repo_relative(FALLBACK_FONT_FILE),
        "sha256": _sha256(FALLBACK_FONT_FILE),
        "reason": "no matching font in custom_fonts/; using the committed Montserrat Black fallback (DEC-159)",
    }


# ------------------------------------------------------------------- public

def resolve_font(family: str, *, custom_fonts_dir=None) -> dict:
    """Resolve the font file to burn subtitles with for a style template
    requesting *family* (its own ``typography.font_family``, e.g.
    "Montserrat ExtraBold"). *custom_fonts_dir* defaults to
    :data:`CUSTOM_FONTS_DIR`; a caller (a test, or a future runner given a
    non-default layout) may pass any directory.

    Returns ``{"family", "file", "sha256", "reason"}`` (module docstring):
    ``family`` is the ASS ``Fontname`` styles must reference -- for a
    ``custom_fonts/`` match this is *family* itself (the match already
    proves that string resolves to the accepted file); for the fallback it
    is :data:`FALLBACK_FONT_FAMILY`, pinned separately (module docstring).
    ``file`` is repo-relative, ``sha256`` is of the file's own bytes, and
    ``reason`` names which branch fired and why -- never silent.
    """
    directory = Path(custom_fonts_dir) if custom_fonts_dir is not None else CUSTOM_FONTS_DIR
    pil_ok = _pil_available()

    for name in _candidate_files(directory):
        path = directory / name
        if pil_ok:
            if _clipping_fonts.font_file_declares(str(path), family):
                return {
                    "family": family,
                    "file": _repo_relative(path),
                    "sha256": _sha256(path),
                    "reason": f"custom_fonts/{name} declares the requested family {family!r} (clipping.fonts.font_file_declares)",
                }
        else:
            if _filename_matches(name, family):
                return {
                    "family": family,
                    "file": _repo_relative(path),
                    "sha256": _sha256(path),
                    "reason": (
                        f"PIL is not installed, so custom_fonts/{name} was accepted by a filename match "
                        f"against requested family {family!r}; the file's own declared family is unverified"
                    ),
                }

    return _fallback_record()


def stage(record: dict, fonts_dir) -> str:
    """Copy *record*'s own ``"file"`` (repo-relative, from
    :func:`resolve_font`) into *fonts_dir* -- the render runner's own
    already-resolved ``render/fonts/`` folder (spec: "the font is staged
    into render/fonts/ and passed as fontsdir="); never a path built from
    user input here, *fonts_dir* is taken as given. Returns the staged
    file's own path. Creates *fonts_dir* if missing (a fresh episode's
    first render), and overwrites an existing copy (``shutil.copy2``) so a
    stale stage from an earlier render is never trusted over the current
    resolution.
    """
    src = REPO_ROOT / record["file"]
    os.makedirs(str(fonts_dir), exist_ok=True)
    dest = os.path.join(str(fonts_dir), os.path.basename(record["file"]))
    shutil.copy2(str(src), dest)
    return dest

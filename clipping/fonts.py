"""Does the font file on disk actually declare the family the config asks for?

Why this module exists. Every clip rendered on 2026-09-21 had the wrong subtitle
typeface, and the pipeline reported success the whole way:

    ✅ Font 'Montserrat-Regular.ttf' already exists and is valid.
    ✅ All fonts prepared successfully in: /app/custom_fonts
    ...
    fontselect: (Montserrat, 400, 0) -> /usr/.../DejaVuSans.ttf, 0, DejaVuSans

The `subtitles=` filter already passes `fontsdir`, at all four burn sites, and it
works. The problem was the file: the URL configured for Montserrat
(`cdn.jsdelivr.net/fontsource/fonts/montserrat@latest/latin-400-normal.ttf`)
serves a face whose name table says **Montserrat Thin**. libass loaded it, found
no family called "Montserrat", and fell through to fontconfig, which handed back
DejaVuSans.

It stayed broken because the validity check was `os.path.getsize(path) > 1000`.
A wrong-but-large font is "valid" forever and is never re-fetched, so fixing the
URL alone would have changed nothing on any machine that had already cached the
bad file -- and `custom_fonts/` is bind-mounted, so that is every machine.

Worse than the wrong glyphs: PIL loads the same file by absolute PATH
(clipping/studio/subtitles.py), so family names never come into it and there is
no fallback. The line-wrapping and \\pos centering written into every .ass were
measured from hairline Thin metrics while libass drew DejaVuSans.

Stdlib only at import time; PIL is imported lazily inside font_family_name, so
the comparison rule stays testable in the pytest-only CI environment.
"""

from __future__ import annotations


def normalise_family(name):
    """Casefolded, whitespace-collapsed, for comparison only."""
    return " ".join(str(name or "").split()).casefold()


def family_matches(expected, family, style=None):
    """Whether a font declaring (*family*, *style*) satisfies *expected*.

    ``expected`` is the config's ``nama``, which is what goes into the ASS
    ``Fontname`` and therefore what libass looks up.

    Two forms are accepted, because the font table legitimately uses both:

    * the family alone -- ``nama`` "Montserrat" against a file whose name table
      says family "Montserrat", style "Regular";
    * family plus style -- ``nama`` "Montserrat Black" against a file whose name
      table says family "Montserrat", style "Black", which is how the DEFAULT
      style's fonts are named.

    "Montserrat" against family "Montserrat Thin" matches NEITHER form, which is
    the bug this function exists to catch.
    """
    want = normalise_family(expected)
    if not want:
        return False

    fam = normalise_family(family)
    if not fam:
        return False
    if want == fam:
        return True

    sty = normalise_family(style)
    return bool(sty) and want == f"{fam} {sty}"


def font_family_name(path):
    """``(family, style)`` from a font file's name table, or ``None``.

    Uses PIL, which is already a dependency of the render path, rather than
    adding fontTools. Returns None rather than raising: a file that cannot be
    parsed is a file to re-download, not a crash.

    The file is opened here and the HANDLE is passed to PIL, which matters more
    than it looks. ``ImageFont.truetype(path, ...)`` falls back to searching the
    system font directories for a file of the same BASENAME when the path itself
    will not load -- and ``register_fonts_for_libass`` copies these very fonts
    into ``~/.local/share/fonts``. So the path form happily reported
    "Montserrat Thin" for a file of pure garbage, by reading the stale installed
    copy instead. A validator that can silently inspect a different file than the
    one it was asked about is worse than no validator: it would have passed the
    bind-mounted bad font and left the bug exactly where it was.
    """
    try:
        from PIL import ImageFont

        with open(path, "rb") as handle:
            family, style = ImageFont.truetype(handle, 16).getname()
        return (family or ""), (style or "")
    except Exception:  # noqa: BLE001 - unreadable, truncated, or not a font
        return None


def font_file_declares(path, expected):
    """Whether the font at *path* declares the family *expected* asks for.

    A file PIL cannot read at all counts as not matching, so the caller
    re-downloads it. That is the right default: the alternative is trusting a
    file we could not identify, which is exactly how the Thin face survived.
    """
    names = font_family_name(path)
    if names is None:
        return False
    return family_matches(expected, names[0], names[1])

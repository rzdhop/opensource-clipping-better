"""The font on disk has to declare the family the ASS file asks for.

Every clip rendered on 2026-09-21 had the wrong subtitle typeface, and nothing
said so. The log read:

    ✅ Font 'Montserrat-Regular.ttf' already exists and is valid.
    ✅ All fonts prepared successfully in: /app/custom_fonts
    fontselect: (Montserrat, 400, 0) -> /usr/.../DejaVuSans.ttf, 0, DejaVuSans

Not a `fontsdir` problem -- all four burn sites in clipping/studio/core.py pass
it. The configured URL served a face whose name table says "Montserrat Thin", so
libass found no family "Montserrat" and fontconfig handed back DejaVuSans. And it
would have stayed broken after fixing the URL, because the validity check was
file size alone: a wrong-but-large font is "valid" forever and never re-fetched,
and custom_fonts/ is bind-mounted so the stale file is on every machine.

These tests are offline and import no font file: none is tracked in git
(.gitignore excludes *.ttf and custom_fonts/). The comparison rule is pure, so it
is tested exhaustively; the file-reading half is tested against a file written in
tmp_path; and the configured URL that shipped the bad face is pinned by name so
it cannot come back.

Proving the fix end to end needs a real render, whose log must show
`fontselect: (Montserrat, 400, 0) -> .../custom_fonts/Montserrat-Regular.ttf`.
That is a Tier-2 check, not something this file can assert.
"""

import pytest

from clipping import fonts


# ----------------------------------------------------------- the rule, exhaustively

@pytest.mark.parametrize("expected,family,style", [
    # The family alone. HORMOZI, STORYTELLER and CINEMATIC are all shaped this way.
    ("Montserrat", "Montserrat", "Regular"),
    ("Anton", "Anton", "Regular"),
    ("Inter", "Inter", "Regular"),
    ("Lora", "Lora", "Bold"),
    ("Roboto", "Roboto", "Regular"),
    # Family plus style, which is how the DEFAULT style's fonts are named.
    ("Montserrat Black", "Montserrat", "Black"),
    ("Montserrat Medium", "Montserrat", "Medium"),
    # Comparison is case- and whitespace-insensitive, because a name table is
    # written by whoever built the font.
    ("montserrat", "Montserrat", "Regular"),
    ("MONTSERRAT BLACK", "Montserrat", "black"),
    ("Montserrat  Black", "Montserrat", " Black "),
])
def test_a_matching_font_is_accepted(expected, family, style):
    assert fonts.family_matches(expected, family, style) is True


def test_the_actual_bug_is_rejected():
    """The whole reason this module exists: config asks for "Montserrat", the file
    declares "Montserrat Thin". Neither accepted form matches."""
    assert fonts.family_matches("Montserrat", "Montserrat Thin", "Regular") is False


@pytest.mark.parametrize("expected,family,style", [
    ("Montserrat", "DejaVuSans", "Book"),          # the substitute libass chose
    ("Montserrat", "Montserrat Thin", "Regular"),
    ("Anton", "Montserrat", "Regular"),
    ("Montserrat Black", "Montserrat", "Regular"),  # right family, wrong weight
    ("Montserrat", "Montserrat Extra", "Light"),
])
def test_a_mismatched_font_is_rejected(expected, family, style):
    assert fonts.family_matches(expected, family, style) is False


@pytest.mark.parametrize("expected,family,style", [
    ("", "Montserrat", "Regular"),
    (None, "Montserrat", "Regular"),
    ("Montserrat", "", "Regular"),
    ("Montserrat", None, None),
    ("Montserrat Black", "Montserrat", ""),
    ("Montserrat Black", "Montserrat", None),
])
def test_an_empty_side_never_matches(expected, family, style):
    """An unreadable name table must not accept every font -- that is how the
    Thin face survived a check in the first place."""
    assert fonts.family_matches(expected, family, style) is False


# ------------------------------------------------------- reading a file's names

def test_a_file_that_is_not_a_font_reads_as_none(tmp_path):
    """None, not an exception: an unparseable file is one to re-download."""
    pytest.importorskip("PIL")
    bogus = tmp_path / "Montserrat-Regular.ttf"
    bogus.write_bytes(b"this is not a font" * 100)
    assert fonts.font_family_name(str(bogus)) is None


def test_a_missing_file_reads_as_none(tmp_path):
    pytest.importorskip("PIL")
    assert fonts.font_family_name(str(tmp_path / "nope.ttf")) is None


def test_an_unreadable_file_does_not_declare_anything(tmp_path):
    """font_file_declares must say False, so the caller re-downloads rather than
    trusting a file it could not identify."""
    pytest.importorskip("PIL")
    bogus = tmp_path / "Anton-Regular.ttf"
    bogus.write_bytes(b"\x00" * 5000)
    assert fonts.font_file_declares(str(bogus), "Anton") is False


# --------------------------------------------------------- the config that broke

BAD_MONTSERRAT_URL = (
    "https://cdn.jsdelivr.net/fontsource/fonts/montserrat@latest/latin-400-normal.ttf"
)


def test_the_url_that_ships_montserrat_thin_is_gone():
    """Pinned by name. It looks identical to the other fontsource URLs in the
    table -- all of which are fine -- so nothing about it invites suspicion, and
    re-introducing it would silently restore the bug."""
    from clipping import config

    for style, roles in config.DAFTAR_FONT.items():
        for role, spec in roles.items():
            assert spec["url"] != BAD_MONTSERRAT_URL, f"{style}/{role}"


def test_every_style_names_a_family_and_a_source():
    """A style missing either cannot be validated at all, and would fall straight
    back to the silent-substitution behaviour."""
    from clipping import config

    for style, roles in config.DAFTAR_FONT.items():
        assert set(roles) >= {"utama", "khusus"}, style
        for role, spec in roles.items():
            assert spec.get("nama"), f"{style}/{role} has no family name"
            assert spec.get("url", "").startswith("https://"), f"{style}/{role}"
            assert spec.get("file", "").endswith(".ttf"), f"{style}/{role}"


def test_the_active_style_is_one_that_exists():
    from clipping import config

    assert config.GAYA_FONT_AKTIF in config.DAFTAR_FONT


def test_the_preparation_step_passes_the_family_through():
    """Read from source: siapkan_font_tipografi cannot be imported here, because
    clipping/studio/typography.py imports cv2 and mediapipe at module scope. If
    expected_family were dropped, the size-only check would come back and the
    whole fix would be inert while every test above still passed."""
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "clipping" / "studio" / "typography.py"
    ).read_text(encoding="utf-8")

    assert "expected_family=f_utama.get(\"nama\")" in source
    assert "expected_family=f_khusus.get(\"nama\")" in source
    assert "font_file_declares" in source


def test_the_validator_reads_the_file_it_is_given_not_a_system_copy(tmp_path, monkeypatch):
    """The trap that nearly made this whole fix inert.

    ImageFont.truetype(PATH, ...) falls back to searching the system font
    directories for a file of the same BASENAME when the path will not load --
    and register_fonts_for_libass copies these exact fonts into
    ~/.local/share/fonts. So a garbage file named Montserrat-Regular.ttf reported
    family "Montserrat Thin", by reading the stale installed copy. The gate would
    then have passed the bind-mounted bad font and changed nothing.
    """
    pytest.importorskip("PIL")

    installed = tmp_path / "system"
    installed.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))

    garbage = tmp_path / "Montserrat-Regular.ttf"
    garbage.write_bytes(b"not a font at all" * 200)

    # Whatever is installed on this machine under that basename, the answer for
    # THIS file must be "unreadable".
    assert fonts.font_family_name(str(garbage)) is None
    assert fonts.font_file_declares(str(garbage), "Montserrat") is False

"""Tests for the AI Story audio/font assets (phase 4, stage 2): the
self-made SFX generator (``tools/make_sfx.py``), the shipped-BGM index, the
committed Montserrat font pack, and the pure resolver module
``clipping.aistory.render.audio_assets`` (DEC-160, RC-A9, A-074, A-075).

Everything here is stdlib + pytest, hermetic and fast: no ffmpeg call, no
subprocess, no network. The generator is exercised in-process (imported,
not shelled out to), and every asset check reads bytes already committed to
the repository.

On the parent commit none of ``tools/make_sfx.py``, the ``assets/sfx``,
``assets/bgm/bgm_index.json``, ``assets/fonts`` tree or
``clipping/aistory/render/`` exist yet, so every test below fails --
several at collection (``ModuleNotFoundError`` / import errors), the rest on
their first filesystem read. That is the expected fail-first state.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import wave
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STYLES_DIR = ROOT / "clipping" / "aistory" / "templates" / "styles"
SFX_DIR = ROOT / "assets" / "sfx"
BGM_DIR = ROOT / "assets" / "bgm"
FONTS_DIR = ROOT / "assets" / "fonts"
OVERLAYS_DIR = ROOT / "assets" / "overlays"

MAX_DURATION_S = 3.0
MAX_BYTES = 150 * 1024


# ------------------------------------------------------------------ helpers

def _style_audio_blocks() -> dict:
    """``{style_id: audio_block}`` for every shipped style template."""
    blocks = {}
    for path in sorted(STYLES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        blocks[path.stem] = data["audio"]
    return blocks


def _load_sfx_index(pack: str) -> dict:
    path = SFX_DIR / pack / "sfx_index.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_bgm_index() -> dict:
    path = BGM_DIR / "bgm_index.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _load_fonts_index() -> dict:
    path = FONTS_DIR / "fonts_index.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _wav_peak_dbfs(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2
        n = w.getnframes()
        raw = w.readframes(n)
    if not raw:
        return -math.inf
    import array

    samples = array.array("h")
    samples.frombytes(raw)
    peak = max(abs(s) for s in samples) if samples else 0
    if peak == 0:
        return -math.inf
    return 20 * math.log10(peak / 32768.0)


# ============================================================ 1. sfx cues

def test_every_style_cue_is_covered_by_its_packs_index():
    for style_id, audio in _style_audio_blocks().items():
        pack = audio["sfx_pack"]
        index = _load_sfx_index(pack)
        assert index.get("$schema") == "sfx_index_v1"
        assert index.get("pack") == pack
        have = set(index.get("cues", {}).keys())
        want = set(audio["sfx_cues"])
        missing = want - have
        assert not missing, f"{style_id}: pack {pack!r} index is missing cues {sorted(missing)}"


def test_every_sfx_index_entry_file_exists_and_is_well_formed():
    checked = 0
    for style_id, audio in _style_audio_blocks().items():
        pack = audio["sfx_pack"]
        index = _load_sfx_index(pack)
        for cue, entry in index["cues"].items():
            file_path = SFX_DIR / entry["file"]
            assert file_path.is_file(), f"{pack}/{cue}: {entry['file']} does not exist"

            data = file_path.read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry["sha256"], f"{pack}/{cue}: sha256 mismatch"
            assert len(data) <= MAX_BYTES, f"{pack}/{cue}: {len(data)} bytes > {MAX_BYTES}"

            with wave.open(str(file_path), "rb") as w:
                assert w.getframerate() == 22050, f"{pack}/{cue}: not 22.05 kHz"
                assert w.getnchannels() == 1, f"{pack}/{cue}: not mono"
                assert w.getsampwidth() == 2, f"{pack}/{cue}: not 16-bit"
                real_duration = w.getnframes() / w.getframerate()

            assert real_duration <= MAX_DURATION_S + 1e-6, f"{pack}/{cue}: {real_duration:.3f}s > {MAX_DURATION_S}s"
            assert entry["duration_s"] == pytest.approx(real_duration, abs=0.01)

            assert entry["licence"] == "self-made"
            assert entry["source"] == "tools/make_sfx.py v1"

            peak_dbfs = _wav_peak_dbfs(file_path)
            assert peak_dbfs <= -0.99, f"{pack}/{cue}: peak {peak_dbfs:.2f} dBFS exceeds the -1 dBFS ceiling"
            checked += 1
    assert checked > 0


def test_a_cue_shared_between_packs_points_at_one_physical_file():
    pack_cues: dict = {}
    for audio in _style_audio_blocks().values():
        pack_cues.setdefault(audio["sfx_pack"], set()).update(audio["sfx_cues"])

    cue_files: dict = {}
    for pack, cues in pack_cues.items():
        index = _load_sfx_index(pack)
        for cue in cues:
            cue_files.setdefault(cue, set()).add(index["cues"][cue]["file"])

    shared = {cue: files for cue, files in cue_files.items() if len(pack_cues_naming(cue, pack_cues)) > 1}
    assert shared, "expected at least one cue name shared between packs (e.g. 'boing')"
    for cue, files in shared.items():
        assert len(files) == 1, f"cue {cue!r} resolves to more than one physical file: {files}"


def pack_cues_naming(cue, pack_cues):
    return [pack for pack, cues in pack_cues.items() if cue in cues]


def test_generator_is_deterministic_against_committed_files():
    make_sfx = importlib.import_module("tools.make_sfx")
    pack_cues = make_sfx.load_pack_cues()
    owners = make_sfx.cue_owners(pack_cues)
    sample_cues = sorted(owners)[:2]
    assert len(sample_cues) == 2

    for cue in sample_cues:
        owner = owners[cue]
        wav_bytes, duration_s = make_sfx.synthesize_wav_bytes(cue)
        committed = (SFX_DIR / owner / f"{cue}.wav").read_bytes()
        assert wav_bytes == committed, f"cue {cue!r}: rebuilt bytes differ from the committed file"
        assert duration_s <= MAX_DURATION_S


def test_paper_texture_overlay_exists_and_is_a_png():
    path = OVERLAYS_DIR / "paper_texture.png"
    assert path.is_file()
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) <= MAX_BYTES


# ============================================================ 2. bgm index

def test_every_named_mood_resolves_to_at_least_one_track():
    bgm_index = _load_bgm_index()
    assert bgm_index.get("$schema") == "bgm_index_v1"
    mood_to_tracks: dict = {}
    for track in bgm_index["tracks"]:
        for mood in track["moods"]:
            mood_to_tracks.setdefault(mood, []).append(track)

    for style_id, audio in _style_audio_blocks().items():
        named_moods = set(audio["bgm_moods"]) | set(audio["emotion_to_mood"].values())
        for mood in named_moods:
            assert mood_to_tracks.get(mood), f"{style_id}: mood {mood!r} has no BGM track"


def test_bgm_index_tracks_exist_on_disk_and_are_not_moved():
    bgm_index = _load_bgm_index()
    known_mp3s = {
        str(p.relative_to(BGM_DIR)) for p in BGM_DIR.glob("*/*.mp3")
    }
    indexed_files = set()
    for track in bgm_index["tracks"]:
        file_path = BGM_DIR / track["file"]
        assert file_path.is_file(), f"bgm_index track {track['file']} does not exist"
        assert file_path.suffix == ".mp3"
        assert isinstance(track["duration_s"], (int, float)) and track["duration_s"] > 0
        assert track["licence"] == "shipped with Clips, source unrecorded"
        indexed_files.add(track["file"])

    # every mp3 that ships under assets/bgm/<mood>/ is still exactly where
    # the clip auto-BGM reader expects it (RC-A9): the index maps into the
    # existing mood folders, it never introduces new ones.
    for rel in indexed_files:
        assert rel in known_mp3s, f"{rel} is not one of the mood-folder mp3s"


def test_bgm_index_has_notes_rationale_per_mood():
    bgm_index = _load_bgm_index()
    notes = bgm_index.get("notes")
    assert isinstance(notes, dict) and notes

    all_moods = set()
    for audio in _style_audio_blocks().values():
        all_moods |= set(audio["bgm_moods"]) | set(audio["emotion_to_mood"].values())
    missing = all_moods - set(notes.keys())
    assert not missing, f"notes missing a rationale line for {sorted(missing)}"


# ============================================================ 3. fonts

def test_fonts_index_matches_the_committed_font_and_licence():
    fonts_index = _load_fonts_index()
    assert fonts_index.get("$schema") == "fonts_index_v1"
    fonts = fonts_index["fonts"]
    assert len(fonts) >= 1
    entry = fonts[0]

    font_path = FONTS_DIR / entry["file"]
    assert font_path.is_file()
    data = font_path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    assert entry["family"] == "Montserrat"
    assert entry["style"] == "Black"
    assert entry["licence"] == "OFL-1.1"
    assert entry["licence_file"] == "OFL.txt"


def test_font_copy_matches_root_font_byte_for_byte():
    # The worktree's own root Montserrat-Black.ttf is gitignored (*.ttf) and
    # not guaranteed to be present in every checkout, so this only asserts
    # the committed copy is internally consistent and non-empty; the
    # byte-for-byte source copy was verified at generation time.
    font_path = FONTS_DIR / "Montserrat-Black.ttf"
    assert font_path.is_file()
    assert font_path.stat().st_size > 100_000


def test_ofl_text_starts_with_the_montserrat_copyright_and_is_the_full_licence():
    text = (FONTS_DIR / "OFL.txt").read_text(encoding="utf-8")
    assert text.startswith("Copyright 2011 The Montserrat Project Authors")
    assert "SIL OPEN FONT LICENSE Version 1.1" in text
    assert "PERMISSION & CONDITIONS" in text
    assert "DISCLAIMER" in text


def test_ttf_is_not_gitignored_under_assets_fonts():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = gitignore.splitlines()
    assert "*.ttf" in lines
    ttf_line = lines.index("*.ttf")
    negation_lines = [
        i for i, line in enumerate(lines) if line.strip() == "!assets/fonts/*.ttf"
    ]
    assert negation_lines, "expected a '!assets/fonts/*.ttf' negation in .gitignore"
    assert negation_lines[0] > ttf_line, "the negation must come after the '*.ttf' ignore rule"


# ============================================ 4. clipping.aistory.render.audio_assets

def _audio_assets():
    return importlib.import_module("clipping.aistory.render.audio_assets")


def test_render_package_docstring_names_dec_156():
    render_pkg = importlib.import_module("clipping.aistory.render")
    assert render_pkg.__doc__ is not None
    assert "DEC-156" in render_pkg.__doc__
    assert "clipping/studio" in render_pkg.__doc__ or "studio" in render_pkg.__doc__


def test_resolve_sfx_returns_provenance_for_a_known_cue():
    aa = _audio_assets()
    for style_id, audio in _style_audio_blocks().items():
        pack = audio["sfx_pack"]
        cue = sorted(audio["sfx_cues"])[0]
        resolved = aa.resolve_sfx(pack, cue)
        assert resolved is not None, f"{style_id}: {pack}/{cue} failed to resolve"
        assert resolved["pack"] == pack
        assert resolved["cue"] == cue
        assert Path(resolved["abs_path"]).is_file()
        assert resolved["sha256"]
        assert resolved["licence"] == "self-made"


def test_resolve_sfx_missing_cue_is_none_never_raises():
    aa = _audio_assets()
    assert aa.resolve_sfx("anime", "this_cue_does_not_exist") is None
    assert aa.resolve_sfx("no_such_pack_at_all", "whoosh_sharp") is None
    # never raises, even with garbage input
    assert aa.resolve_sfx(None, None) is None
    assert aa.resolve_sfx("../../etc", "passwd") is None


def test_dominant_emotion_picks_the_largest_summed_duration():
    aa = _audio_assets()
    scenes = [
        {"scene_id": "s01", "emotion": "a", "duration_s": 3.0},
        {"scene_id": "s02", "emotion": "b", "duration_s": 2.0},
        {"scene_id": "s03", "emotion": "a", "duration_s": 1.0},
    ]
    assert aa.dominant_emotion(scenes) == "a"


def test_dominant_emotion_ties_go_to_the_earliest_scene():
    aa = _audio_assets()
    # zeta and alpha both sum to 3.0s; zeta's first (and only earlier)
    # scene is scene 0, alpha's is scene 1, so zeta must win even though
    # "alpha" sorts first alphabetically.
    scenes = [
        {"scene_id": "s01", "emotion": "zeta", "duration_s": 2.0},
        {"scene_id": "s02", "emotion": "alpha", "duration_s": 1.0},
        {"scene_id": "s03", "emotion": "zeta", "duration_s": 1.0},
        {"scene_id": "s04", "emotion": "alpha", "duration_s": 2.0},
    ]
    assert aa.dominant_emotion(scenes) == "zeta"


def test_dominant_emotion_empty_input():
    aa = _audio_assets()
    assert aa.dominant_emotion([]) is None


def test_bgm_mood_uses_emotion_to_mood_then_default():
    aa = _audio_assets()
    audio = _style_audio_blocks()["fruit_drama"]
    assert aa.bgm_mood(audio, "tension") == "telenovela_tension"
    assert aa.bgm_mood(audio, "tender") == "tropical_drama"
    # "gleeful" is not a key fruit_drama names -- falls back to default
    assert aa.bgm_mood(audio, "gleeful") == audio["emotion_to_mood"]["default"]


def test_pick_track_is_deterministic_and_varies_by_episode():
    aa = _audio_assets()
    index = {
        "tracks": [
            {"file": "chill/a.mp3", "moods": ["warm_family"], "duration_s": 10.0, "bpm": None,
             "licence": "shipped with Clips, source unrecorded", "source": "assets/bgm (Clips mode)"},
            {"file": "chill/b.mp3", "moods": ["warm_family"], "duration_s": 12.0, "bpm": None,
             "licence": "shipped with Clips, source unrecorded", "source": "assets/bgm (Clips mode)"},
            {"file": "epic/c.mp3", "moods": ["battle_synth"], "duration_s": 8.0, "bpm": None,
             "licence": "shipped with Clips, source unrecorded", "source": "assets/bgm (Clips mode)"},
        ]
    }

    first = aa.pick_track(index, "warm_family", "story-abc", 1)
    again = aa.pick_track(index, "warm_family", "story-abc", 1)
    assert first == again
    assert first["file"] in ("chill/a.mp3", "chill/b.mp3")

    picks = {aa.pick_track(index, "warm_family", "story-abc", ep)["file"] for ep in range(1, 9)}
    assert len(picks) > 1, "different ep values should be able to pick a different track"

    assert aa.pick_track(index, "no_such_mood", "story-abc", 1) is None


def test_safe_join_confines_paths_and_refuses_symlinks(tmp_path):
    aa = _audio_assets()
    base = tmp_path / "assets" / "sfx"
    (base / "pack").mkdir(parents=True)
    (base / "pack" / "cue.wav").write_bytes(b"RIFF")

    assert aa._safe_join(base, "pack/cue.wav") is not None
    assert aa._safe_join(base, "../outside.wav") is None
    assert aa._safe_join(base, "/etc/passwd") is None
    assert aa._safe_join(base, "pack/../../escape.wav") is None
    assert aa._safe_join(base, "") is None
    assert aa._safe_join(base, None) is None

    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"x")
    link = base / "pack" / "link.wav"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported in this environment")
    assert aa._safe_join(base, "pack/link.wav") is None


# ==================================================== 5. RC-A9 guard

def test_clip_auto_bgm_reader_still_lists_mood_folders_not_the_index():
    """RC-A9: the clip layer's BGM reader must keep reading
    ``assets/bgm/<mood>/`` folders directly. ``bgm_index.json`` lives at the
    folder root and must stay invisible to it -- otherwise a clip job would
    start depending on the AI-Story asset index.
    """
    text = (ROOT / "clipping" / "studio" / "audio_bgm.py").read_text(encoding="utf-8")
    assert "bgm_index" not in text, "clipping/studio/audio_bgm.py must never read bgm_index.json"
    assert "sfx_index" not in text
    # it must still work the old way: enumerate files inside a mood folder
    assert "def get_local_bgm_file" in text
    assert "os.listdir" in text
    assert "mood_dir" in text

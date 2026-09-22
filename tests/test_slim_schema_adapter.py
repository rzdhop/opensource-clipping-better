"""The boundary between the slim analysis output and the render layer.

This is the file standing between a redesigned analysis and a render layer with
zero automated coverage (RC-7), which is verified only by live renders. A key
the render layer reads and the adapter does not produce is a feature silently
switching itself off — or, for the three required keys, a KeyError minutes into
a job, after the transcription and the analysis have already been paid for.

The guard reads the render layer with ``ast`` rather than importing it, because
``clipping/studio/*`` imports cv2 and mediapipe at module scope and the CI suite
installs pytest and nothing else (DEC-012).
"""

import ast
import pathlib

import pytest

from clipping.analysis import adapter, derive, snap

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Names a clip dict is bound to in the consuming code.
CLIP_NAMES = {"clip", "klip", "item", "clip_data", "data_klip"}

CONSUMERS = [
    PROJECT_ROOT / "clipping" / "studio" / "core.py",
    PROJECT_ROOT / "clipping" / "studio" / "subtitles.py",
    PROJECT_ROOT / "clipping" / "runner.py",
]


def _keys_read(path):
    """``{key: is_required}`` for every clip key *path* reads.

    ``clip["k"]`` is required (raises KeyError); ``clip.get("k")`` is optional.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id in CLIP_NAMES and isinstance(node.slice, ast.Constant):
                if isinstance(node.slice.value, str):
                    found[node.slice.value] = True
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and isinstance(node.func.value, ast.Name):
                if node.func.value.id in CLIP_NAMES and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        found.setdefault(arg.value, False)
    return found


def _all_keys_read():
    merged = {}
    for path in CONSUMERS:
        for key, required in _keys_read(path).items():
            merged[key] = merged.get(key, False) or required
    return merged


KNOWN_ELSEWHERE = set(adapter.PIPELINE_WRITTEN_KEYS) | set(adapter.NORMALIZER_KEYS)


# --------------------------------------------------------------- the guard

def test_every_key_the_render_layer_reads_is_accounted_for():
    """The whole point of this file.

    If this fails, the render layer started reading a key nobody produces.
    Either add it to LEGACY_KEYS and produce it in to_legacy_clip, or — if the
    pipeline writes it itself — list it in PIPELINE_WRITTEN_KEYS with a comment
    saying where.
    """
    unaccounted = sorted(
        key
        for key in _all_keys_read()
        if key not in adapter.LEGACY_KEYS and key not in KNOWN_ELSEWHERE
    )
    assert unaccounted == [], (
        f"clipping/studio reads keys nothing produces: {unaccounted}"
    )


def test_the_required_keys_are_exactly_the_unguarded_ones():
    """A key read with [] and not produced is a KeyError mid-render, not a
    missing feature, so the two lists must agree."""
    required = sorted(
        key
        for key, is_required in _all_keys_read().items()
        if is_required and key not in KNOWN_ELSEWHERE
    )
    assert required == sorted(adapter.REQUIRED_KEYS)


def test_the_guard_can_actually_see_the_render_layer():
    """A guard that parses nothing passes vacuously."""
    keys = _all_keys_read()
    assert len(keys) > 10
    assert "start_time" in keys and keys["start_time"] is True
    assert "typography_plan" in keys


# ------------------------------------------------------------ golden output

@pytest.fixture
def beats():
    out = []
    t = 0.0
    for i in range(12):
        out.append(
            {
                "i": i,
                "start": t,
                "end": t + 4.0,
                "text": f"sentence number {i} about islands and twelve people",
                "n_words": 8,
                "w0": i * 8,
                "w1": i * 8 + 7,
            }
        )
        t += 4.5
    return out


@pytest.fixture
def words(beats):
    out = []
    for beat in beats:
        parts = beat["text"].split()
        step = (beat["end"] - beat["start"]) / len(parts)
        for j, part in enumerate(parts):
            out.append(
                {
                    "word": part,
                    "start": beat["start"] + j * step,
                    "end": beat["start"] + (j + 1) * step,
                }
            )
    return out


META = {
    "score": 88,
    "title_native": "Douze personnes sur une île",
    "title_en": "Twelve people on an island",
    "hashtags": ["#realitytv", "#island", "#drama"],
    "desc_hook": "Nobody expected what happened on day three.",
    "desc_context": "A reality show strands twelve strangers together.",
    "keywords": ["reality tv", "island", "survival", "drama", "twelve", "show"],
    "caption_native": "Ils ne savaient pas.",
    "caption_en": "They had no idea.",
    "reason": "A complete setup and payoff in under a minute.",
    "hook_beats": [1],
    "emphasis": ["islands", "twelve", "people"],
    "broll_queries": ["tropical island aerial"],
    "mood": "suspense",
    "drop_beats": [],
}


class Cfg:
    durasi_hook = 3
    jumlah_clip = 5
    use_broll = True
    pexels_api_key = "x"
    hook_v2 = False
    no_segment_trim = False


def _build(beats, words, meta=META, cfg=None):
    from clipping.analysis.analyzer import _derive_all

    span = snap.Span(0.0, 40.0, 0, 8, 88)
    cfg = cfg or Cfg()
    clip_beats = derive.clip_beats_of(beats, span.b0, span.b1)
    clip_words = derive.clip_words_of(beats, words, span.b0, span.b1)
    derived = _derive_all(meta, span, clip_beats, clip_words, cfg, True)
    return adapter.to_legacy_clip(rank=1, span=span, meta=meta, derived=derived)


def test_a_built_clip_carries_every_legacy_key(beats, words):
    clip = _build(beats, words)
    for key in adapter.REQUIRED_KEYS:
        assert key in clip, key
    for key in adapter.OPTIONAL_KEYS:
        if key in ("keep_segments", "hook_v2"):
            continue  # deliberately omitted when they do not apply
        assert key in clip, key


def test_a_built_clip_is_renderable(beats, words):
    adapter.assert_renderable(_build(beats, words))


def test_types_match_what_the_render_layer_assumes(beats, words):
    clip = _build(beats, words)
    assert isinstance(clip["rank"], int)
    assert isinstance(clip["start_time"], float)
    assert isinstance(clip["end_time"], float)
    assert isinstance(clip["typography_plan"], list)
    assert isinstance(clip["broll_list"], list)
    assert isinstance(clip["keyword_tags"], list)
    assert isinstance(clip["hastag"], str)


def test_the_hook_sits_inside_the_clip(beats, words):
    clip = _build(beats, words)
    assert clip["start_time"] <= clip["hook_start_time"] < clip["hook_end_time"]
    assert clip["hook_end_time"] <= clip["end_time"]


def test_broll_never_covers_the_hook(beats, words):
    """The three seconds that decide whether anyone watches at all."""
    clip = _build(beats, words)
    assert clip["broll_list"]
    for entry in clip["broll_list"]:
        assert entry["start_time"] >= clip["hook_end_time"]
        assert entry["end_time"] <= clip["end_time"]


def test_emphasis_words_are_really_in_the_transcript(beats, words):
    """An invented word cannot be matched by buat_file_ass and would render
    unstyled, so the failure used to be invisible."""
    clip = _build(beats, words)
    spoken = {derive.normalize_word(w["word"]) for w in words}
    assert clip["typography_plan"]
    for entry in clip["typography_plan"]:
        assert entry["kata_utama"] in spoken


def test_an_invented_emphasis_word_is_dropped(beats, words):
    meta = dict(META, emphasis=["islands", "definitelynotspoken"])
    clip = _build(beats, words, meta)
    kata = [e["kata_utama"] for e in clip["typography_plan"]]
    assert "definitelynotspoken" not in kata
    assert "islands" in kata


def test_typography_entries_have_the_shape_subtitles_py_reads(beats, words):
    clip = _build(beats, words)
    for entry in clip["typography_plan"]:
        assert set(entry) == {"kata_utama", "scale_level", "style", "animasi"}
        assert entry["scale_level"] in (1, 2, 3)
        assert entry["style"] in ("utama", "khusus")
        assert entry["animasi"] in ("bounce_pop", "stagger_up")


def test_hashtags_are_two_or_three(beats, words):
    clip = _build(beats, words)
    tags = clip["hastag"].split()
    assert 2 <= len(tags) <= 3
    assert all(tag.startswith("#") for tag in tags)


def test_keywords_are_capped(beats, words):
    clip = _build(beats, words)
    assert 1 <= len(clip["keyword_tags"]) <= 8


def test_the_native_title_goes_to_the_indonesian_key(beats, words):
    """The key name is historical. A French video gets a French title in it;
    renaming it would touch the render layer and both uploaders for no gain."""
    clip = _build(beats, words)
    assert clip["title_indonesia"] == META["title_native"]
    assert clip["title_inggris"] == META["title_en"]
    assert clip["tiktok_caption_id"] == META["caption_native"]
    assert clip["tiktok_caption"] == META["caption_en"]


def test_keep_segments_is_omitted_when_it_would_do_nothing(beats, words):
    """studio/core.py:456 only activates trimming for more than one segment, so
    an empty or single-segment value is a key that does nothing."""
    clip = _build(beats, words)
    assert "keep_segments" not in clip


def test_hook_v2_is_omitted_when_the_flag_is_off(beats, words):
    clip = _build(beats, words)
    assert "hook_v2" not in clip


def test_hook_v2_is_built_when_the_flag_is_on(beats, words):
    class WithHookV2(Cfg):
        hook_v2 = True
        hook_v2_items = 3

    clip = _build(beats, words, cfg=WithHookV2())
    assert clip["hook_v2"]["enabled"] is True
    assert clip["hook_v2"]["transition"]["type"] in ("white_flash", "glitch")
    for item in clip["hook_v2"]["items"]:
        assert set(item) == {"start_time", "end_time", "text"}


# --------------------------------------------- a clip with no metadata at all

def test_a_clip_whose_metadata_failed_still_renders(beats, words):
    """A failed pass-C request costs one clip's metadata, never the job."""
    from clipping.analysis.analyzer import _derive_all

    span = snap.Span(0.0, 40.0, 0, 8, 70)
    cfg = Cfg()
    derived = _derive_all({}, span, derive.clip_beats_of(beats, 0, 8),
                          derive.clip_words_of(beats, words, 0, 8), cfg, True)
    clip = adapter.to_legacy_clip(rank=2, span=span, meta={}, derived=derived,
                                  gist="Clip 2")

    adapter.assert_renderable(clip)
    assert clip["title_inggris"] == "Clip 2"
    assert clip["bgm_mood"] == "chill"
    assert clip["typography_plan"] == []
    assert clip["broll_list"] == []


# ---------------------------------------------- through the real normalizer

def test_the_normalizer_accepts_what_the_adapter_produces(beats, words):
    """metadata.normalize_and_validate is a required step before the render
    loop and is what synthesizes every *_final field the uploaders read."""
    from clipping import metadata

    clips = metadata.normalize_and_validate([_build(beats, words)])

    assert len(clips) == 1
    clip = clips[0]
    for key in adapter.NORMALIZER_KEYS:
        assert key in clip, key
    assert clip["youtube_title_final"]
    assert clip["rank"] == 1
    adapter.assert_renderable(clip)


def test_assert_renderable_catches_a_broken_clip():
    with pytest.raises(ValueError):
        adapter.assert_renderable({"start_time": 0.0, "end_time": 1.0})
    with pytest.raises(ValueError):
        adapter.assert_renderable({"rank": 1, "start_time": 5.0, "end_time": 5.0})
    with pytest.raises(ValueError):
        adapter.assert_renderable({"rank": "1", "start_time": 0.0, "end_time": 1.0})

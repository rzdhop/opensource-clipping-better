"""Hosted transcription: parsing, stitching, and the chain.

The stitch is where a silent corruption would live — chunks overlap on purpose,
so those seconds are transcribed twice and one copy must go without taking a
real repetition with it. No network: the POST is injected.
"""

import pytest

from helpers import assert_valid_data_segmen

from clipping.providers import stt
from clipping.providers.registry import Link


def body(words, language="fr"):
    return {
        "language": language,
        "text": " ".join(w[0] for w in words),
        "words": [{"word": w, "start": s, "end": e} for w, s, e in words],
    }


def fake_post(*bodies):
    sent = []
    queue = list(bodies)

    def _post(url, key, file_path, fields):
        sent.append({"url": url, "fields": dict(fields), "file": file_path})
        if not queue:
            raise AssertionError("more requests than scripted")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    _post.sent = sent
    return _post


# ------------------------------------------------------------------ parsing

def test_words_become_the_transcript_contract():
    payload = body([("hello", 0.0, 0.4), ("world", 0.4, 0.9)])
    words = stt._words_from(payload)
    assert [w["word"] for w in words] == ["hello", "world"]
    assert words[0]["start"] == pytest.approx(0.0)


def test_an_offset_rebases_every_word():
    """A chunk starting at 600s reports its own words from zero."""
    words = stt._words_from(body([("late", 1.0, 1.5)]), offset=600.0)
    assert words[0]["start"] == pytest.approx(601.0)
    assert words[0]["end"] == pytest.approx(601.5)


def test_a_degenerate_word_span_is_widened():
    """assert_valid_data_segmen rejects end <= start, and providers do emit it."""
    words = stt._words_from(body([("x", 2.0, 2.0)]))
    assert words[0]["end"] > words[0]["start"]


def test_blank_and_malformed_words_are_skipped():
    payload = {"words": [
        {"word": "  ", "start": 0.0, "end": 1.0},
        {"word": "kept", "start": 1.0, "end": 2.0},
        {"word": "nostart", "end": 3.0},
    ]}
    assert [w["word"] for w in stt._words_from(payload)] == ["kept"]


def test_segments_are_a_fallback_when_there_are_no_words():
    """Better than failing the run: the same even-division approximation the
    VTT parser makes for an untagged cue."""
    payload = {"segments": [{"text": "three little words", "start": 0.0, "end": 3.0}]}
    words = stt._words_from(payload)
    assert [w["word"] for w in words] == ["three", "little", "words"]
    assert words[0]["start"] == pytest.approx(0.0)


# ----------------------------------------------------------------- stitching

def test_the_overlap_is_not_transcribed_twice():
    first = [{"word": "a", "start": 0.0, "end": 1.0},
             {"word": "b", "start": 1.0, "end": 2.0}]
    second = [{"word": "b", "start": 1.0, "end": 2.0},
              {"word": "c", "start": 2.0, "end": 3.0}]
    assert [w["word"] for w in stt._stitch(first, second)] == ["a", "b", "c"]


def test_a_genuine_repetition_across_the_seam_survives():
    """Speech repeats. 'no no' must not become 'no' because the seam fell
    between them — the same trap the VTT dedupe rule has."""
    first = [{"word": "no", "start": 0.0, "end": 1.0}]
    second = [{"word": "no", "start": 1.2, "end": 2.0}]
    assert len(stt._stitch(first, second)) == 2


def test_the_result_is_strictly_ordered():
    first = [{"word": "a", "start": 0.0, "end": 5.0}]
    second = [{"word": "b", "start": 4.9, "end": 6.0}]
    out = stt._stitch(first, second)
    for a, b in zip(out, out[1:]):
        assert b["start"] >= a["start"]


def test_stitching_onto_nothing_returns_the_incoming():
    incoming = [{"word": "a", "start": 0.0, "end": 1.0}]
    assert stt._stitch([], incoming) == incoming


def test_stitching_nothing_keeps_the_existing():
    existing = [{"word": "a", "start": 0.0, "end": 1.0}]
    assert stt._stitch(existing, []) == existing


# -------------------------------------------------------------- the request

@pytest.fixture
def one_chunk(monkeypatch, tmp_path):
    """Pretend extraction produced one small file, so no ffmpeg runs."""
    audio_file = tmp_path / "a.flac"
    audio_file.write_bytes(b"\x00" * 16)
    monkeypatch.setattr(stt.audio_mod, "extract", lambda *a, **kw: str(audio_file))
    monkeypatch.setattr(stt.audio_mod, "plan_chunks", lambda *a, **kw: [(0.0, 60.0)])
    return audio_file


def test_word_timestamps_are_always_requested(one_chunk):
    post = fake_post(body([("bonjour", 0.0, 0.5)]))
    stt.transcribe("v.mp4", chain=[Link("groq", "whisper-large-v3-turbo")],
                   keys={"groq": "k"}, on_log=lambda *a: None, post=post)

    fields = post.sent[0]["fields"]
    assert fields["response_format"] == "verbose_json"
    assert fields["timestamp_granularities[]"] == "word"


def test_groq_is_given_the_language_hint(one_chunk):
    post = fake_post(body([("bonjour", 0.0, 0.5)]))
    stt.transcribe("v.mp4", chain=[Link("groq", "whisper-large-v3-turbo")],
                   keys={"groq": "k"}, language="fr", on_log=lambda *a: None,
                   post=post)
    assert post.sent[0]["fields"]["language"] == "fr"


def test_voxtral_is_not_given_a_language(one_chunk):
    """Mistral rejects timestamp_granularities together with language, and word
    timings matter more than a language hint."""
    post = fake_post(body([("bonjour", 0.0, 0.5)]))
    stt.transcribe("v.mp4", chain=[Link("mistral", "voxtral-mini-latest")],
                   keys={"mistral": "k"}, language="fr", on_log=lambda *a: None,
                   post=post)
    assert "language" not in post.sent[0]["fields"]
    assert post.sent[0]["fields"]["timestamp_granularities[]"] == "word"


def test_the_endpoint_is_the_providers_own(one_chunk):
    post = fake_post(body([("x", 0.0, 0.5)]))
    stt.transcribe("v.mp4", chain=[Link("groq", "m")], keys={"groq": "k"},
                   on_log=lambda *a: None, post=post)
    assert post.sent[0]["url"].endswith("/audio/transcriptions")
    assert "groq.com" in post.sent[0]["url"]


# --------------------------------------------------------------- the result

def test_the_output_satisfies_the_transcript_contract(one_chunk):
    payload = body([("un", 0.0, 0.4), ("deux", 0.4, 0.9), ("trois", 0.9, 1.4),
                    ("quatre", 1.4, 1.9), ("cinq", 1.9, 2.4), ("six", 2.4, 2.9)])
    transkrip, segmen, language = stt.transcribe(
        "v.mp4", chain=[Link("groq", "m")], keys={"groq": "k"},
        max_words_per_subtitle=2, on_log=lambda *a: None, post=fake_post(payload),
    )

    assert_valid_data_segmen(segmen)
    assert language == "fr"
    assert transkrip.strip()
    assert [w["word"] for s in segmen for w in s["words"]] == [
        "un", "deux", "trois", "quatre", "cinq", "six"]


def test_an_empty_transcription_is_an_error_not_an_empty_success(one_chunk):
    """Returning empty would degrade into a silent no-subtitles render."""
    with pytest.raises(stt.SttError):
        stt.transcribe("v.mp4", chain=[Link("groq", "m")], keys={"groq": "k"},
                       on_log=lambda *a: None, post=fake_post({"words": []}))


# ---------------------------------------------------------------- the chain

def test_a_link_without_a_key_is_skipped(one_chunk):
    post = fake_post(body([("x", 0.0, 0.5)]))
    _, _, _ = stt.transcribe(
        "v.mp4",
        chain=[Link("mistral", "voxtral-mini-latest"), Link("groq", "m")],
        keys={"groq": "k"},           # no mistral key
        on_log=lambda *a: None, post=post,
    )
    assert len(post.sent) == 1
    assert "groq.com" in post.sent[0]["url"]


def test_the_chain_advances_past_a_failing_provider(one_chunk):
    post = fake_post(RuntimeError("groq exploded"), body([("x", 0.0, 0.5)]))
    _, _, _ = stt.transcribe(
        "v.mp4",
        chain=[Link("groq", "m"), Link("mistral", "voxtral-mini-latest")],
        keys={"groq": "k", "mistral": "k"},
        on_log=lambda *a: None, post=post,
    )
    assert len(post.sent) == 2


def test_every_provider_failing_raises_with_the_reasons(one_chunk):
    post = fake_post(RuntimeError("one"), RuntimeError("two"))
    with pytest.raises(stt.SttError) as info:
        stt.transcribe("v.mp4",
                       chain=[Link("groq", "m"), Link("mistral", "v")],
                       keys={"groq": "k", "mistral": "k"},
                       on_log=lambda *a: None, post=post)
    assert "groq/m" in str(info.value) and "mistral/v" in str(info.value)


# ----------------------------------------------------------- chain parsing

def test_local_is_accepted_as_a_pseudo_provider():
    chain = stt.parse_stt_chain("groq/whisper-large-v3-turbo,local/faster-whisper")
    assert [l.provider for l in chain] == ["groq", "local"]
    assert chain[1].model == "faster-whisper"


def test_a_bare_local_defaults_to_faster_whisper():
    assert stt.parse_stt_chain("local")[0].model == "faster-whisper"


def test_the_default_chain_is_hosted_first():
    chain = stt.parse_stt_chain(stt.DEFAULT_STT_CHAIN)
    assert chain[0].provider == "groq"

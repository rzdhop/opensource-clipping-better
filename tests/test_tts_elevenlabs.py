"""ElevenLabs TTS (plan 23 stage B3): a PAID link of ``TTS_CHAIN`` over the
``with-timestamps`` REST endpoint, gated like every paid link (allow_paid,
the budget), its character alignment grouped into words so the sidecar says
``tts_word_timestamps``. Everything is offline: a fake transport stands in
for the network, and one local server for the redirect case."""

import base64
import contextlib
import http.server
import json
import pathlib
import threading
from types import SimpleNamespace

import pytest

from clipping.providers import adapters, errors, gating, generation, pricing, tts
from clipping.providers.generation import GenRequest
from clipping.providers.registry import Link
from clipping.providers.transport import HttpStatusError, Response

FLASH = Link("elevenlabs", "flash")
MULTI = Link("elevenlabs", "multilingual-v2")
RACHEL = "21m00Tcm4TlvDq8ikWAM"
CREDS = {"ELEVENLABS_API_KEY": "test-xi-key"}
AUDIO = b"ID3fake-elevenlabs-mp3"


class FakeTransport:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        status, payload = self.answers.pop(0)
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)
        if isinstance(payload, str):
            payload = payload.encode()
        return Response(status, {}, payload)


def alignment(text, *, step=0.1, offset=0.0):
    """A per-character alignment of *text*, *step* seconds a character."""
    chars = list(text)
    return {"characters": chars,
            "character_start_times_seconds": [round(offset + i * step, 3) for i in range(len(chars))],
            "character_end_times_seconds": [round(offset + (i + 1) * step, 3) for i in range(len(chars))]}


def answer(text="Bonjour le monde", **overrides):
    body = {"audio_base64": base64.b64encode(AUDIO).decode(), "alignment": alignment(text),
            "normalized_alignment": alignment(text)}
    body.update(overrides)
    return (200, body)


def request(tmp_path, text="Bonjour le monde", **kwargs):
    kwargs.setdefault("voice", RACHEL)
    extra = dict(kwargs.pop("extra", {}), name="line_01")
    return GenRequest(kind="tts", text=text, out_dir=str(tmp_path), extra=extra, **kwargs)


def generate(link, req, transport, log=None, **kwargs):
    on_log = (lambda *_: None) if log is None else log.append
    return tts.ELEVENLABS_TTS.generate(link, req, credentials=CREDS, on_log=on_log, transport=transport, **kwargs)


# ------------------------------------------------------------ registration

def test_the_adapter_registers_for_tts_and_the_models_are_the_published_ids():
    assert generation.adapter_for("tts", "elevenlabs") is tts.ELEVENLABS_TTS
    assert generation.adapter_for("image", "elevenlabs") is None
    assert tts.ELEVENLABS_MODELS == {"flash": "eleven_flash_v2_5", "multilingual-v2": "eleven_multilingual_v2"}
    assert gating.api_model_id("tts", FLASH) == "eleven_flash_v2_5"
    assert gating.api_model_id("tts", MULTI) == "eleven_multilingual_v2"


def test_an_unknown_model_is_a_404_naming_the_table(tmp_path):
    with pytest.raises(HttpStatusError) as excinfo:
        generate(Link("elevenlabs", "v9"), request(tmp_path), FakeTransport([]))
    assert excinfo.value.status_code == 404 and "flash" in str(excinfo.value)


# ------------------------------------------------------------------ request

def test_the_request_is_a_post_to_with_timestamps_with_the_key_header_and_the_body(tmp_path):
    transport = FakeTransport([answer()])
    generate(FLASH, request(tmp_path, extra={"language": "fr-FR"}), transport)
    [call] = transport.calls
    assert call["method"] == "POST"
    assert call["url"] == (f"https://api.elevenlabs.io/v1/text-to-speech/{RACHEL}/with-timestamps"
                           "?output_format=mp3_44100_128")
    assert call["headers"]["xi-api-key"] == "test-xi-key"
    assert call["headers"]["Content-Type"] == "application/json"
    assert json.loads(call["body"]) == {"text": "Bonjour le monde", "model_id": "eleven_flash_v2_5",
                                        "language_code": "fr"}


def test_a_request_without_a_language_sends_none_and_multilingual_v2_never_does(tmp_path):
    flash, multi = FakeTransport([answer()]), FakeTransport([answer()])
    generate(FLASH, request(tmp_path), flash)
    generate(MULTI, request(tmp_path, extra={"language": "fr"}), multi)
    assert json.loads(flash.calls[0]["body"]) == {"text": "Bonjour le monde", "model_id": "eleven_flash_v2_5"}
    assert json.loads(multi.calls[0]["body"]) == {"text": "Bonjour le monde", "model_id": "eleven_multilingual_v2"}


def test_the_voice_id_is_one_path_segment(tmp_path):
    transport = FakeTransport([answer()])
    generate(FLASH, request(tmp_path, voice="../x?y=1"), transport)
    assert "/v1/text-to-speech/..%2Fx%3Fy%3D1/with-timestamps?output_format=" in transport.calls[0]["url"]


def test_a_request_without_a_voice_is_refused_before_anything_is_sent(tmp_path):
    transport = FakeTransport([])
    with pytest.raises(ValueError, match="voice id"):
        generate(FLASH, request(tmp_path, voice=""), transport)
    assert transport.calls == []


# ------------------------------------------------------- alignment -> words

def test_the_character_alignment_becomes_word_cues_and_the_sidecar_says_so(tmp_path):
    transport = FakeTransport([answer("Bonjour le monde")])
    result = generate(FLASH, request(tmp_path), transport, probe_duration=lambda path: 1.7)
    assert result.provider == "elevenlabs" and result.model == "flash"
    assert result.paths[0].endswith("line_01.mp3") and pathlib.Path(result.paths[0]).read_bytes() == AUDIO
    timing = json.loads(pathlib.Path(result.paths[1]).read_text(encoding="utf-8"))
    assert timing["$schema"] == "line_timing_v1" and timing["source"] == tts.SOURCE_WORDS
    assert timing["provider"] == "elevenlabs" and timing["voice"] == RACHEL
    assert timing["words"] == [{"word": "Bonjour", "start": 0.0, "end": 0.7},
                               {"word": "le", "start": 0.8, "end": 1.0},
                               {"word": "monde", "start": 1.1, "end": 1.6}]
    assert timing["duration_s"] == 1.7 and result.meta["duration_s"] == 1.7 and result.meta["words"] == 3


def test_the_duration_is_never_shorter_than_the_last_word(tmp_path):
    result = generate(FLASH, request(tmp_path), FakeTransport([answer("Bonjour le monde")]),
                      probe_duration=lambda path: None)
    assert result.meta["duration_s"] == 1.6  # no ffprobe: the last word's end


def test_punctuation_stays_in_its_word_and_runs_of_spaces_and_newlines_split_once():
    words = tts._eleven_words(alignment("Attends,  quoi ?\nOui"))
    assert [w["word"] for w in words] == ["Attends,", "quoi", "?", "Oui"]
    assert words[0]["start"] == 0.0 and words[0]["end"] == 0.8


@pytest.mark.parametrize("bad", [None, {}, {"characters": ["a"]}, {"characters": ["a", "b"],
                                  "character_start_times_seconds": [0.0],
                                  "character_end_times_seconds": [0.1, 0.2]}, "x"])
def test_an_alignment_that_is_missing_or_torn_gives_no_words(bad):
    assert tts._eleven_words(bad) == []


def test_without_an_alignment_the_audio_is_measured_and_the_warning_is_logged(tmp_path):
    log = []
    result = generate(FLASH, request(tmp_path), FakeTransport([answer(alignment=None, normalized_alignment=None)]),
                      log=log, probe_duration=lambda path: 2.5)
    timing = json.loads(pathlib.Path(result.paths[1]).read_text(encoding="utf-8"))
    assert timing["source"] == tts.SOURCE_DURATION and timing["duration_s"] == 2.5 and timing["words"] == []
    assert any("no character alignment" in line for line in log)


def test_the_normalized_alignment_stands_in_when_the_plain_one_is_absent(tmp_path):
    result = generate(FLASH, request(tmp_path), FakeTransport([answer(alignment=None)]),
                      probe_duration=lambda path: 1.7)
    assert result.meta["source"] == tts.SOURCE_WORDS and result.meta["words"] == 3


def test_an_answer_without_audio_or_with_broken_audio_is_a_provider_error(tmp_path):
    with pytest.raises(errors.ProviderError, match="no audio"):
        generate(FLASH, request(tmp_path), FakeTransport([(200, {"alignment": alignment("x")})]))
    with pytest.raises(errors.ProviderError, match="base64"):
        generate(FLASH, request(tmp_path), FakeTransport([(200, {"audio_base64": "###"})]))


# -------------------------------------------- rate / pitch / direction notes

def test_rate_pitch_and_direction_are_recorded_not_applied_and_said_so(tmp_path):
    log = []
    transport = FakeTransport([answer()])
    result = generate(FLASH, request(tmp_path, extra={"rate": "+10%", "pitch": "-4Hz", "direction": "whispering."}),
                      transport, log=log, probe_duration=lambda path: 1.7)
    body = json.loads(transport.calls[0]["body"])
    assert set(body) == {"text", "model_id"}, "a note never reaches the request"
    assert result.meta["not_applied"] == {"rate": "+10%", "pitch": "-4Hz", "direction": "whispering"}
    assert any("rate/pitch are not supported by elevenlabs/flash" in line for line in log)
    assert any("spoken direction is not supported by elevenlabs/flash" in line for line in log)


def test_a_plain_request_records_nothing_and_warns_of_nothing(tmp_path):
    log = []
    result = generate(FLASH, request(tmp_path), FakeTransport([answer()]), log=log, probe_duration=lambda p: 1.7)
    assert "not_applied" not in result.meta and log == []


def test_a_malformed_rate_is_refused_not_dropped(tmp_path):
    with pytest.raises(ValueError, match="rate"):
        generate(FLASH, request(tmp_path, extra={"rate": "fast"}), FakeTransport([]))


# ------------------------------------------------------------------- errors

@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_is_a_credential_error_that_names_the_key(status, tmp_path):
    transport = FakeTransport([(status, {"detail": {"status": "invalid_api_key", "message": "bad key"}})])
    with pytest.raises(HttpStatusError) as excinfo:
        generate(FLASH, request(tmp_path), transport)
    assert excinfo.value.status_code == status
    assert "ELEVENLABS_API_KEY was refused" in str(excinfo.value)
    assert errors.classify(excinfo.value) == errors.FATAL


def test_a_rate_limit_is_named_and_stays_a_429(tmp_path):
    transport = FakeTransport([(429, {"detail": {"status": "too_many_concurrent_requests", "message": "slow down"}})])
    with pytest.raises(HttpStatusError) as excinfo:
        generate(FLASH, request(tmp_path), transport)
    assert excinfo.value.status_code == 429 and "rate limiting" in str(excinfo.value)
    assert errors.classify(excinfo.value) == errors.RATE_LIMITED


def test_a_spent_quota_is_named_even_though_elevenlabs_answers_it_401(tmp_path):
    transport = FakeTransport([(401, {"detail": {"status": "quota_exceeded",
                                                 "message": "This request exceeds your quota of 10000"}})])
    with pytest.raises(HttpStatusError) as excinfo:
        generate(FLASH, request(tmp_path), transport)
    text = str(excinfo.value)
    assert "character quota" in text and "quota_exceeded" in text
    assert "was refused" not in text, "a spent quota is not a bad key"
    assert errors.classify(excinfo.value) == errors.FATAL


def test_another_failure_passes_through_unchanged(tmp_path):
    with pytest.raises(HttpStatusError) as excinfo:
        generate(FLASH, request(tmp_path), FakeTransport([(500, "boom")]))
    assert excinfo.value.status_code == 500 and "boom" in str(excinfo.value)
    assert "ElevenLabs" not in str(excinfo.value)


# ------------------------------------------------------- price and the gates

def test_the_estimate_is_per_character_from_the_price_table(tmp_path):
    thousand = "a" * 1000
    assert tts.ELEVENLABS_TTS.estimate(FLASH, request(tmp_path, text=thousand)).est_usd == 0.04
    assert tts.ELEVENLABS_TTS.estimate(MULTI, request(tmp_path, text=thousand)).est_usd == 0.08
    est = tts.ELEVENLABS_TTS.estimate(FLASH, request(tmp_path, text="é" * 250))
    assert est.qty == 250 and est.unit == "char" and est.paid is True and est.est_usd == 0.01


def test_both_links_are_paid_and_priced_and_the_flash_row_is_re_dated():
    for link in (FLASH, MULTI):
        assert generation.is_paid(link)
    assert pricing.price_for(FLASH).usd == 0.00004 and "2026-10-04" in pricing.price_for(FLASH).note
    assert pricing.price_for(MULTI).usd == 0.00008 and "2026-10-04" in pricing.price_for(MULTI).note
    assert "extension point" not in pricing.price_for(FLASH).note


def test_the_provider_needs_its_key_and_is_no_longer_an_extension_point():
    assert generation.missing_keys(FLASH, {}) == ["ELEVENLABS_API_KEY"]
    assert generation.missing_keys(FLASH, CREDS) == []
    provider = generation.GEN_PROVIDERS["elevenlabs"]
    assert provider.free_tier is False and provider.base_url == "https://api.elevenlabs.io"
    assert "Extension point" not in provider.notes


def test_it_is_the_last_link_of_the_default_chain_so_a_keyless_install_is_unchanged():
    links = generation.parse_generation_chain("tts", generation.DEFAULT_CHAINS["tts"])
    assert links[-1] == FLASH and [link.provider for link in links[:-1]] == [
        "edge", "gemini", "local", "local", "local"]


def run_chain(link, transport, *, allow_paid, env=None):
    adapters.load_all()
    return generation.run_generation_chain("tts", [link], request_for_chain(), env=CREDS if env is None else env,
                                           allow_paid=allow_paid, on_log=lambda *_: None, transport=transport)


def request_for_chain():
    import tempfile

    return GenRequest(kind="tts", text="Bonjour", voice=RACHEL, out_dir=tempfile.mkdtemp(prefix="eleven-test-"))


def test_no_call_is_made_while_allow_paid_is_off():
    transport = FakeTransport([answer()])
    with pytest.raises(generation.NoRunnableLink) as excinfo:
        run_chain(FLASH, transport, allow_paid=False)
    assert transport.calls == []
    assert "allow_paid is off" in str(excinfo.value)


def test_no_call_is_made_without_the_key_either():
    transport = FakeTransport([answer()])
    with pytest.raises(generation.NoRunnableLink) as excinfo:
        run_chain(FLASH, transport, allow_paid=True, env={})
    assert transport.calls == [] and "ELEVENLABS_API_KEY" in str(excinfo.value)


def test_with_allow_paid_on_the_chain_calls_once_and_the_result_is_priced():
    transport = FakeTransport([answer("Bonjour")])
    result, answered = run_chain(FLASH, transport, allow_paid=True)
    assert len(transport.calls) == 1 and answered == FLASH
    assert result.paid is True and result.est_cost == 0.0003  # 7 characters, rounded to 4 places like every estimate


def test_the_budget_refuses_a_paid_line_over_its_cap_before_any_call():
    from clipping.providers import budget as budget_mod

    transport = FakeTransport([answer()])
    adapters.load_all()
    tiny = budget_mod.Budget(True, 0.0001, 5.0, 5.0, "")

    def check(estimate, link):
        budget_mod.check(estimate, link, budget=tiny, day_spent=0.0, day_extra=0.0, ep_spent=0.0, story_spent=0.0)

    with pytest.raises(generation.NoRunnableLink):
        generation.run_generation_chain("tts", [FLASH], request_for_chain(), env=CREDS, allow_paid=True,
                                        on_log=lambda *_: None, budget_check=check, transport=transport)
    assert transport.calls == []


def test_link_summary_says_why_a_paid_voice_is_refused_while_allow_paid_is_off():
    merged = gating.merged_env(CREDS)
    budget_obj = gating.budget_of({"ALLOW_PAID": ""})
    summary = gating.link_summary("tts", FLASH, merged, budget_obj,
                                  GenRequest(kind="tts", text="a" * 1000, voice=RACHEL))
    assert summary["paid"] is True and summary["keyed"] is True and summary["allowed"] is False
    assert summary["est_usd"] == 0.04 and "allow_paid is off" in summary["reason"]


# ------------------------------------------------------------ the key's safety

@contextlib.contextmanager
def _server(location=None):
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - http.server's name
            seen.append({name.lower(): value for name, value in self.headers.items()})
            status, body = (302, b"") if self.path == "/start" else (200, b"ok")
            self.send_response(status)
            if status == 302:
                self.send_header("Location", location)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_address[1]}", seen=seen)
    finally:
        server.shutdown()
        server.server_close()


def test_the_xi_api_key_header_is_dropped_on_a_cross_origin_redirect():
    from clipping.providers import transport

    assert "xi-api-key" in transport.CREDENTIAL_HEADERS
    with _server() as other:
        with _server(location=f"{other.url}/end") as first:
            response = transport.urllib_transport("GET", f"{first.url}/start",
                                                  headers={"xi-api-key": "test-xi-key", "X-Trace": "kept"}, timeout=5)
    assert response.status == 200
    assert first.seen[0]["xi-api-key"] == "test-xi-key"
    assert "xi-api-key" not in other.seen[0] and other.seen[0]["x-trace"] == "kept"

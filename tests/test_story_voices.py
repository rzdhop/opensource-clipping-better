"""Voice proposal, pinning and sampling (AI Story phase 2, stage 3; spec 8.1, 11).

``clipping/aistory/voices.py`` must import no ``edge_tts`` (it is exercised in
the CI environment too); every scenario here is offline and hermetic, like
``tests/test_style_preview.py``: no key, chain or limit of the machine reaches
a test, nothing is written outside ``tmp_path``, and no real request leaves
the process (a ``urllib_transport`` stand-in fails any test that reaches it
without its own ``FakeTransport``/adapter double).

Also here: the RC-T2 guard (Contract A) proving the clip voice-over path is
byte-for-byte unchanged by the optional Edge rate/pitch kwargs.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import os
from pathlib import Path

import pytest

from clipping.aistory import schemas, voices
from clipping.aistory.store import StoryStore
from clipping.cancel import CancelToken
from clipping.providers import generation, tts
from clipping.providers.errors import ProviderError
from clipping.providers.generation import GenRequest, GenResult
from clipping.providers.registry import Link

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-26T10:00:00+00:00"

GEN_VARS = (
    "FAL_KEY", "OPENAI_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "POLLINATIONS_API_KEY",
    "IMAGE_CHAIN", "IMAGE_EDIT_CHAIN", "VIDEO_CHAIN", "TTS_CHAIN", "VISION_CHAIN",
    "LOCAL_COMFYUI_URL", "LOCAL_OLLAMA_URL",
    "ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD", "BUDGET_PROFILE",
    "LLM_CHAIN", "ALLOW_SLOW_CHAIN", "MAX_QUEUED_JOBS",
)
REAL_FILES = tuple(ROOT / "data" / name for name in ("usage.json", "spend.json", "chain_test_ledger.json"))
REAL_STORIES = (ROOT / "outputs" / "stories", ROOT / "outputs" / "stories.json")


def _fingerprint(path: Path):
    if path.is_symlink() or path.exists():
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
        return "present"
    return None


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    """No key, chain, cap or limit of the machine reaches a test; no request
    leaves the process; nothing is written outside ``tmp_path`` (the pattern
    of ``tests/test_style_preview.py``'s own fixture)."""
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env
    from clipping.providers import adapters, budget, limits, transport

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in GEN_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("LIMIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USAGE_PATH", str(tmp_path / "data" / "usage.json"))
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "data" / "spend.json"))
    limits.reset()
    budget.reset()
    adapters.load_all()

    def no_network(method, url, **_kwargs):
        raise AssertionError(f"a real request was attempted: {method} {url}")

    monkeypatch.setattr(transport, "urllib_transport", no_network)

    before = {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES}
    yield
    limits.reset()
    budget.reset()
    assert {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES} == before


@pytest.fixture
def store(tmp_path):
    return StoryStore(tmp_path / "outputs", on_log=lambda line: None)


# ------------------------------------------------------------------ helpers

def _char(char_id, *, role="lead", name=None, created_at=NOW, voice=None):
    """A minimal, schema-valid character_v1 document, undescribed (K1 not run
    yet) unless *voice* is given."""
    return {
        "$schema": schemas.CHARACTER_SCHEMA_NAME,
        "char_id": char_id,
        "name": name or char_id,
        "role": role,
        "archetype": "x",
        "one_line": "x",
        "descriptor": None,
        "signature_items": [],
        "personality": {"traits": [], "wants": None, "fears": None, "speech_style": None},
        "relationships": {},
        "voice": voice,
        "voice_hints": None,
        "refs": {"portrait": None, "turnaround": None, "expressions": None, "extra": [], "uploads": []},
        "ref_seed": None,
        "prompt_block": None,
        "state": {"alive": True, "location": None, "arc_notes": []},
        "source": "custom",
        "approved_at": None,
        "created_at": created_at,
        "updated_at": created_at,
    }


def _k1_voice(*, gender=None, age=None, style_tags=(), direction="a voice", sample_line="Salut !"):
    """K1's raw output shape (never persisted under this shape, see
    voices.py's module docstring): what propose()/alternates() read."""
    return {"gender": gender, "age": age, "style_tags": list(style_tags), "direction": direction,
            "sample_line": sample_line}


def _write_char(store, story_id, char_id, **kwargs):
    doc = _char(char_id, **kwargs)
    store.write_entity(story_id, "characters", doc, now=NOW, validator=schemas.character_errors)
    return doc


EDGE_VOICE = voices.Voice(provider="edge", voice_id="fr-FR-HenriNeural", lang="fr-FR", gender="m",
                          age="adult", style_tags=(), link=Link("edge", "fr-FR-HenriNeural"))


class FakeAdapter:
    """A free TTS adapter that writes *ext* bytes and never fails."""

    def __init__(self, ext="mp3", duration_s=2.5):
        self.ext = ext
        self.duration_s = duration_s
        self.calls = []

    def estimate(self, link, request):
        return None

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.calls.append(copy.copy(request))
        path = os.path.join(request.out_dir, f"sample.{self.ext}")
        with open(path, "wb") as fh:
            fh.write(b"ID3fake")
        return GenResult(provider=link.provider, model=link.model, paths=(path,), meta={"duration_s": self.duration_s})


class FailingAdapter:
    def estimate(self, link, request):
        return None

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        raise ProviderError(f"{link.provider}/{link.model}: synthetic failure")


class NeverCalledAdapter:
    """Registered on another provider; asserts the chain never reached it
    (voices.py builds a chain of exactly one link -- the pinned voice)."""

    def __init__(self):
        self.calls = 0

    def estimate(self, link, request):
        return None

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.calls += 1
        raise AssertionError("the fallback adapter must never be called")


# ------------------------------------------------------------------ catalogue

def test_catalogue_is_in_chain_order_and_language_filtered(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural")
    cat = voices.catalogue("fr", env={})
    assert [v.voice_id for v in cat] == [
        "fr-FR-HenriNeural", "fr-FR-DeniseNeural", "fr-FR-EloiseNeural",
        "fr-FR-RemyMultilingualNeural", "fr-FR-VivienneMultilingualNeural",
        "fr-CA-AntoineNeural", "fr-CA-SylvieNeural", "fr-CA-ThierryNeural",
    ]
    assert all(v.provider == "edge" for v in cat)


def test_catalogue_multi_voices_count_for_every_language(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural,gemini/flash-lite-tts")
    cat_en = voices.catalogue("en", env={"GOOGLE_API_KEY": "test-key"})
    gemini_ids = {v.voice_id for v in cat_en if v.provider == "gemini"}
    assert gemini_ids == {"Kore", "Puck", "Charon", "Fenrir", "Aoede", "Leda", "Orus", "Zephyr"}
    edge_en = {v.voice_id for v in cat_en if v.provider == "edge"}
    assert edge_en == {"en-US-GuyNeural", "en-US-JennyNeural", "en-US-AriaNeural", "en-US-AndrewNeural",
                       "en-US-AnaNeural", "en-US-ChristopherNeural", "en-US-MichelleNeural",
                       "en-GB-RyanNeural", "en-GB-SoniaNeural"}


def test_catalogue_skips_keyless_gemini_without_a_network_call(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural,gemini/flash-lite-tts")
    cat = voices.catalogue("fr", env={"GOOGLE_API_KEY": ""})
    assert all(v.provider != "gemini" for v in cat)
    assert any(v.provider == "edge" for v in cat)


def test_catalogue_skips_an_uninstalled_local_engine(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural,local/piper")
    monkeypatch.setattr(voices, "_installed", lambda name: False)
    cat = voices.catalogue("fr", env={})
    assert all(v.provider != "piper" for v in cat)


def test_catalogue_includes_an_installed_local_engine(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "local/piper")
    monkeypatch.setattr(voices, "_installed", lambda name: name == "piper")
    cat = voices.catalogue("fr", env={})
    assert {v.voice_id for v in cat} == {"fr_FR-tom-medium", "fr_FR-siwis-medium", "fr_FR-upmc-medium",
                                         "fr_FR-gilles-low"}
    assert all(v.link == Link("local", "piper") for v in cat)


def test_catalogue_malformed_chain_returns_empty_not_an_error(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "not-a-valid-chain")
    assert voices.catalogue("fr", env={}) == []


# -------------------------------------------------------------------- propose

def test_propose_gives_three_fr_characters_three_distinct_voices(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural")
    characters = [_char(f"char_{n}", role="lead", created_at=f"2026-09-26T10:00:0{n}+00:00") for n in range(3)]
    result = voices.propose(characters, "fr", env={})
    chosen = [result[c["char_id"]] for c in characters]
    assert all(v is not None for v in chosen)
    assert len({(v.provider, v.voice_id) for v in chosen}) == 3


def test_propose_honours_gender_when_possible(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural")
    characters = [
        _char("char_a", role="lead", created_at="2026-09-26T10:00:00+00:00",
              voice=_k1_voice(gender="male")),
        _char("char_b", role="lead", created_at="2026-09-26T10:00:01+00:00",
              voice=_k1_voice(gender="female")),
    ]
    result = voices.propose(characters, "fr", env={})
    assert result["char_a"].gender == "m"
    assert result["char_b"].gender == "f"


def test_propose_9_leads_8_fr_edge_voices_9th_gets_none(monkeypatch, capsys):
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural,gemini/flash-lite-tts")
    monkeypatch.setenv("GOOGLE_API_KEY", "")
    characters = [
        _char(f"char_lead{n:02d}", role="lead", name=f"Lead{n}", created_at=f"2026-09-26T10:00:{n:02d}+00:00")
        for n in range(9)
    ]
    result = voices.propose(characters, "fr", env={"GOOGLE_API_KEY": ""})
    voiced = [result[c["char_id"]] for c in characters[:8]]
    assert all(v is not None for v in voiced)
    assert len({(v.provider, v.voice_id) for v in voiced}) == 8
    assert result["char_lead08"] is None
    out = capsys.readouterr().out
    assert "Lead8" in out and "pick a voice" in out


def test_propose_guest_reuses_only_once_the_catalogue_is_exhausted(monkeypatch, capsys):
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural")
    leads = [_char(f"char_lead{n}", role="lead", created_at=f"2026-09-26T10:00:{n:02d}+00:00") for n in range(8)]
    guest = _char("char_guest", role="guest", created_at="2026-09-26T10:00:09+00:00", name="Guest")
    result = voices.propose(leads + [guest], "fr", env={})
    assert all(result[c["char_id"]] is not None for c in leads)
    assert result["char_guest"] is not None
    # The guest's voice is one already given to a lead: nothing was left unused.
    lead_voices = {(result[c["char_id"]].provider, result[c["char_id"]].voice_id) for c in leads}
    guest_voice = (result["char_guest"].provider, result["char_guest"].voice_id)
    assert guest_voice in lead_voices
    out = capsys.readouterr().out
    assert "Guest" in out and "reusing" in out


def test_propose_is_deterministic(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural")
    characters = [_char(f"char_{n}", role="lead", created_at=f"2026-09-26T10:00:0{n}+00:00") for n in range(4)]
    first = voices.propose(characters, "fr", env={})
    second = voices.propose(characters, "fr", env={})
    key = lambda result: {cid: (v.provider, v.voice_id) if v else None for cid, v in result.items()}
    assert key(first) == key(second)


# ------------------------------------------------------------------ alternates

def test_alternates_excludes_taken_voices(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural")
    character = _char("char_x", role="lead")
    taken = {("edge", "fr-FR-HenriNeural"), ("edge", "fr-FR-DeniseNeural")}
    alts = voices.alternates(character, "fr", env={}, taken=taken)
    assert len(alts) <= voices.ALTERNATES_LIMIT
    assert all((v.provider, v.voice_id) not in taken for v in alts)


def test_alternates_capped_at_six(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural")
    character = _char("char_x", role="lead")
    alts = voices.alternates(character, "fr", env={}, taken=set())
    assert len(alts) == voices.ALTERNATES_LIMIT


# ------------------------------------------------------------------------ pin

def test_pin_keeps_direction_and_sample_line_from_k1():
    character = _char("char_x", voice=_k1_voice(direction="warm and slow", sample_line="On y va."))
    block = voices.pin(character, EDGE_VOICE)
    assert block == {
        "provider": "edge", "voice_id": "fr-FR-HenriNeural", "rate": None, "pitch": None,
        "direction": "warm and slow", "sample_line": "On y va.",
    }


def test_pin_accepts_rate_and_pitch():
    character = _char("char_x", voice=_k1_voice())
    block = voices.pin(character, EDGE_VOICE, rate="+10%", pitch="-5Hz")
    assert block["rate"] == "+10%" and block["pitch"] == "-5Hz"


def test_pin_refuses_a_malformed_rate():
    character = _char("char_x", voice=_k1_voice())
    with pytest.raises(voices.VoiceError):
        voices.pin(character, EDGE_VOICE, rate="loud")


def test_pin_refuses_a_malformed_pitch():
    character = _char("char_x", voice=_k1_voice())
    with pytest.raises(voices.VoiceError):
        voices.pin(character, EDGE_VOICE, pitch="high")


# ------------------------------------------------------------ synthesize_sample

def test_synthesize_sample_happy_path(store):
    story_id = store.create(language="fr", seed_text="x", now=NOW)["story_id"]
    character = _write_char(store, story_id, "char_kiwi", name="Kiwi",
                            voice=voices.pin(_char("char_kiwi", voice=_k1_voice(sample_line="Salut !")),
                                             EDGE_VOICE))
    fake = FakeAdapter(ext="mp3", duration_s=1.95)
    result = voices.synthesize_sample(store, story_id, "char_kiwi", env={}, on_log=lambda l: None,
                                      cancel=CancelToken(), adapters={("tts", "edge"): fake})

    assert result == {"name": "voice_sample.mp3", "provider": "edge", "voice_id": "fr-FR-HenriNeural",
                      "duration_s": 1.95}
    assert len(fake.calls) == 1
    assert fake.calls[0].text == "Salut !"

    media_path = store.media_path(story_id, "characters", "char_kiwi", "voice_sample.mp3")
    assert os.path.exists(media_path)

    ledger_path = os.path.join(store.story_dir(story_id), "cost_ledger.json")
    import json
    entries = json.loads(open(ledger_path, encoding="utf-8").read())["entries"]
    assert len(entries) == 1
    assert entries[0]["step"] == "voice_sample" and entries[0]["unit"] == "char"
    assert entries[0]["qty"] == len("Salut !") and entries[0]["est_usd"] == 0.0 and entries[0]["paid"] is False


def test_synthesize_sample_replaces_a_stale_sample_of_the_other_extension(store):
    story_id = store.create(language="fr", seed_text="x", now=NOW)["story_id"]
    _write_char(store, story_id, "char_kiwi", name="Kiwi",
               voice=voices.pin(_char("char_kiwi", voice=_k1_voice()), EDGE_VOICE))
    wav_adapter = FakeAdapter(ext="wav")
    voices.synthesize_sample(store, story_id, "char_kiwi", env={}, on_log=lambda l: None,
                             cancel=CancelToken(), adapters={("tts", "edge"): wav_adapter})
    entity_dir = store.entity_dir(story_id, "characters", "char_kiwi")
    assert os.path.exists(os.path.join(entity_dir, "voice_sample.wav"))

    mp3_adapter = FakeAdapter(ext="mp3")
    voices.synthesize_sample(store, story_id, "char_kiwi", env={}, on_log=lambda l: None,
                             cancel=CancelToken(), adapters={("tts", "edge"): mp3_adapter})
    assert os.path.exists(os.path.join(entity_dir, "voice_sample.mp3"))
    assert not os.path.exists(os.path.join(entity_dir, "voice_sample.wav"))


def test_synthesize_sample_refuses_when_no_voice_is_pinned(store):
    story_id = store.create(language="fr", seed_text="x", now=NOW)["story_id"]
    _write_char(store, story_id, "char_kiwi", name="Kiwi")
    with pytest.raises(voices.VoiceError) as excinfo:
        voices.synthesize_sample(store, story_id, "char_kiwi", env={}, on_log=lambda l: None, cancel=CancelToken())
    assert "no pinned voice" in str(excinfo.value)
    assert len(excinfo.value.alternates) > 0


def test_synthesize_sample_refuses_an_empty_sample_line(store, monkeypatch):
    story_id = store.create(language="fr", seed_text="x", now=NOW)["story_id"]
    _write_char(store, story_id, "char_kiwi", name="Kiwi",
               voice=voices.pin(_char("char_kiwi", voice=_k1_voice(sample_line="Salut !")), EDGE_VOICE))

    # character_v1's sample_line is non-empty by schema (write AND read both
    # validate it), so an empty line is simulated the way a corrupted or
    # not-yet-written K1 field would surface it: read_entity still returns a
    # document, its sample_line blank.
    original_read_entity = store.read_entity

    def blank_sample_line(story_id_, kind, eid):
        doc = original_read_entity(story_id_, kind, eid)
        if kind == "characters" and eid == "char_kiwi":
            doc = copy.deepcopy(doc)
            doc["voice"]["sample_line"] = "   "
        return doc

    monkeypatch.setattr(store, "read_entity", blank_sample_line)

    with pytest.raises(voices.VoiceError) as excinfo:
        voices.synthesize_sample(store, story_id, "char_kiwi", env={}, on_log=lambda l: None, cancel=CancelToken())
    assert "no sample line" in str(excinfo.value)
    assert len(excinfo.value.alternates) > 0


def test_synthesize_sample_pinned_provider_failing_never_tries_the_fallback(store):
    story_id = store.create(language="fr", seed_text="x", now=NOW)["story_id"]
    _write_char(store, story_id, "char_kiwi", name="Kiwi",
               voice=voices.pin(_char("char_kiwi", voice=_k1_voice(sample_line="Salut !")), EDGE_VOICE))
    fallback = NeverCalledAdapter()

    with pytest.raises(voices.VoiceError) as excinfo:
        voices.synthesize_sample(store, story_id, "char_kiwi", env={}, on_log=lambda l: None, cancel=CancelToken(),
                                 adapters={("tts", "edge"): FailingAdapter(), ("tts", "gemini"): fallback})
    assert "synthetic failure" in str(excinfo.value)
    assert len(excinfo.value.alternates) > 0
    assert fallback.calls == 0


def test_synthesize_sample_never_shares_a_lead_or_supports_voice_in_its_alternates(store):
    story_id = store.create(language="fr", seed_text="x", now=NOW)["story_id"]
    _write_char(store, story_id, "char_other", role="lead", name="Other",
               voice=voices.pin(_char("char_other", voice=_k1_voice(sample_line="Coucou")), EDGE_VOICE))
    _write_char(store, story_id, "char_kiwi", role="lead", name="Kiwi")

    with pytest.raises(voices.VoiceError) as excinfo:
        voices.synthesize_sample(store, story_id, "char_kiwi", env={}, on_log=lambda l: None, cancel=CancelToken())
    assert all((v.provider, v.voice_id) != ("edge", "fr-FR-HenriNeural") for v in excinfo.value.alternates)


# --------------------------------------------------------- RC-T2 guard (A)

def test_rc_t2_edge_kwargs_unchanged_for_the_clip_path_and_forwarded_for_the_story_path(monkeypatch, tmp_path):
    """Contract A guard: the clip voice-over path's ``edge_tts.Communicate(...)``
    receives exactly ``(text, voice)`` plus the boundary kwarg it received
    before this stage; the story path (through ``tts.EdgeTtsAdapter``)
    receives ``rate``/``pitch`` when the character's pinned voice has them."""
    from clipping import voiceover

    seen = []

    class SubMaker:
        def __init__(self):
            self.cues = []

        def feed(self, chunk):
            pass

        def get_srt(self):
            return ""

    class Communicate:
        def __init__(self, text, voice, *, boundary="SentenceBoundary", rate=None, pitch=None):
            kwargs = {"boundary": boundary}
            if rate is not None:
                kwargs["rate"] = rate
            if pitch is not None:
                kwargs["pitch"] = pitch
            seen.append(((text, voice), kwargs))

        async def stream(self):
            yield {"type": "audio", "data": b"ID3"}

    fake_edge_tts = type("EdgeTts", (), {"Communicate": Communicate, "SubMaker": SubMaker})
    monkeypatch.setattr(voiceover, "edge_tts", fake_edge_tts)

    # Clip path: clipping.voiceover.synthesize_voice, unchanged, no rate/pitch ever.
    voiceover.synthesize_voice("Bonjour", "fr-FR-HenriNeural", str(tmp_path), "clip1")
    assert seen[-1] == (("Bonjour", "fr-FR-HenriNeural"), {"boundary": "WordBoundary"})

    # Story path: tts.EdgeTtsAdapter forwards rate/pitch read from request.extra.
    # _installed("edge_tts") is bypassed (like the existing "package missing"
    # test does the opposite way) so the real _edge_synthesize path is taken
    # without needing the real package -- voiceover.edge_tts is already faked.
    monkeypatch.setattr(tts, "_installed", lambda name: True)
    request = GenRequest(kind="tts", text="Salut", out_dir=str(tmp_path),
                         extra={"rate": "+10%", "pitch": "-5Hz", "name": "story1"})
    tts.EDGE.generate(Link("edge", "fr-FR-HenriNeural"), request, credentials={}, on_log=lambda *a: None)
    assert seen[-1] == (("Salut", "fr-FR-HenriNeural"), {"boundary": "WordBoundary", "rate": "+10%", "pitch": "-5Hz"})

    # And still unchanged with neither given.
    request2 = GenRequest(kind="tts", text="Salut", out_dir=str(tmp_path), extra={"name": "story2"})
    tts.EDGE.generate(Link("edge", "fr-FR-HenriNeural"), request2, credentials={}, on_log=lambda *a: None)
    assert seen[-1] == (("Salut", "fr-FR-HenriNeural"), {"boundary": "WordBoundary"})


def test_a_written_characters_voice_hints_drive_the_proposal_and_the_pin():
    # K1's brief is persisted as voice_hints (character_v1), so a voice can be
    # proposed again after the text was written and before one is pinned.
    from clipping.aistory import schemas
    character = {
        "char_id": "char_mangella", "name": "Mangella", "role": "lead", "created_at": "2026-09-26T00:00:00+00:00",
        "voice": None,
        "voice_hints": {"gender": "female", "age": "adult", "style_tags": ["authoritative"],
                        "direction": "proud, clipped", "sample_line": "Je gagnerai, avec ou sans vous."},
    }
    assert schemas.validate(character["voice_hints"], schemas.CHARACTER_SCHEMA["properties"]["voice_hints"]) == []
    env = {"TTS_CHAIN": "edge/fr-FR-DeniseNeural"}
    chosen = voices.propose([character], "fr", env=env)["char_mangella"]
    assert chosen is not None and chosen.gender in ("f", "female")
    pinned = voices.pin(character, chosen)
    assert pinned["direction"] == "proud, clipped"
    assert pinned["sample_line"] == "Je gagnerai, avec ou sans vous."


def test_an_elder_brief_prefers_an_adult_voice_over_a_young_one():
    # Found live (Tier-2, 2026-09-26): Broccolia, a female elder, got the
    # "young" fr-FR-EloiseNeural because no female voice was elder and the
    # age score was all-or-nothing -- catalogue order then picked the young
    # one. Age is a distance: adult is one step from elder, young two.
    elder = {"char_id": "char_b", "name": "B", "role": "recurring", "created_at": "2026-09-26T00:00:00+00:00",
             "voice_hints": {"gender": "female", "age": "elder", "style_tags": [], "direction": "", "sample_line": "x"}}
    env = {"TTS_CHAIN": "edge/fr-FR-DeniseNeural"}
    pool = voices.catalogue("fr", env=env)
    ranked = voices.alternates(elder, "fr", env=env, taken=())
    ages = {v.voice_id: v.age for v in pool}
    assert ages.get("fr-FR-EloiseNeural") == "young"
    assert ranked[0].gender in ("f", "female")
    assert ranked[0].age != "young"
    assert [v.voice_id for v in ranked].index("fr-FR-EloiseNeural") > 0


def test_the_catalogue_senior_counts_as_elder():
    senior_male = {"char_id": "char_s", "name": "S", "role": "lead", "created_at": "2026-09-26T00:00:00+00:00",
                   "voice_hints": {"gender": "male", "age": "elder", "style_tags": [], "direction": "", "sample_line": "x"}}
    env = {"TTS_CHAIN": "edge/fr-FR-HenriNeural"}
    ranked = voices.alternates(senior_male, "fr", env=env, taken=())
    assert ranked[0].voice_id == "fr-CA-ThierryNeural"


# ------------------------------------------------------ phase 7 stage 6c: v2 locale

def test_v2_catalogue_keeps_to_the_default_locale(monkeypatch):
    """E1 finding 10: a bare language prefix like "fr" matches both fr-FR
    and fr-CA -- a v2 story keeps to its one default locale instead
    (:data:`voices._V2_LOCALE`); legacy (v2=False, the default) is
    untouched."""
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural")
    legacy = voices.catalogue("fr", env={})
    assert any(v.lang == "fr-CA" for v in legacy)  # today's behaviour: both locales

    v2 = voices.catalogue("fr", env={}, v2=True)
    assert v2 and all(v.lang == "fr-FR" for v in v2)
    assert not any(v.lang == "fr-CA" for v in v2)


def test_v2_propose_gives_a_french_story_only_fr_fr_voices(monkeypatch):
    monkeypatch.setenv("TTS_CHAIN", "edge/fr-FR-HenriNeural")
    characters = [_char(f"char_{n}", role="lead", created_at=f"2026-09-26T10:00:0{n}+00:00") for n in range(3)]
    result = voices.propose(characters, "fr", env={}, v2=True)
    chosen = [result[c["char_id"]] for c in characters]
    assert all(v is not None and v.lang == "fr-FR" for v in chosen)


# --------------------------------------------------- phase 7 stage 6c: prosody

def test_base_prosody_reads_the_dossiers_keyword_map():
    assert voices.base_prosody(None) == (None, None)
    assert voices.base_prosody("") == (None, None)
    rate, pitch = voices.base_prosody("Speaks fast, with a deep, gravelly voice.")
    assert rate == "+12%"
    assert pitch == "-6Hz"
    rate, pitch = voices.base_prosody("A slow, high-pitched drawl.")
    assert rate == "-12%"
    assert pitch == "+6Hz"


def test_v2_line_prosody_legacy_request_identical(store, monkeypatch):
    """phase 7 stage 6c, BUILD item 4: ``prosody_for`` combines the voice's
    own base rate/pitch with a per-emotion delta, clamped to +-20%/+-8Hz;
    ``synthesize_line`` only uses it with a *line* AND *v2* both given --
    anything else (no line, or a legacy v2=False request) sends exactly the
    voice's own raw rate/pitch, today's request, byte for byte."""
    # Pure function: an angry line's delta on top of the voice's own base.
    voice = {"provider": "edge", "voice_id": "fr-FR-HenriNeural", "rate": "+2%", "pitch": "-1Hz"}
    line = {"line_id": "l1", "text": "Attention !", "emotion": "angry", "delivery": ""}
    rate, pitch = voices.prosody_for(voice, line)
    assert rate == "+8%"   # 2 + 6
    assert pitch == "+1Hz"  # -1 + 2

    # The clamp: an extreme base is never pushed past +-20% / +-8Hz by a delta.
    hot_voice = {"provider": "edge", "voice_id": "fr-FR-HenriNeural", "rate": "+18%", "pitch": "+7Hz"}
    rate, pitch = voices.prosody_for(hot_voice, {"emotion": "angry"})
    assert rate == "+20%"
    assert pitch == "+8Hz"

    # No emotion delta at all (e.g. "neutral"): the voice's own base, unchanged.
    rate, pitch = voices.prosody_for(voice, {"emotion": "neutral"})
    assert rate == "+2%"
    assert pitch == "-1Hz"

    # synthesize_line: capture the request's extra instead of calling a
    # provider (generation.run_generation_chain faked to refuse at once).
    story_id = store.create(language="fr", seed_text="x", now=NOW)["story_id"]
    gates = voices.LineGates(store, story_id, env={})
    captured = []

    def fake_chain(kind, chain, request, **kwargs):
        captured.append(copy.copy(request))
        raise generation.NoRunnableLink("no link reached", failures=[])

    monkeypatch.setattr(voices.generation, "run_generation_chain", fake_chain)

    def dest_for(ext):
        return str(Path(store.story_dir(story_id)) / f"line.{ext}")

    def speak(**extra):
        with pytest.raises(voices.VoiceError):
            voices.synthesize_line(gates, voice=voice, text=line["text"], dest_for=dest_for,
                                   on_log=lambda l: None, cancel=CancelToken(), **extra)
        return captured[-1].extra

    # Legacy (neither line nor v2): the voice's own raw rate/pitch -- today's request.
    assert speak() == {"rate": "+2%", "pitch": "-1Hz"}
    # A line given but the story not v2: still untouched.
    assert speak(line=line, v2=False) == {"rate": "+2%", "pitch": "-1Hz"}
    # v2 but no line: still untouched (nothing to read an emotion off).
    assert speak(v2=True) == {"rate": "+2%", "pitch": "-1Hz"}
    # Both given: prosody_for's own combined rate/pitch.
    assert speak(line=line, v2=True) == {"rate": "+8%", "pitch": "+1Hz"}

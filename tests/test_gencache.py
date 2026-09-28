"""The generation cache and submit journal (DEC-151..154), piece by piece:
the request key, the conservative booking rule, the entry on disk and the
ledger's optional note. How the chain runner uses them is
``test_gencache_runner.py``. Stdlib + pytest only (DEC-012).
"""

import hashlib
import json
import os

import pytest

from clipping.aistory.ledger import CostLedger
from clipping.providers import errors, gencache, transport
from clipping.providers.generation import GenRequest
from clipping.providers.registry import Link

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
FAL = Link("fal", "flux-schnell")
EDGE = Link("edge", "fr-FR-HenriNeural")
FIELDS = {"v", "kind", "link", "prompt", "negative", "refs", "seed", "width", "height", "text", "voice",
          "rate", "pitch", "template", "take"}


def image(**kw):
    kw.setdefault("prompt", "an anthropomorphic kiwi in a linen shirt")
    kw.setdefault("seed", 7)
    return GenRequest(kind="image", **kw)


def edit(*refs):
    return GenRequest(kind="image_edit", prompt="the kiwi, turning", seed=3, references=tuple(map(str, refs)))


def line(text="Bonjour.", **extra):
    return GenRequest(kind="tts", text=text, voice="fr-FR-HenriNeural", extra=extra)


class Books(list):
    def __call__(self, entry):
        self.append(entry)


# ------------------------------------------------------------------ the key

def test_the_same_request_gives_the_same_key_wherever_it_is_written():
    a = gencache.request_key("image", FAL, image(out_dir="/tmp/a", extra={"name": "shot_01"}))
    b = gencache.request_key("image", FAL, image(out_dir="/tmp/b", extra={"name": "shot_09"}))
    assert a == b
    assert len(a) == 64 and set(a) <= set("0123456789abcdef")


@pytest.mark.parametrize("change", [
    {"prompt": "a mango"}, {"negative": "blurry"}, {"seed": 8}, {"width": 720}, {"height": 1280},
    {"extra": {"take": 2}}, {"extra": {"template": "t2i_other"}},
], ids=["prompt", "negative", "seed", "width", "height", "take", "template"])
def test_every_field_that_changes_the_answer_changes_the_key(change):
    assert gencache.request_key("image", FAL, image(**change)) != gencache.request_key("image", FAL, image())


def test_the_link_is_in_the_key_so_a_swap_target_is_another_request():
    key = gencache.request_key("image", FAL, image())
    assert gencache.request_key("image", Link("openai", "gpt-image-2-low"), image()) != key
    assert gencache.request_key("image", "fal/flux-schnell", image()) == key


def test_references_are_keyed_by_their_bytes_not_their_paths(tmp_path):
    one, two = tmp_path / "a.png", tmp_path / "b.png"
    one.write_bytes(PNG)
    two.write_bytes(PNG)
    key = gencache.request_key("image_edit", FAL, edit(one))
    assert gencache.request_key("image_edit", FAL, edit(two)) == key
    assert gencache.request_key("image_edit", FAL, edit(one, one)) != key
    two.write_bytes(PNG + b"retouched")
    assert gencache.request_key("image_edit", FAL, edit(two)) != key


def test_a_seedless_image_request_has_no_key_so_nothing_is_cached_or_journaled(tmp_path):
    assert gencache.request_key("image", FAL, image(seed=None)) is None
    assert gencache.request_key("image_edit", FAL, GenRequest(kind="image_edit", prompt="p")) is None
    cache = gencache.GenCache(tmp_path / "gen", book=Books())
    assert cache.journal("image", FAL, image(seed=None), paid=True) is None


def test_a_voice_line_is_keyed_without_a_seed_and_a_new_take_misses_on_purpose():
    key = gencache.request_key("tts", EDGE, line(rate="+5%"))
    assert key is not None
    assert gencache.request_key("tts", EDGE, line(rate="+5%", name="line_01")) == key
    assert gencache.request_key("tts", EDGE, line(rate="+10%")) != key
    assert gencache.request_key("tts", EDGE, line(rate="+5%", pitch="+2Hz")) != key
    assert gencache.request_key("tts", EDGE, line(rate="+5%", take=2)) != key
    assert gencache.request_key("tts", EDGE, line("Salut.", rate="+5%")) != key


def test_the_key_is_the_sha256_of_the_canonical_payload():
    payload = gencache.key_payload("image", FAL, image())
    assert set(payload) == FIELDS
    assert (payload["v"], payload["kind"], payload["link"], payload["refs"], payload["seed"]) == (
        1, "image", "fal/flux-schnell", [], 7)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert gencache.request_key("image", FAL, image()) == hashlib.sha256(canonical).hexdigest()


@pytest.mark.parametrize("kind", ["video", "vision"])
def test_a_kind_the_key_cannot_describe_is_never_cached(kind):
    # The key has no field for a clip's duration or the frames to describe.
    assert gencache.request_key(kind, FAL, GenRequest(kind=kind, prompt="p", seed=1)) is None


# ---------------------------------------------------------- the booking rule

@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422, 429])
def test_a_refusal_the_provider_answered_proves_the_request_unbilled(status):
    billed, note = gencache.billing_verdict(transport.HttpStatusError(status, "https://api.example"), sent=True)
    assert billed is False and f"HTTP {status}" in note


@pytest.mark.parametrize("exc,words", [
    (transport.HttpStatusError(500, "https://api.example"), "HTTP 500"),
    (transport.HttpStatusError(503, "https://api.example"), "HTTP 503"),
    (transport.HttpStatusError(410, "https://api.example"), "HTTP 410"),
    (transport.APITimeoutError("POST https://api.example timed out after 120s"), "timeout"),
    (transport.APIConnectionError("POST https://api.example: connection reset"), "connection"),
    (errors.ProviderError("gemini/nano-banana-2: the answer carried no image"), "no usable answer"),
], ids=["500", "503", "410", "timeout", "reset", "no-output"])
def test_anything_else_after_sending_is_booked_with_a_note(exc, words):
    billed, note = gencache.billing_verdict(exc, sent=True)
    assert billed is True and words in note


def test_only_a_failure_known_to_precede_the_transport_is_unbilled():
    exc = ValueError("fal/seedream-4-edit needs at least one reference image")
    assert gencache.billing_verdict(exc, sent=False)[0] is False
    # An adapter that does not speak through the injected transport cannot prove it sent nothing.
    assert gencache.billing_verdict(exc, sent=None)[0] is True


# ------------------------------------------------------------ the entry on disk

def test_an_entry_is_written_atomically_before_the_call_and_books_nothing_yet(tmp_path):
    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    journal = cache.journal("image", FAL, image(), paid=True, on_log=lambda *_: None)
    journal.begin(0.0062)
    path = tmp_path / "gen" / f"{journal.key}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["$schema"] == "gen_journal_v1"
    assert {"key", "kind", "link", "paid", "est_usd", "state", "request", "output", "seed", "meta", "booked",
            "attempts"} <= set(data)
    assert (data["key"], data["kind"], data["link"], data["paid"], data["est_usd"], data["state"], data["seed"]) == (
        journal.key, "image", "fal/flux-schnell", True, 0.0062, "sending", 7)
    assert data["request"] is None and data["output"] is None and data["booked"] is None
    assert [a["event"] for a in data["attempts"]] == ["sending"]
    assert sorted(os.listdir(tmp_path / "gen")) == [path.name]  # no temp file outlives the write
    assert path.stat().st_mode & 0o777 == 0o644
    assert books == []
    assert cache.lookup(journal.key) == data


def test_an_unreadable_entry_stops_rather_than_starting_afresh(tmp_path):
    cache = gencache.GenCache(tmp_path, book=Books())
    key = cache.key("image", FAL, image())
    (tmp_path / f"{key}.json").write_text("{torn", encoding="utf-8")
    with pytest.raises(gencache.JournalError) as excinfo:
        cache.lookup(key)
    assert key in str(excinfo.value)
    (tmp_path / f"{key}.json").write_text(json.dumps({"$schema": "something_else"}), encoding="utf-8")
    with pytest.raises(gencache.JournalError):
        cache.lookup(key)


def test_a_symlinked_entry_is_never_followed(tmp_path):
    cache = gencache.GenCache(tmp_path / "gen", book=Books())
    journal = cache.journal("image", FAL, image(), paid=True, on_log=lambda *_: None)
    journal.begin(0.0062)
    real = tmp_path / "gen" / f"{journal.key}.json"
    moved = tmp_path / "elsewhere.json"
    real.rename(moved)
    real.symlink_to(moved)
    with pytest.raises(gencache.JournalError) as excinfo:
        cache.lookup(journal.key)
    assert "symlink" in str(excinfo.value)


def test_a_key_that_is_not_a_digest_is_refused(tmp_path):
    cache = gencache.GenCache(tmp_path, book=Books())
    with pytest.raises(ValueError):
        cache.lookup("../../story.json")


def test_a_missing_entry_is_simply_absent(tmp_path):
    cache = gencache.GenCache(tmp_path / "not-yet", book=Books())
    assert cache.lookup(cache.key("image", FAL, image())) is None
    assert not (tmp_path / "not-yet").exists()


def test_every_cache_on_one_root_shares_one_lock(tmp_path):
    a = gencache.GenCache(tmp_path / "gen", book=Books())
    b = gencache.GenCache(str(tmp_path / "gen"), book=Books())
    c = gencache.GenCache(tmp_path / "other", book=Books())
    assert a.lock is b.lock and a.lock is not c.lock


def test_the_booking_is_the_callers_and_must_be_given(tmp_path):
    with pytest.raises(TypeError):
        gencache.GenCache(tmp_path, book=None)


# ----------------------------------------------------------- the ledger note

def test_a_ledger_row_carries_a_note_only_when_one_is_given(tmp_path):
    ledger = CostLedger(str(tmp_path / "cost_ledger.json"), time_fn=lambda: 0)
    plain = ledger.append(step="assets", provider="fal", model="fal-ai/flux/schnell", unit="image", qty=1,
                          est_usd=0.0062, paid=True, ep=1)
    noted = ledger.append(step="assets", provider="fal", model="fal-ai/flux/schnell", unit="image", qty=1,
                          est_usd=0.0062, paid=True, ep=1, note="HTTP 500 after sending: may be billed")
    assert "note" not in plain
    assert noted["note"] == "HTTP 500 after sending: may be billed"
    assert ["note" in row for row in ledger.entries()] == [False, True]

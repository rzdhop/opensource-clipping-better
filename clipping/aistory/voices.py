"""Voice proposal, pinning and sampling for a story's cast (spec 8.1, 11).

Three things live here, kept together because they share one catalogue and
one scoring rule:

* :func:`catalogue` -- every voice ``TTS_CHAIN`` can reach for a language,
  right now, without a network call;
* :func:`propose` / :func:`alternates` / :func:`pin` -- picking one distinct
  voice per lead/support character (spec 11: "no two lead characters share a
  voice") and turning a choice into the character's persisted ``voice``
  block;
* :func:`synthesize_sample` -- a ~3s sample of the pinned voice, through a
  single-link chain built from that voice alone (spec 8.1: never another
  provider, never another voice -- a failure is reported with alternatives,
  not silently routed around).

**Where a character's voice preference lives.** ``schemas.CHARACTER_SCHEMA``'s
``voice`` field is a *closed* schema (``provider, voice_id, rate, pitch,
direction, sample_line``): once a character has a pinned voice, that is all
the persisted document ever holds -- there is nowhere in ``character.json``
for K1's raw ``gender``/``age``/``style_tags`` preference to live once pinned,
and ``store.write_entity``/``read_entity`` validate strictly on every write
and every read, so a document cannot hold anything else under ``voice``
either. ``propose()`` and ``alternates()`` therefore do not read
``character.json`` documents directly: their ``characters``/``character``
arguments are plain dicts the caller (the K1 step runner, phase 2 stage 6)
assembles for the duration of one proposal -- ``char_id``, ``role``,
``created_at`` from the entity, plus a ``voice`` dict carrying K1's live
output (``gender``, ``age``, ``style_tags``, ``direction``, ``sample_line``)
that is never itself written to disk. ``pin()`` is what turns that into the
persisted shape, keeping only ``direction``/``sample_line`` from it.

**Provider naming.** A :class:`Voice` (and a pinned voice's ``provider``) uses
the ``voices.json`` catalogue key: ``"edge"``, ``"gemini"``, or one of the
local engine names ``"piper"``/``"kokoro"``/``"chatterbox"`` -- never the bare
generation provider ``"local"``, which does not say which engine. The chain
``Link`` to call (``edge/<voice_id>``, ``gemini/flash-lite-tts``,
``local/piper``, ...) is derived from that name (:func:`_chain_link`) exactly
the same way at proposal time and at synthesis time, so a pin survives
round-tripping through ``character.json`` with only ``provider``/``voice_id``.

Stdlib only (DEC-012): the provider modules this reaches (``generation``,
``gating``, ``tts``, ``budget``) are stdlib at import, per their own doc
comments.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import tempfile

from clipping.providers import adapters as adapters_mod
from clipping.providers import budget as budget_mod
from clipping.providers import generation, gating, tts
from clipping.providers.registry import ChainError, Link, describe

from . import ledger as ledger_mod
from . import schemas

STEP = "voice_sample"
LEDGER_NAME = "cost_ledger.json"
VOICE_SAMPLE_STEM = "voice_sample"
KEPT_EXTENSIONS = ("mp3", "wav")
ALTERNATES_LIMIT = 6

_RATE_RE = re.compile(schemas.VOICE_RATE_PATTERN)
_PITCH_RE = re.compile(schemas.VOICE_PITCH_PATTERN)

# K1's closed gender vocabulary (schemas.VOICE_GENDERS) -> the catalogue's
# ("m"/"f"/"any", spec 11). "any" on either side always matches: a catalogue
# voice marked "any" (e.g. chatterbox's zero-shot clone) suits every
# preference, and a "neutral" preference is satisfied only by such a voice.
_GENDER_TO_CATALOGUE = {"female": "f", "male": "m", "neutral": "any"}


class VoiceError(Exception):
    """A voice operation refused, before anything past it was tried.

    ``alternates`` is a fresh list of :class:`Voice` to offer instead of
    whatever failed -- empty when none apply (e.g. the ledger itself could
    not be read).
    """

    def __init__(self, message: str, *, alternates=()):
        super().__init__(message)
        self.alternates = list(alternates)


class Voice:
    """One catalogued voice. Immutable; compares and hashes by its fields."""

    __slots__ = ("provider", "voice_id", "lang", "gender", "age", "style_tags", "link")

    def __init__(self, *, provider, voice_id, lang, gender, age, style_tags, link):
        self.provider = provider
        self.voice_id = voice_id
        self.lang = lang
        self.gender = gender
        self.age = age
        self.style_tags = tuple(style_tags)
        self.link = link

    def _key(self):
        return (self.provider, self.voice_id, self.lang, self.gender, self.age, self.style_tags, self.link)

    def __eq__(self, other):
        return isinstance(other, Voice) and self._key() == other._key()

    def __hash__(self):
        return hash(self._key())

    def __repr__(self):
        return f"Voice(provider={self.provider!r}, voice_id={self.voice_id!r}, link={describe(self.link)})"


# ------------------------------------------------------------- catalogue

def _installed(name: str) -> bool:
    """Whether the package *name* can be imported, without importing it.

    Duplicated from ``tts._installed`` (a private helper of a different
    package) rather than called across the module boundary; the check itself
    is a one-line ``importlib`` lookup with no state to drift.
    """
    return importlib.util.find_spec(name) is not None


def _chain_link(provider: str, voice_id: str) -> Link:
    """The ``generation`` chain link that speaks *provider*'s *voice_id*.

    Edge is one voice per request, so the voice IS the model
    (``edge/<voice_id>``). Gemini's speech model is fixed (only one is
    catalogued, spec 8.1) and the voice travels as ``GenRequest.voice``
    instead. A local engine's provider is always ``"local"``; *provider*
    (``piper``/``kokoro``/``chatterbox``) becomes the model, and the voice
    again travels as ``GenRequest.voice``.
    """
    if provider == "edge":
        return Link("edge", voice_id)
    if provider == "gemini":
        model = next(iter(tts.GEMINI_TTS_MODELS))
        return Link("gemini", model)
    if provider in tts.LOCAL_ENGINES:
        return Link("local", provider)
    raise VoiceError(f"{provider!r} is not a TTS provider this module knows how to build a chain link for.")


def catalogue(language, *, env) -> list:
    """Voices ``TTS_CHAIN`` can reach for *language*, in chain order.

    Every entry of ``voices.json`` for each link's provider, kept only when
    that engine can run here right now WITHOUT a network call: Gemini needs
    ``GOOGLE_API_KEY``, a local engine needs its package installed
    (``tts.LOCAL_ENGINES``), Edge needs neither (it ships as a base
    dependency, never an extra). Filtered to *language* (``"fr"`` matches
    ``"fr-FR"``/``"fr-CA"``; a ``"multi"`` voice -- Gemini's prebuilt voices,
    Chatterbox's zero-shot clone -- counts for every language).

    *env* is the Settings overrides as saved (not yet merged with the process
    environment); this function merges them itself (``gating.merged_env``),
    so a caller only ever hands over what it wants to override.
    """
    merged = gating.merged_env(env)
    try:
        chain = generation.chain_from_env(generation.TTS, merged)
    except ChainError:
        return []

    wanted = (language or "").strip().lower()
    voices, seen = [], set()
    for link in chain:
        if link.provider == "edge":
            key = "edge"
        elif link.provider == "gemini":
            key = "gemini"
            if generation.missing_keys(link, merged):
                continue
        elif link.provider == "local":
            key = link.model
            package = tts.LOCAL_ENGINES.get(link.model)
            if package is None or not _installed(package):
                continue
        else:
            continue  # not a TTS provider this catalogue knows (gcloud/openai/elevenlabs: no table yet)
        if key in seen:
            continue
        seen.add(key)

        entries = tts.load_voices().get("providers", {}).get(key, [])
        for entry in entries:
            entry_lang = str(entry.get("lang", "")).lower()
            if not (entry_lang == "multi" or entry_lang.startswith(wanted)):
                continue
            voice_id = entry["voice_id"]
            voices.append(Voice(
                provider=key, voice_id=voice_id, lang=entry.get("lang", ""),
                gender=entry.get("gender", ""), age=entry.get("age", ""),
                style_tags=entry.get("style_tags") or (), link=_chain_link(key, voice_id),
            ))
    return voices


# --------------------------------------------------------------- scoring

def _score(prefer: dict, voice: Voice, index: int) -> tuple:
    """``(gender_match, age_match, style_overlap, -index)``: higher is
    better, catalogue order the last tie-break (spec 11)."""
    prefer = prefer or {}
    pref_gender = _GENDER_TO_CATALOGUE.get(prefer.get("gender"))
    gender = 1 if pref_gender and (voice.gender == pref_gender or voice.gender == "any") else 0
    # Age is a distance, not a match: with no elder voice left, an adult one
    # (one step away) beats a young one (two). Found live when a matriarch
    # got a youthful voice because catalogue order broke an all-zero tie.
    pref_rank = _AGE_RANK.get(_AGE_ALIAS.get(prefer.get("age"), prefer.get("age")))
    voice_rank = _AGE_RANK.get(_AGE_ALIAS.get(voice.age, voice.age))
    age = -abs(pref_rank - voice_rank) if pref_rank is not None and voice_rank is not None else -len(_AGE_RANK)
    style = len(set(prefer.get("style_tags") or ()) & set(voice.style_tags))
    return (gender, age, style, -index)


# K1's ages and the catalogue's, on one scale (the catalogue says "senior").
_AGE_RANK = {"child": 0, "young": 1, "adult": 2, "elder": 3}
_AGE_ALIAS = {"senior": "elder", "teen": "young", "kid": "child"}


def _best_of(prefer: dict, pool: list):
    if not pool:
        return None
    ranked = sorted(enumerate(pool), key=lambda pair: _score(prefer, pair[1], pair[0]), reverse=True)
    return ranked[0][1]


def _ranked(prefer: dict, pool: list) -> list:
    return [voice for _, voice in
            sorted(enumerate(pool), key=lambda pair: _score(prefer, pair[1], pair[0]), reverse=True)]


# ------------------------------------------------------------- proposal

_ROLE_ORDER = {role: index for index, role in enumerate(schemas.CHARACTER_ROLES)}


def _voice_key(voice: Voice) -> tuple:
    return (voice.provider, voice.voice_id)


def _preference(character) -> dict:
    """K1's voice brief: the persisted ``voice_hints`` (character_v1), or a
    live dict under ``voice`` from a caller that has not written it yet."""
    return character.get("voice_hints") or character.get("voice") or {}


def propose(characters, language, *, env, taken=(), on_log=print) -> dict:
    """One distinct voice per lead/support character; ``{char_id: Voice|None}``.

    *characters* is ordered leads -> support -> recurring -> guest, then
    ``created_at`` (then ``char_id``, for a total order when both tie --
    ``store.list_entities``'s own rule). Each gets the best-scoring **unused**
    voice of :func:`catalogue`; a lead/support left with none gets ``None``
    (printed) rather than a shared voice. A recurring/guest character may
    reuse a voice already given to someone else, but only once no unused
    voice is left (also printed) -- and doing so never removes that voice
    from what a later character could still get fresh. Deterministic: the
    catalogue, the scoring and the character order are all deterministic.

    *taken* is ``{(provider, voice_id)}`` already pinned by characters left
    out of *characters* (the cast step proposes only for the unpinned ones):
    those voices are never proposed fresh. *on_log* prints the two lines
    (``print`` by default; a step hands its own log).
    """
    pool = catalogue(language, env=env)
    ordered = sorted(
        characters,
        key=lambda c: (_ROLE_ORDER.get(c.get("role"), len(_ROLE_ORDER)), c.get("created_at") or "",
                       c.get("char_id") or ""),
    )
    taken = {tuple(key) for key in (taken or ())}
    result = {}
    for character in ordered:
        char_id = character["char_id"]
        name = character.get("name") or char_id
        role = character.get("role")
        prefer = _preference(character)
        unused = [voice for voice in pool if _voice_key(voice) not in taken]
        choice = _best_of(prefer, unused)
        if choice is not None:
            taken.add(_voice_key(choice))
        elif role not in schemas.CAST_APPROVAL_ROLES and pool:
            choice = _best_of(prefer, pool)
            on_log(f"🔁 {name}: no unused voice is left; reusing {choice.provider}/{choice.voice_id}.")
        if choice is None:
            on_log(f"🔇 pick a voice for {name}: no catalogue voice is available for this language/chain.")
        result[char_id] = choice
    return result


def alternates(character, language, *, env, taken) -> list:
    """Up to :data:`ALTERNATES_LIMIT` other voices for *character*, best
    first, excluding every voice in *taken* (the other lead/support
    characters' pinned voices). Same scoring as :func:`propose`."""
    excluded = set(taken or ())
    pool = [voice for voice in catalogue(language, env=env) if _voice_key(voice) not in excluded]
    prefer = _preference(character)
    return _ranked(prefer, pool)[:ALTERNATES_LIMIT]


def pin(character, voice: Voice, *, rate=None, pitch=None) -> dict:
    """The character's new ``voice`` block (``schemas.CHARACTER_SCHEMA``'s
    closed shape): *voice*'s ``provider``/``voice_id``, *rate*/*pitch* as
    given, and ``direction``/``sample_line`` kept from the character's
    current ``voice`` (K1's output) -- never regenerated here. The caller
    writes the result back with ``store.write_entity``.

    ``VoiceError`` for a *rate*/*pitch* that does not match the character_v1
    pattern: caught here, at the one place a human chooses these strings,
    rather than surfacing later as a generic schema-validation failure.
    """
    if rate is not None and not _RATE_RE.fullmatch(rate):
        raise VoiceError(f"rate {rate!r} does not match {schemas.VOICE_RATE_PATTERN} (e.g. '+10%', '-5%').")
    if pitch is not None and not _PITCH_RE.fullmatch(pitch):
        raise VoiceError(f"pitch {pitch!r} does not match {schemas.VOICE_PITCH_PATTERN} (e.g. '+5Hz', '-10Hz').")
    current = character.get("voice") or {}
    hints = character.get("voice_hints") or {}
    return {
        "provider": voice.provider,
        "voice_id": voice.voice_id,
        "rate": rate,
        "pitch": pitch,
        "direction": current.get("direction") or hints.get("direction") or "",
        "sample_line": current.get("sample_line") or hints.get("sample_line") or "",
    }


# ---------------------------------------------------------------- sample

def _open_ledger(stories, story_id) -> ledger_mod.CostLedger:
    """The story's ledger, checked before anything is spent (the pattern of
    ``steps.style_preview._open_ledger``, duplicated: a step-local helper of
    a sibling module, not something to import across that boundary)."""
    path = os.path.join(stories.story_dir(story_id), LEDGER_NAME)
    if os.path.islink(path):
        raise VoiceError(f"{LEDGER_NAME} is a symlink, which is never followed, so this story's "
                         "spending cannot be checked; replace it with the file itself.")
    if os.path.lexists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if (not isinstance(data, dict) or data.get("$schema") != ledger_mod.SCHEMA
                    or not isinstance(data.get("entries"), list)):
                raise ValueError(f"not a {ledger_mod.SCHEMA} document")
            sum(float(entry["est_usd"]) for entry in data["entries"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise VoiceError(f"{LEDGER_NAME} cannot be read ({type(exc).__name__}: {exc}), so this "
                             "story's spending cannot be checked; fix it before making a voice sample.") from None
    return ledger_mod.CostLedger(path)


def _book(ledger, result, answered, *, qty) -> float:
    paid = bool(result.paid)
    est = float(result.est_cost) if paid else 0.0
    ledger.append(step=STEP, provider=answered.provider, model=gating.api_model_id(generation.TTS, answered),
                  unit="char", qty=qty, est_usd=est, paid=paid)
    if result.paid and result.est_cost > 0:
        budget_mod.record(result.est_cost)
    return round(est, 4)


def _taken_by_others(stories, story_id, exclude_char_id) -> set:
    """``{(provider, voice_id)}`` pinned by every other lead/support character
    of the story: what a fresh pin, or a failed sample's alternates, must not
    repeat. Best-effort: an unreadable cast blocks nothing here (the write
    path is what enforces validity)."""
    taken = set()
    try:
        characters = stories.list_entities(story_id, "characters")
    except KeyError:
        return taken
    for other in characters:
        if other["char_id"] == exclude_char_id or other.get("role") not in schemas.CAST_APPROVAL_ROLES:
            continue
        voice = other.get("voice") or {}
        if voice.get("provider") and voice.get("voice_id"):
            taken.add((voice["provider"], voice["voice_id"]))
    return taken


def _remove_stale_sample(stories, story_id, char_id, *, keep_ext) -> None:
    """A new sample changed extension (mp3 <-> wav): the old file is not
    overwritten by ``write_media`` (different name), so it is removed here."""
    other_ext = "wav" if keep_ext == "mp3" else "mp3"
    try:
        entity_dir = stories.entity_dir(story_id, "characters", char_id)
    except KeyError:
        return
    stale = os.path.join(entity_dir, f"{VOICE_SAMPLE_STEM}.{other_ext}")
    if os.path.lexists(stale) and not os.path.isdir(stale):
        try:
            os.remove(stale)
        except OSError:
            pass


def synthesize_sample(stories, story_id, char_id, *, env, on_log, cancel, adapters=None, transport=None) -> dict:
    """A sample of the character's pinned voice speaking its ``sample_line``.

    Builds a single-link chain from the pinned voice ALONE (spec 8.1: never
    another provider, never another voice) and runs it through the story's
    ledger, budget and free-tier gates -- the same wiring
    ``steps.style_preview`` uses. Books the call (``step="voice_sample"``,
    unit ``"char"``, qty = the sample line's length) and keeps the result as
    ``voice_sample.mp3``/``.wav`` (``store.write_media``), removing a stale
    sample of the other extension.

    Returns ``{"name", "provider", "voice_id", "duration_s"?}``.

    ``VoiceError`` -- carrying :func:`alternates` for the character -- when
    no voice is pinned yet, its sample line is empty, or the pinned link
    could not answer; nothing else is tried in any of these cases.
    """
    cancel.check()
    story = stories.get(story_id)
    character = stories.read_entity(story_id, "characters", char_id)
    name = character.get("name") or char_id
    voice = character.get("voice") or {}
    provider, voice_id = voice.get("provider"), voice.get("voice_id")
    sample_line = (voice.get("sample_line") or "").strip()

    def alts():
        taken = _taken_by_others(stories, story_id, char_id)
        return alternates(character, story["language"], env=env, taken=taken)

    if not provider or not voice_id:
        raise VoiceError(f"{name} has no pinned voice yet; propose and pin one first.", alternates=alts())
    if not sample_line:
        raise VoiceError(
            f"{name}'s voice has no sample line to speak; write one (K1), then pin the voice again.",
            alternates=alts(),
        )

    link = _chain_link(provider, voice_id)
    request = generation.GenRequest(
        kind=generation.TTS, text=sample_line, voice=voice_id,
        extra={"rate": voice.get("rate"), "pitch": voice.get("pitch")},
    )

    if adapters is None:
        adapters_mod.load_all()

    merged = gating.merged_env(env)
    try:
        budget_obj = gating.budget_of(merged)
    except ValueError as exc:
        raise VoiceError(f"The budget settings cannot be used: {exc}") from None

    ledger = _open_ledger(stories, story_id)
    check = gating.budget_check(budget_obj, story_spent=lambda: ledger.totals()["est_usd"])
    limiter = gating.FreeTierLimiter()

    with tempfile.TemporaryDirectory(prefix="voice-sample-") as scratch:
        request.out_dir = scratch
        try:
            result, answered = generation.run_generation_chain(
                generation.TTS, [link], request, env=merged, allow_paid=budget_obj.allow_paid,
                on_log=on_log, budget_check=check, limiter=limiter, adapters=adapters,
                transport=transport, cancel=cancel,
            )
        except generation.NoRunnableLink as exc:
            reason = exc.failures[0][1] if exc.failures else str(exc)
            raise VoiceError(f"{name}: {describe(link)} could not make the sample ({reason}).",
                             alternates=alts()) from exc

        audio_path = next(
            (p for p in result.paths if os.path.splitext(str(p))[1].lstrip(".").lower() in KEPT_EXTENSIONS), None,
        )
        if audio_path is None:
            raise VoiceError(f"{name}: {describe(answered)} answered without an audio file.", alternates=alts())

        _book(ledger, result, answered, qty=len(sample_line))
        ext = os.path.splitext(str(audio_path))[1].lstrip(".").lower()
        media_name = f"{VOICE_SAMPLE_STEM}.{ext}"
        stories.write_media(story_id, "characters", char_id, media_name, str(audio_path))

    _remove_stale_sample(stories, story_id, char_id, keep_ext=ext)

    on_log(f"🔊 {name}: voice sample via {describe(answered)}")
    payload = {"name": media_name, "provider": answered.provider, "voice_id": voice_id}
    duration = (result.meta or {}).get("duration_s")
    if duration is not None:
        payload["duration_s"] = duration
    return payload

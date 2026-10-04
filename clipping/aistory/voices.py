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
  not silently routed around);
* :func:`synthesize_line` -- one script line spoken the same way (DEC-122),
  for the script step's opt-in voice measurement (spec 6.4 source (a)): the
  audio and its ``line_timing_v1`` sidecar are kept where the caller says,
  and the call is booked once, the moment the provider answered.

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
``gating``, ``tts``, ``budget``, ``pricing``, ``limits``) are stdlib at
import, per their own doc comments.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import tempfile

from clipping.providers import adapters as adapters_mod
from clipping.providers import budget as budget_mod
from clipping.providers import generation, gating, limits, pricing, tts
from clipping.providers.registry import ChainError, Link, describe

from . import ledger as ledger_mod
from . import schemas

STEP = "voice_sample"
MEASURE_STEP = "voice_measure"
LEDGER_NAME = "cost_ledger.json"
VOICE_SAMPLE_STEM = "voice_sample"
KEPT_EXTENSIONS = ("mp3", "wav")
ALTERNATES_LIMIT = 6

# What a line's ``timing.source`` says once it was measured (spec 6.4): the
# engine's own word timestamps, or the length of the audio it wrote.
MEASURED_SOURCES = (tts.SOURCE_WORDS, tts.SOURCE_DURATION)

# How much longer a provider's voices speak a line than ``timing.estimate_line``
# says (DEC-250): the French rate was measured on Edge voices; Gemini's
# prebuilt voices ran 1.16-1.80x the estimate over the 18 lines of story
# d0ee5ebd745d's first episode (2026-10-03; mean 1.35, speech alone, pauses
# aside -- A-134). A provider not named speaks at the estimate (1.0). What the
# storyboard step adds to a scene's estimated length before deciding whether
# one clip can cover it (``storyboard.expected_scene_seconds``); a measured
# line needs none.
SPEECH_OVERRUN = {"gemini": 1.35}


def speech_overrun(voice) -> float:
    """The :data:`SPEECH_OVERRUN` of a character's *voice* (its ``voice``
    document, or None): 1.0 for a provider not named, or no voice."""
    provider = (voice or {}).get("provider") if isinstance(voice, dict) else None
    return float(SPEECH_OVERRUN.get(provider, 1.0))
_FILE_MODE = 0o644

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

    __slots__ = ("provider", "voice_id", "lang", "gender", "age", "style_tags", "link", "paid")

    def __init__(self, *, provider, voice_id, lang, gender, age, style_tags, link, paid=False):
        self.provider = provider
        self.voice_id = voice_id
        self.lang = lang
        self.gender = gender
        self.age = age
        self.style_tags = tuple(style_tags)
        self.link = link
        # A voice whose link bills per character (``voices.json``'s ``paid``):
        # offered to the human with its price, never proposed on its own.
        self.paid = bool(paid)

    def _key(self):
        return (self.provider, self.voice_id, self.lang, self.gender, self.age, self.style_tags, self.link,
                self.paid)

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
    if provider == "elevenlabs":
        # One model per link, the voice id as ``GenRequest.voice``: Flash, the
        # cheaper one, is the link a voice speaks through (plan 23 stage B3).
        model = next(iter(tts.ELEVENLABS_MODELS))
        return Link("elevenlabs", model)
    if provider in tts.LOCAL_ENGINES:
        return Link("local", provider)
    raise VoiceError(f"{provider!r} is not a TTS provider this module knows how to build a chain link for.")


# A v2 story's default locale per language (phase 7 stage 6c, A17): a bare
# language prefix like "fr" matches both "fr-FR" and "fr-CA" (E1 finding 10
# -- story B's mayor got an fr-CA voice in an fr-FR story), so a v2 request
# keeps to one locale. ``schemas.LANGUAGES`` names only "fr"/"en" today; a
# language with no entry here falls back to the bare prefix, matching the
# legacy behaviour. Legacy (v2=False) is untouched -- byte-identical.
_V2_LOCALE = {"fr": "fr-FR", "en": "en-US"}


def _wanted_lang(language, v2) -> str:
    language = (language or "").strip().lower()
    if v2:
        return _V2_LOCALE.get(language, language).lower()
    return language


def catalogue(language, *, env, v2=False) -> list:
    """Voices ``TTS_CHAIN`` can reach for *language*, in chain order.

    Every entry of ``voices.json`` for each link's provider, kept only when
    that engine can run here right now WITHOUT a network call: Gemini needs
    ``GOOGLE_API_KEY``, ElevenLabs needs ``ELEVENLABS_API_KEY`` (its voices
    are ``paid``: :func:`propose` never picks one), a local engine needs its
    package installed (``tts.LOCAL_ENGINES``), Edge needs neither (it ships as a base
    dependency, never an extra). Filtered to *language* (``"fr"`` matches
    ``"fr-FR"``/``"fr-CA"``; a ``"multi"`` voice -- Gemini's prebuilt voices,
    Chatterbox's zero-shot clone -- counts for every language). With *v2*
    True, kept to the story's default locale instead (:data:`_V2_LOCALE`):
    ``"fr"`` then matches ``"fr-FR"`` only, never ``"fr-CA"``.

    *env* is the Settings overrides as saved (not yet merged with the process
    environment); this function merges them itself (``gating.merged_env``),
    so a caller only ever hands over what it wants to override.
    """
    merged = gating.merged_env(env)
    try:
        chain = generation.chain_from_env(generation.TTS, merged)
    except ChainError:
        return []

    wanted = _wanted_lang(language, v2)
    voices, seen = [], set()
    for link in chain:
        if link.provider == "edge":
            key = "edge"
        elif link.provider == "gemini":
            key = "gemini"
            if generation.missing_keys(link, merged):
                continue
        elif link.provider == "elevenlabs":
            key = "elevenlabs"
            if generation.missing_keys(link, merged):
                continue
        elif link.provider == "local":
            key = link.model
            package = tts.LOCAL_ENGINES.get(link.model)
            if package is None or not _installed(package):
                continue
        else:
            continue  # not a TTS provider this catalogue knows (gcloud/openai: no table yet)
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
                paid=bool(entry.get("paid")),
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


def propose(characters, language, *, env, taken=(), on_log=print, v2=False) -> dict:
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
    those voices are never proposed fresh. A ``paid`` voice (ElevenLabs's) is
    never proposed at all: it can only be chosen, through :func:`alternates`. *on_log* prints the two lines
    (``print`` by default; a step hands its own log). *v2* keeps the pool to
    the story's default locale (:func:`catalogue`); legacy (default) is
    unchanged.
    """
    # A paid voice is the human's choice (the alternates list shows its price),
    # never an automatic one: the proposal's pool holds free voices only.
    pool = [voice for voice in catalogue(language, env=env, v2=v2) if not voice.paid]
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


def alternates(character, language, *, env, taken, v2=False) -> list:
    """Up to :data:`ALTERNATES_LIMIT` other voices for *character*, best
    first, excluding every voice in *taken* (the other lead/support
    characters' pinned voices). Same scoring as :func:`propose`. *v2* keeps
    the pool to the story's default locale (:func:`catalogue`); legacy
    (default) is unchanged."""
    excluded = set(taken or ())
    pool = [voice for voice in catalogue(language, env=env, v2=v2) if _voice_key(voice) not in excluded]
    prefer = _preference(character)
    return _ranked(prefer, pool)[:ALTERNATES_LIMIT]


# A character's BASE rate/pitch, read off a v2 dossier's own free-text
# ``voice.patterns`` (D1's prose, schemas.CHARACTER_DOSSIER_SCHEMA) at cast
# time (phase 7 stage 6c, BUILD item 4): the first keyword of either table
# found in the text, case-insensitive, wins that axis -- a small map, not an
# LLM call, so pinning stays deterministic and free. A legacy character has
# no dossier at all (:func:`base_prosody` then returns ``(None, None)``,
# exactly today's always-None rate/pitch).
_PATTERN_RATE_KEYWORDS = (
    ("fast", "+12%"), ("quick", "+12%"), ("rapid", "+12%"), ("hurried", "+10%"),
    ("slow", "-12%"), ("deliberate", "-12%"), ("measured", "-8%"), ("unhurried", "-8%"),
)
_PATTERN_PITCH_KEYWORDS = (
    ("deep", "-6Hz"), ("low", "-6Hz"), ("gravelly", "-4Hz"), ("baritone", "-6Hz"),
    ("high", "+6Hz"), ("shrill", "+6Hz"), ("squeaky", "+6Hz"), ("piping", "+6Hz"),
)


def base_prosody(patterns) -> tuple:
    """``(rate, pitch)`` -- each one of the keyword tables' own values or
    ``None`` -- read off a dossier's ``voice.patterns`` text (*patterns*,
    falsy for a legacy character or one with no dossier yet). Pure,
    deterministic: the caller (the cast step's voice pin, part 4) pins the
    result once; it is never re-derived per line."""
    text = str(patterns or "").lower()

    def _first(table):
        for keyword, value in table:
            # Word-boundary, not a bare substring: "slow" must never match
            # "low" (pitch) off the "slow"/"low" (rate) overlap in the text.
            if re.search(rf"\b{re.escape(keyword)}\b", text):
                return value
        return None

    return _first(_PATTERN_RATE_KEYWORDS), _first(_PATTERN_PITCH_KEYWORDS)


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


def _book(ledger, result, answered, *, qty, step=STEP, ep=None) -> float:
    paid = bool(result.paid)
    est = float(result.est_cost) if paid else 0.0
    ledger.append(step=step, provider=answered.provider, model=gating.api_model_id(generation.TTS, answered),
                  unit="char", qty=qty, est_usd=est, paid=paid, ep=ep)
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
    if link.provider == "elevenlabs":
        request.extra["language"] = story["language"]

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


# ----------------------------------------------------------------- lines

def voice_label(voice):
    """``"<provider>/<voice_id>"`` of a pinned voice block -- what a measured
    line's ``timing.voice`` records -- or None when *voice* pins nothing."""
    voice = voice or {}
    provider, voice_id = voice.get("provider"), voice.get("voice_id")
    if not provider or not voice_id:
        return None
    return f"{provider}/{voice_id}"


def _spoken_by(voice, link) -> str:
    """How a failure names the voice: its label, and the chain link behind it
    when that says something else (``gemini/Kore (gemini/flash-lite-tts)``)."""
    label = voice_label(voice)
    return label if label == describe(link) else f"{label} ({describe(link)})"


class LineGates:
    """What every :func:`synthesize_line` of one run shares, opened once
    before the first: the Settings over the process environment, the budget
    they describe, the story's ledger (checked readable first: its totals
    feed the caps, and a torn file would be started afresh by the first
    booking), the free-tier limiter, and :meth:`check` -- the budget verdict
    the runner asks before a paid call, with the story's and (with *ep*) the
    episode's spending so far read at each check, so a line booked a moment
    ago counts against the next one.

    ``VoiceError`` when the budget settings or the ledger cannot be used:
    nothing can be spent safely, so nothing is tried.
    """

    def __init__(self, stories, story_id, *, env, ep=None):
        self.merged = gating.merged_env(env)
        try:
            self.budget = gating.budget_of(self.merged)
        except ValueError as exc:
            raise VoiceError(f"The budget settings cannot be used: {exc}") from None
        self.ledger = _open_ledger(stories, story_id)
        self.ep = ep
        self.limiter = gating.FreeTierLimiter()

    def spent(self, ep=None) -> float:
        return float(self.ledger.totals(ep)["est_usd"])

    def check(self, estimate, link) -> None:
        ep_spent = self.spent(self.ep) if self.ep is not None else 0.0
        state = budget_mod.day_state()
        budget_mod.check(estimate, link, budget=self.budget, day_spent=state.spent, day_extra=state.extra,
                         ep_spent=ep_spent, story_spent=self.spent())

    def booker(self, kind, *, step, unit, qty):
        """``book(entry)`` for a generation cache (``gencache.GenCache``,
        DEC-151): the journal books each request through it, once, on this
        ledger -- the one :meth:`check` reads, so what was booked a moment
        ago counts against the next call's caps. One row (*step*, the gates'
        episode, *unit*, *qty*, the entry's estimate when it is paid, its
        ``note`` passed through: absent unless the journal gave one), and
        today's spend for a paid one (``budget.record``)."""

        def book(entry) -> None:
            provider, _, model = str(entry["link"]).partition("/")
            paid = bool(entry.get("paid"))
            est = float(entry.get("est_usd") or 0.0) if paid else 0.0
            self.ledger.append(step=step, provider=provider,
                               model=gating.api_model_id(kind, Link(provider, model)), unit=unit, qty=qty,
                               est_usd=est, paid=paid, ep=self.ep, note=entry.get("note"))
            if paid and est > 0:
                budget_mod.record(est)

        return book

    def releaser(self, kind, *, step, unit, qty):
        """``release(entry)`` for a generation cache: gives back what
        :meth:`booker` booked for a request proven never run (the journal's
        ``void``) -- one row on this ledger mirroring the booking (negative
        ``est_usd`` and ``qty``, ``void`` saying why), and the booked day's
        spend less it (``budget.release``, never below zero) -- so this
        episode's, this story's and the day's spending drop back for the
        caps."""

        def release(entry) -> None:
            provider, _, model = str(entry["link"]).partition("/")
            paid = bool(entry.get("paid"))
            est = float(entry.get("est_usd") or 0.0) if paid else 0.0
            back = -qty if isinstance(qty, (int, float)) else qty
            reason = entry.get("note") or "proven never run by the provider: unbilled"
            self.ledger.append(step=step, provider=provider,
                               model=gating.api_model_id(kind, Link(provider, model)), unit=unit, qty=back,
                               est_usd=-est, paid=paid, ep=self.ep, void=reason)
            if paid and est > 0:
                booked_at = str((entry.get("booked") or {}).get("at") or "")
                budget_mod.release(est, day=booked_at[:10] or None)

        return release


def _atomic_copy(src, dest) -> None:
    """Copy *src* to *dest* so a reader sees the old file or the new one (a
    temp file in *dest*'s own directory, fsync, 0644, ``os.replace``; the
    temp file never outlives a failure). ``store._atomic_copy``'s pattern,
    duplicated: a private helper of another module."""
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(dest), prefix=".voice-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out, open(src, "rb") as source:
            shutil.copyfileobj(source, out)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp, _FILE_MODE)
        os.replace(tmp, dest)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _line_outputs(result, spoken):
    """``(audio path, its extension, the sidecar's path, the sidecar)`` of
    an answer; ``VoiceError`` naming what is missing or unusable -- a line
    with no measured duration is never given one (spec 6.4: no silent
    fallback, never an estimate labelled measured)."""
    audio = sidecar = None
    for path in result.paths:
        ext = os.path.splitext(str(path))[1].lstrip(".").lower()
        if ext in KEPT_EXTENSIONS and audio is None:
            audio = (str(path), ext)
        elif ext == "json" and sidecar is None:
            sidecar = str(path)
    if audio is None:
        raise VoiceError(f"{spoken} answered without an audio file.")
    if sidecar is None:
        raise VoiceError(f"{spoken} answered without its {tts.TIMING_SCHEMA} timing file.")
    try:
        with open(sidecar, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise VoiceError(f"{spoken}: its timing file cannot be read ({type(exc).__name__}: {exc}).") from None
    if not isinstance(data, dict) or data.get("$schema") != tts.TIMING_SCHEMA:
        raise VoiceError(f"{spoken}: its timing file is not a {tts.TIMING_SCHEMA} document.")
    if data.get("source") not in MEASURED_SOURCES:
        raise VoiceError(f"{spoken}: its timing file names no known source ({data.get('source')!r}).")
    duration = data.get("duration_s")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not duration > 0:
        raise VoiceError(f"{spoken} answered, but no duration could be measured ({duration!r}: no word "
                         "timestamps and no audio length -- is ffprobe installed?).")
    return audio[0], audio[1], sidecar, data


# Per-emotion delta onto a line's voice (phase 7 stage 6c, BUILD item 4):
# (rate_pct, pitch_hz), additive onto the voice's own base. Every other
# ``schemas.EMOTIONS`` value (neutral, happy, scheming, tension, triumph)
# gets no delta -- the voice's own base carries the whole line.
_EMOTION_DELTA = {
    "angry": (6, 2),
    "sad": (-8, -3),
    "fear": (8, 3),
    "shocked": (5, 4),
    "tender": (-5, -1),
}

# tts._rate_pitch's own clamp (phase 7 stage 6c): no combination of a
# character's base and a line's emotion delta ever leaves this range.
_RATE_CLAMP_PCT = 20
_PITCH_CLAMP_HZ = 8


def _pct(value) -> float:
    return float(str(value).rstrip("%")) if value else 0.0


def _hz(value) -> float:
    return float(str(value).rstrip("Hz")) if value else 0.0


def prosody_for(voice: dict, line: dict) -> tuple:
    """``(rate, pitch)`` for *voice* speaking *line* -- each a
    ``tts._rate_pitch``-shaped string (``"+10%"``/``"-5Hz"``) or ``None``
    when the combined value is exactly 0, same as *voice* carrying no
    rate/pitch at all. Pure: no I/O, no clock, the same *voice*/*line*
    inputs always the same answer (phase 7 stage 6c, BUILD item 4).

    The base is *voice*'s own ``rate``/``pitch`` (:func:`base_prosody`,
    pinned once at cast time): a slow, deep voice stays slow and deep on
    every line. *line*'s own ``emotion`` adds a fixed delta on top
    (:data:`_EMOTION_DELTA`), the sum clamped to
    :data:`_RATE_CLAMP_PCT`/:data:`_PITCH_CLAMP_HZ` so a strong base and a
    strong emotion together never leave the range ``tts._rate_pitch``
    accepts.
    """
    rate_pct = _pct(voice.get("rate"))
    pitch_hz = _hz(voice.get("pitch"))
    d_rate, d_pitch = _EMOTION_DELTA.get(line.get("emotion"), (0, 0))
    rate_pct = max(-_RATE_CLAMP_PCT, min(_RATE_CLAMP_PCT, rate_pct + d_rate))
    pitch_hz = max(-_PITCH_CLAMP_HZ, min(_PITCH_CLAMP_HZ, pitch_hz + d_pitch))
    rate = f"{rate_pct:+.0f}%" if rate_pct else None
    pitch = f"{pitch_hz:+.0f}Hz" if pitch_hz else None
    return rate, pitch


def synthesize_line(gates, *, voice, text, dest_for, on_log, cancel, step=MEASURE_STEP, adapters=None,
                    transport=None, cache=None, take=None, direction=None, line=None, v2=False,
                    language=None) -> dict:
    """*text* spoken by the pinned *voice* (a character's ``voice`` block,
    or the narrator's) through a single-link chain built from that voice
    ALONE (DEC-122: never another provider, never another voice, never
    silently), under *gates* (:class:`LineGates`): ``allow_paid`` and the
    budget verdict before a paid link, the free-tier limiter before a free
    one -- the runner's own gates, in its order; a paid link gets one
    attempt (DEC-106).

    Booked once, the moment the provider answered (``_book``: *step*, the
    gates' episode, unit ``"char"``, qty ``len(text)``, the link's price) --
    ``run_generation_chain`` books nothing -- and only then: a refusal or a
    failure costs nothing and books nothing. An answer this cannot use (no
    audio, no sidecar, no duration) is still booked: the call was made.

    The audio is kept at ``dest_for(ext)`` (``"mp3"`` or ``"wav"``, as the
    engine wrote it) and its ``line_timing_v1`` sidecar at
    ``dest_for("json")``, each copied atomically. Returns ``{"ext",
    "duration_s", "source", "voice": "<provider>/<voice_id>", "link",
    "paid", "est_usd", "cached", "tail_guard"}`` (``cached``: a kept answer
    of *cache*, no call made; ``tail_guard``: the Gemini tail guard's report,
    None for another engine). A Gemini answer *cache* kept from before the
    tail guard is cleaned before it is kept (``tts.guard_kept_take``), so a
    cache hit never brings its static back.

    ``VoiceError`` for a voice that pins nothing or a provider no chain link
    speaks for, a chain that could not run its one link (the refusal of a
    paid link while ``allow_paid`` is off carries the estimate and the
    day's spending), or an answer that cannot be kept. ``Cancelled`` passes
    through.

    *cache* (phase 4, the assets step and its regenerates) is a
    ``gencache.GenCache`` the request goes through: a kept answer is served
    without a call, and the journal books every answer through the cache's
    ``book`` (:meth:`LineGates.booker`), so an answer carrying
    ``meta["booked"]`` is not booked again here. *take* joins the request
    (``extra["take"]``, a field of the cache's key) so a voice regenerate
    misses the cache on purpose. Both None: exactly the call of phase 3
    (RC-A2, RC-A6). ``gencache.JournalError`` passes through: a request
    the provider may hold could not be journaled or booked.

    *direction* (phase 5 stage 7: a voice regenerate's note) joins the
    request as ``extra["direction"]``, how the take should be spoken: an
    engine that can follow one speaks it (Gemini), the others say it was
    recorded, not applied (``providers.tts``). It is not a field of the
    cache's key: a regenerate asks it with its own take, and asks that take
    again only with the same note.

    *line* and *v2* (phase 7 stage 6c): with both given -- *line* the
    script line's own dict (its ``emotion`` read) and *v2* the story's
    pipeline flag -- the rate/pitch sent is :func:`prosody_for`'s, not
    *voice*'s own raw ``rate``/``pitch``. Either missing (the default), the
    request is exactly what it always was: *voice*'s own ``rate``/``pitch``,
    unchanged by this parameter pair.

    *language* (plan 23 stage B3: the story's, ``fr``) joins the request as
    ``extra["language"]`` for an ElevenLabs link only -- the one engine that
    is told the language; every other request is exactly what it was.
    """
    label = voice_label(voice)
    if label is None:
        raise VoiceError("no voice is pinned.")
    link = _chain_link(voice["provider"], voice["voice_id"])
    spoken = _spoken_by(voice, link)
    if line is not None and v2:
        rate, pitch = prosody_for(voice, line)
    else:
        rate, pitch = voice.get("rate"), voice.get("pitch")
    extra = {"rate": rate, "pitch": pitch}
    if take is not None:
        extra["take"] = take
    if direction:
        extra["direction"] = direction
    if language and link.provider == "elevenlabs":
        extra["language"] = language
    request = generation.GenRequest(kind=generation.TTS, text=text, voice=voice["voice_id"], extra=extra)
    if adapters is None:
        adapters_mod.load_all()
    journaled = {} if cache is None else {"cache": cache}

    with tempfile.TemporaryDirectory(prefix="voice-line-") as scratch:
        request.out_dir = scratch
        try:
            result, answered = generation.run_generation_chain(
                generation.TTS, [link], request, env=gates.merged, allow_paid=gates.budget.allow_paid,
                on_log=on_log, budget_check=gates.check, limiter=gates.limiter, adapters=adapters,
                transport=transport, cancel=cancel, **journaled,
            )
        except generation.NoRunnableLink as exc:
            reason = exc.failures[0][1] if exc.failures else str(exc)
            if generation.is_paid(link) and not gates.budget.allow_paid:
                # The runner's first gate says only "allow_paid is off"; the
                # same verdict with its numbers, computed without a call.
                summary = gating.link_summary(generation.TTS, link, gates.merged, gates.budget, request,
                                              story_spent=gates.spent(), adapters=adapters)
                reason = summary["reason"] or reason
            raise VoiceError(f"{spoken} could not speak it ({reason}).") from exc
        except pricing.PriceUnknown as exc:
            raise VoiceError(f"{spoken} is a paid link with no price: {exc}") from None

        if "booked" not in (result.meta or {}):
            est = _book(gates.ledger, result, answered, qty=len(text), step=step, ep=gates.ep)
        else:
            # The journal booked it (or served a kept answer it booked before).
            est = round(float(result.est_cost) if result.paid else 0.0, 4)
        audio, ext, sidecar, data = _line_outputs(result, spoken)
        try:
            # A Gemini answer the generation cache kept from before the tail
            # guard comes back with its static: its scratch copy is cleaned
            # before it is kept (the cache's own file stays as answered, so
            # every later hit is cleaned the same way).
            guarded = tts.guard_kept_take(audio, sidecar, data)
        except OSError as exc:
            raise VoiceError(f"{spoken} answered, but its static could not be cut "
                             f"({type(exc).__name__}: {exc}).") from None
        if guarded is not None:
            data = guarded
            if guarded["tail_guard"]["trimmed_s"] > 0:
                on_log(f"   🔇 {spoken}: a take kept from before the tail guard; "
                       f"{guarded['tail_guard']['trimmed_s']:.2f} s of static cut from its end")
        try:
            _atomic_copy(audio, dest_for(ext))
            _atomic_copy(sidecar, dest_for("json"))
        except (KeyError, OSError, ValueError) as exc:
            raise VoiceError(f"{spoken} answered, but the audio could not be kept "
                             f"({type(exc).__name__}: {exc}).") from None

    return {"ext": ext, "duration_s": round(float(data["duration_s"]), 3), "source": data["source"],
            "voice": label, "link": answered, "paid": bool(result.paid), "est_usd": est,
            "cached": bool((result.meta or {}).get("cached")), "tail_guard": data.get("tail_guard")}


def _paid_off_reason(est, link, budget_obj) -> str:
    """``gating.link_summary``'s refusal while ``allow_paid`` is off, for an
    amount summed over several lines (the runner's first gate, DEC-097)."""
    return (f"refused: est ${est:.3f} on {describe(link)}; allow_paid is off "
            f"(today ${budget_mod.day_spent():.2f} of ${budget_obj.daily_cap_usd:.2f})")


# What a paid voice is priced for in the cast step's alternates list, where no
# script exists yet: a reference episode of this many characters of speech (about
# two minutes at 15 characters a second), all of it in that one voice -- the
# dearest case, so the figure is never low. Once a script exists the
# estimate is :func:`estimate_lines`'s, from its real lines.
PAID_VOICE_EPISODE_CHARS = 1800


def paid_voice_summary(voice: Voice, *, env, adapters=None) -> dict:
    """``{"est_usd", "allowed", "reason"}`` for a paid *voice* speaking
    :data:`PAID_VOICE_EPISODE_CHARS` characters, from the runner's own gates
    (``gating.link_summary``, nothing called): the adapter's estimate, and
    the refusal -- ``allow_paid`` off, a cap, a missing key -- with its
    numbers. ``allowed`` False with the budget error as its reason when the
    budget settings cannot be read."""
    if adapters is None:
        adapters_mod.load_all()
    merged = gating.merged_env(env)
    try:
        budget_obj = gating.budget_of(merged)
    except ValueError as exc:
        return {"est_usd": 0.0, "allowed": False, "reason": f"The budget settings cannot be used: {exc}"}
    request = generation.GenRequest(kind=generation.TTS, text="x" * PAID_VOICE_EPISODE_CHARS, voice=voice.voice_id)
    summary = gating.link_summary(generation.TTS, voice.link, merged, budget_obj, request, adapters=adapters)
    return {"est_usd": round(float(summary["est_usd"]), 4), "allowed": bool(summary["allowed"]),
            "reason": summary["reason"]}


def estimate_lines(stories, story_id, items, *, env, ep=None, adapters=None) -> dict:
    """What :func:`synthesize_line` would cost and whether the runner's gates
    would let each voice through, for *items* (``[(voice, text,
    speaker_name)]``), calling nothing -- no provider, no probe, no counter::

        {"voices": [{"voice", "link", "speakers", "lines", "chars", "paid", "est_usd", "allowed", "reason"}],
         "est_usd": x, "paid_links": [{"link", "allowed", "reason"}], "allow_paid": bool,
         "free_tier": {provider: {"rpm", "rpd", "calls", "left", "needed"}}, "ready": bool}

    One row per pinned voice, in the order the lines name them. A paid
    link's price is the adapter's own estimate of each line (the number the
    runner checks and ``_book`` records), summed; its verdict is
    ``allow_paid`` then the caps, against the story's and the episode's
    spending so far. A free link is checked against its provider's daily
    allowance (``free_tier``: what is left today and how many lines would
    use it); a local engine is probed when it runs, never here.
    """
    if adapters is None:
        adapters_mod.load_all()
    merged = gating.merged_env(env)
    try:
        budget_obj = gating.budget_of(merged)
        budget_error = None
    except ValueError as exc:
        budget_obj, budget_error = None, f"The budget settings cannot be used: {exc}"

    ledger = ledger_mod.CostLedger(os.path.join(stories.story_dir(story_id), LEDGER_NAME))
    story_spent = float(ledger.totals()["est_usd"])
    ep_spent = float(ledger.totals(ep)["est_usd"]) if ep is not None else 0.0

    rows, links = {}, {}
    for voice, text, speaker in items:
        label = voice_label(voice)
        row = rows.get(label)
        if row is None:
            row = rows[label] = {"voice": label, "link": None, "speakers": [], "lines": 0, "chars": 0,
                                 "paid": False, "est_usd": 0.0, "allowed": True, "reason": None}
            try:
                links[label] = _chain_link(voice["provider"], voice["voice_id"])
            except VoiceError as exc:
                links[label] = None
                row.update(allowed=False, reason=str(exc))
            else:
                row["link"] = describe(links[label])
                row["paid"] = generation.is_paid(links[label])
        if speaker not in row["speakers"]:
            row["speakers"].append(speaker)
        row["lines"] += 1
        row["chars"] += len(text)
        link = links[label]
        if link is not None and row["paid"]:
            adapter = generation.adapter_for(generation.TTS, link.provider, adapters)
            request = generation.GenRequest(kind=generation.TTS, text=text, voice=voice["voice_id"])
            try:
                estimate = adapter.estimate(link, request) if adapter is not None else None
                if estimate is None:
                    estimate = pricing.estimate(link, len(text))
                row["est_usd"] = round(row["est_usd"] + float(getattr(estimate, "est_usd", estimate) or 0.0), 4)
            except pricing.PriceUnknown as exc:
                row.update(allowed=False, reason=str(exc))

    table = limits.limits_from_env()
    usage = limits.default_usage()
    free_tier = {}
    pending = 0.0  # paid voices before this one, as the runner would book them in turn
    for label, row in rows.items():
        link = links[label]
        if link is None or not row["allowed"]:
            continue
        adapter = generation.adapter_for(generation.TTS, link.provider, adapters)
        missing = generation.missing_keys(link, merged)
        if adapter is None:
            row.update(allowed=False, reason=f"no adapter yet for {link.provider} {generation.TTS}")
        elif missing:
            row.update(allowed=False, reason=f"no API key ({' and '.join(missing)} "
                                              f"{'is' if len(missing) == 1 else 'are'} not set)")
        elif row["paid"]:
            if budget_obj is None:
                row.update(allowed=False, reason=budget_error)
            elif not budget_obj.allow_paid:
                row.update(allowed=False, reason=_paid_off_reason(row["est_usd"], link, budget_obj))
            else:
                try:
                    state = budget_mod.day_state()
                    budget_mod.check(row["est_usd"], link, budget=budget_obj,
                                     day_spent=state.spent + pending, day_extra=state.extra,
                                     ep_spent=ep_spent + pending, story_spent=story_spent + pending)
                except budget_mod.BudgetRefused as exc:
                    row.update(allowed=False, reason=str(exc))
                else:
                    pending += row["est_usd"]
        elif link.provider in table:
            limit = table[link.provider]
            entry = free_tier.get(link.provider)
            if entry is None:
                calls = usage.calls(link.provider)
                entry = free_tier[link.provider] = {
                    "rpm": limit.rpm, "rpd": limit.rpd, "calls": calls,
                    "left": None if limit.rpd is None else max(0, limit.rpd - calls), "needed": 0,
                }
            entry["needed"] += row["lines"]

    for label, row in rows.items():
        link = links[label]
        entry = free_tier.get(link.provider) if link is not None and not row["paid"] else None
        if row["allowed"] and entry is not None and entry["left"] is not None and entry["needed"] > entry["left"]:
            row.update(allowed=False, reason=f"daily allowance: {entry['left']} of {entry['rpd']} {link.provider} "
                                             f"calls left today, {entry['needed']} needed (resets at 00:00 UTC)")

    paid_links = {}
    for row in rows.values():
        if not row["paid"]:
            continue
        entry = paid_links.setdefault(row["link"], {"link": row["link"], "allowed": True, "reason": None})
        if not row["allowed"] and entry["allowed"]:
            entry.update(allowed=False, reason=row["reason"])
    voices_rows = list(rows.values())
    return {
        "voices": voices_rows, "est_usd": round(sum(row["est_usd"] for row in voices_rows), 4),
        "paid_links": list(paid_links.values()), "allow_paid": bool(budget_obj and budget_obj.allow_paid),
        "free_tier": free_tier, "ready": all(row["allowed"] for row in voices_rows),
    }

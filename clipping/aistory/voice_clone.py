"""One frozen voice per character, cloned on the RunPod worker (plan 32 stage 6).

A story whose TTS chain starts with ``runpod/tts_chatterbox`` -- the
``own_gpu`` budget profile's ``tts_chain`` -- or that follows the
``fruit_drama`` recipe gives each character ONE reference recording, made
once, at cast time, and never again:

* the writer's character answer (K1) carries two more fields on such a story
  only (:func:`extend_k1`): ``voice_pick``, one of the Gemini prebuilt voices of
  ``templates/voices.json``, and ``voice_sample_text``, about 12 seconds (25 to
  40 words) the character says, in the story's language. A story off the gate
  is asked exactly what it always was;
* they are kept beside the character (:data:`PLAN_NAME`, :func:`write_plan`):
  the character document's schema is closed, and the text is longer than any
  of its voice fields;
* the cast step pins the character's voice to the clone
  (``{"provider": "runpod", "voice_id": "reference"}``,
  ``voice_reference.is_clone_voice``) -- or, when RunPod has no key and Gemini
  has one, to the Gemini voice picked, the link behind it (:func:`pin_choice`);
* the reference is the sample text spoken by that Gemini voice through the
  gemini link (``voices.synthesize_line``: the story's gates and ledger, free),
  then accepted by ``voice_reference.accept_voice_reference`` -- re-encoded
  mono 24 kHz 16-bit, 5 to 30 s, stored as the character's
  ``voice_reference.wav`` (:func:`make_reference`). Without a Gemini key the
  pick and the text stay recorded and the reference is made by the first
  voices step that needs it; the cast never fails over a missing key;
* every line is then cloned from it on the character's fixed seed
  (``tts.voice_seed`` of its id).

Synthetic references only (DEC-281): a reference made here is a prebuilt
synthetic voice, never a person's; the ``consent`` the reference entry
records is the app's own statement that no real voice is involved.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import json
import os
import tempfile

from clipping.providers import budget as budget_mod
from clipping.providers import gating, generation, tts
from clipping.providers.registry import ChainError, Link, describe

from . import voice_reference, voices

RECIPE = "fruit_drama"
CLONE_LINK = Link(voice_reference.CLONE_PROVIDER, tts.RUNPOD_TTS_TEMPLATES[0])
GEMINI_LINK = Link("gemini", next(iter(tts.GEMINI_TTS_MODELS)))
# The chain a gated story is voiced on when its budget profile names none.
DEFAULT_CHAIN = (describe(CLONE_LINK), describe(GEMINI_LINK))

PLAN_NAME = "voice_clone.json"
PLAN_SCHEMA = "voice_clone_plan_v1"
STEP = "voice_reference"
# What the writer is asked (words of the sample text), and what is accepted.
SAMPLE_WORDS = (25, 40)
ACCEPT_WORDS = (18, 50)
SAMPLE_MAX_CHARS = 400
# K1's reply cap with the two fields (prompts.MAX_TOKENS["K1"] is 750; the
# sample text adds about 100 tokens of French).
K1_EXTRA_TOKENS = 200

PICK = "voice_pick"
TEXT = "voice_sample_text"

_GENDERS = {"f": "female", "m": "male"}


# The ffprobe/ffmpeg seam :func:`make_reference` hands to the reference's
# acceptance when its caller gives none (None: ``subprocess.run``); tests set it.
TOOL_RUN = None


class VoiceCloneError(Exception):
    """A reference could not be made; the message says why in one sentence."""


# ----------------------------------------------------------------- the gate

def tts_chain(story, env=None) -> list:
    """The TTS link labels *story* is voiced on, in order: its budget
    profile's ``tts_chain`` when it names one, else ``TTS_CHAIN`` of the
    merged Settings (*env*), else the shipped default."""
    profile = (story or {}).get("generation_profile") or {}
    try:
        chain = budget_mod.profile_settings(profile.get("budget_profile")).get("tts_chain")
    except (OSError, ValueError, KeyError, TypeError):
        chain = None
    if chain:
        return [str(link) for link in chain]
    try:
        return [describe(link) for link in generation.chain_from_env(generation.TTS, gating.merged_env(env or {}))]
    except ChainError:
        return []


def applies(story, env=None) -> bool:
    """Whether *story*'s characters each get a frozen cloned voice: its TTS
    chain starts with ``runpod/tts_chatterbox``, or it follows the
    ``fruit_drama`` recipe."""
    if (story or {}).get("recipe") == RECIPE:
        return True
    chain = tts_chain(story, env)
    return bool(chain) and chain[0] == describe(CLONE_LINK)


def clone_chain(story, env=None) -> list:
    """The chain a gated story pins on: its own when it starts with the
    clone, else :data:`DEFAULT_CHAIN` (a recipe story on another profile)."""
    chain = tts_chain(story, env)
    return chain if chain and chain[0] == describe(CLONE_LINK) else list(DEFAULT_CHAIN)


def keys_missing(link, env) -> list:
    """The variables *link* lacks in the merged Settings (*env*)."""
    return generation.missing_keys(link, gating.merged_env(env or {}))


def pin_choice(story, env=None) -> str:
    """``"runpod"`` (the clone) or ``"gemini"`` (the voice picked, spoken by
    the gemini link): the first link of :func:`clone_chain` whose keys are
    set; the clone when none is (the voices step then says what is missing)."""
    for label in clone_chain(story, env):
        provider, _, model = label.partition("/")
        if provider not in (CLONE_LINK.provider, GEMINI_LINK.provider):
            continue
        if not keys_missing(Link(provider, model), env):
            return provider
    return CLONE_LINK.provider


# ------------------------------------------------------------------ the ask

def gemini_voices() -> list:
    """The Gemini prebuilt voices of ``templates/voices.json``: ``[entry]``."""
    return list(tts.load_voices().get("providers", {}).get("gemini", []))


def voice_names() -> list:
    return [entry["voice_id"] for entry in gemini_voices()]


def _voice_line(entry) -> str:
    gender = _GENDERS.get(entry.get("gender"), entry.get("gender") or "any")
    tags = ", ".join(entry.get("style_tags") or ())
    return f"{entry['voice_id']} ({gender}, {entry.get('age') or 'adult'}{', ' + tags if tags else ''})"


def ask_addendum(taken=()) -> str:
    """The two fields asked of K1 on a gated story, after its own ask."""
    lo, hi = SAMPLE_WORDS
    taken = [name for name in taken if name]
    avoid = (f"; prefer one no other character has ({', '.join(taken)} "
             f"{'is' if len(taken) == 1 else 'are'} taken)") if taken else ""
    return (
        "\n\nThis character gets one fixed voice, made once and kept for every episode. Also give:\n"
        f"- voice_pick: the voice it is made from, one of {', '.join(_voice_line(e) for e in gemini_voices())}"
        f"{avoid}\n"
        f"- voice_sample_text: {lo} to {hi} words the character says aloud, in the story's language, in "
        "character, about 12 seconds of natural speech in one or two sentences; no name, no stage direction, "
        "no emoji"
    )


def extend_k1(user, schema, *, taken=()) -> tuple:
    """``(user, schema)`` of K1 with the two fields asked and required."""
    lo, hi = SAMPLE_WORDS
    extended = copy.deepcopy(schema)
    extended["properties"][PICK] = {"type": "string", "enum": voice_names()}
    extended["properties"][TEXT] = {"type": "string",
                                    "description": f"story language, {lo}-{hi} words, in character, no name"}
    extended["required"] = list(extended.get("required") or []) + [PICK, TEXT]
    return user + ask_addendum(taken), extended


def k1_part(reply) -> dict:
    """*reply* without the two fields: what K1's own checks read."""
    return {key: value for key, value in (reply or {}).items() if key not in (PICK, TEXT)}


def reply_errors(reply) -> list:
    """What is wrong with the two fields of a gated K1 *reply*."""
    errors = []
    names = voice_names()
    pick = (reply or {}).get(PICK)
    if pick not in names:
        errors.append(f"$.{PICK}: {pick!r} is not one of {', '.join(names)}")
    text = (reply or {}).get(TEXT)
    if not isinstance(text, str) or not text.strip():
        errors.append(f"$.{TEXT}: write what the character says ({SAMPLE_WORDS[0]}-{SAMPLE_WORDS[1]} words)")
    else:
        words = len(text.split())
        lo, hi = ACCEPT_WORDS
        if not lo <= words <= hi or len(text) > SAMPLE_MAX_CHARS:
            errors.append(f"$.{TEXT}: {words} words; write {SAMPLE_WORDS[0]} to {SAMPLE_WORDS[1]} "
                          f"(about 12 seconds of speech)")
    return errors


# ----------------------------------------------------------------- the plan

def _plan_path(stories, story_id, char_id) -> str:
    return os.path.join(stories.entity_dir(story_id, voice_reference.KIND, char_id), PLAN_NAME)


def read_plan(stories, story_id, char_id):
    """The character's recorded pick and text ``{voice_pick, sample_text,
    seed, written_at}``, or None (none, unreadable, or a symlink)."""
    try:
        path = _plan_path(stories, story_id, char_id)
    except KeyError:
        return None
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if (not isinstance(data, dict) or data.get("$schema") != PLAN_SCHEMA or data.get(PICK) not in voice_names()
            or not isinstance(data.get("sample_text"), str) or not data["sample_text"].strip()):
        return None
    return data


def write_plan(stories, story_id, char_id, *, pick, text, now) -> dict:
    """Record *pick* and *text* beside the character, atomically."""
    data = {"$schema": PLAN_SCHEMA, PICK: pick, "sample_text": " ".join(str(text).split()),
            "seed": tts.voice_seed(char_id), "written_at": now}
    path = _plan_path(stories, story_id, char_id)
    if os.path.islink(path):
        raise VoiceCloneError(f"{PLAN_NAME} is a symlink, which is never followed; replace it with the file itself.")
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".voice-clone-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return data


def has_reference(stories, story_id, char_id) -> bool:
    try:
        return voice_reference.reference_path(stories, story_id, char_id) is not None
    except KeyError:
        return False


def keep_plan(stories, story_id, char_id, reply, *, now):
    """K1's two fields recorded for the character -- unless its reference is
    already made (it is frozen: a rewritten text never changes the voice).
    Returns the plan written, or None."""
    if has_reference(stories, story_id, char_id):
        return None
    return write_plan(stories, story_id, char_id, pick=reply[PICK], text=reply[TEXT], now=now)


def taken_picks(stories, story_id, *, exclude=None) -> list:
    """The voices other characters of the story were made from, in cast order."""
    picks = []
    for doc in stories.list_entities(story_id, voice_reference.KIND):
        if doc["char_id"] == exclude:
            continue
        plan = read_plan(stories, story_id, doc["char_id"])
        if plan and plan[PICK] not in picks:
            picks.append(plan[PICK])
    return picks


def fallback_plan(character, *, taken=()) -> tuple:
    """``(pick, text)`` for a character whose text was written before the
    gate (no plan): the best Gemini voice for its K1 brief not yet taken
    (``voices`` scoring), and its own words -- the sample line, then what it
    wants and fears -- as the sample text. Deterministic; no call."""
    pool = [voices.Voice(provider="gemini", voice_id=e["voice_id"], lang=e.get("lang", ""),
                         gender=e.get("gender", ""), age=e.get("age", ""), style_tags=e.get("style_tags") or (),
                         link=GEMINI_LINK) for e in gemini_voices()]
    fresh = [voice for voice in pool if voice.voice_id not in set(taken)] or pool
    prefer = character.get("voice_hints") or {}
    pick = voices._best_of(prefer, fresh).voice_id  # noqa: SLF001 - the one scoring rule
    personality = character.get("personality") or {}
    parts = [prefer.get("sample_line"), personality.get("wants"), personality.get("fears"),
             personality.get("speech_style")]
    sentences = []
    for part in parts:
        part = " ".join(str(part or "").split()).strip()
        if part:
            sentences.append(part if part[-1] in ".!?…" else part + ".")
    words = " ".join(sentences).split()[:SAMPLE_WORDS[1]]
    return pick, " ".join(words)


# ------------------------------------------------------------ the reference

def make_reference(stories, story_id, char_id, *, gates, on_log, cancel, now, adapters=None, transport=None,
                   run=None) -> dict:
    """The character's frozen reference: its recorded sample text spoken by
    the Gemini voice it picked (``voices.synthesize_line`` under *gates*,
    booked as ``voice_reference``), accepted as its ``voice_reference.wav``
    (``voice_reference.accept_voice_reference``: mono 24 kHz 16-bit, 5-30 s;
    *run* is its ffprobe/ffmpeg seam). Returns the reference entry.

    :class:`VoiceCloneError` in one sentence: no recorded plan, no Gemini key
    (the reference waits for one), or the speech or the reference refused."""
    character = stories.read_entity(story_id, voice_reference.KIND, char_id)
    name = character.get("name") or char_id
    plan = read_plan(stories, story_id, char_id)
    if plan is None:
        raise VoiceCloneError(f"{name} has no voice pick and sample text recorded; run the cast step again.")
    missing = generation.missing_keys(GEMINI_LINK, gates.merged)
    if missing:
        raise VoiceCloneError(f"{name}'s voice reference waits for a Gemini key ({' and '.join(missing)} is not "
                              "set): the voice is picked and its text written; it is made once the key is there.")
    voice = {"provider": "gemini", "voice_id": plan[PICK], "rate": None, "pitch": None}
    with tempfile.TemporaryDirectory(prefix="voice-reference-") as scratch:
        try:
            spoken = voices.synthesize_line(gates, voice=voice, text=plan["sample_text"],
                                            dest_for=lambda ext: os.path.join(scratch, f"reference.{ext}"),
                                            on_log=on_log, cancel=cancel, step=STEP, adapters=adapters,
                                            transport=transport)
        except voices.VoiceError as exc:
            raise VoiceCloneError(f"{name}'s voice reference could not be spoken: {exc}") from exc
        source = os.path.join(scratch, f"reference.{spoken['ext']}")
        try:
            entry = voice_reference.accept_voice_reference(stories, story_id, char_id, source, consent=True,
                                                           now=now, run=run if run is not None else TOOL_RUN,
                                                           first_frozen=True)
        except voice_reference.VoiceReferenceError as exc:
            raise VoiceCloneError(f"{name}'s voice reference was refused: {exc}") from exc
    on_log(f"🎙️ {name}: voice reference made from gemini/{plan[PICK]} ({entry['duration_s']:.1f} s), frozen")
    return entry

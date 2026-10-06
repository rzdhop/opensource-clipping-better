"""Speaking an episode's lines with their pinned voices (spec 6.4 source (a),
8.1; DEC-122, DEC-135; AI Story phase 4, stage 8).

Lifted out of ``steps/script.py`` (phase 3's opt-in "measure with real
voices") unchanged in behaviour, so the script step and the assets step
(phase 4) speak a line the one same way:

- :func:`is_measured` / :func:`lines_to_measure` -- a line whose timing is
  not a current measurement (estimated, of other words, of another voice
  than its speaker's pinned one, or its audio gone) is spoken again; every
  other line is left alone;
- :class:`LineMeasurement` -- the run: each line through its speaker's
  pinned voice alone (``voices.synthesize_line``: a one-link chain; a
  failing voice fails only its own lines, naming the character, and nothing
  else is tried), kept as ``assets/voice/line_NN.mp3|.wav`` + its ``.json``
  sidecar, the line's ``timing`` becoming the measured one, the script
  re-timed and written after every line and the storyboard's shot durations
  following (``shots.retime_storyboard``: no revision moves, no approval is
  cleared). Cancel and the step budget (:data:`STORY_TTS_CALL_SECONDS` per
  synthesis) are checked before each line;
- :func:`measure_estimate` -- what it would do, calling nothing.

:class:`LineMeasurement` is a mixin of the step's own run object (the script
step's ``_Run``, the assets step's): it reads the run's ``ctx``, ``ec``,
``script``, ``storyboard``, ``budget`` and ``tools``, calls its ``save()``
and ``failures()``, and keeps ``voice_failed``, ``measured`` and
``board_refused`` on it. Four hooks change what the assets step asks for,
and nothing when left alone (the script step): :attr:`measure_step` (the
ledger's ``step``), :meth:`voice_cache` (the generation cache a line's
request goes through, DEC-151), :attr:`voice_take` (a voice regenerate's
take, so the cache misses on purpose) and :attr:`voice_direction` (its
note, the take's spoken direction, phase 5 stage 7); a fifth,
:meth:`voice_refused`, hands it a failed line's ``VoiceError`` (its chain
failures pace a voice a free tier held back).

The storyboard follows every measured line (:meth:`sync_storyboard`): a
scene a text-only edit marked ``retime_only`` is re-timed in place with the
others, and its mark goes once its lines are all measured again
(``shots.retime_storyboard``, phase 5 stage 7).

**The Gemini tail guard** (``providers.tts_tail``): a new Gemini line comes
cleaned from its adapter, and a cached answer kept from before the guard is
cleaned as it lands (``voices.synthesize_line``). A line voiced before it
keeps its static until :meth:`LineMeasurement.guard_tails` -- the assets
step's, before it measures -- cleans its kept WAV in place, for free, never
speaking it again (``tts.guard_kept_take``; an aligned line's last word
gives the speech's end), and re-times the line exactly as a measurement
does. A sixth hook, :meth:`tail_guarded`, hands the run each report.

The story's own document is never read for writing (RC-E2).
"""

from __future__ import annotations

import json
import os

from clipping.providers import tts, tts_tail

from .. import media_policy, schemas, shots, timing, voice_clone, voice_reference, voices, wordtiming
from .. import store as store_mod
from . import episode_common, llm_call
from .episode_common import STORYBOARD_DOC
from .llm_call import StepFailed

# How long one synthesis may take, for the step budget's predictive check: a
# line is a few seconds of speech, a free link retries once after 3 s, and a
# free tier's per-minute pacing may hold a request for up to a minute (Edge
# 30, Gemini 15 requests a minute).
STORY_TTS_CALL_SECONDS = 60

VOICE_ASSETS = f"{store_mod.EPISODE_ASSETS_DIRNAME}/voice"
_HOW = {"tts_word_timestamps": "word timings", "audio_duration_only": "audio duration"}


class BudgetSpent(StepFailed):
    """The step's time budget ended the run (not a failed call)."""


def _and(items) -> str:
    """``a``, ``a and b``, ``a, b and c`` (``script._and``, duplicated: a
    private helper of the module this code was lifted from)."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


# ----------------------------------------------------------------- speakers

def speaker_name(ec, speaker) -> str:
    return "Narrator" if speaker == "narrator" else ec.names.get(speaker, speaker)


def speaker_voice(ec, speaker):
    """The voice block that speaks *speaker*: a character's pinned voice, or
    the story's narrator voice; None when there is none."""
    if speaker == "narrator":
        return (ec.story.get("narrator") or {}).get("voice")
    return (ec.entities["characters"].get(speaker) or {}).get("voice")


# Plan 28 stage B1: the provider part of the label a native take records as
# its line's voice on a story without generated voices (``clip/<char_id>``):
# the character's own clip speaks it, no TTS voice does.
CLIP_VOICE_PROVIDER = "clip"


def line_voice_label(ec, speaker):
    """What a measured line of *speaker* records as ``timing.voice``, and
    what :func:`is_measured` compares it with: the speaker's pinned voice
    label (``voices.voice_label``), None without one. Plan 28 stage B1: on a
    story without generated voices (``media_policy.no_voices``) a
    character's lines are its clips' own speech, labelled
    ``clip/<char_id>`` -- no voice is ever pinned for it."""
    if speaker != "narrator" and media_policy.no_voices(ec.story):
        return f"{CLIP_VOICE_PROVIDER}/{speaker}"
    return voices.voice_label(speaker_voice(ec, speaker))


def speech_provider(ec, speaker):
    """The TTS provider whose voice speaks *speaker*'s lines (plan 24 stage
    1, D-1/D-5): the narrator's or the character's pinned voice's
    ``provider``; None when there is no voice yet, and for a character on a
    native-speech story -- its lines are spoken by its clips
    (:func:`spoken_by_clip`), at no TTS overrun."""
    if speaker != "narrator" and media_policy.native_speech(ec.story):
        return None
    voice = speaker_voice(ec, speaker)
    return voice.get("provider") if isinstance(voice, dict) else None


def no_voice_reason(ec, speaker) -> str:
    if speaker == "narrator":
        return "the narrator has no voice yet"
    return f"{speaker_name(ec, speaker)} has no pinned voice yet"


def asset_name(line_id, ext) -> str:
    """``line_05.mp3`` for line ``l05``: the line's own number, so a take is
    never taken for another line's (``schemas.line_id_for``)."""
    return f"line_{line_id[1:]}.{ext}"


def _audio_kept(ec, audio) -> bool:
    """Whether the file a measured line's ``timing.audio`` names is there
    (a regular file, never through a symlink)."""
    prefix = f"{VOICE_ASSETS}/"
    if not isinstance(audio, str) or not audio.startswith(prefix):
        return False
    try:
        path = ec.store.episode_asset_path(ec.story_id, ec.ep, "voice", audio[len(prefix):])
    except KeyError:
        return False
    return os.path.isfile(path)


def is_measured(ec, line) -> bool:
    """Whether *line*'s timing is a current measurement: measured (not
    estimated), of its text as it is now (the text hash: an edited line has
    fallen back to the estimate, ``timing.line_duration``), with the voice
    its speaker has pinned now, and its audio still on disk."""
    current = line["timing"]
    label = line_voice_label(ec, line["speaker"])
    return (current["source"] in voices.MEASURED_SOURCES and current["text_hash"] == timing.text_hash(line["text"])
            and label is not None and current.get("voice") == label and _audio_kept(ec, current.get("audio")))


# The extensions a synthesis has ever been kept under (module docstring:
# "assets/voice/line_NN.mp3|.wav").
_AUDIO_EXTS = ("mp3", "wav")


def audio_path_candidates(ec, line_id) -> list:
    """The real paths line *line_id*'s audio could be at (``line_NN.mp3``,
    ``line_NN.wav``), whether or not either exists yet -- unlike
    :func:`_audio_kept`, not keyed to the script's own current ``timing.
    audio`` pointer, which a text edit clears (``timing.estimated_timing``)
    while the file made for the words it replaced is kept on disk (``workflow.
    patch_script``'s own docstring). ``None`` where the store refuses a path
    (an unknown episode) in place of that extension's entry."""
    paths = []
    for ext in _AUDIO_EXTS:
        try:
            paths.append(ec.store.episode_asset_path(ec.story_id, ec.ep, "voice", asset_name(line_id, ext)))
        except KeyError:
            paths.append(None)
    return paths


def has_audio(ec, line_id) -> bool:
    """Whether line *line_id* has a synthesised audio file on disk at
    either extension, current text or not -- the common case of a line the
    plain assets step voiced and a later text edit then made stale, which
    never gets a ``take`` (that is only recorded by a ``line:`` regenerate):
    without this, EpisodeStudio's "Re-voice this line" has nothing left to
    tell it apart from a line that was never voiced at all (plan 11 stage
    11, browser-check fix round 2). One stat per candidate extension, no
    symlink followed (``os.path.isfile``, as :func:`_audio_kept` reads the
    same folder)."""
    return any(path is not None and os.path.isfile(path) for path in audio_path_candidates(ec, line_id))


def spoken_by_clip(ec, line) -> bool:
    """Whether *line* is spoken by its own clip, never by a TTS voice (plan
    22): a character's line on a native-speech story -- its audio is the
    clip's own sound (the assets step's native take). A narrator's line is
    always a TTS voice-over."""
    return line["speaker"] != "narrator" and media_policy.native_speech(ec.story)


def lines_to_measure(ec, script) -> list:
    """Every line of *script* the measurement would synthesise, in reading
    order -- never one its clip speaks (:func:`spoken_by_clip`), and none on
    a story without generated voices (plan 28 stage B1,
    ``media_policy.no_voices``: no TTS at all)."""
    if media_policy.no_voices(ec.story):
        return []
    return [line for scene in script["scenes"] for line in scene["lines"]
            if not is_measured(ec, line) and not spoken_by_clip(ec, line)]


def measure_estimate(ec, script, *, env=None, adapters=None) -> dict:
    """What measuring *script* with real voices would do now, calling
    nothing (stage 8's ``GET /estimate/script?measure=1``)::

        {"lines": n, "chars": n, "est_usd": x,
         "voices": [{"voice", "link", "speakers", "lines", "chars", "paid", "est_usd", "allowed", "reason"}],
         "unvoiced": [{"line_id", "speaker", "reason"}],
         "paid_links": [{"link", "allowed", "reason"}], "allow_paid": bool,
         "free_tier": {provider: {"rpm", "rpd", "calls", "left", "needed"}},
         "ready": bool}

    ``lines``/``chars``/``voices`` count the lines :func:`lines_to_measure`
    names whose speaker has a voice; ``unvoiced`` the ones that would fail
    for want of one. ``est_usd`` is the paid links' price of those
    characters (a free link is $0.00). A voice is ``allowed`` when the
    runner's gates would let it through (adapter, key, then ``allow_paid``
    and the caps for a paid link, the day's allowance for a free one);
    ``reason`` says why not. *env* is the Settings values (None: the process
    environment alone)."""
    wanted, unvoiced = [], []
    for line in lines_to_measure(ec, script):
        voice = speaker_voice(ec, line["speaker"])
        if voices.voice_label(voice) is None:
            unvoiced.append({"line_id": line["line_id"], "speaker": line["speaker"],
                             "reason": no_voice_reason(ec, line["speaker"])})
            continue
        wanted.append((voice, line["text"], speaker_name(ec, line["speaker"])))
    verdict = voices.estimate_lines(ec.store, ec.story_id, wanted, env=env, ep=ec.ep, adapters=adapters)
    return {
        "lines": len(wanted), "chars": sum(len(text) for _voice, text, _name in wanted),
        "est_usd": verdict["est_usd"], "voices": verdict["voices"], "unvoiced": unvoiced,
        "paid_links": verdict["paid_links"], "allow_paid": verdict["allow_paid"],
        "free_tier": verdict["free_tier"], "ready": verdict["ready"] and not unvoiced,
    }


# ---------------------------------------------------------- the tail report

def aligned_end(sidecar):
    """The last aligned word's end of a line's sidecar -- the speech's end
    the tail guard is handed -- or None when its words are not an
    alignment's (Gemini times no word itself)."""
    if wordtiming.source_of(sidecar)[0] != wordtiming.ALIGNMENT:
        return None
    return max((word["end"] for word in sidecar["words"] if isinstance(word, dict)
                and isinstance(word.get("end"), (int, float)) and not isinstance(word.get("end"), bool)),
               default=None)


def _speaker_label(store, story_id, speaker) -> str:
    if speaker == "narrator":
        return "Narrator"
    try:
        return store.read_entity(story_id, "characters", speaker).get("name") or speaker
    except Exception:  # noqa: BLE001 - a label only: the id stands in
        return speaker


def tail_report(store, story_id, ep, script) -> list:
    """What the Gemini tail guard would cut, or cut, at the end of each line
    of *script* (episode *ep*), reading the kept files and changing nothing
    -- the ``voice-tails`` command's rows, in reading order::

        {"line_id", "speaker", "voice", "file", "seconds", "provider",
         "guard": the sidecar's tail_guard or None, "due": bool,
         "analysis": tts_tail.analyse(...) of the file as it is now, or None}

    ``file``/``seconds`` are None for a line with no kept audio; ``analysis``
    is None for a file that is not a 16-bit mono WAV (an Edge mp3). An
    aligned line's last word end is handed to the analysis, as
    :meth:`LineMeasurement.guard_tails` hands it to the guard."""
    rows = []
    for line in (line for scene in script["scenes"] for line in scene["lines"]):
        line_id, timing_doc = line["line_id"], line.get("timing") or {}
        row = {"line_id": line_id, "speaker": _speaker_label(store, story_id, line["speaker"]),
               "voice": timing_doc.get("voice"), "file": None, "seconds": None, "provider": None, "guard": None,
               "due": False, "analysis": None}
        rows.append(row)
        audio = timing_doc.get("audio")
        if not isinstance(audio, str) or not audio.startswith(f"{VOICE_ASSETS}/"):
            continue
        try:
            audio_path = store.episode_asset_path(story_id, ep, "voice", audio[len(VOICE_ASSETS) + 1:])
            timing_path = store.episode_asset_path(story_id, ep, "voice", asset_name(line_id, "json"))
        except KeyError:
            continue
        if not os.path.isfile(audio_path):
            continue
        row["file"] = os.path.basename(audio_path)
        try:
            with open(timing_path, encoding="utf-8") as fh:
                sidecar = json.load(fh)
        except (OSError, ValueError):
            sidecar = {}
        sidecar = sidecar if isinstance(sidecar, dict) else {}
        guard = sidecar.get("tail_guard")
        row.update(provider=sidecar.get("provider"), guard=guard if isinstance(guard, dict) else None,
                   due=tts.tail_guard_due(sidecar))
        read = tts_tail.read_wav(audio_path)
        if read is None:
            row["seconds"] = sidecar.get("duration_s")
            continue
        pcm, rate = read
        row["seconds"] = round(len(pcm) / 2 / rate, 3)
        row["analysis"] = tts_tail.analyse(pcm, rate, speech_end_s=aligned_end(sidecar))
    return rows


# -------------------------------------------------------------------- the run

class LineMeasurement:
    """The voice measurement of one step run, mixed into the run object (see
    the module docstring for what it reads from it). The run's ``__init__``
    sets ``voice_failed = []`` (``[(line_id, speaker, reason)]``),
    ``measured = 0`` and ``board_refused = False``."""

    # The ledger's ``step`` for every line spoken here (``voices.synthesize_line``).
    measure_step = voices.MEASURE_STEP
    # A voice regenerate's take (``gencache``'s key field): None asks for
    # the line as it always was.
    voice_take = None
    # A voice regenerate's note, the take's spoken direction (phase 5 stage
    # 7; ``voices.synthesize_line``): None asks for the line as it always was.
    voice_direction = None

    def voice_cache(self, gates, line):
        """The generation cache *line*'s request goes through, or None (the
        script step: no cache, nothing changes, RC-A2)."""
        return None

    def voice_refused(self, line, exc) -> None:
        """*line*'s pinned voice failed with *exc* (``voices.VoiceError``;
        its ``__cause__`` is the chain's ``NoRunnableLink`` when the chain
        ran). Nothing here (the script step); the assets step keeps the
        chain's failures to pace a rate-limited voice."""

    def tail_guarded(self, line, report) -> None:
        """*line*'s audio went through the Gemini tail guard in this run --
        spoken now, or cleaned in place (:meth:`guard_tails`) -- with
        *report* (``tts_tail.clean``'s). Nothing here (the script step); the
        assets step counts what was cut."""

    def voice_failures(self) -> str:
        return "; ".join(f"line {line_id} failed ({speaker_name(self.ec, speaker)}: {reason.rstrip('.')})"
                         for line_id, speaker, reason in self.voice_failed)

    def voice_message(self) -> str:
        """The sentence that ends a measurement with failed lines: each line,
        why, and whose voice to change -- nothing else was tried (DEC-122)."""
        ec = self.ec
        lines = "; ".join(f"{line_id} ({speaker_name(ec, speaker)}: {reason.rstrip('.')})"
                          for line_id, speaker, reason in self.voice_failed)
        advice = []
        for speaker in dict.fromkeys(speaker for _line_id, speaker, _reason in self.voice_failed):
            who = "the narrator" if speaker == "narrator" else speaker_name(ec, speaker)
            pinned = voices.voice_label(speaker_voice(ec, speaker)) is not None
            advice.append(f"pick {'another' if pinned else 'a'} voice for {who}")
        return (f"Episode {ec.ep}'s lines were not all measured: {lines}. No other voice was tried: "
                f"{_and(advice)}, then measure again.")

    def before_synthesis(self, remaining) -> None:
        """The cancel token, then the step budget with one synthesis's
        allowance; *remaining* are the lines not measured yet."""
        self.ctx.cancel.check()

        def left():
            ids = [line["line_id"] for line in remaining]
            return f"the voice measurement of line{'s' if len(ids) > 1 else ''} {_and(ids)}"

        try:
            self.budget.before_call(left, per_call=STORY_TTS_CALL_SECONDS)
        except StepFailed as exc:
            message = str(exc)
            also = "; ".join(part for part in (self.failures(), self.voice_failures()) if part)
            if also:
                message += f" Also failed in this run: {also}."
            raise BudgetSpent(message) from None

    def sync_storyboard(self) -> None:
        """The storyboard's shot durations re-timed from the lines as they
        are now, written when one moved (``shots.retime_storyboard``: plans,
        prompts, ids, revision and approval untouched). A storyboard timed
        before whole frames that this re-time switches to them (phase 5
        stage 6) has the script -- saved just before, in the old timing --
        re-timed and saved again beside it, so the two stay one timing."""
        board, ec = self.storyboard, self.ec
        if board is None or self.board_refused:
            return
        switching = not timing.board_whole_frames(board)
        if not shots.retime_storyboard(board, self.script, template=ec.template, language=ec.language,
                                       style_lock=ec.style_lock):
            return
        try:
            episode_common.write_storyboard(ec, board, self.script, now=llm_call.utc_now())
        except schemas.SchemaError as exc:
            # A storyboard that no longer fits its script is left as it is on
            # disk (said once); planning the shots again rebuilds it.
            self.board_refused = True
            self.storyboard = episode_common.read_episode(ec, STORYBOARD_DOC)
            self.ctx.on_log(f"⚠️ The storyboard's shot durations could not be re-timed "
                            f"({'; '.join(exc.errors[:2])}); run the storyboard step again.")
            return
        if switching and timing.board_whole_frames(board):
            self.save()

    def drop_other_take(self, line_id, ext) -> None:
        """A line measured again with an engine of the other format (mp3 <->
        wav) leaves its old take behind under another name: it is removed.
        A symlink in its place is refused by the store and left alone."""
        other = "wav" if ext == "mp3" else "mp3"
        try:
            path = self.ec.store.episode_asset_path(self.ec.story_id, self.ec.ep, "voice", asset_name(line_id, other))
        except KeyError:
            return
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

    def clone_reference(self, gates, char_id, name):
        """None once *char_id*'s frozen reference is there -- made now from
        its recorded pick and text when it is missing (plan 32 stage 6,
        ``voice_clone.make_reference``) -- else why it is not, in a sentence."""
        ec, ctx = self.ec, self.ctx
        if voice_clone.has_reference(ec.store, ec.story_id, char_id):
            return None
        ctx.on_log(f"🎙️ {name}: making the voice reference first")
        try:
            voice_clone.make_reference(ec.store, ec.story_id, char_id, gates=gates, on_log=ctx.on_log,
                                       cancel=ctx.cancel, now=llm_call.utc_now(), adapters=self.tools.adapters,
                                       transport=self.tools.transport)
        except (voice_clone.VoiceCloneError, voices.VoiceError) as exc:
            return str(exc)
        return None

    def measure_line(self, gates, line) -> None:
        ec, ctx = self.ec, self.ctx
        line_id, speaker = line["line_id"], line["speaker"]
        name = speaker_name(ec, speaker)
        voice = speaker_voice(ec, speaker)
        if voices.voice_label(voice) is None:
            reason = no_voice_reason(ec, speaker)
        else:
            def dest_for(ext):
                return ec.store.episode_asset_path(ec.story_id, ec.ep, "voice", asset_name(line_id, ext), create=True)

            # The hooks' defaults ask for exactly what phase 3 asked for.
            extra = {}
            cache = self.voice_cache(gates, line)
            if cache is not None:
                extra["cache"] = cache
            if self.voice_take is not None:
                extra["take"] = self.voice_take
            if self.voice_direction is not None:
                extra["direction"] = self.voice_direction
            reason = None
            if voice_reference.is_clone_voice(voice) and speaker != "narrator":
                # Plan 32 stage 6: the character's frozen reference, made here
                # once when the cast step could not (no Gemini key then), and
                # every line spoken on the character's own seed.
                reason = self.clone_reference(gates, speaker, name)
                extra["seed"] = tts.voice_seed(speaker)
            if voice_reference.is_reference_voice(voice) and speaker != "narrator":
                # The character's own recording (plan 23 stage B4); None when
                # the file is gone, which synthesize_line refuses in a sentence.
                try:
                    extra["reference"] = voice_reference.reference_path(ec.store, ec.story_id, speaker)
                except KeyError:
                    extra["reference"] = None
            if reason is not None:
                self.voice_failed.append((line_id, speaker, reason))
                ctx.on_log(f"✖ {line_id} {name}: {reason}")
                return
            try:
                spoken = voices.synthesize_line(gates, voice=voice, text=line["text"], dest_for=dest_for,
                                                on_log=ctx.on_log, cancel=ctx.cancel, step=self.measure_step,
                                                adapters=self.tools.adapters, transport=self.tools.transport,
                                                line=line, v2=media_policy.is_v2(ec.story),
                                                language=ec.language, **extra)
            except voices.VoiceError as exc:
                reason = str(exc)
                self.voice_refused(line, exc)
            else:
                line["timing"] = {
                    "source": spoken["source"], "duration_s": spoken["duration_s"],
                    "text_hash": timing.text_hash(line["text"]), "voice": spoken["voice"],
                    "audio": f"{VOICE_ASSETS}/{asset_name(line_id, spoken['ext'])}",
                }
                self.measured += 1
                if spoken.get("tail_guard"):
                    self.tail_guarded(line, spoken["tail_guard"])
                self.save()
                self.sync_storyboard()
                self.drop_other_take(line_id, spoken["ext"])
                ctx.on_log(f"🔊 {line_id} {name}: {spoken['duration_s']:.2f} s ({spoken['voice']}, "
                           f"{_HOW[spoken['source']]})")
                return
        self.voice_failed.append((line_id, speaker, reason))
        ctx.on_log(f"✖ {line_id} {name}: {reason}")

    def guard_tails(self) -> list:
        """Every measured line whose kept take the Gemini tail guard has not
        cleaned (``tts.tail_guard_due``: voiced before the guard), cleaned in
        place, in reading order -- for free, never spoken again, so its voice
        cannot change (module docstring). A line whose words were aligned
        gives the guard its last word's end. The line's measured duration
        becomes the cleaned one, and the script is re-timed and saved and
        the storyboard follows after each line (:meth:`save`,
        :meth:`sync_storyboard`: no revision moves, no approval is cleared),
        as a measurement does -- a stop never leaves the script behind a
        cleaned file. A take that cannot be read or written is left as it
        is, said. Returns the ids of the lines cleaned."""
        ec, ctx = self.ec, self.ctx
        cleaned = []
        for line in (line for scene in self.script["scenes"] for line in scene["lines"]):
            if not is_measured(ec, line):
                continue
            ctx.cancel.check()
            line_id = line["line_id"]
            audio = line["timing"]["audio"]
            try:
                audio_path = ec.store.episode_asset_path(ec.story_id, ec.ep, "voice",
                                                         audio[len(VOICE_ASSETS) + 1:])
                timing_path = ec.store.episode_asset_path(ec.story_id, ec.ep, "voice", asset_name(line_id, "json"))
            except KeyError:
                continue
            try:
                with open(timing_path, encoding="utf-8") as fh:
                    sidecar = json.load(fh)
            except (OSError, ValueError):
                continue
            if not tts.tail_guard_due(sidecar):
                continue
            try:
                new = tts.guard_kept_take(audio_path, timing_path, sidecar, speech_end_s=aligned_end(sidecar))
            except OSError as exc:
                ctx.on_log(f"⚠️ {line_id}: its static could not be cut ({exc}); the line is left as it is.")
                continue
            if new is None:
                continue
            report = new["tail_guard"]
            line["timing"]["duration_s"] = new["duration_s"]
            cleaned.append(line_id)
            self.tail_guarded(line, report)
            self.save()
            self.sync_storyboard()
            if report["trimmed_s"] > 0:
                ctx.on_log(f"🔇 {line_id} {speaker_name(ec, line['speaker'])}: {report['trimmed_s']:.2f} s of "
                           f"static cut from its end ({report['reason']}), {report['kept_s']:.2f} s kept")
        return cleaned

    def open_gates(self):
        """``voices.LineGates`` for the episode; ``StepFailed`` saying the
        lines cannot be measured (and what else failed in this run)."""
        ec, ctx = self.ec, self.ctx
        try:
            return voices.LineGates(ec.store, ec.story_id, env=ctx.settings_env, ep=ec.ep)
        except voices.VoiceError as exc:
            message = f"Episode {ec.ep}'s lines cannot be measured: {exc}"
            if self.failed:
                message += f" Also failed in this run: {self.failures()}."
            raise StepFailed(message) from None

    def measure(self, gates=None) -> None:
        """Every line :func:`lines_to_measure` names, in reading order.
        *gates* are the run's own when it opened them already (the assets
        step: its images meet the same ledger and caps); else they are
        opened here, once there is a line to speak."""
        ec, ctx = self.ec, self.ctx
        todo = lines_to_measure(ec, self.script)
        if not todo:
            ctx.on_log("🎙 Every line is measured with its pinned voice: nothing to synthesise.")
            self.sync_storyboard()
            return
        ctx.on_log(f"🎙 Measuring {len(todo)} line{'s' if len(todo) != 1 else ''} with the pinned voices")
        if gates is None:
            gates = self.open_gates()
        for index, line in enumerate(todo):
            self.before_synthesis(todo[index:])
            self.measure_line(gates, line)
        self.sync_storyboard()

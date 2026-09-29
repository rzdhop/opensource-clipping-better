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
``board_refused`` on it. Three hooks change what the assets step asks for,
and nothing when left alone (the script step): :attr:`measure_step` (the
ledger's ``step``), :meth:`voice_cache` (the generation cache a line's
request goes through, DEC-151) and :attr:`voice_take` (a voice regenerate's
take, so the cache misses on purpose); a fourth, :meth:`voice_refused`,
hands it a failed line's ``VoiceError`` (its chain failures pace a voice a
free tier held back).

The story's own document is never read for writing (RC-E2).
"""

from __future__ import annotations

import os

from .. import schemas, shots, timing, voices
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
    label = voices.voice_label(speaker_voice(ec, line["speaker"]))
    return (current["source"] in voices.MEASURED_SOURCES and current["text_hash"] == timing.text_hash(line["text"])
            and label is not None and current.get("voice") == label and _audio_kept(ec, current.get("audio")))


def lines_to_measure(ec, script) -> list:
    """Every line of *script* the measurement would synthesise, in reading order."""
    return [line for scene in script["scenes"] for line in scene["lines"] if not is_measured(ec, line)]


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

    def voice_cache(self, gates, line):
        """The generation cache *line*'s request goes through, or None (the
        script step: no cache, nothing changes, RC-A2)."""
        return None

    def voice_refused(self, line, exc) -> None:
        """*line*'s pinned voice failed with *exc* (``voices.VoiceError``;
        its ``__cause__`` is the chain's ``NoRunnableLink`` when the chain
        ran). Nothing here (the script step); the assets step keeps the
        chain's failures to pace a rate-limited voice."""

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
            try:
                spoken = voices.synthesize_line(gates, voice=voice, text=line["text"], dest_for=dest_for,
                                                on_log=ctx.on_log, cancel=ctx.cancel, step=self.measure_step,
                                                adapters=self.tools.adapters, transport=self.tools.transport,
                                                **extra)
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
                self.save()
                self.sync_storyboard()
                self.drop_other_take(line_id, spoken["ext"])
                ctx.on_log(f"🔊 {line_id} {name}: {spoken['duration_s']:.2f} s ({spoken['voice']}, "
                           f"{_HOW[spoken['source']]})")
                return
        self.voice_failed.append((line_id, speaker, reason))
        ctx.on_log(f"✖ {line_id} {name}: {reason}")

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

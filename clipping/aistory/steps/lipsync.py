"""Lip-synced clips: a bought clip's lips moved to its shot's dialogue (AI
Story, DEC-258; the human: "decide for me" -- option B, a post-process of
each clip on Kling LipSync, over Veo's native speech).

A story lipsyncs when ``media_policy.lipsync`` says so (a fully animated v2
story on the quality preset, unless its ``generation_profile.lipsync`` is
``none``). Then, once a shot's clip is current, the video phase of the
assets step (``steps/assets.py``) sends it with its **dialogue track** to
the ``LIPSYNC_CHAIN`` link (``fal/kling-lipsync``, $0.014 per started 5 s)
and keeps the answer beside the clip as ``assets/clips/shot_NN.lipsync.mp4``
-- ``assets.video`` then names it, so the render, the approval fingerprint
and the dashboard all see the lip-synced take; a lipsync that fails keeps
the plain clip in ``assets.video``, never a missing video.

**The dialogue track** (:func:`track_spec`, :func:`build_track`): a 24 kHz
mono WAV exactly as long as the clip was bought (``clip_s``), silent but for
the shot's lines whose speaker is **in its frame** (a character among the
shot's ``subject_tags``) -- a voice-over of someone off screen would move a
mouth that is not speaking, so such a line is left out; a shot with no such
line gets no track and no lipsync (the plain clip stays). Each line sits at
its offset inside the shot as ``render.timeline`` places it (the one place
that knows where a line lands in the episode: line start - shot start). A
clip the render slows to cover a longer shot (DEC-250, ``cover: stretch``,
factor f) hears its lines f times faster (``atempo``) at offsets divided by
f, so once slowed the mouths meet the voices again. The track only drives
the mouths: the lip-synced take keeps the plain clip's own sound (an
ambience clip's ambience; none for a silent clip) and never the track, so
the TTS lines stay the only voice in the mix.

**When it is current** (:func:`is_current`): its record
(``assets.clip.lipsync``, ``schemas._STORYBOARD_LIPSYNC_SCHEMA``) says
``current``, on the chain's link, made from the plain clip on disk (its
sha256) and a track whose inputs hash the same (:func:`track_spec`'s
``hash``: each line's audio sha256, offset, trim and tempo -- computed
without ffmpeg), and its file is there. A new clip, a re-voiced or re-timed
line, or another link makes it stale: it is made again (a kept answer in the
generation journal is served for free).

**No face** (:func:`no_face_reason`, :func:`is_no_face`): a link that finds
no face in a clip (Kling on fal answers 422 ``face_detection_error``, "No face
detected": a fruit head is not a face to it) refuses that clip for good. The
refusal is unbilled, so its booking is released (the generation journal's
``void``); the record says ``no_face`` against the clip's sha256 and the
link, the plain clip stays the take (DEC-258), and neither the video phase nor
the estimate asks for that clip again -- a new clip, or another link, does.

**The estimate** (:func:`lipsync_units`): every planned clip with at least
one in-frame line whose lipsync is not current (nor ``no_face`` for that
very clip), priced on the link (``lipsync.estimate_for``: rounded up to 5 s).
``clips.video_units`` adds it to the clips' ``est_usd`` when the link could
run it.

Stdlib + ffmpeg on PATH (DEC-012).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess

from clipping.providers import generation as gen
from clipping.providers import lipsync as lipsync_providers
from clipping.providers import pricing
from clipping.providers.registry import ChainError, describe

from .. import media_policy, schemas
from .. import shots as shots_mod
from ..render import timeline as timeline_mod

SAMPLE_RATE = 24000
TRACK_VERSION = 1
# A tempo is never asked below this (a shot shorter than its clip is held,
# never sped up); above, a clip slowed to cover its shot (DEC-250: at most 1.25).
_NO_TEMPO = 1.0
_ROUND = 3


class LipsyncError(Exception):
    """The dialogue track or the lip-synced take could not be made here (not
    a provider's failure): ffmpeg missing or failing, a line's audio gone."""


def _canonical_sha256(payload) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def sha256_file(path):
    """The sha256 of a regular file (never through a symlink), or None."""
    if not path or os.path.islink(path) or not os.path.isfile(path):
        return None
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


# ------------------------------------------------------------------ the files

def lipsync_name(shot_id) -> str:
    """``shot_03.lipsync.mp4`` for shot ``sh03`` (``schemas.SHOT_CLIP_NAME_PATTERN``)."""
    return f"shot_{shot_id[2:]}{schemas.SHOT_LIPSYNC_SUFFIX}.mp4"


def lipsync_rel(shot_id) -> str:
    """What ``assets.video`` holds while the lipsync is current."""
    return f"{schemas.SHOT_CLIP_DIR}/{lipsync_name(shot_id)}"


def plain_name(shot_id) -> str:
    return f"shot_{shot_id[2:]}.mp4"


def plain_rel(shot_id) -> str:
    return f"{schemas.SHOT_CLIP_DIR}/{plain_name(shot_id)}"


def _clip_file(ec, name):
    try:
        path = ec.store.episode_asset_path(ec.story_id, ec.ep, "clips", name)
    except KeyError:
        return None
    return path if os.path.isfile(path) and not os.path.islink(path) else None


def plain_clip_path(ec, shot):
    """The real path of *shot*'s bought clip (``shot_NN.mp4``), or None."""
    return _clip_file(ec, plain_name(shot["shot_id"]))


def lipsync_path(ec, shot):
    """The real path of *shot*'s lip-synced take, or None."""
    return _clip_file(ec, lipsync_name(shot["shot_id"]))


# ------------------------------------------------------------------ the lines

def in_frame(shot) -> set:
    """The character ids among *shot*'s ``subject_tags``."""
    ids = set()
    for tag in shot.get("subject_tags") or ():
        try:
            kind, entity_id, _variant = shots_mod.parse_tag(tag)
        except ValueError:
            continue
        if kind == "char":
            ids.add(entity_id)
    return ids


def spoken_lines(script, shot) -> list:
    """The lines of *shot* (``shot["lines"]``, in order) whose speaker is in
    its frame: the ones a mouth on screen says."""
    framed = in_frame(shot)
    lines = {line["line_id"]: line for scene in script["scenes"] for line in scene["lines"]}
    return [lines[line_id] for line_id in shot.get("lines") or () if line_id in lines
            and lines[line_id].get("speaker") in framed]


def line_audio(ec, line):
    """The real path of a measured line's audio (``assets.line_audio_path``), or None."""
    from . import assets  # the assets step imports this module: a cycle at import time

    return assets.line_audio_path(ec, line)


def episode_timeline(ec, script, storyboard):
    """``render.timeline.build_timeline`` of the episode as it is timed now."""
    return timeline_mod.build_timeline(script, storyboard, ec.template, ec.language, style_lock=ec.style_lock)


def tempo_of(shot, clip) -> float:
    """How much faster the track plays than the lines were spoken: the
    render's stretch of a ``cover: stretch`` clip (shot / clip length,
    DEC-250), else 1."""
    clip_s = float((clip or {}).get("clip_s") or 0.0)
    duration = float(shot.get("duration_s") or 0.0)
    if (clip or {}).get("cover") == "stretch" and clip_s and duration > clip_s:
        return round(duration / clip_s, 4)
    return _NO_TEMPO


def track_spec(ec, script, storyboard, shot, *, clip=None, timeline=None, audio_of=None):
    """What *shot*'s dialogue track is made of, calling nothing but file
    hashes (no ffmpeg), or None when no in-frame line of it has audio::

        {"clip_s", "tempo", "lines": [{"line_id", "speaker", "path", "sha256", "at_s", "from_s"}], "hash"}

    *clip* is the shot's clip record (``clip_s``, ``cover``; default its
    own), *timeline* ``render.timeline.build_timeline``'s (built when not
    given; ``timeline_mod.TimelineError`` passes through), *audio_of(line)*
    the path of a line's audio (default :func:`line_audio`). ``at_s`` is
    where the line starts in the clip; ``from_s`` how much of its audio is
    cut from its head (a line that starts before the shot does); a line
    that starts past the clip's end is left out."""
    clip = clip if clip is not None else (shot["assets"].get("clip") or {})
    clip_s = int(clip.get("clip_s") or 0)
    lines = spoken_lines(script, shot)
    if not lines or clip_s <= 0:
        return None
    audio_of = audio_of or (lambda line: line_audio(ec, line))
    timeline = timeline if timeline is not None else episode_timeline(ec, script, storyboard)
    shot_start = next(entry["start_s"] for entry in timeline["shots"] if entry["shot_id"] == shot["shot_id"])
    starts = {entry["line_id"]: entry["start_s"] for entry in timeline["lines"]}
    tempo = tempo_of(shot, clip)
    rows = []
    for line in lines:
        path = audio_of(line)
        sha = sha256_file(path)
        if sha is None or line["line_id"] not in starts:
            continue
        at = (float(starts[line["line_id"]]) - float(shot_start)) / tempo
        cut = 0.0
        if at < 0:
            cut, at = -at * tempo, 0.0
        if at >= clip_s:
            continue
        rows.append({"line_id": line["line_id"], "speaker": line["speaker"], "path": path, "sha256": sha,
                     "at_s": round(at, _ROUND), "from_s": round(cut, _ROUND)})
    if not rows:
        return None
    payload = {"v": TRACK_VERSION, "rate": SAMPLE_RATE, "clip_s": clip_s, "tempo": tempo,
               "lines": [[row["line_id"], row["sha256"], row["at_s"], row["from_s"]] for row in rows]}
    return {"clip_s": clip_s, "tempo": tempo, "lines": rows, "hash": _canonical_sha256(payload)}


def track_argv(spec, out_path) -> list:
    """The ffmpeg command that writes *spec*'s track to *out_path*: a silent
    24 kHz mono base exactly ``clip_s`` long, each line resampled to it (cut
    from its head, sped up by the tempo) and delayed to its offset, summed
    without normalising (the lines never overlap in a shot), cut to the
    clip's length, 16-bit PCM, bit-exact. Deterministic: the same spec, the
    same argv."""
    clip_s = spec["clip_s"]
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-f", "lavfi", "-t", f"{clip_s}", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono"]
    chains, labels = [], ["[0:a]"]
    for index, row in enumerate(spec["lines"], start=1):
        argv += ["-i", row["path"]]
        steps = [f"aresample={SAMPLE_RATE}", "aformat=sample_fmts=fltp:channel_layouts=mono"]
        if row["from_s"] > 0:
            steps += [f"atrim=start={row['from_s']:g}", "asetpts=PTS-STARTPTS"]
        if spec["tempo"] != _NO_TEMPO:
            steps.append(f"atempo={spec['tempo']:g}")
        steps.append(f"adelay={int(round(row['at_s'] * 1000))}:all=1")
        chains.append(f"[{index}:a]{','.join(steps)}[a{index}]")
        labels.append(f"[a{index}]")
    graph = ";".join(chains + [f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:dropout_transition=0:"
                               f"normalize=0,atrim=duration={clip_s}[out]"])
    return argv + ["-filter_complex", graph, "-map", "[out]", "-ar", str(SAMPLE_RATE), "-ac", "1",
                   "-c:a", "pcm_s16le", "-fflags", "+bitexact", "-flags:a", "+bitexact", "-map_metadata", "-1",
                   out_path]


def _ffmpeg(argv, out_path, what, run):
    run = run or subprocess.run
    try:
        result = run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise LipsyncError(f"ffmpeg is needed to {what} and cannot run ({exc})") from None
    if result.returncode != 0 or not os.path.isfile(out_path):
        detail = (getattr(result, "stderr", "") or "").strip()[-200:]
        raise LipsyncError(f"ffmpeg could not {what} (exit {result.returncode}{': ' + detail if detail else ''})")


def build_track(spec, out_path, *, run=None) -> dict:
    """*spec*'s track written to *out_path* (:func:`track_argv`):
    ``{"path", "sha256", "lines": [line_id, ...], "hash"}``.
    :class:`LipsyncError` when ffmpeg is missing or fails."""
    _ffmpeg(track_argv(spec, out_path), out_path, "build the dialogue track", run)
    return {"path": out_path, "sha256": sha256_file(out_path), "lines": [row["line_id"] for row in spec["lines"]],
            "hash": spec["hash"]}


def dialogue_track(ec, script, storyboard, shot, out_path, *, clip=None, timeline=None, run=None):
    """*shot*'s dialogue track (module docstring) written to *out_path*, or
    None when it has no in-frame line with audio (no track, no lipsync)."""
    spec = track_spec(ec, script, storyboard, shot, clip=clip, timeline=timeline)
    return None if spec is None else build_track(spec, out_path, run=run)


def remux_argv(synced, plain, out_path, *, plain_has_audio) -> list:
    """The ffmpeg command that writes the lip-synced take: the answer's
    picture, copied, with the plain clip's own sound (copied) or none -- the
    track that drove the mouths is never kept."""
    argv = ["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-y", "-i", synced]
    if plain_has_audio:
        argv += ["-i", plain, "-map", "0:v:0", "-map", "1:a:0", "-c", "copy"]
    else:
        argv += ["-map", "0:v:0", "-c", "copy", "-an"]
    return argv + ["-map_metadata", "-1", "-movflags", "+faststart", out_path]


def remux(synced, plain, out_path, *, plain_has_audio, run=None) -> str:
    _ffmpeg(remux_argv(synced, plain, out_path, plain_has_audio=plain_has_audio), out_path,
            "keep the clip's own sound under the lip-synced picture", run)
    return out_path


# ------------------------------------------------------------------ the state

def recorded(shot):
    """*shot*'s lipsync record, or None."""
    return (shot["assets"].get("clip") or {}).get("lipsync")


def is_current(ec, script, storyboard, shot, *, link, timeline=None, spec=None) -> bool:
    """Whether *shot*'s lipsync is current (module docstring) for the link
    labelled *link*. *spec* (:func:`track_spec`) is computed when not given."""
    record = recorded(shot)
    clip = shot["assets"].get("clip") or {}
    if not record or record.get("state") != "current" or clip.get("state") != "current":
        return False
    if record.get("link") != link or lipsync_path(ec, shot) is None:
        return False
    if record.get("clip_sha256") != sha256_file(plain_clip_path(ec, shot)):
        return False
    if spec is None:
        try:
            spec = track_spec(ec, script, storyboard, shot, timeline=timeline)
        except (timeline_mod.TimelineError, KeyError, ValueError, StopIteration):
            return False
    return spec is not None and record.get("track_hash") == spec["hash"]


# A face-detection refusal as the chain reports it: fal's error type, or
# Kling's message when the type was not kept.
NO_FACE = "no_face"
NO_FACE_MARKERS = ("face_detection_error", "no face detected")


def no_face_reason(failures):
    """The ``"<link>: <reason>"`` of the chain failure (``NoRunnableLink.failures``)
    that says the link found no face in the clip -- an HTTP 422 naming
    ``face_detection_error`` or "No face detected" -- or None."""
    for label, reason in failures or ():
        text = str(reason)
        lower = text.lower()
        if "422" in text and any(marker in lower for marker in NO_FACE_MARKERS):
            return f"{label}: {text}"
    return None


def is_no_face(ec, shot, *, link) -> bool:
    """Whether *shot*'s lipsync on the link labelled *link* ended ``no_face``
    for the very clip on disk (its sha256): then it is never asked again."""
    record = recorded(shot)
    clip = shot["assets"].get("clip") or {}
    if not record or record.get("state") != NO_FACE or clip.get("state") != "current":
        return False
    if record.get("link") != link:
        return False
    sha = sha256_file(plain_clip_path(ec, shot))
    return sha is not None and record.get("clip_sha256") == sha


# ------------------------------------------------------------------ the link

def lipsync_link(merged):
    """The first link of LIPSYNC_CHAIN (*merged*: the settings over the
    environment); ``ChainError`` when it cannot be read."""
    chain = gen.chain_from_env(gen.LIPSYNC, merged)
    if not chain:
        raise ChainError(f"{gen.ENV_NAMES[gen.LIPSYNC]} names no link")
    return chain[0]


def link_status(merged, adapters, *, allow_paid) -> dict:
    """``{"link", "available", "ready", "reason"}`` of the lipsync link,
    calling nothing: *available* -- it has an adapter, its keys and a price
    per second; *ready* -- and ``allow_paid`` is on (a lipsync is paid)."""
    try:
        link = lipsync_link(merged)
    except ChainError as exc:
        return {"link": None, "available": False, "ready": False, "reason": str(exc)}
    label = describe(link)
    reason = None
    if gen.adapter_for(gen.LIPSYNC, link.provider, adapters) is None:
        reason = f"no adapter for {link.provider} lipsync"
    elif gen.missing_keys(link, merged):
        reason = f"{' and '.join(gen.missing_keys(link, merged))} not set"
    else:
        try:
            price = pricing.price_for(link)
        except pricing.PriceUnknown as exc:
            reason = str(exc)
        else:
            if price.unit != "second":
                reason = f"priced per {price.unit}, not per second"
    available = reason is None
    ready = available and bool(allow_paid)
    if available and not ready:
        reason = "allow_paid is off"
    return {"link": label, "available": available, "ready": ready, "reason": reason}


def lipsync_units(ec, script, storyboard, plan_rows, *, new_ids, merged, adapters, allow_paid) -> dict:
    """The lipsync part of the clips' estimate (``clips.video_units``)::

        {"link", "count", "seconds", "est_usd", "plan": [{"shot_id", "clip_s", "billed_s", "est_usd"}],
         "current": n, "skipped": [shot_id, ...], "no_face": [shot_id, ...], "available", "ready", "reason",
         "counted"}

    over the plan's rows (*plan_rows*: ``{"shot_id", "clip_s", ...}``):
    every planned clip with at least one in-frame line (:func:`spoken_lines`
    -- the lines' audio need not exist yet: the fast track prices it before
    the voices) is lipsynced, unless it is a clip kept (not in *new_ids*,
    the clips to buy or collect) whose lipsync is current. ``skipped``:
    the planned shots with no in-frame line; ``no_face``: the kept clips the
    link found no face in (:func:`is_no_face`), never asked again. ``counted``: the price belongs
    in the clips' ``est_usd`` -- the link could run it (``available``),
    even while ``allow_paid`` is off (priced, as a refused clip is)."""
    status = link_status(merged, adapters, allow_paid=allow_paid)
    by_id = {shot["shot_id"]: shot for shot in storyboard["shots"]}
    units = {"link": status["link"], "count": 0, "seconds": 0, "est_usd": 0.0, "plan": [], "current": 0,
             "skipped": [], "no_face": [], "available": status["available"], "ready": status["ready"],
             "reason": status["reason"], "counted": False}
    timeline = None
    for row in plan_rows:
        shot = by_id.get(row["shot_id"])
        if shot is None:
            continue
        if not spoken_lines(script, shot):
            units["skipped"].append(row["shot_id"])
            continue
        if row["shot_id"] not in new_ids and status["link"]:
            if is_no_face(ec, shot, link=status["link"]):
                units["no_face"].append(row["shot_id"])
                continue
            if timeline is None:
                try:
                    timeline = episode_timeline(ec, script, storyboard)
                except (timeline_mod.TimelineError, KeyError, ValueError):
                    timeline = False
            if timeline and is_current(ec, script, storyboard, shot, link=status["link"], timeline=timeline):
                units["current"] += 1
                continue
        billed = lipsync_providers.billed_seconds(row["clip_s"])
        usd = 0.0
        if status["link"]:
            try:
                usd = float(pricing.price_for(lipsync_link(merged)).usd) * billed
            except (pricing.PriceUnknown, ChainError):
                usd = 0.0
        units["plan"].append({"shot_id": row["shot_id"], "clip_s": int(row["clip_s"]), "billed_s": billed,
                              "est_usd": round(usd, 4)})
    units["count"] = len(units["plan"])
    units["seconds"] = sum(item["billed_s"] for item in units["plan"])
    units["est_usd"] = round(sum(item["est_usd"] for item in units["plan"]), 4)
    units["counted"] = bool(units["count"] and status["available"])
    return units


def counted_usd(video) -> float:
    """The lipsync dollars inside a ``video`` part's ``est_usd`` (0 when none)."""
    part = (video or {}).get("lipsync") or {}
    return float(part.get("est_usd") or 0.0) if part.get("counted") else 0.0


def counted_count(video) -> int:
    part = (video or {}).get("lipsync") or {}
    return int(part.get("count") or 0) if part.get("counted") else 0


def clause(video) -> str:
    """`` + $0.280 lip-sync (8 clips)`` for a ``video`` part whose lipsync is
    counted, else ''."""
    usd, count = counted_usd(video), counted_count(video)
    if not count:
        return ""
    return f" + ${usd:.3f} lip-sync ({count} clip{'' if count == 1 else 's'})"


def policy_on(ec) -> bool:
    return media_policy.lipsync(ec.story)

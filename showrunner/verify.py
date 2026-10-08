"""Quick checks of a generated clip, with ffmpeg/ffprobe only (no model, no GPU).

- :func:`probe` -> duration, size, fps, whether an audio stream exists, its sample rate.
- :func:`loudness` -> mean/max volume of the audio (``volumedetect``), to catch silent takes.
- :func:`contact_sheet` -> one jpg of N frames spread over the clip, to look at in the chat.
- :func:`extract_audio` -> the clip's sound as a mono 24 kHz wav (a voice reference, or the
  input of an STT check later).

- :func:`verify_take` -> the clip check (plan 36 §2.2, stage 1.4): the clip's own audio transcribed on
  this host's CPU (faster-whisper, decoded by ffmpeg) and aligned to its scripted lines: share of each
  line's words heard, where the lines start and end, whether they come in order, whether the last word
  ends before the clip does.

Only :func:`transcribe` needs a model (``faster_whisper``, imported lazily); everything else is ffmpeg
or pure Python, so stage 0 and the tests run without it.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import unicodedata


def _run(cmd: list, *, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=capture, text=True, check=False)


def probe(path: str) -> dict:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path])
    if out.returncode:
        raise RuntimeError(f"ffprobe failed on {path}: {out.stderr.strip()[:300]}")
    info = json.loads(out.stdout)
    video = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    audio = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    fps = None
    if video and video.get("avg_frame_rate", "0/0") not in ("0/0", "0"):
        num, den = video["avg_frame_rate"].split("/")
        fps = round(int(num) / max(int(den), 1), 3)
    return {
        "duration_s": round(float(info["format"].get("duration") or 0), 3),
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "fps": fps,
        "frames": int(video.get("nb_frames") or 0) if video else 0,
        "has_audio": audio is not None,
        "sample_rate": int(audio["sample_rate"]) if audio else None,
        "size_mb": round(os.path.getsize(path) / 1e6, 2),
    }


def loudness(path: str) -> dict:
    out = _run(["ffmpeg", "-hide_banner", "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"])
    text = out.stderr
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", text)
    peak = re.search(r"max_volume:\s*(-?[\d.]+) dB", text)
    return {"mean_db": float(mean.group(1)) if mean else None, "max_db": float(peak.group(1)) if peak else None,
            "silent": (not mean) or float(mean.group(1)) < -50}


def contact_sheet(path: str, dest: str, *, frames: int = 8, width: int = 270) -> str:
    """*frames* frames spread evenly over the clip, tiled in one row, written to *dest* (jpg)."""
    info = probe(path)
    total = max(info["frames"], 1)
    step = max(total // frames, 1)
    vf = f"select='not(mod(n\\,{step}))',scale={width}:-2,tile={frames}x1"
    out = _run(["ffmpeg", "-hide_banner", "-y", "-i", path, "-vf", vf, "-frames:v", "1", "-q:v", "4", dest])
    if out.returncode:
        raise RuntimeError(f"contact sheet failed: {out.stderr.strip()[-300:]}")
    return dest


def extract_audio(path: str, dest: str, *, start_s: float = 0.0, max_s: float | None = None,
                  sample_rate: int = 24000) -> str:
    """The clip's audio as mono 16-bit wav at *sample_rate* (what Chatterbox and LTX's
    reference-audio node expect)."""
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", path, "-vn", "-ac", "1", "-ar", str(sample_rate),
           "-acodec", "pcm_s16le"]
    if start_s:
        cmd += ["-ss", str(start_s)]
    if max_s:
        cmd += ["-t", str(max_s)]
    out = _run(cmd + [dest])
    if out.returncode:
        raise RuntimeError(f"audio extraction failed: {out.stderr.strip()[-300:]}")
    return dest


def silences(path: str, *, noise_db: float = -35.0, min_s: float = 0.3) -> list:
    """``[(start_s, end_s)]`` of the pauses in *path* (ffmpeg silencedetect); a pause running to
    the end of the file ends at its duration."""
    out = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-af",
                f"silencedetect=noise={noise_db}dB:d={min_s}", "-f", "null", "-"]).stderr
    starts = [float(x) for x in re.findall(r"silence_start: (-?[0-9.]+)", out)]
    ends = [float(x) for x in re.findall(r"silence_end: ([0-9.]+)", out)]
    if len(ends) < len(starts):
        ends.append(probe(path)["duration_s"])
    return list(zip(starts, ends))


def line_cut_s(text: str, pauses: list, duration_s: float, *, words_per_s: float = 2.4,
               floor: float = 0.7, cap: float = 1.6, pad_s: float = 0.15) -> float:
    """Where a TTS line ends: the first pause that starts after *floor* of the line's expected
    length (words / *words_per_s*), plus *pad_s*; with no such pause, *cap* times the expected
    length. Chatterbox has no length limit but its 1000-token cap (≈ 40 s) and keeps talking
    after the line (2026-10-08: a 9-word line came back 40 s long, the line in its first 2.9 s)."""
    words = re.findall(r"[^\W_][\w'’-]*", text)  # a French " ?" or " !" is not a word
    expected = max(1.0, len(words) / words_per_s)
    for start, _ in pauses:
        if start >= floor * expected:
            return round(min(start + pad_s, duration_s), 3)
    return round(min(duration_s, cap * expected + pad_s), 3)


def concat_audio(parts: list, dest: str, *, gap_s: float = 0.35, sample_rate: int = 24000) -> str:
    """Join wav *parts* with *gap_s* of silence between them (a two-speaker exchange for A2V)."""
    inputs = []
    filters = []
    for k, p in enumerate(parts):
        inputs += ["-i", p]
        filters.append(f"[{k}:a]aresample={sample_rate},aformat=channel_layouts=mono,apad=pad_dur={gap_s}[a{k}]")
    chain = "".join(f"[a{k}]" for k in range(len(parts)))
    graph = ";".join(filters) + f";{chain}concat=n={len(parts)}:v=0:a=1[out]"
    out = _run(["ffmpeg", "-hide_banner", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
                "-ar", str(sample_rate), "-ac", "1", "-acodec", "pcm_s16le", dest])
    if out.returncode:
        raise RuntimeError(f"audio concat failed: {out.stderr.strip()[-300:]}")
    return dest


def report(path: str) -> dict:
    info = probe(path)
    info.update(loudness(path) if info["has_audio"] else {"mean_db": None, "max_db": None, "silent": True})
    return info


# ------------------------------------------------------------------ the clip check (STT)

STT_MODEL = os.environ.get("SHOWRUNNER_STT_MODEL", "large-v3")
STT_RATE = 16000
MIN_MATCHED = 0.75   # share of a line's words that must be heard (the app's native_speech rule)
END_MARGIN_S = 0.1   # the last word must end this long before the clip does
_MODELS: dict = {}


def tokens(text: str) -> list:
    """Words for matching: lowercase, accents and apostrophes dropped (``C'est`` -> ``c``, ``est``, the
    way whisper splits elisions), punctuation gone."""
    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(ch for ch in plain if not unicodedata.combining(ch))
    return re.findall(r"[a-z0-9]+", re.sub(r"['’`]", " ", plain))


def decode_audio(path: str, *, sample_rate: int = STT_RATE):
    """The audio of *path* as a mono float32 numpy array at *sample_rate* (ffmpeg, not PyAV: the
    venv's ``av`` breaks faster-whisper's own decoder)."""
    import numpy as np

    out = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-i", path, "-vn", "-f", "f32le", "-ac", "1",
                          "-ar", str(sample_rate), "-"], capture_output=True, check=False)
    if out.returncode:
        raise RuntimeError(f"audio decode failed on {path}: {out.stderr.decode(errors='replace')[-300:]}")
    return np.frombuffer(out.stdout, dtype=np.float32)


def _model(size: str):
    if size not in _MODELS:
        from faster_whisper import WhisperModel  # lazy: the rest of the module needs no model

        _MODELS[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _MODELS[size]


def transcribe(path: str, language: str, *, model: str | None = None) -> list:
    """``[{"word", "start", "end", "prob"}]`` heard in *path* (faster-whisper on the CPU, word timings,
    no text hint: the check must hear what the clip says, not what the script says)."""
    segments, _ = _model(model or STT_MODEL).transcribe(decode_audio(path), language=language, beam_size=5,
                                                        word_timestamps=True, vad_filter=False)
    return [{"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3),
             "prob": round(w.probability, 3)} for seg in segments for w in seg.words]


def _heard_tokens(words: list) -> list:
    """Each heard token with the timing of the word it came from."""
    return [(tok, w["start"], w["end"]) for w in words for tok in tokens(w["word"])]


def align(lines: list, words: list) -> list:
    """Align the scripted *lines* (texts, in order) to the heard *words* in one pass (a later line's
    repeated word cannot be taken by an earlier one). Per line: ``{"matched", "words", "heard",
    "start_s", "end_s"}``; start/end are None when nothing of the line was heard."""
    heard = _heard_tokens(words)
    script, owner = [], []
    for k, text in enumerate(lines):
        toks = tokens(text)
        script += toks
        owner += [k] * len(toks)
    blocks = difflib.SequenceMatcher(None, script, [t for t, _, _ in heard], autojunk=False).get_matching_blocks()
    pairs = [(b.a + d, b.b + d) for b in blocks for d in range(b.size)]
    out = []
    for k, text in enumerate(lines):
        mine = [j for i, j in pairs if owner[i] == k]
        n = len(tokens(text))
        out.append({"text": text, "words": n, "heard": len(mine), "matched": round(len(mine) / n, 3) if n else 0.0,
                    "start_s": heard[min(mine)][1] if mine else None, "end_s": heard[max(mine)][2] if mine else None})
    return out


def verify_take(clip: str, lines: list, language: str, *, words: list | None = None,
                duration_s: float | None = None) -> dict:
    """The clip check of one take. *lines*: ``[(speaker, text), ...]`` in the scripted order.

    ``state``: ``ok`` (every line ≥ 75 % heard, in order, the last word ending ≥ 0.1 s before the clip
    ends) · ``mismatch`` (a line not heard well enough, or out of order) · ``late`` (the speech runs into
    the clip's last 0.1 s: the line may be cut) · ``no_speech``. ``start_s``/``end_s`` frame the scripted
    speech (the assembly trims after ``end_s``); ``extra_after_s`` is speech heard after the last line.
    """
    if duration_s is None:
        duration_s = probe(clip)["duration_s"]
    if words is None:
        words = transcribe(clip, language)
    per_line = align([text for _, text in lines], words)
    for (speaker, _), row in zip(lines, per_line):
        row["speaker"] = speaker
    verdict = {"state": "ok", "matched": min((r["matched"] for r in per_line), default=0.0), "lines": per_line,
               "duration_s": duration_s, "heard_text": " ".join(w["word"] for w in words), "in_order": True,
               "start_s": None, "end_s": None, "extra_after_s": 0.0}
    if not words:
        verdict["state"] = "no_speech"
        return verdict
    spans = [(r["start_s"], r["end_s"]) for r in per_line if r["start_s"] is not None]
    verdict["in_order"] = all(spans[k][0] >= spans[k - 1][1] - 0.05 for k in range(1, len(spans)))
    if spans:
        verdict["start_s"], verdict["end_s"] = spans[0][0], max(e for _, e in spans)
        after = [w for w in words if w["start"] >= verdict["end_s"]]
        verdict["extra_after_s"] = round(after[-1]["end"] - after[0]["start"], 3) if after else 0.0
    if verdict["matched"] < MIN_MATCHED or not verdict["in_order"]:
        verdict["state"] = "mismatch"
    elif verdict["end_s"] > duration_s - END_MARGIN_S + 1e-9:
        verdict["state"] = "late"
    return verdict


# ------------------------------------------------------------------ per-speaker parts (multi-speaker voice conversion)

def speaker_parts(verdict: dict, duration_s: float) -> list:
    """``[(speaker, start_s, end_s)]`` tiling the whole clip, one part per scripted line, cut in the middle
    of the pause between two lines (so a part never clips a word and the parts rejoin to the same length).
    ``ValueError`` when a line was not heard or the lines overlap: such a take cannot be split by speaker."""
    lines = verdict.get("lines") or []
    if not lines:
        raise ValueError("no lines in the verdict")
    missing = [r.get("speaker", "?") for r in lines if r.get("start_s") is None]
    if missing:
        raise ValueError(f"line(s) of {', '.join(missing)} not heard: cannot split this take by speaker")
    for a, b in zip(lines, lines[1:]):
        if b["start_s"] < a["end_s"]:
            raise ValueError(f"{a['speaker']} and {b['speaker']} overlap ({a['end_s']} > {b['start_s']})")
    cuts = [0.0] + [round((a["end_s"] + b["start_s"]) / 2, 3) for a, b in zip(lines, lines[1:])] + [float(duration_s)]
    return [(r["speaker"], cuts[k], cuts[k + 1]) for k, r in enumerate(lines)]


def join_parts(parts: list, dest: str, *, sample_rate: int = 24000) -> str:
    """Join ``[(path, seconds)]`` back to back, each padded or trimmed to exactly *seconds* (a converted
    part can come back a few ms longer), so every line lands where it was in the clip."""
    inputs, filters = [], []
    for k, (path, seconds) in enumerate(parts):
        inputs += ["-i", path]
        filters.append(f"[{k}:a]aresample={sample_rate},aformat=channel_layouts=mono,apad,"
                       f"atrim=duration={seconds:.4f},asetpts=PTS-STARTPTS[p{k}]")
    chain = "".join(f"[p{k}]" for k in range(len(parts)))
    graph = ";".join(filters) + f";{chain}concat=n={len(parts)}:v=0:a=1[out]"
    out = _run(["ffmpeg", "-hide_banner", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
                "-ar", str(sample_rate), "-ac", "1", "-acodec", "pcm_s16le", dest])
    if out.returncode:
        raise RuntimeError(f"join failed: {out.stderr.strip()[-300:]}")
    return dest

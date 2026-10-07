"""Render an episode from a cut sheet (plan 35): clips in order, each trimmed
to a length, the voice lines laid at their offsets, a hook text on the first
seconds, burned subtitles and an end card. The generic form of the ep01 edit
(``tools/edit_faille_ep01.py``), driven by JSON so the chat can write one:

    {"fps": 16, "width": 480, "height": 832,
     "hook": {"text": "Il a hacké sa boîte… et son cœur ?", "seconds": 2.5},
     "segments": [
        {"clip": "faille_damour/ep01/talk/l01_rida.mp4", "seconds": 3.1,
         "lines": [{"id": "l01", "wav": "faille_damour/ep01/voices/l01_rida.wav",
                    "text": "Vaulta… t'as laissé la porte grande ouverte.", "at": 0.25}]},
        {"clip": "faille_damour/ep01/clips_v2/clip05.mp4", "seconds": 2.0}],
     "card": {"lines": ["Team Rida", "ou Team Marie-Jeanne ?", "Partie 2 demain"], "seconds": 1.5}}

Paths are relative to the outputs dir (or absolute). A segment without
``seconds`` keeps its clip's whole length; ``at`` is the line's start inside
its segment (default 0.25 s). Two ffmpeg passes (voices mixed first).

    python tools/episode_cut.py <sheet.json> <out.mp4> [--outputs-dir DIR]

Stdlib + ffmpeg/ffprobe. Every segment is a real clip; the card is a title card.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = {"fps": 16, "line_at": 0.25, "card_seconds": 1.5, "hook_seconds": 2.5}


class CutError(Exception):
    """The sheet cannot be rendered; the message says why."""


def run(argv: list) -> None:
    done = subprocess.run(argv, capture_output=True, text=True)
    if done.returncode != 0:
        raise CutError(f"ffmpeg failed: {done.stderr[-1500:]}")


def probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,nb_frames,avg_frame_rate:format=duration", "-of", "json", path],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise CutError(f"ffprobe could not read {path}")
    info = json.loads(out.stdout)
    stream = info["streams"][0]
    num, den = (stream.get("avg_frame_rate") or "16/1").split("/")
    return {"width": int(stream["width"]), "height": int(stream["height"]),
            "duration": float(info["format"]["duration"]), "fps": float(num) / float(den or 1)}


def wav_seconds(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate()


def resolve(path: str, outputs_dir: str) -> str:
    full = path if os.path.isabs(path) else os.path.join(outputs_dir, path)
    if not os.path.isfile(full):
        raise CutError(f"no such file: {path}")
    return full


def ass_time(seconds: float) -> str:
    h, rem = divmod(max(0.0, seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def timeline(sheet: dict, outputs_dir: str) -> tuple:
    """``(segments, lines, total_s)`` on the episode's clock."""
    segments, lines, t = [], [], 0.0
    for i, seg in enumerate(sheet.get("segments") or []):
        path = resolve(seg["clip"], outputs_dir)
        info = probe(path)
        seconds = float(seg.get("seconds") or info["duration"])
        if seconds > info["duration"] + 0.05:
            raise CutError(f"segment {i} asks {seconds} s of a {info['duration']:.2f}-s clip ({seg['clip']}); a clip is "
                           f"never slowed or frozen -- make a longer one")
        segments.append({"path": path, "start": t, "seconds": round(seconds, 3), **info})
        for line in seg.get("lines") or []:
            wav = resolve(line["wav"], outputs_dir)
            at = t + float(line.get("at", DEFAULTS["line_at"]))
            dur = wav_seconds(wav)
            if at + dur > t + seconds + 0.3:
                print(f"warning: line {line.get('id')} ends {at + dur - t - seconds:.1f} s after segment {i}",
                      file=sys.stderr)
            lines.append({"id": line.get("id"), "wav": wav, "start": round(at, 3), "end": round(at + dur, 3),
                          "text": line.get("text") or ""})
        t += seconds
    if not segments:
        raise CutError("the sheet has no segments")
    return segments, lines, round(t, 3)


def write_ass(lines: list, width: int, height: int, path: str, *, hook: dict | None = None,
              card: dict | None = None, card_start: float | None = None) -> None:
    """The burned text: the lines (bottom), the hook (top, on an opaque box,
    the first seconds) and the end card's rows (centred on the card). libass
    renders UTF-8 whole; ffmpeg 6.1's drawtext drops accented lines' tails."""
    line_size, hook_size, card_size = int(height * 0.034), int(height * 0.046), int(height * 0.042)
    margin = int(width * 0.06)
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Line,DejaVu Sans,{line_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,"
            f"100,100,0,0,1,3,1,2,{margin},{margin},{int(height * 0.14)},1\n"
            f"Style: Hook,DejaVu Sans,{hook_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H70000000,-1,0,0,0,"
            f"100,100,0,0,3,{int(hook_size * 0.45)},0,8,{margin},{margin},{int(height * 0.10)},1\n"
            f"Style: Card,DejaVu Sans,{card_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,"
            f"100,100,0,0,1,0,0,5,{margin},{margin},0,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    rows = [f"Dialogue: 0,{ass_time(l['start'])},{ass_time(l['end'] + 0.15)},Line,,0,0,0,,{l['text']}"
            for l in lines if l["text"]]
    if hook and hook.get("text"):
        seconds = float(hook.get("seconds") or DEFAULTS["hook_seconds"])
        text = "\\N".join(wrap_words(hook["text"], max(10, int(width / (hook_size * 0.56)))))
        rows.append(f"Dialogue: 1,{ass_time(0)},{ass_time(seconds)},Hook,,0,0,0,,{text}")
    if card and card_start is not None:
        seconds = float(card.get("seconds") or DEFAULTS["card_seconds"])
        card_rows = list(card.get("lines") or [])
        if card_rows:
            body = "\\N".join(card_rows[:-1])
            last = card_rows[-1]
            text = (body + "\\N{\\c&H66D1FF&}" + last) if body else last
            rows.append(f"Dialogue: 1,{ass_time(card_start)},{ass_time(card_start + seconds)},Card,,0,0,0,,{text}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head + "\n".join(rows) + "\n")


def mix_voices(lines: list, total_s: float, out_wav: str) -> None:
    argv = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", f"{total_s:.3f}", "-i", "anullsrc=r=24000:cl=mono"]
    parts, labels = [], ["[0:a]"]
    for i, line in enumerate(lines, start=1):
        argv += ["-i", line["wav"]]
        ms = int(round(line["start"] * 1000))
        parts.append(f"[{i}:a]adelay={ms}|{ms}[d{i}]")
        labels.append(f"[d{i}]")
    graph = (";".join(parts) + ";" if parts else "") + "".join(labels) + \
        f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0,atrim=0:{total_s:.3f},asetpts=N/SR/TB[a]"
    argv += ["-filter_complex", graph, "-map", "[a]", "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", out_wav]
    run(argv)


def end_card(card: dict, width: int, height: int, fps: int, path: str) -> float:
    """The title card's background: a near-black clip; its rows are ASS events."""
    seconds = float(card.get("seconds") or DEFAULTS["card_seconds"])
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", str(seconds), "-i",
         f"color=c=0x0B0B10:s={width}x{height}:r={fps}", "-vf", "format=yuv420p", "-c:v", "libx264", "-crf", "18",
         "-r", str(fps), path])
    return seconds


def wrap_words(text: str, width_chars: int) -> list:
    """*text* on lines of at most *width_chars* characters, cut between words."""
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > width_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [text]


def render(sheet: dict, out: str, *, outputs_dir: str) -> dict:
    fps = int(sheet.get("fps") or DEFAULTS["fps"])
    segments, lines, total_s = timeline(sheet, outputs_dir)
    width = int(sheet.get("width") or segments[0]["width"])
    height = int(sheet.get("height") or segments[0]["height"])
    work = os.path.splitext(out)[0]
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    voices_wav, ass_path, card_mp4 = work + "_voices.wav", work + ".ass", work + "_card.mp4"
    card_s = 0.0
    inputs, chains, concat = [], [], []
    for i, seg in enumerate(segments):
        inputs += ["-i", seg["path"]]
        chains.append(f"[{i}:v]trim=duration={seg['seconds']},setpts=PTS-STARTPTS,"
                      f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                      f"fps={fps}[s{i}]")
        concat.append(f"[s{i}]")
    n = len(segments)
    if sheet.get("card"):
        card_s = end_card(sheet["card"], width, height, fps, card_mp4)
        inputs += ["-i", card_mp4]
        chains.append(f"[{n}:v]fps={fps}[s{n}]")
        concat.append(f"[s{n}]")
        n += 1
    mix_voices(lines, total_s + card_s, voices_wav)
    write_ass(lines, width, height, ass_path, hook=sheet.get("hook"), card=sheet.get("card"),
              card_start=total_s if sheet.get("card") else None)
    inputs += ["-i", voices_wav]
    graph = (";".join(chains) + ";" + "".join(concat) + f"concat=n={len(concat)}:v=1:a=0,"
             f"ass={ass_path},format=yuv420p[v]")
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", f"{n}:a",
         "-c:v", "libx264", "-crf", "19", "-preset", "medium", "-pix_fmt", "yuv420p", "-r", str(fps),
         "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out])
    for leftover in (voices_wav, card_mp4):
        try:
            os.unlink(leftover)
        except OSError:
            pass
    return {"out": out, "duration_s": round(probe(out)["duration"], 2), "segments": len(segments),
            "lines": len(lines), "subtitles": ass_path, "size_bytes": os.path.getsize(out)}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Render an episode from a cut sheet (JSON).")
    parser.add_argument("sheet")
    parser.add_argument("out")
    parser.add_argument("--outputs-dir", default=os.path.join(ROOT, "outputs"))
    args = parser.parse_args(argv)
    with open(args.sheet, encoding="utf-8") as fh:
        sheet = json.load(fh)
    try:
        print(render(sheet, args.out, outputs_dir=args.outputs_dir))
    except CutError as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()

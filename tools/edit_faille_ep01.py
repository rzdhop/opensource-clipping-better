"""The final edit of "Faille d'amour" episode 1 (plan 34, 2026-10-07): the ten
shots' clips in order (a shot may carry a second, continuation clip ``<name>b``
made from the first one's last frame), the 13 Gemini voice lines laid on their
shots one after the other, the lines burned as subtitles (ASS), and a 1.5-s end
card with the Team question and "Partie 2 demain" (the fruit-drama format's
CTA). Two ffmpeg passes: the voice track is mixed first (one ``amix`` over many
delayed inputs inside the video graph overflowed its frame queue), then the
clips are concatenated at 16 fps and muxed with it.

    python tools/edit_faille_ep01.py <clips_dir> <out.mp4> [--no-card]

Stdlib + ffmpeg/ffprobe only. Every shot is a real clip; the card is a title
card, not a shot.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VOICES = os.path.join(ROOT, "outputs", "faille_damour", "ep01", "voices")
FPS = 16
LINE_LEAD_S = 0.3      # a line starts this long after its shot's first frame
LINE_GAP_S = 0.25      # between two lines of one shot
CARD_S = 1.5
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

SHOTS = ("clip01", "clip02", "clip03", "clip04", "clip05", "clip06", "clip07", "clip08", "clip09", "clip10")
# The clip file a shot's first part comes from when a better take exists under another name.
TAKES = {"clip06": ("clip06c", "clip06")}
LINES_ON = {
    "clip01": ["l01_rida"], "clip02": ["l02_marie_jeanne", "l03_rida"], "clip03": ["l04_marie_jeanne", "l05_rida"],
    "clip04": ["l06_paloma", "l07_marie_jeanne"], "clip05": ["l08_leonardo"], "clip06": ["l09_marie_jeanne"],
    "clip07": ["l10_leonardo"], "clip08": ["l11_marie_jeanne"], "clip09": ["l12_don_maximiliano"],
    "clip10": ["l13_paloma"],
}
TEXTS = {
    "l01_rida": "Vaulta… t'as laissé la porte grande ouverte.",
    "l02_marie_jeanne": "Pardon ! Je signe le contrat de l'année dans une heure !",
    "l03_rida": "Et vous êtes en retard même sur le café ?",
    "l04_marie_jeanne": "Marie-Jeanne. Retenez le nom, il sera dans les journaux.",
    "l05_rida": "Rida. Le mien aussi, peut-être.",
    "l06_paloma": "Tu souris à ton téléphone. C'est qui, le kiwi ?",
    "l07_marie_jeanne": "Personne ! La démo, Paloma.",
    "l08_leonardo": "Ce contrat, c'est ma promotion. Zéro surprise.",
    "l09_marie_jeanne": "Non, non, non… pas maintenant !",
    "l10_leonardo": "Trouve ce hacker. Et fais-le taire.",
    "l11_marie_jeanne": "R… comme Rida ?",
    "l12_don_maximiliano": "Enfin… quelqu'un a trouvé ma porte.",
    "l13_paloma": "Alors… Team Rida ou Team Marie-Jeanne ?",
}
CARD_LINES = ("Team Rida", "ou Team Marie-Jeanne ?", "Partie 2 demain")


def run(argv: list) -> None:
    done = subprocess.run(argv, capture_output=True, text=True)
    if done.returncode != 0:
        sys.exit(f"ffmpeg failed:\n{' '.join(argv)}\n{done.stderr[-2000:]}")


def probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height,nb_frames:format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout.split()
    w, h, frames = out[0].split(",")[:3]
    return {"width": int(w), "height": int(h), "frames": int(frames), "duration": float(out[1])}


def wav_seconds(path: str) -> float:
    with wave.open(path, "rb") as w:
        return w.getnframes() / w.getframerate()


def shot_files(clips_dir: str, shot: str) -> list:
    """The clip file(s) of *shot*: its take, then its continuation ``<shot>b`` when present."""
    files = []
    for name in TAKES.get(shot, (shot,)):
        path = os.path.join(clips_dir, name + ".mp4")
        if os.path.exists(path):
            files.append(path)
            break
    if not files:
        sys.exit(f"no clip for {shot} in {clips_dir}")
    more = os.path.join(clips_dir, shot + "b.mp4")
    if os.path.exists(more):
        files.append(more)
    return files


def timeline(clips_dir: str) -> tuple:
    """``(segments, lines)``: the clip files in order with their start times,
    and every line with its start/end on the episode's clock."""
    segments, lines, t = [], [], 0.0
    for shot in SHOTS:
        shot_start = t
        for path in shot_files(clips_dir, shot):
            info = probe(path)
            segments.append({"shot": shot, "path": path, "start": t, "frames": info["frames"], **info})
            t += info["frames"] / FPS
        at = shot_start + LINE_LEAD_S
        for lid in LINES_ON[shot]:
            wav = os.path.join(VOICES, lid + ".wav")
            seconds = wav_seconds(wav)
            if at + seconds > t + 0.5:
                print(f"warning: {lid} runs {at + seconds - t:.1f} s past the end of {shot}; a continuation clip "
                      f"{shot}b would hold it", file=sys.stderr)
            lines.append({"id": lid, "wav": wav, "start": round(at, 3), "end": round(at + seconds, 3),
                          "text": TEXTS[lid]})
            at += seconds + LINE_GAP_S
    return segments, lines, t


def ass_time(seconds: float) -> str:
    h, rem = divmod(max(0.0, seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def write_ass(lines: list, width: int, height: int, path: str) -> None:
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Line,DejaVu Sans,{int(height * 0.034)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,"
            f"100,100,0,0,1,3,1,2,{int(width * 0.06)},{int(width * 0.06)},{int(height * 0.14)},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    rows = [f"Dialogue: 0,{ass_time(l['start'])},{ass_time(l['end'] + 0.15)},Line,,0,0,0,,{l['text']}" for l in lines]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head + "\n".join(rows) + "\n")


def mix_voices(lines: list, total_s: float, out_wav: str) -> None:
    """Pass 1: the voice track, every line delayed to its start over silence."""
    argv = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", f"{total_s:.3f}", "-i", "anullsrc=r=24000:cl=mono"]
    parts, labels = [], ["[0:a]"]
    for i, line in enumerate(lines, start=1):
        argv += ["-i", line["wav"]]
        ms = int(round(line["start"] * 1000))
        parts.append(f"[{i}:a]adelay={ms}|{ms}[d{i}]")
        labels.append(f"[d{i}]")
    graph = ";".join(parts) + ";" + "".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0," \
            f"atrim=0:{total_s:.3f},asetpts=N/SR/TB[a]"
    argv += ["-filter_complex", graph, "-map", "[a]", "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le", out_wav]
    run(argv)


def end_card(width: int, height: int, path: str) -> None:
    """The title card: the Team question on two lines, the CTA under it, on near-black."""
    size = int(height * 0.042)
    rows = [(CARD_LINES[0], "white", -int(height * 0.09)), (CARD_LINES[1], "white", -int(height * 0.03)),
            (CARD_LINES[2], "0xFFD166", int(height * 0.05))]
    texts = [f"drawtext=fontfile={FONT}:text='{text}':fontcolor={color}:fontsize={size}:x=(w-text_w)/2:y=(h/2)+({dy})"
             for text, color, dy in rows]
    run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-t", str(CARD_S), "-i",
         f"color=c=0x0B0B10:s={width}x{height}:r={FPS}", "-vf", ",".join(texts) + ",format=yuv420p",
         "-c:v", "libx264", "-crf", "18", "-r", str(FPS), path])


def assemble(clips_dir: str, out: str, *, card: bool = True) -> dict:
    segments, lines, total_s = timeline(clips_dir)
    width, height = segments[0]["width"], segments[0]["height"]
    work = os.path.splitext(out)[0]
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    voices_wav, ass_path, card_mp4 = work + "_voices.wav", work + ".ass", work + "_card.mp4"
    card_s = CARD_S if card else 0.0
    mix_voices(lines, total_s + card_s, voices_wav)
    write_ass(lines, width, height, ass_path)
    inputs, concat = [], []
    for i, seg in enumerate(segments):
        inputs += ["-i", seg["path"]]
        concat.append(f"[{i}:v]")
    n = len(segments)
    if card:
        end_card(width, height, card_mp4)
        inputs += ["-i", card_mp4]
        concat.append(f"[{n}:v]")
        n += 1
    inputs += ["-i", voices_wav]
    graph = ("".join(concat) + f"concat=n={len(concat)}:v=1:a=0,fps={FPS},"
             f"ass={ass_path},format=yuv420p[v]")
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", f"{n}:a",
         "-c:v", "libx264", "-crf", "19", "-preset", "medium", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out])
    for leftover in (voices_wav, card_mp4):
        try:
            os.unlink(leftover)
        except OSError:
            pass
    return {"out": out, "duration_s": round(probe(out)["duration"], 2), "segments": len(segments), "lines": len(lines),
            "subtitles": ass_path}


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("clips_dir")
    parser.add_argument("out")
    parser.add_argument("--no-card", action="store_true", help="no end card")
    args = parser.parse_args(argv)
    report = assemble(args.clips_dir, args.out, card=not args.no_card)
    print(report)


if __name__ == "__main__":
    main()

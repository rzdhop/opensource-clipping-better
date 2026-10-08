"""Render episode 1 of "Faille d'amour" (60 s, 1080x1920 @30 fps) from animated Wan clips.

Every shot is a fully animated Wan 2.2 clip (ep01/clips/clipNN.mp4, 480x832 @16 fps,
about 5 s): cropped to 9:16, slowed to its slot when the slot is longer (at most x1.4),
motion-interpolated to 30 fps and scaled to 1080x1920. Never a still: a missing clip
stops the render. Then the episode gets:
  - ASS overlays burned in: dialogue (Montserrat Black 74 px, coloured speaker names),
    tags and the end card (Bebas Neue), the SMS bubble;
  - audio: the voice lines at their cues (ep01/voices/<id>_<who>.wav when present),
    SFX from assets/sfx, BGM beds from assets/bgm with fades, ducked under the voices
    (sidechaincompress), mixed with amix normalize=0 + alimiter.

Missing voice files are not an error: the line keeps its subtitle with an estimated
duration, so a silent draft can be rendered before the voices exist.

Run from the repo root:
    python productions/faille_damour/render_ep01.py            # full render
    python productions/faille_damour/render_ep01.py --stage shots|assemble
Output: productions/faille_damour/ep01/out/faille_damour_ep01.mp4
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EP = HERE / "ep01"
VOICES = EP / "voices"
OUT = EP / "out"
WORK = OUT / "work"
FONTS = REPO / "assets" / "fonts"
SFX = REPO / "assets" / "sfx"
BGM = REPO / "assets" / "bgm"

W, H, FPS = 1080, 1920, 30
CLIPS = EP / "clips"
MAX_SLOWDOWN = 1.4
TOTAL = 60.0

# id, clip, start, end, crop (width fraction of the 9:16 window, x centre, y centre), eq filter
SHOTS = [
    ("s01", "clip01.mp4",   0.0,  6.0, (1.00, 0.50, 0.50), None),
    ("s02", "clip02.mp4",   6.0, 13.0, (0.92, 0.46, 0.50), None),   # keeps the blurred extra at the right edge out
    ("s03", "clip03.mp4",  13.0, 20.0, (1.00, 0.50, 0.50), None),
    ("s04", "clip04.mp4",  20.0, 26.0, (1.00, 0.50, 0.50), None),
    ("s05", "clip05.mp4",  26.0, 31.0, (1.00, 0.50, 0.50), None),
    ("s06", "clip06c.mp4", 31.0, 37.0, (1.00, 0.50, 0.50), None),
    ("s07", "clip07.mp4",  37.0, 43.0, (1.00, 0.50, 0.50), None),
    ("s08", "clip08.mp4",  43.0, 49.0, (1.00, 0.50, 0.50), None),
    ("s09", "clip09.mp4",  49.0, 55.0, (1.00, 0.50, 0.50), None),
    ("s10", "clip10.mp4",  55.0, 60.0, (1.00, 0.50, 0.50), None),
]

# When each line starts (seconds on the episode clock).
CUES = {"l01": 1.2, "l02": 6.3, "l03": 9.7, "l04": 13.4, "l05": 17.4, "l06": 20.3, "l07": 23.5,
        "l08": 26.6, "l09": 32.2, "l10": 39.6, "l11": 44.8, "l12": 50.6, "l13": 55.4}

# Lines voiced but not subtitled: the end card already shows them.
CARD_LINES = {"l13"}

# Speaker label and its colour (ASS &HBBGGRR&).
SPEAKERS = {
    "rida": ("RIDA", "&H4AC38B&"),
    "marie_jeanne": ("MARIE-JEANNE", "&H4B4BFF&"),
    "leonardo": ("LEONARDO", "&H37AFD4&"),
    "paloma": ("PALOMA", "&H0098FF&"),
    "don_maximiliano": ("DON MAXIMILIANO", "&HDB9DB3&"),
}

# (sfx path under assets/sfx, time, gain)
SFX_CUES = [
    ("anime/whoosh_sharp.wav", 0.0, 0.6), ("anime/impact_hit.wav", 0.35, 0.5),
    ("cartoon/pop.wav", 6.3, 0.6), ("cartoon/slide_whistle.wav", 6.5, 0.35),
    ("cartoon_soft/twinkle.wav", 13.2, 0.5), ("cartoon_soft/whoosh_soft.wav", 20.1, 0.5),
    ("anime/whoosh_sharp.wav", 26.0, 0.4), ("soap/dramatic_sting.wav", 31.0, 0.8),
    ("soap/gasp_crowd.wav", 31.4, 0.5), ("real/phone_buzz.wav", 37.0, 0.8),
    ("anime/heartbeat.wav", 43.0, 0.7), ("anime/impact_hit.wav", 49.0, 0.6),
    ("cartoon/record_scratch.wav", 55.0, 0.5),
]

# (bgm path under assets/bgm, episode start, episode end, offset into the track, gain)
BGM_BEDS = [
    ("suspense/the_mountain-epic-mood-129192.mp3", 0.0, 6.4, 0.0, 0.32),
    ("upbeat/tolsetmusic-talking-about-love-romantic-piano-background-music-542958.mp3", 6.0, 26.4, 4.0, 0.30),
    ("epic/trailer-score-room-epic-cinematic-thriller-538890.mp3", 26.0, 49.4, 0.0, 0.30),
    ("suspense/the_mountain-cinematic-mood-129193.mp3", 49.0, 60.0, 0.0, 0.36),
]


def run(cmd: list[str]) -> None:
    """Run ffmpeg/ffprobe quietly; stop with its error output on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"command failed: {' '.join(cmd[:6])} ...\n{result.stderr[-2000:]}")


def ass_time(t: float) -> str:
    """Seconds to ASS h:mm:ss.cc."""
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def line_duration(line: dict) -> tuple[float, Path | None]:
    """The spoken length of a line: the wav when it exists, else an estimate (16 chars/s)."""
    wav_path = VOICES / f"{line['id']}_{line['who']}.wav"
    if wav_path.exists():
        with wave.open(str(wav_path)) as wav:
            return wav.getnframes() / wav.getframerate(), wav_path
    return 0.3 + len(line["text"]) / 16.0, None


# ----------------------------------------------------------------------------- shots

def probe_duration(path: Path) -> float:
    """Duration of a media file in seconds (ffprobe)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(path)], capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def render_shot(shot) -> Path:
    """One Wan clip -> its slot at 1080x1920@30: 9:16 crop, slow-down if needed, interpolation."""
    sid, clip, start, end, (wfrac, cx, cy), eq = shot
    src = CLIPS / clip
    if not src.exists():
        sys.exit(f"{sid}: {src} is missing -- every shot must be an animated clip, never a still")
    slot = end - start
    factor = slot / probe_duration(src)
    if factor > MAX_SLOWDOWN:
        sys.exit(f"{sid}: the slot needs x{factor:.2f} slow-down (max x{MAX_SLOWDOWN}); split the beat in two clips")
    # 9:16 window inside the clip, centred on (cx, cy), wfrac of the widest possible window.
    crop = (f"crop=w='trunc(min(iw,ih*9/16)*{wfrac}/2)*2':h='trunc(min(iw,ih*9/16)*{wfrac}*16/9/2)*2':"
            f"x='max(0,min(iw-out_w,iw*{cx}-out_w/2))':y='max(0,min(ih-out_h,ih*{cy}-out_h/2))'")
    # 4 % of margin: minterpolate drops a few frames at the end, -frames:v cuts the excess.
    chain = [crop, f"setpts={max(factor, 1.0) * 1.04:.4f}*PTS",
             f"minterpolate=fps={FPS}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1",
             f"scale={W}:{H}:flags=lanczos"]
    if eq:
        chain.append(eq)
    chain.append("format=yuv420p")
    frames = int(round(slot * FPS))
    dest = WORK / f"{sid}.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", ",".join(chain), "-an",
         "-frames:v", str(frames), "-c:v", "libx264", "-preset", "veryfast", "-crf", "17",
         "-r", str(FPS), str(dest)])
    got = int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                              "stream=nb_read_frames", "-of", "csv=p=0", str(dest)],
                             capture_output=True, text=True).stdout.strip() or 0)
    if got < frames:
        sys.exit(f"{sid}: {got} frames rendered, {frames} expected")
    return dest


# ----------------------------------------------------------------------------- overlays

def build_ass(lines: list[dict], timings: dict) -> Path:
    """All text of the episode as one ASS file (1080x1920 play resolution)."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Dialog,Montserrat Black,74,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,6,2,2,80,80,330,1
Style: Tag,Bebas Neue,120,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,2,0,1,6,3,8,60,60,180,1
Style: Small,Bebas Neue,60,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,3,0,1,4,2,8,60,60,90,1
Style: Sms,Montserrat Black,50,&H00222222,&H00222222,&H00F2F2F2,&H00F2F2F2,0,0,0,0,100,100,0,0,3,22,0,5,120,120,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []

    def add(start, end, style, text, layer=0):
        events.append(f"Dialogue: {layer},{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}")

    # Dialogue: speaker name on its own smaller line, coloured; the line in white.
    for line in lines:
        if line["id"] in CARD_LINES:
            continue
        start = CUES[line["id"]]
        end = start + timings[line["id"]] + 0.25
        name, colour = SPEAKERS[line["who"]]
        add(start, end, "Dialog", f"{{\\fad(80,80)}}{{\\fs52\\c{colour}}}{name}\\N{{\\fs74\\c&HFFFFFF&}}{line['text']}")

    # Hook and tags.
    add(0.0, 3.0, "Small", "{\\fad(0,300)}FAILLE D'AMOUR · ÉPISODE 1")
    add(0.35, 5.8, "Tag", "{\\pos(540,250)\\c&H2020FF&\\fad(0,200)\\t(0,120,\\fscx115\\fscy115)\\t(120,260,\\fscx100\\fscy100)}FAILLE CRITIQUE")
    add(26.0, 31.0, "Small", "{\\pos(540,200)\\fad(150,150)}CONTRAT VAULTA · SIGNATURE T-72:00:00")
    add(31.0, 37.0, "Tag", "{\\pos(540,330)\\fs104\\fad(0,200)\\t(0,150,\\fscx120\\fscy120)\\t(150,300,\\fscx100\\fscy100)}ACCÈS NON SÉCURISÉ")
    # The SMS bubble (opaque box style) with a small header.
    add(37.2, 42.8, "Sms", "{\\pos(540,330)\\fad(120,150)\\fs40\\c&H777777&}Numéro inconnu\\N{\\fs60\\c&H222222&}Ton produit a une faille.\\N— R.")
    # End card.
    add(55.2, 60.0, "Tag", "{\\pos(540,1180)\\fs140\\c&H4AC38B&\\fad(120,0)}TEAM RIDA")
    add(55.6, 60.0, "Tag", "{\\pos(540,1310)\\fs80\\fad(120,0)}ou")
    add(56.0, 60.0, "Tag", "{\\pos(540,1440)\\fs124\\c&H4B4BFF&\\fad(120,0)}TEAM MARIE-JEANNE ?")
    add(57.6, 60.0, "Tag", "{\\pos(540,1640)\\fs96\\c&H00D7FF&\\fad(150,0)}PARTIE 2 DEMAIN")

    ass = WORK / "ep01.ass"
    ass.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return ass


# ----------------------------------------------------------------------------- audio

def build_audio(lines: list[dict], voice_files: dict) -> Path:
    """Voices at their cues, SFX, BGM beds ducked under the voices, one stereo AAC track."""
    inputs, filters, vox_labels, sfx_labels, bgm_labels = [], [], [], [], []

    def add_input(path: Path) -> int:
        """Append one ffmpeg input and return its index."""
        inputs.extend(["-i", str(path)])
        return len(inputs) // 2 - 1

    for line in lines:
        path = voice_files.get(line["id"])
        if path is None:
            continue
        idx = add_input(path)
        ms = int(CUES[line["id"]] * 1000)
        filters.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume=1.6,adelay={ms}|{ms}[v{idx}]")
        vox_labels.append(f"[v{idx}]")

    for rel, t, gain in SFX_CUES:
        idx = add_input(SFX / rel)
        ms = int(t * 1000)
        filters.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={gain},adelay={ms}|{ms}[s{idx}]")
        sfx_labels.append(f"[s{idx}]")

    for rel, start, end, offset, gain in BGM_BEDS:
        idx = add_input(BGM / rel)
        length = end - start
        ms = int(start * 1000)
        filters.append(
            f"[{idx}:a]atrim=start={offset}:duration={length},asetpts=PTS-STARTPTS,aresample=48000,"
            f"aformat=channel_layouts=stereo,volume={gain},afade=t=in:d=0.4,"
            f"afade=t=out:st={max(0.0, length - 0.8):.2f}:d=0.8,adelay={ms}|{ms}[b{idx}]")
        bgm_labels.append(f"[b{idx}]")

    filters.append(f"{''.join(bgm_labels)}amix=inputs={len(bgm_labels)}:normalize=0:duration=longest[bgm]")
    filters.append(f"{''.join(sfx_labels)}amix=inputs={len(sfx_labels)}:normalize=0:duration=longest[sfx]")
    if vox_labels:
        filters.append(f"{''.join(vox_labels)}amix=inputs={len(vox_labels)}:normalize=0:duration=longest,asplit=2[vox][key]")
        filters.append("[bgm][key]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=350[duck]")
        filters.append("[duck][vox][sfx]amix=inputs=3:normalize=0:duration=longest[mix]")
    else:
        filters.append("[bgm][sfx]amix=inputs=2:normalize=0:duration=longest[mix]")
    filters.append(f"[mix]apad,atrim=duration={TOTAL},afade=t=out:st={TOTAL - 0.6}:d=0.6,"
                   "alimiter=limit=0.89:level=false[out]")

    dest = WORK / "mix.m4a"
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(filters),
         "-map", "[out]", "-c:a", "aac", "-b:a", "192k", str(dest)])
    return dest


# ----------------------------------------------------------------------------- main

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", choices=["shots", "assemble", "all"], default="all")
    parser.add_argument("--shots", nargs="*", help="only (re)render these shot ids, e.g. s10")
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)

    spec = json.loads((EP / "lines.json").read_text(encoding="utf-8"))
    lines = spec["lines"]
    timings, voice_files = {}, {}
    for line in lines:
        timings[line["id"]], path = line_duration(line)
        if path:
            voice_files[line["id"]] = path

    # A line starts at its cue, or 0.15 s after the previous line ends if that is later;
    # warn when it then spills past the end of its shot.
    shot_ends = [(s[2], s[3]) for s in SHOTS]
    previous_end = 0.0
    for line in sorted(lines, key=lambda l: CUES[l["id"]]):
        planned = CUES[line["id"]]
        CUES[line["id"]] = max(planned, previous_end + 0.15)
        previous_end = CUES[line["id"]] + timings[line["id"]]
        shot_end = next(end for start, end in shot_ends if start <= planned < end)
        if previous_end > shot_end:
            print(f"warning: {line['id']} ends at {previous_end:.2f} s, after its shot ({shot_end:.2f} s)")

    if args.stage in ("shots", "all"):
        for shot in SHOTS:
            if args.shots and shot[0] not in args.shots:
                continue
            render_shot(shot)
            print(f"shot {shot[0]} ok")
        if args.stage == "shots":
            return

    concat = WORK / "concat.txt"
    concat.write_text("".join(f"file '{(WORK / (s[0] + '.mp4')).as_posix()}'\n" for s in SHOTS), encoding="utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy",
         str(WORK / "video.mp4")])

    ass = build_ass(lines, timings)
    audio = build_audio(lines, voice_files)

    OUT.mkdir(parents=True, exist_ok=True)
    final = OUT / "faille_damour_ep01.mp4"
    # The subtitles filter wants a path with escaped ':' (Windows drives) -> run it from WORK.
    vf = (f"subtitles=ep01.ass:fontsdir='{FONTS.as_posix().replace(':', chr(92) + ':')}',"
          f"fade=t=out:st={TOTAL - 0.5}:d=0.5")
    result = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", "video.mp4", "-i", audio.name, "-vf", vf,
                             "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                             "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", "-t", str(TOTAL),
                             str(final.resolve())], cwd=WORK, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(result.stderr[-2000:])
    voiced = len(voice_files)
    print(f"done: {final}  ({voiced}/{len(lines)} voice lines; "
          f"{'draft without voices' if voiced == 0 else 'with voices'})")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Assemble episode 1 of "Accès refusé" (fruit drama, ~81 s, 1080x1920) with character voices.

Run from anywhere (paths are relative to the repo root) on a machine that can reach the Gemini API:
    python tools/render_ep01.py                 # voice the lines (Gemini TTS) if needed, then render
    python tools/render_ep01.py --force-voices  # re-voice every line
    python tools/render_ep01.py --no-voices     # subtitles only (version 1)
    python tools/render_ep01.py --fake-voices   # offline test: ffmpeg flite stands in for Gemini

Needs: ffmpeg on PATH, GOOGLE_API_KEY (or GEMINI_API_KEY) in the environment or in ./.env.
Optional: GEMINI_TTS_MODEL to force a TTS model id.

Inputs:
    outputs/acces_refuse/ep01/clips/clip01.mp4 ... clip17.mp4
    assets/fonts, assets/sfx, assets/bgm
Outputs:
    outputs/acces_refuse/ep01/voices/lNN.wav            (one file per spoken line, reused on reruns)
    outputs/acces_refuse/ep01/acces_refuse_ep01.mp4      (full quality)
    outputs/acces_refuse/ep01/acces_refuse_ep01_share.mp4 (re-encoded under ~25 MB for upload)
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # repo root (script lives in tools/)
EP_DIR = ROOT / "outputs" / "acces_refuse" / "ep01"
CLIP_DIR = EP_DIR / "clips"
VOICE_DIR = EP_DIR / "voices"
WORK_DIR = EP_DIR / "work"
FONT_DIR = ROOT / "assets" / "fonts"
SFX_DIR = ROOT / "assets" / "sfx"
BGM_DIR = ROOT / "assets" / "bgm"
FINAL = EP_DIR / "acces_refuse_ep01.mp4"
SHARE = EP_DIR / "acces_refuse_ep01_share.mp4"

W, H, FPS = 1080, 1920, 30
END_CARD_SECONDS = 3.0

# (clip file, duration in seconds used in the edit) in story order.
SHOTS = [
    ("clip01", 5.0),  # hook: Marie-Jeanne laughs at the hotel bar
    ("clip02", 5.0),  # Rida's server room, night
    ("clip03", 5.0),  # the pineapple on video call
    ("clip04", 5.0),  # the target: Marie-Jeanne's photo
    ("clip05", 4.0),  # disguise in the mirror
    ("clip06", 4.0),  # arrival at the hotel
    ("clip07", 5.0),  # the spill
    ("clip08", 5.0),  # Marie-Jeanne teases
    ("clip09", 5.0),  # Rida grins
    ("clip10", 5.0),  # they laugh together
    ("clip11", 5.0),  # midnight, empty bar
    ("clip12", 5.0),  # she leaves her card
    ("clip13", 4.0),  # he forgot the badge
    ("clip14", 5.0),  # the elevator: the mask falls
    ("clip15", 4.0),  # Inès on the phone
    ("clip16", 4.0),  # the screens flash
    ("clip17", 3.0),  # shock close-up
]

# Speaker colours in ASS format (&HBBGGRR&).
COL = {
    "RIDA": "&HE070B0&",          # violet
    "MARIE-JEANNE": "&H7FB4FF&",  # peach
    "ANANAS": "&H20C8F0&",        # golden yellow
    "INÈS": "&H40E0A0&",          # lime green
}

# Gemini prebuilt voice per character ("realistic drama" casting).
# Change a name here and rerun with --force-voices to recast a character.
VOICES = {
    "RIDA": "Orus",           # firm, low male: calm hacker
    "MARIE-JEANNE": "Kore",   # firm, confident female: sales director
    "ANANAS": "Algenib",      # gravelly, very low male: the sponsor
    "INÈS": "Erinome",        # clear, dry female: head of security
}
VOICE_GAIN = {"RIDA": 1.0, "MARIE-JEANNE": 1.0, "ANANAS": 1.0, "INÈS": 1.0}
# Slower delivery for the pineapple (applied after synthesis, 1.0 = unchanged).
VOICE_TEMPO = {"ANANAS": 0.92}

# Model ids tried in order; the first one the API accepts wins.
TTS_MODELS = [m for m in [
    os.environ.get("GEMINI_TTS_MODEL"),
    "gemini-3.8-flash-lite-tts",
    "gemini-2.5-flash-preview-tts",
] if m]

# (start, end, style, text). Times are in seconds on the final timeline.
# Styles: Title (big centre), Line (dialogue, lower third, voiced), Tag (small top),
# Count (countdown, top right), Screen (terminal message centre), End (end card).
EVENTS = [
    (0.2, 4.8, "Title", "ELLE NE SAIT PAS\\NQU'ELLE EST UNE CIBLE."),
    (5.0, 9.5, "Tag", "PANTIN · 23H47"),
    (5.0, 28.0, "Count", "T-72:00:00"),
    (10.2, 12.6, "Line", "ANANAS|Vexel signe le contrat du siècle vendredi."),
    (12.6, 15.0, "Line", "ANANAS|Tu as 72 heures pour entrer."),
    (15.3, 16.9, "Line", "RIDA|Par où ?"),
    (17.2, 19.8, "Line", "ANANAS|Par elle."),
    (20.2, 22.2, "Line", "RIDA|Les gens, c'est comme les systèmes."),
    (22.2, 24.0, "Line", "RIDA|Il y a toujours une faille."),
    (24.0, 28.0, "Tag", "LA DÉFENSE · 21H12"),
    (28.4, 30.0, "Line", "RIDA|Oh non… pardon !"),
    (30.1, 33.0, "Line", "MARIE-JEANNE|Vous faites toujours ça pour aborder les femmes ?"),
    (33.2, 35.6, "Line", "RIDA|Seulement celles qui ont l'air de s'ennuyer."),
    (35.7, 38.0, "Line", "MARIE-JEANNE|Je ne m'ennuie jamais. Je travaille."),
    (38.4, 41.0, "Line", "RIDA|C'est pareil… mais en plus cher."),
    (41.0, 43.0, "Tag", "OBJECTIF : SON BADGE"),
    (43.0, 45.5, "Title", "4 HEURES\\NPLUS TARD"),
    (48.3, 50.9, "Line", "MARIE-JEANNE|Vous ne m'avez rien demandé sur mon métier."),
    (51.0, 53.0, "Line", "RIDA|Ce n'est pas ce qui m'intéresse."),
    (54.0, 56.8, "Line", "MARIE-JEANNE|Bonne nuit… consultant."),
    (58.4, 61.8, "Title", "LE BADGE.\\NIL A OUBLIÉ LE BADGE."),
    (62.3, 64.8, "Line", "MARIE-JEANNE|Il est mignon. Mais il ment mal."),
    (65.0, 67.0, "Line", "MARIE-JEANNE|Tu as tout enregistré ?"),
    (67.2, 69.0, "Line", "INÈS|Chaque mot. On le neutralise ?"),
    (69.1, 71.0, "Line", "MARIE-JEANNE|Non. Je veux savoir qui l'envoie."),
    (72.0, 75.0, "Screen", "> Bien essayé, consultant.\\N> – MJ"),
    (71.0, 78.0, "Count", "T-71:12:43"),
    (75.4, 78.0, "Title", "72H.\\NQUI PIRATE QUI ?"),
    (78.0, 81.0, "End", "ACCÈS REFUSÉ\\N{\\fs60}Épisode 2 →"),
]

# (start second, relative path in assets/sfx, volume)
SFX = [
    (0.0, "soap/dramatic_sting.wav", 0.9),
    (10.0, "real/phone_buzz.wav", 0.5),
    (17.2, "anime/impact_hit.wav", 0.7),
    (20.0, "anime/whoosh_sharp.wav", 0.6),
    (28.4, "cartoon/pop.wav", 0.5),
    (43.0, "gentle/page_turn.wav", 0.5),
    (58.2, "anime/heartbeat.wav", 0.8),
    (61.6, "soap/gasp_crowd.wav", 0.5),
    (71.0, "cartoon/record_scratch.wav", 0.8),
    (72.0, "real/phone_buzz.wav", 0.6),
    (75.2, "soap/dramatic_sting.wav", 1.0),
]

# (start, end, relative path in assets/bgm, volume). Fades are applied at both ends.
BGM = [
    (0.0, 28.5, "suspense/the_mountain-cinematic-mood-129193.mp3", 0.35),
    (28.0, 58.5, "upbeat/tolsetmusic-talking-about-love-romantic-piano-background-music-542958.mp3", 0.30),
    (58.0, 81.0, "suspense/virtualvisualviews-dueling-music-box-horror-373912.mp3", 0.35),
]


def run(cmd, quiet=False):
    """Run a command and stop the script on failure."""
    if not quiet:
        print(" ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True,
                   stdout=subprocess.DEVNULL if quiet else None,
                   stderr=subprocess.DEVNULL if quiet else None)


def probe_duration(path):
    """Duration of a media file in seconds (ffprobe)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", str(path)], check=True, capture_output=True, text=True)
    return float(out.stdout.strip())


def ass_time(t):
    """Seconds -> ASS timestamp H:MM:SS.cc."""
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


# ------------------------------------------------------------------ spoken lines

def spoken_lines():
    """The voiced lines in timeline order: id, speaker, text, start, and the time it may last."""
    lines = sorted((e for e in EVENTS if e[2] == "Line"), key=lambda e: e[0])
    out = []
    for i, (start, end, _style, text) in enumerate(lines):
        speaker, said = text.split("|", 1)
        # A line may run until just before the next line starts (or its own end + 1 s for the last).
        next_start = lines[i + 1][0] if i + 1 < len(lines) else end + 1.0
        out.append({"id": f"l{i + 1:02d}", "speaker": speaker, "text": said,
                    "start": start, "room": max(next_start - start - 0.1, 0.8)})
    return out


def find_api_key():
    """GOOGLE_API_KEY / GEMINI_API_KEY from the environment, else from ./.env."""
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(GOOGLE_API_KEY|GEMINI_API_KEY)\s*=\s*(.+?)\s*$", raw)
            if m:
                return m.group(2).strip().strip('"').strip("'")
    return None


def write_pcm_wav(pcm, rate, path):
    """Raw 16-bit little-endian mono PCM -> WAV."""
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)


def gemini_tts(text, voice, key, out_wav, retries=6):
    """Speak *text* with a Gemini prebuilt voice and save it as WAV.

    The line is sent alone: the repo's tests found that a spoken direction
    ("Say coldly: ...") is read aloud by the flash-lite TTS model.
    """
    body = json.dumps({
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }).encode("utf-8")
    errors = []
    for model in TTS_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for attempt in range(retries):
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "Content-Type": "application/json", "x-goog-api-key": key})
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.load(resp)
            except urllib.error.HTTPError as err:
                detail = err.read().decode("utf-8", "replace")[:300]
                if err.code in (429, 500, 503):
                    # Free-tier rate limit or overload: wait (the API's retryDelay when it gives one).
                    m = re.search(r'"retryDelay":\s*"(\d+)', detail)
                    wait = int(m.group(1)) + 1 if m else 15 * (attempt + 1)
                    print(f"   {model}: HTTP {err.code}, retry in {wait}s")
                    time.sleep(wait)
                    continue
                errors.append(f"{model}: HTTP {err.code} {detail}")
                break  # unknown model or bad request: try the next model id
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            audio = next((p["inlineData"] for p in parts if "inlineData" in p), None)
            if not audio:
                errors.append(f"{model}: no audio in the answer")
                break
            m = re.search(r"rate=(\d+)", audio.get("mimeType", ""))
            write_pcm_wav(base64.b64decode(audio["data"]), int(m.group(1)) if m else 24000, out_wav)
            return model
        else:
            errors.append(f"{model}: still rate-limited after {retries} tries")
    raise RuntimeError("Gemini TTS failed:\n  " + "\n  ".join(errors))


def fake_tts(text, out_wav):
    """Offline stand-in for tests: ffmpeg's flite voice reads the line."""
    safe = re.sub(r"[^A-Za-z0-9 ,.?!']", " ", text)
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"flite=text='{safe}'", "-ar", "24000", "-ac", "1", out_wav],
        quiet=True)


def voice_lines(force=False, fake=False):
    """Make one WAV per spoken line (kept between runs so a rate limit costs no rework)."""
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = VOICE_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    key = None if fake else find_api_key()
    if not fake and not key:
        sys.exit("No GOOGLE_API_KEY / GEMINI_API_KEY found (environment or .env). "
                 "Use --no-voices for the subtitles-only version.")
    for line in spoken_lines():
        wav = VOICE_DIR / f"{line['id']}.wav"
        voice = "flite" if fake else VOICES[line["speaker"]]
        # Re-voice when forced, missing, or when the text or the voice changed.
        stamp = {"text": line["text"], "voice": voice}
        if wav.exists() and not force and manifest.get(line["id"], {}).get("stamp") == stamp:
            continue
        print(f"voice {line['id']} {line['speaker']} ({voice}): {line['text']}")
        model = "flite" if fake else gemini_tts(line["text"], voice, key, wav)
        if fake:
            fake_tts(line["text"], wav)
        manifest[line["id"]] = {"stamp": stamp, "model": model, "speaker": line["speaker"]}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def check_existing_voices(voice_dir):
    """--use-existing-voices: one lNN.wav per spoken line must already be in
    *voice_dir* (made elsewhere: tools/voice_ep01_comfy.py on the RunPod worker,
    or an earlier Gemini run). Nothing is synthesised and no key is needed."""
    missing = [line["id"] for line in spoken_lines() if not (voice_dir / f"{line['id']}.wav").exists()]
    if missing:
        sys.exit(f"Missing voice files in {voice_dir}: {', '.join(missing)}. "
                 "Run tools/voice_ep01_comfy.py first (or drop the flag to voice with Gemini).")
    return len(spoken_lines())


def voice_filters(first_input):
    """ffmpeg inputs + filters placing every line at its start, trimmed, levelled and fitted to its slot."""
    inputs, filters, labels, report = [], [], [], []
    idx = first_input
    for line in spoken_lines():
        wav = VOICE_DIR / f"{line['id']}.wav"
        if not wav.exists():
            sys.exit(f"Missing voice {wav}: run without --no-voices first.")
        tempo = VOICE_TEMPO.get(line["speaker"], 1.0)
        # Rough spoken length after the leading-silence trim (TTS adds ~0.1-0.3 s).
        spoken = max(probe_duration(wav) - 0.2, 0.3) / tempo
        if spoken > line["room"]:
            # Speed up just enough to fit before the next line, never past 1.35x.
            tempo *= min(spoken / line["room"], 1.35)
            report.append(f"{line['id']} sped up x{spoken / line['room']:.2f} to fit {line['room']:.1f}s")
        gain = VOICE_GAIN.get(line["speaker"], 1.0)
        delay = int(line["start"] * 1000)
        inputs += ["-i", wav]
        filters.append(
            f"[{idx}:a]silenceremove=start_periods=1:start_threshold=-45dB,"
            f"atempo={tempo:.3f},highpass=f=80,"
            f"loudnorm=I=-16:TP=-2:LRA=7,volume={gain},"
            f"aformat=channel_layouts=stereo:sample_rates=48000,"
            f"afade=t=out:st={line['room'] - 0.08:.2f}:d=0.08,"
            f"adelay={delay}|{delay}[vx{idx}]"
        )
        labels.append(f"[vx{idx}]")
        idx += 1
    return inputs, filters, labels, report


# ------------------------------------------------------------------ picture

def build_ass(path):
    """Write the subtitle/overlay file (dialogue, tags, countdown, titles)."""
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Line,Montserrat Black,74,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,70,70,420,1
Style: Title,Bebas Neue,140,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,2,0,1,8,4,5,60,60,0,1
Style: Tag,Bebas Neue,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,4,0,1,4,0,8,60,60,230,1
Style: Count,Bebas Neue,64,&H003030FF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,3,0,1,4,0,9,50,50,120,1
Style: Screen,Montserrat Black,60,&H0000FF66,&H00FFFFFF,&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,3,18,0,5,90,90,0,1
Style: End,Bebas Neue,170,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,4,0,1,6,0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for start, end, style, text in EVENTS:
        if style == "Line":
            speaker, said = text.split("|", 1)
            colour = COL.get(speaker, "&HFFFFFF&")
            # Coloured speaker name on its own line, dialogue below.
            text = f"{{\\c{colour}\\fs52}}{speaker}{{\\r}}\\N{said}"
        elif style in ("Title", "End"):
            # Punch-in pop for titles: scale from 130% to 100% over 150 ms.
            text = f"{{\\fscx130\\fscy130\\t(0,150,\\fscx100\\fscy100)}}{text}"
        lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},{style},,0,0,0,,{text}")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def build_video(silent_out, ass_path):
    """Normalise every clip to 1080x1920 @30fps, trim, concat, add end card and overlays."""
    inputs, filters, labels = [], [], []
    for i, (name, dur) in enumerate(SHOTS):
        clip = CLIP_DIR / f"{name}.mp4"
        if not clip.exists():
            sys.exit(f"Missing clip: {clip}")
        inputs += ["-i", clip]
        # Fill the frame (scale up then centre-crop), constant fps, exact duration.
        # tpad clones the last frame if a clip is slightly shorter than its slot.
        filters.append(
            f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"fps={FPS},setsar=1,tpad=stop_mode=clone:stop_duration=1,"
            f"trim=duration={dur},setpts=PTS-STARTPTS[v{i}]"
        )
        labels.append(f"[v{i}]")
    n = len(SHOTS)
    inputs += ["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={END_CARD_SECONDS}"]
    filters.append(f"[{n}:v]setsar=1[v{n}]")
    labels.append(f"[v{n}]")
    # Concat, then burn the overlays. Paths are escaped for the filter graph (Windows-safe).
    ass_arg = str(ass_path).replace("\\", "/").replace(":", "\\:")
    font_arg = str(FONT_DIR).replace("\\", "/").replace(":", "\\:")
    filters.append(
        "".join(labels) + f"concat=n={n + 1}:v=1:a=0[cat];"
        f"[cat]subtitles='{ass_arg}':fontsdir='{font_arg}',format=yuv420p[vout]"
    )
    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters),
         "-map", "[vout]", "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-r", FPS, silent_out])


# ------------------------------------------------------------------ sound

def build_audio(audio_out, total, with_voices):
    """Music beds (ducked under the voices), timed SFX and the voiced lines in one stereo track."""
    inputs, filters = [], []
    idx = 0
    music = []
    for start, end, rel, vol in BGM:
        inputs += ["-i", BGM_DIR / rel]
        length = end - start
        delay = int(start * 1000)
        filters.append(
            f"[{idx}:a]atrim=0:{length},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d=0.8,afade=t=out:st={max(length - 1.2, 0)}:d=1.2,"
            f"volume={vol},aformat=channel_layouts=stereo:sample_rates=48000,"
            f"adelay={delay}|{delay}[m{idx}]"
        )
        music.append(f"[m{idx}]")
        idx += 1
    sfx = []
    for start, rel, vol in SFX:
        inputs += ["-i", SFX_DIR / rel]
        delay = int(start * 1000)
        filters.append(
            f"[{idx}:a]volume={vol},aformat=channel_layouts=stereo:sample_rates=48000,"
            f"adelay={delay}|{delay}[s{idx}]"
        )
        sfx.append(f"[s{idx}]")
        idx += 1
    # normalize=0 keeps each source at its own volume instead of dividing by the input count.
    filters.append("".join(music) + f"amix=inputs={len(music)}:normalize=0:dropout_transition=0[music]")
    filters.append("".join(sfx) + f"amix=inputs={len(sfx)}:normalize=0:dropout_transition=0[sfx]")
    if with_voices:
        v_inputs, v_filters, v_labels, report = voice_filters(idx)
        inputs += v_inputs
        filters += v_filters
        for note in report:
            print("   fit:", note)
        filters.append("".join(v_labels) + f"amix=inputs={len(v_labels)}:normalize=0:dropout_transition=0,"
                       "asplit=2[vox][key]")
        # Duck the music (about -10 dB) whenever someone speaks, release smoothly after.
        filters.append("[music][key]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=350[ducked]")
        filters.append("[ducked][sfx][vox]amix=inputs=3:normalize=0:dropout_transition=0,"
                       f"atrim=0:{total},alimiter=limit=0.95[aout]")
    else:
        filters.append(f"[music][sfx]amix=inputs=2:normalize=0:dropout_transition=0,"
                       f"atrim=0:{total},alimiter=limit=0.95[aout]")
    run(["ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filters),
         "-map", "[aout]", "-c:a", "aac", "-b:a", "192k", audio_out])


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--no-voices", action="store_true", help="subtitles only, no TTS")
    group.add_argument("--fake-voices", action="store_true", help="offline test voices (ffmpeg flite)")
    group.add_argument("--use-existing-voices", action="store_true",
                       help="mix the lNN.wav files already in the voices folder (tools/voice_ep01_comfy.py); "
                            "no Gemini call, no key needed")
    parser.add_argument("--force-voices", action="store_true", help="re-voice every line")
    parser.add_argument("--voices-dir", type=Path, default=None,
                        help=f"where the lNN.wav files are (default {(EP_DIR / 'voices').relative_to(ROOT)})")
    return parser.parse_args(argv)


def main(argv=None):
    global VOICE_DIR
    args = parse_args(argv)
    if args.voices_dir:
        VOICE_DIR = Path(args.voices_dir)

    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is not installed or not on PATH.")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    total = sum(d for _, d in SHOTS) + END_CARD_SECONDS
    with_voices = not args.no_voices

    if args.use_existing_voices:
        check_existing_voices(VOICE_DIR)
    elif with_voices:
        voice_lines(force=args.force_voices, fake=args.fake_voices)

    ass_path = WORK_DIR / "ep01_overlays.ass"
    silent = WORK_DIR / "ep01_silent.mp4"
    audio = WORK_DIR / "ep01_audio.m4a"
    build_ass(ass_path)
    build_video(silent, ass_path)
    build_audio(audio, total, with_voices)
    run(["ffmpeg", "-y", "-i", silent, "-i", audio, "-map", "0:v", "-map", "1:a",
         "-c:v", "copy", "-c:a", "copy", "-shortest", "-movflags", "+faststart", FINAL])
    # Lighter copy for chat/phone upload limits.
    run(["ffmpeg", "-y", "-i", FINAL, "-c:v", "libx264", "-preset", "slow", "-crf", "23",
         "-maxrate", "2.6M", "-bufsize", "5M", "-c:a", "copy", "-movflags", "+faststart", SHARE])
    source = ("fake" if args.fake_voices else "existing files" if args.use_existing_voices
              else "gemini" if with_voices else "none")
    print(f"Done: {FINAL} and {SHARE} ({total:.1f} s, voices: {source})")


if __name__ == "__main__":
    main()

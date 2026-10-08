"""Speak every line of an episode with Gemini TTS, one prebuilt voice per character.

Stdlib only: run it on any machine that can reach generativelanguage.googleapis.com
(your Windows PC is fine), from the repo root:

    set GOOGLE_API_KEY=...            (PowerShell: $env:GOOGLE_API_KEY="...")
    python productions/faille_damour/make_voices_gemini.py

It reads  productions/faille_damour/ep01/lines.json
and writes productions/faille_damour/ep01/voices/<id>_<who>.wav  (mono 24 kHz 16-bit)
plus      productions/faille_damour/ep01/voices/durations.json

A line already on disk is skipped, so a failed run can simply be started again.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Tried in order: the first model that answers with audio is kept for the whole run.
MODELS = ["gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts"]
# Gemini sometimes appends a burst of noise after the last word: anything quieter than
# this RMS (16-bit scale) at the end is treated as silence, and the tail is cut after it.
SILENCE_RMS = 300
WINDOW_S = 0.02


def api_key() -> str:
    """The key from the environment, else from the repo's .env file."""
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    env_file = HERE.parent.parent / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\s*(GOOGLE_API_KEY|GEMINI_API_KEY)\s*=\s*(.+)", line)
            if match:
                return match.group(2).strip().strip('"').strip("'")
    sys.exit("No GOOGLE_API_KEY (or GEMINI_API_KEY) in the environment or in .env")


def synthesize(model: str, key: str, voice: str, style: str, text: str) -> tuple[bytes, int]:
    """One Gemini TTS call: returns raw 16-bit PCM and its sample rate."""
    prompt = f"Say this line in French, as {style}. Say only the line:\n{text}"
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    }
    request = urllib.request.Request(API.format(model=model), data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))
    for candidate in payload.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if inline and inline.get("data"):
                mime = inline.get("mimeType") or inline.get("mime_type") or ""
                rate = re.search(r"rate=(\d+)", mime)
                return base64.b64decode(inline["data"]), int(rate.group(1)) if rate else 24000
    raise RuntimeError(f"no audio in the answer: {json.dumps(payload)[:300]}")


def trim(pcm: bytes, rate: int) -> bytes:
    """Cut leading/trailing silence and any low noise tail, keep 80 ms of air at each end."""
    samples = struct.unpack(f"<{len(pcm) // 2}h", pcm[: len(pcm) // 2 * 2])
    step = max(1, int(rate * WINDOW_S))
    loud = [i for i in range(0, len(samples), step)
            if (sum(s * s for s in samples[i:i + step]) / max(1, len(samples[i:i + step]))) ** 0.5 > SILENCE_RMS]
    if not loud:
        return pcm
    pad = int(rate * 0.08)
    start = max(0, loud[0] - pad)
    end = min(len(samples), loud[-1] + step + pad)
    return struct.pack(f"<{end - start}h", *samples[start:end])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lines", default=str(HERE / "ep01" / "lines.json"))
    parser.add_argument("--out", default=str(HERE / "ep01" / "voices"))
    parser.add_argument("--redo", nargs="*", default=[], help="line ids to speak again (e.g. l03 l09)")
    args = parser.parse_args()

    spec = json.loads(Path(args.lines).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    key = api_key()
    models = list(MODELS)
    durations = {}

    for line in spec["lines"]:
        who = spec["voices"][line["who"]]
        dest = out / f"{line['id']}_{line['who']}.wav"
        if dest.exists() and line["id"] not in args.redo:
            with wave.open(str(dest)) as wav:
                durations[line["id"]] = round(wav.getnframes() / wav.getframerate(), 3)
            print(f"skip {dest.name}")
            continue
        for attempt in range(3):
            try:
                pcm, rate = synthesize(models[0], key, who["voice"], who["style"], line["text"])
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:200]
                if exc.code == 404 and len(models) > 1:
                    print(f"model {models[0]} not available, trying {models[1]}")
                    models.pop(0)
                elif exc.code in (429, 500, 503) and attempt < 2:
                    time.sleep(10 * (attempt + 1))
                else:
                    sys.exit(f"{line['id']}: HTTP {exc.code} {detail}")
        else:
            sys.exit(f"{line['id']}: no audio after 3 tries")
        pcm = trim(pcm, rate)
        with wave.open(str(dest), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(pcm)
        durations[line["id"]] = round(len(pcm) / 2 / rate, 3)
        print(f"ok   {dest.name}  {durations[line['id']]:.2f} s  ({who['voice']}, {models[0]})")

    (out / "durations.json").write_text(json.dumps(durations, indent=2), encoding="utf-8")
    print(f"done: {len(durations)} lines in {out}")


if __name__ == "__main__":
    main()

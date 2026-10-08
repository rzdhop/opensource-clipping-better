"""Build the Chatterbox voice references of the series "Faille d'amour".

Each character gets one clean 10-15 s French reference, spoken by an edge-tts
voice (synthetic, so no consent issue), re-encoded to the format the
tts_chatterbox template expects: mono, 24 kHz, 16-bit PCM WAV.

Run it on the machine that hosts the rzdhop-story MCP server, from the repo
root, so the files land where comfy_submit can read them:

    pip install edge-tts
    python productions/faille_damour/make_voice_refs.py

Output: outputs/faille_damour/refs/ref_<character>.wav + manifest.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
from datetime import date
from pathlib import Path

import edge_tts

# One entry per character: edge-tts voice, prosody, and a neutral text that
# covers many French phonemes (the clone learns the timbre, not the words).
VOICES = {
    "rida": {
        "voice": "fr-FR-HenriNeural",
        "rate": "+0%",
        "pitch": "+0Hz",
        "direction": "young man, dry humour, calm and precise (hacker kiwi)",
        "text": ("Je travaille la nuit, quand la ville dort enfin. Les écrans sont mes fenêtres. "
                 "Je cherche les portes que personne ne ferme, et je les montre, même quand ça dérange. "
                 "Le code ne ment jamais. Les gens, parfois."),
    },
    "marie_jeanne": {
        "voice": "fr-FR-DeniseNeural",
        "rate": "+5%",
        "pitch": "+0Hz",
        "direction": "confident woman, quick and proud (sales director strawberry)",
        "text": ("Dans une heure, je signe le contrat de l'année. J'ai préparé chaque slide, chaque chiffre, "
                 "chaque réponse. Personne ne me fera perdre cette négociation. "
                 "Retenez bien mon nom : il sera dans tous les journaux demain matin."),
    },
    "leonardo": {
        "voice": "fr-BE-GerardNeural",
        "rate": "-5%",
        "pitch": "-8Hz",
        "direction": "smooth ambitious boss, theatrical (avocado)",
        "text": ("Mesdames et messieurs, bienvenue. Aujourd'hui, nous ne vendons pas un logiciel, "
                 "nous vendons la tranquillité. Ce contrat, c'est l'avenir de cette entreprise. "
                 "Et l'avenir, je n'aime pas qu'on le fasse attendre."),
    },
    "paloma": {
        "voice": "fr-FR-EloiseNeural",
        "rate": "+8%",
        "pitch": "+6Hz",
        "direction": "bubbly best friend, playful, quick (mango)",
        "text": ("Attends, attends, attends ! Tu souris à ton téléphone depuis dix minutes. "
                 "Ne me dis pas que c'est pour un tableau de chiffres. Je te connais par cœur, ma belle : "
                 "il y a quelqu'un. Et je veux tout savoir, maintenant !"),
    },
    "don_maximiliano": {
        "voice": "fr-FR-RemyMultilingualNeural",
        "rate": "-20%",
        "pitch": "-25Hz",
        "direction": "very low, gravelly, slow man (mysterious sponsor pineapple)",
        "text": ("Vendredi, le contrat sera signé. Avant cela, je veux tout savoir. "
                 "Chaque nom, chaque porte, chaque faiblesse. Prends ton temps, mais ne reviens pas "
                 "les mains vides. Je n'aime ni les excuses, ni les surprises."),
    },
}


async def speak(text: str, voice: str, rate: str, pitch: str, dest: Path) -> None:
    """Synthesize one reference with edge-tts into an mp3 file."""
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(str(dest))


def to_reference_wav(src: Path, dest: Path) -> float:
    """Re-encode to mono 24 kHz 16-bit WAV (capped at 30 s) and return its duration."""
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-t", "30", "-ac", "1",
                    "-ar", "24000", "-c:a", "pcm_s16le", str(dest)], check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", str(dest)],
                           check=True, capture_output=True, text=True)
    return float(probe.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="outputs/faille_damour/refs", help="output folder")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    manifest = {}
    for name, spec in VOICES.items():
        mp3 = out / f"ref_{name}.mp3"
        wav = out / f"ref_{name}.wav"
        asyncio.run(speak(spec["text"], spec["voice"], spec["rate"], spec["pitch"], mp3))
        duration = to_reference_wav(mp3, wav)
        mp3.unlink()
        if not 6.0 <= duration <= 30.0:
            raise SystemExit(f"{wav} lasts {duration:.1f} s; Chatterbox needs 6 to 30 s")
        manifest[name] = {
            "file": wav.name,
            "engine": f"edge:{spec['voice']}:{spec['rate']}:{spec['pitch']}",
            "text": spec["text"],
            "direction": spec["direction"],
            "duration_s": round(duration, 2),
            "made_on": date.today().isoformat(),
        }
        print(f"ok  {wav}  {duration:.1f} s")

    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"manifest: {out / 'manifest.json'}")


if __name__ == "__main__":
    main()

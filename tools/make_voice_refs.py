#!/usr/bin/env python3
"""Make the four frozen reference voices of "Accès refusé", once (plan 31).

RUNS ON THE SERVER HOST that holds GOOGLE_API_KEY / GEMINI_API_KEY (the A1,
where .env lives): the coding sandbox cannot reach Gemini. Gemini's prebuilt
voices come first (Orus, Kore, Algenib, Erinome: the casting of
tools/render_ep01.py); edge-tts fr-FR voices when there is no key or Gemini
fails (--engine edge forces them). Each reference is 10-15 s of French, mono
24 kHz 16-bit WAV, written to outputs/acces_refuse/voices/ref_<name>.wav with a
manifest.json saying how it was made.

An existing reference is never remade unless --force: these voices are frozen.
The episodes' lines clone them on the RunPod worker (tools/voice_ep01_comfy.py,
the tts_chatterbox template), so a remake would change every voice of the
series. Nothing goes to the RunPod volume: the job client uploads the reference
with each line.

    python tools/make_voice_refs.py            # Gemini when a key is found, else edge-tts
    python tools/make_voice_refs.py --engine edge --only ananas
    python tools/make_voice_refs.py --force    # recast (then re-voice every episode)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tools/ is not a package
import render_ep01  # noqa: E402  (the Gemini call and the key lookup the episode uses)

ROOT = Path(__file__).resolve().parent.parent
REFS_DIR = ROOT / "outputs" / "acces_refuse" / "voices"
SAMPLE_RATE = 24000      # the repo's voice-reference shape (voice_reference.py): mono 24 kHz 16-bit
MAX_SECONDS = 15.0       # the brief asks 10-15 s; Chatterbox needs at least 6 s

# One entry per character: the Gemini prebuilt voice (render_ep01's casting),
# the edge-tts fallback (fr-FR neural voices; rate/pitch shape the delivery),
# and ~40 words of French in character. The text is sent alone: a spoken
# direction ("say coldly: ...") is read aloud by the flash-lite TTS model.
CHARACTERS = [
    {"name": "RIDA", "slug": "rida", "direction": "firm, low, calm young man (hacker)",
     "gemini": "Orus", "edge": "fr-FR-HenriNeural", "edge_rate": "-5%", "edge_pitch": "-10Hz",
     "text": "Je ne force jamais une porte. Je regarde qui la laisse ouverte, et pourquoi. "
             "Les systèmes sont comme les gens : patients, fiers, et toujours un peu distraits. "
             "Il suffit d'attendre le bon moment, sans bruit, sans précipitation."},
    {"name": "MARIE-JEANNE", "slug": "marie_jeanne", "direction": "confident, poised woman (sales director)",
     "gemini": "Kore", "edge": "fr-FR-DeniseNeural", "edge_rate": "+0%", "edge_pitch": "+0Hz",
     "text": "Je dirige les ventes depuis six ans, et je n'ai jamais perdu un contrat que je voulais vraiment. "
             "On me dit exigeante. C'est faux : je suis précise. Les gens confondent souvent les deux, "
             "surtout quand ils ont quelque chose à cacher."},
    {"name": "ANANAS", "slug": "ananas", "direction": "very low, gravelly, slow man (mysterious sponsor)",
     "gemini": "Algenib", "edge": "fr-FR-RemyMultilingualNeural", "edge_rate": "-20%", "edge_pitch": "-25Hz",
     "text": "Vendredi, le contrat sera signé. Avant cela, je veux tout savoir. Chaque nom, chaque porte, "
             "chaque faiblesse. Prends ton temps, mais ne reviens pas les mains vides. "
             "Je n'aime ni les excuses, ni les surprises."},
    {"name": "INÈS", "slug": "ines", "direction": "clear, dry, sharp woman (head of security)",
     "gemini": "Erinome", "edge": "fr-FR-VivienneMultilingualNeural", "edge_rate": "+5%", "edge_pitch": "+0Hz",
     "text": "La sécurité, c'est moi. Chaque badge, chaque accès, chaque mot échangé dans ce bâtiment "
             "finit sur mon bureau. Vous pouvez sourire, mentir, ou vous taire. Les journaux, eux, "
             "ne mentent jamais. Alors, on commence par quoi ?"},
]
BY_NAME = {c["name"]: c for c in CHARACTERS}


def ref_path(refs_dir: Path, name: str) -> Path:
    """Where a character's frozen reference lives: ref_<slug>.wav."""
    return Path(refs_dir) / f"ref_{BY_NAME[name]['slug']}.wav"


def pick_engine(wanted: str, key: str | None) -> str:
    """'auto' is Gemini with a key, edge-tts without one."""
    if wanted == "auto":
        return "gemini" if key else "edge"
    return wanted


def synth_gemini(character: dict, raw: Path, key: str) -> str:
    model = render_ep01.gemini_tts(character["text"], character["gemini"], key, raw)
    return f"gemini:{character['gemini']}:{model}"


def synth_edge(character: dict, raw: Path) -> str:
    try:
        import edge_tts  # a dependency of the app (clipping/voiceover.py)
    except ImportError:
        sys.exit("edge-tts is not installed: pip install edge-tts, or give a GOOGLE_API_KEY for Gemini")
    mp3 = raw.with_suffix(".mp3")
    communicate = edge_tts.Communicate(character["text"], character["edge"], rate=character["edge_rate"],
                                       pitch=character["edge_pitch"])
    asyncio.run(communicate.save(str(mp3)))
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), str(raw)], check=True)
    mp3.unlink(missing_ok=True)
    return f"edge:{character['edge']}:{character['edge_rate']}:{character['edge_pitch']}"


def finish(raw: Path, out: Path) -> None:
    """The reference shape: silence trimmed at both ends, levelled, mono 24 kHz
    16-bit, capped at MAX_SECONDS."""
    trim = "silenceremove=start_periods=1:start_threshold=-45dB"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af",
                    f"{trim},areverse,{trim},areverse,loudnorm=I=-18:TP=-2",
                    "-ar", str(SAMPLE_RATE), "-ac", "1", "-sample_fmt", "s16", "-t", str(MAX_SECONDS), str(out)],
                   check=True)


def make_refs(characters: list, refs_dir: Path, *, synth, finish=finish, force: bool = False) -> dict:
    """One reference per character, skipped when it exists (unless *force*).
    *synth(character, raw_path) -> str* writes the raw WAV and says how;
    returns the manifest."""
    refs_dir = Path(refs_dir)
    refs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = refs_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    for character in characters:
        out = ref_path(refs_dir, character["name"])
        if out.exists() and not force:
            print(f"keep  {out.name} (frozen; --force to recast)")
            continue
        print(f"voice {out.name}: {character['direction']}")
        raw = refs_dir / f"raw_{character['slug']}.wav"
        how = synth(character, raw)
        finish(raw, out)
        raw.unlink(missing_ok=True)
        manifest[character["name"]] = {"file": out.name, "engine": how, "text": character["text"],
                                       "direction": character["direction"], "made_on": date.today().isoformat()}
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=("auto", "gemini", "edge"), default="auto")
    parser.add_argument("--only", help="one character slug (rida, marie_jeanne, ananas, ines)")
    parser.add_argument("--force", action="store_true", help="recast: remake an existing reference")
    parser.add_argument("--out", type=Path, default=REFS_DIR, help=f"folder (default {REFS_DIR.relative_to(ROOT)})")
    args = parser.parse_args(argv)
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is not installed or not on PATH.")
    characters = [c for c in CHARACTERS if not args.only or c["slug"] == args.only]
    if not characters:
        sys.exit(f"no character {args.only!r}; one of {', '.join(c['slug'] for c in CHARACTERS)}")
    key = render_ep01.find_api_key()
    engine = pick_engine(args.engine, key)
    if engine == "gemini" and not key:
        sys.exit("No GOOGLE_API_KEY / GEMINI_API_KEY (environment or .env): run this on the server host, "
                 "or --engine edge.")
    print(f"engine: {engine} (this needs the network; on the server host, not the sandbox)")

    def synth(character, raw):
        if engine == "gemini":
            try:
                return synth_gemini(character, raw, key)
            except RuntimeError as exc:
                print(f"   Gemini failed ({str(exc).splitlines()[0]}); edge-tts instead")
        return synth_edge(character, raw)

    manifest = make_refs(characters, args.out, synth=synth, force=args.force)
    for name, entry in manifest.items():
        print(f"{name}: {entry['file']} ({entry['engine']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

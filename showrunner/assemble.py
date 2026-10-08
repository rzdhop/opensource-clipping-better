"""Assemble an episode from its approved clips (plan 36 §2.2 step 6, stage 1.5). ffmpeg only, no GPU.

    python -m showrunner.assemble stories/<slug> <episode> [--preset medium]

Reads ``epNN/shots.json`` and ``epNN/takes.json``; writes ``epNN/final.mp4``, ``final.ass`` (the burned
subtitles), ``final.json`` (the report) and ``final_sheet.jpg``. Rules:

- **Approved clips only.** A shot without an approved take stops the assembly, naming every such shot;
  a shot is never filled with a still, a hold or a zoom (DEC-317).
- **A clip is never slowed, stretched or frozen.** Each clip is cut after its last scripted word + 0.3 s
  when it runs on more than 1 s past it (the clip check's ``end_s``), else kept whole.
- **The clip's own sound is the voice track** (path a, D7: picture and voice come together).
- Subtitles from the clip check's timings (the scripted text, one colour per speaker, the speaker's name
  above), drawn by libass from an ASS file (ffmpeg 6.1's drawtext drops accented tails).
- An optional music bed ducked under the voices (sidechain), optional SFX cues, a limiter, then the end
  card ("Partie N+1 demain" / "Part N+1 tomorrow").

The recipe is copied from ``productions/faille_damour/render_ep01.py`` (styles, ducking, limiter) and
``tools/episode_cut.py`` (trim, card), not imported.

``shots.json``::

    {"bgm": {"file": "assets/bgm/suspense/x.mp3", "gain": 0.3} | null,
     "hook": {"text": "...", "seconds": 2.5} | null,
     "card": {"lines": ["Faille d'amour", "Partie 2 demain"], "seconds": 1.5} | null,
     "shots": [{"id": "s01", "lines": [{"speaker": "paloma", "text": "..."}],
                "sfx": [{"file": "assets/sfx/soap/gasp_crowd.wav", "at": 1.2, "gain": 0.8}], ...}]}
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from showrunner import verify  # noqa: E402
from showrunner.store import Story, StoreError, sections  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(REPO_ROOT, "assets", "fonts")
W, H, FPS = 1080, 1920, 24
TRIM_PAD_S = 0.3      # kept after the last scripted word
TRIM_AFTER_S = 1.0    # a clip is cut only when it runs on longer than this past its last word
SUB_TAIL_S = 0.25     # a subtitle stays this long after its line
CARD_SECONDS = 1.5
VOICE_GAIN = 1.6
BGM_GAIN = 0.3
CARD_TEXT = {"fr": "Partie {n} demain", "en": "Part {n} tomorrow"}
# Speaker colours (ASS &HBBGGRR&) by cast order when a sheet has no "## Colour" (render_ep01's palette).
PALETTE = ["&H0098FF&", "&H4B4BFF&", "&H4AC38B&", "&H37AFD4&", "&HDB9DB3&", "&HFFC864&"]

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Dialog,Montserrat Black,74,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,6,2,2,80,80,330,1
Style: Hook,Montserrat Black,64,&H00FFFFFF,&H00FFFFFF,&H00000000,&H70000000,0,0,0,0,100,100,0,0,3,24,0,8,80,80,190,1
Style: Card,Bebas Neue,120,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,0,0,0,0,100,100,2,0,1,6,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


class AssemblyError(RuntimeError):
    pass


# ------------------------------------------------------------------ small helpers

def ass_time(t: float) -> str:
    cs = int(round(max(t, 0.0) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_text(text: str) -> str:
    """Plain text safe inside an ASS event (braces open override blocks, a backslash starts a tag)."""
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def ass_colour(hex_rgb: str) -> str | None:
    m = re.fullmatch(r"#?([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})", hex_rgb.strip())
    return f"&H{m.group(3)}{m.group(2)}{m.group(1)}&".upper() if m else None


def frames_floor(seconds: float, fps: int = FPS) -> float:
    """*seconds* rounded down to whole frames (a cut never adds a frame that is not there)."""
    return math.floor(seconds * fps + 1e-6) / fps


def _run(cmd: list, *, cwd: str | None = None) -> None:
    out = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, check=False)
    if out.returncode:
        raise AssemblyError(f"ffmpeg failed:\n{out.stderr[-2500:]}")


# ------------------------------------------------------------------ the plan

def _speakers(story: Story) -> dict:
    """``{id: {"name": "PALOMA", "colour": "&H...&"}}`` from the cast sheets."""
    out = {}
    for k, cid in enumerate(story.cast()):
        sec = sections(story.read_text(story.sheet(cid)))
        title = re.search(r"^#\s+(.+?)\s*$", sec.get("", ""), re.M)
        colour = ass_colour(sec.get("Colour", "").splitlines()[0]) if sec.get("Colour") else None
        out[cid] = {"name": (title.group(1) if title else cid).upper(), "colour": colour or PALETTE[k % len(PALETTE)]}
    return out


def plan(story: Story, n: int) -> dict:
    """The cut: per shot its approved clip, the trim, and the lines' timings on the episode's clock."""
    sheet = story.read_json(story.shots(n))
    if not sheet or not sheet.get("shots"):
        raise AssemblyError(f"{story.shots(n)} has no shots")
    missing = [s["id"] for s in sheet["shots"] if not story.approved_clip(n, s["id"])]
    if missing:
        raise AssemblyError(f"no approved clip for {', '.join(missing)}: approve a take or make the clip again "
                            f"(a shot is never filled with a still)")
    speakers = _speakers(story)
    segments, events, t = [], [], 0.0
    for shot in sheet["shots"]:
        take = story.approved_take(n, shot["id"])
        clip = story.path(take["path"])
        info = verify.probe(clip)
        verdict = take.get("verdict") or {}
        end_s = verdict.get("end_s")
        trim = info["duration_s"]
        if end_s is not None and info["duration_s"] - end_s > TRIM_AFTER_S:
            trim = end_s + TRIM_PAD_S
        trim = frames_floor(min(trim, info["duration_s"]))
        timed = verdict.get("lines") or []
        for k, line in enumerate(shot.get("lines") or []):
            row = timed[k] if k < len(timed) else {}
            if row.get("start_s") is None:
                events.append({"shot": shot["id"], "speaker": line["speaker"], "text": line["text"], "start": None})
                continue
            start = t + row["start_s"]
            end = t + min(row["end_s"] + SUB_TAIL_S, trim)
            nxt = timed[k + 1] if k + 1 < len(timed) else {}
            if nxt.get("start_s") is not None:
                end = min(end, t + nxt["start_s"] - 0.02)
            who = speakers.get(line["speaker"], {"name": line["speaker"].upper(), "colour": PALETTE[0]})
            events.append({"shot": shot["id"], "speaker": line["speaker"], "name": who["name"], "colour": who["colour"],
                           "text": line["text"], "start": round(start, 3), "end": round(end, 3)})
        segments.append({"shot": shot["id"], "take": take["take"], "clip": clip, "start": round(t, 3),
                         "seconds": trim, "source_s": info["duration_s"], "has_audio": info["has_audio"],
                         "sfx": shot.get("sfx") or []})
        t += trim
    card = sheet.get("card") or {"lines": [story.meta.get("title", ""), CARD_TEXT[story.language].format(n=n + 1)]}
    card = {"lines": [x for x in card.get("lines", []) if x], "seconds": float(card.get("seconds") or CARD_SECONDS)}
    return {"segments": segments, "events": events, "card": card, "card_start": round(t, 3),
            "total_s": round(t + card["seconds"], 3), "bgm": sheet.get("bgm"), "hook": sheet.get("hook")}


def write_ass(cut: dict, path: str) -> int:
    rows = []
    for e in cut["events"]:
        if e["start"] is None:
            continue
        text = (f"{{\\fad(80,80)}}{{\\fs52\\c{e['colour']}}}{ass_text(e['name'])}\\N"
                f"{{\\fs74\\c&HFFFFFF&}}{ass_text(e['text'])}")
        rows.append(f"Dialogue: 0,{ass_time(e['start'])},{ass_time(e['end'])},Dialog,,0,0,0,,{text}")
    hook = cut.get("hook") or {}
    if hook.get("text"):
        rows.append(f"Dialogue: 1,{ass_time(0)},{ass_time(float(hook.get('seconds') or 2.5))},Hook,,0,0,0,,"
                    f"{ass_text(hook['text'])}")
    if cut["card"]["lines"]:
        lines = [ass_text(x) for x in cut["card"]["lines"]]
        body = "\\N".join(lines[:-1])
        text = (body + "\\N{\\c&H00D7FF&}" + lines[-1]) if body else "{\\c&H00D7FF&}" + lines[-1]
        start = cut["card_start"]
        rows.append(f"Dialogue: 1,{ass_time(start)},{ass_time(start + cut['card']['seconds'])},Card,,0,0,0,,{text}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(ASS_HEADER.format(w=W, h=H) + "\n".join(rows) + "\n")
    return len(rows)


# ------------------------------------------------------------------ ffmpeg

def ffmpeg_command(cut: dict, ass_name: str, dest: str, *, preset: str = "medium") -> list:
    """One ffmpeg call: trim + scale + concat the clips and the card, burn the ASS, mix the audio."""
    args, vf, k = ["ffmpeg", "-hide_banner", "-y"], [], 0
    seg_labels = []
    for seg in cut["segments"]:
        args += ["-i", seg["clip"]]
        v = k
        k += 1
        if seg["has_audio"]:
            a = f"{v}:a"
        else:
            args += ["-f", "lavfi", "-t", f"{seg['seconds']}", "-i", "anullsrc=r=48000:cl=stereo"]
            a, k = f"{k}:a", k + 1
        T = f"{seg['seconds']:.4f}"
        vf.append(f"[{v}:v]trim=duration={T},setpts=PTS-STARTPTS,scale={W}:{H}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H},fps={FPS},format=yuv420p,setsar=1[v{len(seg_labels)}]")
        vf.append(f"[{a}]atrim=duration={T},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                  f"volume={VOICE_GAIN}[a{len(seg_labels)}]")
        seg_labels.append(len(seg_labels))
    card_s = cut["card"]["seconds"]
    args += ["-f", "lavfi", "-i", f"color=c=0x0B0B10:s={W}x{H}:r={FPS}:d={card_s}",
             "-f", "lavfi", "-t", f"{card_s}", "-i", "anullsrc=r=48000:cl=stereo"]
    cv, ca = k, k + 1
    k += 2
    vf.append(f"[{cv}:v]format=yuv420p,setsar=1[vc]")
    vf.append(f"[{ca}:a]aformat=channel_layouts=stereo[ac]")
    chain = "".join(f"[v{i}][a{i}]" for i in seg_labels) + "[vc][ac]"
    fonts = FONTS.replace("\\", "/").replace(":", "\\:")
    vf.append(f"{chain}concat=n={len(seg_labels) + 1}:v=1:a=1[vcat][acat]")
    vf.append(f"[vcat]ass={ass_name}:fontsdir='{fonts}'[vout]")
    total = cut["total_s"]
    mix_inputs = []
    bgm = cut.get("bgm")
    if bgm and bgm.get("file"):
        args += ["-stream_loop", "-1", "-i", os.path.join(REPO_ROOT, bgm["file"])]
        b = k
        k += 1
        gain = float(bgm.get("gain", BGM_GAIN))
        vf.append(f"[{b}:a]atrim=duration={total},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                  f"volume={gain},afade=t=in:d=0.4,afade=t=out:st={max(0.0, total - 1.0):.2f}:d=1.0[bgm]")
        vf.append("[acat]asplit=2[vox][key]")
        vf.append("[bgm][key]sidechaincompress=threshold=0.03:ratio=6:attack=20:release=350[duck]")
        mix_inputs = ["[vox]", "[duck]"]
    else:
        mix_inputs = ["[acat]"]
    for seg in cut["segments"]:
        for cue in seg["sfx"]:
            args += ["-i", os.path.join(REPO_ROOT, cue["file"])]
            ms = int(round((seg["start"] + float(cue.get("at", 0.0))) * 1000))
            vf.append(f"[{k}:a]aresample=48000,aformat=channel_layouts=stereo,volume={float(cue.get('gain', 0.8))},"
                      f"adelay={ms}|{ms}[sfx{k}]")
            mix_inputs.append(f"[sfx{k}]")
            k += 1
    if len(mix_inputs) > 1:
        vf.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:normalize=0:duration=first[mix]")
        last = "[mix]"
    else:
        last = mix_inputs[0]
    vf.append(f"{last}atrim=duration={total},alimiter=limit=0.89:level=false[aout]")
    args += ["-filter_complex", ";".join(vf), "-map", "[vout]", "-map", "[aout]",
             "-c:v", "libx264", "-preset", preset, "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS),
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", dest]
    return args


def assemble(story: Story, n: int, *, preset: str = "medium") -> dict:
    cut = plan(story, n)
    final = story.writable(story.final(n))
    ass_rel = f"{story.episode(n)}/final.ass"
    ass_path = story.writable(ass_rel)
    events = write_ass(cut, ass_path)
    # Run in the episode folder so the ASS path needs no escaping in the filter graph.
    tmp = final + ".part.mp4"
    _run(ffmpeg_command(cut, os.path.basename(ass_path), tmp, preset=preset), cwd=os.path.dirname(ass_path))
    os.replace(tmp, final)
    info = verify.report(final)
    sheet = verify.contact_sheet(final, story.writable(f"{story.episode(n)}/final_sheet.jpg"), frames=10)
    report = {"final": story.rel(final), "duration_s": info["duration_s"], "expected_s": cut["total_s"],
              "width": info["width"], "height": info["height"], "fps": info["fps"], "mean_db": info["mean_db"],
              "max_db": info["max_db"], "subtitles": events, "sheet": story.rel(sheet),
              "unsubtitled": [e["shot"] for e in cut["events"] if e["start"] is None],
              "segments": [{k: v for k, v in s.items() if k not in ("clip", "sfx")} | {"clip": story.rel(s["clip"])}
                           for s in cut["segments"]], "bgm": (cut.get("bgm") or {}).get("file")}
    story.write_json(f"{story.episode(n)}/final.json", report)
    if not story.exists(story.metadata(n)):
        story.write_text(story.metadata(n), f"# {story.meta.get('title', story.slug)} — episode {n}\n\n## Title\n\n"
                                            "## Hook text (2 variants)\n\n## Comment bait\n")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("story", help="the story folder, stories/<slug>")
    ap.add_argument("episode", type=int)
    ap.add_argument("--preset", default="medium", help="x264 preset (ultrafast for a quick look)")
    args = ap.parse_args()
    try:
        report = assemble(Story.open(args.story), args.episode, preset=args.preset)
    except (AssemblyError, StoreError) as exc:
        sys.exit(str(exc))
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

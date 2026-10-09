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
- **The punch-in edit** (on by default): the camera never moves inside a clip, so the energy comes from the cut.
  Each clip is split at its line boundaries; the first line plays wide, every following line punches in on its
  speaker (a crop of the *moving* clip toward the speaker's side, never a still, never a zoom ramp). A clip with
  one character punches in once, mid-way. Timings do not change, so the subtitles and SFX stay where they are.
  The speaker's side comes from the order of ``characters`` (left to right, as the keyframe prompt places them)
  unless the shot gives ``positions``.

The recipe is copied from ``productions/faille_damour/render_ep01.py`` (styles, ducking, limiter) and
``tools/episode_cut.py`` (trim, card), not imported.

``shots.json``::

    {"bgm": {"file": "assets/bgm/suspense/x.mp3", "gain": 0.3} | null,
     "hook": {"text": "...", "seconds": 2.5} | null,
     "card": {"lines": ["Faille d'amour", "Partie 2 demain"], "seconds": 1.5} | null,
     "punch_in": true,                                  # optional, default true: the punch-in edit
     "shots": [{"id": "s01", "characters": ["paloma", "rida"],          # left to right in the picture
                "positions": {"rida": "left"},                          # optional override: left | center | right
                "punch_in": false,                                      # optional: this clip stays wide
                "lines": [{"speaker": "paloma", "text": "..."}],
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
# The punch-in edit (Rida, 2026-10-09: camera still in the clips, the energy from the cut).
PUNCH_SIDE = 1.25     # crop factor toward the speaker of a clip with two or three characters
PUNCH_SOLO = 1.15     # crop factor of the mid-way punch-in of a clip with one character
PUNCH_TOP = 0.04      # the crop keeps the top of the frame, where the heads are
PUNCH_SOLO_AT = 0.45  # a one-character clip punches in at this share of its length...
SOLO_MIN_S = 3.0      # ...when it lasts at least this long
PIECE_MIN_S = 0.8     # a framing shorter than this is merged into its neighbour (no flash cuts)
SIDES = {1: ("center",), 2: ("left", "right"), 3: ("left", "center", "right")}
FRAMINGS = ("wide", "left", "center", "right")
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


# ------------------------------------------------------------------ the punch-in edit

def _sides(shot: dict) -> dict:
    """``{character: framing}``: left to right in the order of ``characters`` (else of the speakers), then the
    shot's own ``positions``. More than three characters: only the given positions."""
    chars = list(shot.get("characters") or [])
    for line in shot.get("lines") or []:
        if line.get("speaker") and line["speaker"] not in chars:
            chars.append(line["speaker"])
    sides = dict(zip(chars, SIDES.get(len(chars), ())))
    for cid, side in (shot.get("positions") or {}).items():
        if side in FRAMINGS[1:]:
            sides[cid] = side
    return sides


def _merge(pieces: list) -> list:
    """Neighbours with the same framing become one; a piece shorter than PIECE_MIN_S joins its neighbour."""
    out = []
    for p in pieces:
        if out and out[-1]["framing"] == p["framing"]:
            out[-1]["end"] = p["end"]
        else:
            out.append(dict(p))
    k = 0
    while len(out) > 1 and k < len(out):
        if out[k]["end"] - out[k]["start"] >= PIECE_MIN_S:
            k += 1
            continue
        if k == 0:
            out[1]["start"] = out[0]["start"]
            del out[0]
        else:
            out[k - 1]["end"] = out[k]["end"]
            del out[k]
            if k < len(out) and out[k - 1]["framing"] == out[k]["framing"]:
                out[k - 1]["end"] = out[k]["end"]
                del out[k]
        k = 0
    return out


def pieces(shot: dict, timed: list, trim: float, *, enabled: bool = True) -> list:
    """The framings of one clip on its own clock: ``[{"start", "end", "framing", "zoom"}]``, end to end over
    ``[0, trim]``. The first line plays wide; each next line punches in on its speaker, from the middle of the
    silence before it. A clip with one character (or one speaker) punches in once, at PUNCH_SOLO_AT of its
    length, when it lasts at least SOLO_MIN_S."""
    wide = [{"start": 0.0, "end": trim, "framing": "wide", "zoom": 1.0}]
    if not enabled or shot.get("punch_in") is False or trim <= 0:
        return wide
    sides = _sides(shot)
    lines = shot.get("lines") or []
    rows = [(line.get("speaker"), (timed[k] if k < len(timed) else {})) for k, line in enumerate(lines)]
    rows = [(who, r) for who, r in rows if r.get("start_s") is not None and r.get("end_s") is not None]
    speakers = {who for who, _ in rows}
    if len(sides) >= 2 and len(speakers) >= 2:
        cuts = [(0.0, "wide")]
        for k in range(1, len(rows)):
            at = (rows[k - 1][1]["end_s"] + rows[k][1]["start_s"]) / 2
            side = sides.get(rows[k][0])
            cuts.append((frames_floor(min(max(at, 0.0), trim)), side or "wide"))
        out = [{"start": a, "end": (cuts[i + 1][0] if i + 1 < len(cuts) else trim), "framing": f,
                "zoom": PUNCH_SIDE if f != "wide" else 1.0} for i, (a, f) in enumerate(cuts)]
        out = [p for p in out if p["end"] > p["start"]]
    else:
        if trim < SOLO_MIN_S:
            return wide
        at = frames_floor(min(max(trim * PUNCH_SOLO_AT, 1.5), trim - 1.2))
        side = sides.get(next(iter(speakers)), "center") if len(speakers) == 1 and len(sides) >= 2 else "center"
        zoom = PUNCH_SOLO if side == "center" else PUNCH_SIDE
        out = [{"start": 0.0, "end": at, "framing": "wide", "zoom": 1.0},
               {"start": at, "end": trim, "framing": side, "zoom": zoom}]
    out = _merge(out)
    out[0]["start"], out[-1]["end"] = 0.0, trim
    return [{**p, "start": round(p["start"], 4), "end": round(p["end"], 4)} for p in out]


def punch_filter(framing: str, zoom: float) -> str:
    """The crop of one framing, scaled back to the frame (an empty string for the wide framing)."""
    if framing == "wide" or zoom <= 1.0:
        return ""
    x = {"left": "0", "center": "(iw-ow)/2", "right": "iw-ow"}[framing]
    return (f"crop=w=trunc(iw/{zoom}/2)*2:h=trunc(ih/{zoom}/2)*2:x={x}:y=ih*{PUNCH_TOP},"
            f"scale={W}:{H},setsar=1")


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
    punch_in = sheet.get("punch_in", True) is not False
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
                         "pieces": pieces(shot, timed, trim, enabled=punch_in), "sfx": shot.get("sfx") or []})
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
        n = len(seg_labels)
        parts = seg.get("pieces") or [{"start": 0.0, "end": seg["seconds"], "framing": "wide", "zoom": 1.0}]
        base = (f"[{v}:v]trim=duration={T},setpts=PTS-STARTPTS,scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H},fps={FPS},format=yuv420p,setsar=1")
        if len(parts) == 1 and not punch_filter(parts[0]["framing"], parts[0]["zoom"]):
            vf.append(f"{base}[v{n}]")
        else:
            # The punch-in edit: the same moving clip, split at the line boundaries, each piece cropped
            # toward its speaker; the pieces are cut back to back, so the clip keeps its length and its sound.
            vf.append(f"{base},split={len(parts)}" + "".join(f"[p{n}_{j}]" for j in range(len(parts))))
            for j, p in enumerate(parts):
                crop = punch_filter(p["framing"], p["zoom"])
                vf.append(f"[p{n}_{j}]trim=start={p['start']:.4f}:end={p['end']:.4f},setpts=PTS-STARTPTS"
                          + (f",{crop}" if crop else "") + f"[q{n}_{j}]")
            vf.append("".join(f"[q{n}_{j}]" for j in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0[v{n}]")
        vf.append(f"[{a}]atrim=duration={T},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,"
                  f"volume={VOICE_GAIN}[a{n}]")
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
              "cuts": sum(len(s["pieces"]) for s in cut["segments"]),
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

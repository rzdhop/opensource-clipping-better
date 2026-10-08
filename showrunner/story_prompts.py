"""The prompts of a story's GPU jobs, built from the story's own files (plan 36 stage 3, finding 2).

A chat cannot import :mod:`showrunner.prompts`; without this module it would fill the templates by hand, and
the batch-a golden (pinned by a test) would drift at the first episode. Every keyframe, clip and cast image
prompt is built here from:

- ``01-universe.md`` ``## Medium`` (the style lock, D8),
- ``02-cast/<char>/sheet.md`` ``# Name``, ``## Head``, ``## Voice (en)``,
- ``03-places/<place>/plate.md`` ``## Setting``,
- ``epNN/shots.json``: per shot ``id``, ``seconds`` (5 or 10), ``place``, ``characters`` (who is in frame, in
  order), ``lines`` (``[{speaker, text}]`` in the order spoken), and optionally ``framing``
  (:data:`prompts.KEYFRAME_FRAMINGS`, default ``faces_camera``), ``expression`` (the keyframe's face) and
  ``reaction`` (what the face shows in a silent shot).

The MCP server exposes it as ``prompt_keyframe`` / ``prompt_clip`` / ``prompt_cast`` and feeds ``comfy_submit``
with it (``prompt_from``). Nothing here is creative: the words come from the story's files and the templates.
Stdlib only.
"""

from __future__ import annotations

import math

from showrunner import prompts as P
from showrunner import store as st

KEYFRAME_SIZE = (704, 1280)          # the clip's own size (LTX-2.5, 64 px grid): I2V keeps the start image
CLIP_SECONDS = (5, 10)               # LTX frames 8n+1 at 24 fps: 121 f = 5 s, 241 f = 10 s (plan 36 §2.2 step 5)
WORDS_PER_S, OVERHEAD_S = 2.4, 0.7   # speech capacity (the old native_speech math, kept by the plan)
CAST_KINDS = ("full_body", "turnaround", "emotions")
CLIP_TEMPLATE = "ltx25_i2v_speech"   # D7: picture and voice in one pass
T2I_TEMPLATE, EDIT_TEMPLATE = "t2i_flux2_klein", "edit_flux2_klein_multiref"
EDIT_SLOTS = ("ref1", "ref2", "ref3", "ref4")
SPEC_KINDS = ("keyframe", "clip", "cast")


class StoryPromptError(ValueError):
    pass


# ------------------------------------------------------------------ the story's own words

def _section(story: st.Story, relpath: str, heading: str) -> str:
    if not story.exists(relpath):
        raise StoryPromptError(f"no {relpath} in {story.slug}")
    body = story.sections(relpath).get(heading, "").strip()
    if not body:
        raise StoryPromptError(f"{relpath} has no '## {heading}' (or it is empty)")
    return body


def medium(story: st.Story) -> str:
    """``01-universe.md`` ``## Medium``: the sentence that opens every prompt (agreed in chat first, D8)."""
    return " ".join(_section(story, "01-universe.md", "Medium").split())


def setting(story: st.Story, place: str) -> str:
    if not place:
        raise StoryPromptError("the shot has no 'place' (a folder of 03-places/)")
    return " ".join(_section(story, story.plate(place), "Setting").split())


def member(story: st.Story, char: str) -> dict:
    """``prompts.character(...)`` of one cast member, from its ``sheet.md``."""
    rel = story.sheet(char)
    if not story.exists(rel):
        raise StoryPromptError(f"no character sheet {rel}")
    try:
        return P.character_from_sheet(story.sections(rel))
    except P.PromptError as exc:
        raise StoryPromptError(str(exc)) from exc


def shot(story: st.Story, episode: int, shot_id: str) -> dict:
    data = story.read_json(story.shots(episode)) or {}
    for entry in data.get("shots") or []:
        if entry.get("id") == shot_id:
            return entry
    raise StoryPromptError(f"no shot {shot_id} in {story.shots(episode)}")


def in_frame(entry: dict) -> list:
    """Who is in the picture: ``characters``, else the speakers in order of their first line."""
    frame = list(entry.get("characters") or [])
    if not frame:
        frame = list(dict.fromkeys(line.get("speaker") for line in entry.get("lines") or []))
    if not frame:
        raise StoryPromptError(f"shot {entry.get('id')} has no 'characters' and no lines")
    return frame


# ------------------------------------------------------------------ the speech budget

def max_words(seconds: float) -> int:
    """The most words a clip of *seconds* can speak (≈ 2.4 words/s after a beat of silence)."""
    return max(0, math.floor((float(seconds) - OVERHEAD_S) * WORDS_PER_S))


def budget(seconds: float, lines: list) -> dict:
    words = sum(P.words(line.get("text", "")) for line in lines)
    cap = max_words(seconds)
    return {"words": words, "max_words": cap, "fits": words <= cap}


# ------------------------------------------------------------------ builders

def _refs(paths: list) -> dict:
    """The edit template's four reference slots, filled in order and repeated to fill all four."""
    if not paths:
        return {}
    return {slot: paths[k % len(paths)] for k, slot in enumerate(EDIT_SLOTS)}


def keyframe(story: st.Story, episode: int, shot_id: str) -> dict:
    """The start image of a shot: the prompt, its size, and what to send it with (text to image, or the
    multi-reference edit when every character in frame has a locked ``full_body.png``)."""
    entry = shot(story, episode, shot_id)
    frame = in_frame(entry)
    chars = [member(story, c) for c in frame]
    framing = entry.get("framing") or "faces_camera"
    try:
        prompt = P.keyframe(medium(story), chars, setting(story, entry.get("place", "")), framing=framing,
                            expression=entry.get("expression") or P.DEFAULT_EXPRESSION)
    except P.PromptError as exc:
        raise StoryPromptError(f"{shot_id}: {exc}") from exc
    bodies = [f"{story.cast_dir(c)}/full_body.png" for c in frame]
    have = all(story.exists(b) and story.is_locked(b) for b in bodies)
    width, height = KEYFRAME_SIZE
    return {"kind": "keyframe", "episode": episode, "shot": shot_id, "prompt": prompt, "width": width,
            "height": height, "characters": frame, "framing": framing if len(frame) == 1 else "together",
            "template": EDIT_TEMPLATE if have else T2I_TEMPLATE, "files": _refs(bodies) if have else {},
            "dest": f"{st.episode_dir(episode)}/keyframes/candidates/{shot_id}_c<k>"}


def clip(story: st.Story, episode: int, shot_id: str) -> dict:
    """The LTX-2.5 I2V prompt of a shot (D7): the s33 golden for one speaker, the s22 golden for two or three,
    the reaction template for a silent shot. Includes the speech budget of its lines."""
    entry = shot(story, episode, shot_id)
    seconds = entry.get("seconds", 5)
    if seconds not in CLIP_SECONDS:
        raise StoryPromptError(f"{shot_id}: 'seconds' is {seconds!r}; a clip is 5 or 10 s")
    frame = in_frame(entry)
    lines = entry.get("lines") or []
    if any(not line.get("speaker") or not str(line.get("text", "")).strip() for line in lines):
        raise StoryPromptError(f"{shot_id}: every line needs a 'speaker' and a 'text'")
    place = setting(story, entry.get("place", ""))
    cast = {c: member(story, c) for c in dict.fromkeys(frame + [line.get("speaker") for line in lines])}
    try:
        if lines:
            turns = [(line["speaker"], line["text"]) for line in lines]
            prompt = P.dialogue_clip(medium(story), cast, place, turns, language=story.language, in_frame=frame)
        else:
            if len(frame) != 1:
                raise StoryPromptError(f"{shot_id}: a silent shot holds one listener, not {len(frame)}")
            if not entry.get("reaction"):
                raise StoryPromptError(f"{shot_id}: a silent shot needs 'reaction' (what the face shows)")
            prompt = P.reaction_clip(medium(story), cast[frame[0]], place, entry["reaction"])
    except P.PromptError as exc:
        raise StoryPromptError(f"{shot_id}: {exc}") from exc
    image = entry.get("keyframe") or story.keyframe(episode, shot_id)
    return {"kind": "clip", "episode": episode, "shot": shot_id, "prompt": prompt, "seconds": seconds,
            "template": CLIP_TEMPLATE, "files": {"image": image}, "keyframe_ready": story.exists(image),
            "budget": budget(seconds, lines)}


def cast(story: st.Story, char: str, kind: str) -> dict:
    """A cast image: the canonical ``full_body`` (text to image, 3-5 candidates), then ``turnaround`` and
    ``emotions`` (multi-reference edits of the LOCKED ``full_body.png``)."""
    if kind not in CAST_KINDS:
        raise StoryPromptError(f"kind must be one of {', '.join(CAST_KINDS)}")
    c = member(story, char)
    m = medium(story)
    build = {"full_body": P.full_body, "turnaround": P.turnaround, "emotions": P.emotions}[kind]
    width, height = P.CAST_IMAGE_SIZE
    out = {"kind": "cast", "character": char, "image": kind, "prompt": build(m, c), "width": width, "height": height}
    if kind == "full_body":
        return {**out, "template": T2I_TEMPLATE, "files": {}, "dest": f"{story.cast_dir(char)}/candidates/full_body_c<k>"}
    body = f"{story.cast_dir(char)}/full_body.png"
    if not (story.exists(body) and story.is_locked(body)):
        raise StoryPromptError(f"{kind} starts from the locked {body}: pick and lock the full body first")
    return {**out, "template": EDIT_TEMPLATE, "files": _refs([body]), "dest": f"{story.cast_dir(char)}/candidates/{kind}_c<k>"}


def from_spec(story: st.Story, spec: str) -> dict:
    """``"keyframe:<ep>:<shot>"`` · ``"clip:<ep>:<shot>"`` · ``"cast:<char>:<kind>"`` -> the built prompt."""
    parts = [p.strip() for p in (spec or "").split(":")]
    if len(parts) != 3 or parts[0] not in SPEC_KINDS or not all(parts):
        raise StoryPromptError(f"prompt_from {spec!r}: write keyframe:<episode>:<shot>, clip:<episode>:<shot> "
                               "or cast:<character>:<full_body|turnaround|emotions>")
    kind, a, b = parts
    if kind == "cast":
        return cast(story, a, b)
    try:
        episode = int(a)
    except ValueError as exc:
        raise StoryPromptError(f"prompt_from {spec!r}: the episode is a number") from exc
    return (keyframe if kind == "keyframe" else clip)(story, episode, b)


def record(story: st.Story, built: dict) -> bool:
    """Write a built keyframe/clip prompt into its shot (``keyframe_prompt`` / ``clip_prompt``), so the story
    keeps what it sends. A locked ``shots.json`` is left as it is (the job journal keeps the prompt anyway)."""
    if built.get("kind") not in ("keyframe", "clip"):
        return False
    rel = story.shots(built["episode"])
    if story.is_locked(rel):
        return False
    data = story.read_json(rel) or {}
    for entry in data.get("shots") or []:
        if entry.get("id") == built["shot"]:
            entry[f"{built['kind']}_prompt"] = built["prompt"]
            story.write_json(rel, data)
            return True
    return False

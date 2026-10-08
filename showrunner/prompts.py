"""Prompt templates (``showrunner/prompts/*.md``) and the builders that fill them.

A template is a markdown file: an optional ``<!-- notes -->`` header, then the text with
``{{placeholders}}``. :func:`render` fills it; a missing value or a leftover ``{{`` raises. Clip and
image prompts are one line (the template's line breaks become single spaces *before* the values go
in, so a value is never altered); documents (``character_sheet``, ``universe``) keep their layout.

Nothing about a universe is written here: the medium sentence, the heads and the voices come from the
story's own files (``01-universe.md``, ``02-cast/<char>/sheet.md``, D8). The dialogue templates are the
batch-a prompts Rida preferred, word for word except :data:`CLEAN_FRAME` (golden test).
"""

from __future__ import annotations

import os
import re

PROMPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")
HEADER = re.compile(r"^\s*<!--.*?-->\s*", re.S)

# Every workflow samples at cfg 1.0: the negative prompt is ignored, so a clean picture is asked for in the
# positive. Naming what is unwanted ("no subtitles") primed burned-in captions in 6/16 clips of batch a.
CLEAN_FRAME = "One continuous, clean cinematic shot from the first frame to the last."
# Words a positive prompt never contains (they prime what they name).
UNWANTED_WORDS = ("subtitle", "caption", "on-screen text")

LANGUAGE_NAME = {"fr": "French", "en": "English"}
MAX_SPEAKERS = 3  # D6/D7: 1-3 characters in a clip

# The keyframe's framing. "faces_camera" is the stage-0 wording and the default. "three_quarter" is A-222,
# a hypothesis: its first wording ("turned toward someone just off-screen beside the camera") drew that
# someone, a stray human at the frame's edge (smoke, 2026-10-08). A framing names nobody but the characters
# in frame (test); this one is checked once on the GPU before a shot relies on it.
KEYFRAME_FRAMINGS = {
    "faces_camera": "{name} faces the camera",
    "three_quarter": "{name} with the head turned three-quarters to the right, eyes looking past the right edge of the frame",
    "together": "{names} stand close together, facing each other mid-conversation",
}
# Words a keyframe framing never contains (whole words): each one invites the image model to draw a
# person who is not in the shot. ("each other" between the characters in frame is fine.)
FRAMING_FORBIDDEN = ("someone", "somebody", "anyone", "person", "people", "off-screen", "offscreen", "another",
                     "stranger", "crowd")


def framing_intruders(text: str) -> list:
    """The :data:`FRAMING_FORBIDDEN` words in *text* (whole words, any case)."""
    low = text.lower()
    return [w for w in FRAMING_FORBIDDEN if re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", low)]
DEFAULT_EXPRESSION = "a tense and composed expression"
WHO = {1: "only this one character, nobody else", 2: "only these two characters, nobody else",
       3: "only these three characters, nobody else"}

# The engine's default size for a cast image (Flux 2 Klein); clips are 704x1280 (LTX, 64 px grid).
CAST_IMAGE_SIZE = (832, 1216)


class PromptError(ValueError):
    pass


def load(name: str) -> str:
    """The text of ``prompts/<name>.md`` without its notes header."""
    path = os.path.join(PROMPTS_DIR, f"{name}.md")
    with open(path, encoding="utf-8") as fh:
        return HEADER.sub("", fh.read(), count=1)


def names() -> list:
    return sorted(f[:-3] for f in os.listdir(PROMPTS_DIR) if f.endswith(".md"))


def placeholders(name: str) -> list:
    return sorted(set(PLACEHOLDER.findall(load(name))))


def render(_template: str, *, oneline: bool = True, **values) -> str:
    """``prompts/<_template>.md`` with every ``{{key}}`` filled from *values* (``name`` is a placeholder of
    several templates, so the template's own argument is ``_template``)."""
    name = _template
    text = load(name)
    if oneline:
        text = " ".join(text.split())
    else:
        text = text.strip() + "\n"
    missing = sorted({k for k in PLACEHOLDER.findall(text) if k not in values})
    if missing:
        raise PromptError(f"{name} needs values for: {', '.join(missing)}")
    out = PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), text)
    if "{{" in out:
        raise PromptError(f"{name}: a value left a placeholder in the prompt")
    return out


def unwanted_words(text: str) -> list:
    low = text.lower()
    return [w for w in UNWANTED_WORDS if w in low]


# ------------------------------------------------------------------ characters

def character(name: str, head: str, voice: str) -> dict:
    """What a prompt needs of a character: its display name, its master head prompt (no final
    period) and its voice description in English (the models are prompted in English, D4)."""
    return {"name": name, "head": head.strip().rstrip("."), "voice": voice.strip()}


def character_from_sheet(sheet_sections: dict) -> dict:
    """From the sections of a ``sheet.md`` (:func:`showrunner.store.sections`)."""
    title = re.search(r"^#\s+(.+?)\s*$", sheet_sections.get("", ""), re.M)
    if not title:
        raise PromptError("sheet.md needs a '# <Name>' title")
    for key in ("Head", "Voice (en)"):
        if not sheet_sections.get(key):
            raise PromptError(f"sheet.md of {title.group(1)} has no '## {key}'")
    return character(title.group(1), sheet_sections["Head"], sheet_sections["Voice (en)"])


def words(text: str) -> int:
    return len(re.findall(r"[^\W_][\w'’-]*", text))


def _language(language: str) -> str:
    return LANGUAGE_NAME.get(language, language)


# ------------------------------------------------------------------ builders

def dialogue_framing(name: str) -> str:
    return render("framing_dialogue", name=name)


def dialogue_clip(medium: str, cast: dict, setting: str, turns: list, *, language: str,
                  in_frame: list | None = None) -> str:
    """The LTX-2.5 I2V prompt of a speaking clip.

    *cast*: ``{id: character(...)}``; *turns*: ``[(id, line), ...]`` in the order they are spoken;
    *in_frame*: who is in the picture (default: the speakers, in order of first line). One character in
    frame -> ``clip_dialogue`` (the s33 golden); two or three -> ``clip_exchange`` (the s22 golden).
    """
    if not turns:
        raise PromptError("a speaking clip needs at least one line")
    unknown = [who for who, _ in turns if who not in cast]
    if unknown:
        raise PromptError(f"no character sheet for: {', '.join(sorted(set(unknown)))}")
    frame = list(in_frame) if in_frame else list(dict.fromkeys(who for who, _ in turns))
    missing = [who for who, _ in turns if who not in frame]
    if missing:
        raise PromptError(f"speakers not in frame: {', '.join(sorted(set(missing)))}")
    if len(frame) > MAX_SPEAKERS:
        raise PromptError(f"{len(frame)} characters in one clip; at most {MAX_SPEAKERS}")
    lang = _language(language)
    if len(frame) == 1:
        if len(turns) != 1:
            raise PromptError("one character in frame speaks one line per clip")
        who, line = turns[0]
        c = cast[who]
        return render("clip_dialogue", medium=medium, head=c["head"], setting=setting,
                      framing=dialogue_framing(c["name"]), language=lang, voice=c["voice"], line=line,
                      clean_frame=CLEAN_FRAME)
    heads = " ".join(cast[c]["head"] + "." for c in frame)
    spoken = " ".join(render("turn", name=cast[who]["name"], language=lang, voice=cast[who]["voice"], line=line)
                      for who, line in turns)
    return render("clip_exchange", medium=medium, heads=heads, setting=setting, turns=spoken, clean_frame=CLEAN_FRAME)


def reaction_clip(medium: str, char: dict, setting: str, reaction: str) -> str:
    return render("clip_reaction", medium=medium, head=char["head"], setting=setting, name=char["name"],
                  reaction=reaction, clean_frame=CLEAN_FRAME)


def keyframe(medium: str, chars: list, setting: str, *, framing: str = "faces_camera",
             expression: str = DEFAULT_EXPRESSION) -> str:
    """The start image of a shot with *chars* (1-3 ``character(...)`` dicts) in frame."""
    if not 1 <= len(chars) <= MAX_SPEAKERS:
        raise PromptError(f"a keyframe holds 1 to {MAX_SPEAKERS} characters, not {len(chars)}")
    if framing not in KEYFRAME_FRAMINGS:
        raise PromptError(f"framing must be one of {sorted(KEYFRAME_FRAMINGS)}")
    if len(chars) > 1:
        framing = "together"
    names_ = " and ".join(c["name"] for c in chars) if len(chars) < 3 else \
        f"{chars[0]['name']}, {chars[1]['name']} and {chars[2]['name']}"
    framed = KEYFRAME_FRAMINGS[framing].format(name=chars[0]["name"], names=names_)
    heads = " ".join(c["head"] + "." for c in chars)
    return render("keyframe", medium=medium, heads=heads, setting=setting, framing=framed, expression=expression,
                  who=WHO[len(chars)])


def full_body(medium: str, char: dict) -> str:
    """The canonical cast image (cast step 1), sent at :data:`CAST_IMAGE_SIZE`."""
    return render("full_body", medium=medium, head=char["head"])


def turnaround(medium: str, char: dict) -> str:
    return render("turnaround", medium=medium, head=char["head"])


def emotions(medium: str, char: dict) -> str:
    return render("emotions", medium=medium, head=char["head"])

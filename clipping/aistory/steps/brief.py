"""The shot brief: what the human needs to make an episode's clips on their own
subscription (plan 22 stage 5, the manual link).

A story whose clips are on ``manual/upload`` (the ``native_speech_manual``
profile) is made in two hands: the app writes, plans, draws the keyframes,
times, subtitles and renders; the human makes each clip on Google Flow or
Higgsfield / Freepik and uploads it. This module writes the human's half of
the contract, calling nothing:

* :func:`shot_brief` -- one entry per shot of the episode's storyboard, in
  order: what the shot must show (``purpose``, one sentence from the scene's
  summary and the shot's action, so a take can be judged by eye), the
  prompt -- stage 4's own (``clips.speech_request_parts`` for a speaking
  shot, ``clips.clip_request_parts``' ambience prompt for a silent one) --
  rephrased for the platform's preset (``clipping.aistory.platforms``), the
  negative prompt, the length to pick and the aspect, the reference images
  in priority order (the shot's keyframe when it exists, then the speaker's
  sheet, the listener's sheet, the place's plate; cut to the platform's
  maximum), the line in the story's language with the speaker's voice
  line, what to check before uploading, the upload slot (the API path),
  and where the shot stands (``missing``, ``uploaded``, ``take_ok``,
  ``mismatch``, ``approximate``);
* :func:`image_brief` -- the same for the sheets, plates, props and
  keyframes when the story's images are manual too
  (``media_policy.images_manual``);
* :func:`render_markdown` -- the brief for a human: numbered shots,
  copy-ready fenced prompt blocks, the reference file names, the line, the
  checks;
* :func:`missing_clips` -- the shots on a manual link with no current
  clip: what the assets step waits for (``awaiting_uploads``);
* :func:`write_brief` / :func:`brief_zip` -- ``assets/brief/shot_brief.json``
  and ``shot_brief.md`` next to the episode's other assets, and a zip of the
  ``.md`` with every referenced image.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import io
import json
import os
import re
import zipfile

from clipping.providers import generation as gen

from .. import media_policy, platforms, prompting, schemas
from .. import shots as shots_mod
from . import clips, episode_common

SCHEMA = "shot_brief_v1"
IMAGE_SCHEMA = "image_brief_v1"
BRIEF_DIR = "assets/brief"
JSON_NAME = "shot_brief.json"
MD_NAME = "shot_brief.md"
IMAGE_JSON_NAME = "image_brief.json"
IMAGE_MD_NAME = "image_brief.md"
BRIEF_NAMES = (JSON_NAME, MD_NAME, IMAGE_JSON_NAME, IMAGE_MD_NAME)
ASPECT = "9:16"
# Where a shot stands for the human: no clip yet (or one that no longer
# matches its line); a clip uploaded (silent, or its take not checked yet);
# a speaking clip whose take heard its line; one whose take did not; one
# whose take could not be checked (no STT key: the subtitles are approximate).
STATES = ("missing", "uploaded", "take_ok", "mismatch", "approximate")
_TAKE_STATES = {"ok": "take_ok", "mismatch": "mismatch", "no_speech": "mismatch", "stt_unavailable": "approximate"}
PURPOSE_MAX_WORDS = 40

_TAG = re.compile(r"(@char_[a-z0-9_]+|#place_[a-z0-9_]+(?::[a-z][a-z0-9_]*)?|%prop_[a-z0-9_]+)")


# ------------------------------------------------------------------ helpers

def upload_slot(story_id, ep, shot_id) -> str:
    """The API path a shot's clip is uploaded to."""
    return f"/api/stories/{story_id}/episodes/{ep}/shots/{shot_id}/clip"


def keyframe_slot(story_id, ep, shot_id) -> str:
    return f"/api/stories/{story_id}/episodes/{ep}/shots/{shot_id}/keyframe"


def _names(ec) -> dict:
    """``{entity_id: name}`` of every character, place and prop."""
    names = {}
    for kind, key in (("characters", "char_id"), ("places", "place_id"), ("props", "prop_id")):
        for eid, doc in ((ec.entities or {}).get(kind) or {}).items():
            names[eid] = doc.get("name") or eid
    return names


def named(ec, text) -> str:
    """*text* with every subject tag (``@char_x``, ``#place_y:day``,
    ``%prop_z``) as the entity's name -- the brief is for the human, who
    knows them by name."""
    names = _names(ec)

    def name_of(match):
        tag = match.group(1)
        try:
            _kind, eid, _variant = shots_mod.parse_tag(tag)
        except ValueError:
            return tag
        return names.get(eid, eid)

    return " ".join(_TAG.sub(name_of, str(text or "")).split())


def _sentence(text) -> str:
    text = " ".join(str(text or "").split()).strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?…" else text + "."


def _cut_words(text, limit) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]).rstrip(",;:") + "…"


def _scene(script, shot):
    return next((scene for scene in script["scenes"] if scene["scene_id"] == shot["scene_id"]), None) or {}


def _in_frame(shot) -> list:
    """The character ids in *shot*'s frame, in its subject tags' order."""
    ids = []
    for tag in shot.get("subject_tags") or ():
        try:
            kind, eid, _variant = shots_mod.parse_tag(tag)
        except ValueError:
            continue
        if kind == "char" and eid not in ids:
            ids.append(eid)
    return ids


def purpose(ec, script, shot) -> str:
    """What *shot* must show, in one sentence a human can judge a take by:
    its scene's summary and the shot's own action, by the entities' names
    (at most :data:`PURPOSE_MAX_WORDS` words)."""
    scene = _scene(script, shot)
    summary = named(ec, scene.get("summary") or "").rstrip(".")
    action = named(ec, shot.get("action") or "").rstrip(".")
    if summary and action:
        text = f"{summary} — {action}"
    else:
        text = summary or action or "A shot of the scene"
    return _sentence(_cut_words(text, PURPOSE_MAX_WORDS))


# ------------------------------------------------------------- the references

def _entity_ref(ec, kind, eid, entry, label):
    """A reference image of an entity: ``{kind, label, path, name, url}``
    (``path`` story-relative, as a shot's ``reference_images`` names it), or
    None when it is not on disk."""
    if not isinstance(entry, dict) or not entry.get("name"):
        return None
    name = entry["name"]
    try:
        ec.store.media_path(ec.story_id, kind, eid, name)
    except KeyError:
        return None
    return {"label": label, "path": f"{kind}/{eid}/refs/{name}", "name": name,
            "url": f"/api/stories/{ec.story_id}/media/{kind}/{eid}/{name}"}


def keyframe_path(ec, shot):
    """The real path of *shot*'s keyframe on disk, or None."""
    image = (shot.get("assets") or {}).get("image")
    if not image:
        return None
    try:
        path = ec.store.episode_asset_path(ec.story_id, ec.ep, "shots", image.rpartition("/")[2])
    except KeyError:
        return None
    return path if os.path.isfile(path) else None


def references(ec, script, shot) -> list:
    """*shot*'s reference images in priority order, uncut: its keyframe when
    one is on disk, then the speaker's sheet, the listener's sheet (the
    characters in frame), then the place's plate for the scene's time of
    day. Each ``{kind, label, path, name, url}``."""
    refs = []
    names = _names(ec)
    if keyframe_path(ec, shot) is not None:
        name = shot["assets"]["image"].rpartition("/")[2]
        refs.append({"kind": "keyframe", "label": f"Shot {shot['shot_id']} — keyframe (first frame)",
                     "path": f"episodes/ep{ec.ep:02d}/{schemas.SHOT_IMAGE_DIR}/{name}", "name": name,
                     "url": f"/api/stories/{ec.story_id}/episodes/{ec.ep}/shots/{name}"})
    _scene_doc, line = clips.speech_line(script, shot)
    order = _in_frame(shot)
    if line is not None and line.get("speaker") in order:
        order.remove(line["speaker"])
        order.insert(0, line["speaker"])
    characters = (ec.entities or {}).get("characters") or {}
    for char_id in order:
        doc = characters.get(char_id)
        if not doc:
            continue
        refs_doc = doc.get("refs") or {}
        for which in ("portrait", "turnaround"):
            entry = _entity_ref(ec, "characters", char_id, refs_doc.get(which),
                                f"{names.get(char_id, char_id)} — character sheet ({which})")
            if entry is not None:
                refs.append(dict(entry, kind="sheet"))
                break
    scene = _scene(script, shot)
    place = ((ec.entities or {}).get("places") or {}).get(scene.get("place_id")) or {}
    if place:
        variants = place.get("time_variants") or {}
        variant = scene.get("time_variant") if scene.get("time_variant") in variants else schemas.MASTER_PLATE_VARIANT
        entry = _entity_ref(ec, "places", place["place_id"], variants.get(variant),
                            f"{place.get('name') or place['place_id']} — place plate ({variant.replace('_', ' ')})")
        if entry is not None:
            refs.append(dict(entry, kind="plate"))
    return refs


# ------------------------------------------------------------------ the prompt

def _reference_line(model, refs) -> str:
    syntax = model.get("reference_syntax")
    if not syntax or not refs:
        return ""
    items = [syntax.format(n=n, label=ref["label"]) for n, ref in enumerate(refs, start=1)]
    return "References: " + "; ".join(items) + "."


def platform_prompt(preset, model, base, refs) -> str:
    """*base* (stage 4's clip prompt) for *preset*'s *model*: Google's own
    five-part syntax kept as it is, the preset's closing sentence ensured,
    and -- a model that names its references in the text -- one sentence
    naming each attached image by its number."""
    text = " ".join(str(base or "").split())
    closing = preset["closing"]
    if closing.lower().rstrip(".") not in text.lower():
        text = f"{text} {closing}".strip()
    line = _reference_line(model, refs)
    return f"{text} {line}".strip() if line else text


# ------------------------------------------------------------------ the state

def shot_state(ec, script, shot, assets_doc=None) -> str:
    """Where *shot* stands for the human (:data:`STATES`)."""
    clip = (shot.get("assets") or {}).get("clip") or {}
    if not clip or clip.get("state") != "current" or clips.shot_clip_path(ec, shot) is None:
        return "missing"
    flags = clips.shot_flags(shot, assets_doc)
    try:
        state = clips.clip_state(ec, shot, script, link=None, tier=3, flags=flags,
                                 image_sha=clip.get("image_sha256"))
    except (KeyError, ValueError):
        state = "current"
    if state != "current":
        return "missing"
    if not shot.get("speaks"):
        return "uploaded"
    take = clip.get("native_speech") or {}
    return _TAKE_STATES.get(take.get("state"), "uploaded")


def manual_shot(story, shot, assets_doc) -> bool:
    """Whether *shot*'s clip is on ``manual/upload`` (its class's link)."""
    return gen.is_manual(clips.class_link(story, shot, assets_doc, None) or "")


def missing_clips(ec, script, storyboard, assets_doc=None) -> list:
    """The shots of *storyboard* whose clip is the human's to upload and is
    not there (no clip, or one stale for its line): ``[{shot_id, order,
    speaks, clip_s, upload_slot}]`` in shot order. A shot kept still is not
    asked for. Empty on a story with no manual link."""
    if not media_policy.native_speech(getattr(ec, "story", None)):
        return []
    out = []
    for shot in sorted(storyboard["shots"], key=lambda item: item["order"]):
        if clips.shot_flags(shot, assets_doc).get("keep_still"):
            continue
        if not manual_shot(ec.story, shot, assets_doc):
            continue
        if shot_state(ec, script, shot, assets_doc) != "missing":
            continue
        out.append({"shot_id": shot["shot_id"], "order": shot["order"], "speaks": bool(shot.get("speaks")),
                    "clip_s": int(shot.get("clip_s") or round(float(shot.get("duration_s") or 0)) or 4),
                    "upload_slot": upload_slot(ec.story_id, ec.ep, shot["shot_id"])})
    return out


def waiting_sentence(count, *, what="clip") -> str:
    """"Waiting for 5 clips -- download the brief"."""
    return f"Waiting for {count} {what}{'' if count == 1 else 's'} — download the brief"


# ------------------------------------------------------------------ the brief

def _checks(ec, shot, line, clip_s, length, preset) -> list:
    look = "the look kept: the faces, outfits and colours of the reference sheets"
    size = f"9:16, at least {max(2, int(clip_s))} s (pick {length} s on {preset['name']})"
    if shot.get("speaks") and line is not None:
        return ["the lips move on the words, in sync, for the whole line",
                f"the line is spoken as written, in {_language(ec)}, by the one speaker -- nobody else talks",
                "no subtitles, captions or on-screen text burned in",
                look, "the clip has its sound (the voice)", size]
    return ["nobody speaks (sound of the place only)", "no subtitles, captions or on-screen text burned in", look, size]


def _language(ec) -> str:
    return {"fr": "French", "en": "English"}.get(getattr(ec, "language", ""), getattr(ec, "language", ""))


def shot_entry(ec, script, shot, *, preset, assets_doc=None) -> dict:
    """One shot of the brief (module docstring)."""
    speaks = bool(shot.get("speaks"))
    _scene_doc, line = clips.speech_line(script, shot)
    model_name, model = platforms.model_of(preset, speaks=speaks, language=ec.language)
    if speaks and line is not None:
        parts = clips.speech_request_parts(ec, shot, script, link=gen.MANUAL_LINK)
    else:
        parts = clips.clip_request_parts(ec, shot, script, tier=3, flags=clips.shot_flags(shot, assets_doc),
                                         link=gen.MANUAL_LINK)
    refs = references(ec, script, shot)[:model["max_references"]]
    for number, ref in enumerate(refs, start=1):
        ref["number"] = number
        ref["file"] = f"{shot['shot_id']}_{number}_{ref['kind']}{os.path.splitext(ref['name'])[1]}"
    clip_s = int(shot.get("clip_s") or round(float(shot.get("duration_s") or 0)) or 4)
    length = platforms.length_for(model, clip_s)
    mode = preset["modes"]["keyframe" if refs and refs[0]["kind"] == "keyframe" else "references"]
    entry = {
        "shot_id": shot["shot_id"], "order": shot["order"], "scene_id": shot["scene_id"], "speaks": speaks,
        "purpose": purpose(ec, script, shot),
        "platform": preset["platform"], "model": model_name, "model_label": model["label"], "mode": mode,
        "prompt": platform_prompt(preset, model, parts["prompt"], refs),
        "negative_prompt": parts.get("negative") or "",
        "length_s": length, "clip_s": clip_s,
        "length_note": preset["length_note"], "aspect": ASPECT,
        "references": refs,
        "line": None, "speaker": None, "voice_line": None,
        "checks": None,
        "upload_slot": upload_slot(ec.story_id, ec.ep, shot["shot_id"]),
        "state": shot_state(ec, script, shot, assets_doc),
    }
    if speaks and line is not None:
        character = ((ec.entities or {}).get("characters") or {}).get(line["speaker"]) or {}
        entry.update(line=line["text"], line_id=line["line_id"], speaker=character.get("name") or line["speaker"],
                     voice_line=prompting.voice_line(character.get("voice_hints")))
    entry["checks"] = _checks(ec, shot, line if speaks else None, clip_s, length, preset)
    take = ((shot.get("assets") or {}).get("clip") or {}).get("native_speech")
    if take:
        entry["take"] = {key: take.get(key) for key in ("state", "matched", "heard", "start_s", "end_s", "reason")}
    return entry


def shot_brief(ec, *, platform=None, script=None, storyboard=None, assets_doc=None) -> dict:
    """The episode's shot brief for *platform* (``platforms.PLATFORMS``;
    None: Flow), from its storyboard (module docstring)::

        {"$schema": "shot_brief_v1", "story_id", "ep", "language",
         "platform": {"platform", "name", "url", "where_to_paste", "prompt_notes", "length_note", "credits_note"},
         "shots": [<shot_entry>], "counts": {"total", "uploaded", "missing", "takes_ok", "flagged"},
         "credits": sentence, "waiting": sentence | None}

    ``StepFailed`` while the episode has no storyboard."""
    preset = platforms.load(platform)
    script = script if script is not None else episode_common.read_episode(ec, episode_common.SCRIPT_DOC)
    board = storyboard if storyboard is not None else episode_common.read_episode(ec, episode_common.STORYBOARD_DOC)
    if not script or not board or not board.get("shots"):
        raise episode_common.StepFailed(f"Episode {ec.ep} has no storyboard yet: plan its shots first, then the "
                                        "brief lists them.")
    if assets_doc is None:
        assets_doc = episode_common.read_episode(ec, episode_common.store_mod.EPISODE_ASSETS_DOC)
    shots = [shot_entry(ec, script, shot, preset=preset, assets_doc=assets_doc)
             for shot in sorted(board["shots"], key=lambda item: item["order"])
             if not clips.shot_flags(shot, assets_doc).get("keep_still")]
    missing = [entry for entry in shots if entry["state"] == "missing"]
    counts = {"total": len(shots), "uploaded": len(shots) - len(missing), "missing": len(missing),
              "takes_ok": sum(1 for entry in shots if entry["state"] == "take_ok"),
              "flagged": sum(1 for entry in shots if entry["state"] == "mismatch")}
    return {
        "$schema": SCHEMA, "story_id": ec.story_id, "ep": ec.ep, "language": ec.language,
        "title": (ec.story or {}).get("title") or "",
        "platform": {"platform": preset["platform"], "name": preset["name"], "url": preset["url"],
                     "where_to_paste": preset["where_to_paste"], "prompt_notes": list(preset["prompt_notes"]),
                     "length_note": preset["length_note"], "credits_note": preset["credits"]["note"],
                     "checked_at": preset["checked_at"]},
        "shots": shots, "counts": counts,
        "credits": platforms.credits_line(preset, len(missing) or len(shots)),
        "waiting": waiting_sentence(len(missing)) if missing else None,
    }


# --------------------------------------------------------------- the markdown

def _fence(text) -> str:
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}text\n{text}\n{fence}"


def render_markdown(brief) -> str:
    """The brief for a human (module docstring)."""
    platform = brief["platform"]
    counts = brief["counts"]
    head = [f"# Shot brief — episode {brief['ep']}" + (f" of {brief['title']}" if brief.get("title") else ""),
            "",
            f"Platform: **{platform['name']}** ({platform['url']}). {platform['where_to_paste']}",
            "",
            f"{counts['uploaded']} of {counts['total']} clips uploaded. {brief['credits']}.",
            "",
            "How to use it:"]
    head += [f"- {note}" for note in platform["prompt_notes"]]
    head += [f"- {platform['length_note']}",
             "- Upload each clip on its shot in the episode's Shot list, or: "
             "`aistory upload-clip <story> <ep> <shot_id> <file>`.", ""]
    body = []
    for number, entry in enumerate(brief["shots"], start=1):
        state = entry["state"].replace("_", " ")
        kind = "speaks" if entry["speaks"] else "silent"
        body += [f"## {number}. Shot {entry['shot_id']} ({kind}) — {state}", "",
                 f"**What it must show:** {entry['purpose']}", "",
                 f"**Model:** {entry['model_label']} · **Length:** pick {entry['length_s']} s "
                 f"(planned {entry['clip_s']} s) · **Aspect:** {entry['aspect']}", "",
                 f"**Mode:** {entry['mode']}", ""]
        if entry.get("line"):
            body += [f"**Line ({brief['language']}):** {entry['speaker']} — “{entry['line']}”", "",
                     f"**Voice:** {entry['voice_line']}", ""]
        body += ["**Prompt:**", "", _fence(entry["prompt"]), ""]
        if entry.get("negative_prompt"):
            body += ["**Negative prompt:**", "", _fence(entry["negative_prompt"]), ""]
        if entry["references"]:
            body += ["**Reference images (attach in this order):**", ""]
            body += [f"{ref['number']}. `{ref['file']}` — {ref['label']}" for ref in entry["references"]]
            body.append("")
        body += ["**Check before uploading:**", ""]
        body += [f"- [ ] {check}" for check in entry["checks"]]
        body += ["", f"Upload to: `{entry['upload_slot']}`", ""]
    return "\n".join(head + body).rstrip() + "\n"


# ------------------------------------------------------------- files and zip

def brief_path(ec, name, *, create=False) -> str:
    """Where the brief file *name* (:data:`BRIEF_NAMES`) of the episode
    lives: ``episodes/ep<NN>/assets/brief/<name>`` (``StoryStore.
    episode_brief_path``: no symlink at any level)."""
    if name not in BRIEF_NAMES:
        raise KeyError(name)
    return ec.store.episode_brief_path(ec.story_id, ec.ep, name, create=create)


def _write(path, text) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write_brief(ec, brief, *, image=False) -> dict:
    """*brief* as its ``.json`` and ``.md`` (``assets/brief/``); returns the
    two story-relative paths."""
    json_name, md_name = (IMAGE_JSON_NAME, IMAGE_MD_NAME) if image else (JSON_NAME, MD_NAME)
    render = render_image_markdown if image else render_markdown
    _write(brief_path(ec, json_name, create=True), json.dumps(brief, indent=2, ensure_ascii=False) + "\n")
    _write(brief_path(ec, md_name, create=True), render(brief))
    folder = f"episodes/ep{ec.ep:02d}/{BRIEF_DIR}"
    return {"json": f"{folder}/{json_name}", "md": f"{folder}/{md_name}"}


def reference_file(ec, ref):
    """The real path of a brief's reference image, or None."""
    parts = ref["path"].split("/")
    try:
        if parts[0] == "episodes":
            path = ec.store.episode_asset_path(ec.story_id, ec.ep, "shots", parts[-1])
            return path if os.path.isfile(path) else None
        return ec.store.media_path(ec.story_id, parts[0], parts[1], parts[-1])
    except (KeyError, IndexError):
        return None


def brief_zip(ec, brief, *, image=False) -> bytes:
    """A zip of the brief's ``.md`` and ``.json`` and every reference image
    it names, under the file names the ``.md`` gives them."""
    render = render_image_markdown if image else render_markdown
    stem = "image_brief" if image else "shot_brief"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{stem}.md", render(brief))
        archive.writestr(f"{stem}.json", json.dumps(brief, indent=2, ensure_ascii=False) + "\n")
        seen = set()
        for entry in brief.get("shots") or brief.get("images") or []:
            for ref in entry.get("references") or ():
                if ref["file"] in seen:
                    continue
                path = reference_file(ec, ref)
                if path is not None:
                    archive.write(path, f"references/{ref['file']}")
                    seen.add(ref["file"])
    return buffer.getvalue()


# ============================================================ the image brief

def render_image_markdown(brief) -> str:
    """The image brief for a human: one numbered entry per image to make."""
    counts = brief["counts"]
    lines = [f"# Image brief — {brief.get('title') or brief['story_id']}", "",
             f"{counts['uploaded']} of {counts['total']} images uploaded. Make each image in your own tool "
             "(Flow's image mode, Higgsfield, Freepik...) at the size shown, then upload it on its tile.", ""]
    for number, entry in enumerate(brief["images"], start=1):
        lines += [f"## {number}. {entry['label']} — {entry['state']}", "",
                  f"**Size:** {entry['size'][0]}x{entry['size'][1]} (at least {entry['min_size'][0]}x"
                  f"{entry['min_size'][1]})", "", "**Prompt:**", "", _fence(entry["prompt"]), ""]
        if entry.get("negative_prompt"):
            lines += ["**Negative prompt:**", "", _fence(entry["negative_prompt"]), ""]
        if entry["references"]:
            lines += ["**Reference images:**", ""]
            lines += [f"{ref['number']}. `{ref['file']}` — {ref['label']}" for ref in entry["references"]]
            lines.append("")
        lines += [f"Upload to: `{entry['upload_slot']}`", ""]
    return "\n".join(lines).rstrip() + "\n"

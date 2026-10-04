"""The human's own files land here: an uploaded clip (plan 22 stage 5, the
manual link) -- checked, stored as the step would store a made one, and
taken.

A story whose clips are on ``manual/upload`` waits for the human
(``steps/brief.py``: the shot brief; the assets step ends
``awaiting_uploads``). Each clip arrives through ``POST /api/stories/{id}/
episodes/{ep}/shots/{shot_id}/clip`` (or the CLI's ``upload-clip``), both of
which hand the received file to :func:`accept_clip`:

1. **refusals before anything is stored** (:class:`UploadRefused`, the
   reason in one sentence, HTTP 400 unless said): the episode's script and
   storyboard approved (409 otherwise), the shot on the storyboard (404),
   its clip the human's (a native-speech story, the shot's class link
   ``manual/upload``), then the file itself (:func:`probe_clip`, ffprobe):
   a video stream; at least :data:`MIN_CLIP_S`; 9:16 within
   :data:`ASPECT_TOLERANCE` (2 %) -- the render would crop any other shape
   to 9:16 (scale to cover, centre crop), which on a 16:9 or 1:1 clip cuts
   the characters out of frame, so the clip is refused rather than cropped;
   a sound track when the shot speaks (its sound is the line);
2. **stored** as ``assets/clips/shot_NN.manual.mp4`` by an atomic rename
   from the received file (same folder); a clip already there moves to
   ``assets/clips/takes/shot_NN.manual.<UTC>.mp4`` first, so nothing the
   human sent is ever lost;
3. **recorded** as the shot's ``assets.clip`` -- ``{link: manual/upload,
   route: manual, state: current, clip_s: <the plan's>, est_usd: 0,
   prompt_hash, image_sha256, sha256, uploaded_at, duration_s, filename}``
   (the prompt hash of the brief's prompt, so a line rewritten later makes
   it stale) -- with ``assets.video`` naming it, the storyboard written, the
   episode's links recorded as a made clip records them; the old take and
   lipsync records go with the old clip (a take is keyed by the clip's
   sha256);
4. **taken** (free): ``assets._Assets.native_take_shot`` on that shot alone
   -- a speaking clip transcribed, aligned, its line's audio cut from it; any
   clip's shot then lasts its real length (``native_speech.shot_seconds``).

Nothing here sends a request to a provider or books a cent (RC-N4); the
transcription of the take is the one call, on the STT chain, as for any
clip. Stdlib only (DEC-012); ffprobe through *run*.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone

from clipping.cancel import CancelToken
from clipping.providers import generation as gen

from . import media_policy, schemas
from . import store as store_mod

MAX_CLIP_BYTES = 500 * 1024 * 1024
MIN_CLIP_S = 2.0
ASPECT = 9 / 16
ASPECT_TOLERANCE = 0.02
FILENAME_MAX = 200

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


class UploadRefused(Exception):
    """An upload refused with the reason; *status* the HTTP code it answers."""

    def __init__(self, message, *, status=400):
        super().__init__(message)
        self.status = status


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stamp(now) -> str:
    try:
        when = datetime.fromisoformat(str(now))
    except ValueError:
        when = datetime.now(timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_filename(name) -> str:
    """The name the file was sent as, for the record only (never a path)."""
    base = os.path.basename(str(name or "").replace("\\", "/"))
    base = _SAFE_NAME.sub("_", base).strip(" .") or "clip.mp4"
    return base[:FILENAME_MAX]


# ------------------------------------------------------------------- the probe

def probe_clip(path, *, run=None) -> dict:
    """What ffprobe reads of the file at *path*::

        {"video": bool, "audio": bool, "width", "height", "duration_s", "codec"}

    :class:`UploadRefused` when ffprobe cannot read it at all."""
    runner = run or subprocess.run
    argv = ["ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type,codec_name,width,height:stream_tags=rotate:stream_side_data=rotation:"
            "format=duration,format_name", "-of", "json", os.fspath(path)]
    try:
        result = runner(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise UploadRefused(f"The clip cannot be checked: ffprobe could not run ({exc}).", status=500) from None
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        data = {}
    if result.returncode != 0 or not isinstance(data, dict) or not data.get("streams") and not data.get("format"):
        raise UploadRefused("The file is not a video ffprobe can read (send an MP4 from Flow or Higgsfield).")
    streams = [stream for stream in data.get("streams") or () if isinstance(stream, dict)]
    video = next((stream for stream in streams if stream.get("codec_type") == "video"
                  and stream.get("codec_name") not in ("mjpeg", "png")), None)
    audio = any(stream.get("codec_type") == "audio" for stream in streams)
    try:
        duration = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    width = int((video or {}).get("width") or 0)
    height = int((video or {}).get("height") or 0)
    rotation = 0
    for item in (video or {}).get("side_data_list") or ():
        try:
            rotation = int(float(item.get("rotation") or 0))
        except (TypeError, ValueError):
            pass
    try:
        rotation = rotation or int(float(((video or {}).get("tags") or {}).get("rotate") or 0))
    except (TypeError, ValueError):
        pass
    if abs(rotation) % 180 == 90:
        width, height = height, width
    container = str((data.get("format") or {}).get("format_name") or "")
    return {"video": video is not None, "audio": audio, "width": width, "height": height,
            "duration_s": round(duration, 3), "codec": (video or {}).get("codec_name"),
            "mp4": bool({"mp4", "mov"} & set(container.split(",")))}


def clip_refusal(info, *, speaks) -> str | None:
    """Why the probed clip *info* cannot be a shot's clip, or None."""
    if not info["video"]:
        return "The file has no video stream: send the clip itself (an MP4), not its sound or a still."
    if not info.get("mp4", True):
        return "The clip is not an MP4 (or MOV): download the take from the platform as MP4 and send that."
    if info["duration_s"] < MIN_CLIP_S:
        return (f"The clip lasts {info['duration_s']:g} s: a shot's clip runs at least {MIN_CLIP_S:g} s (Flow makes "
                "8 s clips).")
    width, height = info["width"], info["height"]
    if not width or not height:
        return "The clip's size cannot be read: send it again as an MP4."
    ratio = width / height
    if abs(ratio - ASPECT) / ASPECT > ASPECT_TOLERANCE:
        return (f"The clip is {width}x{height} ({_ratio_name(width, height)}), not 9:16: the render would crop it to "
                "9:16 and cut the characters out of frame. Pick 9:16 on the platform and make it again.")
    if speaks and not info["audio"]:
        return ("The clip has no sound track, and this shot speaks: its sound is the line. Download the take with "
                "its audio (Veo 3.1 always makes sound).")
    return None


def _ratio_name(width, height) -> str:
    for name, value in (("16:9", 16 / 9), ("9:16", 9 / 16), ("1:1", 1.0), ("4:3", 4 / 3), ("3:4", 3 / 4),
                        ("4:5", 4 / 5)):
        if abs(width / height - value) / value <= ASPECT_TOLERANCE:
            return name
    return f"{width / height:.3f}:1"


# --------------------------------------------------------------- the clip

def manual_clip_name(shot_id) -> str:
    """``shot_03.manual.mp4`` for shot ``sh03``."""
    return f"shot_{shot_id[2:]}{schemas.SHOT_MANUAL_SUFFIX}.mp4"


def manual_clip_rel(shot_id) -> str:
    return f"{schemas.SHOT_CLIP_DIR}/{manual_clip_name(shot_id)}"


def clips_folder(stories, story_id, ep) -> str:
    """The episode's real ``assets/clips/`` folder (made when missing), where
    an upload is received so its rename into place is atomic."""
    probe = stories.episode_asset_path(story_id, ep, "clips", "shot_01.mp4", create=True)
    return os.path.dirname(probe)


class _Context:
    """The step context an upload's take runs under (``steps.StepContext``'s
    fields the assets step reads), with its own cancel token."""

    def __init__(self, stories, story_id, ep, *, env, on_log):
        from .steps import StepContext

        self.ctx = StepContext(job_id="upload", story_id=story_id, step="upload-clip", ep=ep, params={},
                               cancel=CancelToken(), settings_env=dict(env or {}),
                               outputs_dir=stories.outputs_dir, on_log=on_log)


def _host(stories, story_id, ep, *, env, on_log, transcribe, run):
    """``(host, ec)``: an ``assets._Assets`` of the episode with its approved
    script and storyboard, for one shot's record and take."""
    from .steps import assets as assets_step
    from .steps import entities, episode_common
    from .steps.llm_call import StepFailed

    try:
        ec = episode_common.load_context(stories, story_id, ep)
    except StepFailed as exc:
        raise UploadRefused(str(exc), status=404) from None
    ctx = _Context(stories, story_id, ep, env=env, on_log=on_log).ctx
    host = assets_step._Assets(ctx, ec, tools=entities.Tools(), transcribe=transcribe)
    if run is not None:
        host.native_run = run
    try:
        host.script, host.storyboard = assets_step.require_approved(ec)
    except StepFailed as exc:
        raise UploadRefused(f"{exc} A shot's clip or keyframe is uploaded once its storyboard is approved.",
                            status=409) from None
    return host, ec


def _shot_of(host, shot_id):
    shot = next((item for item in host.storyboard["shots"] if item["shot_id"] == shot_id), None)
    if shot is None:
        raise UploadRefused(f"Episode {host.ec.ep}'s storyboard has no shot {shot_id!r}.", status=404)
    return shot


def upload_target_refusal(ec, shot, doc) -> str | None:
    """Why *shot*'s clip is not the human's to upload, or None."""
    from .steps import clips

    if not media_policy.native_speech(ec.story):
        return (f"Episode {ec.ep}'s clips are not yours to upload: the story is not on a native-speech profile "
                f"whose clips are on {gen.MANUAL_LINK} (choose Native speech — your own clips).")
    link = clips.class_link(ec.story, shot, doc, None)
    if not gen.is_manual(link or ""):
        return f"Shot {shot['shot_id']}'s clip is made on {link}, not uploaded: its link is not {gen.MANUAL_LINK}."
    return None


def accept_clip(stories, story_id, ep, shot_id, received, *, filename, env=None, on_log=None, now=None,
                transcribe=None, run=None, guard=None) -> dict:
    """The uploaded file *received* (a path inside the episode's clips folder,
    :func:`clips_folder`; consumed: moved into place, or left for the caller
    to delete on a refusal) as shot *shot_id*'s clip (module docstring).
    Returns ``{"shot_id", "clip": <the record>, "state", "take": <the take> |
    None, "duration_s", "replaced": bool, "missing": [...], "waiting":
    sentence | None}``. *transcribe* and *run* are the take's STT and
    ffmpeg/ffprobe seams (tests). *guard()*, when given, is called once the
    checks pass and right before anything is written: it raises
    :class:`UploadRefused` (409) when a step of the story started while the
    file was arriving, so nothing writes the storyboard beside it."""
    from .steps import assets as assets_step
    from .steps import brief as brief_mod
    from .steps import clips, sticky_link
    from .steps.llm_call import StepFailed

    on_log = on_log or (lambda _line: None)
    now = now or _utc_now()
    host, ec = _host(stories, story_id, ep, env=env, on_log=on_log, transcribe=transcribe, run=run)
    shot = _shot_of(host, shot_id)
    doc = assets_step._read_assets_doc(ec)
    refusal = upload_target_refusal(ec, shot, doc)
    if refusal:
        raise UploadRefused(refusal)
    speaks = bool(shot.get("speaks"))
    info = probe_clip(received, run=run)
    refusal = clip_refusal(info, speaks=speaks)
    if refusal:
        raise UploadRefused(refusal)

    tier = clips.tier_of(ec)
    flags = clips.shot_flags(shot, doc)
    try:
        parts = clips.clip_request_parts(ec, shot, host.script, tier=tier, flags=flags, link=gen.MANUAL_LINK)
    except (KeyError, ValueError) as exc:
        raise UploadRefused(f"Shot {shot_id}'s prompt cannot be built ({exc}): plan the storyboard again.") from None
    image = assets_step.shot_image_path(ec, shot)
    image_sha = (assets_step._sha256_file(image) if image else
                 assets_step._canonical_sha256({"manual": shot_id, "image": shot["assets"].get("image")}))

    if guard is not None:
        guard()
    name = manual_clip_name(shot_id)
    try:
        dest = stories.episode_asset_path(story_id, ep, "clips", name, create=True)
    except KeyError:
        raise UploadRefused(f"{manual_clip_rel(shot_id)} is not a real file (a symlink is never followed): move it "
                            "away and upload again.", status=409) from None
    replaced = os.path.isfile(dest)
    if replaced:
        _keep_old(stories, story_id, ep, shot_id, dest, now)
    os.replace(received, dest)
    os.chmod(dest, 0o644)
    sha = sha256_file(dest)
    record = {"state": "current", "link": gen.MANUAL_LINK, "route": "manual",
              "clip_s": int(shot.get("clip_s") or max(1, round(float(shot.get("duration_s") or 0) or 4))),
              "est_usd": 0.0, "prompt_hash": parts["hash"], "image_sha256": image_sha, "cache_key": None,
              "generated_at": now, "note": None, "sha256": sha, "uploaded_at": now,
              "duration_s": info["duration_s"], "filename": clean_filename(filename)}
    shot["assets"]["clip"] = record
    shot["assets"]["video"] = manual_clip_rel(shot_id)
    try:
        host.write_board()
    except StepFailed as exc:
        raise UploadRefused(str(exc), status=409) from None
    kind = sticky_link.VIDEO_SPEECH if speaks else sticky_link.VIDEO
    host.video_link_kept = sticky_link.recorded(doc, sticky_link.VIDEO) is not None
    host.speech_link_kept = sticky_link.recorded(doc, sticky_link.VIDEO_SPEECH) is not None
    host.keep_video_link(gen.MANUAL_LINK, kind=kind)
    on_log(f"📥 Shot {shot_id}: your clip ({info['duration_s']:g} s, {info['width']}x{info['height']}"
           f"{', with sound' if info['audio'] else ''}) is stored as {manual_clip_rel(shot_id)}"
           + (" (the one before is kept in assets/clips/takes/)" if replaced else ""))
    # The take, free: the clip's sound becomes its line; any shot lasts its clip's real length.
    host.native_take_shot(shot, sha=sha)
    if (host.video or {}).get("takes"):
        try:
            host.write_assets_doc()
        except StepFailed as exc:
            on_log(f"⚠️ {exc}")
    stored = next(item for item in host.storyboard["shots"] if item["shot_id"] == shot_id)
    clip = stored["assets"].get("clip") or {}
    take = clip.get("native_speech")
    missing = brief_mod.missing_clips(ec, host.script, host.storyboard, assets_step._read_assets_doc(ec))
    return {"shot_id": shot_id, "clip": clip, "state": brief_mod.shot_state(ec, host.script, stored),
            "take": take, "duration_s": stored.get("duration_s"), "replaced": replaced, "missing": missing,
            "waiting": brief_mod.waiting_sentence(len(missing)) if missing else None}


# ------------------------------------------------------------- the images

MAX_IMAGE_BYTES = 20 * 1024 * 1024
# A user's own image keeps up to the render's long side (1080x1920).
IMAGE_MAX_SIDE = 1920
ENTITY_KINDS = ("characters", "places", "props")


def _image_size(path) -> tuple:
    from PIL import Image

    with Image.open(path) as image:
        return image.size


def _clean_image(received, folder, *, role, exact_916=False) -> tuple:
    """*received* decoded, re-encoded as a clean PNG in *folder* (the
    uploads module's own decoder: PNG, JPEG, WebP or GIF, no metadata) and
    checked against the least size *role* takes; a keyframe is 9:16 within
    :data:`ASPECT_TOLERANCE`, cropped to the exact even 9:16 the render
    expects. ``(path, (width, height))``; :class:`UploadRefused` otherwise."""
    import tempfile

    from . import media_policy as policy
    from . import uploads as uploads_mod
    from .steps import brief as brief_mod

    handle, cleaned = tempfile.mkstemp(dir=folder, prefix=".upload-", suffix=".png")
    os.close(handle)
    try:
        try:
            uploads_mod._reencode(received, cleaned, max_side=IMAGE_MAX_SIDE)
        except uploads_mod.UploadError as exc:
            raise UploadRefused(str(exc), status=exc.http_status) from None
        width, height = _image_size(cleaned)
        least = brief_mod.IMAGE_MIN_SIZES[role]
        if width < least[0] or height < least[1]:
            raise UploadRefused(f"The image is {width}x{height}: a {role} is at least {least[0]}x{least[1]} "
                                f"(the app makes it at {'x'.join(map(str, brief_mod.IMAGE_SIZES[role]))}).")
        if exact_916:
            if abs(width / height - ASPECT) / ASPECT > ASPECT_TOLERANCE:
                raise UploadRefused(f"The keyframe is {width}x{height} ({_ratio_name(width, height)}), not 9:16: "
                                    "make it 9:16 (the render would crop the characters out of frame).")
            crop = policy.keyframe_crop((width, height))
            if crop is not None:
                from PIL import Image

                with Image.open(cleaned) as image:
                    left, top = (width - crop[0]) // 2, (height - crop[1]) // 2
                    image.crop((left, top, left + crop[0], top + crop[1])).save(cleaned, format="PNG")
                width, height = crop
        return cleaned, (width, height)
    except BaseException:
        try:
            os.unlink(cleaned)
        except OSError:
            pass
        raise


def _images_refusal(story) -> str | None:
    if media_policy.images_manual(story):
        return None
    return ("This story's images are made by the app: switch its images to your own uploads (the generation "
            "profile's Images: manual) to upload them.")


def accept_image(stories, story_id, kind, eid, slot, received, *, now=None, guard=None) -> dict:
    """The user's own image of an entity -- a character's ``portrait``,
    ``turnaround`` or ``expressions`` sheet, a place's plate (``slot`` its
    time variant: ``day`` is the master plate), a prop's ``image`` -- on a
    story whose images are manual (``media_policy.images_manual``): decoded
    and re-encoded clean (the uploads module's decoder), checked against
    the least size its role takes, stored where the app keeps a made one
    (``refs/<slot>.png``) and recorded as one, ``source: manual/upload``.
    Returns ``{"kind", "id", "slot", "ref", "size"}``."""
    from . import imaging, prompting, refimages

    now = now or _utc_now()
    if kind not in ENTITY_KINDS:
        raise UploadRefused(f"{kind!r} has no images to upload.", status=404)
    try:
        story = stories.get(story_id)
        doc = stories.read_entity(story_id, kind, eid)
    except KeyError:
        raise UploadRefused(f"This story has no {kind[:-1]} {eid!r}.", status=404) from None
    refusal = _images_refusal(story)
    if refusal:
        raise UploadRefused(refusal)
    if kind == "characters":
        if slot not in refimages.CHARACTER_IMAGES:
            raise UploadRefused(f"{slot!r} is not a character sheet ({', '.join(refimages.CHARACTER_IMAGES)}).")
        stem, role, base = slot, slot, slot == "portrait"
    elif kind == "places":
        if not isinstance(slot, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,19}", slot or ""):
            raise UploadRefused(f"{slot!r} is not a time variant name (day, night, golden_hour...).")
        stem, role, base = f"variant_{slot}", "plate", slot == refimages.MASTER_PLATE
    else:
        stem, role, base = "image", "prop", True
    try:
        folder = stories.refs_dir(story_id, kind, eid, create=True)
    except (KeyError, TypeError):
        raise UploadRefused("The entity's refs/ folder is not a real folder (a symlink is never followed).",
                            status=409) from None
    cleaned, size = _clean_image(received, folder, role=role)
    try:
        if guard is not None:
            guard()
        name = f"{stem}.png"
        try:
            stories.write_media(story_id, kind, eid, name, cleaned)
        except (KeyError, ValueError) as exc:
            raise UploadRefused(f"The image could not be stored ({exc}).", status=409) from None
    finally:
        try:
            os.unlink(cleaned)
        except OSError:
            pass
    ref = {"name": name, "consistency": "base" if base else "references", "source": gen.MANUAL_LINK,
           "seed": None, "created_at": now}
    lock = imaging.read_lock(stories, story_id, error=refimages.RefImageError)
    current = stories.read_entity(story_id, kind, eid)
    if kind == "characters":
        current["refs"][slot] = ref
        if slot == "portrait" and current["descriptor"] and current["signature_items"]:
            current["prompt_block"] = prompting.character_prompt_block(
                lock, descriptor=current["descriptor"], signature_items=current["signature_items"])
    elif kind == "places":
        current["time_variants"][slot] = ref
        if base and current["descriptor"] and current["layout_notes"]:
            current["prompt_block"] = prompting.place_prompt_block(
                lock, descriptor=current["descriptor"], layout_notes=current["layout_notes"])
    else:
        current["image"] = ref
        if current["descriptor"]:
            current["prompt_block"] = prompting.prop_prompt_block(lock, descriptor=current["descriptor"])
    stories.write_entity(story_id, kind, current, now=now)
    refimages._remove_other_extensions(stories, story_id, kind, eid, stem, name)
    return {"kind": kind, "id": eid, "slot": slot, "ref": ref, "size": list(size), "name": doc.get("name")}


def accept_keyframe(stories, story_id, ep, shot_id, received, *, env=None, on_log=None, now=None,
                    guard=None) -> dict:
    """The user's own keyframe of a shot, on a story whose images are manual:
    decoded clean, 9:16 within 2 % (cropped to the exact even 9:16), at
    least 360x640, stored as ``assets/shots/shot_NN.png`` and recorded as a
    made keyframe is -- ``provider: manual``, ``model: upload``, ``route: free``, $0, the
    prompt hash it would be asked with now -- so it reads current; the
    episode's image link recorded as ``manual/upload``. Returns ``{"shot_id",
    "image", "size", "state", "missing"}``."""
    from .steps import assets as assets_step
    from .steps import brief as brief_mod
    from .steps import sticky_link

    on_log = on_log or (lambda _line: None)
    now = now or _utc_now()
    host, ec = _host(stories, story_id, ep, env=env, on_log=on_log, transcribe=None, run=None)
    refusal = _images_refusal(ec.story)
    if refusal:
        raise UploadRefused(refusal)
    shot = _shot_of(host, shot_id)
    if shot["assets"].get("locked"):
        raise UploadRefused(f"Shot {shot_id} is locked: unlock it first.", status=409)
    name = f"shot_{shot_id[2:]}.png"
    try:
        dest = stories.episode_asset_path(story_id, ep, "shots", name, create=True)
    except KeyError:
        raise UploadRefused(f"assets/shots/{name} is not a real file (a symlink is never followed).",
                            status=409) from None
    cleaned, size = _clean_image(received, os.path.dirname(dest), role="keyframe", exact_916=True)
    if guard is not None:
        try:
            guard()
        except UploadRefused:
            os.unlink(cleaned)
            raise
    os.replace(cleaned, dest)
    os.chmod(dest, 0o644)
    host.drop_other_images(shot_id, "png")
    doc = assets_step._read_assets_doc(ec)
    link = assets_step.recorded_image_link(doc)
    parts = assets_step.request_parts(ec, shot, note=None, link=link)
    # An image paid for by nobody here: ``route: free`` (the image routes are free, local or paid).
    shot["assets"].update({"image": f"{schemas.SHOT_IMAGE_DIR}/{name}", "seed": None, "provider": gen.MANUAL,
                           "model": "upload", "consistency": parts["consistency"], "route": "free",
                           "prompt_hash": parts["hash"], "est_usd": 0.0, "cache_key": None, "generated_at": now,
                           "note": None, "pending": None})
    host.write_board()
    if sticky_link.recorded(doc, sticky_link.IMAGE) is None:
        host.link_pending = sticky_link.record(gen.MANUAL_LINK, now=now)
        try:
            host.write_assets_doc()
        except Exception as exc:  # noqa: BLE001 - the link is recorded by the next assets run
            on_log(f"⚠️ The episode's image link could not be recorded now ({exc}).")
    on_log(f"📥 Shot {shot_id}: your keyframe ({size[0]}x{size[1]}) is stored as assets/shots/{name}")
    missing = brief_mod.missing_keyframes(ec, host.storyboard, assets_step._read_assets_doc(ec))
    return {"shot_id": shot_id, "image": f"{schemas.SHOT_IMAGE_DIR}/{name}", "size": list(size),
            "state": assets_step.shot_state(ec, shot), "missing": missing,
            "waiting": brief_mod.waiting_sentence(0, keyframes=len(missing)) if missing else None}


def _keep_old(stories, story_id, ep, shot_id, dest, now) -> str:
    """The clip at *dest* moved to ``assets/clips/takes/`` under a stamped
    name; returns that name."""
    stamp = _stamp(now)
    base = f"shot_{shot_id[2:]}{schemas.SHOT_MANUAL_SUFFIX}.{stamp}"
    for attempt in range(100):
        name = f"{base}.mp4" if not attempt else f"{base}-{attempt}.mp4"
        try:
            path = stories.episode_take_path(story_id, ep, name, create=True)
        except KeyError:
            raise UploadRefused("assets/clips/takes/ is not a real folder (a symlink is never followed): move it away "
                                "and upload again.", status=409) from None
        if not os.path.lexists(path):
            os.replace(dest, path)
            return name
    raise UploadRefused("Too many earlier takes of this shot share the same second: upload again.", status=409)


__all__ = ("UploadRefused", "accept_clip", "probe_clip", "clip_refusal", "clips_folder", "manual_clip_name",
           "MAX_CLIP_BYTES", "store_mod")

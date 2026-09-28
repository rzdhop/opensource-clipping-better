"""Step ``metadata``: the publishing pack of a rendered episode (spec 2.10, 3
step 12, 4.2 row M1; AI Story phase 4, stage 9; DEC-115, DEC-159, DEC-161,
DEC-166).

``ctx.ep`` is the episode. It needs a **finished render**: an approved
script, a ``render_manifest.json`` with its ``output`` recorded, and an
``episode_final.mp4`` whose sha256 is that output's (:func:`require_render`);
otherwise ``StepFailed`` saying what to do, before anything is sent.

**One M1 call per platform** (``schemas.PLATFORMS``: tiktok, shorts,
reels), through ``llm_call.call_json`` -- so a paid link of the chain is
never called while ``allow_paid`` is off (DEC-115) -- under the episode
steps' predictive budget. The model writes the title, the description, the
hashtags and the cover's hook text (and, for a French story, ``title_en``
and ``hashtags_en``); **Python builds the rest** (:func:`platform_entry`):
every tag carries its ``#`` (``prompts.normalize_hashtags``); the
description ends with the script's ``next_episode_teaser``; the pinned
comment is the teaser and ``PART {n+1} →`` / ``PARTIE {n+1} →``; French
prose gets its dropped elisions repaired (F1, as the script step does).

``metadata_pack.json`` (``metadata_pack_v1``) records the ``script_rev`` and
the ``render_sha256`` it was written from and is written after every
accepted call. A run fills only what is missing (DEC-124): a pack written
for the same render and script keeps its platforms (a complete re-run makes
no call); a pack of another render or script revision is written again,
every platform.

**The cover** (:func:`make_cover`, DEC-159: text by libass from ASS built
in Python, no PIL): the hook scene's first shot image, filled to 1080x1920,
with ``hook.on_screen_text`` -- else the first platform's M1 ``hook_text``
-- burned by ``subtitles.cover_ass`` in the style's typography and the font
the render resolves; one frame by ffmpeg (``filtergraph.cover_argv``) to the
episode's ``cover.jpg``. It is made again whenever the pack is written anew
or the file is missing.

:func:`regenerate_platform` is the ``metadata:<ep>:<platform>`` target of
``regenerate`` (kind :data:`METADATA_KIND`, ``episode_regenerate``): that
platform alone again, with the note; every other platform and the cover are
left as they are. The step and the regenerate both end ``completed``
(DEC-161; the worker change is stage 10's). ``story.json`` is never written
(RC-E2).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time

from .. import context, prompts, schemas
from .. import store as store_mod
from ..render import filtergraph, fonts
from ..render import plan as plan_mod
from ..render import runner as runner_mod
from ..render import subtitles as subtitles_mod
from . import assets as assets_step
from . import entities, episode_common, llm_call
from . import script as script_step
from .episode_common import SCRIPT_DOC, STORYBOARD_DOC
from .llm_call import StepFailed

STEP = "metadata"
PROMPT_ID = "M1"
PACK_DOC = store_mod.EPISODE_METADATA_PACK_DOC
MANIFEST_DOC = store_mod.EPISODE_RENDER_MANIFEST_DOC
FINAL_FILE = plan_mod.FINAL_REL
COVER_FILE = "cover.jpg"
PLATFORMS = schemas.PLATFORMS

# The regenerate kind of ``metadata:<ep>:<platform>`` (``episode_regenerate``).
METADATA_KIND = "metadata"

# The call to the next episode, after the teaser in the pinned comment.
PART_CALL = {"en": "PART {n} →", "fr": "PARTIE {n} →"}

# The cover's work files, inside render/ (the ffmpeg working folder).
COVER_ASS_REL = "cover.ass"
COVER_PART_REL = "cover.part.jpg"
# One frame of one image: seconds, whatever the machine.
COVER_TIMEOUT_S = 120
COVER_STDERR_CHARS = 600


def _and(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def target_for(ep, platform) -> str:
    return f"{METADATA_KIND}:{ep}:{platform}"


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ------------------------------------------------------------ preconditions

def require_render(ec) -> tuple:
    """``(script, manifest, render_sha256)`` of a finished render of episode
    *ec.ep*; else ``StepFailed`` saying what to do."""
    ep = ec.ep
    script = episode_common.read_episode(ec, SCRIPT_DOC)
    if script is None or not script["scenes"]:
        raise StepFailed(f"Episode {ep} has no script yet: write it, make its assets and render it first.")
    if not script["approved_at"]:
        raise StepFailed(f"Episode {ep}'s script is not approved: approve it, render the episode, then write its "
                         "metadata.")
    manifest = episode_common.read_episode(ec, MANIFEST_DOC)
    if manifest is None:
        raise StepFailed(f"Episode {ep} is not rendered yet: render it first (the render step).")
    output = manifest.get("output")
    if not output:
        raise StepFailed(f"Episode {ep}'s last render did not finish ({MANIFEST_DOC} has no output): render it "
                         "again.")
    try:
        final = ec.store.episode_file_path(ec.story_id, ep, FINAL_FILE)
    except KeyError:
        final = None
    if final is None or not os.path.isfile(final):
        raise StepFailed(f"Episode {ep}'s {FINAL_FILE} is missing: render it again.")
    sha = _sha256_file(final)
    if sha != output["sha256"]:
        raise StepFailed(f"Episode {ep}'s {FINAL_FILE} is not the file its last render made (sha256 {sha[:12]}, "
                         f"recorded {output['sha256'][:12]}): render it again.")
    return script, manifest, sha


def is_current(pack, script, render_sha) -> bool:
    """Whether *pack* was written for this render and this script revision."""
    return pack is not None and pack["render_sha256"] == render_sha and pack["script_rev"] == script["rev"]


# ------------------------------------------------------------------- M1

def episode_cast_names(ec, script) -> list:
    """The names of the characters in the episode, in cast order."""
    present = {cid for scene in script["scenes"] for cid in scene["characters"]}
    return [doc["name"] for doc in ec.cast if doc["char_id"] in present]


def ask_platform(ctx, ec, script, platform, *, tools, announced, note=None) -> dict:
    """One accepted M1 reply for *platform* (``llm_call.call_json``: free
    links only while ``allow_paid`` is off, retried once on a rejected
    reply)."""
    pack = context.build_pack(language=ec.language, story=ec.story, note=note)
    llm_call.announce_trimmed(ctx, pack, announced)
    system, user, schema = prompts.build_m1(
        pack, platform=platform, ep=ec.ep, story_title=ec.story["title"], episode_title=script.get("title"),
        hook_text=(script.get("hook") or {}).get("on_screen_text"), teaser=script.get("next_episode_teaser"),
        cast_names=episode_cast_names(ec, script), note=pack.note)
    english = prompts.m1_english_fields(pack)
    return llm_call.call_json(ctx, PROMPT_ID, system, user, schema,
                              validator=lambda reply: prompts.validate_m1(reply, platform=platform, english=english),
                              runner=tools.runner, time_fn=tools.time_fn)


def _prose(text, language) -> str:
    text = " ".join(str(text).split())
    return prompts.repair_fr_elisions(text) if language == "fr" else text


def pinned_comment(teaser, ep, language) -> str:
    """The teaser, then ``PART {ep+1} →`` (``PARTIE`` in French)."""
    call = PART_CALL[language].format(n=ep + 1)
    teaser = " ".join(str(teaser or "").split())
    return f"{teaser} {call}" if teaser else call


def platform_entry(reply, *, ep, language, teaser, now) -> dict:
    """A platform's ``metadata_pack_v1`` entry from an accepted M1 *reply*:
    what the model wrote, and what Python adds (module docstring)."""
    description = _prose(reply["description"], language)
    teaser = " ".join(str(teaser or "").split())
    if teaser:
        description = f"{description}\n\n{teaser}"
    entry = {
        "title": _prose(reply["title"], language),
        "description": description,
        "hashtags": prompts.normalize_hashtags(reply["hashtags"]),
        "hook_text": _prose(reply["hook_text"], language),
        "pinned_comment": pinned_comment(teaser, ep, language),
        "cover": COVER_FILE,
        "written_at": now,
    }
    if language == "fr":
        entry["title_en"] = " ".join(str(reply["title_en"]).split())
        entry["hashtags_en"] = prompts.normalize_hashtags(reply["hashtags_en"])
    return entry


def new_pack(ec, script, render_sha, *, now) -> dict:
    return {"$schema": schemas.METADATA_PACK_SCHEMA_NAME, "ep": ec.ep, "language": ec.language,
            "script_rev": script["rev"], "render_sha256": render_sha, "platforms": {},
            "created_at": now, "updated_at": now}


def save_pack(ec, doc) -> dict:
    try:
        return ec.store.write_episode_doc(ec.story_id, ec.ep, PACK_DOC, doc, now=llm_call.utc_now())
    except (schemas.SchemaError, ValueError, KeyError) as exc:
        raise StepFailed(f"Episode {ec.ep}'s {PACK_DOC} could not be written ({exc}).") from None


def write_platform(ctx, ec, script, doc, platform, *, tools, announced, note=None) -> dict:
    """Ask M1 for *platform*, put its entry into *doc* and write the pack;
    returns the document as written."""
    reply = ask_platform(ctx, ec, script, platform, tools=tools, announced=announced, note=note)
    entry = platform_entry(reply, ep=ec.ep, language=ec.language, teaser=script.get("next_episode_teaser"),
                           now=llm_call.utc_now())
    doc["platforms"][platform] = entry
    saved = save_pack(ec, doc)
    ctx.on_log(f"🏷 {prompts.M1_PLATFORM_RULES[platform]['name']}: {entry['title']} "
               f"{' '.join(entry['hashtags'])}")
    return saved


# ------------------------------------------------------------------ cover

def cover_text(script, pack) -> str:
    """The hook's own on-screen text, else the first platform's M1
    ``hook_text``."""
    own = ((script.get("hook") or {}).get("on_screen_text") or "").strip()
    if own:
        return own
    for platform in PLATFORMS:
        entry = (pack or {}).get("platforms", {}).get(platform)
        if entry:
            return entry["hook_text"]
    return ""


def hook_shot(ec, script):
    """The hook scene's first shot (the board's order) and its image path;
    ``StepFailed`` when there is none."""
    ep = ec.ep
    scene = script_step.framing_scene(script, "hook")
    board = episode_common.read_episode(ec, STORYBOARD_DOC)
    if scene is None or board is None:
        raise StepFailed(f"Episode {ep} has no hook scene with a shot: the cover is made from its first shot.")
    shot = next((s for s in board["shots"] if s["scene_id"] == scene["scene_id"]), None)
    path = assets_step.shot_image_path(ec, shot) if shot is not None else None
    if path is None:
        raise StepFailed(f"Episode {ep}'s hook scene ({scene['scene_id']}) has no shot image for the cover: make "
                         "it (the assets step), render, then write the metadata again.")
    return shot, path


def _write_text(path, text) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.replace(tmp, path)


def _stage_image(path, render_dir) -> str:
    """*path* copied into ``render/in/`` under its content hash (the runner's
    convention); its relative name."""
    sha = _sha256_file(path)
    rel = plan_mod.staged_name(sha, path)
    dest = os.path.join(render_dir, rel)
    if not (os.path.isfile(dest) and not os.path.islink(dest) and _sha256_file(dest) == sha):
        tmp = dest + ".tmp"
        shutil.copyfile(path, tmp)
        os.replace(tmp, dest)
    return rel


def make_cover(ctx, ec, script, text, *, run_process=subprocess.run, custom_fonts_dir=None) -> dict:
    """The episode's ``cover.jpg`` (module docstring); ``{file, text, shot,
    sha256}``. Every path from the store; the command runs in ``render/``
    with relative paths only. ``StepFailed`` naming ffmpeg's stderr tail
    when it fails."""
    ep, store = ec.ep, ec.store
    shot, image = hook_shot(ec, script)
    try:
        render_dir = store.episode_render_dir(ec.story_id, ep, create=True)
        store.episode_render_dir(ec.story_id, ep, plan_mod.IN_DIR, create=True)
        store.episode_render_dir(ec.story_id, ep, plan_mod.FONTS_DIR, create=True)
        dest = store.episode_file_path(ec.story_id, ep, COVER_FILE, create=True)
    except KeyError as exc:
        raise StepFailed(f"Episode {ep}'s cover cannot be written: {exc.args[0] if exc.args else exc} is not a real "
                         "file or folder; it is never followed: move it away first.") from None

    typography = dict(ec.style_lock["typography"])
    font = fonts.resolve_font(typography["font_family"], custom_fonts_dir=custom_fonts_dir)
    typography["font_family"] = font["family"]
    image_rel = _stage_image(image, render_dir)
    try:
        # The runner's own staging: the font alone in fonts/ (another font
        # left there could answer the same family name), its bytes checked.
        runner_mod._stage_font({"font": font}, render_dir)
    except (OSError, runner_mod.RunnerError) as exc:
        raise StepFailed(f"Episode {ep}'s cover could not be made: the font could not be staged ({exc}).") from None
    _write_text(os.path.join(render_dir, COVER_ASS_REL), subtitles_mod.cover_ass(text, typography))
    part = os.path.join(render_dir, COVER_PART_REL)
    if os.path.isfile(part) and not os.path.islink(part):
        os.remove(part)

    argv = filtergraph.cover_argv(image_rel, COVER_ASS_REL, plan_mod.FONTS_DIR, COVER_PART_REL)
    ctx.cancel.check()
    try:
        done = run_process(argv, cwd=render_dir, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=COVER_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        raise StepFailed(f"Episode {ep}'s cover could not be made: ffmpeg did not run ({exc}).") from None
    if done.returncode != 0 or not os.path.isfile(part) or os.path.getsize(part) == 0:
        tail = " ".join((done.stderr or "").split())[-COVER_STDERR_CHARS:]
        raise StepFailed(f"Episode {ep}'s cover could not be made: ffmpeg exit {done.returncode}"
                         + (f"; stderr: {tail}" if tail else "") + ". The platforms written are kept: run the step "
                         "again to make the cover (no call is repeated).")
    os.replace(part, dest)
    ctx.on_log(f"🖼 Cover: shot {shot['shot_id']} with “{text}”" if text else f"🖼 Cover: shot {shot['shot_id']}")
    return {"file": COVER_FILE, "text": text, "shot": shot["shot_id"], "sha256": _sha256_file(dest)}


def _cover_missing(ec) -> bool:
    try:
        path = ec.store.episode_file_path(ec.story_id, ec.ep, COVER_FILE)
    except KeyError:
        return True
    return not os.path.isfile(path)


# --------------------------------------------------------------------- run

def run(ctx, *, runner=None, time_fn=time.monotonic, run_process=subprocess.run, custom_fonts_dir=None) -> dict:
    """The step (module docstring). Returns ``{ep, platforms{platform:
    {title, hashtags}}, asked[platforms asked now], kept[platforms kept],
    cover{file, text, shot, sha256} | None, script_rev, render_sha256}``.
    *runner* (the LLM chain), *time_fn*, *run_process* (ffmpeg) and
    *custom_fonts_dir* are for tests."""
    ec = episode_common.load_episode_context(ctx)
    episode_common.check_episode_preconditions(ctx, ec)
    ctx.cancel.check()
    script, _manifest, render_sha = require_render(ec)
    tools = entities.Tools(runner=runner, time_fn=time_fn)
    budget = episode_common.Budget(time_fn)
    announced = set()
    ep = ec.ep

    existing = episode_common.read_episode(ec, PACK_DOC)
    fresh = not is_current(existing, script, render_sha)
    if fresh and existing is not None:
        ctx.on_log("♻️ The metadata pack was written for another render or script: every platform is written "
                   "again.")
    doc = new_pack(ec, script, render_sha, now=llm_call.utc_now()) if fresh else existing
    todo = [platform for platform in PLATFORMS if platform not in doc["platforms"]]
    kept = [platform for platform in PLATFORMS if platform in doc["platforms"]]
    if not todo:
        ctx.on_log(f"🏷 Every platform of episode {ep} is written for this render: nothing to ask.")
    for index, platform in enumerate(todo):
        ctx.cancel.check()
        rest = todo[index:]
        budget.before_call(lambda rest=rest: f"the metadata of {_and(rest)} (M1)")
        ctx.on_log(f"🏷 Episode {ep} on {prompts.M1_PLATFORM_RULES[platform]['name']} (M1)")
        try:
            doc = write_platform(ctx, ec, script, doc, platform, tools=tools, announced=announced)
        except StepFailed as exc:
            written = [p for p in PLATFORMS if p in doc["platforms"]]
            also = f" Written and kept: {_and(written)}." if written else ""
            raise StepFailed(f"Episode {ep}'s metadata for {platform} failed: {exc.reason}.{also} Run the step "
                             "again to finish (what is written is not asked again).", reason=exc.reason) from None

    cover = None
    if fresh or _cover_missing(ec):
        cover = make_cover(ctx, ec, script, cover_text(script, doc), run_process=run_process,
                           custom_fonts_dir=custom_fonts_dir)
    ctx.on_log(f"✅ Episode {ep}'s metadata pack is written ({', '.join(PLATFORMS)}).")
    return {
        "ep": ep,
        "platforms": {p: {"title": doc["platforms"][p]["title"], "hashtags": list(doc["platforms"][p]["hashtags"])}
                      for p in PLATFORMS if p in doc["platforms"]},
        "asked": todo, "kept": kept, "cover": cover,
        "script_rev": doc["script_rev"], "render_sha256": doc["render_sha256"],
    }


# ---------------------------------------------------------------- regenerate

def regenerate_platform(ctx, ec, target, platform, note, *, tools, refuse, run_process=subprocess.run,
                        custom_fonts_dir=None) -> dict:
    """``metadata:<ep>:<platform>`` (kind :data:`METADATA_KIND`): M1 again
    for *platform* alone, with *note*; only that platform's entry is
    replaced. A pack written for another render or script is refused (the
    step writes every platform again); with no pack yet, the pack starts
    with this platform. The cover is made only when it is missing.
    *refuse(reason)* is the caller's ``StepFailed`` builder."""
    if platform not in PLATFORMS:
        raise refuse(f"{platform!r} is not a platform (one of {', '.join(PLATFORMS)}).")
    if note is not None and len(note) > schemas.REGENERATE_NOTE_MAX:
        raise refuse(f"a note is at most {schemas.REGENERATE_NOTE_MAX} characters ({len(note)} given).")
    try:
        script, _manifest, render_sha = require_render(ec)
    except StepFailed as exc:
        raise refuse(str(exc)) from None
    existing = episode_common.read_episode(ec, PACK_DOC)
    if existing is not None and not is_current(existing, script, render_sha):
        raise refuse(f"episode {ec.ep}'s metadata pack was written for another render or script: run the metadata "
                     "step, which writes every platform again.")
    doc = existing or new_pack(ec, script, render_sha, now=llm_call.utc_now())
    ctx.on_log(f"🏷 {prompts.M1_PLATFORM_RULES[platform]['name']} again (M1)" + (f" (note: {note})" if note else ""))
    ctx.cancel.check()
    try:
        doc = write_platform(ctx, ec, script, doc, platform, tools=tools, announced=set(), note=note)
    except StepFailed as exc:
        raise refuse(str(exc)) from None
    cover = None
    if _cover_missing(ec):
        cover = make_cover(ctx, ec, script, cover_text(script, doc), run_process=run_process,
                           custom_fonts_dir=custom_fonts_dir)
    ctx.on_log(f"🔁 Regenerated {target}")
    entry = doc["platforms"][platform]
    return {"target": target, "platform": platform, "title": entry["title"], "hashtags": list(entry["hashtags"]),
            "cover": cover}

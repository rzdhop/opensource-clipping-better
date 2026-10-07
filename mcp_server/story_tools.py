"""Stage 2: the story store and the steps as tools.

Reading: the story, its documents, its entities, an episode. Writing: a
step run with the chat as writer (``director``), the approvals, the
patches -- every one through ``clipping.aistory.workflow``, the same rules
the web API applies (minus its job-queue checks: the director is the only
thing running steps here, one per story). Nothing in this module invents a
document format: what a step writes is what the app's schemas say.

Plan 32 stage 1: a story can be created from a preset (``presets``), the
style is a step like the others, ``story_make_episode`` runs one episode's
fast track in one run, and ``story_estimate`` says what a step would spend
before it starts (the workflow's own estimates, calling nothing).

Plan 33 stage 4 (the "no still" rule): every shot of an episode is a video
clip, never a still with camera motion. A story made here is fully animated:
without a preset or a generation profile it gets :func:`animated_profile`
(own_gpu, tier 3); a tier under 2, or a budget profile that does not animate
every shot, is refused at ``story_create`` and ``story_patch``
(:func:`still_refusal`); ``story_options`` lists only the budget profiles
that animate every shot.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from fastmcp.exceptions import ToolError

from clipping.aistory import defaults, presets, store as story_store, templates, workflow
from clipping.providers import budget as budget_mod
from clipping.aistory.steps.entities import CHARACTERS, PLACES, PROPS

from .director import CHAT_SETTINGS, STEP_MODULES, Director, DirectorError

ENTITY_KINDS = {"characters": CHARACTERS, "places": PLACES, "props": PROPS}
EPISODE_DOCS = ("script.json", "storyboard.json", "assets.json", "render_manifest.json", "metadata_pack.json",
                "memory.json", "feedback.json", "proposals.json", "consistency_report.json")
APPROVE_ALL_GROUPS = {"cast": (CHARACTERS,), "places": (PLACES, PROPS)}
# What story_estimate prices (plan 32 stage 1).
ESTIMATES = ("cast", "episode", "render", "story")
# The step story_make_episode runs (one episode, script to metadata pack, in one run).
MAKE_EPISODE_STEP = "fast-track"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stored_settings() -> dict:
    """The dashboard's saved Settings (keys, chains, budget), as the CLI reads
    them: ``data/settings.json`` or ``WEB_SETTINGS_FILE``."""
    from clipping.aistory import cli

    env = cli.stored_settings()
    try:
        from clipping.providers import budget as budget_mod

        budget_mod.set_settings_reader(lambda: dict(env))
    except Exception:  # noqa: BLE001 - the budget reader is a convenience
        pass
    return env


class StoryBackend:
    def __init__(self, outputs_dir: str, *, director: Optional[Director] = None, settings_env: Optional[dict] = None):
        self.outputs_dir = outputs_dir
        self.stories = story_store.StoryStore(outputs_dir, on_log=lambda *_: None)
        env = stored_settings() if settings_env is None else dict(settings_env)
        # Plan 33 stage 3: the voice tools read the same keys (GOOGLE_API_KEY
        # lives in the app's stored settings, not in .env, on the live host).
        self.settings_env = env
        self.director = director or Director(outputs_dir, settings_env=env)

    def load(self, story_id: str) -> dict:
        return workflow.load(self.stories, story_id)


def _answering(fn, *args, **kwargs):
    """Run a workflow call; its refusals become tool errors with the reason."""
    try:
        return fn(*args, **kwargs)
    except workflow.WorkflowError as exc:
        raise ToolError(f"{exc.code}: {exc}") from exc
    except workflow.StoryUnreadable as exc:
        raise ToolError(str(exc)) from exc
    except (KeyError, ValueError) as exc:
        raise ToolError(f"{type(exc).__name__}: {exc}") from exc
    except DirectorError as exc:
        raise ToolError(str(exc)) from exc


def _entity_summary(doc: dict) -> dict:
    keys = ("id", "char_id", "place_id", "prop_id", "name", "role", "approved_at", "locked", "descriptor",
            "signature_items", "personality", "voice", "images", "sheets", "variants")
    return {k: doc.get(k) for k in keys if k in doc}


def _story_overview(backend: StoryBackend, story_id: str) -> dict:
    stories = backend.stories
    story = backend.load(story_id)
    out = {"story": story, "recipe": story.get("recipe")}
    for name in ("style_lock.json", "season.json", "knowledge.json", "places_proposal.json"):
        try:
            doc = stories.read_doc(story_id, name)
        except Exception:  # noqa: BLE001 - a missing or unreadable doc is shown as absent
            doc = None
        out[name.replace(".json", "")] = doc
    for word, kind in ENTITY_KINDS.items():
        try:
            out[word] = [_entity_summary(e) for e in stories.list_entities(story_id, kind)]
        except Exception:  # noqa: BLE001
            out[word] = []
    try:
        out["progress"] = workflow.progress(stories, story, env=backend.director.settings_env)
    except Exception as exc:  # noqa: BLE001
        out["progress"] = {"error": str(exc)}
    try:
        out["list_progress"] = workflow.list_progress(stories, story)
    except Exception as exc:  # noqa: BLE001
        out["list_progress"] = {"error": str(exc)}
    try:
        out["episodes"] = stories.list_episodes(story_id)
    except Exception:  # noqa: BLE001
        out["episodes"] = []
    out["runs"] = backend.director.list(story_id)[:5]
    return out


# The budget profiles' animate mode that makes every shot a clip.
ALL_SHOTS = "all_shots"


def animated_profile() -> dict:
    """The ``generation_profile`` a story made here gets without a preset or a
    profile of its own (plan 33 stage 4): fully animated on the human's own
    GPU -- the quality profile (v2, tier 3, references) on ``own_gpu``, the
    fruit_drama preset's own choice without its cast or its agent mode."""
    return dict(defaults.quality_generation_profile(), budget_profile=defaults.OWN_GPU_PROFILE)


def _animated_profile_ids() -> list:
    """The ids of the budget profiles that animate every shot (``animate:
    all_shots``), in ``defaults.BUDGET_PROFILES`` order."""
    try:
        profiles = budget_mod.load_profiles()["profiles"]
    except (OSError, ValueError):
        return [defaults.OWN_GPU_PROFILE]
    return [pid for pid in defaults.BUDGET_PROFILES if (profiles.get(pid) or {}).get("animate") == ALL_SHOTS]


def still_refusal(profile: dict, keys=None) -> Optional[str]:
    """Why *profile* (a ``generation_profile``, or the part of one a patch
    sends: *keys* limits the check to the keys it sets) would put a still
    with motion in an episode, in one sentence naming the fully animated
    budget profiles, or None. Plan 33 stage 4: a tier under 2 makes no clip
    at all; a budget profile whose animate mode is not ``all_shots`` leaves
    shots without one."""
    profile = profile or {}
    keys = set(profile) if keys is None else set(keys)
    animated = ", ".join(_animated_profile_ids())
    why = None
    if "tier" in keys:
        try:
            tier = int(profile.get("tier") or 1)
        except (TypeError, ValueError):
            tier = None
        if tier is not None and tier < 2:
            why = f"generation_profile.tier {profile.get('tier')!r} makes no clip (tier 2 or 3 does)"
    if why is None and "budget_profile" in keys:
        chosen = profile.get("budget_profile")
        if chosen not in _animated_profile_ids():
            why = f"budget_profile {chosen!r} does not animate every shot"
    if why is None:
        return None
    return (f"Every shot of an episode is a video clip, never a still with camera motion: {why}. Use tier 2 or 3 "
            f"with a budget profile that animates every shot: {animated}.")


def _create_kwargs(*, preset, style, episode_format, generation_profile) -> dict:
    """``StoryStore.create``'s choices: the preset's (``presets.merge``) with
    the caller's on top, or -- without a preset -- :func:`animated_profile`
    with the caller's profile keys on top (plan 33 stage 4: never the
    store's tier-1 default). An unknown preset, or a profile that would put
    a still in an episode (:func:`still_refusal`), is a tool error saying
    why."""
    if preset is None:
        kwargs = {"style_template_id": style, "episode_template_id": episode_format,
                  "generation_profile": dict(animated_profile(), **(generation_profile or {}))}
    else:
        try:
            kwargs = presets.merge(preset, style_template_id=style, generation_profile=generation_profile,
                                   episode_template_id=episode_format)
        except presets.UnknownPreset as exc:
            raise ToolError(str(exc)) from None
    refusal = still_refusal(kwargs.get("generation_profile") or {})
    if refusal:
        raise ToolError(refusal)
    return kwargs


def _budget_profiles() -> list:
    """``[{id, label, cap_usd}]`` from ``templates/budget_profiles.json``: the
    ones that animate every shot only (plan 33 stage 4)."""
    try:
        profiles = budget_mod.load_profiles()["profiles"]
    except (OSError, ValueError) as exc:
        return [{"error": str(exc)}]
    return [{"id": pid, "label": doc.get("label") or pid.replace("_", " ").capitalize(),
             "cap_usd": float(doc.get("cap_usd") or 0.0)}
            for pid, doc in profiles.items() if pid in defaults.BUDGET_PROFILES and doc.get("animate") == ALL_SHOTS]


def _episode_formats() -> list:
    """``[{id, label, window_s: [min, max], target_s, scenes: [min, max]}]``, one per shipped episode
    template."""
    out = []
    for tid in templates.list_episode_template_ids():
        try:
            doc = templates.load_episode_template(tid)
        except Exception:  # noqa: BLE001 - a format that cannot be read is listed by its id
            out.append({"id": tid})
            continue
        label = doc.get("label")
        out.append({"id": tid, "label": label.get("en") if isinstance(label, dict) else label,
                    "window_s": list(doc.get("window_s") or []), "target_s": doc.get("target_s"),
                    "scenes": list(doc.get("scenes") or [])})
    return out


def _estimate_env(backend: StoryBackend) -> dict:
    """The Settings an estimate reads: the director's, with the chat as the writer (what a run uses)."""
    return {**backend.director.settings_env, **CHAT_SETTINGS}


def _not_yet(what, ep, message) -> dict:
    return {"what": what, "episode": ep, "ready": False, "est_usd": 0.0, "message": message, "details": None}


def _cast_estimate(backend: StoryBackend, story: dict, env: dict) -> dict:
    """The pictures the cast step would buy for the chosen concept's sketched
    characters (``workflow.agent_cast_pick``: the sketch within the cast's
    size limits) and what the story's own still lack (``workflow.cast_units``), priced as the
    gate prices them (``workflow.generation_budget``). Calls nothing."""
    stories = backend.stories
    if not story.get("concept"):
        return _not_yet("cast", None, "No cost before the pictures: choose a concept first. The cast's pictures "
                                      "are priced once its characters are known.")
    # The characters a cast run would make: the concept's sketch, within the cast's size limits.
    sketch = workflow.agent_cast_pick(stories, story)
    units = workflow.cast_units(stories, story, selected=sketch)
    images = workflow.image_verdict(stories, story, units["images"], env=env)
    edit = workflow.edit_readiness(stories, story, env=env, qty=units["edit_images"])
    budget = workflow.generation_budget(stories, story, units, images if units["images"] else None,
                                        edit if units["edit_images"] else None, env=env)
    usd = float(budget["usd"] or 0.0)
    if not (units["images"] or units["edit_images"]):
        message = "Every character already has its pictures: the cast step buys nothing more."
    else:
        message = (f"The cast step would draw {units['images']} portrait{'s' if units['images'] != 1 else ''} "
                   f"and {units['edit_images']} reference sheet{'s' if units['edit_images'] != 1 else ''} "
                   f"for {len(sketch)} sketched character{'s' if len(sketch) != 1 else ''}: about ${usd:.2f}. "
                   "You write the characters' texts yourself, at no cost.")
    refusals = []
    if units["images"] and not images.get("ready"):
        refusals.append(str(images.get("message") or ""))
    if units["edit_images"] and not edit.get("ready"):
        refusals.append(str(edit.get("message") or ""))
    if budget.get("refusal") and budget.get("blocks"):
        refusals.append(str(budget.get("message") or ""))
    refusals = [text for text in refusals if text]
    if refusals:
        message += " It cannot run yet: " + " ".join(refusals)
    return {"what": "cast", "episode": None, "ready": not refusals, "est_usd": round(usd, 4), "message": message,
            "details": {"units": units, "budget": budget}}


def _episode_estimate(backend: StoryBackend, story: dict, ep: int, env: dict) -> dict:
    """What story_make_episode would do and spend on episode *ep*
    (``workflow.fast_track_estimate``: every step from the script to the
    metadata, an upper bound before the script and storyboard exist), and
    the pictures-and-clips part alone once the storyboard is approved
    (``workflow.assets_estimate``). Calls nothing."""
    stories = backend.stories
    try:
        ec = workflow.episode_context(stories, story, ep, step=MAKE_EPISODE_STEP)
    except workflow.WorkflowError as exc:
        return _not_yet("episode", ep, f"Episode {ep} cannot be made yet: {exc}")
    whole = workflow.fast_track_estimate(ec, env=env)
    try:
        assets = workflow.assets_estimate(ec, env=env)
    except workflow.WorkflowError as exc:
        assets = {"message": f"The pictures and clips alone are priced once the storyboard is approved ({exc})"}
    usd = float(whole.get("est_usd") or 0.0)
    exact = bool((whole.get("images") or {}).get("exact"))
    stop = whole.get("stops_at")
    paid = whole.get("paid") or {}
    message = (f"Making episode {ep} would spend {'' if exact else 'up to '}${usd:.2f} on pictures, clips and "
               f"voices; the writing is yours, at no cost. {paid.get('message') or ''}").strip()
    if stop:
        message += f" It would stop at the {stop.get('step')}: {stop.get('reason')}"
    return {"what": "episode", "episode": ep, "ready": stop is None and paid.get("verdict") != "blocked",
            "est_usd": round(usd, 4), "message": message, "details": {"episode": whole, "assets": assets}}


def _render_estimate(backend: StoryBackend, story: dict, ep: int) -> dict:
    """``workflow.render_estimate`` of episode *ep* (free, on this server)."""
    try:
        ec = workflow.episode_context(backend.stories, story, ep, step="render")
        body = workflow.render_estimate(ec, {})
    except workflow.WorkflowError as exc:
        return _not_yet("render", ep, f"Episode {ep} cannot be rendered yet: {exc}")
    return {"what": "render", "episode": ep, "ready": bool(body.get("ready", True)), "est_usd": 0.0,
            "message": str(body.get("message") or ""), "details": body}


def _story_estimate(backend: StoryBackend, story: dict, env: dict) -> dict:
    """``workflow.story_fast_track_estimate``: the one-run story from its
    seed to episode 1, every part still to do, summed. Calls nothing."""
    try:
        body = workflow.story_fast_track_estimate(backend.stories, story, env=env)
    except workflow.WorkflowError as exc:
        return _not_yet("story", 1, f"{exc} (create the story with preset 'fruit_drama', or patch its "
                                    "generation_profile with mode 'agent').")
    usd = round(float(body.get("est_usd") or 0.0), 4)
    message = str(body.get("message") or "")
    stop = body.get("stops_at")
    if stop:
        message = (f"The one-run story (from the idea to episode 1) would spend "
                   f"{'' if body.get('exact') else 'up to '}${usd:.2f}, but it cannot run yet: it would stop at "
                   f"part {stop.get('number')} ({stop.get('part')}): {stop.get('reason')}")
    return {"what": "story", "episode": 1, "ready": bool(body.get("ready")), "est_usd": usd, "message": message,
            "details": body}


def estimate(backend: StoryBackend, story_id: str, what: str, episode: Optional[int] = None) -> dict:
    """story_estimate's answer (see the tool)."""
    if what not in ESTIMATES:
        raise ToolError(f"what must be one of {', '.join(ESTIMATES)}")
    story = _answering(backend.load, story_id)
    env = _estimate_env(backend)
    if what == "cast":
        return _answering(_cast_estimate, backend, story, env)
    if what == "story":
        return _answering(_story_estimate, backend, story, env)
    if episode is None:
        raise ToolError(f"Name the episode for the {what} estimate (episode=1 for the first).")
    if what == "episode":
        return _answering(_episode_estimate, backend, story, episode, env)
    return _answering(_render_estimate, backend, story, episode)


def register(mcp, backend: StoryBackend) -> None:
    stories = backend.stories
    director = backend.director

    # ---------------------------------------------------------- reading

    @mcp.tool
    def story_list() -> list:
        """Free. Every story in the store: id, title, language, status, updated time."""
        return stories.list()

    @mcp.tool
    def story_create(language: str, seed_text: Optional[str] = None, style: Optional[str] = None,
                     episode_format: Optional[str] = None, generation_profile: Optional[dict] = None,
                     preset: Optional[str] = None) -> dict:
        """Free. Create a draft story. Every shot of every episode is a video clip. language: 'fr' or 'en'.
        seed_text: the idea in a few lines (optional). preset: one of story_options().presets ids -- e.g.
        'fruit_drama': the fruit-drama look, pictures and clips made on your own GPU, a cast of fruits, ready
        for story_make_episode and the one-run story. Without a preset the story is made on your own GPU
        (budget profile own_gpu, tier 3). style: one of story_options().styles ids (optional, pickable later
        at the style step). episode_format: an id of story_options().episode_formats (optional).
        generation_profile: {budget_profile (one of story_options().budget_profiles), route, ...}
        (optional); a tier under 2 or a budget profile not in that list is refused. What you name wins over
        the preset: a style or format replaces the preset's, and a generation_profile's keys replace the
        preset's (or the default's) one by one. Returns the story document; its story_id is what every
        other tool takes."""
        kwargs = _create_kwargs(preset=preset, style=style, episode_format=episode_format,
                                generation_profile=generation_profile)
        return _answering(stories.create, language=language, seed_text=seed_text, now=now(), **kwargs)

    @mcp.tool
    def story_options() -> dict:
        """Free. The choices a story can be made with: presets (id, label, summary, what each sets), styles
        (id, name), episode formats (id, length window in seconds, number of scenes), budget profiles (id,
        label, spending cap in dollars; each animates every shot), the step names the director runs, the
        documents and entity kinds the read tools take."""
        styles = []
        for tid in templates.list_style_ids():
            try:
                style = templates.load_style(tid)
                styles.append({"id": tid, "name": style.get("name"),
                               "summary": str(style.get("summary") or style.get("description") or "")[:160]})
            except Exception:  # noqa: BLE001
                styles.append({"id": tid})
        return {"presets": presets.list_presets(), "styles": styles, "episode_formats": _episode_formats(),
                "budget_profiles": _budget_profiles(),
                "languages": list(getattr(defaults, "LANGUAGES", None) or ("fr", "en")),
                "steps": list(STEP_MODULES), "docs": list(story_store.DOC_NAMES), "episode_docs": list(EPISODE_DOCS),
                "entity_kinds": list(ENTITY_KINDS)}

    @mcp.tool
    def story_get(story_id: str) -> dict:
        """Free. The story at a glance: story.json (bible, approvals, status, concept, profile), its recipe
        (the preset's, or null), style lock,
        season, knowledge, the cast/places/props summaries with what each still lacks (progress), the
        episodes, and the director's recent runs on it. Read this before deciding the next step."""
        return _answering(_story_overview, backend, story_id)

    @mcp.tool
    def story_doc(story_id: str, name: str) -> dict:
        """Free. One story document in full: story.json, style_lock.json, concepts.json, season.json,
        places_proposal.json, knowledge.json, style_preview.json."""
        if name not in story_store.DOC_NAMES:
            raise ToolError(f"name must be one of {', '.join(story_store.DOC_NAMES)}")
        doc = _answering(stories.read_doc, story_id, name)
        if doc is None:
            raise ToolError(f"{name} does not exist yet on story {story_id}")
        return doc

    @mcp.tool
    def story_entities(story_id: str, kind: str) -> list:
        """Free. The full documents of one kind of entity: 'characters', 'places' or 'props' (reference
        sheets, voices, approvals included). story_get gives the short form."""
        if kind not in ENTITY_KINDS:
            raise ToolError(f"kind must be one of {', '.join(ENTITY_KINDS)}")
        return _answering(stories.list_entities, story_id, ENTITY_KINDS[kind])

    @mcp.tool
    def story_entity(story_id: str, kind: str, entity_id: str) -> dict:
        """Free. One entity's document: kind 'characters'/'places'/'props', its id (char_id, place_id,
        prop_id). The image paths it lists are under outputs/ (view_file shows them)."""
        if kind not in ENTITY_KINDS:
            raise ToolError(f"kind must be one of {', '.join(ENTITY_KINDS)}")
        doc = _answering(stories.read_entity, story_id, ENTITY_KINDS[kind], entity_id)
        try:
            doc = dict(doc, folder=os.path.relpath(stories.entity_dir(story_id, ENTITY_KINDS[kind], entity_id),
                                                   backend.outputs_dir))
        except Exception:  # noqa: BLE001
            pass
        return doc

    @mcp.tool
    def episode_get(story_id: str, ep: int) -> dict:
        """Free. The episode page: script, storyboard, the template, and the state of each (none, writing,
        complete, approved; stale scenes; what the script step would still write)."""
        story = _answering(backend.load, story_id)
        return _answering(workflow.episode_view, stories, story, ep)

    @mcp.tool
    def episode_doc(story_id: str, ep: int, name: str) -> dict:
        """Free. One episode document in full: script.json, storyboard.json, assets.json,
        render_manifest.json, metadata_pack.json, memory.json, feedback.json, proposals.json,
        consistency_report.json."""
        if name not in EPISODE_DOCS:
            raise ToolError(f"name must be one of {', '.join(EPISODE_DOCS)}")
        doc = _answering(stories.read_episode_doc, story_id, ep, name)
        if doc is None:
            raise ToolError(f"episode {ep} of story {story_id} has no {name} yet")
        return doc

    # ------------------------------------------------------------ steps

    @mcp.tool
    def story_step_start(story_id: str, step: str, ep: Optional[int] = None, params: Optional[dict] = None,
                         wait_s: int = 25) -> dict:
        """Run a story step with YOU as the writer. The step runs in the background; this returns its first
        event: state 'waiting' with a 'pending' prompt (system, user, schema, max_tokens) that you answer
        with story_step_answer, or 'done' with its result, or 'failed' with the error, or 'running' after
        wait_s (poll with story_step_status). One run per story at a time. Steps that make pictures or
        clips spend money (cast, places, style_preview, assets, regenerate of an image or clip, fast-track,
        story-fast-track) -- say so, with story_estimate's figure, before starting them.

        The steps, in story order, and their params (all optional unless said):
        - concepts: {count: how many concept cards}. Then story_choose_concept.
        - bible: none. Then story_approve('bible').
        - style: {template_id (default: the story's style, else the concept's), overrides: {dotted path:
          value}, consistency_mode}. Builds the style draft on this server, no writing, no cost. Then
          story_approve('style') -- cast, places_proposal and places refuse until the style is approved.
        - style_preview: none. Three sample pictures of the style (costs a few cents).
        - cast: {selected: [names from the concept's cast sketch], custom: [{name, role, one_line,
          archetype?}]}. Then story_approve('character:<id>') or story_approve_all('cast').
        - places_proposal: none. places: {places: [{name}], props: [{name}]} (default: the saved proposal).
          Then story_approve_all('places').
        - season: {episodes: 3 to 12}. Then story_approve('season'). knowledge: none (animated stories).
          Then story_approve('knowledge').
        - script (ep): {measure_voices, check_only}. Then story_approve('script:<ep>').
        - storyboard (ep): {fast: true for the plan without writing}. Then story_approve('storyboard:<ep>').
        - assets (ep): {align_words}. Every shot becomes a video clip. This is TWO passes: the first run
          makes and checks the keyframes, then stops; you look at them and call
          story_approve('keyframes:<ep>'); a second assets run buys the clips. Then story_approve('assets:<ep>').
          story_approve('assets:<ep>') and render refuse while a shot has no clip.
        - render (ep): {subtitles: style|word_pop|two_line|none, encoder: libx264|auto}. Free, on this
          server. A missing or failed clip stops the render and names the shot's target
          shot:<ep>:<shot_id>:video: regenerate that clip, approve the assets again, then render.
        - metadata (ep), memory (ep), feedback (ep), propose-next (ep): none. rerender (ep): none.
        - regenerate: {target, note (what to change), voice ({provider, voice_id} for a voice target)}.
          Targets: concepts, bible:<field>, character:<id>:text, character:<id>:image:portrait|turnaround|
          expressions, character:<id>:voice, place:<id>:text, place:<id>:image:<variant>, prop:<id>:text,
          prop:<id>:image, season:<ep>, scene:<ep>:<scene_id>, hook:<ep>, cliffhanger:<ep>, teaser:<ep>,
          shot:<ep>:<shot_id>:plan, shot:<ep>:<shot_id> (its picture), shot:<ep>:<shot_id>:video (its clip),
          shot:<ep>:<shot_id>:closeup:<line_id> (on your own GPU, a shot cut into one talking clip per line:
          that line's close-up picture and its clip), line:<ep>:<line_id> (its voice), metadata:<ep>:<platform>.
        - fast-track (ep): {storyboard: t1|fast, stop_at_keyframes, stop_on_script_issues}: one episode
          from its script to its metadata pack in one run (story_make_episode is the short form).
        - story-fast-track: none. A story in agent mode (preset 'fruit_drama') from its idea to episode 1
          rendered, in one run, approving each document by rule; you still answer every writing prompt.
        ep: the episode number, for the steps marked (ep)."""
        return _answering(director.start, story_id, step, ep=ep, params=params, wait=wait_s)

    @mcp.tool
    def story_make_episode(story_id: str, episode: int, stop_at_keyframes: bool = False,
                           wait_s: int = 25) -> dict:
        """Make one episode -- script, storyboard, keyframes, clips, render, metadata pack -- in ONE run
        instead of eight step starts (the fast-track step, the same run mechanics as story_step_start).
        You still answer every writing prompt: each one comes back as state 'waiting' with a 'pending'
        prompt for story_step_answer, exactly as with story_step_start. The run approves the script, the
        storyboard and the assets itself when they pass their checks. Keyframes: by default the run approves
        them itself once they are made and checked (a keyframe the check still flags is kept with its
        warning and named at the end, never a stop) and goes on to buy the clips; with
        stop_at_keyframes=true it stops there instead (state 'failed' with a sentence saying so) -- look at
        them, call story_approve('keyframes:<episode>'), then call story_make_episode again. Before the
        clips it checks the money: a paid part over a cap, or while paid generation is off, stops it before
        anything is bought. Any stop keeps everything made so far: call story_make_episode again and it
        picks up where it stopped, repeating nothing. Ask story_estimate(what='episode') first and say the
        figure. The story must be ready (concept, bible, style, cast, places, season approved)."""
        params = {"stop_at_keyframes": True} if stop_at_keyframes else {}
        return _answering(director.start, story_id, MAKE_EPISODE_STEP, ep=episode, params=params, wait=wait_s)

    @mcp.tool
    def story_estimate(story_id: str, what: str, episode: Optional[int] = None) -> dict:
        """Free; calls nothing. What a step would spend, in dollars, before you start it. what: 'cast' (the
        pictures the cast step would buy for the concept's sketched characters), 'episode' (everything
        story_make_episode would buy for that episode; needs episode), 'render' (the render; free, on this
        server; needs episode), 'story' (the one-run story from its idea to episode 1; agent-mode stories).
        Answers {what, episode, ready, est_usd, message (plain words), details (the full estimate)}; when the
        story is not far enough along, ready is false and the message says what comes first."""
        return estimate(backend, story_id, what, episode)

    @mcp.tool
    def story_step_answer(handle: str, answer: dict, wait_s: int = 25) -> dict:
        """Answer a pending prompt (its handle from the run's 'pending'). answer: a JSON object exactly in
        the prompt's schema, in the story's language, within max_tokens — the step's own validator checks
        it and asks again with the reason if it refuses (you then get the same prompt with the refusal
        under it; a second refusal fails the step). Returns the run's next event like story_step_start."""
        return _answering(director.answer, handle, answer, wait=wait_s)

    @mcp.tool
    def story_step_status(run_id: str, wait_s: int = 0) -> dict:
        """Free. The run as it is (or after waiting up to wait_s for its next event): state, pending
        prompt, log tail, result or error."""
        return _answering(director.status, run_id, wait=wait_s)

    @mcp.tool
    def story_step_cancel(run_id: str) -> dict:
        """Free. Cancel a run; what the step already wrote stays."""
        return _answering(director.cancel, run_id)

    @mcp.tool
    def story_runs(story_id: Optional[str] = None) -> list:
        """Free. The director's runs (all, or one story's), newest first."""
        return director.list(story_id)

    # -------------------------------------------------------- approvals

    @mcp.tool
    def story_approve(story_id: str, doc: str, approve_anyway: bool = False, direction: Optional[int] = None) -> dict:
        """Approve one document, by the app's rules (refused with the reason while something is missing).
        doc: bible | style (after the style step) | season | knowledge | character:<id> | place:<id> |
        prop:<id> | script:<ep> | storyboard:<ep> | assets:<ep> | keyframes:<ep> | memory:<ep> | feedback:<ep> | proposals:<ep>.
        approve_anyway: script/keyframes/entity with a failed check. direction: feedback's chosen
        direction (0, 1, 2) or omitted."""
        story = _answering(backend.load, story_id)
        word, sep, eid = doc.partition(":")
        if sep and eid and word in workflow.EPISODE_APPROVALS:
            ep = _answering(workflow.episode_bounds, stories, story, eid)
            if word == "script":
                _answering(workflow.approve_script, stories, story_id, ep, approve_anyway=approve_anyway, now=now())
            elif word == "storyboard":
                _answering(workflow.approve_storyboard, stories, story_id, ep, now=now())
            else:
                _answering(workflow.approve_assets, stories, story_id, ep, now=now())
            return _answering(workflow.episode_view, stories, backend.load(story_id), ep)
        if sep and eid and word == workflow.KEYFRAMES_APPROVAL:
            ep = _answering(workflow.episode_bounds, stories, story, eid)
            _answering(workflow.approve_keyframes, stories, story_id, ep, approve_anyway=approve_anyway, now=now())
            return _answering(workflow.episode_view, stories, backend.load(story_id), ep)
        if sep and eid and word in workflow.SERIES_APPROVALS:
            ep = _answering(workflow.episode_bounds, stories, story, eid)
            kwargs = {"direction": direction} if direction is not None else {}
            _answering(workflow.approve_series, stories, story_id, doc, now=now(), **kwargs)
            return _answering(workflow.episode_view, stories, backend.load(story_id), ep)
        if sep and eid and word in workflow.ENTITY_KINDS_BY_WORD:
            kind = workflow.ENTITY_KINDS_BY_WORD[word]
            return _answering(workflow.approve_entity, stories, story_id, kind, eid, now=now(), anyway=approve_anyway)
        variant = workflow.parse_variant_approval(doc)
        if variant is not None:
            return _answering(workflow.approve_variant, stories, story_id, variant[0], variant[1], now=now())
        if doc == "knowledge":
            return _answering(workflow.approve_knowledge, stories, story_id, now=now())
        if doc == "season":
            return _answering(workflow.approve_season, stories, story_id, now=now())
        if doc == "bible":
            return _answering(workflow.approve_bible, stories, story_id, now=now())
        if doc == "style":
            return _answering(workflow.approve_style, stories, story_id, now=now())
        return _answering(workflow.refuse_approval, doc)

    @mcp.tool
    def story_approve_all(story_id: str, group: str) -> dict:
        """Approve every complete, unapproved entity of a group: 'cast' (characters) or 'places' (places and
        props). Answers {approved, skipped (with what each lacks), refused}."""
        kinds = APPROVE_ALL_GROUPS.get(group)
        if kinds is None:
            raise ToolError(f"group must be one of {', '.join(APPROVE_ALL_GROUPS)}")
        _answering(backend.load, story_id)
        return _answering(workflow.approve_complete, stories, story_id, kinds, now=now())

    @mcp.tool
    def story_choose_concept(story_id: str, concept_id: Optional[str] = None, concept: Optional[dict] = None) -> dict:
        """Choose the story's concept: a library id or a generated card's id (gen_NN, from concepts.json),
        or a concept card you write yourself (concept: the generated-card shape). Returns the story."""
        return _answering(workflow.choose_concept, stories, story_id, concept_id=concept_id, concept=concept,
                          now=now())

    # ----------------------------------------------------------- edits

    @mcp.tool
    def story_patch(story_id: str, fields: dict) -> dict:
        """Edit story fields (bible fields such as logline, premise, tone, world, themes, audience; title,
        seed_text, narrator, generation_profile, episode_template_id). A changed bible field clears the
        bible's approval. generation_profile: a tier under 2, or a budget profile not in
        story_options().budget_profiles, is refused (every shot is a video clip). Returns the story."""
        sent = (fields or {}).get("generation_profile")
        if isinstance(sent, dict):
            refusal = still_refusal(sent, keys=sent.keys())
            if refusal:
                raise ToolError(refusal)
        return _answering(workflow.patch_story, stories, story_id, fields, now=now())

    @mcp.tool
    def entity_patch(story_id: str, kind: str, entity_id: str, fields: dict) -> dict:
        """Edit a character/place/prop's text fields (kind 'characters'/'places'/'props'). Returns the
        entity."""
        if kind not in ENTITY_KINDS:
            raise ToolError(f"kind must be one of {', '.join(ENTITY_KINDS)}")
        return _answering(workflow.patch_entity, stories, story_id, ENTITY_KINDS[kind], entity_id, fields, now=now())

    @mcp.tool
    def episode_patch(story_id: str, ep: int, doc: str, fields: dict) -> dict:
        """Edit an episode document inline: doc 'script' (lines, on-screen text), 'storyboard' (prompts,
        motion, transitions) or 'assets'. Returns the document."""
        if doc == "script":
            return _answering(workflow.patch_script, stories, story_id, ep, fields, now=now())
        if doc == "storyboard":
            return _answering(workflow.patch_storyboard, stories, story_id, ep, fields, now=now())
        if doc == "assets":
            return _answering(workflow.patch_assets, stories, story_id, ep, fields, now=now(),
                              env=director.settings_env)
        raise ToolError("doc must be script, storyboard or assets")

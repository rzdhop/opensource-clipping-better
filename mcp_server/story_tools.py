"""Stage 2: the story store and the steps as tools.

Reading: the story, its documents, its entities, an episode. Writing: a
step run with the chat as writer (``director``), the approvals, the
patches -- every one through ``clipping.aistory.workflow``, the same rules
the web API applies (minus its job-queue checks: the director is the only
thing running steps here, one per story). Nothing in this module invents a
document format: what a step writes is what the app's schemas say.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from fastmcp.exceptions import ToolError

from clipping.aistory import defaults, store as story_store, templates, workflow
from clipping.aistory.steps.entities import CHARACTERS, PLACES, PROPS

from .director import STEP_MODULES, Director, DirectorError

ENTITY_KINDS = {"characters": CHARACTERS, "places": PLACES, "props": PROPS}
EPISODE_DOCS = ("script.json", "storyboard.json", "assets.json", "render_manifest.json", "metadata_pack.json",
                "memory.json", "feedback.json", "proposals.json", "consistency_report.json")
APPROVE_ALL_GROUPS = {"cast": (CHARACTERS,), "places": (PLACES, PROPS)}


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
    out = {"story": story}
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
                     episode_format: Optional[str] = None, generation_profile: Optional[dict] = None) -> dict:
        """Free. Create a draft story. language: 'fr' or 'en'. seed_text: the idea in a few lines (optional).
        style: one of story_options().styles (optional, pickable later at the style step). episode_format:
        one of story_options().episode_formats (optional). generation_profile: {tier, route,
        consistency_mode, budget_profile, pipeline, ...} (optional; the server's defaults otherwise).
        Returns the story document; its story_id is what every other tool takes."""
        return _answering(stories.create, language=language, seed_text=seed_text, style_template_id=style,
                          generation_profile=generation_profile, episode_template_id=episode_format, now=now())

    @mcp.tool
    def story_options() -> dict:
        """Free. The choices a story can be made with: style template ids (with their names), episode format
        ids, the step names the director runs, the documents and entity kinds the read tools take."""
        styles = []
        for tid in templates.list_style_ids():
            try:
                style = templates.load_style(tid)
                styles.append({"id": tid, "name": style.get("name"),
                               "summary": str(style.get("summary") or style.get("description") or "")[:160]})
            except Exception:  # noqa: BLE001
                styles.append({"id": tid})
        return {"styles": styles, "episode_formats": templates.list_episode_template_ids(),
                "languages": list(getattr(defaults, "LANGUAGES", None) or ("fr", "en")),
                "steps": list(STEP_MODULES), "docs": list(story_store.DOC_NAMES), "episode_docs": list(EPISODE_DOCS),
                "entity_kinds": list(ENTITY_KINDS)}

    @mcp.tool
    def story_get(story_id: str) -> dict:
        """Free. The story at a glance: story.json (bible, approvals, status, concept, profile), style lock,
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
        """Run a story step with YOU as the writer. Steps: concepts, bible, cast, places_proposal, places,
        season, knowledge, script, storyboard, assets, render, metadata, memory, feedback, propose-next,
        regenerate (params {target, note}), rerender. ep: the episode number for script/storyboard/assets/
        render/metadata/memory/feedback/propose-next. The step runs in the background; this returns its
        first event: state 'waiting' with a 'pending' prompt (system, user, schema, max_tokens) that you
        answer with story_step_answer, or 'done' with its result, or 'failed' with the error, or 'running'
        after wait_s (poll with story_step_status). Steps that make images or clips spend GPU seconds
        (cast, places, assets, style_preview) — say so before starting them. One run per story at a time."""
        return _answering(director.start, story_id, step, ep=ep, params=params, wait=wait_s)

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
        doc: bible | style | season | knowledge | character:<id> | place:<id> | prop:<id> | script:<ep> |
        storyboard:<ep> | assets:<ep> | keyframes:<ep> | memory:<ep> | feedback:<ep> | proposals:<ep>.
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
        bible's approval. Returns the story."""
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

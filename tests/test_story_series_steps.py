"""The series steps of AI Story phase 5, stage 4 (plan 11): ``memory`` (S3),
``feedback`` (F1) and ``propose-next`` (N1), their approvals, the decisions
on N1's proposals, and the gate they feed (DEC-130 as amended: episode N+1's
script and storyboard need episode N's memory written, approved and fresh).

Every step runs on a real ``StoryStore`` under ``tmp_path``, seeded with the
phase-3 fixture story (``test_story_episode_steps``: French, ready, three
characters, two places, a prop, an eight-episode arc) whose episode 1 is
written by the real ``script`` runner on a fake LLM and approved. The LLM is
that module's stand-in for ``llm.run_chain``; the RC-M7 tests drive the real
``run_chain`` through a client factory that must never be reached. The cast
path (an accepted N1 character) runs the real ``cast`` runner on the
phase-2 fakes (``test_story_cast_steps``). Offline and hermetic: no key,
chain or cap of the machine reaches a test, no request leaves the process,
and the repository's ``data/`` files and ``outputs/stories`` are
fingerprinted before and after every test.

Covered: the gate's refusals, each naming the missing piece; a stale memory
blocking a new episode-2 script or storyboard but never un-approving what
episode 2 already has; memory re-run replacing its entry; a fold error named;
feedback capped and never trimmed, one paste per episode, F1's digest and
directions, the chosen direction; proposals written, a twist amending the arc
with history and keeping the season approval, a character queued on the cast
path (a lead folding the cast approval, a recurring one not: RC-M5), a
rejection recorded only; the story's approvals and status untouched by the
three steps (RC-M5); no paid link without ``allow_paid`` (RC-M7); races with a
character delete and ``approve_season`` during the call (re-read-then-write).

Stdlib + pytest (the CI environment, DEC-012). The new modules are imported
inside the tests (fixtures), so on the parent commit each test fails on its
own instead of the file failing to collect.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipping.aistory import schemas, series_memory, steps
from clipping.aistory.store import StoryStore

import test_story_cast_steps as tcs
import test_story_episode_steps as eps

ROOT = Path(__file__).resolve().parents[1]
NOW = eps.NOW
LATER = "2026-09-27T11:00:00+00:00"
LATEST = "2026-09-27T12:00:00+00:00"
KIWILO, MANGELLA, BROCCOLIA = eps.KIWILO, eps.MANGELLA, eps.BROCCOLIA
HOOK_PHONE, HOOK_BETRAY, HOOK_VOTE = eps.HOOK_PHONE, eps.HOOK_BETRAY, eps.HOOK_VOTE

REAL_FILES = tuple(ROOT / "data" / name for name in ("usage.json", "spend.json", "chain_test_ledger.json"))
REAL_STORIES = (ROOT / "outputs" / "stories", ROOT / "outputs" / "stories.json")

# Test values only: every request goes to a fake. The cast path's chains too.
SETTINGS = dict(tcs.SETTINGS)
# A chain whose only keyed link is paid, allow_paid off (DEC-115).
PAID_ONLY = {"LLM_CHAIN": "gemini/gemini-test,openrouter/test-model", "OPENROUTER_API_KEY": "test-openrouter-key"}


def _fingerprint(path: Path):
    if path.is_symlink() or path.exists():
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
        return "present"
    return None


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    """No key, chain, cap or limit of the machine reaches a test; no request
    leaves the process (LLM, image, editor, TTS); nothing is written outside
    ``tmp_path``. The union of the phase-2 and phase-3 fixtures' rules."""
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env (A-049)
    from clipping.providers import adapters, budget, images, limits, llm, local_comfyui, pacing, transport

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in tcs.GEN_VARS + eps.CHAIN_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("LIMIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LIMIT_POLLINATIONS_RPM", "0")
    monkeypatch.setenv("LIMIT_EDGE_RPM", "0")
    monkeypatch.setenv("USAGE_PATH", str(tmp_path / "data" / "usage.json"))
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "data" / "spend.json"))
    limits.reset()
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    adapters.load_all()

    def no_network(method, url, **_kwargs):
        raise AssertionError(f"a real request was attempted: {method} {url}")

    def no_sdk(**_kwargs):
        raise AssertionError("the OpenAI SDK was reached")

    monkeypatch.setattr(images, "urllib_transport", no_network)
    monkeypatch.setattr(local_comfyui, "urllib_transport", no_network)
    monkeypatch.setattr(transport, "urllib_transport", no_network)
    monkeypatch.setattr(images, "_openai_client", no_sdk)

    before = {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES}
    yield
    limits.reset()
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    assert {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES} == before


@pytest.fixture
def store(tmp_path):
    return StoryStore(tmp_path / "outputs", on_log=lambda line: None)


@pytest.fixture
def wf():
    return importlib.import_module("clipping.aistory.workflow")


@pytest.fixture
def m():
    """The stage-4 step modules (absent on the parent commit) and the phase-3 ones."""
    from clipping.aistory.steps import episode_common, feedback, memory, propose_next, script, storyboard

    return SimpleNamespace(memory=memory, feedback=feedback, propose_next=propose_next, script=script,
                           storyboard=storyboard, common=episode_common)


# ------------------------------------------------------------------ helpers

def _refused(wf, code, call, *args, **kwargs):
    with pytest.raises(wf.WorkflowError) as info:
        call(*args, **kwargs)
    assert info.value.code == code, info.value.detail
    return info.value.detail


def _run(module, store, story_id, *, llm, step, ep, settings=None, params=None):
    return eps._run(module, store, story_id, llm=llm, step=step, ep=ep, settings=settings, params=params)


def _failed(module, store, story_id, *, llm, step, ep, settings=None):
    return eps._failed(module, store, story_id, llm=llm, step=step, ep=ep, settings=settings)


def _season(store, story_id):
    return store.read_doc(story_id, "season.json")


def _season_bytes(store, story_id):
    return (Path(store.story_dir(story_id)) / "season.json").read_bytes()


def _story_state(store, story_id):
    story = store.get(story_id)
    return copy.deepcopy(story["approvals"]), story["status"]


def _written_ep1(store, *, approve=True):
    """The fixture story with episode 1 written (E4 passed) and, with
    *approve*, its script approved."""
    from clipping.aistory import workflow

    story_id = eps._ready_story(store)
    eps._run(eps._new().script, store, story_id, llm=eps._script_llm(E4=[eps.E4_PASSED]))
    if approve:
        workflow.approve_script(store, story_id, 1, now=NOW)
    return story_id


S3_REPLY = {
    # "l alliance": a dropped French elision, repaired before the checks (DEC-144).
    "recap": "Kiwilo et Mangella scellent l alliance secrète au parloir, sous l oeil du téléphone.",
    "hooks_opened": [HOOK_PHONE, HOOK_BETRAY],
    "hooks_closed": [],
    "relationship_deltas": [{"pair": f"{KIWILO}|{MANGELLA}", "text": "alliés en secret"}],
}


def _s3(**changes):
    reply = copy.deepcopy(S3_REPLY)
    reply.update(changes)
    return reply


def _with_memory(m, store, story_id, *, reply=None, approve=True):
    """Episode 1's memory by the memory step (and approved)."""
    from clipping.aistory import workflow

    _run(m.memory, store, story_id, llm=eps.FakeLLM(S3=[reply or S3_REPLY]), step="memory", ep=1)
    if approve:
        workflow.approve_memory(store, story_id, 1, now=NOW)


def _approved_memory_story(m, store):
    story_id = _written_ep1(store)
    _with_memory(m, store, story_id)
    return story_id


def _ep2_llm():
    return eps._script_llm(E1=[eps._e1_ep2_paying({"s04": [HOOK_PHONE]})], E3=[eps.E3_EP2], E4=[eps.E4_PASSED])


def _edit_ep1(wf, store, story_id):
    """A line of episode 1 rewritten: its script moves to rev 2 and loses its approval."""
    script = eps._script(store, story_id, 1)
    line = next(line for scene in script["scenes"] for line in scene["lines"])
    wf.patch_script(store, story_id, 1, {"lines": [{"line_id": line["line_id"], "text": "Une autre réplique."}]},
                    now=LATER)
    assert eps._script(store, story_id, 1)["rev"] == 2


GATE_MISSING = ("Episode 1's series memory is not written yet: run memory for episode 1 and approve it, before "
                "writing episode 2.")
GATE_MISSING_SCRIPT = ("Episode 1's series memory is not written yet: approve episode 1's script, then run memory "
                       "for episode 1 and approve it, before writing episode 2.")
GATE_DRAFT = ("Episode 1's series memory is written but not approved: approve it (memory:1), before writing "
              "episode 2.")
GATE_STALE = ("Episode 1's series memory is out of date: episode 1's script changed since it was written. Approve "
              "episode 1's script, then run memory for episode 1 again and approve it, before writing episode 2.")
GATE_STALE_APPROVED = ("Episode 1's series memory is out of date: episode 1's script changed since it was written. "
                       "Run memory for episode 1 again and approve it, before writing episode 2.")


# ================================================================ the gate

def test_episode_2_waits_for_its_memory_then_for_its_approval_then_is_written(m, wf, store):
    story_id = _written_ep1(store)
    story = store.get(story_id)

    # No memory: the step, the fast storyboard and the workflow each name it.
    message, _ = _failed(m.script, store, story_id, llm=eps.FakeLLM(), step="script", ep=2)
    assert message == GATE_MISSING
    assert _refused(wf, "conflict", wf.episode_context, store, story, 2, step="script") == GATE_MISSING
    assert _refused(wf, "conflict", wf.episode_context, store, story, 2, step="storyboard") == GATE_MISSING
    with pytest.raises(steps.StepFailed) as caught:
        m.storyboard.build_fast(store, story_id, 2, now=NOW, on_log=eps.Log())
    assert str(caught.value) == GATE_MISSING
    assert wf.memory_state(store, story, 1) == "none"

    # The memory step writes a draft: still refused, now naming the approval.
    llm = eps.FakeLLM(S3=[S3_REPLY])
    summary, log = _run(m.memory, store, story_id, llm=llm, step="memory", ep=1)
    assert llm.prompts() == ["S3"]
    entry = _season(store, story_id)["series_memory"]["entries"]["ep01"]
    script = eps._script(store, story_id, 1)
    assert entry["recap"] == "Kiwilo et Mangella scellent l'alliance secrète au parloir, sous l'oeil du téléphone."
    assert entry["hooks_opened"] == [HOOK_PHONE, HOOK_BETRAY] and entry["hooks_closed"] == []
    assert entry["relationship_deltas"] == {f"{KIWILO}|{MANGELLA}": "alliés en secret"}
    assert entry["script_rev"] == script["rev"] == 1 and entry["approved_at"] is None and entry["at"]
    assert summary["ep"] == 1 and summary["script_rev"] == 1
    assert any("memory:1" in line for line in log)
    assert wf.memory_state(store, store.get(story_id), 1) == "draft"
    message, _ = _failed(m.script, store, story_id, llm=eps.FakeLLM(), step="script", ep=2)
    assert message == GATE_DRAFT
    assert _refused(wf, "conflict", wf.episode_context, store, store.get(story_id), 2, step="storyboard") == GATE_DRAFT

    # Approved: episode 2 is written from it.
    approved = wf.approve_memory(store, story_id, 1, now=LATER)
    assert approved["approved_at"] == LATER
    assert wf.memory_state(store, store.get(story_id), 1) == "approved"
    assert wf.episode_context(store, store.get(story_id), 2, step="script").ep == 2
    llm = _ep2_llm()
    _run(m.script, store, story_id, llm=llm, step="script", ep=2)
    assert "- Previous recap: Kiwilo et Mangella scellent l'alliance" in llm.of("E1")[0]["user"]
    assert f"- {HOOK_PHONE}\n- {HOOK_BETRAY}\n" in llm.of("E1")[0]["user"]
    assert eps._script(store, story_id, 2)["consistency_report"]["passed"] is True


def test_the_gate_names_an_unapproved_script_of_the_episode_before(m, wf, store):
    story_id = _written_ep1(store, approve=False)

    message, _ = _failed(m.script, store, story_id, llm=eps.FakeLLM(), step="script", ep=2)

    assert message == GATE_MISSING_SCRIPT


def test_episode_1_never_waits_for_any_memory(m, wf, store):
    story_id = eps._ready_story(store)
    assert wf.episode_context(store, store.get(story_id), 1, step="script").ep == 1
    assert wf.episode_context(store, store.get(story_id), 1, step="storyboard").ep == 1


def test_a_season_with_a_hand_written_recap_but_no_entry_is_refused(m, wf, store):
    """DEC-130 read ``recaps["ep01"]``; the amended gate reads the entry: a
    recap no memory step wrote is not an approved, fresh memory."""
    story_id = eps._ready_story(store, recaps={"ep01": "Un resume."})

    message, _ = _failed(m.script, store, story_id, llm=eps.FakeLLM(), step="script", ep=2)

    assert message == GATE_MISSING_SCRIPT


def test_a_stale_memory_blocks_a_new_episode_2_but_never_unapproves_it(m, wf, store):
    story_id = _approved_memory_story(m, store)
    _run(m.script, store, story_id, llm=_ep2_llm(), step="script", ep=2)
    wf.approve_script(store, story_id, 2, now=NOW)
    wf.build_fast_storyboard(store, store.get(story_id), 2, now=NOW, on_log=eps.Log())
    wf.approve_storyboard(store, story_id, 2, now=NOW)
    folder = Path(store.episode_dir(story_id, 2))
    ep2 = {name: (folder / name).read_bytes() for name in ("script.json", "storyboard.json")}

    _edit_ep1(wf, store, story_id)

    story = store.get(story_id)
    assert wf.memory_state(store, story, 1) == "stale"
    # A new episode-2 script or storyboard is refused, the step and the workflow alike ...
    message, _ = _failed(m.script, store, story_id, llm=eps.FakeLLM(), step="script", ep=2)
    assert message == GATE_STALE
    message, _ = _failed(m.storyboard, store, story_id, llm=eps.FakeLLM(), step="storyboard", ep=2)
    assert message == GATE_STALE
    assert _refused(wf, "conflict", wf.build_fast_storyboard, store, story, 2, now=LATER, on_log=eps.Log()) == \
        GATE_STALE
    # ... and approving the stale memory too.
    detail = _refused(wf, "conflict", wf.approve_memory, store, story_id, 1, now=LATER)
    assert detail.startswith("Episode 1's series memory is out of date")
    # What episode 2 has keeps its approvals, byte for byte (no cascade) ...
    assert {name: (folder / name).read_bytes() for name in ep2} == ep2
    assert set(wf.approved_episode_docs(store, story_id, 2)) >= {"script", "storyboard"}
    # ... and what is made from them is not gated by the memory.
    for step in ("assets", "render", "metadata"):
        assert wf.episode_context(store, story, 2, step=step).ep == 2
    # The fast track keeps an approved script and storyboard as they are: not gated either.
    assert wf.episode_context(store, story, 2, step="fast-track").ep == 2

    # Episode 1's script checked and approved again: the refusal now asks for the memory alone.
    eps._run(eps._new().script, store, story_id, llm=eps.FakeLLM(E4=[eps.E4_PASSED]))
    wf.approve_script(store, story_id, 1, now=LATER)
    message, _ = _failed(m.script, store, story_id, llm=eps.FakeLLM(), step="script", ep=2)
    assert message == GATE_STALE_APPROVED


def test_the_fast_track_is_gated_while_it_would_write_the_script(m, wf, store):
    story_id = _written_ep1(store)

    detail = _refused(wf, "conflict", wf.episode_context, store, store.get(story_id), 2, step="fast-track")

    assert detail == GATE_MISSING


def test_assets_render_and_metadata_of_episode_2_are_not_gated_by_memory(m, wf, store):
    story_id = _written_ep1(store)
    story = store.get(story_id)
    for step in ("assets", "render", "metadata"):
        assert wf.episode_context(store, story, 2, step=step).ep == 2, step
    # Their own inputs still decide (no script yet: the step's own sentence).
    assert "script" in _refused(wf, "conflict", wf.require_step_inputs,
                                wf.episode_context(store, story, 2, step="assets"), "assets")


def test_the_runners_meet_the_gate_only_where_they_would_write_the_script_or_storyboard(m, wf, store):
    """The runners' own checks, not only the workflow's: the assets, the
    render and the metadata of episode 2 are refused by what they are made
    from, never by the memory; the fast track meets the gate while it would
    write the script. Each is refused before anything is called."""
    from clipping.aistory.steps import assets, fast_track, metadata, render

    story_id = _written_ep1(store)

    def refusal(module, step):
        ctx, _log = eps._ctx(store, story_id, step=step, ep=2)
        with pytest.raises(steps.StepFailed) as caught:
            module.run(ctx)
        return str(caught.value)

    for module, step in ((assets, "assets"), (render, "render"), (metadata, "metadata")):
        message = refusal(module, step)
        assert "series memory" not in message and "Episode 2" in message, (step, message)
    assert refusal(fast_track, "fast-track") == GATE_MISSING


def test_approving_the_season_keeps_a_memory_entry_written_meanwhile(m, wf, store, monkeypatch):
    """``approve_season`` re-reads ``season.json`` under the store lock before
    it writes: an entry the memory step wrote between its checks and its
    write is kept (the reverse of the race the steps guard against)."""
    story_id = _written_ep1(store)
    season = _season(store, story_id)
    season["approved_at"] = None
    store.write_doc(story_id, "season.json", season, now=NOW)
    checked = wf.season

    def checked_then_memory_written(stories, sid):
        doc = checked(stories, sid)
        _with_memory(m, store, story_id, approve=False)
        return doc

    monkeypatch.setattr(wf, "season", checked_then_memory_written)
    wf.approve_season(store, story_id, now=LATER)

    after = _season(store, story_id)
    assert after["approved_at"] == LATER
    assert list(after["series_memory"]["entries"]) == ["ep01"]


# ================================================================ memory

def test_memory_needs_an_approved_script(m, wf, store):
    story_id = eps._ready_story(store)
    llm = eps.FakeLLM()
    message, _ = _failed(m.memory, store, story_id, llm=llm, step="memory", ep=1)
    assert message == ("Episode 1 has no script yet: write it and approve it (script:1) first; its series memory "
                       "is written from the approved script.")
    assert _refused(wf, "conflict", wf.series_context, store, store.get(story_id), 1, step="memory") == message

    story_id = _written_ep1(store, approve=False)
    message, _ = _failed(m.memory, store, story_id, llm=llm, step="memory", ep=1)
    assert message == ("Episode 1's script is not approved yet: approve it (script:1) first; its series memory is "
                       "written from the approved script.")
    assert llm.calls == []
    assert "entries" not in _season(store, story_id)["series_memory"]


def test_memory_is_asked_from_the_script_and_the_hooks_open_before_it(m, wf, store):
    story_id = _approved_memory_story(m, store)
    # Episode 2 written and approved; its memory closes the phone and opens the vote.
    _run(m.script, store, story_id, llm=_ep2_llm(), step="script", ep=2)
    wf.approve_script(store, story_id, 2, now=NOW)
    llm = eps.FakeLLM(S3=[_s3(recap="Le téléphone retrouvé, le vote approche.", hooks_opened=[HOOK_VOTE],
                              hooks_closed=[HOOK_PHONE], relationship_deltas=[])])

    _run(m.memory, store, story_id, llm=llm, step="memory", ep=2)

    call = llm.of("S3")[0]
    assert call["user"].startswith("Episode 2 script:\nScene s00 (recap) -- Le Parloir des Secrets, day -- ")
    assert f"Open hooks before this episode:\n- {HOOK_PHONE}\n- {HOOK_BETRAY}\n\n" in call["user"]
    assert "This episode's arc entry plans to leave open:\n- Kiwilo va-t-il trahir Mangella ?\n" in call["user"]
    assert f"Current relationships:\n- {KIWILO}|{MANGELLA}: alliés en secret\n" in call["user"]
    assert call["schema"]["properties"]["hooks_closed"]["items"]["enum"] == [HOOK_PHONE, HOOK_BETRAY]
    pairs = call["schema"]["properties"]["relationship_deltas"]["items"]["properties"]["pair"]["enum"]
    assert pairs == [f"{BROCCOLIA}|{KIWILO}", f"{BROCCOLIA}|{MANGELLA}", f"{KIWILO}|{MANGELLA}"]
    memory = _season(store, story_id)["series_memory"]
    assert memory["open_hooks"] == [HOOK_BETRAY, HOOK_VOTE]
    assert list(memory["recaps"]) == ["ep01", "ep02"]


def test_an_s3_reply_closing_a_hook_that_is_not_open_is_asked_again(m, wf, store):
    story_id = _written_ep1(store)
    llm = eps.FakeLLM(S3=[_s3(hooks_closed=["Un secret inventé ?"]), S3_REPLY])

    _summary, log = _run(m.memory, store, story_id, llm=llm, step="memory", ep=1)

    assert llm.prompts() == ["S3", "S3"]
    assert any("S3 reply rejected" in line and "is not an open hook" in line for line in log)
    assert _season(store, story_id)["series_memory"]["entries"]["ep01"]["hooks_closed"] == []


def test_rerunning_memory_replaces_its_entry_and_its_approval(m, wf, store):
    story_id = _approved_memory_story(m, store)
    assert _season(store, story_id)["series_memory"]["entries"]["ep01"]["approved_at"] == NOW

    _run(m.memory, store, story_id, llm=eps.FakeLLM(S3=[_s3(hooks_opened=[HOOK_BETRAY], relationship_deltas=[])]),
         step="memory", ep=1)

    memory = _season(store, story_id)["series_memory"]
    assert list(memory["entries"]) == ["ep01"]
    assert memory["entries"]["ep01"]["hooks_opened"] == [HOOK_BETRAY]
    assert memory["entries"]["ep01"]["approved_at"] is None  # a new draft asks for a new approval
    assert memory["open_hooks"] == [HOOK_BETRAY] and memory["relationship_state"] == {}
    assert memory == {**memory, **series_memory.fold_memory(memory["entries"])}


def test_a_fold_error_fails_the_step_naming_the_later_episode(m, wf, store):
    story_id = _approved_memory_story(m, store)
    season = series_memory.merge_entry(_season(store, story_id), 2, eps._memory_entry(
        "Le téléphone retrouvé.", [HOOK_VOTE], [HOOK_PHONE]))
    store.write_doc(story_id, "season.json", season, now=NOW)
    before = _season_bytes(store, story_id)

    message, _ = _failed(m.memory, store, story_id, llm=eps.FakeLLM(S3=[_s3(hooks_opened=[HOOK_BETRAY])]),
                         step="memory", ep=1)

    assert message.startswith("Episode 1's memory was not saved: this version no longer opens ")
    assert HOOK_PHONE in message and "episode 2" in message
    assert _season_bytes(store, story_id) == before


def test_approving_memory_needs_one_and_never_touches_the_story(m, wf, store):
    story_id = _written_ep1(store)
    assert _refused(wf, "conflict", wf.approve_memory, store, story_id, 1, now=NOW) == (
        "Episode 1 has no series memory yet: run memory for episode 1 first.")
    _with_memory(m, store, story_id, approve=False)
    state = _story_state(store, story_id)

    wf.approve_memory(store, story_id, 1, now=LATER)

    assert _story_state(store, story_id) == state
    assert _season(store, story_id)["series_memory"]["entries"]["ep01"]["approved_at"] == LATER


# ================================================================ feedback

FEEDBACK = "Top commentaires : on veut un vote truqué ! Broccolia est la meilleure. #TeamKiwilo"
STATS = "Vues : 12 400. Rétention : 61 %."
F1_REPLY = {
    "digest": "Le public adore Broccolia et réclame un vote truqué.",
    # "l île": repaired before the checks.
    "directions": ["Plus de Broccolia au parloir.", "Un vote truqué, sous les yeux de tous.",
                   "Kiwilo seul contre toute l île."],
}


def test_feedback_over_the_cap_is_refused_never_trimmed(m, wf, store):
    story_id = _written_ep1(store)
    before = _season_bytes(store, story_id)

    detail = _refused(wf, "invalid", wf.store_feedback, store, story_id, 1, "x" * 6001, now=NOW)
    assert "6001" in detail and "6000" in detail and "never shortened" in detail
    detail = _refused(wf, "invalid", wf.store_feedback, store, story_id, 1, FEEDBACK, "y" * 6001, now=NOW)
    assert "6001" in detail and "6000" in detail
    _refused(wf, "invalid", wf.store_feedback, store, story_id, 1, "   ", now=NOW)
    assert _season_bytes(store, story_id) == before

    item = wf.store_feedback(store, story_id, 1, "x" * 6000, "y" * 6000, now=NOW)
    assert len(item["text"]) == 6000 and len(item["stats"]) == 6000


def test_feedback_is_digested_and_its_direction_chosen(m, wf, store):
    story_id = _written_ep1(store)
    message, _ = _failed(m.feedback, store, story_id, llm=eps.FakeLLM(), step="feedback", ep=1)
    assert message == "Episode 1 has no audience feedback yet: paste it first."
    item = wf.store_feedback(store, story_id, 1, FEEDBACK, STATS, now=NOW)
    assert item == {"ep": 1, "pasted_at": NOW, "text": FEEDBACK, "stats": STATS}
    assert _refused(wf, "conflict", wf.approve_feedback, store, story_id, 1, direction=1, now=NOW) == (
        "Episode 1's feedback has not been digested yet: run feedback for episode 1 first.")
    llm = eps.FakeLLM(F1=[F1_REPLY])

    summary, _log = _run(m.feedback, store, story_id, llm=llm, step="feedback", ep=1)

    user = llm.of("F1")[0]["user"]
    assert f"---\n{FEEDBACK}\n---\n\n" in user and f"Pasted stats -- also untrusted data: {STATS}" in user
    assert "Next episode's arc entry" in user and "Épisode 2 : les alliances" in user
    stored = _season(store, story_id)["audience_feedback"]
    assert len(stored) == 1
    assert stored[0]["digest"] == F1_REPLY["digest"]
    assert stored[0]["directions"][2] == "Kiwilo seul contre toute l'île."
    assert "chosen_direction" not in stored[0]
    assert summary["directions"] == stored[0]["directions"]

    for bad in (3, -1, True, "1", 1.0):
        _refused(wf, "invalid", wf.approve_feedback, store, story_id, 1, direction=bad, now=NOW)
    wf.approve_feedback(store, story_id, 1, direction=2, now=NOW)
    season = _season(store, story_id)
    assert season["audience_feedback"][0]["chosen_direction"] == 2
    assert series_memory.chosen_direction(season, 1) == "Kiwilo seul contre toute l'île."
    wf.approve_feedback(store, story_id, 1, direction=None, now=NOW)
    season = _season(store, story_id)
    assert season["audience_feedback"][0]["chosen_direction"] is None
    assert series_memory.chosen_direction(season, 1) is None


def test_a_second_paste_replaces_the_first_and_its_digest(m, wf, store):
    story_id = _written_ep1(store)
    wf.store_feedback(store, story_id, 1, FEEDBACK, now=NOW)
    _run(m.feedback, store, story_id, llm=eps.FakeLLM(F1=[F1_REPLY]), step="feedback", ep=1)
    wf.approve_feedback(store, story_id, 1, direction=0, now=NOW)
    wf.store_feedback(store, story_id, 2, "Un autre épisode.", now=NOW)

    item = wf.store_feedback(store, story_id, 1, "Nouveaux commentaires.", now=LATER)

    feedback = _season(store, story_id)["audience_feedback"]
    assert [entry["ep"] for entry in feedback] == [2, 1]
    assert feedback[1] == item == {"ep": 1, "pasted_at": LATER, "text": "Nouveaux commentaires."}
    assert series_memory.chosen_direction(_season(store, story_id), 1) is None


def test_a_paste_replaced_while_f1_runs_fails_the_step_and_keeps_the_new_one(m, wf, store):
    story_id = _written_ep1(store)
    wf.store_feedback(store, story_id, 1, FEEDBACK, now=NOW)

    def replaced_meanwhile(call):
        wf.store_feedback(store, story_id, 1, "Collé pendant la lecture.", now=LATER)
        return F1_REPLY

    llm = eps.FakeLLM(F1=[replaced_meanwhile])
    message, _ = _failed(m.feedback, store, story_id, llm=llm, step="feedback", ep=1)

    assert message == "Episode 1's feedback was pasted again while it was being read: run feedback for episode 1 again."
    assert _season(store, story_id)["audience_feedback"] == [
        {"ep": 1, "pasted_at": LATER, "text": "Collé pendant la lecture."}]


# ============================================================= propose-next

N1_REPLY = {
    "characters": [
        {"name": "Figuette", "role": "recurring", "one_line": "Une figue timide qui voit tout.",
         "archetype": "témoin discret", "why": "Elle a vu qui a volé le téléphone."},
        {"name": "Ananax", "role": "guest", "one_line": "Un ananas arrogant venu du continent.",
         "archetype": None, "why": "Il bouscule l alliance."},
    ],
    "twists": [
        {"target_ep": 3, "summary": "Le téléphone révèle que le vote est truqué depuis le début.",
         "open_hooks_out": ["Qui truque le vote ?"], "why": "Paie la trahison annoncée."},
    ],
}


def _proposals(store, story_id, ep=2):
    return store.read_episode_doc(story_id, ep, "proposals.json")


def _proposed(m, store, *, reply=None, feedback=True):
    story_id = _approved_memory_story(m, store)
    if feedback:
        from clipping.aistory import workflow

        workflow.store_feedback(store, story_id, 1, FEEDBACK, now=NOW)
        _run(m.feedback, store, story_id, llm=eps.FakeLLM(F1=[F1_REPLY]), step="feedback", ep=1)
        workflow.approve_feedback(store, story_id, 1, direction=1, now=NOW)
    llm = eps.FakeLLM(N1=[reply or N1_REPLY])
    _run(m.propose_next, store, story_id, llm=llm, step="propose-next", ep=1)
    return story_id, llm


def test_propose_next_needs_a_fresh_approved_memory(m, wf, store):
    story_id = _written_ep1(store)
    llm = eps.FakeLLM()
    before = "before proposing what comes next in episode 2."
    message, _ = _failed(m.propose_next, store, story_id, llm=llm, step="propose-next", ep=1)
    assert message == ("Episode 1's series memory is not written yet: run memory for episode 1 and approve it, "
                       + before)
    _with_memory(m, store, story_id, approve=False)
    message, _ = _failed(m.propose_next, store, story_id, llm=llm, step="propose-next", ep=1)
    assert message == "Episode 1's series memory is written but not approved: approve it (memory:1), " + before
    wf.approve_memory(store, story_id, 1, now=NOW)
    _edit_ep1(wf, store, story_id)
    message, _ = _failed(m.propose_next, store, story_id, llm=llm, step="propose-next", ep=1)
    assert message.startswith("Episode 1's series memory is out of date") and message.endswith(before)
    assert _refused(wf, "conflict", wf.series_context, store, store.get(story_id), 1, step="propose-next") == message
    assert llm.calls == []
    assert _proposals(store, story_id) is None


def test_the_last_episode_has_nothing_to_propose_for(m, wf, store):
    story_id = eps._ready_story(store)

    message, _ = _failed(m.propose_next, store, story_id, llm=eps.FakeLLM(), step="propose-next", ep=8)

    assert message == "Episode 8 is the last the season plans: there is no episode 9 to propose for."


def test_propose_next_writes_the_proposals_of_the_next_episode(m, wf, store):
    story_id, llm = _proposed(m, store)

    call = llm.of("N1")[0]
    assert "Propose new material for episode 2." in call["user"]
    assert f"- Open hooks: {HOOK_PHONE}; {HOOK_BETRAY}" in call["user"]
    assert "Un vote truqué, sous les yeux de tous." in call["user"]  # the direction chosen on episode 1
    assert call["schema"]["properties"]["twists"]["items"]["properties"]["target_ep"]["enum"] == list(range(2, 9))
    doc = _proposals(store, story_id)
    assert doc["for_ep"] == 2 and doc["based_on"] == {"memory_ep": 1, "script_rev": 1}
    assert [item["item_id"] for item in doc["characters"]] == ["char_1", "char_2"]
    assert [item["item_id"] for item in doc["twists"]] == ["twist_1"]
    assert doc["characters"][1]["why"] == "Il bouscule l'alliance."  # repaired
    assert "archetype" not in doc["characters"][1] and doc["characters"][0]["archetype"] == "témoin discret"
    assert doc["decisions"] == {}
    assert schemas.next_proposals_errors(doc) == []

    # A re-run replaces the document: the decisions start again.
    wf.decide_proposal(store, story_id, 2, "char_2", accept=False, now=NOW)
    _run(m.propose_next, store, story_id, llm=eps.FakeLLM(N1=[N1_REPLY]), step="propose-next", ep=1)
    assert _proposals(store, story_id)["decisions"] == {}


def test_an_n1_character_named_like_one_of_the_cast_is_asked_again(m, wf, store):
    story_id = _approved_memory_story(m, store)
    known = copy.deepcopy(N1_REPLY)
    known["characters"][0]["name"] = "Broccolia"
    llm = eps.FakeLLM(N1=[known, N1_REPLY])

    _summary, log = _run(m.propose_next, store, story_id, llm=llm, step="propose-next", ep=1)

    assert llm.prompts() == ["N1", "N1"]
    assert any("N1 reply rejected" in line and "already a character" in line for line in log)
    assert _proposals(store, story_id)["characters"][0]["name"] == "Figuette"


def test_accepting_a_twist_amends_the_arc_keeps_the_season_approval_and_fills_history(m, wf, store):
    story_id, _llm = _proposed(m, store)
    season = _season(store, story_id)
    old = copy.deepcopy(season["arc"][2])
    state = _story_state(store, story_id)

    result = wf.decide_proposal(store, story_id, 2, "twist_1", accept=True, now=LATER)

    after = _season(store, story_id)
    entry = after["arc"][2]
    assert entry["summary"] == N1_REPLY["twists"][0]["summary"]
    assert entry["open_hooks_out"] == ["Qui truque le vote ?"]
    assert entry["history"] == [{"summary": old["summary"], "open_hooks_out": old["open_hooks_out"],
                                 "replaced_at": LATER, "source": "proposal"}]
    assert after["approved_at"] == season["approved_at"] == NOW
    assert _story_state(store, story_id) == state
    assert [e for i, e in enumerate(after["arc"]) if i != 2] == [e for i, e in enumerate(season["arc"]) if i != 2]
    assert result["decision"] == "accepted" and result["kind"] == "twist" and "cast" not in result
    assert _proposals(store, story_id)["decisions"] == {"twist_1": "accepted"}
    # A decision is final.
    assert "already accepted" in _refused(wf, "conflict", wf.decide_proposal, store, story_id, 2, "twist_1",
                                          accept=False, now=LATEST)
    assert _refused(wf, "not_found", wf.decide_proposal, store, story_id, 2, "twist_9", accept=True, now=LATEST)


def test_rejecting_records_the_decision_only(m, wf, store):
    story_id, _llm = _proposed(m, store)
    season = _season_bytes(store, story_id)
    state = _story_state(store, story_id)

    result = wf.decide_proposal(store, story_id, 2, "char_1", accept=False, now=LATER)

    assert result["decision"] == "rejected" and "cast" not in result
    assert _proposals(store, story_id)["decisions"] == {"char_1": "rejected"}
    assert _season_bytes(store, story_id) == season
    assert _story_state(store, story_id) == state
    assert {doc["char_id"] for doc in store.list_entities(story_id, "characters")} == {KIWILO, MANGELLA, BROCCOLIA}


def test_a_decision_is_checked_before_anything_is_written(m, wf, store):
    story_id = _written_ep1(store)
    assert "no proposals yet" in _refused(wf, "conflict", wf.decide_proposal, store, story_id, 2, "char_1",
                                          accept=True, now=NOW)
    story_id, _llm = _proposed(m, store, feedback=False)
    for kwargs in ({"accept": "yes"}, {"accept": True, "role": "hero"}):
        _refused(wf, "invalid", wf.decide_proposal, store, story_id, 2, "char_1", now=NOW, **kwargs)
    _refused(wf, "invalid", wf.decide_proposal, store, story_id, 2, "twist_1", accept=True, role="lead", now=NOW)
    # The memory the proposals were written from moved on: accepting is refused, rejecting is not.
    _edit_ep1(wf, store, story_id)
    detail = _refused(wf, "conflict", wf.decide_proposal, store, story_id, 2, "twist_1", accept=True, now=NOW)
    assert "propose-next for episode 1 again" in detail
    assert _proposals(store, story_id)["decisions"] == {}
    wf.decide_proposal(store, story_id, 2, "twist_1", accept=False, now=NOW)


def test_approving_the_proposals_needs_every_item_decided(m, wf, store):
    story_id, _llm = _proposed(m, store, feedback=False)
    detail = _refused(wf, "conflict", wf.approve_proposals, store, story_id, 2, now=NOW)
    assert detail == ("Episode 2's proposals are not all decided: accept or reject 'char_1', 'char_2' and "
                      "'twist_1' first.")
    for item_id in ("char_1", "char_2", "twist_1"):
        wf.decide_proposal(store, story_id, 2, item_id, accept=False, now=NOW)

    assert wf.approve_proposals(store, story_id, 2, now=NOW)["decisions"] == {
        "char_1": "rejected", "char_2": "rejected", "twist_1": "rejected"}


# --------------------------------------------------- an accepted character (RC-M5)

def _complete_cast(store, story_id, tmp_path):
    """Every character of the fixture story complete on disk (portrait,
    sheets, voice sample) and approved, so a cast run touches only a new one."""
    src = tmp_path / "ref.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0 reference")
    sample = tmp_path / "sample.mp3"
    sample.write_bytes(b"ID3 sample")
    for doc in store.list_entities(story_id, "characters"):
        cid = doc["char_id"]
        for which in ("portrait", "turnaround", "expressions"):
            store.write_media(story_id, "characters", cid, f"{which}.jpg", src)
        store.write_media(story_id, "characters", cid, "voice_sample.mp3", sample)
        doc["refs"]["turnaround"] = eps._image("turnaround.jpg", "prompt_only")
        doc["refs"]["expressions"] = eps._image("expressions.jpg", "prompt_only")
        doc["approved_at"] = NOW
        store.write_entity(story_id, "characters", doc, now=NOW)


def _run_cast(store, story_id, params):
    from clipping.aistory.steps import cast

    fakes = tcs._fakes()
    ctx, log = eps._ctx(store, story_id, step="cast", ep=None, params=params, settings=SETTINGS)
    summary = cast.run(ctx, runner=tcs.FakeLLM(K1=[tcs.K1_FIG]), time_fn=lambda: 100.0, sleep_fn=tcs._no_sleep,
                       adapters=fakes.adapters)
    return summary, log


@pytest.mark.parametrize("role, folds", [(None, False), ("guest", False), ("lead", True), ("support", True)])
def test_an_accepted_character_goes_through_the_cast_path(m, wf, store, tmp_path, role, folds):
    story_id, _llm = _proposed(m, store, feedback=False)
    _complete_cast(store, story_id, tmp_path)
    approvals, status = _story_state(store, story_id)
    assert status == "ready" and approvals["cast"] == NOW

    result = wf.decide_proposal(store, story_id, 2, "char_1", accept=True, role=role, now=LATER)

    final_role = role or "recurring"
    assert result["decision"] == "accepted" and result["kind"] == "character" and result["role"] == final_role
    assert result["folds_cast"] is folds
    assert result["cast"] == {"step": "cast", "params": {
        "custom": [{"name": "Figuette", "role": final_role, "one_line": "Une figue timide qui voit tout.",
                    "archetype": "témoin discret"}],
        "introduced_in": 2}}
    assert ("the cast approval is cleared" in result["message"]) is folds
    assert ("the story stays ready" in result["message"]) is not folds
    assert _proposals(store, story_id)["decisions"] == {"char_1": "accepted"}
    # Deciding changes nothing of the story yet: the cast step is queued by the caller.
    assert _story_state(store, story_id) == (approvals, status)
    wf.cast_request(store, store.get(story_id), result["cast"]["params"])  # the cast step's own check passes

    summary, _log = _run_cast(store, story_id, result["cast"]["params"])

    created = summary["created"]
    assert len(created) == 1
    figuette = store.read_entity(story_id, "characters", created[0])
    assert figuette["name"] == "Figuette" and figuette["role"] == final_role and figuette["descriptor"]
    assert _season(store, story_id)["series_memory"]["introduced"] == {"ep02": created}
    approvals_after, status_after = _story_state(store, story_id)
    if folds:
        # DEC-123 unchanged: an unapproved lead/support clears the cast approval.
        assert approvals_after["cast"] is None and status_after != "ready"
    else:
        assert approvals_after == approvals and status_after == "ready"


def test_accepting_a_character_the_cast_cannot_take_is_refused(m, wf, store):
    story_id, _llm = _proposed(m, store, feedback=False)
    doc = copy.deepcopy(eps.CHARACTERS[2])
    doc.update(char_id="char_figuette", name="Figuette", role="guest", approved_at=None)
    store.write_entity(story_id, "characters", doc, now=NOW)

    detail = _refused(wf, "conflict", wf.decide_proposal, store, story_id, 2, "char_1", accept=True, now=NOW)

    assert detail == "Figuette is already a character of this story: reject this proposal instead."
    assert _proposals(store, story_id)["decisions"] == {}


def test_the_cast_step_takes_introduced_in_as_an_episode_of_the_season(m, wf, store):
    story_id = eps._ready_story(store)
    custom = [{"name": "Figuette", "role": "guest", "one_line": "Une figue."}]
    for value in (0, 9, True, "2"):
        _refused(wf, "invalid", wf.cast_request, store, store.get(story_id), {"custom": custom, "introduced_in": value})
    assert wf.cast_request(store, store.get(story_id), {"custom": custom, "introduced_in": 2})[1] == custom


def test_e1_offers_only_the_approved_characters(m, wf, store):
    story_id = eps._ready_story(store)
    doc = copy.deepcopy(eps.CHARACTERS[2])
    doc.update(char_id="char_figuette", name="Figuette", role="guest", approved_at=None)
    store.write_entity(story_id, "characters", doc, now=NOW)
    assert store.get(story_id)["status"] == "ready"
    llm = eps._script_llm()

    eps._run(eps._new().script, store, story_id, llm=llm)

    call = llm.of("E1")[0]
    enum = call["schema"]["properties"]["scenes"]["items"]["properties"]["characters"]["items"]["enum"]
    assert "char_figuette" not in enum and set(enum) == {KIWILO, MANGELLA, BROCCOLIA}
    assert "Figuette" not in call["user"]


# ======================================================== RC-M5, RC-M7, races

def test_the_series_steps_never_touch_the_storys_approvals_or_status(m, wf, store):
    story_id = _written_ep1(store)
    state = _story_state(store, story_id)

    _with_memory(m, store, story_id)
    wf.store_feedback(store, story_id, 1, FEEDBACK, now=NOW)
    _run(m.feedback, store, story_id, llm=eps.FakeLLM(F1=[F1_REPLY]), step="feedback", ep=1)
    wf.approve_feedback(store, story_id, 1, direction=0, now=NOW)
    _run(m.propose_next, store, story_id, llm=eps.FakeLLM(N1=[N1_REPLY]), step="propose-next", ep=1)
    wf.decide_proposal(store, story_id, 2, "twist_1", accept=True, now=NOW)
    wf.decide_proposal(store, story_id, 2, "char_2", accept=False, now=NOW)

    assert _story_state(store, story_id) == state


@pytest.mark.parametrize("step", ["memory", "feedback", "propose-next"])
def test_a_chain_whose_only_keyed_link_is_paid_sends_nothing(m, wf, store, step):
    from clipping.providers import llm as llm_mod

    story_id = _written_ep1(store)
    if step == "propose-next":
        _with_memory(m, store, story_id)
    if step == "feedback":
        wf.store_feedback(store, story_id, 1, FEEDBACK, now=NOW)
    before = _season_bytes(store, story_id)
    constructed = []

    def factory(link, **kwargs):
        constructed.append(link.provider)
        raise AssertionError("no client may be built")

    module = {"memory": m.memory, "feedback": m.feedback, "propose-next": m.propose_next}[step]
    runner = functools.partial(llm_mod.run_chain, client_factory=factory)
    message, _ = _failed(module, store, story_id, llm=runner, step=step, ep=1, settings=PAID_ONLY)

    assert constructed == []
    assert "allow_paid is off" in message and "GOOGLE_API_KEY" in message
    assert _season_bytes(store, story_id) == before
    assert _proposals(store, story_id) is None


def test_a_paid_link_is_skipped_and_printed(m, wf, store):
    from clipping.providers import llm as llm_mod

    story_id = _written_ep1(store)
    settings = {"LLM_CHAIN": "gemini/gemini-test,openrouter/test-model", "GOOGLE_API_KEY": "test-gemini-key",
                "OPENROUTER_API_KEY": "test-openrouter-key"}
    constructed = []

    class Completions:
        def create(self, **kwargs):
            content = json.dumps(S3_REPLY, ensure_ascii=False)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                                   usage=SimpleNamespace(total_tokens=90))

    def factory(link, **kwargs):
        constructed.append(link.provider)
        return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    runner = functools.partial(llm_mod.run_chain, client_factory=factory, sleep_fn=tcs._no_sleep)
    _summary, log = _run(m.memory, store, story_id, llm=runner, step="memory", ep=1, settings=settings)

    assert constructed == ["gemini"]
    assert log.count("   ⏭ Skipping openrouter/test-model: paid link, allow_paid is off "
                     "(AI Story spends only on opt-in).") == 1


def test_a_character_deleted_and_the_season_approved_during_s3_are_both_kept(m, wf, store):
    story_id = _written_ep1(store)
    season = _season(store, story_id)
    season["approved_at"] = None
    season["arc"][0]["characters"].append(BROCCOLIA)
    season["series_memory"]["introduced"] = {"ep01": [BROCCOLIA]}
    store.write_doc(story_id, "season.json", season, now=NOW)
    reply = _s3(relationship_deltas=[{"pair": f"{KIWILO}|{MANGELLA}", "text": "alliés en secret"},
                                     {"pair": f"{BROCCOLIA}|{MANGELLA}", "text": "rivales jurées"}])

    def meanwhile(call):
        wf.delete_entity(store, story_id, "characters", BROCCOLIA, now=LATER)
        wf.approve_season(store, story_id, now=LATER)
        return reply

    _summary, log = _run(m.memory, store, story_id, llm=eps.FakeLLM(S3=[meanwhile]), step="memory", ep=1)

    after = _season(store, story_id)
    assert after["approved_at"] == LATER  # approve_season's write is kept
    # So is the delete's cleanup.
    assert after["arc"][0]["characters"] == [KIWILO, MANGELLA]
    assert after["series_memory"]["introduced"] == {"ep01": []}
    entry = after["series_memory"]["entries"]["ep01"]
    assert entry["relationship_deltas"] == {f"{KIWILO}|{MANGELLA}": "alliés en secret"}
    assert after["series_memory"]["relationship_state"] == {f"{KIWILO}|{MANGELLA}": "alliés en secret"}
    assert any(f"{BROCCOLIA}|{MANGELLA}" in line and "deleted" in line for line in log)


def test_the_season_approved_during_f1_is_kept(m, wf, store):
    story_id = _approved_memory_story(m, store)
    wf.store_feedback(store, story_id, 1, FEEDBACK, now=NOW)

    def unapproved_then_approved(reply):
        def call(_call):
            season = _season(store, story_id)
            season["approved_at"] = None
            store.write_doc(story_id, "season.json", season, now=LATER)
            wf.approve_season(store, story_id, now=LATEST)
            return reply
        return call

    _run(m.feedback, store, story_id, llm=eps.FakeLLM(F1=[unapproved_then_approved(F1_REPLY)]), step="feedback",
         ep=1)
    assert _season(store, story_id)["approved_at"] == LATEST
    assert _season(store, story_id)["audience_feedback"][0]["digest"] == F1_REPLY["digest"]


# ============================================================ registration

def test_the_series_steps_are_registered_and_end_awaiting_approval(wf):
    for step, module in (("memory", "memory"), ("feedback", "feedback"), ("propose-next", "propose_next")):
        assert step in steps.RUNNERS and steps.RUNNERS[step].__name__ == f"run_{module}"
        assert step not in steps.COMPLETED_STEPS and steps.ends_completed(step, {"ep": 1}) is False
    assert wf.SERIES_STEPS == ("memory", "feedback", "propose-next")
    assert wf.SERIES_APPROVALS == ("memory", "feedback", "proposals")
    assert wf.LATER_STEPS == ("rerender", "import")
    assert not set(wf.SERIES_STEPS) & set(wf.LATER_STEPS)
    assert _refused(wf, "later_phase", wf.refuse_step, "rerender") == "'rerender' arrives in a later phase."
    # The document each job awaits: the proposals sit in the folder of the episode they are for.
    assert [wf.series_job_doc(step, 3) for step in wf.SERIES_STEPS] == ["memory:3", "feedback:3", "proposals:4"]
    assert wf.series_job_doc("memory", None) is None


def test_each_series_step_is_one_llm_call_and_takes_no_parameters(m, wf, store):
    story_id = _approved_memory_story(m, store)
    wf.store_feedback(store, story_id, 1, FEEDBACK, now=NOW)
    story = store.get(story_id)
    for step, prompt in (("memory", "S3"), ("feedback", "F1"), ("propose-next", "N1")):
        ec = wf.series_context(store, story, 1, step=step)
        assert wf.series_units(ec, step) == {"llm_calls": 1, "prompt": prompt}
        assert wf.series_request(step, {}) == {}
        assert "takes no parameters" in _refused(wf, "invalid", wf.series_request, step, {"x": 1})
    assert "send its number as ep" in _refused(wf, "invalid", wf.series_context, store, story, None, step="memory")


def test_the_approve_grammar_takes_the_series_documents(m, wf, store):
    story_id = _written_ep1(store)
    _with_memory(m, store, story_id, approve=False)
    wf.store_feedback(store, story_id, 1, FEEDBACK, now=NOW)
    _run(m.feedback, store, story_id, llm=eps.FakeLLM(F1=[F1_REPLY]), step="feedback", ep=1)

    assert wf.approve_series(store, story_id, "memory:1", now=LATER)["approved_at"] == LATER
    assert wf.approve_series(store, story_id, "feedback:1", direction=2, now=LATER)["chosen_direction"] == 2
    assert "direction" in _refused(wf, "invalid", wf.approve_series, store, story_id, "feedback:1", now=LATER)
    assert "direction" in _refused(wf, "invalid", wf.approve_series, store, story_id, "memory:1", direction=1,
                                   now=LATER)
    assert _refused(wf, "invalid", wf.approve_series, store, story_id, "memory:x", now=LATER)
    assert _refused(wf, "not_found", wf.approve_series, store, story_id, "nope:1", now=LATER)
    assert wf.is_series_approval("proposals:2") and not wf.is_series_approval("script:2")


def test_the_series_view_of_an_episode(m, wf, store):
    story_id = _written_ep1(store)
    view = wf.series_view(store, store.get(story_id), 1)
    assert view == {"ep": 1, "memory": {"state": "none", "entry": None}, "feedback": None, "proposals": None}
    story_id, _llm = _proposed(m, store)

    view = wf.series_view(store, store.get(story_id), 1)
    assert view["memory"]["state"] == "approved" and view["memory"]["entry"]["script_rev"] == 1
    assert view["feedback"]["chosen_direction"] == 1
    assert view["proposals"] is None  # episode 1's own: nothing proposed for it
    assert wf.series_view(store, store.get(story_id), 2)["proposals"]["for_ep"] == 2

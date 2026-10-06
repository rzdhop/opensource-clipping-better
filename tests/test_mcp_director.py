"""The director (``mcp_server/director.py``): a story step runs in a thread
with the chat as its writer -- each ``call_json`` parks a prompt, the answer
handed back is the reply the validator checks, a refused reply is asked
again with the refusal, a step without prompts just runs, a cancel ends a
waiting run. The steps here are fakes that use the real ``call_json``; no
provider is contacted and no story is written.
"""

import threading
import time
import types

import pytest

from clipping.aistory import prompts
from clipping.aistory.steps import StepFailed, llm_call
from mcp_server import director as director_mod
from mcp_server.director import Director, DirectorError

B1 = "B1"
SCHEMA = prompts.SCHEMAS.get(B1) if hasattr(prompts, "SCHEMAS") else None


def fake_steps(calls):
    """A step module whose ``run`` asks the chat once per entry of *calls*:
    ``(validator, record)``; the accepted replies are returned in order."""

    def run(ctx, *, runner=None, **_):
        got = []
        for validator in calls:
            got.append(llm_call.call_json(ctx, B1, "SYSTEM", "USER", SCHEMA, validator=validator, runner=runner))
            ctx.on_log(f"accepted {got[-1]}")
        return {"replies": got}

    return types.SimpleNamespace(run=run)


def plain_step():
    def run(ctx, **_):
        ctx.on_log("rendering")
        return {"state": "completed"}

    return types.SimpleNamespace(run=run)


@pytest.fixture
def make_director(tmp_path):
    def build(module, *, answer_timeout=5.0):
        return Director(str(tmp_path), module_loader=lambda name: module, event_wait=5.0,
                        answer_timeout=answer_timeout)
    return build


def test_a_step_parks_its_prompt_and_takes_the_chats_answer(make_director):
    d = make_director(fake_steps([lambda v: []]))
    run = d.start("s1", "bible")
    assert run["state"] == "waiting"
    pending = run["pending"]
    assert pending["schema_name"] == prompts.SCHEMA_NAMES[B1]
    assert pending["system"] == "SYSTEM" and pending["user"] == "USER"
    assert pending["max_tokens"] >= prompts.MAX_TOKENS[B1] and pending["handle"].startswith(run["run_id"] + ":")
    done = d.answer(pending["handle"], {"logline": "a kiwi detective"})
    assert done["state"] == "done" and done["result"] == {"replies": [{"logline": "a kiwi detective"}]}
    assert any("asked the chat" in line for line in done["log_tail"])
    assert any("via chat/claude" in line for line in done["log_tail"]), done["log_tail"]


def test_a_refused_reply_is_asked_again_with_the_refusal_then_the_next_link_is_the_chat_too(make_director):
    seen = []

    def validator(value):
        seen.append(value)
        return [] if value.get("ok") else ["missing ok"]

    d = make_director(fake_steps([validator]))
    run = d.start("s1", "bible")
    second = d.answer(run["pending"]["handle"], {"ok": False})
    assert second["state"] == "waiting"
    assert second["pending"]["user"].startswith("USER")
    assert llm_call.REFUSED_PROMPT_HEAD in second["pending"]["user"] and "missing ok" in second["pending"]["user"]
    assert second["prompts_answered"] == 1
    done = d.answer(second["pending"]["handle"], {"ok": True})
    assert done["state"] == "done" and seen == [{"ok": False}, {"ok": True}]


def test_two_refusals_fail_the_step_as_the_chain_does(make_director):
    d = make_director(fake_steps([lambda v: ["no"]]))
    run = d.start("s1", "bible")
    second = d.answer(run["pending"]["handle"], {})
    failed = d.answer(second["pending"]["handle"], {})
    assert failed["state"] == "failed" and "ReplyRejected" in failed["error"] and "no" in failed["error"]


def test_a_step_without_prompts_runs_to_its_end(make_director):
    d = make_director(plain_step())
    run = d.start("s1", "render")
    assert run["state"] == "done" and run["result"] == {"state": "completed"} and "rendering" in run["log_tail"]
    assert "pending" not in run


def test_one_run_per_story_and_bad_answers_are_refused(make_director):
    d = make_director(fake_steps([lambda v: []]))
    run = d.start("s1", "bible")
    with pytest.raises(DirectorError, match="already has a bible run"):
        d.start("s1", "script")
    other = d.start("s2", "bible")  # another story is fine
    assert other["state"] == "waiting"
    with pytest.raises(DirectorError, match="JSON object"):
        d.answer(run["pending"]["handle"], "text")
    with pytest.raises(DirectorError, match="no prompt"):
        d.answer(run["run_id"] + ":99", {})
    with pytest.raises(DirectorError, match="unknown step"):
        d.start("s3", "nope")
    d.answer(run["pending"]["handle"], {"a": 1})
    with pytest.raises(DirectorError, match="already answered"):
        d.answer(run["pending"]["handle"], {"a": 2})
    assert [r["story_id"] for r in d.list()] == ["s2", "s1"]
    assert d.list("s1")[0]["state"] == "done"


def test_cancel_wakes_a_waiting_run_and_ends_it(make_director):
    d = make_director(fake_steps([lambda v: []]))
    run = d.start("s1", "bible")
    cancelled = d.cancel(run["run_id"])
    assert cancelled["state"] == "cancelled"
    assert d.cancel(run["run_id"])["state"] == "cancelled"  # idempotent
    assert d.start("s1", "bible")["state"] == "waiting"  # the story is free again


def test_an_unanswered_prompt_times_out_as_a_failed_step(make_director):
    d = make_director(fake_steps([lambda v: []]), answer_timeout=0.2)
    run = d.start("s1", "bible")
    time.sleep(0.4)
    status = d.status(run["run_id"])
    assert status["state"] == "failed" and "did not answer" in status["error"]


def test_status_waits_for_the_next_event_but_not_past_its_budget(make_director):
    gate = threading.Event()

    def run(ctx, *, runner=None, **_):
        gate.wait(5)
        return {"ok": True}

    d = make_director(types.SimpleNamespace(run=run))
    started = d.start("s1", "bible", wait=0.1)
    assert started["state"] == "running"
    gate.set()
    assert d.status(started["run_id"], wait=5)["state"] == "done"


def test_the_settings_name_the_chat_on_every_chain(make_director):
    seen = {}

    def run(ctx, *, runner=None, **_):
        seen.update(ctx.settings_env)
        return {}

    d = Director("/tmp/x", settings_env={"GROQ_API_KEY": "g", "LLM_CHAIN": "groq/x"},
                 module_loader=lambda name: types.SimpleNamespace(run=run), event_wait=5.0)
    d.start("s1", "bible")
    assert seen["LLM_CHAIN"] == seen["STORY_LLM_CHAIN"] == seen["STORY_LLM_PREMIUM_CHAIN"] == director_mod.CHAT_LINK
    assert seen["GROQ_API_KEY"] == "g"

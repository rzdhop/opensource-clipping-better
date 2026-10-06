"""The director: the story steps run with the chat as their writer.

A story step (``clipping/aistory/steps``) writes its document through
``llm_call.call_json``: it builds a prompt, sends it on the LLM chain, checks
the reply against the prompt's validator, asks again on a refusal. The chain
runner is injectable (``runner=``), and that is the whole trick here: the
step runs in a thread with a :class:`ChatRunner` that, instead of calling a
provider, hands the prompt to the conversation and waits for its answer.
Claude reads the prompt through ``story_step_*`` tools, answers it, and the
step carries on exactly as it would have with a hosted model -- the same
prompts, the same validators, the same documents, the same approvals. The
chain the context names is ``chat/claude`` (``registry.PROVIDERS["chat"]``),
a free link nothing else can call.

One run per story at a time. A run is ``running`` (the step works between
prompts, or makes images), ``waiting`` (a prompt is pending), ``done``,
``failed`` or ``cancelled``; its log is what the step printed.
"""

from __future__ import annotations

import importlib
import itertools
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from clipping.cancel import Cancelled, CancelToken
from clipping.providers import errors as provider_errors
from clipping.providers.registry import Link

CHAT_LINK = "chat/claude"
# What the step's settings say about the writer: every chain the story steps
# resolve names the chat, so nothing hosted is ever called from a run here.
CHAT_SETTINGS = {"STORY_LLM_CHAIN": CHAT_LINK, "STORY_LLM_PREMIUM_CHAIN": CHAT_LINK, "LLM_CHAIN": CHAT_LINK}
# How long a step waits for the chat's answer before it gives up (the chat's
# turn can take minutes; an hour means "the person went away").
ANSWER_TIMEOUT_SECONDS = 3600.0
# How long a tool call waits for the run to reach its next event (a pending
# prompt or the end) before answering "still running".
EVENT_WAIT_SECONDS = 25.0
LOG_TAIL = 30
STATES = ("running", "waiting", "done", "failed", "cancelled")
STEP_MODULES = {
    "concepts": "concepts", "bible": "bible", "regenerate": "regenerate", "style_preview": "style_preview",
    "cast": "cast", "places_proposal": "places_proposal", "places": "places", "season": "season",
    "knowledge": "knowledge", "script": "script", "storyboard": "storyboard", "assets": "assets",
    "render": "render", "metadata": "metadata", "memory": "memory", "feedback": "feedback",
    "propose-next": "propose_next", "rerender": "rerender",
}
# Steps whose runner takes no LLM runner (no prompt is ever pending).
NO_WRITER_STEPS = ("render", "rerender", "style_preview", "assets")


class DirectorError(Exception):
    pass


@dataclass
class Prompt:
    """One prompt a step is waiting on, as the chat sees it."""

    handle: str
    schema_name: str
    system: str
    user: str
    schema: Optional[dict]
    max_tokens: int
    temperature: float
    asked_at: float
    answer: Optional[dict] = None
    event: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        return {"handle": self.handle, "schema_name": self.schema_name, "system": self.system, "user": self.user,
                "schema": self.schema, "max_tokens": self.max_tokens, "temperature": self.temperature}


@dataclass
class StepRun:
    run_id: str
    story_id: str
    step: str
    ep: Optional[int]
    params: dict
    state: str = "running"
    log: list = field(default_factory=list)
    prompts: list = field(default_factory=list)
    pending: Optional[Prompt] = None
    result: object = None
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    token: CancelToken = field(default_factory=CancelToken)
    changed: threading.Condition = field(default_factory=threading.Condition)
    thread: Optional[threading.Thread] = None

    def public(self, *, log_tail: int = LOG_TAIL) -> dict:
        out = {"run_id": self.run_id, "story_id": self.story_id, "step": self.step, "ep": self.ep,
               "state": self.state, "prompts_answered": sum(1 for p in self.prompts if p.answer is not None),
               "log_tail": self.log[-log_tail:], "started_at": self.started_at, "finished_at": self.finished_at}
        if self.pending is not None and self.state == "waiting":
            out["pending"] = self.pending.public()
        if self.state == "done":
            out["result"] = _jsonable(self.result)
        if self.error:
            out["error"] = self.error
        return out


def _jsonable(value):
    """A step's result as plain JSON (results are dicts of plain values; a
    stray object is shown as text)."""
    try:
        import json

        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


class ChatRunner:
    """``llm.run_chain``'s stand-in for one run: each call parks a
    :class:`Prompt` on the run and blocks until the chat answers it."""

    def __init__(self, run: StepRun, *, answer_timeout: float = ANSWER_TIMEOUT_SECONDS):
        self.run = run
        self.answer_timeout = answer_timeout
        self._counter = itertools.count(1)

    def __call__(self, chain, *, system, user, schema=None, schema_name="result", max_tokens=1024,
                 temperature=0.2, keys=None, on_log=print, deadline=None, time_fn=time.monotonic, cancel=None,
                 **_ignored):
        run = self.run
        link = next((l for l in chain if l.provider == "chat"), None) or (chain[0] if chain else Link("chat", "claude"))
        prompt = Prompt(handle=f"{run.run_id}:{next(self._counter)}", schema_name=schema_name, system=system,
                        user=user, schema=schema, max_tokens=int(max_tokens), temperature=float(temperature),
                        asked_at=time.time())
        with run.changed:
            run.prompts.append(prompt)
            run.pending = prompt
            run.state = "waiting"
            run.changed.notify_all()
        on_log(f"   💬 {schema_name}: asked the chat (prompt {prompt.handle})")
        answered = prompt.event.wait(self.answer_timeout)
        with run.changed:
            run.pending = None
            if run.state == "waiting":
                run.state = "running"
            run.changed.notify_all()
        if cancel is not None:
            cancel.check()
        if run.token.cancelled:
            raise Cancelled()
        if not answered or prompt.answer is None:
            raise provider_errors.ProviderError(f"{schema_name}: the chat did not answer prompt {prompt.handle} "
                                                f"within {self.answer_timeout:.0f} s")
        return prompt.answer, link


class Director:
    """Runs steps on the store, one per story at a time, with the chat as writer."""

    def __init__(self, outputs_dir: str, *, settings_env: Optional[dict] = None, event_wait: float = EVENT_WAIT_SECONDS,
                 answer_timeout: float = ANSWER_TIMEOUT_SECONDS, module_loader=None):
        self.outputs_dir = outputs_dir
        self.settings_env = dict(settings_env or {})
        self.event_wait = event_wait
        self.answer_timeout = answer_timeout
        self.runs: dict = {}
        self._lock = threading.Lock()
        self._module_loader = module_loader or (lambda name: importlib.import_module(f"clipping.aistory.steps.{name}"))

    # -- lookups

    def get(self, run_id: str) -> StepRun:
        run = self.runs.get(run_id)
        if run is None:
            raise DirectorError(f"no run {run_id!r}")
        return run

    def active_for(self, story_id: str):
        for run in self.runs.values():
            if run.story_id == story_id and run.state in ("running", "waiting"):
                return run
        return None

    def by_prompt(self, handle: str):
        run_id = handle.rsplit(":", 1)[0]
        run = self.get(run_id)
        for prompt in run.prompts:
            if prompt.handle == handle:
                return run, prompt
        raise DirectorError(f"no prompt {handle!r} on run {run_id}")

    def list(self, story_id: Optional[str] = None) -> list:
        runs = [r for r in self.runs.values() if story_id is None or r.story_id == story_id]
        return [r.public(log_tail=3) for r in sorted(runs, key=lambda r: r.started_at, reverse=True)]

    # -- running

    def start(self, story_id: str, step: str, *, ep: Optional[int] = None, params: Optional[dict] = None,
              wait: Optional[float] = None) -> dict:
        """Start *step* on the story in a thread; the run after its first
        event (a pending prompt, or the end), or as it is after *wait* s."""
        if step not in STEP_MODULES:
            raise DirectorError(f"unknown step {step!r}; one of {', '.join(STEP_MODULES)}")
        with self._lock:
            busy = self.active_for(story_id)
            if busy is not None:
                raise DirectorError(f"story {story_id} already has a {busy.step} run ({busy.run_id}, {busy.state}); "
                                    "answer or cancel it first")
            run = StepRun(run_id=uuid.uuid4().hex[:10], story_id=story_id, step=step, ep=ep, params=dict(params or {}))
            self.runs[run.run_id] = run
        thread = threading.Thread(target=self._work, args=(run,), name=f"step-{run.run_id}", daemon=True)
        run.thread = thread
        thread.start()
        return self.wait_event(run, wait)

    def _work(self, run: StepRun) -> None:
        from clipping.aistory import steps as steps_pkg

        def on_log(line):
            with run.changed:
                run.log.append(str(line))

        ctx = steps_pkg.StepContext(job_id=f"mcp-{run.run_id}", story_id=run.story_id, step=run.step, ep=run.ep,
                                    params=run.params, cancel=run.token,
                                    settings_env={**self.settings_env, **CHAT_SETTINGS},
                                    outputs_dir=self.outputs_dir, on_log=on_log)
        try:
            module = self._module_loader(STEP_MODULES[run.step])
            if run.step in NO_WRITER_STEPS:
                result = module.run(ctx)
            else:
                result = module.run(ctx, runner=ChatRunner(run, answer_timeout=self.answer_timeout))
            with run.changed:
                run.result = result
                run.state = "done"
        except Cancelled:
            with run.changed:
                run.state = "cancelled"
                run.error = "cancelled"
        except BaseException as exc:  # noqa: BLE001 - the run records any failure
            with run.changed:
                run.state = "failed"
                run.error = f"{type(exc).__name__}: {exc}"
        finally:
            with run.changed:
                run.finished_at = time.time()
                run.pending = None
                run.changed.notify_all()

    def wait_event(self, run: StepRun, wait: Optional[float] = None, *, through=("running",)) -> dict:
        """The run once it waits on a prompt or has ended, or as it is after
        *wait* seconds (``self.event_wait`` by default). *through* names the
        states waited through (a cancel also waits through ``waiting``)."""
        deadline = time.monotonic() + (self.event_wait if wait is None else max(0.0, float(wait)))
        with run.changed:
            while run.state in through:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                run.changed.wait(remaining)
            return run.public()

    def answer(self, handle: str, answer: dict, *, wait: Optional[float] = None) -> dict:
        """Hand the chat's *answer* to the prompt *handle* and follow the run
        to its next event."""
        run, prompt = self.by_prompt(handle)
        if not isinstance(answer, dict):
            raise DirectorError("the answer must be a JSON object (the prompt's schema)")
        if prompt.answer is not None:
            raise DirectorError(f"prompt {handle} was already answered")
        if run.pending is not prompt or run.state != "waiting":
            raise DirectorError(f"prompt {handle} is not pending (run {run.run_id} is {run.state})")
        with run.changed:
            prompt.answer = answer
            run.state = "running"
            prompt.event.set()
            run.changed.notify_all()
        return self.wait_event(run, wait)

    def status(self, run_id: str, *, wait: Optional[float] = 0.0) -> dict:
        return self.wait_event(self.get(run_id), wait)

    def cancel(self, run_id: str) -> dict:
        run = self.get(run_id)
        if run.state in ("done", "failed", "cancelled"):
            return run.public()
        run.token.cancel()
        with run.changed:
            pending = run.pending
            if pending is not None:
                pending.event.set()  # wake the runner; it raises Cancelled
            run.changed.notify_all()
        return self.wait_event(run, 5.0, through=("running", "waiting"))

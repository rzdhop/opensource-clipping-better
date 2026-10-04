"""AI Story step runners, and the registry the web worker runs them from.

A story step -- write the bible, generate ten concepts -- runs as an ordinary
job of kind ``story_step`` (spec 9.1): the worker builds a :class:`StepContext`
and calls :func:`run`, which looks the step up in :data:`RUNNERS`. A runner
prints its progress (``ctx.on_log``) exactly as the clip pipeline does, so the
worker's stdout tee files every line against the job; it checks
``ctx.cancel`` between the calls that spend; it writes what it produced into
the story's folder under ``ctx.outputs_dir``. Returning means "ready for the
user's approval" (or, for a step :func:`ends_completed` names, "done");
raising means the step failed.

Phase 1 registers ``concepts``, ``bible`` and ``regenerate`` (stage 6) and
``style_preview`` (stage 8); phase 2 adds ``cast``, ``places_proposal``,
``places`` and ``season`` (and the entity targets of ``regenerate``); phase 3
adds ``script`` and ``storyboard`` (and the episode targets); phase 4 adds
``assets`` (and the image and voice targets of ``regenerate``), ``render``,
``metadata`` (and the metadata target of ``regenerate``) and ``fast-track``
(module ``fast_track``); phase 5 adds the series steps ``memory``,
``feedback`` and ``propose-next`` (module ``propose_next``), each one LLM
call ending awaiting approval, and ``rerender`` (stage 8: the render again,
making only the shot clips that changed since the last good render); phase 7
(stage 5b) adds ``knowledge``, a v2 story's knowledge base, ending awaiting
approval like the season; plan 21 (agent mode) adds ``story-fast-track``
(module ``story_fast_track``): an agent-mode story from its seed to episode
1 in one job, ending completed. Each is
registered by module name and imported on its first run, never here:
importing this package must not pull in the prompt catalogue, the LLM chain
or the generation chains, so the worker's dispatch and a test that only
needs the registry stay as light as they were.

How a step's job ends is :func:`ends_completed`'s answer (DEC-161, amending
DEC-108): ``render``, ``metadata``, ``fast-track``, ``rerender``,
``story-fast-track`` (plan 21) and a regenerate of
``metadata:<ep>:<platform>`` leave nothing to approve and end
``completed``; every other step ends ``awaiting_approval``, as it always did.

A runner that fails in a way the user can act on raises :class:`StepFailed`
with a sentence saying what to do; the worker records it as
``StepFailed: <sentence>``.

Stdlib only: importable in the pytest-only CI environment (DEC-012).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class StepContext:
    """Everything a runner is given. It never reads the job store itself."""

    job_id: str
    story_id: str
    step: str
    ep: Optional[int]
    params: dict
    # clipping.cancel.CancelToken: .check() raises Cancelled once the job is
    # cancelled, .cancelled says whether it has been.
    cancel: object
    # A copy of the worker's live Settings values (keys, LLM_CHAIN, ...).
    settings_env: dict
    # The outputs/ directory; the story lives in outputs/stories/<story_id>/.
    outputs_dir: str
    on_log: Callable[[str], None] = print
    # Plan 21 stage 1: a runner that chains parts (``story-fast-track``) says
    # which one it is on; the worker records it as the job's ``sub_step``.
    # None (the default, and every other caller): nobody is told.
    on_sub_step: Optional[Callable[[Optional[str]], None]] = None


class UnknownStep(KeyError):
    """No runner is registered under that step name."""

    def __init__(self, step, known):
        self.step = step
        self.known = tuple(known)
        super().__init__(step)

    def __str__(self):
        # KeyError's own str() is the repr of its argument; this is a sentence.
        known = ", ".join(self.known) or "none registered"
        return f"unknown story step {self.step!r} (known: {known})"


class StepFailed(RuntimeError):
    """A step could not produce what it was asked for; the message says why
    and, where there is one, what to do next.

    ``reason`` is the same explanation without the prompt id prefix, for a
    runner that folds several failures into one sentence of its own.
    """

    def __init__(self, message, *, reason=None):
        super().__init__(message)
        self.reason = message if reason is None else reason


def _deferred(module_name: str) -> Callable[[StepContext], object]:
    """A runner that imports ``steps.<module_name>`` on first use and calls
    its ``run(ctx)``. Import errors surface when the step runs, as a failed
    job naming them, not when the worker starts."""

    def runner(ctx: StepContext):
        module = importlib.import_module(f"{__name__}.{module_name}")
        return module.run(ctx)

    runner.__name__ = runner.__qualname__ = f"run_{module_name}"
    runner.__doc__ = f"{__name__}.{module_name}.run, imported on first use."
    return runner


RUNNERS: dict[str, Callable[[StepContext], object]] = {
    "concepts": _deferred("concepts"),
    "bible": _deferred("bible"),
    "regenerate": _deferred("regenerate"),
    "style_preview": _deferred("style_preview"),
    "cast": _deferred("cast"),
    "places_proposal": _deferred("places_proposal"),
    "places": _deferred("places"),
    "season": _deferred("season"),
    "knowledge": _deferred("knowledge"),
    "script": _deferred("script"),
    "storyboard": _deferred("storyboard"),
    "assets": _deferred("assets"),
    "render": _deferred("render"),
    "metadata": _deferred("metadata"),
    "fast-track": _deferred("fast_track"),
    "memory": _deferred("memory"),
    "feedback": _deferred("feedback"),
    "propose-next": _deferred("propose_next"),
    "rerender": _deferred("rerender"),
    "story-fast-track": _deferred("story_fast_track"),
}

# DEC-161: the steps with nothing to approve, whose job ends ``completed``;
# and the first word of the regenerate targets that end so too
# (``metadata:<ep>:<platform>``). Every other step ends awaiting approval.
COMPLETED_STEPS = ("render", "metadata", "fast-track", "rerender", "story-fast-track")
COMPLETED_TARGET_KINDS = ("metadata",)


# Plan 22 stage 5 (the manual link): a step that waits for the human's own
# clips ends ``awaiting_uploads`` instead -- the assets step on a story whose
# clips are on ``manual/upload``, and the fast track or the agent run paused
# there. Its runner returns ``{"state": AWAITING_UPLOADS, "uploads": {count,
# missing, message, brief}, ...}`` (:func:`awaiting_uploads`); the worker ends
# the job so, and an upload that leaves nothing missing starts it again.
AWAITING_UPLOADS = "awaiting_uploads"


class AwaitingUploads(Exception):
    """Raised inside a chained runner (the fast track) when its assets step
    waits for the human's clips; carries the step's ``uploads`` record. The
    chain turns it into its own paused result, never a failure."""

    def __init__(self, uploads):
        self.uploads = dict(uploads or {})
        super().__init__(self.uploads.get("message") or "waiting for your clips")


def awaiting_uploads(result) -> bool:
    """Whether a runner's *result* says it waits for the human's uploads."""
    return isinstance(result, dict) and result.get("state") == AWAITING_UPLOADS


def ends_completed(step, params=None) -> bool:
    """Whether a job of *step* (with its *params*) ends ``completed`` rather
    than ``awaiting_approval`` once its runner returns: :data:`COMPLETED_STEPS`,
    and a ``regenerate`` whose ``params["target"]`` is
    ``<kind>:<ep>:<platform>`` with *kind* in :data:`COMPLETED_TARGET_KINDS`.
    The shape only: a malformed target fails in its runner long before the
    job's end is decided."""
    if step in COMPLETED_STEPS:
        return True
    if step != "regenerate" or not isinstance(params, dict):
        return False
    target = params.get("target")
    if not isinstance(target, str):
        return False
    parts = target.split(":")
    return len(parts) == 3 and parts[0] in COMPLETED_TARGET_KINDS and all(parts)


def run(step: str, ctx: StepContext):
    """Run the runner registered for *step* and return what it returns.

    UnknownStep, naming the known steps, when none is registered; Cancelled,
    before anything runs, when the job was cancelled while it waited.
    """
    runner = RUNNERS.get(step)
    if runner is None:
        raise UnknownStep(step, sorted(RUNNERS))
    ctx.cancel.check()
    return runner(ctx)

"""AI Story step runners, and the registry the web worker runs them from.

A story step -- write the bible, generate ten concepts -- runs as an ordinary
job of kind ``story_step`` (spec 9.1): the worker builds a :class:`StepContext`
and calls :func:`run`, which looks the step up in :data:`RUNNERS`. A runner
prints its progress (``ctx.on_log``) exactly as the clip pipeline does, so the
worker's stdout tee files every line against the job; it checks
``ctx.cancel`` between the calls that spend; it writes what it produced into
the story's folder under ``ctx.outputs_dir``. Returning means "ready for the
user's approval"; raising means the step failed.

The registry ships empty. Runners register here as they arrive (phase 1,
stage 6: concepts, bible, regenerate, style_preview).

Stdlib only: importable in the pytest-only CI environment (DEC-012).
"""

from __future__ import annotations

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


RUNNERS: dict[str, Callable[[StepContext], object]] = {}


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

"""Step ``rerender``: an episode rendered again from its documents as they are
now, making again only the shot clips that changed since its last good render
(AI Story phase 5, stage 8; plan 11 "Stage 8 -- partial re-render"; spec 6.6;
DEC-156, DEC-161, DEC-164).

``ctx.ep`` is the episode. It takes no params: a re-render keeps the last good
render's own subtitles and encoder (switching the subtitles is a render,
DEC-164, and re-runs no shot either).

**Preconditions**, each refused before any process, saying what to do: a
finished render of the episode -- its last good render
(``render_manifest.last_good.json``, or a ``render_manifest.json`` recording
an output, for an episode rendered before this stage;
``render.last_render``) and the ``episode_final.mp4`` it made -- then every
precondition of the render itself (``render.require_renderable``: the script,
the storyboard and the assets approved and current, every image and voice on
disk, no shot image out of date since its shot was edited unless it is locked
-- stage 7).

**The render** is the render step's own (``render.render_episode``): the plan
from the documents as they are, the runner taking from its cache every clip
whose bytes are the ones recorded (``render/partial.py``); the manifest's
``reuse`` says what was made again and why, relative to the last good render,
and the render that completes becomes the next one's baseline. The feed and
the summary say "3 of 11 shots re-rendered" (and why it was not partial when
the storyboard's timing converted to whole frames, stage 6). A dry run of
the same count, starting nothing, is ``render.render_changes``.

Ends ``completed`` (``steps.COMPLETED_STEPS``, DEC-161): nothing to approve.
The metadata pack is not refreshed: it goes stale through its own
``render_sha256`` check (``metadata.is_current``). ``story.json`` is never
written (RC-E2).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import subprocess
import time

from .. import schemas
from . import episode_common
from . import render as render_step
from .llm_call import StepFailed

STEP = "rerender"


def require_finished_render(ec) -> dict:
    """The episode's last good render's manifest, when there is one and the
    episode's ``episode_final.mp4`` is the file it made; else ``StepFailed``
    saying what to do."""
    ep = ec.ep
    known = render_step.last_render(ec)
    baseline = known["baseline"]
    if baseline is None:
        raise StepFailed(f"Episode {ep} has no finished render to re-render: render it first (the render step); "
                         "a re-render makes again only what changed since the last good render.")
    final = known["final"]
    if final is None or render_step.assets_step._sha256_file(final) != baseline["output"]["sha256"]:
        raise StepFailed(f"Episode {ep}'s {render_step.FINAL_FILE} is not the one its last good render made (it "
                         "was changed or removed): render it again (the render step).")
    return baseline


def run(ctx, *, profile="final", run_process=subprocess.run, popen=subprocess.Popen, clock=time.monotonic,
        detect=None, custom_fonts_dir=None) -> dict:
    """The step (module docstring). Returns the render step's summary
    (``render.summary_of``) with ``step`` ``rerender`` and ``reuse`` -- the
    manifest's record and its ``summary`` sentence. The keyword arguments
    are the render step's own seams (``render.run``)."""
    if profile not in schemas.RENDER_PROFILES:
        raise StepFailed(f"Unknown render profile {profile!r} (one of {', '.join(schemas.RENDER_PROFILES)}).")
    ec = episode_common.load_episode_context(ctx)
    # Made from an approved script and storyboard, which met the memory gate
    # when they were written: a re-render never meets it (plan 11 stage 4).
    episode_common.check_episode_preconditions(ctx, ec, require_memory=False)
    extra = sorted(ctx.params or {})
    if extra:
        raise StepFailed(f"A re-render keeps the last render's subtitles and encoder and takes no parameters (got "
                         f"{', '.join(extra)}); to change them, render the episode (the render step).")
    baseline = require_finished_render(ec)
    return render_step.render_episode(ctx, ec, render_step.rerender_params(baseline), step=STEP, profile=profile,
                                      run_process=run_process, popen=popen, clock=clock, detect=detect,
                                      custom_fonts_dir=custom_fonts_dir)

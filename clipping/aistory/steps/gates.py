"""The hard length gate of a v2 story (AI Story phase 7, stage 6a; A17,
DEC-231).

A v2 episode is approved and rendered only inside its template's length
window (``window_s``: 55-75 s for ``serial_60s_v2``), with no "approve
anyway": story B shipped 9.9 s short under a warning. :func:`length_refusal`
is the one check, called with one line each by:

- ``workflow.approve_script`` and ``workflow.approve_storyboard`` -- the
  **estimated** length (the lines' estimates, or their measurements when
  they have some);
- ``workflow.approve_assets`` and the render (``render.require_renderable``)
  -- the **measured** length: every line is voiced by then.

Either way the length is the one timing the render cuts the episode to
(``timing.episode_pass`` with the storyboard, in whole frames when it is
timed in them), so the gate and the render agree. A legacy story is never
refused here: its render keeps today's warning (``render/runner.
output_warnings``).

Stdlib only (DEC-012).
"""

from __future__ import annotations

from .. import media_policy, timing
from .llm_call import StepFailed

STAGES = ("script", "storyboard", "assets", "render")

# Seconds of slack against a float that rounds onto the window's edge.
_EPSILON = 1e-6

_LENGTHEN = {
    "script": ("run the script step again (its fill pass rewrites the shortest scenes), or regenerate or edit the "
               "shortest scenes with more lines, then approve"),
    "storyboard": ("lengthen its script (regenerate or edit its shortest scenes), approve it, plan the shots again "
                   "(the storyboard step), then approve"),
    "assets": ("lengthen its script (regenerate or edit its shortest scenes), approve it and its storyboard again, "
               "make the assets again (the assets step), then approve"),
    "render": ("lengthen its script (regenerate or edit its shortest scenes), approve it and its storyboard again, "
               "make and approve the assets again, then render"),
}
_SHORTEN = {
    "script": ("edit or regenerate its longest scenes (the timing flags name the lines to trim), then approve"),
    "storyboard": ("shorten its script (edit or regenerate its longest scenes), approve it, plan the shots again "
                   "(the storyboard step), then approve"),
    "assets": ("shorten its script (edit or regenerate its longest scenes), approve it and its storyboard again, "
               "make the assets again (the assets step), then approve"),
    "render": ("shorten its script (edit or regenerate its longest scenes), approve it and its storyboard again, "
               "make and approve the assets again, then render"),
}


def episode_length(ec, script, board=None) -> dict:
    """The episode's ``timing`` as the render cuts it: ``timing.
    episode_pass`` over *script* with *board* (None: no storyboard yet), in
    whole frames when the board is timed in them -- what
    ``episode_common.retime`` stores and ``render/timeline.build_timeline``
    renders. A native-speech story with no board yet is timed on its
    stored plans' clips (plan 28 stage A3: one clock with its storyboard)."""
    result, _scenes = timing.episode_pass(script, ec.template, ec.language, style_lock=ec.style_lock,
                                          storyboard=board, whole_frames=timing.board_whole_frames(board),
                                          native_plan=media_policy.native_speech(ec.story))
    return result


def how_measured(result) -> str:
    """``measured`` | ``partly measured`` | ``estimated``
    (``episode_common.timing_line``'s words)."""
    measured, estimated = result["measured_lines"], result["estimated_lines"]
    if measured and not estimated:
        return "measured"
    return "partly measured" if measured else "estimated"


def length_refusal(ec, script, board=None, *, stage):
    """Why a v2 episode may not pass *stage* (one of :data:`STAGES`) for its
    length, or None: a legacy story, or a length inside the template's
    window. The sentence states the length, how it was measured, by how
    much it misses the window, and what to do."""
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {', '.join(STAGES)}, not {stage!r}")
    if not media_policy.is_v2(ec.story) or not script or not script.get("scenes"):
        return None
    result = episode_length(ec, script, board)
    lo, hi = (float(edge) for edge in ec.template["window_s"])
    total = float(result["total_s"])
    if lo - _EPSILON <= total <= hi + _EPSILON:
        return None
    under = total < lo
    gap = (lo - total) if under else (total - hi)
    ep = ec.ep
    where = f"{gap:.1f} s {'under' if under else 'over'} its {lo:g}–{hi:g} s window"
    how = how_measured(result)
    fix = (_LENGTHEN if under else _SHORTEN)[stage]
    verb = "lengthen" if under else "shorten"
    if stage == "script":
        return (f"Episode {ec.ep}'s script runs {total:.1f} s ({how}), {where}: a v2 episode is approved only "
                f"inside it, never anyway. To {verb} it, {fix}.")
    if stage == "storyboard":
        return (f"Episode {ep} runs {total:.1f} s with its storyboard ({how}), {where}: a v2 storyboard is "
                f"approved only inside it, never anyway. To {verb} the episode, {fix}.")
    if stage == "assets":
        return (f"Episode {ep} runs {total:.1f} s with its voices ({how}), {where}: v2 assets are approved only "
                f"inside it, never anyway. To {verb} the episode, {fix}.")
    return (f"Episode {ep} cannot be rendered: it runs {total:.1f} s with its voices ({how}), {where}, and a v2 "
            f"episode is rendered only inside it. To {verb} it, {fix}.")


def require_length(ec, script, board=None, *, stage) -> None:
    """:func:`length_refusal` as a ``StepFailed`` (the render's
    precondition)."""
    refusal = length_refusal(ec, script, board, stage=stage)
    if refusal is not None:
        raise StepFailed(refusal)

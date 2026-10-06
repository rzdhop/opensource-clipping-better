"""The ``style`` step for the director (plan 32 stage 1).

The web API and the CLI build a story's style lock synchronously
(``workflow.build_style``: the template read, the overrides applied, the
draft written -- no LLM call, no image); the director runs it as a step so
the chat drives it like the others (``story_step_start(step="style")``)
and the approval that follows is ``story_approve(doc="style")``. It takes
no writer: it is one of ``director.NO_WRITER_STEPS``.

``params`` are ``workflow.STYLE_PARAMS``: ``template_id`` (default: the
story's own style, else its concept's), ``overrides`` (dotted paths of the
style lock, ``stylelock.OVERRIDABLE``), ``consistency_mode``. A refusal
(the bible not approved, the style already locked, an unknown template or
override) fails the run with the workflow's own sentence.
"""

from __future__ import annotations

from clipping.aistory.steps import llm_call


def run(ctx) -> dict:
    from clipping.aistory import workflow

    store, _story = llm_call.open_story(ctx)
    try:
        result = workflow.build_style(store, ctx.story_id, dict(ctx.params or {}), now=llm_call.utc_now())
    except workflow.WorkflowError as exc:
        raise llm_call.StepFailed(str(exc)) from None
    lock = result["style_lock"]
    overrides = lock.get("overrides") or {}
    ctx.on_log(f"🎨 Style draft: {lock['template_id']} v{lock['template_version']}"
               + (f" ({len(overrides)} change{'s' if len(overrides) != 1 else ''})" if overrides else "")
               + ". Approve it with story_approve(doc='style').")
    return {"template_id": lock["template_id"], "template_version": lock["template_version"],
            "overrides": overrides, "status": result["story"]["status"],
            "next": "Look at the style (story_doc style_lock.json), then approve it: story_approve(doc='style')."}

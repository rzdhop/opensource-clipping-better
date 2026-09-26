"""Step ``style_preview``: three small sample images of the story's style (spec 3 step 4).

The optional preview strip of the style step. Three style-generic prompts -- a
place, a character, a two-shot -- are built by ``prompting.py`` from the
story's style lock (draft or locked) and sent through ``IMAGE_CHAIN`` with
every gate the runner has (DEC-096/097/106): the story's
``generation_profile.route``, ``allow_paid`` and the caps of the budget (a
paid link is tried once), and the free-tier limiter. On a deployment whose
chain reaches a keyless free link, the strip costs $0.00.

The prompts preview the rendering, the palette and the lighting, not the
story: they are English (DEC-064) and name nothing -- no character, no place,
no line of the story, which may be French. The seed of each sample is derived
from the story id and the sample number, so a re-run sends the same request
wherever the provider honours seeds.

Each sample is its own chain run, so a failure is local (DEC-027): a sample no
link could make is printed with the chain's reasons and recorded in
``style_preview.json``; the step fails only when no image at all was made.
Returning means "ready for approval" -- approving the style completes the job.

Every answered call is booked in the story's ``cost_ledger.json`` (a free or
local one at $0.00), and a paid one is added to today's spend
(``budget.record``) exactly as the Settings chain test books its paid call.
``run_generation_chain`` books nothing itself, so that is the only booking.
The ledger feeds the per-story cap of the next sample's budget check, and a
ledger that cannot be read stops the step before anything is spent.

A new preview replaces the previous one: the old ``preview_*`` files go first,
and only those, only inside ``styles/preview/`` (``StoryStore.clear_previews``).

``estimate`` answers the same question without calling anything, for
``GET /api/stories/{id}/estimate/style_preview`` and the job's gate.

The wiring this step shares with the reference images of phase 2 (the
estimate, the one-image run, the booking, the copy) lives in ``imaging.py``,
lifted from here unchanged in behaviour.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import hashlib
import tempfile
import time

from clipping.providers import adapters as adapters_mod
from clipping.providers import gating
from clipping.providers import generation as gen

from .. import imaging
from .. import ledger as ledger_mod
from .. import prompting, schemas
from . import llm_call
from .llm_call import StepFailed

STEP = "style_preview"
DOC_NAME = "style_preview.json"
LOCK_NAME = imaging.LOCK_NAME
LEDGER_NAME = imaging.LEDGER_NAME
KIND = gen.IMAGE

# "Tiny" and vertical (9:16): enough to judge a palette and a light, cheap
# where a price scales with the size.
WIDTH, HEIGHT = 576, 1024
KEPT_EXTENSIONS = imaging.KEPT_EXTENSIONS

# One entry per sample: the ``prompting`` builder and its arguments. Constants
# only -- nothing from the story reaches a preview prompt.
PREVIEW_SAMPLES = (
    ("master_plate_prompt", {
        "place_descriptor": "a typical main location for this series",
        "time_variant": "day",
    }),
    ("portrait_prompt", {
        "descriptor": "an original sample character designed for this style",
        "signature_items": ("one signature accessory",),
    }),
    ("shot_prompt", {
        "subjects_block": "two original sample characters",
        "action": "one leans in to share a secret",
        "place_block": "a set typical of this style",
        "time_variant": "golden hour",
        "framing": "medium_two_shot",
    }),
)
SAMPLES = len(PREVIEW_SAMPLES)


# ---------------------------------------------------------------- prompts

def sample_prompts(lock) -> list:
    """The preview's prompts for *lock*, in sample order."""
    return [getattr(prompting, builder)(lock, **kwargs) for builder, kwargs in PREVIEW_SAMPLES]


def sample_seed(story_id, n) -> int:
    """The seed of sample *n* of *story_id*: the same on every run and in every
    process (not ``hash()``, which is salted per process), 1 .. 2**31-2."""
    digest = hashlib.sha256(f"{STEP}:{story_id}:{n}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**31 - 2) + 1


def _request(prompt="", *, negative="", seed=None, out_dir="", n=None) -> gen.GenRequest:
    return gen.GenRequest(
        kind=KIND, prompt=prompt, negative=negative, width=WIDTH, height=HEIGHT, seed=seed,
        out_dir=out_dir, extra={"name": f"preview_{n}"} if n is not None else {},
    )


# --------------------------------------------------------------- estimate

def estimate(settings_env, *, route, story_spent=0.0, adapters=None) -> dict:
    """What the preview would cost and where it would run; nothing is called::

        {"step": "style_preview", "est_usd", "units": {"images": 3},
         "route_class": "local" | "free" | "paid" | "blocked",
         "link": <the first runnable link> | null,
         "links": [{"link", "status": "runnable" | "skipped", "reason", "paid", "est_usd"}],
         "ready": bool, "message": str}

    A link is ``runnable`` when the runner would try it: allowed by *route*,
    with an adapter and its keys; a local link is then runnable (its server is
    probed when the step runs, never here); a paid link needs ``allow_paid``
    and the budget's verdict on the whole strip (three images, with
    *story_spent* already on the story); a free link needs its daily
    allowance. ``est_usd`` is three times the first runnable link's
    per-image estimate -- 0.0 when it is free or local -- and each row's
    ``est_usd`` is the same for that link. ``blocked``: no link can run;
    ``message`` then names every link's reason (``imaging.estimate``).
    """
    return imaging.estimate(KIND, settings_env, route=route, request=_request(), qty=SAMPLES,
                            story_spent=story_spent, adapters=adapters, step=STEP,
                            what="the preview", when="the preview runs")


# ------------------------------------------------------------------ helpers

def _open_ledger(store, story_id) -> ledger_mod.CostLedger:
    """The story's ledger, checked before anything is spent: its total feeds
    the per-story cap, and ``CostLedger`` would start a torn file afresh on
    the first append -- losing what the story already spent
    (``imaging.open_ledger``)."""
    return imaging.open_ledger(store, story_id, error=StepFailed, doing="making a preview")


def _save(store, story_id, doc) -> None:
    now = llm_call.utc_now()
    doc["updated_at"] = now
    store.write_doc(story_id, DOC_NAME, doc, now=now, validator=schemas.style_preview_errors)


# ---------------------------------------------------------------------- run

def run(ctx, *, transport=None, adapters=None, sleep_fn=time.sleep, time_fn=time.monotonic) -> dict:
    """Make the preview strip of the story's style lock.

    *transport*, *adapters*, *sleep_fn* and *time_fn* are handed to
    ``run_generation_chain`` (tests; the worker passes none). Returns
    ``{"images", "failed", "est_usd"}``; raises ``StepFailed`` before anything
    is spent (no style lock, a chain or budget setting that cannot be used, an
    unreadable ledger, a preview folder that is not a real folder) or when no
    image was made, naming every reason; ``Cancelled`` between samples.
    """
    store, story = llm_call.open_story(ctx)
    lock = imaging.read_lock(store, ctx.story_id, error=StepFailed)
    route = story["generation_profile"]["route"]

    merged, chain, budget_obj = imaging.resolve(KIND, ctx.settings_env, error=StepFailed)
    if adapters is None:
        adapters_mod.load_all()

    ledger = _open_ledger(store, ctx.story_id)
    try:
        folder = store.preview_dir(ctx.story_id, create=True)
    except KeyError:
        raise StepFailed("The story's styles/preview folder is not a real folder inside the story "
                         "(a symlink is never followed); remove it, then make the preview again.") from None

    removed = store.clear_previews(ctx.story_id)
    prompts = sample_prompts(lock)
    negative = prompting.negative_prompt(lock)
    doc = {
        "$schema": schemas.STYLE_PREVIEW_SCHEMA_NAME,
        "template_id": lock["template_id"],
        "template_version": lock["template_version"],
        "overrides": copy.deepcopy(lock.get("overrides") or {}),
        "images": [],
        "failed": [],
        "updated_at": llm_call.utc_now(),
    }
    _save(store, ctx.story_id, doc)
    ctx.on_log(f"🖼 Style preview: {SAMPLES} samples of {lock['template_id']} at {WIDTH}x{HEIGHT}, "
               f"route {route}.")
    if removed:
        ctx.on_log(f"🧹 Removed the previous preview: {removed} file(s).")

    check = gating.budget_check(budget_obj, story_spent=lambda: ledger.totals()["est_usd"])
    limiter = gating.FreeTierLimiter()

    def failed(n, reasons):
        doc["failed"].append({"n": n, "reasons": reasons})
        ctx.on_log(f"✖ preview {n}/{SAMPLES} not made: {'; '.join(reasons)}")
        _save(store, ctx.story_id, doc)

    total = 0.0
    for n, prompt in enumerate(prompts, start=1):
        ctx.cancel.check()
        seed = sample_seed(ctx.story_id, n)
        # The adapter writes into a scratch folder of its own; only an image of
        # a kept type is copied into styles/preview/.
        with tempfile.TemporaryDirectory(prefix="style-preview-") as incoming:
            request = _request(prompt, negative=negative, seed=seed, out_dir=incoming, n=n)
            try:
                result, answered = imaging.run_one(
                    KIND, chain, request, merged=merged, budget_obj=budget_obj, route=route,
                    budget_check=check, limiter=limiter, on_log=ctx.on_log, cancel=ctx.cancel,
                    adapters=adapters, transport=transport, sleep_fn=sleep_fn, time_fn=time_fn,
                )
            except imaging.NoImage as exc:
                failed(n, exc.reasons)
                continue
            except Exception as exc:  # noqa: BLE001 - an adapter's bug fails its sample, not the strip
                failed(n, [f"{type(exc).__name__}: {exc}"])
                continue

            # Answered: booked first, whatever becomes of the file.
            est = imaging.book(ledger, result, answered, kind=KIND, step=STEP)
            label = gen.describe(answered)
            name, problem = imaging.keep(result, folder, f"preview_{n}")
            if name is None:
                failed(n, [f"{label}: {problem}"])
                continue

        total += est
        doc["images"].append({
            "name": name, "n": n, "link": label, "seed": seed, "paid": bool(result.paid),
            "est_usd": est, "prompt": prompt,
        })
        ctx.on_log(f"🖼 preview {n}/{SAMPLES} via {label} (${est:.3f}{' paid' if result.paid else ''})")
        _save(store, ctx.story_id, doc)

    made = len(doc["images"])
    if not made:
        reasons = list(dict.fromkeys(reason for entry in doc["failed"] for reason in entry["reasons"]))
        raise StepFailed(f"No preview image was made on route {route}: {'; '.join(reasons)}.")
    total = round(total, 4)
    paid_note = " paid" if any(image["paid"] for image in doc["images"]) else ""
    ctx.on_log(f"🖼 Style preview ready: {made}/{SAMPLES} images (${total:.3f}{paid_note}).")
    return {"images": made, "failed": len(doc["failed"]), "est_usd": total}

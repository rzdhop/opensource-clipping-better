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

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
import time

from clipping.providers import adapters as adapters_mod
from clipping.providers import budget as budget_mod
from clipping.providers import gating, limits
from clipping.providers import generation as gen
from clipping.providers.registry import ChainError

from .. import ledger as ledger_mod
from .. import prompting, schemas
from . import llm_call
from .llm_call import StepFailed

STEP = "style_preview"
DOC_NAME = "style_preview.json"
LOCK_NAME = "style_lock.json"
LEDGER_NAME = "cost_ledger.json"
KIND = gen.IMAGE

# "Tiny" and vertical (9:16): enough to judge a palette and a light, cheap
# where a price scales with the size.
WIDTH, HEIGHT = 576, 1024
KEPT_EXTENSIONS = ("png", "jpg", "jpeg", "webp")

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

# The runner's own refusal of a paid link while paid is off; the step adds the
# numbers the runner does not have.
_PAID_OFF = "paid link; allow_paid is off"


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

def _allowance_spent(provider):
    """``limits.acquire``'s refusal for *provider*, read without counting a call."""
    limit = limits.limits_from_env().get(provider)
    if limit is None or limit.rpd is None:
        return None
    calls = limits.default_usage().calls(provider)
    if calls >= limit.rpd:
        return f"daily allowance spent ({calls}/{limit.rpd} today, resets at 00:00 UTC)"
    return None


def _missing_keys_reason(missing) -> str:
    # The runner's wording, so the estimate and the feed say the same thing.
    return f"no API key ({' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set)"


def _estimate_row(link, merged, budget_obj, request, *, route, story_spent, adapters) -> dict:
    """One link, through the runner's gates in the runner's order (route,
    adapter, keys, then local / paid / free), calling nothing."""
    summary = gating.link_summary(KIND, link, merged, budget_obj, request, qty=SAMPLES,
                                  story_spent=story_spent, adapters=adapters)
    local = link.provider == "local"
    paid = summary["paid"]
    row = {"link": summary["label"], "status": "skipped", "reason": None, "paid": paid,
           "est_usd": summary["est_usd"] if paid else 0.0}
    if route == "local" and not local:
        row["reason"] = "route is local"
    elif route == "api" and local:
        row["reason"] = "route is api"
    elif not summary["adapter"]:
        row["reason"] = f"no adapter yet for {link.provider} {KIND}"
    elif summary["missing_keys"]:
        row["reason"] = _missing_keys_reason(summary["missing_keys"])
    elif local:
        # Probing is a request; an estimate sends none. The step probes.
        row.update(status="runnable", reason="probed when it runs")
    elif paid:
        if summary["allowed"]:
            row.update(status="runnable", reason="paid, allowed")
        else:
            row["reason"] = summary["reason"]
    else:
        spent = _allowance_spent(link.provider)
        if spent:
            row["reason"] = spent
        else:
            row.update(status="runnable", reason="free")
    return row


def _blocked(rows, why) -> dict:
    return {
        "step": STEP, "est_usd": 0.0, "units": {"images": SAMPLES}, "route_class": "blocked",
        "link": None, "links": rows, "ready": False, "message": why,
    }


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
    ``message`` then names every link's reason.
    """
    if adapters is None:
        adapters_mod.load_all()
    merged = gating.merged_env(settings_env)
    try:
        chain = gen.chain_from_env(KIND, merged)
    except ChainError as exc:
        return _blocked([], f"IMAGE_CHAIN cannot be used: {exc}")
    try:
        budget_obj = gating.budget_of(merged)
    except ValueError as exc:
        return _blocked([], f"The budget settings cannot be used: {exc}")

    request = _request()
    rows = [_estimate_row(link, merged, budget_obj, request, route=route, story_spent=story_spent,
                          adapters=adapters) for link in chain]
    first = next((row for row in rows if row["status"] == "runnable"), None)
    if first is None:
        detail = "; ".join(f"{row['link']}: {row['reason']}" for row in rows) or "the chain is empty"
        return _blocked(rows, f"No link of {gen.ENV_NAMES[KIND]} can make the preview on route {route}: {detail}.")

    label = first["link"]
    if first["paid"]:
        route_class = "paid"
        message = f"{SAMPLES} images on {label}, paid: est ${first['est_usd']:.3f}."
    elif label.startswith("local/"):
        route_class = "local"
        message = (f"{SAMPLES} images on {label}, on your own hardware: $0.00. "
                   "Its server is probed when the preview runs.")
    else:
        route_class = "free"
        message = f"{SAMPLES} images on {label} (free): $0.00."
    later_paid = [row for row in rows[rows.index(first) + 1:] if row["status"] == "runnable" and row["paid"]]
    if not first["paid"] and later_paid:
        most = max(row["est_usd"] for row in later_paid)
        message += (f" If it fails, {', '.join(row['link'] for row in later_paid)} (paid, est up to "
                    f"${most:.3f}) may be reached.")
    return {
        "step": STEP, "est_usd": first["est_usd"], "units": {"images": SAMPLES},
        "route_class": route_class, "link": label, "links": rows, "ready": True, "message": message,
    }


# ------------------------------------------------------------------ helpers

def _read_lock(store, story_id) -> dict:
    try:
        lock = store.read_doc(story_id, LOCK_NAME)
    except schemas.SchemaError as exc:
        first = exc.errors[0] if exc.errors else str(exc)
        raise StepFailed(f"{LOCK_NAME} cannot be read ({first}); build the style again.") from None
    if lock is None:
        raise StepFailed("Build the style first.")
    errors = schemas.style_lock_errors(lock)
    if errors:
        raise StepFailed(f"{LOCK_NAME} is not a valid style lock ({'; '.join(errors[:3])}); "
                         "build the style again.")
    return lock


def _open_ledger(store, story_id) -> ledger_mod.CostLedger:
    """The story's ledger, checked before anything is spent: its total feeds
    the per-story cap, and ``CostLedger`` would start a torn file afresh on
    the first append -- losing what the story already spent."""
    path = os.path.join(store.story_dir(story_id), LEDGER_NAME)
    if os.path.islink(path):
        raise StepFailed(f"{LEDGER_NAME} is a symlink, which is never followed, so this story's "
                         "spending cannot be checked; replace it with the file itself.")
    if os.path.lexists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if (not isinstance(data, dict) or data.get("$schema") != ledger_mod.SCHEMA
                    or not isinstance(data.get("entries"), list)):
                raise ValueError(f"not a {ledger_mod.SCHEMA} document")
            sum(float(entry["est_usd"]) for entry in data["entries"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise StepFailed(f"{LEDGER_NAME} cannot be read ({type(exc).__name__}: {exc}), so this "
                             "story's spending cannot be checked; fix it before making a preview.") from None
    return ledger_mod.CostLedger(path)


def _save(store, story_id, doc) -> None:
    now = llm_call.utc_now()
    doc["updated_at"] = now
    store.write_doc(story_id, DOC_NAME, doc, now=now, validator=schemas.style_preview_errors)


def _book(ledger, result, answered) -> float:
    """Book one answered call; returns what it cost."""
    paid = bool(result.paid)
    est = float(result.est_cost) if paid else 0.0
    ledger.append(step=STEP, provider=answered.provider, model=gating.api_model_id(KIND, answered),
                  unit="image", qty=1, est_usd=est, paid=paid)
    # Today's spend, as the Settings chain test books a paid call. The runner
    # books nothing itself: this is the one booking of this call.
    if result.paid and result.est_cost > 0:
        budget_mod.record(result.est_cost)
    return round(est, 4)


def _keep(result, folder, n):
    """Copy the produced image into *folder* as ``preview_<n>.<ext>``;
    ``(name, None)``, or ``(None, why)`` when it is not an image we keep."""
    paths = [str(p) for p in (result.paths or ()) if p]
    if not paths:
        return None, "the answer carried no file"
    produced = paths[0]
    ext = os.path.splitext(produced)[1].lstrip(".").lower()
    if ext not in KEPT_EXTENSIONS:
        kept = f"{', '.join(KEPT_EXTENSIONS[:-1])} and {KEPT_EXTENSIONS[-1]}"
        return None, f"the answer is a .{ext or '?'} file; only {kept} are kept"
    if os.path.islink(produced) or not os.path.isfile(produced):
        return None, "the answer's file is missing, or a symlink (never followed)"
    name = f"preview_{n}.{ext}"
    handle, tmp = tempfile.mkstemp(dir=folder, prefix=f".{name}-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out, open(produced, "rb") as src:
            shutil.copyfileobj(src, out)
        os.chmod(tmp, 0o644)
        os.replace(tmp, os.path.join(folder, name))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return name, None


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
    lock = _read_lock(store, ctx.story_id)
    route = story["generation_profile"]["route"]

    merged = gating.merged_env(ctx.settings_env)
    try:
        chain = gen.chain_from_env(KIND, merged)
    except ChainError as exc:
        raise StepFailed(f"{gen.ENV_NAMES[KIND]} cannot be used: {exc}") from None
    try:
        budget_obj = gating.budget_of(merged)
    except ValueError as exc:
        raise StepFailed(f"The budget settings cannot be used: {exc}") from None
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
    by_label = {gen.describe(link): link for link in chain}

    def explain(label, reason, request):
        """A chain reason, verbatim; a paid link refused while paid is off also
        gets the numbers, which the runner does not have."""
        link = by_label.get(label)
        if reason == _PAID_OFF and link is not None:
            est = gating.link_summary(KIND, link, merged, budget_obj, request, adapters=adapters)["est_usd"]
            return (f"{label}: {reason} (est ${est:.3f} per image; today ${budget_mod.day_spent():.2f} "
                    f"of the ${budget_obj.daily_cap_usd:.2f} daily cap)")
        return f"{label}: {reason}"

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
                result, answered = gen.run_generation_chain(
                    KIND, chain, request, env=merged, allow_paid=budget_obj.allow_paid, route=route,
                    on_log=ctx.on_log, budget_check=check, limiter=limiter, adapters=adapters,
                    transport=transport, sleep_fn=sleep_fn, time_fn=time_fn, cancel=ctx.cancel,
                )
            except gen.NoRunnableLink as exc:
                reasons = [explain(label, reason, request) for label, reason in exc.failures]
                failed(n, reasons or [str(exc)])
                continue
            except Exception as exc:  # noqa: BLE001 - an adapter's bug fails its sample, not the strip
                failed(n, [f"{type(exc).__name__}: {exc}"])
                continue

            # Answered: booked first, whatever becomes of the file.
            est = _book(ledger, result, answered)
            label = gen.describe(answered)
            name, problem = _keep(result, folder, n)
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

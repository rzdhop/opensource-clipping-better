"""GPU jobs into a story (plan 36 stage 2.2): submit, then settle when RunPod is done.

A job is written to ``<story>/jobs.jsonl`` the moment it is submitted (a crash never loses a job id), and
again once settled: its outputs saved into the story, a clip recorded as its shot's next take, its cost in
``costs.jsonl`` (execution time billed, DEC-316). Settling is idempotent: a second fetch of a settled job
returns the record and bills nothing. Stdlib only; the endpoints are :class:`showrunner.runpod_client.Endpoint`
(or a fake with the same ``run`` / ``status``).
"""

from __future__ import annotations

import json
import os
import tempfile
import time

from showrunner import comfy_templates
from showrunner import runpod_client as rp
from showrunner.store import Story, StoreError

JOURNAL = "jobs.jsonl"
MEDIA_FIRST = (".mp4", ".flac", ".wav", ".mp3", ".png", ".jpg", ".webp")
# A rough price before a job runs (the ledger has the real one): GPU seconds per job, warm worker.
WARM_SECONDS = {"i2v_speech": 8.0, "a2v_speech": 8.0, "idlora_speech": 25.0, "tts_line": 5.0, "voice_conversion": 1.0,
                "t2i": 5.0, "edit": 8.0}   # i2v: ≈ 40 s for 5 s of clip, ≈ 80 s for 10 s -> 8 GPU-s per clip second
PER_CLIP_SECOND = {"i2v_speech", "a2v_speech", "idlora_speech"}
COLD_START_S = 300.0


class JobError(RuntimeError):
    pass


def estimate(template_name: str, values: dict, rate_per_s: float) -> dict:
    """``{"warm_usd", "cold_usd"}``: what one job of *template_name* costs on a warm worker, and with a cold
    start (the model load, ≈ 5 min on the L40S)."""
    task = comfy_templates.load_template(template_name).get("task")
    seconds = WARM_SECONDS.get(task, 10.0)
    if task in PER_CLIP_SECOND:
        seconds *= float(values.get("seconds") or 5)
    return {"warm_usd": round(seconds * rate_per_s, 4), "cold_usd": round((seconds + COLD_START_S) * rate_per_s, 4)}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def journal(story: Story) -> list:
    """Every record of the story's jobs, the latest state of each job last-wins: ``[{job, state, ...}]``."""
    path = story.path(JOURNAL)
    if not os.path.exists(path):
        return []
    merged: dict = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                row = json.loads(line)
                merged.setdefault(row["job"], {}).update(row)
    return list(merged.values())


def find(story: Story, job: str) -> dict:
    for row in journal(story):
        if row["job"] == job:
            return row
    raise JobError(f"no job {job} in {story.slug}'s journal")


def _append(story: Story, row: dict) -> None:
    with open(story.path(JOURNAL), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def submit(story: Story, endpoint, kind: str, template: str, values: dict, files: dict, *, dest: str = "",
           episode: int | None = None, shot: str | None = None, rate_per_s: float = 0.0,
           extra: dict | None = None) -> dict:
    """Send one job. *files*: ``{placeholder: story path}``; *dest*: where the outputs go in the story (a path
    without extension), or *episode* + *shot* for a clip that becomes the shot's next take. *extra*: more
    fields for the journal row (where the prompt came from)."""
    if not dest and not (episode is not None and shot):
        raise JobError("say where the result goes: dest (a story path without extension) or episode + shot")
    local = {}
    try:
        for placeholder, rel in (files or {}).items():
            full = story.path(rel)
            if not os.path.exists(full):
                raise JobError(f"no file {rel} in {story.slug} (for {placeholder})")
            local[placeholder] = full
        if dest:
            story.path(dest)  # refuses a path leaving the story
    except StoreError as exc:
        raise JobError(str(exc)) from exc
    name = f"{story.slug}_{shot or os.path.basename(dest)}"
    values = dict(values, name=name)
    try:
        job = rp.submit_template(endpoint, template, values, local)
    except (ValueError, rp.RunPodError, OSError) as exc:
        raise JobError(str(exc)) from exc
    row = {"job": job, "state": "SUBMITTED", "template": template, "kind": kind, "endpoint": endpoint.id,
           "dest": dest, "episode": episode, "shot": shot, "files": files or {}, "submitted_at": _now(),
           "values": {k: v for k, v in values.items() if k != "prompt"}, "prompt": values.get("prompt"),
           "estimate": estimate(template, values, rate_per_s), **(extra or {})}
    _append(story, row)
    return row


def _place(story: Story, row: dict, saved: list) -> list:
    """Copy the saved outputs into the story; their story paths, the main medium first.

    A clip of a shot -> ``epNN/clips/sNN_vK.mp4`` (the next take) and its last frame ``..._last.png``;
    anything else -> ``<dest><ext>``, extra files ``<dest>_<k><ext>``."""
    if not saved:
        return []
    main = next((p for p in saved if p.endswith(MEDIA_FIRST[:4])), saved[0])
    ext = os.path.splitext(main)[1].lower()
    if ext == ".mp4" and row.get("episode") is not None and row.get("shot"):
        main_rel = story.clip(row["episode"], row["shot"], story.next_take(row["episode"], row["shot"]))
    else:
        base = row.get("dest") or f"{story.episode(row['episode'])}/jobs/{row['shot']}"
        main_rel = base + ext
    base = os.path.splitext(main_rel)[0]
    out = [main_rel]
    story.copy_in(main, main_rel)
    for k, src in enumerate(p for p in saved if p != main):
        e = os.path.splitext(src)[1].lower()
        rel = f"{base}_last{e}" if ext == ".mp4" and e == ".png" and k == 0 else f"{base}_{k + 1}{e}"
        story.copy_in(src, rel)
        out.append(rel)
    return out


def settle(story: Story, row: dict, status: dict, *, rate_per_s: float) -> dict:
    """A finished job into the story: outputs placed, a clip recorded as a take, the cost in the ledger.
    Idempotent: a job already settled is returned as it is."""
    if row.get("state") in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"):
        return row
    state = status.get("status")
    billed, waited = rp.billed_seconds(status), rp.delay_seconds(status)
    usd = round(billed * rate_per_s, 4)
    done = {"job": row["job"], "state": state, "billed_s": billed, "delay_s": waited, "usd": usd, "settled_at": _now()}
    if state == "COMPLETED":
        with tempfile.TemporaryDirectory() as tmp:
            saved = rp.save_outputs(status, tmp, stem=row["job"][:12])
            try:
                done["outputs"] = _place(story, row, saved)
            except StoreError as exc:
                raise JobError(f"the outputs could not be saved: {exc}") from exc
        if row.get("episode") is not None and row.get("shot") and done["outputs"] and done["outputs"][0].endswith(".mp4"):
            done["take"] = story.add_take(row["episode"], row["shot"], done["outputs"][0],
                                          seed=row["values"].get("seed"), job=row["job"], note=row["template"])
    else:
        done["error"] = str(status.get("error") or status.get("output") or state)[:500]
    story.add_cost(row["kind"], row["template"], row["job"], billed, waited, usd, episode=row.get("episode"),
                   note=f"{row.get('shot') or row.get('dest')} {state}")
    _append(story, done)
    return {**row, **done}


def fetch(story: Story, endpoint, job: str, *, wait_s: float = 0.0, rate_per_s: float, poll_s: float = 5.0,
          sleep=time.sleep, clock=time.monotonic) -> dict:
    """The job's state; once terminal it is settled into the story. Waits at most *wait_s* for it."""
    row = find(story, job)
    if row.get("state") not in (None, "SUBMITTED", "IN_QUEUE", "IN_PROGRESS"):
        return row
    deadline = clock() + max(0.0, wait_s)
    while True:
        try:
            status = endpoint.status(job)
        except rp.RunPodError as exc:
            raise JobError(str(exc)) from exc
        if status.get("status") in rp.TERMINAL:
            return settle(story, row, status, rate_per_s=rate_per_s)
        if clock() + poll_s > deadline:
            return {**row, "state": status.get("status"), "waiting": True,
                    "delay_s_so_far": rp.delay_seconds(status)}
        sleep(poll_s)


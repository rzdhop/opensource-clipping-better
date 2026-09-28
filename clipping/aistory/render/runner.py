"""The render runner: runs a render plan's commands one at a time, with the
manifest written before each process starts (spec 6.5, 2.9, 13; plan phase 4
stage 7, "Renderer" -> runner.py; DEC-156).

**Entry points**

- :func:`preflight` -- ``ffmpeg``/``ffprobe`` present, and ``ffmpeg
  -filters`` lists every filter the renderer uses (:data:`REQUIRED_FILTERS`).
  Returns ``{"version", "machine"}``; raises :class:`PreflightError` naming
  what is missing.
- :func:`file_record` -- hash one input file for the plan.
- :func:`run_render` -- run a ``plan.build_render_plan`` plan.
- :func:`render` -- the three in one: pre-flight, plan, run. A pre-flight or
  plan failure (``GraphError``, ``TimelineError``, ...) comes back as a
  failed result carrying the message; nothing is written then, so the
  previous render and its manifest stay as they were.

**One stage** (:func:`run_render`): its manifest entry (``argv``,
``cache_key``, state ``running``) is written atomically *before* ``Popen``;
the process runs in ``render/`` with stdin closed and stdout/stderr going to
``render/logs/``; the runner polls it every :data:`POLL_S` seconds for exit,
cancel and the stage's timeout. A cancel or a timeout terminates the
process, and kills it after :data:`KILL_GRACE_S`. Afterwards the entry is
``done`` (output sha256, seconds), ``failed`` (stderr tail; partial files
are kept where the process left them) or ``cancelled``. The first stage that
does not finish ends the render.

**Cache.** A shot or end-card stage whose ``cache/<key>.mp4`` exists is
``cached``: no process is started. Once a render completes, every file in
``cache/`` whose key neither this manifest nor the previous one names is
pruned. A, L, F, P and M always run.

**Result** (every entry point): ``{"state": "completed"|"failed"|
"cancelled", "error", "failed_stage", "manifest", "output", "warnings",
"ran", "cached"}`` -- ``manifest`` is the document as last written (None
when nothing was), ``ran``/``cached`` the stage ids by what happened.

Stdlib + this package (DEC-012). ``popen``, ``run``, ``clock`` and ``now``
are injectable for tests (a fake process needs ``poll``, ``wait(timeout)``,
``terminate`` and ``kill``).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone

from ... import loudness
from ... import cancel as cancel_mod
from . import fonts as fonts_mod
from . import manifest as manifest_mod
from . import plan as plan_mod
from . import profiles

REQUIRED_FILTERS = ("zoompan", "xfade", "sidechaincompress", "loudnorm", "ass")

POLL_S = 0.25
KILL_GRACE_S = 3.0
# Per-stage ceilings, generous against the measured times (A-067, A-069):
# they only catch a hung process.
STAGE_TIMEOUT_S = {
    "shot": 300.0,
    "end_card": 120.0,
    "audio_mix": 300.0,
    "loudness_measure": 300.0,
    "final": 1800.0,
    "loudness_apply": 600.0,
    "probe": 300.0,
    "framemd5": 600.0,
}
STDERR_TAIL_CHARS = 3000

_CACHE_FILE_RE = re.compile(r"^([0-9a-f]{64})(\.part)?\.mp4$")
_VERSION_RE = re.compile(r"^ffmpeg version (\S+)")


class PreflightError(RuntimeError):
    """ffmpeg, ffprobe or one of the filters the renderer needs is missing."""


class RunnerError(RuntimeError):
    """The runner itself cannot proceed (a bad working folder, a staged input
    whose bytes are not the ones the plan hashed)."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path, source: str) -> dict:
    """``{path, source, sha256}`` for one input file (the plan's ``inputs``)."""
    path = os.fspath(path)
    return {"path": path, "source": source, "sha256": _sha256_file(path)}


def paper_texture_record() -> dict:
    """The bundled paper texture, as a plan input."""
    return file_record(fonts_mod.REPO_ROOT / plan_mod.PAPER_TEXTURE_SOURCE, plan_mod.PAPER_TEXTURE_SOURCE)


# --------------------------------------------------------------- pre-flight

def ffmpeg_version(version_output: str):
    """The version from ``ffmpeg -version``'s first line
    (``ffmpeg version 6.1.1-3ubuntu5 Copyright ...`` -> ``6.1.1-3ubuntu5``)."""
    first = (version_output or "").strip().splitlines()
    match = _VERSION_RE.match(first[0]) if first else None
    return match.group(1) if match else None


def listed_filters(filters_output: str) -> set:
    """The filter names ``ffmpeg -filters`` lists (`` TSC name  A->A  ...``)."""
    names = set()
    for line in (filters_output or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and "->" in parts[2]:
            names.add(parts[1])
    return names


def preflight(*, run=subprocess.run, machine=None) -> dict:
    """``{"version", "machine"}`` of the ffmpeg the render will use, or
    :class:`PreflightError` naming what is missing."""
    outputs = {}
    for tool in ("ffmpeg", "ffprobe"):
        try:
            result = run([tool, "-hide_banner", "-version"], capture_output=True, text=True,
                         stdin=subprocess.DEVNULL)
        except OSError as exc:
            raise PreflightError(f"{tool} is not installed or cannot run: {exc}") from exc
        if result.returncode != 0:
            raise PreflightError(f"{tool} -version failed (exit {result.returncode})")
        outputs[tool] = result.stdout or ""
    version = ffmpeg_version(outputs["ffmpeg"])
    if version is None:
        raise PreflightError("cannot read the ffmpeg version from `ffmpeg -version`")
    result = run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True,
                 stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise PreflightError(f"ffmpeg -filters failed (exit {result.returncode})")
    available = listed_filters(result.stdout)
    missing = [name for name in REQUIRED_FILTERS if name not in available]
    if missing:
        raise PreflightError(f"ffmpeg {version} lacks the filter(s) {', '.join(missing)} the renderer needs")
    return {"version": version, "machine": machine or platform.machine() or "unknown"}


# ------------------------------------------------------------------ helpers

def _result(state, *, error=None, failed_stage=None, manifest=None, output=None, warnings=(), ran=(),
            cached=()) -> dict:
    return {"state": state, "error": error, "failed_stage": failed_stage, "manifest": manifest,
            "output": output, "warnings": list(warnings), "ran": list(ran), "cached": list(cached)}


def _real_dir(path: str) -> str:
    """Make *path* a directory if missing; refuse a symlink or a non-directory
    (the store's rule: never write through a link)."""
    if os.path.islink(path):
        raise RunnerError(f"{path} is a symlink; refused")
    os.makedirs(path, exist_ok=True)
    if not os.path.isdir(path) or os.path.islink(path):
        raise RunnerError(f"{path} is not a directory")
    return path


def _stage_inputs(plan: dict, render_dir: str) -> None:
    """Copy every input into ``in/`` under its content hash (skipped when the
    staged copy already has that hash), checking the bytes are the ones the
    plan hashed."""
    for item in plan["inputs"]:
        dest = os.path.join(render_dir, item["staged"])
        if os.path.isfile(dest) and not os.path.islink(dest) and _sha256_file(dest) == item["sha256"]:
            continue
        tmp = dest + ".tmp"
        digest = hashlib.sha256()
        with open(item["path"], "rb") as source, open(tmp, "wb") as out:
            for chunk in iter(lambda: source.read(1 << 20), b""):
                digest.update(chunk)
                out.write(chunk)
        if digest.hexdigest() != item["sha256"]:
            os.remove(tmp)
            raise RunnerError(f"{item['source']} changed after the render was planned "
                              f"(sha256 {digest.hexdigest()[:12]}, planned {item['sha256'][:12]})")
        os.replace(tmp, dest)


def _stage_font(plan: dict, render_dir: str) -> None:
    """Stage the font alone into ``fonts/`` (a font left by an earlier render
    could answer the same family name), and check its bytes."""
    fonts_dir = os.path.join(render_dir, plan_mod.FONTS_DIR)
    keep = os.path.basename(plan["font"]["file"])
    for name in os.listdir(fonts_dir):
        path = os.path.join(fonts_dir, name)
        if name != keep and os.path.isfile(path) and not os.path.islink(path):
            os.remove(path)
    staged = fonts_mod.stage(plan["font"], fonts_dir)
    if _sha256_file(staged) != plan["font"]["sha256"]:
        raise RunnerError(f"the staged font {plan['font']['file']} does not match its recorded sha256")


def _write_files(plan: dict, render_dir: str) -> None:
    for item in plan["files"]:
        path = os.path.join(render_dir, item["path"])
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(item["text"])
        os.replace(tmp, path)


def _tail(path: str, limit: int = STDERR_TAIL_CHARS) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit * 4))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _log_name(stage_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", stage_id)


def _read_json(path: str):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp, path)


def _wait(proc, *, cancel, clock, timeout_s):
    """Poll *proc* until it exits, is cancelled or times out:
    ``("exited", rc)`` / ``("cancelled", None)`` / ``("timeout", None)``.
    Between two polls the runner blocks in ``proc.wait(POLL_S)``, so an exit
    is seen at once and a cancel within one poll."""
    started = clock()
    while True:
        rc = proc.poll()
        if rc is not None:
            return "exited", rc
        if cancel.cancelled:
            _stop(proc, clock=clock)
            return "cancelled", None
        if clock() - started > timeout_s:
            _stop(proc, clock=clock)
            return "timeout", None
        try:
            proc.wait(timeout=POLL_S)
        except subprocess.TimeoutExpired:
            pass


def _stop(proc, *, clock) -> None:
    """Terminate, then kill after :data:`KILL_GRACE_S`."""
    try:
        proc.terminate()
    except OSError:
        pass
    deadline = clock() + KILL_GRACE_S
    while proc.poll() is None and clock() < deadline:
        try:
            proc.wait(timeout=POLL_S)
        except subprocess.TimeoutExpired:
            pass
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(timeout=KILL_GRACE_S)
        except Exception:  # noqa: BLE001 - the process is gone or unkillable; the stage is over either way
            pass


def _finite(value) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{value!r} is not finite")
    return number


def _output_record(plan, render_dir, final_path, manifest_dir) -> dict:
    """``output`` of the manifest, from P's probe, P:loudness and M."""
    probe = _read_json(os.path.join(render_dir, plan_mod.PROBE_REL))
    video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise RunnerError("the final file has no video stream")
    fmt = probe.get("format") or {}
    loud = _read_json(os.path.join(render_dir, plan_mod.LOUDNESS_FINAL_REL))
    framemd5 = os.path.join(render_dir, plan_mod.FRAMEMD5_REL)
    return {
        "path": manifest_mod.relative_to(final_path, manifest_dir),
        "sha256": _sha256_file(final_path),
        "duration_s": round(_finite(fmt.get("duration")), 3),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "fps": str(video.get("r_frame_rate")),
        "loudness": {"i": _finite(loud["input_i"]), "tp": _finite(loud["input_tp"]),
                     "lra": _finite(loud["input_lra"])},
        "framemd5": {"file": manifest_mod.relative_to(framemd5, manifest_dir), "sha256": _sha256_file(framemd5)},
    }


def output_warnings(plan: dict, output: dict) -> list:
    """Length and loudness outside the targets: reported, never a failure."""
    warnings = []
    lo, hi = plan["length_window_s"]
    duration = output["duration_s"]
    if not lo <= duration <= hi:
        warnings.append(f"The episode is {duration:.1f} s long, outside {lo:g}-{hi:g} s.")
    target = profiles.LOUDNESS_TARGET_I
    if abs(output["loudness"]["i"] - target) > profiles.LOUDNESS_TOLERANCE_LU:
        warnings.append(f"Loudness {output['loudness']['i']:.1f} LUFS is outside {target:g} "
                        f"+/- {profiles.LOUDNESS_TOLERANCE_LU:g} LU.")
    if output["loudness"]["tp"] > profiles.TRUE_PEAK_MAX_DBTP:
        warnings.append(f"True peak {output['loudness']['tp']:.1f} dBTP is above "
                        f"{profiles.TRUE_PEAK_MAX_DBTP:g} dBTP.")
    return warnings


def prune_cache(cache_dir: str, keep: set) -> list:
    """Remove every ``<key>.mp4``/``<key>.part.mp4`` in *cache_dir* whose key
    is not in *keep*; returns the names removed."""
    removed = []
    for name in sorted(os.listdir(cache_dir)):
        match = _CACHE_FILE_RE.match(name)
        path = os.path.join(cache_dir, name)
        if match and match.group(1) not in keep and os.path.isfile(path) and not os.path.islink(path):
            os.remove(path)
            removed.append(name)
    return removed


def _publish(src: str, dest: str) -> None:
    if os.path.abspath(src) == os.path.abspath(dest):
        return
    try:
        os.replace(src, dest)
    except OSError:
        tmp = dest + ".tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, dest)
        os.remove(src)


# ----------------------------------------------------------------------- run

def run_render(plan: dict, *, render_dir, manifest_path, final_path=None, cancel=None, popen=subprocess.Popen,
               clock=time.monotonic, now=_utc_now, timeouts=None, on_log=None) -> dict:
    """Run *plan* (``plan.build_render_plan``) in *render_dir*.

    *manifest_path* is where ``render_manifest.json`` is written; its folder
    must contain *render_dir* and *final_path* (manifest paths never climb).
    *final_path* is where ``episode_final.mp4`` is moved once every stage
    is done (default: it stays in *render_dir*). *cancel* is a
    ``clipping.cancel.CancelToken`` (default: never cancelled). *timeouts*
    overrides :data:`STAGE_TIMEOUT_S` per stage kind. Returns the result
    dict (module docstring). Raises :class:`RunnerError` before writing
    anything when a work folder is a symlink, and ValueError when a path
    is outside the manifest's folder: both are the caller's layout, not
    the render's.
    """
    cancel = cancel if cancel is not None else cancel_mod.NEVER
    log = on_log or (lambda message: None)
    limits = dict(STAGE_TIMEOUT_S, **(timeouts or {}))
    render_dir = os.path.abspath(os.fspath(render_dir))
    manifest_path = os.path.abspath(os.fspath(manifest_path))
    manifest_dir = os.path.dirname(manifest_path)
    final_path = os.path.abspath(os.fspath(final_path)) if final_path is not None \
        else os.path.join(render_dir, plan_mod.FINAL_REL)
    manifest_mod.relative_to(render_dir, manifest_dir)
    manifest_mod.relative_to(final_path, manifest_dir)

    _real_dir(render_dir)
    for sub in plan_mod.WORK_DIRS:
        _real_dir(os.path.join(render_dir, sub))

    previous_keys = manifest_mod.cache_keys(manifest_mod.read_manifest(manifest_path))
    started = clock()
    doc = manifest_mod.new_manifest(plan, now=now())
    doc["warnings"] = [_clip(w, 300) for w in doc["warnings"]]
    ran, cached = [], []

    def save():
        doc["updated_at"] = now()
        manifest_mod.write_manifest(manifest_path, doc)

    def finish(state, *, error=None, failed_stage=None, output=None):
        doc["timings"]["finished_at"] = now()
        doc["timings"]["total_s"] = round(max(0.0, clock() - started), 3)
        save()
        return _result(state, error=error, failed_stage=failed_stage, manifest=doc, output=output,
                       warnings=doc["warnings"], ran=ran, cached=cached)

    save()
    try:
        _stage_inputs(plan, render_dir)
        _stage_font(plan, render_dir)
        _write_files(plan, render_dir)
    except (OSError, RunnerError) as exc:
        return finish("failed", error=f"staging the inputs failed: {exc}")

    measured_mix = None
    for stage in plan["stages"]:
        if cancel.cancelled:
            return finish("cancelled", error="cancelled before stage " + stage["id"])

        if stage["cache_key"] is not None:
            hit = os.path.join(render_dir, stage["output"])
            if os.path.isfile(hit) and not os.path.islink(hit) and os.path.getsize(hit) > 0:
                entry = manifest_mod.stage_entry(stage, stage["argv"], state="cached")
                entry["output"] = stage["output"]
                entry["output_sha256"] = _sha256_file(hit)
                doc["stages"].append(entry)
                save()
                cached.append(stage["id"])
                log(f"{stage['id']}: cached")
                continue

        argv = stage["argv"] if stage["argv"] is not None else plan_mod.loudness_apply_argv(stage, measured_mix)
        entry = manifest_mod.stage_entry(stage, argv)
        doc["stages"].append(entry)
        save()  # manifest-before-run

        log_base = os.path.join(render_dir, plan_mod.LOGS_DIR, _log_name(stage["id"]))
        stdout_path, stderr_path = log_base + ".stdout.log", log_base + ".stderr.log"
        write_path = os.path.join(render_dir, stage["write"])
        if os.path.isfile(write_path) and not os.path.islink(write_path):
            os.remove(write_path)  # an earlier render's file must never pass for this one's
        stage_started = clock()
        with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
            try:
                proc = popen(argv, cwd=render_dir, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
            except OSError as exc:
                proc = None
                err.write(f"{argv[0]} could not start: {exc}".encode("utf-8"))
            if proc is None:
                outcome, rc = "exited", -1
            else:
                outcome, rc = _wait(proc, cancel=cancel, clock=clock, timeout_s=float(limits[stage["kind"]]))
        entry["seconds"] = round(max(0.0, clock() - stage_started), 3)
        ran.append(stage["id"])

        problem = None
        if outcome == "cancelled":
            entry["state"] = "cancelled"
            entry["stderr_tail"] = _clip(_tail(stderr_path), 4000) or None
            if os.path.isfile(write_path):
                entry["output"] = stage["write"]
            return finish("cancelled", error=f"cancelled during {stage['id']}", failed_stage=stage["id"])
        if outcome == "timeout":
            problem = f"timed out after {limits[stage['kind']]:g} s"
        elif rc != 0:
            problem = f"exit {rc}"
        else:
            try:
                if stage["capture"] == "stdout":
                    shutil.copyfile(stdout_path, write_path)
                elif stage["capture"] == "loudnorm":
                    with open(stderr_path, encoding="utf-8", errors="replace") as handle:
                        measured = loudness.parse_measurement(handle.read())
                    if measured is None:
                        raise RunnerError("loudnorm printed no usable measurement")
                    _write_json(write_path, measured)
                    if stage["id"] == "L1":
                        measured_mix = measured
                if not os.path.isfile(write_path) or os.path.getsize(write_path) == 0:
                    raise RunnerError(f"{stage['write']} was not written")
                if stage["write"] != stage["output"]:
                    os.replace(write_path, os.path.join(render_dir, stage["output"]))
            except (OSError, ValueError, RunnerError) as exc:
                problem = str(exc)

        if problem is not None:
            entry["state"] = "failed"
            entry["stderr_tail"] = _clip(f"{problem}\n{_tail(stderr_path)}".strip(), 4000)
            if os.path.isfile(write_path):
                entry["output"] = stage["write"]
            log(f"{stage['id']}: failed ({problem})")
            return finish("failed", error=f"stage {stage['id']} failed: {problem}", failed_stage=stage["id"])

        entry["state"] = "done"
        entry["output"] = stage["output"]
        entry["output_sha256"] = _sha256_file(os.path.join(render_dir, stage["output"]))
        save()
        log(f"{stage['id']}: done in {entry['seconds']:.1f} s")

    try:
        _publish(os.path.join(render_dir, plan_mod.FINAL_REL), final_path)
        output = _output_record(plan, render_dir, final_path, manifest_dir)
    except (OSError, ValueError, KeyError, TypeError, RunnerError) as exc:
        return finish("failed", error=f"the finished render could not be read back: {exc}")
    doc["warnings"] += [_clip(w, 300) for w in output_warnings(plan, output)]
    doc["output"] = output
    result = finish("completed", output=output)
    keep = manifest_mod.cache_keys(doc) | previous_keys
    removed = prune_cache(os.path.join(render_dir, plan_mod.CACHE_DIR), keep)
    if removed:
        log(f"cache: pruned {len(removed)} file(s)")
    return result


def render(*, plan_args: dict, render_dir, manifest_path, final_path=None, profile="final", run=subprocess.run,
           popen=subprocess.Popen, cancel=None, clock=time.monotonic, now=_utc_now, timeouts=None, on_log=None,
           machine=None) -> dict:
    """Pre-flight, plan (``plan.build_render_plan(**plan_args,
    ffmpeg=..., profile=...)``) and run. A pre-flight or plan failure is a
    failed result with its message and writes nothing."""
    try:
        info = preflight(run=run, machine=machine)
    except PreflightError as exc:
        return _result("failed", error=f"ffmpeg pre-flight: {exc}")
    try:
        plan = plan_mod.build_render_plan(**plan_args, ffmpeg=info, profile=profile)
    except plan_mod.PlanError as exc:
        return _result("failed", error=f"render plan: {exc}")
    return run_render(plan, render_dir=render_dir, manifest_path=manifest_path, final_path=final_path,
                      cancel=cancel, popen=popen, clock=clock, now=now, timeouts=timeouts, on_log=on_log)

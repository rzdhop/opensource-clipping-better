"""A failed render leaves no per-item intermediate files behind.

proses_klip writes its intermediates as relative paths in the working
directory. The fixed-name ones (h_1.ts, m_1.ts, ...) are removed by its
`finally`. The ones named per hook-v2 item and per trimmed segment --
h_v2_silent_*, h_v2_ts_*, h_v2_trans_*, m_seg_*, m_bgm_* -- were removed only
by cleanup code that runs after the last ffmpeg call succeeds, so any ffmpeg
failure in between left them in the working directory (the web backend's is
/app) for good.

core is imported through the package with the render stack mocked, so this runs in the
pytest-only CI environment.
"""

import ast
import importlib
import pathlib
import subprocess
import types

import pytest

STUDIO = pathlib.Path(__file__).resolve().parents[1] / "clipping" / "studio"


@pytest.fixture
def core(render_stack_stubbed, monkeypatch):
    # Imported through the package: since clipping/studio/ became a real
    # package, core.py uses relative imports and cannot run outside it.
    return importlib.import_module("clipping.studio.core")


def _touch(path):
    pathlib.Path(path).write_bytes(b"x")


def test_a_failed_hook_v2_transition_leaves_no_temp_files(core, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "outputs").mkdir()

    monkeypatch.setattr(core, "siapkan_font_tipografi", lambda cfg: None)
    monkeypatch.setattr(core, "get_ts_encode_args", lambda enc, fps=30: [])
    monkeypatch.setattr(core, "_get_render_dims", lambda *a, **k: (720, 1280))
    monkeypatch.setattr(core, "buat_video_hybrid", lambda src, out, *a, **k: _touch(out))
    monkeypatch.setattr(core, "build_ffmpeg_progress_cmd", lambda cmd, out: ("ffmpeg", out))
    monkeypatch.setattr(core, "run_ffmpeg_with_progress", lambda cmd, *a, **k: _touch(cmd[1]))

    def transition_fails(path, **kwargs):
        _touch(path)  # ffmpeg had started writing it
        raise subprocess.CalledProcessError(1, ["ffmpeg"])

    monkeypatch.setattr(core.v2_helpers, "create_white_flash_transition", transition_fails)

    cfg = types.SimpleNamespace(
        file_video_asli=str(tmp_path / "talk.mp4"), outputs_dir=str(tmp_path / "outputs"),
        durasi_hook=3.0, use_hook_glitch=False, use_broll=False, hook_v2=True,
        hook_v2_items=2, white_flash_duration=0.12, video_sharpen=False,
        use_split_screen=False, use_camera_switch=False, pexels_api_key="",
    )
    clip = {"start_time": 10.0, "end_time": 30.0,
            "hook_v2": {"items": [{"start_time": 11.0, "end_time": 12.0},
                                  {"start_time": 14.0, "end_time": 15.0}],
                        "transition": {"type": "white_flash"}}}

    result = core.proses_klip(1, clip, "9:16", None, [], cfg, {"name": "libx264", "args": []})

    assert result["status"] == "failed"
    leftovers = sorted(p.name for p in tmp_path.iterdir() if p.is_file())
    assert leftovers == []


def _proses_klip():
    tree = ast.parse((STUDIO / "core.py").read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "proses_klip")


def test_every_per_item_temp_is_registered_for_cleanup():
    """Every variable holding an h_v2_*/m_seg_*/m_bgm_* name is handed to the
    list the `finally` deletes -- including the segment path, which needs a
    real trim plan to reach."""
    func = _proses_klip()
    temps = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.JoinedStr):
            head = node.value.values[0] if node.value.values else None
            if (isinstance(head, ast.Constant)
                    and str(head.value).startswith(("h_v2_", "m_seg_", "m_bgm_"))):
                temps |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    assert temps, "the per-item temp names moved; update this guard"

    registered = set()
    for node in ast.walk(func):
        # step_temps.append(x) / step_temps.extend([...]) / step_temps += [...]
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and getattr(node.func.value, "id", None) == "step_temps"):
            registered |= {n.id for arg in node.args for n in ast.walk(arg)
                           if isinstance(n, ast.Name)}
        if (isinstance(node, ast.AugAssign)
                and getattr(node.target, "id", None) == "step_temps"):
            registered |= {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)}
    assert sorted(temps - registered) == []


def test_the_finally_deletes_the_registered_temps():
    func = _proses_klip()
    finals = [n for n in ast.walk(func) if isinstance(n, ast.Try) and n.finalbody]
    names = {n.id for t in finals for stmt in t.finalbody for n in ast.walk(stmt)
             if isinstance(n, ast.Name)}
    assert "step_temps" in names

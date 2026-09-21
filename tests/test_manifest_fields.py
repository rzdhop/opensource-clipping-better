"""The render manifest is the only cross-process contract for a finished clip.

Both uploaders and the dashboard read `render_manifest.json`, and nothing else
carries a clip's metadata between the render and them. A field the dashboard
displays but the manifest never held renders empty forever, which is what
happened to the score badge.

`clipping/studio/core.py` imports cv2 and mediapipe at file scope, so the
manifest is inspected by AST rather than by calling the function.
"""

import ast
import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = PROJECT_ROOT / "clipping" / "studio" / "core.py"


def manifest_keys():
    """The literal keys `manifest_item` is built with."""
    tree = ast.parse(CORE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "manifest_item" not in targets:
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        return {
            key.value
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    raise AssertionError("manifest_item dict literal not found in core.py")


def test_the_manifest_is_findable():
    """A guard that parses nothing passes vacuously."""
    keys = manifest_keys()
    assert len(keys) > 10
    assert "rank" in keys and "video_path" in keys


def test_the_score_badge_has_something_to_display():
    """web/api/worker.py reads entry.get("viral_score") into ClipDetail, and
    JobDetail.jsx renders it. The manifest never carried it, so the badge has
    always been empty."""
    assert "viral_score" in manifest_keys()


def test_every_field_the_job_store_reads_is_in_the_manifest():
    """ClipDetail is built from manifest entries in web/api/worker.py."""
    worker_src = (PROJECT_ROOT / "web" / "api" / "worker.py").read_text(encoding="utf-8")
    tree = ast.parse(worker_src)

    read = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "get" and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "entry" and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        read.add(arg.value)

    assert read, "no entry.get(...) calls found; has worker.py been restructured?"
    missing = sorted(read - manifest_keys())
    assert missing == [], f"worker reads manifest keys that are never written: {missing}"


def test_the_uploaders_titles_are_in_the_manifest():
    """youtube_uploader and facebook_uploader read these off manifest entries."""
    keys = manifest_keys()
    for field in ("youtube_title_final", "youtube_description_final",
                  "youtube_tags_final", "tiktok_caption_final"):
        assert field in keys, field


def test_the_manifest_still_carries_the_render_outputs():
    keys = manifest_keys()
    for field in ("video_path", "thumbnail_path", "start_time", "end_time", "duration"):
        assert field in keys, field

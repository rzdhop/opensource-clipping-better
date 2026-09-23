"""clipping/studio/ is a real package.

clipping/studio.py shadowed the clipping/studio/ directory, which had no
__init__.py, so `import clipping.studio.core` raised "'clipping.studio' is not
a package". Every file in the directory worked around it with its own copy of
_load_studio_internal_module, loading its siblings by path: ~50 loads, helpers
and ffmpeg_utils executed about 13 times each, none registered in sys.modules,
so no two files shared a module -- or its state. The watermark renderer cache
being emptied every frame (test_watermark_cache.py) was one consequence.

studio.py is now studio/__init__.py with the same public names, and the files
import each other relatively. The guards below read source, and the smoke test
imports the package with the render stack mocked, so all of it runs in the
pytest-only CI environment; the real import runs where cv2 is installed.
"""

import ast
import importlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CLIPPING = ROOT / "clipping"
STUDIO = CLIPPING / "studio"

# The public surface callers use (runner.py, web/api/worker.py).
PUBLIC = [
    "FIREFOX_UA", "format_seconds", "escape_ffmpeg_filter_value",
    "detect_video_encoder", "get_ts_encode_args", "get_mp4_encode_args",
    "open_ffmpeg_video_writer", "build_ffmpeg_progress_cmd",
    "run_ffmpeg_with_progress", "get_face_detector",
    "estimate_speaker_count_from_video", "download_google_font",
    "register_fonts_for_libass", "siapkan_font_tipografi", "get_local_bgm_file",
    "build_bgm_filter", "download_pexels_broll", "crop_center_broll",
    "buat_video_hybrid", "buat_file_ass", "siapkan_glitch_video",
    "download_transition_raw", "download_all_transitions",
    "get_random_transition", "prepare_transition_clip", "TMP_TRANSITION_POOL",
    "buat_thumbnail", "buat_video_split_screen", "buat_video_camera_switch",
    "_get_render_dims", "_is_vertical_ratio", "proses_klip",
]

# Module-level names each file bound to a sibling, which the rest of the file
# uses. They must still be bound, to the same sibling.
SIBLINGS = {
    "broll.py": {"utils": "utils", "_helpers": "helpers", "_ffmpeg_utils": "ffmpeg_utils"},
    "core.py": {
        "utils": "utils", "face_detection": "face_detection", "typography": "typography",
        "audio_bgm": "audio_bgm", "broll": "broll", "subtitles": "subtitles",
        "effects": "effects", "transitions": "transitions", "thumbnail": "thumbnail",
        "render_hybrid": "render_hybrid", "render_split_screen": "render_split_screen",
        "render_camera_switch": "render_camera_switch", "_helpers": "helpers",
        "_ffmpeg_utils": "ffmpeg_utils", "v2_helpers": "v2_helpers",
        "edge_glow_mod": "edge_glow",
    },
    "effects.py": {"_helpers": "helpers", "_ffmpeg_utils": "ffmpeg_utils", "utils": "utils"},
    "face_detection.py": {"_helpers": "helpers", "_ffmpeg_utils": "ffmpeg_utils"},
    "render_camera_switch.py": {
        "_helpers": "helpers", "_ffmpeg_utils": "ffmpeg_utils", "_wm_mod": "watermark",
        "utils": "utils", "face_detection": "face_detection", "broll": "broll",
    },
    "render_hybrid.py": {
        "_helpers": "helpers", "_ffmpeg_utils": "ffmpeg_utils", "_wm_mod": "watermark",
        "utils": "utils", "broll": "broll", "face_detection": "face_detection",
    },
    "render_split_screen.py": {
        "_helpers": "helpers", "_ffmpeg_utils": "ffmpeg_utils", "_wm_mod": "watermark",
        "utils": "utils", "face_detection": "face_detection", "broll": "broll",
    },
    "subtitles.py": {"_helpers": "helpers", "_ffmpeg_utils": "ffmpeg_utils",
                     "utils": "utils", "typography": "typography"},
    "thumbnail.py": {"_helpers": "helpers", "_ffmpeg_utils": "ffmpeg_utils"},
    "transitions.py": {"_ffmpeg_utils": "ffmpeg_utils", "utils": "utils"},
    "typography.py": {"_helpers": "helpers", "_ffmpeg_utils": "ffmpeg_utils"},
    "utils.py": {"_helpers": "helpers", "_ffmpeg_utils": "ffmpeg_utils"},
}


def _relative_imports(path):
    """{bound name: sibling module} for every module-level `from . import x [as y]`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bound = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None:
            for alias in node.names:
                bound[alias.asname or alias.name] = alias.name
    return bound


# ------------------------------------------------------------ guards (CI)

def test_the_shadowing_module_is_gone():
    assert not (CLIPPING / "studio.py").exists()
    assert (STUDIO / "__init__.py").is_file()


def test_no_module_is_loaded_by_path_any_more():
    offenders = []
    for path in CLIPPING.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for needle in ("spec_from_file_location", "_load_studio_internal_module", "clipping_studio_"):
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}: {needle}")
    assert offenders == []


def test_the_package_keeps_its_public_names():
    tree = ast.parse((STUDIO / "__init__.py").read_text(encoding="utf-8"))
    bound = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            bound |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.ImportFrom):
            bound |= {a.asname or a.name for a in node.names}
    assert sorted(set(PUBLIC) - bound) == []


@pytest.mark.parametrize("name", sorted(SIBLINGS))
def test_each_file_still_binds_its_siblings(name):
    bound = _relative_imports(STUDIO / name)
    for alias, module in SIBLINGS[name].items():
        assert bound.get(alias) == module, f"{name}: {alias} should be `from . import {module}`"


def test_the_package_import_graph_has_no_cycle():
    graph = {path.stem: set(_relative_imports(path).values())
             for path in STUDIO.glob("*.py")}
    visiting, done = set(), set()

    def visit(node, trail):
        if node in done:
            return
        assert node not in visiting, "import cycle: " + " -> ".join(trail + [node])
        visiting.add(node)
        for child in graph.get(node, ()):
            visit(child, trail + [node])
        visiting.discard(node)
        done.add(node)

    for node in graph:
        visit(node, [])


# ------------------------------------------------------------ importing it (CI)

def test_the_package_imports_and_shares_one_copy_of_each_module(render_stack_stubbed):
    core = importlib.import_module("clipping.studio.core")
    studio = sys.modules["clipping.studio"]

    assert core.utils is sys.modules["clipping.studio.utils"]
    assert core.render_hybrid._helpers is core._helpers
    assert core.render_hybrid._wm_mod is sys.modules["clipping.studio.watermark"]
    assert studio.proses_klip is core.proses_klip
    assert not [name for name in sys.modules if name.startswith("clipping_studio_")]


def test_the_real_package_imports_where_the_render_stack_exists():
    pytest.importorskip("cv2")
    pytest.importorskip("mediapipe")
    import clipping.studio as studio
    import clipping.studio.core as core

    assert studio.proses_klip is core.proses_klip
    for name in PUBLIC:
        assert hasattr(studio, name), name

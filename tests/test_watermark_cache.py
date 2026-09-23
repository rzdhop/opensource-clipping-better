"""The watermark module is loaded once, and its renderer cache is keyed by what
the renderer depends on.

All three render paths called
`_load_studio_internal_module("watermark.py", ...)` INSIDE their per-frame
loop. That loader is `spec_from_file_location` + `exec_module` with no
sys.modules entry, so every frame re-executed the whole module -- and with it
`_renderer_cache = {}` -- and rebuilt the renderer (font or image load, then
scaling) from scratch. A 60s clip at 30 fps did that 1,800 times.

Moving the load to module level makes the cache live for the process, which
exposes the cache's key: it was `id(cfg)`. Ids are reused after an object is
freed, so a later job's cfg could be handed an earlier job's watermark. The key
is now the watermark settings plus the image file's mtime and size.

The loop guard reads source with ast; the cache tests load watermark.py by path
with the render stack mocked. Both run in the pytest-only CI environment.
"""

import ast
import importlib.util
import pathlib
import types

import pytest

STUDIO = pathlib.Path(__file__).resolve().parents[1] / "clipping" / "studio"


def _loads_inside_loops(path):
    """Calls that EXECUTE a module, inside a loop. A plain `import` statement is
    not one: after the first time it is a sys.modules lookup. The by-path loader
    is, because it never registers the module in sys.modules."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for loop in ast.walk(tree):
        if not isinstance(loop, (ast.For, ast.While, ast.AsyncFor)):
            continue
        for node in ast.walk(loop):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "id", None) == "_load_studio_internal_module":
                found.append(node.lineno)
            elif (isinstance(node.func, ast.Attribute)
                  and node.func.attr in {"exec_module", "reload"}):
                found.append(node.lineno)
    return sorted(set(found))


@pytest.mark.parametrize("path", sorted(STUDIO.glob("*.py")), ids=lambda p: p.name)
def test_no_module_is_executed_inside_a_loop(path):
    assert _loads_inside_loops(path) == []


@pytest.fixture
def watermark(render_stack_stubbed, monkeypatch):
    spec = importlib.util.spec_from_file_location("watermark_under_test", STUDIO / "watermark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    built = []

    class Renderer:
        def __init__(self, cfg):
            self.text = getattr(cfg, "watermark_text", None)
            built.append(self)

        def render(self, frame):
            return (frame, self.text)

    monkeypatch.setattr(module, "create_watermark_renderer", Renderer)
    module.built = built
    return module


def _cfg(**overrides):
    settings = dict(watermark_enabled=True, watermark_text="@rzdhop",
                    watermark_image=None, watermark_opacity=80,
                    watermark_position="bottom-right", watermark_padding=20,
                    watermark_font_size="auto", watermark_scale=15)
    settings.update(overrides)
    return types.SimpleNamespace(**settings)


def test_every_frame_of_a_clip_reuses_one_renderer(watermark):
    cfg = _cfg()
    for frame in range(100):
        watermark.apply_watermark(frame, cfg)
    assert len(watermark.built) == 1


def test_new_settings_are_never_served_an_old_renderer(watermark):
    """The same object -- so the same id() -- with different settings is what an
    id reused after garbage collection looks like to an id-keyed cache."""
    cfg = _cfg(watermark_text="first job")
    assert watermark.apply_watermark("frame", cfg) == ("frame", "first job")

    cfg.watermark_text = "second job"
    assert watermark.apply_watermark("frame", cfg) == ("frame", "second job")


def test_a_replaced_image_file_is_picked_up(watermark, tmp_path):
    image = tmp_path / "logo.png"
    image.write_bytes(b"one")
    cfg = _cfg(watermark_text=None, watermark_image=str(image))
    watermark.apply_watermark("frame", cfg)

    image.write_bytes(b"a different, longer logo")
    watermark.apply_watermark("frame", cfg)

    assert len(watermark.built) == 2


def test_a_disabled_watermark_builds_nothing(watermark):
    assert watermark.apply_watermark("frame", _cfg(watermark_enabled=False)) == "frame"
    assert watermark.built == []

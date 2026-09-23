"""--hook-source URLs: bounded, never stale, and the direct-link fallback runs.

Three defects in clipping.hook_manager.download_custom_hook:

- The download lands at a fixed name, custom_hook_override.mp4. When a run's
  download failed, `os.path.exists(local_path)` found the PREVIOUS run's file
  and returned it as this run's hook -- the same stale-cache shape as the font
  (DEC-049) and the glitch clip (DEC-050).
- gdown was tried first for every URL, and a gdown exception returned None, so
  the direct-link fallback only ran when gdown returned *without* raising: a
  plain https://.../hook.mp4 link never reached it.
- The direct download had no timeout and no size cap: a stalled server hung
  the job, and a huge file filled the disk.

gdown and requests are replaced in sys.modules, so this runs in the
pytest-only CI environment.
"""

import sys
import types

import pytest

from clipping import hook_manager

URL = "https://example.com/hooks/intro.mp4"


class FakeResponse:
    def __init__(self, status=200, chunks=(b"hook-bytes",)):
        self.status_code = status
        self._chunks = chunks

    def iter_content(self, chunk_size=1):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def cfg(tmp_path):
    return types.SimpleNamespace(hook_source=URL, outputs_dir=str(tmp_path))


@pytest.fixture
def cached(cfg, tmp_path):
    return tmp_path / "hooks_cache" / "custom_hook_override.mp4"


def install(monkeypatch, gdown_download, response=None, calls=None):
    gdown = types.ModuleType("gdown")
    gdown.download = gdown_download
    requests = types.ModuleType("requests")

    def get(url, **kwargs):
        if calls is not None:
            calls.append((url, kwargs))
        return response or FakeResponse()

    requests.get = get
    monkeypatch.setitem(sys.modules, "gdown", gdown)
    monkeypatch.setitem(sys.modules, "requests", requests)


def gdown_raises(url, output, quiet=False):
    raise RuntimeError("Cannot retrieve the public link of the file.")


def test_a_failed_download_does_not_return_the_previous_runs_hook(monkeypatch, cfg, cached):
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"LAST RUN'S HOOK")
    install(monkeypatch, gdown_raises, response=FakeResponse(status=404))

    assert hook_manager.download_custom_hook(cfg) is None
    assert not cached.exists()


def test_a_direct_link_is_tried_when_gdown_raises(monkeypatch, cfg, cached):
    install(monkeypatch, gdown_raises, response=FakeResponse(chunks=(b"abc", b"def")))

    assert hook_manager.download_custom_hook(cfg) == str(cached)
    assert cached.read_bytes() == b"abcdef"


def test_the_direct_download_has_a_timeout(monkeypatch, cfg):
    calls = []
    install(monkeypatch, gdown_raises, calls=calls)

    hook_manager.download_custom_hook(cfg)

    assert calls and calls[0][1].get("timeout") is not None


def test_an_oversized_hook_is_refused_and_removed(monkeypatch, cfg, cached):
    monkeypatch.setattr(hook_manager, "MAX_HOOK_BYTES", 10)
    install(monkeypatch, gdown_raises, response=FakeResponse(chunks=(b"x" * 8, b"x" * 8)))

    assert hook_manager.download_custom_hook(cfg) is None
    assert not cached.exists()


def test_a_stalled_download_gives_up_at_the_deadline(monkeypatch, cfg, cached):
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(hook_manager.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(hook_manager, "HOOK_DEADLINE_SECONDS", 250)
    install(monkeypatch, gdown_raises, response=FakeResponse(chunks=(b"a",) * 50))

    assert hook_manager.download_custom_hook(cfg) is None
    assert not cached.exists()


def test_a_google_drive_download_is_used_without_the_fallback(monkeypatch, cfg, cached):
    calls = []

    def gdown_writes(url, output, quiet=False):
        with open(output, "wb") as fh:
            fh.write(b"from-drive")
        return output

    install(monkeypatch, gdown_writes, calls=calls)
    cfg.hook_source = "https://drive.google.com/file/d/FILEID/view"

    assert hook_manager.download_custom_hook(cfg) == str(cached)
    assert cached.read_bytes() == b"from-drive"
    assert calls == []

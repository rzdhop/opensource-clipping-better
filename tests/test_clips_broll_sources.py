"""Clips mode fetches B-roll by source order, with credits (plan 23 stage B2).

``stock.fetch_for_clips`` is the one call the clip renderer makes; ``_pass_c`` asks
``stock.any_source_available`` before the AI writes queries. A fake opener stands in
for the network and a fake ffprobe for the local index, so nothing here touches
either. The render itself is stubbed the way test_render_temp_cleanup.py does it."""

import ast
import json
import pathlib
import random
import subprocess
import types
from types import SimpleNamespace

import pytest

from clipping import stock
from clipping.stock import base, clips, local

ROOT = pathlib.Path(__file__).resolve().parents[1]
PEXELS_KEY, PIXABAY_KEY = "test-pexels-key", "test-pixabay-SECRET"
CLIP_BYTES = b"\x00\x00\x00 ftypmp42-bytes"


class FakeOpener:
    """Search calls get a JSON answer in order; a call for a ``.mp4`` URL gets the clip bytes."""

    def __init__(self, *searches):
        self.searches, self.requests = list(searches), []

    def open(self, request, timeout=None):
        import io

        self.requests.append(request)
        if request.full_url.split("?")[0].endswith(".mp4"):
            return io.BytesIO(CLIP_BYTES)
        answer = self.searches.pop(0) if len(self.searches) > 1 else self.searches[0]
        return io.BytesIO(json.dumps(answer).encode())

    def hosts(self):
        return [r.full_url.split("/")[2] for r in self.requests]


class FakeProbe:
    def __call__(self, path):
        return {"width": 1080, "height": 1920, "duration_s": 8.0}


def pexels_page(*ids):
    return {"videos": [{"id": i, "duration": 12, "url": f"https://www.pexels.com/video/{i}/",
                        "user": {"name": "Ann", "url": "https://www.pexels.com/@ann"},
                        "video_files": [{"file_type": "video/mp4", "quality": "hd", "width": 1080, "height": 1920,
                                         "link": f"https://cdn.pexels/{i}.mp4"}]} for i in ids]}


def pixabay_page(*ids):
    return {"hits": [{"id": i, "pageURL": f"https://pixabay.com/videos/id-{i}/", "tags": "city, night", "duration": 15,
                      "user": "bob", "user_id": 7,
                      "videos": {"large": {"url": f"https://cdn.pixabay/{i}.mp4", "width": 1080, "height": 1920}}}
                     for i in ids]}


def cfg(**kw):
    values = {"pexels_api_key": "", "pixabay_api_key": "", "broll_sources": "", "broll_local_dir": ""}
    values.update(kw)
    return SimpleNamespace(**values)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """No ambient keys or folders, a fresh pool, a private cache, a fake ffprobe, no sleeping."""
    for name in ("PEXELS_API_KEY", "PIXABAY_API_KEY", "BROLL_SOURCES", "BROLL_LOCAL_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("STOCK_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(base, "DEFAULT_POOL", base.StockPool())
    monkeypatch.setattr(local.SOURCE, "_probe", FakeProbe())
    monkeypatch.setattr(local.SOURCE, "_indexes", {})
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def network(monkeypatch):
    def install(*searches):
        fake = FakeOpener(*searches)
        monkeypatch.setattr(base, "opener", lambda: fake)
        return fake
    return install


def local_folder(tmp_path, *names):
    folder = tmp_path / "broll"
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"local-bytes")
    return folder


# ------------------------------------------------------- Pexels-only == today

def test_a_pexels_only_install_searches_and_downloads_as_before(tmp_path, network):
    fake = network(pexels_page(5))
    dest = tmp_path / "temp_broll_1_0.mp4"
    path, record = stock.fetch_for_clips("city night", "9:16", str(dest), cfg(pexels_api_key=PEXELS_KEY))
    assert path == str(dest) and dest.read_bytes() == CLIP_BYTES and not (tmp_path / "temp_broll_1_0.mp4.part").exists()
    search, download = fake.requests
    assert search.full_url.startswith("https://api.pexels.com/videos/search?")
    assert search.get_header("Authorization") == PEXELS_KEY
    assert "orientation=portrait" in search.full_url and "size=large" in search.full_url
    assert download.full_url == "https://cdn.pexels/5.mp4"
    assert set(fake.hosts()) == {"api.pexels.com", "cdn.pexels"}  # nothing else was contacted
    assert (record["provider"], record["id"], record["licence"]) == ("pexels", "5", "Pexels License")
    assert record["credit"] == "Video by Ann on Pexels (https://www.pexels.com/video/5/), Pexels License"


def test_keyless_sources_are_skipped_and_nothing_is_contacted_without_any(tmp_path, network):
    fake = network(pexels_page(1))
    only_pexels = cfg(pexels_api_key=PEXELS_KEY)
    assert stock.available_sources(only_pexels) == ["pexels"] and stock.any_source_available(only_pexels)
    nothing = cfg()
    assert stock.available_sources(nothing) == [] and not stock.any_source_available(nothing)
    assert stock.fetch_for_clips("city", "9:16", str(tmp_path / "x.mp4"), nothing) is None
    assert fake.requests == [] and not (tmp_path / "x.mp4").exists()


def test_the_pool_keeps_the_old_dedupe_and_reset(tmp_path, network):
    network(pexels_page(1, 2))
    config, pool, rng = cfg(pexels_api_key=PEXELS_KEY), base.StockPool(), random.Random(3)
    picked = []
    for n in range(3):
        _, record = stock.fetch_for_clips("city", "9:16", str(tmp_path / f"b{n}.mp4"), config, pool=pool, rng=rng)
        picked.append(record["id"])
    assert set(picked[:2]) == {"1", "2"}      # no repeat while an unused clip is left
    assert picked[2] in {"1", "2"}            # then the pool resets for the query


# ------------------------------------------------------------- source order

def test_the_configured_order_decides_which_source_answers_first(tmp_path, network):
    both = {"pexels_api_key": PEXELS_KEY, "pixabay_api_key": PIXABAY_KEY}
    fake = network(pixabay_page(9))
    _, record = stock.fetch_for_clips("city", "9:16", str(tmp_path / "a.mp4"), cfg(broll_sources="pixabay,pexels", **both))
    assert record["provider"] == "pixabay" and "api.pexels.com" not in fake.hosts()

    fake = network(pexels_page(4))
    _, record = stock.fetch_for_clips("city", "9:16", str(tmp_path / "b.mp4"), cfg(broll_sources="pexels,pixabay", **both))
    assert record["provider"] == "pexels" and "pixabay.com" not in fake.hosts()


def test_a_local_clip_wins_the_default_order_and_needs_no_network(tmp_path, network):
    folder = local_folder(tmp_path, "city-night.mp4", "sea.mp4")
    (folder / "city-night.json").write_text(json.dumps({"licence": "CC0", "author": "Me"}), encoding="utf-8")
    fake = network(pexels_page(1))
    config = cfg(pexels_api_key=PEXELS_KEY, broll_local_dir=str(folder))
    assert stock.available_sources(config) == ["local", "pexels"]
    path, record = stock.fetch_for_clips("city night", "9:16", str(tmp_path / "t.mp4"), config)
    assert pathlib.Path(path).read_bytes() == b"local-bytes" and fake.requests == []
    assert (record["provider"], record["licence"], record["author"]) == ("local", "CC0", "Me")
    # a query the folder cannot answer falls through to the next source
    _, record = stock.fetch_for_clips("volcano", "9:16", str(tmp_path / "u.mp4"), config)
    assert record["provider"] == "pexels"


def test_an_empty_local_folder_is_no_source(tmp_path):
    folder = local_folder(tmp_path)
    assert not stock.any_source_available(cfg(broll_local_dir=str(folder)))
    assert stock.local_clip_count({"BROLL_LOCAL_DIR": str(folder)}) == 0


def test_want_broll_is_true_with_only_a_local_folder(tmp_path):
    from clipping.analysis import analyzer

    folder = local_folder(tmp_path, "city.mp4")
    assert stock.any_source_available(cfg(broll_local_dir=str(folder), use_broll=True))
    source = (ROOT / "clipping" / "analysis" / "analyzer.py").read_text(encoding="utf-8")
    assert 'stock.any_source_available(cfg)' in source and 'getattr(cfg, "pexels_api_key"' not in source
    assert analyzer.stock is stock
    assert not stock.any_source_available(cfg(use_broll=True))


# ------------------------------------------------------------------ credits

def test_the_credit_record_lands_in_the_clip_metadata(tmp_path, monkeypatch, network, render_stack_stubbed):
    import importlib

    core = importlib.import_module("clipping.studio.core")
    (tmp_path / "outputs").mkdir()
    folder = local_folder(tmp_path, "city.mp4")
    (folder / "city.json").write_text(json.dumps({"licence": "CC-BY", "author": "Me"}), encoding="utf-8")
    network(pexels_page(8))

    def touch(path, *a, **k):
        pathlib.Path(path).write_bytes(b"x")

    monkeypatch.setattr(core, "siapkan_font_tipografi", lambda cfg_: None)
    monkeypatch.setattr(core, "get_ts_encode_args", lambda enc, fps=30: [])
    monkeypatch.setattr(core, "_get_render_dims", lambda *a, **k: (720, 1280))
    monkeypatch.setattr(core, "buat_video_hybrid", lambda src, out, *a, **k: touch(out))
    monkeypatch.setattr(core, "build_ffmpeg_progress_cmd", lambda cmd, out: ("ffmpeg", out))
    monkeypatch.setattr(core, "run_ffmpeg_with_progress", lambda cmd, *a, **k: touch(cmd[1]))

    def fails(path, **kwargs):
        touch(path)
        raise subprocess.CalledProcessError(1, ["ffmpeg"])

    monkeypatch.setattr(core.v2_helpers, "create_white_flash_transition", fails)
    config = types.SimpleNamespace(
        file_video_asli=str(tmp_path / "talk.mp4"), outputs_dir=str(tmp_path / "outputs"),
        durasi_hook=3.0, use_hook_glitch=False, use_broll=True, hook_v2=True,
        hook_v2_items=2, white_flash_duration=0.12, video_sharpen=False,
        use_split_screen=False, use_camera_switch=False, pexels_api_key=PEXELS_KEY, pixabay_api_key="",
        broll_sources="local,pexels", broll_local_dir=str(folder))
    clip = {"start_time": 10.0, "end_time": 30.0,
            "broll_list": [{"search_query": "city", "start_time": 12.0, "end_time": 14.0},
                           {"search_query": "volcano", "start_time": 16.0, "end_time": 18.0}],
            "hook_v2": {"items": [{"start_time": 11.0, "end_time": 12.0}, {"start_time": 14.0, "end_time": 15.0}],
                        "transition": {"type": "white_flash"}}}

    result = core.proses_klip(1, clip, "9:16", None, [], config, {"name": "libx264", "args": []})

    credits = json.loads(json.dumps(result["broll_credits"]))  # the manifest is JSON
    assert [(c["provider"], c["licence"]) for c in credits] == [("local", "CC-BY"), ("pexels", "Pexels License")]
    assert credits[0]["credit"].startswith("Me, licence: CC-BY") and credits[1]["credit"].startswith("Video by Ann on Pexels")


def test_core_carries_the_credit_through_br_copy_and_the_manifest():
    tree = ast.parse((ROOT / "clipping" / "studio" / "core.py").read_text(encoding="utf-8"))
    source = ast.unparse(tree)
    assert "stock.fetch_for_clips(q, rasio, file_broll, cfg)" in source
    assert "br_copy['stock'] = fetched[1]" in source
    assert "manifest_item['broll_credits'].append(fetched[1])" in source
    assert "'broll_credits': []" in source


# ------------------------------------------------------- the folder's root

@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(clips, "CONTAINER_ROOT", str(tmp_path / "no-such-container-root"))
    monkeypatch.setattr(clips, "REPO_ROOT", tmp_path)
    (tmp_path / "broll" / "sub").mkdir(parents=True)
    return (tmp_path / "broll").resolve()


def test_the_local_dir_must_resolve_under_the_root(root, tmp_path):
    assert stock.local_root() == str(root)
    assert stock.resolve_local_dir("") == ""
    assert stock.resolve_local_dir(str(root)) == str(root)
    assert stock.resolve_local_dir(str(root / "sub")) == str(root / "sub")
    assert stock.resolve_local_dir("sub") == str(root / "sub")              # relative to the root
    for bad in (str(tmp_path), "/etc", str(root / ".." / "outputs"), "../outside", str(tmp_path / "broll-evil")):
        with pytest.raises(ValueError):
            stock.resolve_local_dir(bad)


def test_a_symlink_out_of_the_root_is_refused(root, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    with pytest.raises(ValueError):
        stock.resolve_local_dir(str(root / "link"))


def test_the_container_root_wins_when_it_exists(tmp_path, monkeypatch):
    mounted = tmp_path / "app-broll"
    mounted.mkdir()
    monkeypatch.setattr(clips, "CONTAINER_ROOT", str(mounted))
    assert stock.local_root() == str(mounted.resolve())


# --------------------------------------------------------- the settings API

@pytest.fixture
def client(tmp_path, monkeypatch, root):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from clipping.providers import budget, limits
    from web.api import worker
    from web.api.routes import settings

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("USAGE_PATH", str(tmp_path / "usage.json"))
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "spend.json"))
    monkeypatch.setenv("DISABLE_AUTH", "1")
    monkeypatch.setattr(worker, "_settings_env", {})
    limits.reset()
    budget.reset()
    app = FastAPI()
    app.include_router(settings.router)
    with TestClient(app) as test_client:
        yield test_client
    limits.reset()
    budget.reset()


def test_settings_validate_store_and_echo_the_broll_values(client, root, tmp_path):
    stored = lambda: json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))  # noqa: E731
    assert client.get("/api/settings").json()["broll_available"] is False

    refused = client.put("/api/settings", json={"broll_local_dir": "/etc"})
    assert refused.status_code == 400 and str(root) in refused.json()["detail"]
    assert client.put("/api/settings", json={"broll_sources": "local,youtube"}).status_code == 400
    assert "BROLL_LOCAL_DIR" not in stored() if (tmp_path / "settings.json").exists() else True

    (root / "city.mp4").write_bytes(b"x")
    data = client.put("/api/settings", json={
        "pixabay_api_key": PIXABAY_KEY, "broll_sources": " Pixabay , local,pixabay", "broll_local_dir": str(root)}).json()
    assert data["pixabay_api_key_set"] is True and PIXABAY_KEY not in json.dumps(data)
    assert (data["broll_sources"], data["broll_local_dir"]) == ("pixabay,local", str(root))
    assert stored()["PIXABAY_API_KEY"] == PIXABAY_KEY and stored()["BROLL_SOURCES"] == "pixabay,local"
    assert data["broll_available"] is True

    status = client.get("/api/broll/status").json()
    assert (status["sources"], status["available"], status["local_clips"]) == (["pixabay", "local"], ["pixabay", "local"], 1)
    assert status["root"] == str(root) and status["local_dir"] == str(root)

    cleared = client.put("/api/settings", json={"pixabay_api_key": "", "broll_sources": "", "broll_local_dir": ""}).json()
    assert (cleared["pixabay_api_key_set"], cleared["broll_sources"], cleared["broll_local_dir"]) == (False, "", "")


def test_the_new_keys_are_persisted_and_the_pixabay_key_is_secret():
    from web.api import settings_store

    assert {"PIXABAY_API_KEY", "BROLL_SOURCES", "BROLL_LOCAL_DIR"} <= settings_store.PERSISTED_KEYS
    assert "PIXABAY_API_KEY" in settings_store.SECRET_KEYS
    assert not {"BROLL_SOURCES", "BROLL_LOCAL_DIR"} & settings_store.SECRET_KEYS
    assert settings_store.redact({"PIXABAY_API_KEY": "k", "BROLL_SOURCES": "local"}) == {"PIXABAY_API_KEY": "***", "BROLL_SOURCES": "local"}


def test_the_config_carries_the_fields_from_the_cli_and_the_adapter(tmp_path, monkeypatch):
    from clipping.config import build_config

    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")
    monkeypatch.setenv("PIXABAY_API_KEY", "from-env")
    built = build_config(["--video", str(video), "--broll-sources", "pexels,local", "--broll-local-dir", "/x"])
    assert (built.pixabay_api_key, built.broll_sources, built.broll_local_dir) == ("from-env", "pexels,local", "/x")
    monkeypatch.setenv("BROLL_SOURCES", "pixabay")
    again = build_config(["--video", str(video)])
    assert (again.broll_sources, again.broll_local_dir) == ("pixabay", "")

    pytest.importorskip("pydantic")
    adapter = ROOT / "web" / "api" / "config_adapter.py"
    text = adapter.read_text(encoding="utf-8")
    for name in ("PIXABAY_API_KEY", "BROLL_SOURCES", "BROLL_LOCAL_DIR"):
        assert f'env.get("{name}"' in text

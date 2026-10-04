"""clipping/stock/local.py: the BROLL_LOCAL_DIR index (plan 23 stage B1), with a
fake ffprobe. No ffprobe, no network."""

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from clipping import stock
from clipping.stock import local
from clipping.stock.local import LocalIndex, rank


class FakeProbe:
    def __init__(self, width=1080, height=1920, duration=8.0, fail=()):
        self.size, self.duration, self.fail, self.paths = (width, height), duration, set(fail), []

    def __call__(self, path):
        self.paths.append(os.path.basename(path))
        if os.path.basename(path) in self.fail:
            return None
        return {"width": self.size[0], "height": self.size[1], "duration_s": self.duration}


def touch(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def names(clips):
    return sorted(os.path.basename(c.url) for c in clips)


def test_only_mp4_mov_webm_are_indexed_and_filename_tokens_become_keywords(tmp_path):
    for name in ("City_Night-lights.mp4", "beach.MOV", "forest.webm", "notes.txt", "clip.mkv", ".hidden.mp4"):
        touch(tmp_path / name)
    touch(tmp_path / "sub" / "red-cars.mp4")
    clips = LocalIndex(tmp_path, probe=FakeProbe()).scan()
    assert names(clips) == ["City_Night-lights.mp4", "beach.MOV", "forest.webm", "red-cars.mp4"]
    city = next(c for c in clips if c.url.endswith("City_Night-lights.mp4"))
    assert city.provider == "local" and len(city.id) == 16 and city.key == f"local:{city.id}"
    assert city.tags == ("city", "night", "light")  # plural stripped: lights -> light
    assert (city.width, city.height, city.duration_s, city.licence) == (1080, 1920, 8.0, "unknown")


def test_a_sidecar_adds_keywords_and_the_licence_and_beats_the_folder_index(tmp_path):
    touch(tmp_path / "a.mp4")
    touch(tmp_path / "b.mp4")
    touch(tmp_path / "c.mp4")
    (tmp_path / "a.json").write_text(json.dumps({
        "keywords": ["Harbour", "boats"], "licence": "CC-BY 4.0", "author": "Me", "source_url": "https://example.org/a"}))
    (tmp_path / "index.json").write_text(json.dumps({
        "a.mp4": {"licence": "ignored", "keywords": "ignored"},
        "b.mp4": {"keywords": "sunrise, mountains", "licence": "CC0", "source_url": "javascript:alert(1)"}}))
    by_name = {os.path.basename(c.url): c for c in LocalIndex(tmp_path, probe=FakeProbe()).scan()}
    a, b, c = by_name["a.mp4"], by_name["b.mp4"], by_name["c.mp4"]
    assert a.tags == ("harbour", "boat") and (a.licence, a.author, a.page_url) == ("CC-BY 4.0", "Me", "https://example.org/a")
    assert b.tags == ("b", "sunrise", "mountain") and b.licence == "CC0" and b.page_url == ""  # only http(s) kept
    assert c.licence == "unknown"
    assert "warning" in stock.credit_record(c) and "warning" not in stock.credit_record(a)


def test_symlinks_are_refused(tmp_path):
    root, outside = tmp_path / "root", tmp_path / "outside"
    touch(root / "real.mp4")
    touch(outside / "secret.mp4")
    (root / "link.mp4").symlink_to(outside / "secret.mp4")
    (root / "linked-dir").symlink_to(outside, target_is_directory=True)
    touch(root / "city.mp4")
    (root / "city.json").symlink_to(outside / "secret.mp4")  # a symlinked sidecar is ignored too
    probe = FakeProbe()
    clips = LocalIndex(root, probe=probe).scan()
    assert names(clips) == [os.path.realpath(root / "city.mp4").rsplit("/", 1)[1], "real.mp4"]
    assert "secret.mp4" not in probe.paths and "link.mp4" not in probe.paths


def test_a_real_path_outside_the_root_is_refused(tmp_path):
    root = tmp_path / "root"
    (tmp_path / "root-evil").mkdir()
    root.mkdir()
    real = os.path.realpath(root)
    assert local.inside_root(real, str(root / "a.mp4"))
    assert local.inside_root(real, str(root))
    assert not local.inside_root(real, str(tmp_path / "root-evil" / "a.mp4"))  # a prefix is not containment
    assert not local.inside_root(real, str(root / ".." / "a.mp4"))
    touch(tmp_path / "root-evil" / "a.mp4")
    (root / "esc").symlink_to(tmp_path / "root-evil", target_is_directory=True)
    assert not local.inside_root(real, str(root / "esc" / "a.mp4"))
    assert LocalIndex(root, probe=FakeProbe()).scan() == []


def test_unreadable_videos_are_skipped(tmp_path):
    touch(tmp_path / "good.mp4")
    touch(tmp_path / "broken.mp4")
    clips = LocalIndex(tmp_path, probe=FakeProbe(fail={"broken.mp4"})).scan()
    assert names(clips) == ["good.mp4"]


def test_the_probe_cache_is_keyed_by_size_and_mtime(tmp_path):
    root, cache = tmp_path / "root", tmp_path / "cache" / "idx.json"
    clip_path = touch(root / "city.mp4", b"1234")
    probe = FakeProbe()
    index = LocalIndex(root, probe=probe, cache_file=str(cache))
    index.scan()
    index.scan()
    assert probe.paths == ["city.mp4"]  # the second scan read the cache
    again = FakeProbe()
    LocalIndex(root, probe=again, cache_file=str(cache)).scan()
    assert again.paths == []  # the disk cache survives a new process
    stat = clip_path.stat()
    os.utime(clip_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))  # edited: mtime moves
    changed = FakeProbe(width=1920, height=1080)
    [clip] = LocalIndex(root, probe=changed, cache_file=str(cache)).scan()
    assert changed.paths == ["city.mp4"] and (clip.width, clip.height) == (1920, 1080)
    clip_path.write_bytes(b"12345678")  # a size change invalidates as well
    resized = FakeProbe()
    LocalIndex(root, probe=resized, cache_file=str(cache)).scan()
    assert resized.paths == ["city.mp4"]


def stem_of(clip):
    return os.path.splitext(os.path.basename(clip.url))[0]


def clips_for(*specs):
    return [stock.StockClip("local", str(i), f"/b/{n}.mp4", w, h, d, tags=tuple(t))
            for i, (n, t, w, h, d) in enumerate(specs)]


def test_ranking_needs_one_token_strips_plurals_and_prefers_the_orientation():
    clips = clips_for(("land-city", ["city", "night"], 1920, 1080, 9), ("port-city", ["city"], 1080, 1920, 9),
                      ("port-sea", ["sea"], 1080, 1920, 9), ("port-both", ["city", "night"], 1080, 1920, 9))
    ranked = local.rank(clips, "Night cities", aspect="9:16")
    assert [stem_of(c) for c in ranked] == ["port-both", "port-city", "land-city"]  # sea: no overlap
    assert [stem_of(c) for c in local.rank(clips, "night cities", aspect="16:9")] == ["land-city", "port-both", "port-city"]
    assert local.rank(clips, "the of", aspect="9:16") == []  # stopwords alone match nothing
    assert local.rank(clips, "city", aspect="9:16", min_duration_s=20) == []
    assert [local.stem(t) for t in ("cities", "boxes", "cars", "glass", "bus", "beaches")] == ["city", "box", "car", "glass", "bus", "beach"]


def test_search_finds_a_local_clip_and_the_pool_cycles(tmp_path, monkeypatch):
    root = tmp_path / "broll"
    touch(root / "harbour-boats.mp4")
    touch(root / "harbour-sunset.mp4")
    source = local.LocalSource(probe=FakeProbe())
    env = {"BROLL_LOCAL_DIR": str(root), "STOCK_CACHE_DIR": str(tmp_path / "cache")}
    assert source.available(env) and not source.available({"BROLL_LOCAL_DIR": str(tmp_path / "missing")})
    pool = stock.StockPool()
    picks = [stock.search("harbour", aspect="9:16", sources=[source], env=env, pool=pool) for _ in range(3)]
    assert [os.path.basename(p.url) for p in picks] == ["harbour-boats.mp4", "harbour-sunset.mp4", "harbour-boats.mp4"]
    assert stock.search("volcano", aspect="9:16", sources=[source], env=env, pool=pool) is None
    assert stock.search("harbour", aspect="9:16", sources=["local"], env={}, pool=pool) is None  # no folder set


def test_probe_video_reads_ffprobe_json_and_applies_rotation(tmp_path):
    def fake_run(stdout, code=0):
        return lambda argv, **kw: SimpleNamespace(stdout=stdout, returncode=code, argv=argv)

    data = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                         "side_data_list": [{"rotation": -90}]}], "format": {"duration": "7.5"}}
    assert local.probe_video("x.mp4", run=fake_run(json.dumps(data))) == {"width": 1080, "height": 1920, "duration_s": 7.5}
    data["streams"][0].pop("side_data_list")
    data["streams"][0]["tags"] = {"rotate": "90"}
    assert local.probe_video("x.mp4", run=fake_run(json.dumps(data)))["width"] == 1080
    data["streams"][0]["tags"] = {}
    assert local.probe_video("x.mp4", run=fake_run(json.dumps(data)))["width"] == 1920
    assert local.probe_video("x.mp4", run=fake_run("not json")) is None
    assert local.probe_video("x.mp4", run=fake_run(json.dumps({"streams": [{"codec_type": "audio"}]}))) is None
    assert local.probe_video("x.mp4", run=fake_run("{}", code=1)) is None

    def missing(argv, **kw):
        raise FileNotFoundError("ffprobe")

    assert local.probe_video("x.mp4", run=missing) is None


def test_a_multi_word_sidecar_keyword_matches_each_of_its_words(tmp_path):
    # review finding (B1): "night city street" must become three tags, not one
    touch(tmp_path / "street.mp4")
    (tmp_path / "street.json").write_text(json.dumps({"keywords": ["night City street"]}), encoding="utf-8")
    clips = LocalIndex(tmp_path, probe=FakeProbe()).scan()
    assert set(clips[0].tags) >= {"night", "city", "street"}
    assert [c.url for c in rank(clips, "city at night", aspect="9:16")] == [clips[0].url]


def test_an_unreadable_file_is_probed_once_and_cached_as_unreadable(tmp_path):
    touch(tmp_path / "good.mp4")
    touch(tmp_path / "bad.mp4")
    probe = FakeProbe()
    index = LocalIndex(tmp_path, probe=lambda p: None if p.endswith("bad.mp4") else probe(p),
                       cache_file=str(tmp_path / ".cache" / "idx.json"))
    assert names(index.scan()) == ["good.mp4"]
    first = index.probe_calls
    assert names(index.scan()) == ["good.mp4"]
    assert index.probe_calls == first  # bad.mp4 is not probed again

"""Phase-4 episode files on disk (AI Story phase 4, stage 3; spec 2, 2.8-2.11).

``clipping/aistory/store.py`` gains what the assets, render and metadata
steps keep:

- three episode documents, ``assets.json``, ``render_manifest.json`` and
  ``metadata_pack.json``, read and written through
  ``read_episode_doc``/``write_episode_doc`` like the script and the
  storyboard: validated on every read and write, stamped, atomic, and never
  touching ``story.json`` or the index (RC-E2);
- the asset kind ``shots`` (``assets/shots/shot_NN.<png|jpg|jpeg|webp>``, NN
  the shot's own number), ``voice`` unchanged;
- ``episode_render_dir`` (``episodes/epNN/render/`` and its allowlisted
  folders) and ``gen_cache_dir`` (``<story>/cache/gen/``), each level a real
  directory: a symlink anywhere, the leaf included, is refused and never
  followed;
- ``episode_file_path`` for the few files at the top of an episode's folder
  (``EPISODE_FILE_NAMES``); any other name is refused before a path is built;
- story delete removes ``cache/`` and every ``render/`` with the story's
  folder, and never follows a link out of it.

A phase-3 script and storyboard still read and validate (RC-A8).

Stdlib + pytest only (DEC-012). Every file lives under ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import pathlib
import stat

import pytest

from clipping.aistory import schemas, store

NOW = "2026-09-28T10:00:00+00:00"
LATER = "2026-09-28T11:00:00+00:00"
LATEST = "2026-09-28T12:00:00+00:00"
SHA_A = "a" * 64
SHA_B = "b" * 64

SCRIPT = "script.json"
STORYBOARD = "storyboard.json"
ASSETS = "assets.json"
MANIFEST = "render_manifest.json"
PACK = "metadata_pack.json"
NEW_DOCS = (ASSETS, MANIFEST, PACK)

STORY_ID = "0123456789ab"


# ---------------------------------------------------------------- builders

def _script(ep=1):
    def line(line_id):
        return {"line_id": line_id, "speaker": "char_kiwilo", "text": "A short line here.", "emotion": "neutral",
                "delivery": "calm",
                "timing": {"source": "estimated", "duration_s": 1.2, "text_hash": "0123456789abcdef",
                           "voice": None, "audio": None}}

    def scene(scene_id, function, line_id):
        return {"scene_id": scene_id, "function": function, "place_id": "place_beach_camp", "time_variant": "day",
                "characters": ["char_kiwilo"], "props": [], "summary": "Something happens.", "emotion": "neutral",
                "target_duration_s": 5.0, "lines": [line(line_id)], "sfx_cues": [], "on_screen_text": None,
                "state": "written", "source": "E2", "rev": 1}

    return {
        "$schema": "episode_script_v1", "ep": ep, "title": "Test Episode", "language": "fr",
        "template_id": "serial_60s_v1", "hook": {"on_screen_text": None},
        "scenes": [scene("s01", "hook", "l04"), scene("s02", "cliffhanger", "l08")],
        "cliffhanger": {"scene_id": "s02", "reveal": None, "cut_to_black": True},
        "next_episode_teaser": None, "timing": None, "consistency_report": None,
        "approved_anyway": None, "approved_at": None, "rev": 1, "created_at": NOW, "updated_at": NOW,
    }


def _storyboard(ep=1, assets=None):
    """A phase-3 board: each shot's assets hold the five spec keys only."""
    def shot(n, scene_id, line_id):
        return {
            "shot_id": f"sh{n:02d}", "scene_id": scene_id, "order": n,
            "framing": "medium_single", "camera_motion": "hold", "modifiers": [],
            "subject_tags": ["@char_kiwilo", "#place_beach_camp:day"], "action": "Something happens on screen.",
            "lines": [line_id], "image_prompt": "a fully resolved prompt", "negative_prompt": "no text",
            "prompt_override": None, "reference_images": ["characters/char_kiwilo/refs/portrait.png"],
            "consistency": "references", "duration_s": 3.0, "keep_still": False,
            "motion": {"type": "hold", "zoom_from": 1.0, "zoom_to": 1.0, "pan": "none"},
            "video_prompt": None,
            "assets": {"image": None, "video": None, "seed": None, "provider": None, "approved": False},
        }

    doc = {
        "$schema": "storyboard_v1", "ep": ep,
        "shots": [shot(1, "s01", "l04"), shot(2, "s02", "l08")],
        "transitions": [{"after": "sh01", "type": "cut", "duration_s": 0.0}],
        "scenes": {sid: {"source": "t1", "script_rev": 1, "stale": False} for sid in ("s01", "s02")},
        "resolved_from": {}, "approved_at": None, "rev": 1, "created_at": NOW, "updated_at": NOW,
    }
    if assets is not None:
        doc["shots"][0]["assets"] = assets
    return doc


def _assets(ep=1):
    return {
        "$schema": "episode_assets_v1", "ep": ep,
        "lines": {"l04": {"words_source": "provider"}, "l08": {"words_source": "even_split"}},
        "sfx": [{"scene_id": "s01", "at": "start", "cue": "waves_soft", "pack": "soap",
                 "file": "assets/sfx/soap/waves_soft.wav", "offset_s": 0.0, "state": "resolved"}],
        "bgm": {"mood": "tropical_drama", "dominant_emotion": "neutral", "weights_s": {"neutral": 10.0},
                "file": "assets/bgm/drama/track.mp3", "sha256": SHA_A, "licence": "self-made"},
        "approved": None, "created_at": NOW, "updated_at": NOW,
    }


def _manifest(ep=1):
    return {
        "$schema": "render_manifest_v1", "ep": ep, "profile": "final",
        "params": {"subtitles": "word_pop", "encoder": "libx264"},
        "ffmpeg": {"version": "6.1.1-3ubuntu5", "machine": "aarch64"},
        "font": None,
        "inputs": [{"role": "shot", "id": "sh01", "source": "episodes/ep01/assets/shots/shot_01.png",
                    "staged": f"in/{SHA_A}.png", "sha256": SHA_A}],
        "stages": [{"id": "S:sh01", "kind": "shot", "argv": ["ffmpeg", "-i", f"in/{SHA_A}.png", "out.mp4"],
                    "cache_key": SHA_B, "state": "running", "output": None, "output_sha256": None,
                    "seconds": None, "stderr_tail": None}],
        "output": None,
        "timings": {"started_at": NOW, "finished_at": None, "total_s": None},
        "warnings": [], "created_at": NOW, "updated_at": NOW,
    }


def _pack(ep=1):
    entry = {"title": "Kiwilo a menti !", "description": "La suite demain ?",
             "hashtags": ["#fruitdrama", "#telenovela", "#kiwi"], "hook_text": "Il a menti.",
             "pinned_comment": "La suite demain ? PARTIE 2 →", "cover": "cover.jpg", "written_at": NOW,
             "title_en": "Kiwilo lied!", "hashtags_en": ["#fruitdrama", "#soap", "#kiwi"]}
    return {
        "$schema": "metadata_pack_v1", "ep": ep, "language": "fr", "script_rev": 1, "render_sha256": SHA_B,
        "platforms": {"tiktok": dict(entry), "shorts": dict(entry)},
        "created_at": NOW, "updated_at": NOW,
    }


BUILD = {SCRIPT: _script, STORYBOARD: _storyboard, ASSETS: _assets, MANIFEST: _manifest, PACK: _pack}


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def outputs(tmp_path):
    path = tmp_path / "outputs"
    path.mkdir()
    return path


@pytest.fixture
def logs():
    return []


@pytest.fixture
def stories(outputs, logs):
    return store.StoryStore(str(outputs), on_log=logs.append)


@pytest.fixture
def story_id(stories):
    return stories.create(language="fr", now=NOW)["story_id"]


def _story_dir(outputs, story_id):
    return outputs / "stories" / story_id


def _episode_dir(outputs, story_id, ep=1):
    return _story_dir(outputs, story_id) / "episodes" / f"ep{ep:02d}"


def _temp_files(outputs):
    return [p for p in outputs.rglob("*") if p.name.startswith(".story-") or p.suffix == ".tmp"]


def _symlink(target, link):
    try:
        os.symlink(target, link, target_is_directory=pathlib.Path(target).is_dir())
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a symlink here")


def _snapshot(folder):
    return {str(p.relative_to(folder)): (None if p.is_dir() else p.read_bytes())
            for p in sorted(folder.rglob("*"))}


# ---------------------------------------------------------------- constants

def test_the_builders_validate():
    for name, build in BUILD.items():
        assert store.EPISODE_DOC_VALIDATORS[name](build()) == [], name


def test_the_new_documents_are_episode_documents():
    assert store.EPISODE_DOC_NAMES[:2] == (SCRIPT, STORYBOARD)
    assert set(NEW_DOCS) <= set(store.EPISODE_DOC_NAMES)
    assert set(store.EPISODE_DOC_VALIDATORS) == set(store.EPISODE_DOC_NAMES)
    assert store.EPISODE_DOC_VALIDATORS[ASSETS] is schemas.episode_assets_errors
    assert store.EPISODE_DOC_VALIDATORS[MANIFEST] is schemas.render_manifest_errors
    assert store.EPISODE_DOC_VALIDATORS[PACK] is schemas.metadata_pack_errors
    assert set(store.EPISODE_DOCS_WITH_TIMESTAMPS) == set(store.EPISODE_DOC_NAMES)
    # Neither story documents nor episode files: each name has one way in.
    assert not set(store.EPISODE_DOC_NAMES) & set(store.DOC_NAMES)
    assert not set(store.EPISODE_DOC_NAMES) & set(store.EPISODE_FILE_NAMES)


def test_the_asset_kinds_gain_shots_and_keep_voice():
    # Phase 6 stage 7 adds the shot clips after them.
    assert store.EPISODE_ASSET_KINDS == ("voice", "shots", "clips")
    assert set(store.EPISODE_ASSET_NAME_PATTERNS) == set(store.EPISODE_ASSET_KINDS)
    assert store.EPISODE_ASSET_NAME_PATTERNS["voice"].pattern == r"^line_[0-9]{2}\.(mp3|wav|json)$"
    assert store.EPISODE_ASSET_NAME_PATTERNS["shots"].pattern == schemas.SHOT_IMAGE_NAME_PATTERN


def test_the_closed_file_and_folder_lists():
    assert store.EPISODE_FILE_NAMES == ("episode_final.mp4", "subtitles.ass", "cover.jpg", "cost_ledger.json")
    assert store.EPISODE_RENDER_SUBDIRS == ("in", "fonts", "cache", "stems", "logs")
    assert store.GEN_CACHE_DIRS == ("cache", "gen")


# ---------------------------------------------------------------- tripwire

class _Tripwire:
    def __init__(self, name):
        self._name = name

    def __getattr__(self, attr):
        raise AssertionError(f"filesystem touched: {self._name}.{attr}")


@pytest.fixture
def tripwired(outputs, monkeypatch):
    stories = store.StoryStore(str(outputs), on_log=lambda _: None)
    for module in ("os", "shutil", "tempfile"):
        monkeypatch.setattr(store, module, _Tripwire(module))

    def no_open(*args, **kwargs):
        raise AssertionError("filesystem touched: open")

    monkeypatch.setattr(store, "open", no_open, raising=False)
    return stories


def _every_phase4_call(stories, story_id, ep):
    calls = []
    for create in (False, True):
        calls += [
            lambda create=create: stories.episode_render_dir(story_id, ep, create=create),
            lambda create=create: stories.episode_render_dir(story_id, ep, "cache", create=create),
            lambda create=create: stories.episode_file_path(story_id, ep, "episode_final.mp4", create=create),
            lambda create=create: stories.episode_asset_path(story_id, ep, "shots", "shot_01.png", create=create),
        ]
    for name in NEW_DOCS:
        calls += [
            lambda name=name: stories.read_episode_doc(story_id, ep, name),
            lambda name=name: stories.write_episode_doc(story_id, ep, name, BUILD[name](), now=LATER),
        ]
    return calls


BAD_EPS = [0, 100, True, "1", 1.0, None]


@pytest.mark.parametrize("ep", BAD_EPS)
def test_a_bad_episode_is_refused_before_any_filesystem_access(tripwired, ep):
    for call in _every_phase4_call(tripwired, STORY_ID, ep):
        with pytest.raises(KeyError):
            call()


@pytest.mark.parametrize("story_id", ["../x", "ABCDEF123456", "0123456789ab\n", "", None, 7])
def test_a_bad_story_id_is_refused_before_any_filesystem_access(tripwired, story_id):
    for call in _every_phase4_call(tripwired, story_id, 1):
        with pytest.raises(KeyError):
            call()
    for create in (False, True):
        with pytest.raises(KeyError):
            tripwired.gen_cache_dir(story_id, create=create)


BAD_SHOT_NAMES = ["shot_1.png", "../x", "shot_01.gif", "shot_00.png", "shot_001.png", "SHOT_01.png",
                  "shot_01.PNG", "shot_01.png\n", "shot_01", "shots/shot_01.png", "/tmp/shot_01.png",
                  ".shot_01.png", "shot_0١.png", "line_01.mp3", "shot_01.png.exe", "", None, 7,
                  # Walk follow-up F5: ids may pass sh99 (shot_112.png is a name), never four digits.
                  "shot_1000.png", "shot_012.png"]


@pytest.mark.parametrize("name", BAD_SHOT_NAMES)
def test_an_unknown_shot_name_is_refused_before_any_filesystem_access(tripwired, name):
    for create in (False, True):
        with pytest.raises(KeyError):
            tripwired.episode_asset_path(STORY_ID, 1, "shots", name, create=create)


def test_a_voice_name_is_not_a_shot_name_and_back(tripwired):
    with pytest.raises(KeyError):
        tripwired.episode_asset_path(STORY_ID, 1, "voice", "shot_01.png")
    with pytest.raises(KeyError):
        tripwired.episode_asset_path(STORY_ID, 1, "shots", "line_01.mp3")


BAD_FILE_NAMES = [SCRIPT, STORYBOARD, ASSETS, MANIFEST, PACK, "story.json", "render", "assets",
                  "episode_final.mkv", "episode_pre.mkv", "cover.png", "../episode_final.mp4",
                  "render/episode_final.mp4", "episode_final.mp4\n", "Episode_final.mp4", "", None, 7]


@pytest.mark.parametrize("name", BAD_FILE_NAMES)
def test_a_name_outside_the_file_allowlist_is_refused_before_any_filesystem_access(tripwired, name):
    for create in (False, True):
        with pytest.raises(KeyError):
            tripwired.episode_file_path(STORY_ID, 1, name, create=create)


@pytest.mark.parametrize("sub", ["tmp", "..", "in/", "cache/gen", "", "CACHE", 0, False, ["in"]])
def test_a_render_folder_outside_the_allowlist_is_refused_before_any_filesystem_access(tripwired, sub):
    for create in (False, True):
        with pytest.raises(KeyError):
            tripwired.episode_render_dir(STORY_ID, 1, sub, create=create)


def test_the_tripwire_does_trip(tripwired):
    with pytest.raises(AssertionError):
        tripwired.gen_cache_dir(STORY_ID)
    with pytest.raises(AssertionError):
        tripwired.episode_file_path(STORY_ID, 1, "cover.jpg")


# --------------------------------------------------------- the documents

@pytest.mark.parametrize("name", NEW_DOCS)
def test_a_new_document_round_trips_with_both_stamps(stories, outputs, story_id, name):
    doc = BUILD[name]()
    assert stories.read_episode_doc(story_id, 1, name) is None

    written = stories.write_episode_doc(story_id, 1, name, doc, now=LATER)

    assert written == {**doc, "updated_at": LATER}
    assert (written["created_at"], written["updated_at"]) == (NOW, LATER)
    assert doc["updated_at"] == NOW  # the caller's document is not changed
    assert stories.read_episode_doc(story_id, 1, name) == written
    path = _episode_dir(outputs, story_id) / name
    assert json.loads(path.read_text(encoding="utf-8")) == written
    assert list(json.loads(path.read_text(encoding="utf-8"))) == list(doc)  # readable field order
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644
    assert _temp_files(outputs) == []

    again = stories.write_episode_doc(story_id, 1, name, written, now=LATEST)
    assert (again["created_at"], again["updated_at"]) == (NOW, LATEST)


def _assets_resolved_without_file(doc):
    doc["sfx"][0]["file"] = None


def _manifest_cached_final(doc):
    doc["stages"][0].update(kind="final", state="cached", output="x.mkv", output_sha256=SHA_A, seconds=1.0)


def _pack_english_fields_missing(doc):
    del doc["platforms"]["tiktok"]["title_en"]


def _extra_key(doc):
    doc["notes"] = "hand-written"


BROKEN = {
    "assets: resolved cue without its file": (ASSETS, _assets_resolved_without_file,
                                              "$.sfx[0].file: a 'resolved' cue has a file and a 'missing' "
                                              "one has none"),
    "assets: extra key": (ASSETS, _extra_key, "$.notes: additional property not allowed"),
    "manifest: a final pass is never cached": (MANIFEST, _manifest_cached_final,
                                               "$.stages[0].state: a 'final' stage is never cached "
                                               "(['shot', 'end_card'])"),
    "manifest: extra key": (MANIFEST, _extra_key, "$.notes: additional property not allowed"),
    "pack: French without title_en": (PACK, _pack_english_fields_missing,
                                      "$.platforms.tiktok.title_en: required for a French story"),
    "pack: extra key": (PACK, _extra_key, "$.notes: additional property not allowed"),
}


@pytest.mark.parametrize("label", list(BROKEN), ids=list(BROKEN))
def test_an_invalid_new_document_names_its_rule_and_writes_nothing(stories, outputs, story_id, label):
    name, mutate, message = BROKEN[label]
    doc = BUILD[name]()
    mutate(doc)

    with pytest.raises(schemas.SchemaError) as caught:
        stories.write_episode_doc(story_id, 1, name, doc, now=LATER)

    assert caught.value.errors == [message]
    assert caught.value.name == f"outputs/stories/{story_id}/episodes/ep01/{name}"
    assert not (_story_dir(outputs, story_id) / "episodes").exists()
    assert _temp_files(outputs) == []


@pytest.mark.parametrize("label", list(BROKEN), ids=list(BROKEN))
def test_an_invalid_new_document_leaves_the_old_file_intact(stories, outputs, story_id, label):
    name, mutate, message = BROKEN[label]
    stories.write_episode_doc(story_id, 1, name, BUILD[name](), now=LATER)
    path = _episode_dir(outputs, story_id) / name
    before = path.read_bytes()
    doc = BUILD[name]()
    mutate(doc)

    with pytest.raises(schemas.SchemaError):
        stories.write_episode_doc(story_id, 1, name, doc, now=LATEST)

    assert path.read_bytes() == before
    assert _temp_files(outputs) == []


@pytest.mark.parametrize("name", NEW_DOCS)
def test_an_invalid_new_document_on_disk_raises_and_is_not_repaired(stories, story_id, name):
    folder = pathlib.Path(stories.episode_dir(story_id, 1, create=True))
    doc = BUILD[name]()
    _extra_key(doc)
    (folder / name).write_text(json.dumps(doc), encoding="utf-8")
    before = (folder / name).read_bytes()

    with pytest.raises(schemas.SchemaError) as caught:
        stories.read_episode_doc(story_id, 1, name)

    assert caught.value.errors == ["$.notes: additional property not allowed"]
    assert (folder / name).read_bytes() == before


@pytest.mark.parametrize("name", NEW_DOCS)
def test_a_new_document_of_another_episode_is_refused(stories, outputs, story_id, name):
    with pytest.raises(schemas.SchemaError) as caught:
        stories.write_episode_doc(story_id, 1, name, BUILD[name](ep=2), now=LATER)
    assert caught.value.errors == ["$.ep: 2 does not match its folder 'ep01'"]
    assert not (_story_dir(outputs, story_id) / "episodes").exists()


@pytest.mark.parametrize("name", NEW_DOCS)
def test_a_symlinked_new_document_is_never_read_or_written(stories, story_id, tmp_path, name):
    secret = tmp_path / "secret.json"
    secret.write_text(json.dumps(BUILD[name]()), encoding="utf-8")
    before = secret.read_bytes()
    folder = pathlib.Path(stories.episode_dir(story_id, 1, create=True))
    _symlink(secret, folder / name)

    with pytest.raises(schemas.SchemaError):
        stories.read_episode_doc(story_id, 1, name)
    with pytest.raises(ValueError):
        stories.write_episode_doc(story_id, 1, name, BUILD[name](), now=LATER)

    assert os.path.islink(folder / name)
    assert secret.read_bytes() == before


def test_a_board_with_the_new_shot_keys_round_trips(stories, story_id):
    record = {"image": "assets/shots/shot_01.webp", "video": None, "seed": 42, "provider": "cloudflare",
              "approved": True, "model": "flux-1-schnell", "consistency": "prompt_only", "route": "free",
              "prompt_hash": SHA_A, "locked": True, "note": "darker", "generated_at": NOW, "est_usd": 0.0,
              "cache_key": SHA_B, "pending": {"seed": 7, "note": None, "requested_at": NOW}}
    written = stories.write_episode_doc(story_id, 1, STORYBOARD, _storyboard(assets=record), now=LATER)
    assert stories.read_episode_doc(story_id, 1, STORYBOARD)["shots"][0]["assets"] == record == \
        written["shots"][0]["assets"]

    with pytest.raises(schemas.SchemaError) as caught:
        stories.write_episode_doc(story_id, 1, STORYBOARD, _storyboard(assets={**record, "route": "api"}),
                                  now=LATEST)
    assert caught.value.errors == ["$.shots[0].assets.route: 'api' is not one of ['free', 'local', 'paid']"]


def test_a_phase3_script_and_board_written_as_phase3_did_still_read(stories, story_id):
    """The bytes a phase-3 store wrote (the five-key assets), read by this one."""
    folder = pathlib.Path(stories.episode_dir(story_id, 1, create=True))
    for name in (SCRIPT, STORYBOARD):
        (folder / name).write_text(json.dumps(BUILD[name](), ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
        assert stories.read_episode_doc(story_id, 1, name) == BUILD[name]()


# ------------------------------------------------------------ shot assets

@pytest.mark.parametrize("name", ["shot_01.png", "shot_02.jpg", "shot_10.jpeg", "shot_60.webp", "shot_99.png"])
def test_an_accepted_shot_asset_path(stories, outputs, story_id, name):
    with pytest.raises(KeyError):
        stories.episode_asset_path(story_id, 1, "shots", name)

    path = stories.episode_asset_path(story_id, 1, "shots", name, create=True)

    folder = _episode_dir(outputs, story_id) / "assets" / "shots"
    assert path == os.path.join(os.path.realpath(folder), name)
    assert folder.is_dir()
    assert not os.path.lexists(path)
    pathlib.Path(path).write_bytes(b"\x89PNG")
    assert stories.episode_asset_path(story_id, 1, "shots", name) == path


def test_a_symlinked_shot_image_is_refused(stories, story_id, tmp_path):
    secret = tmp_path / "secret.png"
    secret.write_bytes(b"SECRET")
    folder = pathlib.Path(stories.episode_asset_path(story_id, 1, "shots", "shot_01.png", create=True)).parent
    _symlink(secret, folder / "shot_01.png")
    for create in (False, True):
        with pytest.raises(KeyError):
            stories.episode_asset_path(story_id, 1, "shots", "shot_01.png", create=create)
    assert secret.read_bytes() == b"SECRET"


# ------------------------------------------------------------ episode files

@pytest.mark.parametrize("name", ["episode_final.mp4", "subtitles.ass", "cover.jpg", "cost_ledger.json"])
def test_an_allowlisted_episode_file_path(stories, outputs, story_id, name):
    with pytest.raises(KeyError):
        stories.episode_file_path(story_id, 1, name)
    assert not (_story_dir(outputs, story_id) / "episodes").exists()

    path = stories.episode_file_path(story_id, 1, name, create=True)

    assert path == os.path.join(os.path.realpath(_episode_dir(outputs, story_id)), name)
    assert not os.path.lexists(path)
    pathlib.Path(path).write_bytes(b"DATA")
    assert stories.episode_file_path(story_id, 1, name) == path


def test_a_symlinked_or_directory_episode_file_is_refused(stories, story_id, tmp_path):
    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"SECRET")
    folder = pathlib.Path(stories.episode_dir(story_id, 1, create=True))
    _symlink(secret, folder / "episode_final.mp4")
    (folder / "cover.jpg").mkdir()
    _symlink(tmp_path / "missing.ass", folder / "subtitles.ass")

    for name in ("episode_final.mp4", "cover.jpg", "subtitles.ass"):
        for create in (False, True):
            with pytest.raises(KeyError):
                stories.episode_file_path(story_id, 1, name, create=create)
    assert secret.read_bytes() == b"SECRET"
    assert not (tmp_path / "missing.ass").exists()


# ------------------------------------------------------------ render/ and cache/gen/

def test_episode_render_dir_needs_the_folder_unless_asked_to_create_it(stories, outputs, story_id):
    with pytest.raises(KeyError):
        stories.episode_render_dir(story_id, 1)
    assert not (_story_dir(outputs, story_id) / "episodes").exists()

    made = stories.episode_render_dir(story_id, 3, create=True)

    assert made == os.path.realpath(_episode_dir(outputs, story_id, 3) / "render")
    assert stories.episode_render_dir(story_id, 3) == made
    for sub in store.EPISODE_RENDER_SUBDIRS:
        with pytest.raises(KeyError):
            stories.episode_render_dir(story_id, 3, sub)
        folder = stories.episode_render_dir(story_id, 3, sub, create=True)
        assert folder == os.path.join(made, sub)
        assert os.path.isdir(folder)


def test_gen_cache_dir_needs_the_folder_unless_asked_to_create_it(stories, outputs, story_id):
    with pytest.raises(KeyError):
        stories.gen_cache_dir(story_id)
    assert not (_story_dir(outputs, story_id) / "cache").exists()

    made = stories.gen_cache_dir(story_id, create=True)

    assert made == os.path.realpath(_story_dir(outputs, story_id) / "cache" / "gen")
    assert os.path.isdir(made)
    assert stories.gen_cache_dir(story_id) == made


def test_an_unknown_story_gets_no_render_or_cache_folder(stories, outputs):
    for call in (lambda: stories.episode_render_dir(STORY_ID, 1, create=True),
                 lambda: stories.gen_cache_dir(STORY_ID, create=True),
                 lambda: stories.episode_file_path(STORY_ID, 1, "cover.jpg", create=True)):
        with pytest.raises(KeyError):
            call()
    assert not (outputs / "stories" / STORY_ID).exists()


def _plant_link(stories, outputs, story_id, level, target):
    """Make every level above *level* real, and *level* itself a symlink to
    *target*. Returns the link."""
    story = _story_dir(outputs, story_id)
    chains = {
        "episodes": [story / "episodes"],
        "ep01": [story / "episodes", story / "episodes" / "ep01"],
        "render": [story / "episodes", story / "episodes" / "ep01", story / "episodes" / "ep01" / "render"],
        "render/cache": [story / "episodes", story / "episodes" / "ep01", story / "episodes" / "ep01" / "render",
                         story / "episodes" / "ep01" / "render" / "cache"],
        "cache": [story / "cache"],
        "cache/gen": [story / "cache", story / "cache" / "gen"],
    }[level]
    for folder in chains[:-1]:
        folder.mkdir(exist_ok=True)
    _symlink(target, chains[-1])
    return chains[-1]


def _elsewhere(tmp_path):
    folder = tmp_path / "elsewhere"
    for sub in ("ep01/render/cache", "render/cache", "cache", "gen"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    (folder / "gen" / "keep.json").write_text("{}", encoding="utf-8")
    return folder


@pytest.mark.parametrize("level", ["episodes", "ep01", "render", "render/cache"])
def test_a_symlink_at_any_level_of_render_is_never_followed(stories, outputs, story_id, tmp_path, level):
    elsewhere = _elsewhere(tmp_path)
    target = {"episodes": elsewhere, "ep01": elsewhere / "ep01", "render": elsewhere / "render",
              "render/cache": elsewhere / "cache"}[level]
    before = _snapshot(elsewhere)
    link = _plant_link(stories, outputs, story_id, level, target)

    for create in (False, True):
        with pytest.raises(KeyError):
            stories.episode_render_dir(story_id, 1, "cache", create=create)
        if level != "render/cache":
            with pytest.raises(KeyError):
                stories.episode_render_dir(story_id, 1, create=create)
            for sub in store.EPISODE_RENDER_SUBDIRS:
                with pytest.raises(KeyError):
                    stories.episode_render_dir(story_id, 1, sub, create=create)

    assert os.path.islink(link)
    assert _snapshot(elsewhere) == before


@pytest.mark.parametrize("level", ["cache", "cache/gen"])
def test_a_symlink_at_either_level_of_the_gen_cache_is_never_followed(stories, outputs, story_id, tmp_path,
                                                                       level):
    elsewhere = _elsewhere(tmp_path)
    target = elsewhere if level == "cache" else elsewhere / "gen"
    before = _snapshot(elsewhere)
    link = _plant_link(stories, outputs, story_id, level, target)

    for create in (False, True):
        with pytest.raises(KeyError):
            stories.gen_cache_dir(story_id, create=create)

    assert os.path.islink(link)
    assert _snapshot(elsewhere) == before


@pytest.mark.parametrize("level", ["render", "cache/gen"])
def test_a_file_in_place_of_a_folder_is_refused(stories, outputs, story_id, level):
    story = _story_dir(outputs, story_id)
    path = story / "episodes" / "ep01" / "render" if level == "render" else story / "cache" / "gen"
    path.parent.mkdir(parents=True)
    path.write_text("a file", encoding="utf-8")
    call = (lambda c: stories.episode_render_dir(story_id, 1, create=c)) if level == "render" else \
        (lambda c: stories.gen_cache_dir(story_id, create=c))
    for create in (False, True):
        with pytest.raises(KeyError):
            call(create)
    assert path.read_text(encoding="utf-8") == "a file"


# ----------------------------------------------- the story is untouched (RC-E2)

def test_phase4_episode_calls_never_touch_the_story(stories, outputs, story_id, monkeypatch):
    stories.update(story_id, lambda doc: doc["approvals"].update(concept=NOW, bible=NOW), now=NOW)
    story_path = _story_dir(outputs, story_id) / "story.json"
    index_path = outputs / "stories.json"
    story_before, index_before = story_path.read_bytes(), index_path.read_bytes()
    saved = stories.get(story_id)

    def untouchable(*args, **kwargs):
        raise AssertionError("story.json or the index was touched by an episode call")

    for method in ("_read_story", "_save_story", "_upsert_index", "_write_index", "_read_index",
                   "get", "update"):
        monkeypatch.setattr(store.StoryStore, method, untouchable)

    for ep in (1, 2):
        for name in (SCRIPT, STORYBOARD) + NEW_DOCS:
            stories.write_episode_doc(story_id, ep, name, BUILD[name](ep), now=LATER)
            # byte-identical after every single episode write
            assert (story_path.read_bytes(), index_path.read_bytes()) == (story_before, index_before), name
            stories.read_episode_doc(story_id, ep, name)
        stories.episode_asset_path(story_id, ep, "shots", "shot_01.png", create=True)
        for name in store.EPISODE_FILE_NAMES:
            stories.episode_file_path(story_id, ep, name, create=True)
        for sub in (None,) + store.EPISODE_RENDER_SUBDIRS:
            stories.episode_render_dir(story_id, ep, sub, create=True)
    stories.gen_cache_dir(story_id, create=True)
    assert stories.list_episodes(story_id) == [1, 2]
    monkeypatch.undo()

    assert story_path.read_bytes() == story_before
    assert index_path.read_bytes() == index_before
    after = stories.get(story_id)
    assert after == saved
    assert (after["approvals"], after["status"]) == (saved["approvals"], "bible_approved")


# -------------------------------------------------------------- delete

def test_deleting_the_story_removes_its_cache_and_render_folders(stories, outputs, story_id):
    sibling = stories.create(language="en", now=NOW)["story_id"]
    for sid in (story_id, sibling):
        for name in NEW_DOCS:
            stories.write_episode_doc(sid, 1, name, BUILD[name](), now=LATER)
        pathlib.Path(stories.episode_render_dir(sid, 1, "cache", create=True), f"{SHA_A}.mp4").write_bytes(b"MP4")
        pathlib.Path(stories.episode_file_path(sid, 1, "episode_final.mp4", create=True)).write_bytes(b"MP4")
        pathlib.Path(stories.episode_asset_path(sid, 1, "shots", "shot_01.png", create=True)).write_bytes(b"PNG")
        pathlib.Path(stories.gen_cache_dir(sid, create=True), f"{SHA_B}.json").write_text("{}", encoding="utf-8")
    sibling_before = _snapshot(_story_dir(outputs, sibling))

    report = stories.delete(story_id)

    assert report == {"removed": [f"outputs/stories/{story_id}/"], "kept": []}
    assert not _story_dir(outputs, story_id).exists()
    assert _snapshot(_story_dir(outputs, sibling)) == sibling_before
    assert os.path.isdir(stories.gen_cache_dir(sibling))
    assert os.path.isdir(stories.episode_render_dir(sibling, 1, "cache"))


def test_deleting_the_story_never_follows_a_link_out_of_it(stories, outputs, story_id, tmp_path):
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    _plant_link(stories, outputs, story_id, "cache", elsewhere)
    stories.episode_dir(story_id, 1, create=True)
    _symlink(elsewhere / "render", _episode_dir(outputs, story_id) / "render")

    report = stories.delete(story_id)

    assert report == {"removed": [f"outputs/stories/{story_id}/"], "kept": []}
    assert not os.path.lexists(_story_dir(outputs, story_id))
    assert _snapshot(elsewhere) == before

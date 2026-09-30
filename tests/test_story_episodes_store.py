"""Episode documents on disk (AI Story phase 3, stage 3; spec 2, 2.7, 2.8).

``clipping/aistory/store.py`` keeps each episode of a story in
``outputs/stories/<id>/episodes/ep<NN>/``: ``script.json``,
``storyboard.json`` and, when the user opts into measuring with real voices,
``assets/voice/line_<NN>.{mp3,wav,json}``. What is pinned here:

- an episode number is a real ``int`` in 1..99; anything else (a bool, a
  string, a float, None) is refused before any filesystem access, like a
  malformed story id, and so are an unknown document name, asset kind or
  asset file name;
- a script and a storyboard round-trip; each is validated on every write and
  every read by its own self-contained checks; an invalid document writes
  nothing and leaves the old file byte-identical; a document whose own
  ``ep`` is not its folder's is refused;
- a script and a storyboard each keep their ``created_at`` and their
  ``updated_at`` becomes *now* (``write_doc``'s rule);
- every level (``episodes/``, ``ep<NN>/``, ``assets/``, ``voice/``) must be a
  real directory directly inside the one above it, and a document or asset
  must not be a symlink: anything else is refused, never followed, and what
  it points to is untouched (DEC-111);
- ``list_episodes`` names the real ``ep<NN>`` folders only; everything else is
  skipped and never deleted;
- episode documents never read or write ``story.json``: approvals, status
  and the index are byte-identical after them (RC-E2);
- deleting the story removes its episodes too;
- two threads writing the script and the storyboard of one episode leave both
  valid.

Stdlib + pytest only (DEC-012). Every file lives under ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import threading

import pytest

from clipping.aistory import schemas, store

NOW = "2026-09-27T10:00:00+00:00"
LATER = "2026-09-27T11:00:00+00:00"
LATEST = "2026-09-27T12:00:00+00:00"

SCRIPT = "script.json"
STORYBOARD = "storyboard.json"


# ---------------------------------------------------------------- builders

def _line(line_id):
    return {
        "line_id": line_id, "speaker": "char_kiwilo", "text": "A short line here.", "emotion": "neutral",
        "delivery": "calm",
        "timing": {"source": "estimated", "duration_s": 1.2, "text_hash": "0123456789abcdef",
                   "voice": None, "audio": None},
    }


def _scene(scene_id, function, line_id):
    return {
        "scene_id": scene_id, "function": function, "place_id": "place_beach_camp", "time_variant": "day",
        "characters": ["char_kiwilo"], "props": [], "summary": "Something happens.", "emotion": "neutral",
        "target_duration_s": 5.0, "lines": [_line(line_id)], "sfx_cues": [], "on_screen_text": None,
        "state": "written", "source": "E2", "rev": 1,
    }


def _script(ep=1, **changes):
    """A minimal valid episode_script_v1: a hook scene, then the cliffhanger."""
    doc = {
        "$schema": "episode_script_v1", "ep": ep, "title": "Test Episode", "language": "fr",
        "template_id": "serial_60s_v1",
        "hook": {"on_screen_text": None},
        "scenes": [_scene("s01", "hook", "l04"), _scene("s02", "cliffhanger", "l08")],
        "cliffhanger": {"scene_id": "s02", "reveal": None, "cut_to_black": True},
        "next_episode_teaser": None, "timing": None, "consistency_report": None,
        "approved_anyway": None, "approved_at": None, "rev": 1,
        "created_at": NOW, "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def _shot(n, scene_id, line_id):
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


def _storyboard(ep=1, **changes):
    """A minimal valid storyboard_v1: one shot per scene of ``_script()``."""
    doc = {
        "$schema": "storyboard_v1", "ep": ep,
        "shots": [_shot(1, "s01", "l04"), _shot(2, "s02", "l08")],
        "transitions": [{"after": "sh01", "type": "cut", "duration_s": 0.0}],
        "scenes": {sid: {"source": "t1", "script_rev": 1, "stale": False} for sid in ("s01", "s02")},
        "resolved_from": {}, "approved_at": None, "rev": 1,
        "created_at": NOW, "updated_at": NOW,
    }
    doc.update(changes)
    return doc


BUILD = {SCRIPT: _script, STORYBOARD: _storyboard}


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


def _elsewhere(tmp_path):
    """A folder outside the root, holding what a followed link would reach."""
    folder = tmp_path / "elsewhere"
    (folder / "ep01").mkdir(parents=True)
    (folder / "ep01" / SCRIPT).write_text(json.dumps(_script()), encoding="utf-8")
    (folder / SCRIPT).write_text(json.dumps(_script()), encoding="utf-8")
    return folder


def _snapshot(folder):
    """Every path below *folder* with its bytes (None for a directory)."""
    return {str(p.relative_to(folder)): (None if p.is_dir() else p.read_bytes())
            for p in sorted(folder.rglob("*"))}


# ------------------------------------------------------------ constants

def test_the_builders_validate():
    assert schemas.episode_script_errors(_script()) == []
    assert schemas.storyboard_errors(_storyboard()) == []


def test_the_episode_documents_and_their_validators():
    # Phase 4 adds the assets, render-manifest and metadata-pack documents (DEC-155);
    # phase 5 adds N1's proposals for the episode (plan 11, stage 1) and the last
    # good render's manifest, a partial re-render's baseline (stage 8).
    assert store.EPISODE_DOC_NAMES == (
        SCRIPT, STORYBOARD, "assets.json", "render_manifest.json", "metadata_pack.json", "proposals.json",
        "render_manifest.last_good.json",
    )
    assert store.EPISODE_DOC_VALIDATORS == {
        SCRIPT: schemas.episode_script_errors, STORYBOARD: schemas.storyboard_errors,
        "assets.json": schemas.episode_assets_errors,
        "render_manifest.json": schemas.render_manifest_errors,
        "metadata_pack.json": schemas.metadata_pack_errors,
        "proposals.json": schemas.next_proposals_errors,
        "render_manifest.last_good.json": schemas.render_manifest_errors,
    }
    # Episode documents are never story documents: read_doc/write_doc cannot reach them.
    assert not set(store.EPISODE_DOC_NAMES) & set(store.DOC_NAMES)


def test_only_a_document_whose_schema_has_timestamps_is_stamped():
    for name, schema in ((SCRIPT, schemas.EPISODE_SCRIPT_SCHEMA), (STORYBOARD, schemas.STORYBOARD_SCHEMA)):
        has = {"created_at", "updated_at"} <= set(schema["properties"])
        assert has == (name in store.EPISODE_DOCS_WITH_TIMESTAMPS), name


def test_the_episode_bounds_are_the_schemas_own():
    for schema in (schemas.EPISODE_SCRIPT_SCHEMA, schemas.STORYBOARD_SCHEMA):
        bounds = schema["properties"]["ep"]
        assert (bounds["minimum"], bounds["maximum"]) == (store.EPISODE_MIN, store.EPISODE_MAX)
    # Two digits in ep<NN>.
    assert store.EPISODE_MAX <= 99


def test_the_asset_kinds_are_closed():
    # Phase 4 adds the shot images (DEC-155); phase 6 stage 7 their clips.
    assert store.EPISODE_ASSET_KINDS == ("voice", "shots", "clips")
    assert set(store.EPISODE_ASSET_NAME_PATTERNS) == set(store.EPISODE_ASSET_KINDS)


# --------------------------------------------------------- check_episode

@pytest.mark.parametrize("ep", [1, 2, 10, 99])
def test_check_episode_accepts_one_to_ninety_nine(ep):
    assert store.check_episode(ep) == ep


BAD_EPS = [0, 100, -1, True, False, "1", "01", 1.0, 2.0, None, [1], 1j, 10 ** 20]


@pytest.mark.parametrize("ep", BAD_EPS)
def test_check_episode_refuses_anything_else(ep):
    with pytest.raises(KeyError):
        store.check_episode(ep)


# ------------------------------------------------------------- tripwire

class _Tripwire:
    """Stands in for os/shutil/tempfile in the store: any use is a failure."""

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


def _every_episode_call(stories, story_id, ep):
    return [
        lambda: stories.episode_dir(story_id, ep),
        lambda: stories.episode_dir(story_id, ep, create=True),
        lambda: stories.read_episode_doc(story_id, ep, SCRIPT),
        lambda: stories.read_episode_doc(story_id, ep, STORYBOARD),
        lambda: stories.write_episode_doc(story_id, ep, SCRIPT, _script(), now=LATER),
        lambda: stories.write_episode_doc(story_id, ep, STORYBOARD, _storyboard(), now=LATER),
        lambda: stories.episode_asset_path(story_id, ep, "voice", "line_01.mp3"),
        lambda: stories.episode_asset_path(story_id, ep, "voice", "line_01.mp3", create=True),
    ]


@pytest.mark.parametrize("ep", BAD_EPS)
def test_a_bad_episode_is_refused_before_any_filesystem_access(tripwired, ep):
    for call in _every_episode_call(tripwired, "0123456789ab", ep):
        with pytest.raises(KeyError):
            call()


@pytest.mark.parametrize("story_id", ["../x", "ABCDEF123456", "0123456789ab\n", "", None, 7])
def test_a_bad_story_id_is_refused_before_any_filesystem_access(tripwired, story_id):
    for call in _every_episode_call(tripwired, story_id, 1):
        with pytest.raises(KeyError):
            call()
    with pytest.raises(KeyError):
        tripwired.list_episodes(story_id)


BAD_DOC_NAMES = ["story.json", "concepts.json", "season.json", "../script.json", "script.json\n",
                 "episodes/ep01/script.json", "Script.json", "script", "", None, 7]


@pytest.mark.parametrize("name", BAD_DOC_NAMES)
def test_an_unknown_document_name_is_refused_before_any_filesystem_access(tripwired, name):
    with pytest.raises(ValueError):
        tripwired.read_episode_doc("0123456789ab", 1, name)
    with pytest.raises(ValueError):
        tripwired.write_episode_doc("0123456789ab", 1, name, _script(), now=LATER)


BAD_ASSET_KINDS = ["shots", "sfx", "bgm", "VOICE", "voice/", "../voice", "", None, 7]
BAD_ASSET_NAMES = ["../x", "line_1.mp3", "line_001.mp3", "line_01.mp3.exe", "LINE_01.mp3", "line_01.MP3",
                   "line_01.ogg", "line_01.mp3\n", "line_01", "voice/line_01.mp3", "/tmp/line_01.mp3",
                   ".line_01.mp3", "line_0١.mp3", "", None, 7]


@pytest.mark.parametrize("kind", BAD_ASSET_KINDS)
def test_an_unknown_asset_kind_is_refused_before_any_filesystem_access(tripwired, kind):
    for create in (False, True):
        with pytest.raises(KeyError):
            tripwired.episode_asset_path("0123456789ab", 1, kind, "line_01.mp3", create=create)


@pytest.mark.parametrize("name", BAD_ASSET_NAMES)
def test_an_unknown_asset_name_is_refused_before_any_filesystem_access(tripwired, name):
    for create in (False, True):
        with pytest.raises(KeyError):
            tripwired.episode_asset_path("0123456789ab", 1, "voice", name, create=create)


def test_the_episode_tripwire_does_trip(tripwired):
    """The tests above would pass vacuously if the tripwire never fired."""
    with pytest.raises(AssertionError):
        tripwired.episode_dir("0123456789ab", 1)


# ------------------------------------------------------------ episode_dir

def test_episode_dir_needs_the_folder_unless_asked_to_create_it(stories, outputs, story_id):
    with pytest.raises(KeyError):
        stories.episode_dir(story_id, 1)
    assert not (_story_dir(outputs, story_id) / "episodes").exists()

    made = stories.episode_dir(story_id, 7, create=True)

    assert made == os.path.realpath(_episode_dir(outputs, story_id, 7))
    assert os.path.isdir(made)
    assert stories.episode_dir(story_id, 7) == made
    with pytest.raises(KeyError):
        stories.episode_dir(story_id, 8)


def test_episode_dir_of_an_unknown_story_creates_nothing(stories, outputs):
    with pytest.raises(KeyError):
        stories.episode_dir("0123456789ab", 1, create=True)
    assert not (outputs / "stories" / "0123456789ab").exists()


# --------------------------------------------------------- read / write

def test_a_script_round_trips(stories, outputs, story_id):
    doc = _script()

    written = stories.write_episode_doc(story_id, 1, SCRIPT, doc, now=LATER)

    assert written == {**doc, "updated_at": LATER}
    assert doc["updated_at"] == NOW  # the caller's document is not changed
    assert stories.read_episode_doc(story_id, 1, SCRIPT) == written
    path = _episode_dir(outputs, story_id) / SCRIPT
    assert json.loads(path.read_text(encoding="utf-8")) == written
    assert list(json.loads(path.read_text(encoding="utf-8"))) == list(doc)  # readable field order
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644
    assert _temp_files(outputs) == []


def test_a_storyboard_round_trips_with_both_stamps(stories, outputs, story_id):
    doc = _storyboard()

    written = stories.write_episode_doc(story_id, 1, STORYBOARD, doc, now=LATER)

    assert written == {**doc, "updated_at": LATER}
    assert (written["created_at"], written["updated_at"]) == (NOW, LATER)
    assert doc["updated_at"] == NOW  # the caller's document is not changed
    assert stories.read_episode_doc(story_id, 1, STORYBOARD) == written
    path = _episode_dir(outputs, story_id) / STORYBOARD
    assert json.loads(path.read_text(encoding="utf-8")) == written
    assert list(json.loads(path.read_text(encoding="utf-8"))) == list(doc)  # readable field order
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o644
    assert _temp_files(outputs) == []


def test_each_episode_has_its_own_documents(stories, story_id):
    stories.write_episode_doc(story_id, 1, SCRIPT, _script(1, title="One"), now=LATER)
    stories.write_episode_doc(story_id, 12, SCRIPT, _script(12, title="Twelve"), now=LATER)

    assert stories.read_episode_doc(story_id, 1, SCRIPT)["title"] == "One"
    assert stories.read_episode_doc(story_id, 12, SCRIPT)["title"] == "Twelve"
    assert stories.read_episode_doc(story_id, 12, STORYBOARD) is None


@pytest.mark.parametrize("name", [SCRIPT, STORYBOARD])
def test_created_at_is_kept_and_updated_at_moves_on_a_rewrite(stories, story_id, name):
    first = stories.write_episode_doc(story_id, 1, name, BUILD[name](), now=LATER)
    edited = stories.read_episode_doc(story_id, 1, name)
    edited["approved_at"] = LATER
    edited["rev"] = 2

    second = stories.write_episode_doc(story_id, 1, name, edited, now=LATEST)

    assert first["created_at"] == second["created_at"] == NOW
    assert (first["updated_at"], second["updated_at"]) == (LATER, LATEST)
    assert stories.read_episode_doc(story_id, 1, name) == second


def test_read_of_a_document_not_written_yet_is_none_and_creates_nothing(stories, outputs, story_id):
    assert stories.read_episode_doc(story_id, 1, SCRIPT) is None
    assert not (_story_dir(outputs, story_id) / "episodes").exists()

    (_story_dir(outputs, story_id) / "episodes").mkdir()
    assert stories.read_episode_doc(story_id, 1, SCRIPT) is None
    assert not _episode_dir(outputs, story_id).exists()

    stories.write_episode_doc(story_id, 1, SCRIPT, _script(), now=LATER)
    assert stories.read_episode_doc(story_id, 1, STORYBOARD) is None


def test_an_unknown_story_is_a_key_error(stories, outputs):
    with pytest.raises(KeyError):
        stories.read_episode_doc("0123456789ab", 1, SCRIPT)
    with pytest.raises(KeyError):
        stories.write_episode_doc("0123456789ab", 1, SCRIPT, _script(), now=LATER)
    with pytest.raises(KeyError):
        stories.list_episodes("0123456789ab")
    with pytest.raises(KeyError):
        stories.episode_asset_path("0123456789ab", 1, "voice", "line_01.mp3", create=True)
    assert not (outputs / "stories" / "0123456789ab").exists()


def _script_with_wrong_cliffhanger(doc):
    doc["cliffhanger"]["scene_id"] = "s01"


def _script_with_an_extra_key(doc):
    doc["duration_s"] = 60


def _storyboard_with_the_spec_example_key(doc):
    doc["approved"] = False  # in the spec's example, not in storyboard_v1


def _storyboard_out_of_order(doc):
    doc["shots"][1]["order"] = 5


def _storyboard_without_created_at(doc):
    del doc["created_at"]


BROKEN = {
    "script: cliffhanger not the last scene": (SCRIPT, _script_with_wrong_cliffhanger),
    "script: extra key": (SCRIPT, _script_with_an_extra_key),
    "storyboard: spec example key": (STORYBOARD, _storyboard_with_the_spec_example_key),
    "storyboard: shot order": (STORYBOARD, _storyboard_out_of_order),
    "storyboard: no created_at": (STORYBOARD, _storyboard_without_created_at),
}


@pytest.mark.parametrize("label", list(BROKEN), ids=list(BROKEN))
def test_an_invalid_first_document_writes_nothing(stories, outputs, story_id, label):
    name, mutate = BROKEN[label]
    doc = BUILD[name]()
    mutate(doc)

    with pytest.raises(schemas.SchemaError):
        stories.write_episode_doc(story_id, 1, name, doc, now=LATER)

    assert not (_story_dir(outputs, story_id) / "episodes").exists()
    assert _temp_files(outputs) == []


@pytest.mark.parametrize("label", list(BROKEN), ids=list(BROKEN))
def test_an_invalid_document_leaves_the_old_file_intact(stories, outputs, story_id, label):
    name, mutate = BROKEN[label]
    stories.write_episode_doc(story_id, 1, name, BUILD[name](), now=LATER)
    path = _episode_dir(outputs, story_id) / name
    before = path.read_bytes()
    doc = BUILD[name]()
    mutate(doc)

    with pytest.raises(schemas.SchemaError):
        stories.write_episode_doc(story_id, 1, name, doc, now=LATEST)

    assert path.read_bytes() == before
    assert _temp_files(outputs) == []


def test_a_caller_validator_can_refuse_the_write(stories, outputs, story_id):
    with pytest.raises(schemas.SchemaError) as caught:
        stories.write_episode_doc(story_id, 1, SCRIPT, _script(), now=LATER,
                                  validator=lambda doc: ["$.scenes[s01].characters: not in the cast"])
    assert caught.value.errors == ["$.scenes[s01].characters: not in the cast"]
    assert not (_story_dir(outputs, story_id) / "episodes").exists()

    seen = []
    stories.write_episode_doc(story_id, 1, SCRIPT, _script(), now=LATER,
                              validator=lambda doc: seen.append(doc["updated_at"]) or [])
    assert seen == [LATER]  # the validator sees the document as it will be written


@pytest.mark.parametrize("payload", [None, [], "{}", 7])
def test_a_document_must_be_an_object(stories, outputs, story_id, payload):
    with pytest.raises(ValueError):
        stories.write_episode_doc(story_id, 1, SCRIPT, payload, now=LATER)
    assert not (_story_dir(outputs, story_id) / "episodes").exists()


@pytest.mark.parametrize("name", [SCRIPT, STORYBOARD])
def test_a_document_of_another_episode_is_refused(stories, outputs, story_id, name):
    with pytest.raises(schemas.SchemaError) as caught:
        stories.write_episode_doc(story_id, 1, name, BUILD[name](ep=2), now=LATER)
    assert any("$.ep" in error for error in caught.value.errors)
    assert not (_story_dir(outputs, story_id) / "episodes").exists()

    stories.write_episode_doc(story_id, 1, name, BUILD[name](), now=LATER)
    before = (_episode_dir(outputs, story_id) / name).read_bytes()
    with pytest.raises(schemas.SchemaError):
        stories.write_episode_doc(story_id, 1, name, BUILD[name](ep=3), now=LATEST)
    assert (_episode_dir(outputs, story_id) / name).read_bytes() == before


@pytest.mark.parametrize("name", [SCRIPT, STORYBOARD])
def test_a_document_copied_into_another_episode_folder_is_refused(stories, outputs, story_id, name):
    stories.write_episode_doc(story_id, 2, name, BUILD[name](ep=2), now=LATER)
    stories.episode_dir(story_id, 1, create=True)
    shutil.copy(_episode_dir(outputs, story_id, 2) / name, _episode_dir(outputs, story_id, 1) / name)

    with pytest.raises(schemas.SchemaError) as caught:
        stories.read_episode_doc(story_id, 1, name)
    assert any("does not match its folder" in error for error in caught.value.errors)


@pytest.mark.parametrize("content", ["{not json", '{"$schema": "episode_script_v1"}', "[]", "\xff"])
def test_an_invalid_document_on_disk_raises_and_is_not_repaired(stories, outputs, story_id, content):
    folder = pathlib.Path(stories.episode_dir(story_id, 1, create=True))
    (folder / SCRIPT).write_text(content, encoding="latin-1")
    before = (folder / SCRIPT).read_bytes()

    with pytest.raises(schemas.SchemaError):
        stories.read_episode_doc(story_id, 1, SCRIPT)

    assert (folder / SCRIPT).read_bytes() == before


def test_a_directory_in_place_of_a_document_is_refused(stories, outputs, story_id):
    folder = pathlib.Path(stories.episode_dir(story_id, 1, create=True))
    (folder / SCRIPT).mkdir()

    with pytest.raises(schemas.SchemaError):
        stories.read_episode_doc(story_id, 1, SCRIPT)
    with pytest.raises(ValueError):
        stories.write_episode_doc(story_id, 1, SCRIPT, _script(), now=LATER)
    assert (folder / SCRIPT).is_dir()


def test_a_failure_mid_write_leaves_the_previous_file_byte_identical(stories, outputs, story_id, monkeypatch):
    stories.write_episode_doc(story_id, 1, SCRIPT, _script(), now=LATER)
    path = _episode_dir(outputs, story_id) / SCRIPT
    before = path.read_bytes()

    def torn_dump(obj, fh, **kwargs):
        fh.write('{"$schema": "episode_script_v1", "ep": ')
        raise OSError("No space left on device")

    monkeypatch.setattr(store.json, "dump", torn_dump)
    with pytest.raises(OSError):
        stories.write_episode_doc(story_id, 1, SCRIPT, _script(title="Lost"), now=LATEST)
    monkeypatch.undo()

    assert path.read_bytes() == before
    assert _temp_files(outputs) == []


# -------------------------------------------------------------- symlinks

def _through_a_link_everything_is_refused(stories, story_id):
    for name in (SCRIPT, STORYBOARD):
        with pytest.raises(KeyError):
            stories.read_episode_doc(story_id, 1, name)
        with pytest.raises(KeyError):
            stories.write_episode_doc(story_id, 1, name, BUILD[name](), now=LATER)
    for create in (False, True):
        with pytest.raises(KeyError):
            stories.episode_dir(story_id, 1, create=create)
        with pytest.raises(KeyError):
            stories.episode_asset_path(story_id, 1, "voice", "line_01.mp3", create=create)


def test_a_symlinked_episodes_folder_is_never_followed(stories, outputs, story_id, tmp_path, logs):
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    link = _story_dir(outputs, story_id) / "episodes"
    _symlink(elsewhere, link)

    _through_a_link_everything_is_refused(stories, story_id)

    del logs[:]
    assert stories.list_episodes(story_id) == []
    assert logs == [f"Skipped outputs/stories/{story_id}/episodes/: not a real directory, never followed"]
    assert os.path.islink(link)
    assert _snapshot(elsewhere) == before


def test_a_file_in_place_of_the_episodes_folder_is_refused(stories, outputs, story_id, logs):
    path = _story_dir(outputs, story_id) / "episodes"
    path.write_text("a file", encoding="utf-8")

    _through_a_link_everything_is_refused(stories, story_id)

    del logs[:]
    assert stories.list_episodes(story_id) == []
    assert logs == [f"Skipped outputs/stories/{story_id}/episodes/: not a real directory, never followed"]
    assert path.read_text(encoding="utf-8") == "a file"


def test_a_symlinked_episode_folder_is_never_followed(stories, outputs, story_id, tmp_path, logs):
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    (_story_dir(outputs, story_id) / "episodes").mkdir()
    link = _episode_dir(outputs, story_id)
    _symlink(elsewhere / "ep01", link)

    _through_a_link_everything_is_refused(stories, story_id)

    del logs[:]
    assert stories.list_episodes(story_id) == []
    assert logs == [f"Skipped outputs/stories/{story_id}/episodes/ep01/: not a real directory inside episodes/"]
    assert os.path.islink(link)
    assert _snapshot(elsewhere) == before


@pytest.mark.parametrize("name", [SCRIPT, STORYBOARD])
def test_a_symlinked_document_is_never_read_or_written(stories, outputs, story_id, tmp_path, name):
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
    assert stories.list_episodes(story_id) == [1]  # the folder is real; its content is not listed


# --------------------------------------------------------- list_episodes

def test_list_episodes_of_a_story_without_episodes_is_empty(stories, outputs, story_id, logs):
    del logs[:]
    assert stories.list_episodes(story_id) == []
    (_story_dir(outputs, story_id) / "episodes").mkdir()
    assert stories.list_episodes(story_id) == []
    assert logs == []


def test_list_episodes_names_the_real_episode_folders_only(stories, outputs, story_id, tmp_path, logs):
    stories.write_episode_doc(story_id, 10, SCRIPT, _script(10), now=LATER)
    stories.episode_dir(story_id, 1, create=True)  # an episode folder counts whatever it holds
    stories.episode_dir(story_id, 99, create=True)
    episodes = _story_dir(outputs, story_id) / "episodes"
    for name in ("ep1", "ep001", "epXX", "ep00", "ep1a", "EP04", "ep05.bak", "ep٠٥"):
        (episodes / name).mkdir()
    elsewhere = _elsewhere(tmp_path)
    before = _snapshot(elsewhere)
    _symlink(elsewhere / "ep01", episodes / "ep02")
    (episodes / "ep03").write_text("a file", encoding="utf-8")
    names = sorted(p.name for p in episodes.iterdir())
    del logs[:]

    assert stories.list_episodes(story_id) == [1, 10, 99]

    assert logs == [
        f"Skipped outputs/stories/{story_id}/episodes/ep02/: not a real directory inside episodes/",
        f"Skipped outputs/stories/{story_id}/episodes/ep03/: not a real directory inside episodes/",
    ]
    assert sorted(p.name for p in episodes.iterdir()) == names  # nothing deleted
    assert os.path.islink(episodes / "ep02")
    assert (episodes / "ep03").read_text(encoding="utf-8") == "a file"
    assert _snapshot(elsewhere) == before


# -------------------------------------------------------------- assets

@pytest.mark.parametrize("name", ["line_01.mp3", "line_01.json", "line_00.wav", "line_99.mp3"])
def test_an_accepted_voice_asset_path(stories, outputs, story_id, name):
    with pytest.raises(KeyError):
        stories.episode_asset_path(story_id, 1, "voice", name)
    assert not (_story_dir(outputs, story_id) / "episodes").exists()

    path = stories.episode_asset_path(story_id, 1, "voice", name, create=True)

    folder = _episode_dir(outputs, story_id) / "assets" / "voice"
    assert path == os.path.join(os.path.realpath(folder), name)
    assert folder.is_dir()
    assert not os.path.lexists(path)  # the folders are made, never the file
    assert stories.episode_asset_path(story_id, 1, "voice", name) == path

    pathlib.Path(path).write_bytes(b"AUDIO")
    assert stories.episode_asset_path(story_id, 1, "voice", name) == path


def test_a_symlinked_asset_is_refused_and_its_target_untouched(stories, outputs, story_id, tmp_path):
    secret = tmp_path / "secret.mp3"
    secret.write_bytes(b"SECRET")
    folder = pathlib.Path(stories.episode_asset_path(story_id, 1, "voice", "line_01.mp3", create=True)).parent
    _symlink(secret, folder / "line_01.mp3")

    for create in (False, True):
        with pytest.raises(KeyError):
            stories.episode_asset_path(story_id, 1, "voice", "line_01.mp3", create=create)

    assert os.path.islink(folder / "line_01.mp3")
    assert secret.read_bytes() == b"SECRET"
    # A dangling link is refused the same way.
    _symlink(tmp_path / "missing.json", folder / "line_01.json")
    with pytest.raises(KeyError):
        stories.episode_asset_path(story_id, 1, "voice", "line_01.json")
    assert not (tmp_path / "missing.json").exists()


def test_a_directory_in_place_of_an_asset_is_refused(stories, outputs, story_id):
    folder = pathlib.Path(stories.episode_asset_path(story_id, 1, "voice", "line_01.mp3", create=True)).parent
    (folder / "line_01.mp3").mkdir()
    with pytest.raises(KeyError):
        stories.episode_asset_path(story_id, 1, "voice", "line_01.mp3")


@pytest.mark.parametrize("level", ["assets", "voice"])
def test_a_symlinked_asset_folder_is_never_followed(stories, outputs, story_id, tmp_path, level):
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "voice").mkdir(parents=True)
    episode = pathlib.Path(stories.episode_dir(story_id, 1, create=True))
    if level == "voice":
        (episode / "assets").mkdir()
        _symlink(elsewhere / "voice", episode / "assets" / "voice")
    else:
        _symlink(elsewhere, episode / "assets")

    for create in (False, True):
        with pytest.raises(KeyError):
            stories.episode_asset_path(story_id, 1, "voice", "line_01.mp3", create=create)

    assert sorted(p.name for p in elsewhere.rglob("*")) == ["voice"]


# ----------------------------------------------- the story is untouched (RC-E2)

def test_episode_documents_never_touch_the_story(stories, outputs, story_id, monkeypatch):
    stories.update(story_id, lambda doc: doc["approvals"].update(concept=NOW, bible=NOW), now=NOW)
    story_before = (_story_dir(outputs, story_id) / "story.json").read_bytes()
    index_before = (outputs / "stories.json").read_bytes()
    saved = stories.get(story_id)

    def untouchable(*args, **kwargs):
        raise AssertionError("story.json or the index was touched by an episode call")

    for method in ("_read_story", "_save_story", "_upsert_index", "_write_index", "_read_index",
                   "get", "update"):
        monkeypatch.setattr(store.StoryStore, method, untouchable)

    for ep in (1, 2):
        stories.write_episode_doc(story_id, ep, SCRIPT, _script(ep, approved_at=LATER), now=LATER)
        stories.write_episode_doc(story_id, ep, STORYBOARD, _storyboard(ep, approved_at=LATER), now=LATER)
        stories.read_episode_doc(story_id, ep, SCRIPT)
        stories.read_episode_doc(story_id, ep, STORYBOARD)
        stories.episode_asset_path(story_id, ep, "voice", "line_01.mp3", create=True)
    assert stories.list_episodes(story_id) == [1, 2]
    monkeypatch.undo()

    assert (_story_dir(outputs, story_id) / "story.json").read_bytes() == story_before
    assert (outputs / "stories.json").read_bytes() == index_before
    after = stories.get(story_id)
    assert after == saved
    assert (after["approvals"], after["status"]) == (saved["approvals"], "bible_approved")


# -------------------------------------------------------------- delete

def test_deleting_the_story_removes_its_episodes_and_keeps_its_sibling(stories, outputs, story_id):
    sibling = stories.create(language="en", now=NOW)["story_id"]
    for sid in (story_id, sibling):
        stories.write_episode_doc(sid, 1, SCRIPT, _script(), now=LATER)
        stories.write_episode_doc(sid, 1, STORYBOARD, _storyboard(), now=LATER)
        pathlib.Path(stories.episode_asset_path(sid, 1, "voice", "line_01.mp3", create=True)).write_bytes(b"A")
    sibling_before = _snapshot(_story_dir(outputs, sibling))

    report = stories.delete(story_id)

    assert report == {"removed": [f"outputs/stories/{story_id}/"], "kept": []}
    assert not _story_dir(outputs, story_id).exists()
    assert _snapshot(_story_dir(outputs, sibling)) == sibling_before
    assert stories.list_episodes(sibling) == [1]
    assert stories.read_episode_doc(sibling, 1, SCRIPT) == {**_script(), "updated_at": LATER}


# ---------------------------------------------------------- concurrency

def test_two_threads_writing_one_episode_leave_both_documents_valid(outputs, story_id):
    instances = [store.StoryStore(str(outputs), on_log=lambda _: None) for _ in range(2)]
    barrier = threading.Barrier(2)
    errors = []
    rounds = 50

    def write(instance, name):
        try:
            barrier.wait()
            for i in range(rounds):
                doc = BUILD[name](rev=i + 1)
                instance.write_episode_doc(story_id, 1, name, doc, now=f"2026-09-27T12:00:{i:02d}+00:00")
        except Exception as exc:  # surfaced below; a thread cannot fail the test itself
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(instances[0], SCRIPT)),
               threading.Thread(target=write, args=(instances[1], STORYBOARD))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    for name in (SCRIPT, STORYBOARD):
        on_disk = json.loads((_episode_dir(outputs, story_id) / name).read_text(encoding="utf-8"))
        assert store.EPISODE_DOC_VALIDATORS[name](on_disk) == [], name
        assert on_disk["rev"] == rounds
        assert instances[0].read_episode_doc(story_id, 1, name) == on_disk
    assert _temp_files(outputs) == []

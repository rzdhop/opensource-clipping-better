"""The story workspace on disk (AI Story phase 1, stage 3).

``clipping/aistory/store.py`` keeps each story in ``outputs/stories/<id>/`` and
a rebuildable index in ``outputs/stories.json``. What is pinned here:

- a story is created with every ``story_bible_v1`` field present, and never
  with a default language;
- ``status`` is derived from ``approvals`` on every save (a contiguous prefix
  only), never taken from the caller;
- every write is atomic -- a failure mid-write leaves the previous file
  byte-identical and no temp file behind;
- the index is a cache: torn, missing or foreign, it is rebuilt from the
  folders and the rebuild is printed; an invalid folder is skipped, never
  deleted;
- a malformed id is refused before any filesystem access, and delete removes
  only a real story directory directly inside ``stories/`` (a symlink is kept
  and never followed) -- the rules of ``web/api/cleanup.py::contained``;
- two StoryStore instances on one root share one lock.

Stdlib + pytest only (DEC-012). Every file lives under ``tmp_path``.
"""

from __future__ import annotations

import inspect
import json
import os
import pathlib
import shutil
import threading

import pytest

from clipping.aistory import defaults, schemas, store, stylelock, templates

ROOT = pathlib.Path(__file__).resolve().parents[1]

NOW = "2026-09-26T10:00:00+00:00"
LATER = "2026-09-26T11:00:00+00:00"
LATEST = "2026-09-26T12:00:00+00:00"


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


def _story_path(outputs, story_id, name="story.json"):
    return outputs / "stories" / story_id / name


def _index(outputs):
    return json.loads((outputs / "stories.json").read_text(encoding="utf-8"))


def _temp_files(outputs):
    return [p for p in outputs.rglob("*") if p.name.startswith(".story-") or p.suffix == ".tmp"]


def _story_folders(outputs):
    root = outputs / "stories"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


# ---------------------------------------------------------------- defaults

def test_the_generation_profile_defaults():
    assert defaults.default_generation_profile() == {
        "tier": 1, "route": "auto", "consistency_mode": "references", "budget_profile": "free",
    }
    assert defaults.DEFAULT_TIER in defaults.TIERS
    assert defaults.DEFAULT_ROUTE in defaults.ROUTES
    assert defaults.DEFAULT_CONSISTENCY_MODE in defaults.CONSISTENCY_MODES
    assert defaults.DEFAULT_BUDGET_PROFILE in defaults.BUDGET_PROFILES


def test_each_default_generation_profile_is_a_fresh_dict():
    first = defaults.default_generation_profile()
    first["tier"] = 3
    assert defaults.default_generation_profile()["tier"] == 1


def test_the_budget_profiles_agree_with_the_shipped_profiles():
    from clipping.providers import budget

    shipped = json.loads((ROOT / "clipping" / "aistory" / "templates" / "budget_profiles.json")
                         .read_text(encoding="utf-8"))
    assert set(defaults.BUDGET_PROFILES) == set(shipped["profiles"])
    assert tuple(defaults.BUDGET_PROFILES) == tuple(budget.PROFILE_NAMES)


def test_there_is_no_default_language_anywhere():
    assert not [name for name in dir(defaults) if "LANG" in name.upper()]
    parameter = inspect.signature(store.StoryStore.create).parameters["language"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY


def test_default_outputs_dir_is_the_one_the_job_store_uses():
    assert store.default_outputs_dir() == str(ROOT / "outputs")
    job_store = (ROOT / "web" / "api" / "store.py").read_text(encoding="utf-8")
    assert 'PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))' in job_store
    assert 'os.path.join(PROJECT_ROOT, "outputs", "jobs.json")' in job_store


# ------------------------------------------------------------ schema

def _valid_bible(**changes):
    doc = {
        "$schema": "story_bible_v1", "story_id": "0123456789ab", "title": "", "language": "fr",
        "seed_text": None, "concept_id": None, "concept": None, "logline": None, "premise": None,
        "tone": None, "genre_tags": [], "world": None, "themes_and_values": [], "audience": None,
        "why_come_back": [], "cast_ids": [], "place_ids": [], "prop_ids": [],
        "style_template_id": None, "episode_template_id": "serial_60s_v1",
        "generation_profile": defaults.default_generation_profile(),
        "narrator": {"enabled": False, "voice": None},
        "approvals": {"concept": None, "bible": None, "style": None,
                      "cast": None, "places": None, "season": None},
        "status": "draft", "created_at": NOW, "updated_at": NOW,
    }
    doc.update(changes)
    return doc


_WORLD = {"setting_summary": "An island", "rules": ["Fruits are people."],
          "time_period": "contemporary", "recurring_motifs": ["the coconut phone"]}


def test_a_complete_bible_validates():
    doc = _valid_bible(
        title="Tentafruit Island", concept_id="tentafruit_island", concept={"title": "x"},
        logline="One line.", premise="Three sentences.", tone="melodramatic",
        genre_tags=["soap"], world=_WORLD, themes_and_values=["loyalty"],
        audience={"age": "13+", "platforms": ["tiktok", "shorts", "reels"]},
        why_come_back=["a", "b", "c"], style_template_id="fruit_drama",
        approvals={"concept": NOW, "bible": NOW, "style": None, "cast": None, "places": None, "season": None},
        status="bible_approved",
    )
    assert schemas.story_bible_errors(doc) == []


def test_every_fixed_object_of_the_bible_is_closed():
    schema = schemas.STORY_BIBLE_SCHEMA
    assert schema["additionalProperties"] is False
    for key in ("world", "audience", "generation_profile", "narrator", "approvals", "approved_by",
                "subtitle_style"):
        assert schema["properties"][key]["additionalProperties"] is False, key
    # Plan 21 stage 1, re-pinned on purpose: ``approved_by`` (the agent run's marks beside the approvals
    # it gave) is an optional field -- absent on every Studio story, whose story.json stays as it was.
    # Plan 23 stage B5, re-pinned on purpose: so is ``subtitle_style`` (the story's own subtitle look).
    assert set(schema["required"]) == set(schema["properties"]) - {"approved_by", "subtitle_style"}


@pytest.mark.parametrize("changes", [
    {"surprise": 1},
    {"world": {**_WORLD, "surprise": 1}},
    {"world": {**_WORLD, "rules": []}},
    {"world": {**_WORLD, "rules": ["r"] * 9}},
    {"audience": {"age": "13+", "platforms": ["youtube"]}},
    {"audience": {"age": "13+", "platforms": [], "surprise": 1}},
    {"generation_profile": {**defaults.default_generation_profile(), "tier": True}},
    {"generation_profile": {**defaults.default_generation_profile(), "tier": 4}},
    {"generation_profile": {**defaults.default_generation_profile(), "route": "cloud"}},
    {"generation_profile": {"tier": 1}},
    {"narrator": {"enabled": "no", "voice": None}},
    {"approvals": {"concept": "", "bible": None, "style": None, "cast": None, "places": None, "season": None}},
    {"approvals": {"concept": None, "bible": None}},
    {"title": "x" * 121},
    {"seed_text": "x" * 2001},
    {"logline": "x" * 401},
    {"premise": "x" * 1501},
    {"tone": "x" * 201},
    {"genre_tags": ["t"] * 9},
    {"genre_tags": ["x" * 41]},
    {"themes_and_values": ["v"] * 7},
    {"why_come_back": ["w"] * 4},
    {"language": "de"},
    {"status": "published"},
    {"episode_template_id": "serial_45s_v1"},
    {"story_id": "ABCDEF123456"},
    {"concept_id": "Bad-Id"},
    {"concept_id": "import:xyz"},
    {"style_template_id": "Fruit"},
    {"$schema": "story_bible_v2"},
    {"created_at": ""},
])
def test_an_invalid_bible_is_refused(changes):
    assert schemas.story_bible_errors(_valid_bible(**changes)) != []


@pytest.mark.parametrize("concept_id", ["tentafruit_island", "custom", "import:0123456789ab", None])
def test_every_kind_of_concept_reference_is_accepted(concept_id):
    assert schemas.story_bible_errors(_valid_bible(concept_id=concept_id)) == []


# ---------------------------------------------------------------- status

@pytest.mark.parametrize("approvals, expected", [
    ({"concept": None, "bible": None, "style": None}, "draft"),
    ({"concept": NOW, "bible": None, "style": None}, "concept_chosen"),
    ({"concept": NOW, "bible": NOW, "style": None}, "bible_approved"),
    ({"concept": NOW, "bible": NOW, "style": NOW}, "style_approved"),
    # Not contiguous: a later approval without the earlier one does not count.
    ({"concept": None, "bible": NOW, "style": None}, "draft"),
    ({"concept": None, "bible": None, "style": NOW}, "draft"),
    ({"concept": None, "bible": NOW, "style": NOW}, "draft"),
    ({"concept": NOW, "bible": None, "style": NOW}, "concept_chosen"),
    ({"concept": "", "bible": NOW, "style": NOW}, "draft"),
    ({}, "draft"),
    (None, "draft"),
    ("style", "draft"),
])
def test_derive_status(approvals, expected):
    assert store.derive_status(approvals) == expected


def test_the_derived_statuses_are_the_first_four_in_order():
    assert defaults.STATUSES[:4] == ("draft", "concept_chosen", "bible_approved", "style_approved")


# ---------------------------------------------------------------- create / get

def test_create_then_get_round_trips(stories, outputs):
    created = stories.create(language="fr", seed_text="Des fruits sur une île.", now=NOW)

    assert stories.get(created["story_id"]) == created
    on_disk = json.loads(_story_path(outputs, created["story_id"]).read_text(encoding="utf-8"))
    assert on_disk == created
    assert [e["story_id"] for e in stories.list()] == [created["story_id"]]


def test_a_new_story_has_every_field_as_a_draft(stories):
    doc = stories.create(language="en", now=NOW)

    # Plan 21 stage 1, re-pinned on purpose: every field but the optional ``approved_by``, which only the
    # agent run's approvals write. Plan 23 stage B5: nor is the optional ``subtitle_style`` on a new story.
    assert set(doc) == set(schemas.STORY_BIBLE_SCHEMA["properties"]) - {"approved_by", "subtitle_style"}
    assert schemas.story_bible_errors(doc) == []
    assert store.STORY_ID_PATTERN.fullmatch(doc["story_id"])
    assert doc["language"] == "en"
    assert doc["approvals"] == {"concept": None, "bible": None, "style": None,
                                "cast": None, "places": None, "season": None}
    assert doc["status"] == "draft"
    assert doc["created_at"] == doc["updated_at"] == NOW
    # Re-pinned on purpose (plan 22 stage 2, DEC-274): every new story is stamped "writing": "v3".
    assert doc["generation_profile"] == dict(defaults.default_generation_profile(), writing=defaults.WRITING_V3)
    assert doc["episode_template_id"] == "serial_60s_v1"
    assert doc["narrator"] == {"enabled": False, "voice": None}
    assert doc["title"] == "" and doc["seed_text"] is None and doc["style_template_id"] is None
    for key in ("genre_tags", "themes_and_values", "why_come_back", "cast_ids", "place_ids", "prop_ids"):
        assert doc[key] == [], key


def test_a_v2_story_opens_with_the_narrator_off(stories):
    """Re-pinned on purpose (plan 28 stage B1, DEC-305: the human, on the
    narrator, "remove"): phase 7 stage 6c opened a v2 story with the
    narrator enabled; every story created from now on opens with it off,
    v2 as legacy (``test_a_new_story_has_every_field_as_a_draft`` pins the
    legacy one). A PATCH may still turn it on, on a story that has voices."""
    doc = stories.create(language="en", generation_profile=defaults.quality_generation_profile(), now=NOW)
    assert doc["generation_profile"]["pipeline"] == defaults.PIPELINE_V2
    assert doc["narrator"] == {"enabled": False, "voice": None}


def test_story_json_keeps_a_readable_field_order(stories, outputs):
    """Never sort_keys: a human reads story.json top to bottom."""
    doc = stories.create(language="fr", now=NOW)
    text = _story_path(outputs, doc["story_id"]).read_text(encoding="utf-8")
    # Plan 21 stage 1, re-pinned on purpose: the optional ``approved_by`` is not on a new story; nor (plan 23
    # stage B5) is the optional ``subtitle_style``.
    assert list(json.loads(text)) == [key for key in schemas.STORY_BIBLE_SCHEMA["properties"]
                                      if key not in ("approved_by", "subtitle_style")]
    assert text.endswith("}\n")


def test_non_ascii_text_is_written_as_is(stories, outputs):
    doc = stories.create(language="fr", seed_text="Un ananas très jaloux", now=NOW)
    assert "très" in _story_path(outputs, doc["story_id"]).read_text(encoding="utf-8")


def test_a_story_must_name_its_language(stories, outputs):
    with pytest.raises(TypeError):
        stories.create(now=NOW)
    for bad in (None, "", "de", "FR", ["fr"]):
        with pytest.raises(ValueError):
            stories.create(language=bad, now=NOW)
    assert list(outputs.iterdir()) == []


def test_a_known_style_template_is_kept(stories):
    style_id = templates.list_style_ids()[0]
    assert stories.create(language="fr", style_template_id=style_id, now=NOW)["style_template_id"] == style_id


@pytest.mark.parametrize("style_id", ["no_such_style", "../fruit_drama", "Fruit_Drama", ""])
def test_an_unknown_style_template_is_refused(stories, outputs, style_id):
    with pytest.raises(ValueError):
        stories.create(language="fr", style_template_id=style_id, now=NOW)
    assert list(outputs.iterdir()) == []


def test_a_partial_generation_profile_is_merged_onto_the_defaults(stories):
    doc = stories.create(language="fr", generation_profile={"route": "local", "tier": 2}, now=NOW)
    # Re-pinned on purpose (plan 22 stage 2, DEC-274): every new story is stamped "writing": "v3".
    assert doc["generation_profile"] == {
        "tier": 2, "route": "local", "consistency_mode": "references", "budget_profile": "free",
        "writing": "v3",
    }


@pytest.mark.parametrize("profile", [
    {"colour": "red"},
    {"tier": 4},
    {"tier": True},
    {"tier": 1.0},
    {"tier": "1"},
    {"route": "cloud"},
    {"consistency_mode": None},
    {"budget_profile": "cheap"},
    "free",
    ["tier"],
])
def test_a_bad_generation_profile_is_refused(stories, outputs, profile):
    with pytest.raises(ValueError):
        stories.create(language="fr", generation_profile=profile, now=NOW)
    assert list(outputs.iterdir()) == []


@pytest.mark.parametrize("changes", [{"seed_text": "x" * 2001}, {"now": None}, {"now": ""}])
def test_a_story_that_does_not_validate_leaves_nothing_on_disk(stories, outputs, changes):
    kwargs = {"language": "fr", "now": NOW, **changes}
    with pytest.raises(schemas.SchemaError):
        stories.create(**kwargs)
    assert list(outputs.iterdir()) == []


def test_an_id_already_on_disk_is_never_reused(outputs):
    ids = iter(["0123456789ab", "0123456789ab", "ba9876543210"])
    stories = store.StoryStore(str(outputs), id_factory=lambda: next(ids), on_log=lambda _: None)
    first = stories.create(language="fr", seed_text="first", now=NOW)
    before = _story_path(outputs, first["story_id"]).read_bytes()

    second = stories.create(language="en", now=LATER)

    assert (first["story_id"], second["story_id"]) == ("0123456789ab", "ba9876543210")
    assert _story_path(outputs, "0123456789ab").read_bytes() == before


def test_a_malformed_id_from_the_factory_is_refused(outputs):
    stories = store.StoryStore(str(outputs), id_factory=lambda: "../escape", on_log=lambda _: None)
    with pytest.raises(ValueError):
        stories.create(language="fr", now=NOW)
    assert list(outputs.iterdir()) == []


def test_new_story_ids_are_twelve_hex():
    assert all(store.STORY_ID_PATTERN.fullmatch(store.new_story_id()) for _ in range(50))


def test_get_of_an_unknown_id_is_a_key_error(stories):
    with pytest.raises(KeyError):
        stories.get("0123456789ab")


def test_a_corrupt_story_raises_and_is_not_repaired(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    path = _story_path(outputs, doc["story_id"])

    path.write_text('{"$schema": "story_bible_v1", "title": ', encoding="utf-8")
    with pytest.raises(schemas.SchemaError):
        stories.get(doc["story_id"])
    assert path.read_text(encoding="utf-8") == '{"$schema": "story_bible_v1", "title": '

    invalid = dict(doc, language="de")
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(schemas.SchemaError):
        stories.get(doc["story_id"])
    assert json.loads(path.read_text(encoding="utf-8")) == invalid


def test_a_story_json_copied_into_another_folder_is_refused(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    (outputs / "stories" / "0123456789ab").mkdir()
    shutil.copy(_story_path(outputs, doc["story_id"]), _story_path(outputs, "0123456789ab"))
    with pytest.raises(schemas.SchemaError):
        stories.get("0123456789ab")


# ---------------------------------------------------------------- update

def test_update_rederives_status_and_bumps_updated_at(stories, outputs):
    doc = stories.create(language="fr", now=NOW)

    def choose(story):
        story["concept_id"] = "tentafruit_island"
        story["title"] = "Tentafruit Island"
        story["approvals"]["concept"] = LATER

    saved = stories.update(doc["story_id"], choose, now=LATER)

    assert saved["status"] == "concept_chosen"
    assert saved["updated_at"] == LATER and saved["created_at"] == NOW
    assert stories.get(doc["story_id"]) == saved
    entry = _index(outputs)["stories"][doc["story_id"]]
    assert entry == {key: saved[key] for key in store.INDEX_FIELDS}


def test_a_directly_set_status_is_overwritten(stories):
    doc = stories.create(language="fr", now=NOW)

    def cheat(story):
        story["status"] = "ready"

    assert stories.update(doc["story_id"], cheat, now=LATER)["status"] == "draft"


def test_clearing_an_approval_lowers_the_status(stories):
    doc = stories.create(language="fr", now=NOW)

    def approve(story):
        story["approvals"].update(concept=NOW, bible=NOW, style=NOW)

    assert stories.update(doc["story_id"], approve, now=LATER)["status"] == "style_approved"

    def clear_bible(story):
        story["approvals"]["bible"] = None

    assert stories.update(doc["story_id"], clear_bible, now=LATEST)["status"] == "concept_chosen"


def test_mutate_works_on_a_copy(stories):
    doc = stories.create(language="fr", now=NOW)
    seen = []
    stories.update(doc["story_id"], seen.append, now=LATER)
    seen[0]["title"] = "changed after the save"
    assert stories.get(doc["story_id"])["title"] == ""


@pytest.mark.parametrize("key, value", [
    ("story_id", "ba9876543210"),
    ("created_at", LATER),
    ("$schema", "story_bible_v2"),
    ("story_id", None),
])
def test_update_refuses_to_change_a_frozen_key(stories, outputs, key, value):
    doc = stories.create(language="fr", now=NOW)
    before = _story_path(outputs, doc["story_id"]).read_bytes()

    def change(story):
        story[key] = value

    with pytest.raises(ValueError):
        stories.update(doc["story_id"], change, now=LATER)
    assert _story_path(outputs, doc["story_id"]).read_bytes() == before


def test_update_refuses_to_drop_a_frozen_key(stories):
    doc = stories.create(language="fr", now=NOW)
    with pytest.raises(ValueError):
        stories.update(doc["story_id"], lambda story: story.pop("created_at"), now=LATER)


def test_an_update_that_does_not_validate_writes_nothing(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    before = _story_path(outputs, doc["story_id"]).read_bytes()
    index_before = (outputs / "stories.json").read_bytes()

    def break_it(story):
        story["language"] = "de"

    with pytest.raises(schemas.SchemaError):
        stories.update(doc["story_id"], break_it, now=LATER)
    assert _story_path(outputs, doc["story_id"]).read_bytes() == before
    assert (outputs / "stories.json").read_bytes() == index_before


def test_a_failing_mutate_writes_nothing(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    before = _story_path(outputs, doc["story_id"]).read_bytes()

    def explode(story):
        story["title"] = "half done"
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        stories.update(doc["story_id"], explode, now=LATER)
    assert _story_path(outputs, doc["story_id"]).read_bytes() == before


# ---------------------------------------------------------------- other documents

def test_write_doc_then_read_doc_round_trips(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    cards = {"$schema": "story_concepts_v1", "cards": [{"title": "Tentafruit"}]}

    saved = stories.write_doc(doc["story_id"], "concepts.json", cards, now=LATER)

    assert saved == dict(cards, updated_at=LATER)
    assert "updated_at" not in cards, "the caller's dict is not modified"
    assert stories.read_doc(doc["story_id"], "concepts.json") == saved
    assert stories.get(doc["story_id"])["updated_at"] == LATER
    assert _index(outputs)["stories"][doc["story_id"]]["updated_at"] == LATER


def test_a_real_style_lock_is_written_with_its_validator(stories):
    doc = stories.create(language="fr", style_template_id="fruit_drama", now=NOW)
    lock = stylelock.build_style_lock(templates.load_style("fruit_drama"), now=NOW)

    saved = stories.write_doc(doc["story_id"], "style_lock.json", lock, now=LATER,
                              validator=schemas.style_lock_errors)

    assert schemas.style_lock_errors(saved) == []
    assert stories.read_doc(doc["story_id"], "style_lock.json") == saved


def test_read_doc_of_a_document_not_written_yet_is_none(stories):
    doc = stories.create(language="fr", now=NOW)
    assert stories.read_doc(doc["story_id"], "style_lock.json") is None


def test_read_doc_of_story_json_is_the_validated_story(stories):
    doc = stories.create(language="fr", now=NOW)
    assert stories.read_doc(doc["story_id"], "story.json") == doc


@pytest.mark.parametrize("name", [
    "../x", "foo.json", "", None, "/etc/passwd", "concepts.json/../story.json",
    "STORY.JSON", "activity.log", "story.json\n",
])
def test_only_the_story_documents_can_be_named(stories, name):
    doc = stories.create(language="fr", now=NOW)
    with pytest.raises(ValueError):
        stories.read_doc(doc["story_id"], name)
    with pytest.raises(ValueError):
        stories.write_doc(doc["story_id"], name, {"$schema": "x_v1"}, now=LATER)


def test_story_json_is_written_through_update_only(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    before = _story_path(outputs, doc["story_id"]).read_bytes()
    with pytest.raises(ValueError):
        stories.write_doc(doc["story_id"], "story.json", dict(doc, status="ready"), now=LATER)
    assert _story_path(outputs, doc["story_id"]).read_bytes() == before


@pytest.mark.parametrize("payload", [{}, {"$schema": 1}, {"$schema": ""}, [], "x"])
def test_a_document_needs_a_schema_string(stories, payload):
    doc = stories.create(language="fr", now=NOW)
    with pytest.raises(ValueError):
        stories.write_doc(doc["story_id"], "concepts.json", payload, now=LATER)


def test_a_validator_failure_leaves_the_old_file_intact(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    stories.write_doc(doc["story_id"], "concepts.json", {"$schema": "story_concepts_v1", "cards": []}, now=LATER)
    path = _story_path(outputs, doc["story_id"], "concepts.json")
    before = path.read_bytes()

    with pytest.raises(schemas.SchemaError):
        stories.write_doc(doc["story_id"], "concepts.json", {"$schema": "story_concepts_v1", "cards": None},
                          now=LATEST, validator=lambda d: ["$.cards: expected a list"])

    assert path.read_bytes() == before
    assert stories.get(doc["story_id"])["updated_at"] == LATER


def test_a_document_of_an_unknown_story_is_refused(stories, outputs):
    with pytest.raises(KeyError):
        stories.write_doc("0123456789ab", "concepts.json", {"$schema": "x_v1"}, now=NOW)
    with pytest.raises(KeyError):
        stories.read_doc("0123456789ab", "concepts.json")
    assert _story_folders(outputs) == []


def test_a_symlinked_document_is_never_read(stories, outputs, tmp_path):
    doc = stories.create(language="fr", now=NOW)
    secret = tmp_path / "secret.json"
    secret.write_text('{"$schema": "secret_v1", "key": "k"}', encoding="utf-8")
    try:
        os.symlink(secret, _story_path(outputs, doc["story_id"], "concepts.json"))
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a symlink here")
    with pytest.raises(ValueError):
        stories.read_doc(doc["story_id"], "concepts.json")


# ---------------------------------------------------------------- atomic writes

def test_writes_leave_no_temp_files_behind(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    other = stories.create(language="en", now=NOW)
    stories.update(doc["story_id"], lambda s: s.update(title="T"), now=LATER)
    stories.write_doc(doc["story_id"], "concepts.json", {"$schema": "story_concepts_v1"}, now=LATER)
    stories.delete(other["story_id"])
    stories.rebuild_index("test")

    assert _temp_files(outputs) == []


def test_a_failure_mid_write_leaves_the_previous_file_byte_identical(stories, outputs, monkeypatch):
    doc = stories.create(language="fr", now=NOW)
    story_before = _story_path(outputs, doc["story_id"]).read_bytes()
    index_before = (outputs / "stories.json").read_bytes()

    def torn_dump(obj, fh, **kwargs):
        fh.write('{"$schema": "story_bible_v1", "title": ')
        raise OSError("No space left on device")

    monkeypatch.setattr(store.json, "dump", torn_dump)
    with pytest.raises(OSError):
        stories.update(doc["story_id"], lambda s: s.update(title="Lost"), now=LATER)
    monkeypatch.undo()

    assert _story_path(outputs, doc["story_id"]).read_bytes() == story_before
    assert (outputs / "stories.json").read_bytes() == index_before
    assert _temp_files(outputs) == []
    assert stories.get(doc["story_id"]) == doc


def test_a_failed_first_write_leaves_no_empty_story_folder(stories, outputs, monkeypatch):
    def failing_dump(obj, fh, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(store.json, "dump", failing_dump)
    with pytest.raises(OSError):
        stories.create(language="fr", now=NOW)
    monkeypatch.undo()

    assert _story_folders(outputs) == []
    assert _temp_files(outputs) == []


def test_written_files_are_readable_from_the_host(stories, outputs):
    """mkstemp makes 0600; the workspace is a bind mount read from the host."""
    doc = stories.create(language="fr", now=NOW)
    for path in (_story_path(outputs, doc["story_id"]), outputs / "stories.json"):
        assert os.stat(path).st_mode & 0o777 == 0o644, path


# ---------------------------------------------------------------- index

def test_the_index_shape(stories, outputs):
    doc = stories.create(language="fr", style_template_id="fruit_drama", now=NOW)
    index = _index(outputs)
    assert index["$schema"] == "stories_index_v1"
    assert index["updated_at"] == NOW
    assert index["stories"] == {doc["story_id"]: {
        "story_id": doc["story_id"], "title": "", "language": "fr",
        "style_template_id": "fruit_drama", "status": "draft",
        "created_at": NOW, "updated_at": NOW,
    }}


def test_list_is_most_recently_updated_first(stories):
    a = stories.create(language="fr", now=NOW)
    b = stories.create(language="fr", now=LATER)
    c = stories.create(language="fr", now=NOW)
    stories.update(c["story_id"], lambda s: None, now=LATEST)

    assert [e["story_id"] for e in stories.list()] == [c["story_id"], b["story_id"], a["story_id"]]


def test_the_first_story_writes_the_index_quietly(stories, logs):
    stories.create(language="fr", now=NOW)
    assert logs == []


def test_list_on_a_fresh_install_is_empty_and_writes_nothing(stories, outputs, logs):
    assert stories.list() == []
    assert list(outputs.iterdir()) == []
    assert logs == []


def test_a_torn_index_is_rebuilt_and_the_rebuild_is_printed(stories, outputs, logs):
    ids = {stories.create(language="fr", now=NOW)["story_id"] for _ in range(3)}
    index = outputs / "stories.json"
    data = index.read_bytes()
    index.write_bytes(data[: len(data) // 2])

    listed = stories.list()

    assert {e["story_id"] for e in listed} == ids
    assert len(logs) == 1
    assert logs[0].startswith("Rebuilt outputs/stories.json from 3 story folder(s): the index is unreadable")
    assert set(_index(outputs)["stories"]) == ids


@pytest.mark.parametrize("content, reason", [
    (None, "the index is missing"),
    ("[]", "the index is not a JSON object"),
    ('{"$schema": "jobs_v1", "stories": {}}', "the index $schema is 'jobs_v1'"),
    ('{"$schema": "stories_index_v1", "stories": []}', "the index has no 'stories' object"),
    ('{"$schema": "stories_index_v1", "stories": {"0123456789ab": {"title": "x"}}}',
     "the index entry '0123456789ab' is malformed"),
    ('{"$schema": "stories_index_v1", "stories": {"../x": {}}}', "the index entry '../x' is malformed"),
])
def test_an_unusable_index_is_rebuilt_with_its_reason(stories, outputs, logs, content, reason):
    doc = stories.create(language="fr", now=NOW)
    index = outputs / "stories.json"
    if content is None:
        index.unlink()
    else:
        index.write_text(content, encoding="utf-8")

    assert [e["story_id"] for e in stories.list()] == [doc["story_id"]]
    assert len(logs) == 1 and logs[0].startswith("Rebuilt outputs/stories.json from 1 story folder(s): ")
    assert reason in logs[0]


def test_a_create_on_a_torn_index_keeps_the_other_stories(stories, outputs, logs):
    first = stories.create(language="fr", now=NOW)
    (outputs / "stories.json").write_text("{", encoding="utf-8")

    second = stories.create(language="en", now=LATER)

    assert set(_index(outputs)["stories"]) == {first["story_id"], second["story_id"]}
    assert any(line.startswith("Rebuilt outputs/stories.json from 2 story folder(s)") for line in logs)


def test_an_invalid_story_folder_is_skipped_and_printed_never_deleted(stories, outputs, logs):
    good = stories.create(language="fr", now=NOW)
    root = outputs / "stories"
    (root / "0123456789ab").mkdir()
    (root / "0123456789ab" / "story.json").write_text("not json", encoding="utf-8")
    (root / "aaaaaaaaaaaa").mkdir()
    (root / "aaaaaaaaaaaa" / "notes.txt").write_text("keep me", encoding="utf-8")
    (root / "bbbbbbbbbbbb").mkdir()
    (root / "bbbbbbbbbbbb" / "story.json").write_text(json.dumps(_valid_bible(story_id="bbbbbbbbbbbb",
                                                                              language="de")), encoding="utf-8")
    (root / "not-a-story").mkdir()
    (outputs / "stories.json").unlink()

    listed = stories.list()

    assert [e["story_id"] for e in listed] == [good["story_id"]]
    skipped = [line for line in logs if line.startswith("Skipped ")]
    assert [line.split(":")[0] for line in skipped] == [
        "Skipped outputs/stories/0123456789ab/",
        "Skipped outputs/stories/aaaaaaaaaaaa/",
        "Skipped outputs/stories/bbbbbbbbbbbb/",
    ]
    assert logs[-1].startswith("Rebuilt outputs/stories.json from 1 story folder(s)")
    assert (root / "0123456789ab" / "story.json").read_text(encoding="utf-8") == "not json"
    assert (root / "aaaaaaaaaaaa" / "notes.txt").exists()
    assert (root / "bbbbbbbbbbbb" / "story.json").exists()
    assert (root / "not-a-story").is_dir()


def test_rebuild_index_returns_how_many_it_found(stories, logs):
    for _ in range(2):
        stories.create(language="fr", now=NOW)
    assert stories.rebuild_index("asked for by hand") == 2
    assert logs == ["Rebuilt outputs/stories.json from 2 story folder(s): asked for by hand"]


# ---------------------------------------------------------------- delete

def test_delete_removes_only_its_own_folder(stories, outputs):
    gone = stories.create(language="fr", now=NOW)
    kept = stories.create(language="en", now=NOW)
    (outputs / "notes.txt").write_text("beside stories/", encoding="utf-8")
    (outputs / "abc123").mkdir()  # a job's folder

    report = stories.delete(gone["story_id"])

    assert report == {"removed": [f"outputs/stories/{gone['story_id']}/"], "kept": []}
    assert not (outputs / "stories" / gone["story_id"]).exists()
    assert stories.get(kept["story_id"]) == kept
    assert list(_index(outputs)["stories"]) == [kept["story_id"]]
    assert (outputs / "notes.txt").exists() and (outputs / "abc123").is_dir()
    assert (outputs / "stories").is_dir()


def test_delete_of_an_unknown_id_is_a_key_error(stories, outputs):
    stories.create(language="fr", now=NOW)
    index_before = (outputs / "stories.json").read_bytes()
    with pytest.raises(KeyError):
        stories.delete("0123456789ab")
    assert (outputs / "stories.json").read_bytes() == index_before


def test_delete_drops_an_index_entry_whose_folder_is_gone(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    shutil.rmtree(outputs / "stories" / doc["story_id"])

    assert stories.delete(doc["story_id"]) == {"removed": [], "kept": []}
    assert _index(outputs)["stories"] == {}


def test_delete_removes_a_folder_the_index_does_not_know(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    (outputs / "stories.json").write_text(
        json.dumps({"$schema": "stories_index_v1", "stories": {}, "updated_at": NOW}), encoding="utf-8")

    assert stories.delete(doc["story_id"])["removed"] == [f"outputs/stories/{doc['story_id']}/"]
    assert not (outputs / "stories" / doc["story_id"]).exists()


def test_delete_with_a_torn_index_rebuilds_it_without_the_story(stories, outputs, logs):
    gone = stories.create(language="fr", now=NOW)
    kept = stories.create(language="fr", now=NOW)
    (outputs / "stories.json").write_text("{", encoding="utf-8")

    stories.delete(gone["story_id"])

    assert list(_index(outputs)["stories"]) == [kept["story_id"]]
    assert any(line.startswith("Rebuilt outputs/stories.json") for line in logs)


def test_a_symlinked_story_folder_is_kept_and_its_target_untouched(stories, outputs, tmp_path, logs):
    doc = stories.create(language="fr", now=NOW)
    precious = tmp_path / "precious"
    shutil.move(str(outputs / "stories" / doc["story_id"]), str(precious))
    (precious / "only_copy.txt").write_text("irreplaceable", encoding="utf-8")
    link = outputs / "stories" / doc["story_id"]
    try:
        os.symlink(precious, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("this platform cannot create a symlink here")

    # Never followed for reading or writing either.
    with pytest.raises(KeyError):
        stories.get(doc["story_id"])
    with pytest.raises(KeyError):
        stories.update(doc["story_id"], lambda s: s.update(title="x"), now=LATER)

    report = stories.delete(doc["story_id"])

    assert report == {"removed": [], "kept": [f"outputs/stories/{doc['story_id']}/ (a symlink, never followed)"]}
    assert os.path.islink(link)
    assert (precious / "only_copy.txt").read_text(encoding="utf-8") == "irreplaceable"
    assert (precious / "story.json").exists()
    assert doc["story_id"] not in _index(outputs)["stories"]

    # A rebuild skips it, and says so.
    stories.rebuild_index("check")
    assert any(line.startswith(f"Skipped outputs/stories/{doc['story_id']}/") for line in logs)


def test_a_file_in_place_of_a_story_folder_is_kept(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    shutil.rmtree(outputs / "stories" / doc["story_id"])
    (outputs / "stories" / doc["story_id"]).write_text("a file", encoding="utf-8")

    report = stories.delete(doc["story_id"])

    assert report["kept"] == [f"outputs/stories/{doc['story_id']}/ (not a directory)"]
    assert (outputs / "stories" / doc["story_id"]).read_text(encoding="utf-8") == "a file"


BAD_IDS = [
    "../x", "ABCDEF123456", "123", "a/b", "/etc", "/tmp/0123456789ab", "", ".", "..",
    "0123456789ab\n", "0123456789abc", "0123456789a/", "0123456789a\\", "g123456789ab",
    None, 7, ["0123456789ab"],
]


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


@pytest.mark.parametrize("story_id", BAD_IDS)
def test_a_malformed_id_is_refused_before_any_filesystem_access(tripwired, story_id):
    with pytest.raises(KeyError):
        tripwired.get(story_id)
    with pytest.raises(KeyError):
        tripwired.update(story_id, lambda s: None, now=NOW)
    with pytest.raises(KeyError):
        tripwired.delete(story_id)
    with pytest.raises(KeyError):
        tripwired.story_dir(story_id)
    with pytest.raises(KeyError):
        tripwired.read_doc(story_id, "concepts.json")
    with pytest.raises(KeyError):
        tripwired.write_doc(story_id, "concepts.json", {"$schema": "x_v1"}, now=NOW)
    assert tripwired.append_activity(story_id, "line") is None


def test_the_tripwire_does_trip(tripwired):
    """The test above would pass vacuously if the tripwire never fired."""
    with pytest.raises(AssertionError):
        tripwired.get("0123456789ab")


# ---------------------------------------------------------------- containment

CONTAINMENT_NAMES = ["", ".", "..", "a/..", "../outputs", "a/b", "a\\b", "/etc", "C:\\Windows", "C:x", None, 7]


@pytest.mark.parametrize("name", CONTAINMENT_NAMES)
def test_anything_but_a_plain_child_name_is_not_contained(tmp_path, name):
    assert store._contained(str(tmp_path), name, want_dir=True) is None


def test_containment_matches_the_job_cleanup_rules(tmp_path):
    """Re-implemented (clipping/ does not import web/), so pinned to the original."""
    from web.api import cleanup

    root = tmp_path / "root"
    root.mkdir()
    (root / "real").mkdir()
    (root / "file.json").write_text("{}", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    names = CONTAINMENT_NAMES + ["real", "file.json", "missing"]
    try:
        os.symlink(elsewhere, root / "link", target_is_directory=True)
        os.symlink(root / "real", root / "inner_link", target_is_directory=True)
        names += ["link", "inner_link"]
    except (OSError, NotImplementedError):
        pass
    for name in names:
        for want_dir in (True, False):
            assert store._contained(str(root), name, want_dir=want_dir) == \
                cleanup.contained(str(root), name, want_dir=want_dir), (name, want_dir)


def test_story_dir_is_the_real_path(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    assert stories.story_dir(doc["story_id"]) == os.path.realpath(outputs / "stories" / doc["story_id"])


def test_story_dir_of_an_unknown_story_is_a_key_error(stories):
    with pytest.raises(KeyError):
        stories.story_dir("0123456789ab")


# ---------------------------------------------------------------- activity

def test_append_activity_appends_one_line_per_call(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    stories.append_activity(doc["story_id"], "✍️ B1 via groq/llama ≈180 tokens out (cap 250)")
    stories.append_activity(doc["story_id"], "line one\nline two\r\nline three")

    text = _story_path(outputs, doc["story_id"], "activity.log").read_text(encoding="utf-8")
    assert text == "✍️ B1 via groq/llama ≈180 tokens out (cap 250)\nline one line two line three\n"


def test_append_activity_never_raises(stories, outputs):
    doc = stories.create(language="fr", now=NOW)
    stories.append_activity("0123456789ab", "unknown story")
    assert not (outputs / "stories" / "0123456789ab").exists()

    (outputs / "stories" / doc["story_id"] / "activity.log").mkdir()  # open() will fail
    stories.append_activity(doc["story_id"], "cannot be written")

    class Unprintable:
        def __str__(self):
            raise RuntimeError("no text")

    stories.append_activity(doc["story_id"], Unprintable())


# ---------------------------------------------------------------- concurrency

def test_two_stores_on_one_root_share_a_lock(outputs):
    a = store.StoryStore(str(outputs), on_log=lambda _: None)
    b = store.StoryStore(str(outputs) + os.sep, on_log=lambda _: None)
    assert a._lock is b._lock


def test_concurrent_creates_on_two_instances_keep_the_index_whole(outputs):
    instances = [store.StoryStore(str(outputs), on_log=lambda _: None) for _ in range(2)]
    barrier = threading.Barrier(8)
    errors = []

    def worker(n):
        try:
            barrier.wait()
            for i in range(5):
                instances[n % 2].create(language="fr", seed_text=f"thread {n} story {i}", now=NOW)
        except Exception as exc:  # surfaced below; a thread cannot fail the test itself
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    listed = instances[0].list()
    assert len(listed) == 40
    assert set(_index(outputs)["stories"]) == set(_story_folders(outputs))
    for story_id in _story_folders(outputs):
        doc = json.loads(_story_path(outputs, story_id).read_text(encoding="utf-8"))
        assert schemas.story_bible_errors(doc) == [], story_id
    assert _temp_files(outputs) == []

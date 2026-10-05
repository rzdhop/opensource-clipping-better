"""Keyframes consistent by construction on a v2 story (phase 8 stage B,
items 1-3 of the brief: the human's "make it so that it doesn't happen").

1. A shot that dresses a character in another wardrobe set than its sheets
   show (the continuity ledger's) never asks for the sheet's outfit too: the
   reference role keeps who the character is and says what it wears here
   (``prompting.role_text``'s ``outfits``; the pure side is in
   ``tests/test_story_shots.py``).
2. Every v2 shot but the first of its scene sends the previous keyframe of
   the scene as a continuity reference, in the slot the storyboard reserved
   (``shots.CONTINUITY_REFERENCE``) -- recorded on the shot
   (``assets.continuity``) and never part of its prompt hash, so a redrawn
   previous keyframe never makes it stale (no cascade of paid redraws).
   With no previous keyframe to send, the shot is asked as it resolves
   without the slot, its roles numbered for the images really sent.
3. J2 sees each on-screen character's identity sheet, reads each character
   as its look and this shot's wardrobe set, and is told whether image 2 is
   in the same scene (J2 prompt version 2); a verdict of the older prompt
   stays readable and is asked again.

The episode is ``tests/test_story_assets_step.py``'s, in ``references``
mode (every portrait, plate and prop image on disk), put on the v2 pipeline
with its storyboard resolved layered (``shots.refresh_prompts``); the editor
is that file's fake on a local link, the vision adapter
``tests/test_story_keyframe_gate.py``'s. Offline and hermetic (the assets
step's ``hermetic`` fixture). Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import copy
import hashlib

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_keyframe_gate as kg
from clipping.aistory import defaults, shots
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_keyframe_gate import unpaced  # noqa: F401 -- the free Gemini tier's pacing lifted

NOW = eps.NOW
KIWILO, MANGELLA, BROCCOLIA = eps.KIWILO, eps.MANGELLA, eps.BROCCOLIA
SETTINGS = tas._settings(**tas.LOCAL_EDIT, VISION_CHAIN="gemini/flash-lite")
GALA_ITEMS = "a white tuxedo with a red silk bow tie"


def _look(build, daily, *, gala=False):
    sets = [{"id": "daily", "context": "every day", "items": daily}]
    if gala:
        sets.append({"id": "gala", "context": "the elimination gala", "items": GALA_ITEMS})
    return {"presentation": "adult", "build": build, "silhouette": "upright human body, fruit head",
            "face": "small dark eyes, thin mouth", "hair": "none", "skin_material": "fuzzy fruit skin",
            "height_cm": 180, "palette": ["brown", "green"], "wardrobe_sets": sets, "season_change": None}


LOOKS = {
    KIWILO: ("tall lean body with a kiwi head", "a sharp tailored charcoal suit"),
    MANGELLA: ("elegant slim body with a mango head", "a tailored emerald green pantsuit"),
    BROCCOLIA: ("tall poised body with a broccoli head", "an emerald velvet evening gown"),
}


def _layered(store, tmp_path, *, looks=False, gala=False):
    """The assets step's references episode on the v2 pipeline, its
    storyboard resolved layered; with *looks* each character has one (its
    sheets drawn in its ``daily`` set), with *gala* Kiwilo's look has a
    ``gala`` set too and the episode's ledger dresses him in it."""
    from clipping.aistory.steps import script as script_step

    story_id = tas._episode(store, tmp_path, mode="references")
    store.update(story_id, lambda doc: doc["generation_profile"].update(pipeline=defaults.PIPELINE_V2), now=NOW)
    if looks:
        for cid, (build, daily) in LOOKS.items():
            doc = store.read_entity(story_id, "characters", cid)
            doc["look"] = _look(build, daily, gala=gala and cid == KIWILO)
            store.write_entity(story_id, "characters", doc, now=NOW)
    if gala:
        knowledge = eps._approved_knowledge()
        knowledge["ledger_seed"] = {KIWILO: {"location": None, "wardrobe_set": "gala", "possessions": [],
                                             "injuries": None, "relationship_notes": None}}
        store.write_knowledge(story_id, knowledge, now=NOW)
    ec = tas._ec(store, story_id)
    board = shots.refresh_prompts(tas._board(store, story_id), eps._script(store, story_id), entities=ec.entities,
                                  style_lock=ec.style_lock, consistency_mode="references", v2=True,
                                  ledger=script_step.ledger_of(ec))
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    return story_id


class SeededImage(tas.FakeImage):
    """The assets step's fake editor, whose image bytes also carry the
    request's seed: a redraw with a fresh seed is another file even from a
    new instance (the parent's bytes count its own requests only)."""

    def generate(self, link, request, *, credentials, on_log, transport=None, **kwargs):
        result = super().generate(link, request, credentials=credentials, on_log=on_log, transport=transport,
                                  **kwargs)
        with open(result.paths[0], "ab") as fh:
            fh.write(f" seed {request.seed}".encode())
        return result


def _adapters(edit=None, vision=None):
    table = tas._adapters(edit=edit or tas.FakeImage())
    table[("vision", "gemini")] = vision or kg.FakeVision()
    return table


def _run(store, story_id, *, edit=None, vision=None, settings=SETTINGS):
    return tas._run(store, story_id, adapters=_adapters(edit, vision), settings=settings)


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _by_name(edit):
    return {request.extra["name"]: request for request in edit.requests}


def _name(shot):
    return f"shot_{shot['shot_id'][2:]}"


def _continuing(board):
    return [i for i in range(len(board["shots"])) if shots.continues_scene(board["shots"], i)]


# ================================================================ 2. continuity

def test_a_shot_after_another_of_its_scene_sends_that_keyframe_in_its_continuity_slot(store, tmp_path):
    from clipping.aistory.steps import assets

    story_id = _layered(store, tmp_path)
    edit = tas.FakeImage()

    summary, _log = _run(store, story_id, edit=edit)

    assert summary["complete"] is True
    ec, board = tas._ec(store, story_id), tas._board(store, story_id)
    requests = _by_name(edit)
    continuing = _continuing(board)
    assert len(continuing) >= 5
    for index, shot in enumerate(board["shots"]):
        request = requests[_name(shot)]
        # Plan 26 (H1): the request carries the master + scene template before the stored core.
        assert shot["prompt_layout"] == "layered_v1" and request.prompt.endswith(shot["image_prompt"])
        if index not in continuing:
            assert shots.CONTINUITY_REFERENCE not in shot["reference_images"]
            assert shot["assets"]["continuity"] is None
            assert len(request.references) == len(shot["reference_images"])
            continue
        previous = board["shots"][index - 1]
        slot = shot["reference_images"].index(shots.CONTINUITY_REFERENCE)
        # The previous keyframe of the scene, in its slot: after the sheets and the plate, before the props.
        assert len(request.references) == len(shot["reference_images"])
        assert request.references[slot] == assets.shot_image_path(ec, previous)
        assert f"Image {slot + 1} is the previous shot of this scene" in request.prompt
        assert shot["assets"]["continuity"] == {"shot_id": previous["shot_id"],
                                                "image_sha256": _sha(assets.shot_image_path(ec, previous))}
        assert assets.shot_state(ec, shot) == "current"


def test_a_redrawn_previous_keyframe_never_makes_the_shot_after_it_stale(store, tmp_path):
    from clipping.aistory.steps import assets

    story_id = _layered(store, tmp_path)
    _run(store, story_id)
    board = tas._board(store, story_id)
    index = _continuing(board)[0]
    previous, shot = board["shots"][index - 1], board["shots"][index]
    kept = copy.deepcopy(shot["assets"])

    # The previous keyframe made again (another seed, another image): the shot after it keeps its own.
    tas._regenerate_shot(store, story_id, previous["shot_id"], note="sharper", adapters=_adapters(SeededImage()),
                         settings=SETTINGS)

    ec, board = tas._ec(store, story_id), tas._board(store, story_id)
    assert _sha(assets.shot_image_path(ec, board["shots"][index - 1])) != kept["continuity"]["image_sha256"]
    assert board["shots"][index]["assets"] == kept
    assert assets.shot_state(ec, board["shots"][index]) == "current"
    assert assets.shots_to_make(ec, board) == []
    edit = tas.FakeImage()
    summary, _log = _run(store, story_id, edit=edit)
    assert edit.requests == [] and summary["complete"] is True


def test_without_the_previous_keyframe_a_shot_is_asked_alone_with_its_own_roles(store, tmp_path):
    from clipping.aistory.steps import assets

    story_id = _layered(store, tmp_path)
    board = tas._board(store, story_id)
    index = _continuing(board)[0]
    previous, shot = board["shots"][index - 1], board["shots"][index]
    edit = tas.FakeImage(fail_for={_name(previous)})

    summary, log = _run(store, story_id, edit=edit)

    assert [item["target"] for item in summary["failed"]] == [f"shot:1:{previous['shot_id']}"]
    ec = tas._ec(store, story_id)
    scene = next(s for s in eps._script(store, story_id)["scenes"] if s["scene_id"] == shot["scene_id"])
    alone = shots.resolve_shot(shots.plan_of(shot, v2=True), scene=scene, entities=ec.entities,
                               style_lock=ec.style_lock, consistency_mode="references", v2=True)
    request = _by_name(edit)[_name(shot)]
    # Asked as it resolves without the slot: every role names an image really sent.
    assert request.prompt.endswith(alone["image_prompt"]) and "previous shot" not in request.prompt
    assert len(request.references) == len(alone["reference_images"]) == len(shot["reference_images"]) - 1
    assert tas._board(store, story_id)["shots"][index]["assets"]["continuity"] is None
    assert f"ℹ️ Shot {shot['shot_id']}: the previous shot of its scene has no keyframe yet, so it is asked " \
           "without its continuity reference." in log
    # Its hash is its stored prompt's: once the previous keyframe is made, it stays current, never redrawn.
    edit = tas.FakeImage()
    summary, _log = _run(store, story_id, edit=edit)
    assert edit.names() == [_name(previous)] and summary["complete"] is True
    assert assets.shot_state(tas._ec(store, story_id), tas._board(store, story_id)["shots"][index]) == "current"


def test_a_legacy_shot_sends_its_references_as_before_and_records_no_continuity(store, tmp_path):
    story_id = tas._episode(store, tmp_path, mode="references")
    edit = tas.FakeImage()

    _run(store, story_id, edit=edit)

    board = tas._board(store, story_id)
    requests = _by_name(edit)
    for shot in board["shots"]:
        assert "continuity" not in shot["assets"] and "prompt_layout" not in shot
        assert shots.CONTINUITY_REFERENCE not in shot["reference_images"]
        assert requests[_name(shot)].prompt == shot["image_prompt"]
        assert len(requests[_name(shot)].references) == len(shot["reference_images"][:4])


# ===================================================================== 1. outfit

def test_a_shot_in_another_set_than_the_sheet_asks_for_that_outfit_alone(store, tmp_path):
    story_id = _layered(store, tmp_path, looks=True, gala=True)
    board = tas._board(store, story_id)
    with_kiwilo = [shot for shot in board["shots"] if f"@{KIWILO}" in shot["subject_tags"]]
    without = [shot for shot in board["shots"] if f"@{KIWILO}" not in shot["subject_tags"]
               and any(tag.startswith("@") for tag in shot["subject_tags"])]
    assert with_kiwilo and without
    kiwi = shots.character_handles(tas._ec(store, story_id).entities["characters"])[KIWILO]
    for shot in with_kiwilo:
        prompt = shot["image_prompt"]
        # His sheet keeps who he is; the gala outfit is the one asked (the role's full or compact form).
        assert (f"{kiwi}'s reference (keep face, hair, build and proportions exactly; here they wear {GALA_ITEMS}, "
                "not the outfit shown)." in prompt
                or f"exactly, except that {kiwi} wears {GALA_ITEMS}." in prompt), prompt
        assert f"{kiwi}'s reference (keep identity, proportions and outfit exactly)" not in prompt
    for shot in without:
        assert "not the outfit shown" not in shot["image_prompt"] and "except that" not in shot["image_prompt"]


# ======================================================================== 3. J2

def _j2_requests(vision):
    return {request.prompt.split("Image 1 is the keyframe of shot ", 1)[1][:4]: request
            for request in vision.requests}


def test_j2_sees_each_on_screen_character_s_sheet_and_this_shot_s_outfit(store, tmp_path):
    story_id = _layered(store, tmp_path, looks=True, gala=True)
    vision = kg.FakeVision()

    _run(store, story_id, vision=vision)

    board = tas._board(store, story_id)
    requests = _j2_requests(vision)
    assert sorted(requests) == [shot["shot_id"] for shot in board["shots"]]
    for index, shot in enumerate(board["shots"]):
        request = requests[shot["shot_id"]]
        cast = list(dict.fromkeys(tag[1:] for tag in shot["subject_tags"] if tag.startswith("@")))[:4]
        first = 2 if index else 1
        assert request.images[first:] == tuple(store.media_path(story_id, "characters", cid, "portrait.jpg")
                                               for cid in cast)
        for number, cid in enumerate(cast, start=first + 1):
            assert f"Image {number} is {eps.NAMES[cid]}'s character sheet." in request.prompt
        if cast:
            assert "A character sheet shows who the character is: face, hair, build and proportions" \
                in request.prompt
        if KIWILO in cast:
            # His look and the set he wears in this episode -- not his descriptor, not his sheet's suit.
            assert ("- Kiwilo: adult, tall lean body with a kiwi head, small dark eyes, thin mouth, none, fuzzy fruit "
                    f"skin; wearing {GALA_ITEMS}") in request.prompt
        if MANGELLA in cast:
            assert "- Mangella: adult, elegant slim body with a mango head" in request.prompt
            assert "wearing a tailored emerald green pantsuit" in request.prompt
    verdicts = tas._assets_doc(store, story_id)["keyframe_verdicts"]
    assert {entry["prompt_version"] for entry in verdicts.values()} == {2}


def test_j2_across_a_scene_change_compares_only_who_the_characters_are(store, tmp_path):
    story_id = _layered(store, tmp_path, looks=True)
    vision = kg.FakeVision()

    _run(store, story_id, vision=vision)

    board = tas._board(store, story_id)
    requests = _j2_requests(vision)
    change = next(i for i in range(1, len(board["shots"])) if not shots.continues_scene(board["shots"], i))
    same = _continuing(board)[0]
    text = requests[board["shots"][change]["shot_id"]].prompt
    assert (f"Image 2 is the keyframe of the shot right before it ({board['shots'][change - 1]['shot_id']}), the "
            "last shot of the previous scene (another place or moment).") in text
    assert ("what changed from image 2 to image 1 in who a character is (face, build, hair, outfit) -- never the "
            "set or the light, which change with the scene") in text
    text = requests[board["shots"][same]["shot_id"]].prompt
    assert f"({board['shots'][same - 1]['shot_id']}), in the same scene." in text
    assert "(a character's face, outfit or size, the set, the light)" in text


def test_a_verdict_of_the_older_j2_stays_readable_and_is_asked_again(store, tmp_path):
    from clipping.aistory.steps import judge

    story_id = _layered(store, tmp_path)
    _run(store, story_id)
    doc = tas._assets_doc(store, story_id)
    first = doc["keyframe_verdicts"]["sh01"]
    assert first["prompt_version"] == 2 and judge.verdict_current(first, first["image_sha256"], None)

    # Stage 6b's verdict, as written before the stamp existed: still a valid document.
    del doc["keyframe_verdicts"]["sh01"]["prompt_version"]
    store.write_episode_doc(story_id, 1, "assets.json", doc, now=NOW)
    old = tas._assets_doc(store, story_id)["keyframe_verdicts"]["sh01"]
    assert judge.verdict_version(old) == 1 and not judge.verdict_current(old, old["image_sha256"], None)

    vision = kg.FakeVision()
    _run(store, story_id, vision=vision)
    assert vision.shots() == ["sh01"]
    assert tas._assets_doc(store, story_id)["keyframe_verdicts"]["sh01"]["prompt_version"] == 2

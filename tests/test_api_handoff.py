"""The handoff document and its routes (plan 25 stage 2, D-2).

``GET /api/stories/{id}/episodes/{ep}/handoff?platform=&model=`` composes the
clip brief, the image brief and stage 1's per-shot modes into one JSON the
Handoff view reads: per shot an ``image`` and a ``clip`` block (mode, state,
the prompt -- the briefs' own, nothing rebuilt -- the references, the upload
slot, a per-shot zip, and for an ``auto`` row the link, the estimate and the
gate's verdict), the entities (sheets, variants, plates, props), the counts,
what is missing and the next missing. The platform and model are remembered in
``assets.json`` (``handoff = {platform, model}``) by ``PATCH .../handoff``.
``GET .../shots/{id}/references.zip`` hands one shot's reference files and
``GET .../image-brief.zip?ep=`` the image brief with its references. The
briefs themselves are unchanged. No auth, as every story route.

Real ffmpeg on tiny fixture clips; no provider call.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

import test_story_assets_step as tas
import test_story_native_speech_plan as nsp
from test_api_shot_upload import api  # noqa: F401 - the upload routes' client
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from test_story_shot_modes import FAST, PAID, _keyframe

URL = "/api/stories/{}/episodes/1/handoff"
MODE = "/api/stories/{}/episodes/1/shots/{}/mode"
MANUAL = "manual/upload"


def _manual_images(api):
    """The manual profile with the images the human's own too: every sheet, plate, prop and keyframe to make."""
    story_id = nsp.planned_story(api.store, profile="native_speech_manual")
    api.store.update(story_id, lambda doc: doc["generation_profile"].update(images="manual"), now=tas.NOW)
    return story_id


def _get(api, story_id, query=""):
    response = api.client.get(URL.format(story_id) + query)
    assert response.status_code == 200, response.text
    return response.json()


def _briefs(api, story_id, platform="flow"):
    from clipping.aistory import workflow
    from clipping.aistory.steps import brief
    from web.api import worker

    ec = tas._ec(api.store, story_id)
    story = workflow.load(api.store, story_id)
    return (brief.shot_brief(ec, platform=platform),
            brief.image_brief(api.store, story, env=worker.get_settings_env(), ec=ec))


# ================================================================ (a) the shape

def test_the_handoff_composes_each_shot_from_the_two_briefs(api):
    """Fail-first: the route did not exist. A manual episode whose images are
    manual too: every clip and keyframe is missing and the human's, the first
    thing to make is named, and each shot's blocks hold the briefs' own
    prompts, references and slots -- equal to what ``shot_brief`` and
    ``image_brief`` say for that shot."""
    story_id = _manual_images(api)
    body = _get(api, story_id)
    clip_brief, image_brief = _briefs(api, story_id)
    assert body["$schema"] == "handoff_v1" and body["story_id"] == story_id and body["ep"] == 1

    platform = body["platform"]
    assert (platform["platform"], platform["name"], platform["model"]) == ("flow", "Google Flow (Veo 3.1)", "veo-3.1-fast")
    assert platform["url"] == clip_brief["platform"]["url"] and platform["where_to_paste"]
    assert platform["prompt_notes"] == clip_brief["platform"]["prompt_notes"]
    assert [model["id"] for model in platform["models"]] == ["veo-3.1-fast", "veo-3.1-quality"]
    assert set(platform["models"][0]) == {"id", "label", "lengths", "max_references", "speech", "languages"}
    assert platform["credits"] and [item["platform"] for item in platform["choices"]] == ["flow", "higgsfield"]

    shots = body["shots"]
    assert [shot["order"] for shot in shots] == sorted(shot["order"] for shot in shots)
    total = len(shots)
    assert total >= 2 and total == clip_brief["counts"]["total"]
    assert body["counts"]["clips"] == {"total": total, "done": 0, "made": 0, "uploaded": 0, "missing": total}
    assert body["counts"]["keyframes"] == {"total": total, "done": 0, "made": 0, "uploaded": 0, "missing": total}
    entities = [entry for entry in image_brief["images"] if entry["kind"] != "keyframe"]
    todo = [entry for entry in entities if entry["state"] == "missing"]
    assert todo and len(todo) < len(entities)  # the fixture draws some of them: the rest are the human's
    assert body["counts"]["entities"] == {"total": len(entities), "done": len(entities) - len(todo),
                                          "missing": len(todo)}

    for shot in shots:
        brief_entry = next(item for item in clip_brief["shots"] if item["shot_id"] == shot["shot_id"])
        keyframe = next(item for item in image_brief["images"] if item["kind"] == "keyframe"
                        and item["id"] == shot["shot_id"])
        assert shot["scene_id"] == brief_entry["scene_id"] and shot["purpose"] == brief_entry["purpose"]
        assert (shot["speaks"], shot["length_s"], shot["clip_s"], shot["aspect"]) == (
            brief_entry["speaks"], brief_entry["length_s"], brief_entry["clip_s"], "9:16")
        clip, image = shot["clip"], shot["image"]
        # The prompts are the briefs' own, byte for byte.
        assert clip["prompt"] == brief_entry["prompt"] and clip["negative_prompt"] == brief_entry["negative_prompt"]
        assert clip["references"] == brief_entry["references"] and clip["checks"] == brief_entry["checks"]
        assert (clip["line"], clip["speaker"], clip["voice_line"]) == (
            brief_entry["line"], brief_entry["speaker"], brief_entry["voice_line"])
        assert clip["upload_slot"] == brief_entry["upload_slot"] == (
            f"/api/stories/{story_id}/episodes/1/shots/{shot['shot_id']}/clip")
        assert (clip["mode"], clip["mode_explicit"], clip["state"], clip["gate"]) == ("manual", False, "missing", None)
        assert (clip["link"], clip["est_usd"], clip["model"]) == (MANUAL, 0.0, brief_entry["model"])
        assert image["prompt"] == keyframe["prompt"] and image["negative_prompt"] == keyframe["negative_prompt"]
        assert image["references"] == keyframe["references"] and image["upload_slot"] == keyframe["upload_slot"]
        assert image["size"] == keyframe["size"] == [1080, 1920] and image["min_size"] == keyframe["min_size"]
        assert (image["mode"], image["mode_explicit"], image["state"], image["gate"]) == (
            "manual", False, "missing", None)
        if clip["references"]:
            assert clip["zip_url"] == (f"/api/stories/{story_id}/episodes/1/shots/{shot['shot_id']}"
                                       "/references.zip?platform=flow&model=veo-3.1-fast")
        else:
            assert clip["zip_url"] is None

    # What is the human's, in the order to make it: the entities first (everything else uses them), then each
    # shot's keyframe and its clip; the first of them is the next.
    missing = body["missing"]
    assert [(item["kind"], item["id"], item["slot"]) for item in missing[:len(todo)]] == [
        (entry["kind"], entry["id"], entry["slot"]) for entry in todo]
    assert [(item["kind"], item["id"]) for item in missing[len(todo):]] == [
        (kind, shot["shot_id"]) for shot in shots for kind in ("keyframe", "clip")]
    assert body["next_missing"] == missing[0] and all(item["label"] for item in missing)
    assert [item["id"] for item in body["entities"]] == [entry["id"] for entry in entities]
    first = body["entities"][0]
    assert first["prompt"] and first["size"] and first["upload_slot"] and first["mode"] == "manual"
    assert first["state"] == entities[0]["state"]
    assert body["export"]["brief_zip"] == f"/api/stories/{story_id}/episodes/1/brief.zip?platform=flow"
    assert body["export"]["image_brief_zip"] == f"/api/stories/{story_id}/image-brief.zip?ep=1"
    assert body["export"]["brief_md"].startswith("# ")
    # Plan 26 stage 3: the master prompt every shot prompt carries; each clip row its fit.
    master = body["master_prompt"]
    assert master["text"].startswith("SERIES:") and master["words"] > 0
    assert master == clip_brief["master_prompt"] and "## Master prompt" in body["export"]["brief_md"]
    for shot in shots:
        brief_entry = next(item for item in clip_brief["shots"] if item["shot_id"] == shot["shot_id"])
        assert shot["clip"]["prompt"].startswith("SERIES:") and shot["clip"]["fit"] == brief_entry["fit"]
        assert shot["clip"].get("prompt_warning") == brief_entry.get("prompt_warning")
        # Stage 4a: each keyframe row too, the image brief's own (a hand-made keyframe: unbounded).
        keyframe = next(item for item in image_brief["images"] if item["kind"] == "keyframe"
                        and item["id"] == shot["shot_id"])
        assert shot["image"]["prompt"].startswith("SERIES:") and shot["image"]["fit"] == keyframe["fit"]
        assert shot["image"]["fit"]["limit"] is None and shot["image"]["fit"]["dropped"] == []
        assert shot["image"].get("prompt_warning") == keyframe.get("prompt_warning")


def test_an_exchange_shot_s_clip_row_names_its_speakers_and_its_lines(api):
    """Plan 27 stage 3: the handoff's clip block of an exchange shot carries
    the brief's own ``line`` (joined), ``speaker`` (the first), ``speakers``,
    ``lines`` and checks; a one-line shot's block has no ``speakers``."""
    story_id = nsp.exchange_story(api.store, profile="native_speech_manual")
    shot = nsp.exchange_shot(api.store, story_id)
    body = _get(api, story_id)
    clip_brief, _image_brief = _briefs(api, story_id)
    entry = next(item for item in clip_brief["shots"] if item["shot_id"] == shot["shot_id"])
    clip = next(item for item in body["shots"] if item["shot_id"] == shot["shot_id"])["clip"]
    assert (clip["line"], clip["speaker"], clip["speakers"], clip["checks"]) == (
        entry["line"], entry["speaker"], entry["speakers"], entry["checks"])
    assert len(clip["speakers"]) == 2 and clip["line"].startswith(f"{clip['speakers'][0]}: ")
    assert [row["line_id"] for row in clip["lines"]] == shot["lines"]
    assert clip["checks"][1].startswith("2 lines, spoken as written")
    single = next(item for item in body["shots"] if item["speaks"] and item["shot_id"] != shot["shot_id"])
    assert "speakers" not in single["clip"] and "lines" not in single["clip"]


def test_the_handoff_leaves_the_briefs_as_they_were(api):
    """The existing briefs are untouched: the same JSON before and after the
    handoff was asked, with the platform remembered."""
    story_id = _manual_images(api)
    before = _briefs(api, story_id)
    _get(api, story_id)
    api.client.patch(URL.format(story_id), json={"platform": "higgsfield", "model": "seedance-2.0"})
    assert _briefs(api, story_id) == before
    assert "handoff" not in json.dumps(before)


# ================================================================ (b) an auto shot and its gate

def test_an_auto_shot_carries_its_link_the_estimate_and_the_gate_s_sentence(api, monkeypatch):
    """A shot flipped to auto on the manual profile: its clip block names the
    link its mode puts it on, the estimate, and the gate says allowed with
    the key set, refused with the reason without one. A manual shot's gate
    stays null; the explicit mode is flagged."""
    from web.api import worker

    story_id = nsp.planned_story(api.store, profile="native_speech_manual")
    shots = [shot["shot_id"] for shot in tas._board(api.store, story_id)["shots"] if shot.get("speaks")]
    auto, manual = shots[0], shots[1]
    assert api.client.patch(MODE.format(story_id, auto), json={"clip": "auto"}).status_code == 200

    keyless = {shot["shot_id"]: shot for shot in _get(api, story_id)["shots"]}
    clip = keyless[auto]["clip"]
    assert (clip["mode"], clip["mode_explicit"], clip["link"]) == ("auto", True, FAST)
    assert clip["gate"]["allowed"] is False and "GEMINI_PAID_API_KEY is not set" in clip["gate"]["reason"]
    assert keyless[manual]["clip"]["gate"] is None and keyless[manual]["clip"]["mode"] == "manual"

    monkeypatch.setattr(worker, "_settings_env", tas._settings(**PAID))
    keyed = {shot["shot_id"]: shot for shot in _get(api, story_id)["shots"]}
    clip = keyed[auto]["clip"]
    assert clip["gate"]["allowed"] is True and clip["gate"]["reason"] == "paid, allowed"
    assert clip["est_usd"] > 0 and clip["est_usd"] == clip["gate"]["est_usd"] and clip["link"] == FAST
    assert keyed[manual]["clip"]["est_usd"] == 0.0 and keyed[manual]["clip"]["link"] == MANUAL
    # An auto clip is not the human's to make: it is not on their list.
    body = _get(api, story_id)
    assert (("clip", auto) not in [(item["kind"], item["id"]) for item in body["missing"]]
            and ("clip", manual) in [(item["kind"], item["id"]) for item in body["missing"]])
    # The keyframes are the app's on this story: auto, priced by the image quote or refused with its reason.
    image = keyed[auto]["image"]
    assert image["mode"] == "auto" and image["mode_explicit"] is False
    assert set(image["gate"]) == {"link", "est_usd", "allowed", "reason"}
    assert body["counts"]["keyframes"]["total"] == len(body["shots"])


# ================================================================ (c) the platform and model memory

def test_the_platform_and_model_are_remembered_per_episode(api):
    story_id = _manual_images(api)
    assert _get(api, story_id)["platform"]["platform"] == "flow"

    saved = api.client.patch(URL.format(story_id), json={"platform": "higgsfield", "model": "seedance-2.0"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["handoff"] == {"platform": "higgsfield", "model": "seedance-2.0"}
    assert tas._assets_doc(api.store, story_id)["handoff"] == {"platform": "higgsfield", "model": "seedance-2.0"}

    remembered = _get(api, story_id)
    assert (remembered["platform"]["platform"], remembered["platform"]["model"]) == ("higgsfield", "seedance-2.0")
    assert {shot["clip"]["model"] for shot in remembered["shots"] if shot["clip"]} == {"seedance-2.0"}
    assert remembered["export"]["brief_zip"].endswith("/brief.zip?platform=higgsfield")
    # The query beats the memory, which stays what it was.
    flow = _get(api, story_id, "?platform=flow")
    assert (flow["platform"]["platform"], flow["platform"]["model"]) == ("flow", "veo-3.1-fast")
    assert _get(api, story_id)["platform"]["model"] == "seedance-2.0"
    kling = _get(api, story_id, "?platform=higgsfield&model=veo-3.1")
    assert kling["platform"]["model"] == "veo-3.1"
    # The platform with no model: its default (the saved model belongs to the saved platform's choice).
    assert api.client.patch(URL.format(story_id), json={"platform": "flow"}).json()["handoff"] == {"platform": "flow"}
    assert _get(api, story_id)["platform"]["model"] == "veo-3.1-fast"

    unknown = api.client.patch(URL.format(story_id), json={"platform": "higgsfield", "model": "sora-9"})
    assert unknown.status_code == 400 and "sora-9" in json.dumps(unknown.json())
    assert api.client.patch(URL.format(story_id), json={"platform": "sora"}).status_code == 400
    assert api.client.patch(URL.format(story_id), json={}).status_code == 400
    assert api.client.get(URL.format(story_id) + "?platform=higgsfield&model=nope").status_code == 400
    assert api.client.get(URL.format(story_id) + "?platform=sora").status_code == 400
    assert api.client.get("/api/stories/nope/episodes/1/handoff").status_code == 404
    # An episode with no storyboard: the brief's own answer (409), not a new one.
    no_board = api.client.get(f"/api/stories/{story_id}/episodes/9/brief")
    assert no_board.status_code == 409
    assert api.client.get(URL.format(story_id).replace("episodes/1", "episodes/9")).status_code == 409
    assert tas._assets_doc(api.store, story_id)["handoff"] == {"platform": "flow"}


# ================================================================ (d) the per-shot references zip

def test_a_shot_s_references_zip_holds_exactly_that_shot_s_files(api):
    """A shot whose keyframe is a real file: its clip zip holds that file under the name the brief zip gives it
    (``references/<file>``) and nothing of another shot's; the keyframe's own references zip is the image
    brief's."""
    from clipping.aistory.steps import brief

    story_id = _manual_images(api)
    first, second = [shot["shot_id"] for shot in tas._board(api.store, story_id)["shots"]][:2]
    _keyframe(api.store, story_id, first)
    _keyframe(api.store, story_id, second)
    ec = tas._ec(api.store, story_id)
    clip_brief, image_brief = _briefs(api, story_id)
    entries = {item["shot_id"]: item for item in clip_brief["shots"]}
    body = {shot["shot_id"]: shot for shot in _get(api, story_id)["shots"]}
    assert body[first]["clip"]["references"] == entries[first]["references"]
    assert body[first]["clip"]["references"][0]["kind"] == "keyframe" and body[first]["clip"]["zip_url"]
    url = f"/api/stories/{story_id}/episodes/1/shots/{first}/references.zip"
    response = api.client.get(url + "?platform=flow")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert "references_sh" in response.headers["content-disposition"] or first in response.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    expected = sorted(f"references/{ref['file']}" for ref in entries[first]["references"]
                      if brief.reference_file(ec, ref))
    assert names and sorted(names) == expected
    assert f"references/{first}_1_keyframe.png" in names
    assert not [name for name in names if name.startswith(f"references/{second}_")]
    # Another shot's zip names its own files only.
    other = api.client.get(f"/api/stories/{story_id}/episodes/1/shots/{second}/references.zip").content
    assert f"references/{second}_1_keyframe.png" in zipfile.ZipFile(io.BytesIO(other)).namelist()
    assert f"references/{first}_1_keyframe.png" not in zipfile.ZipFile(io.BytesIO(other)).namelist()
    # The keyframe's references: the image brief's own files (a shot's sheets and plate, named by the image brief).
    keyframe = next(item for item in image_brief["images"] if item["kind"] == "keyframe" and item["id"] == first)
    image_zip = api.client.get(url + "?kind=image")
    assert image_zip.status_code == 200
    assert sorted(zipfile.ZipFile(io.BytesIO(image_zip.content)).namelist()) == sorted(
        f"references/{ref['file']}" for ref in keyframe["references"] if brief.reference_file(ec, ref))
    assert api.client.get(url.replace(first, "sh99")).status_code == 404
    assert api.client.get(url + "?platform=sora").status_code == 400
    assert api.client.get(url + "?kind=video").status_code == 400


# ================================================================ (e) the image-brief zip

def test_the_image_brief_zip_holds_the_markdown_the_json_and_the_references(api):
    story_id = _manual_images(api)
    response = api.client.get(f"/api/stories/{story_id}/image-brief.zip?ep=1")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "image_brief_ep01" in response.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    assert {"image_brief.md", "image_brief.json"} <= set(names)
    assert archive.read("image_brief.md").decode().startswith("# Image brief")
    brief = json.loads(archive.read("image_brief.json"))
    assert brief["$schema"] == "image_brief_v1" and brief["ep"] == 1
    assert brief == api.client.get(f"/api/stories/{story_id}/image-brief?ep=1").json()
    assert all(name.startswith("references/") for name in names if name not in ("image_brief.md", "image_brief.json"))
    # Without an episode: the story's sheets, plates and props alone.
    story_only = api.client.get(f"/api/stories/{story_id}/image-brief.zip")
    assert story_only.status_code == 200
    assert all(entry["kind"] != "keyframe" for entry in json.loads(
        zipfile.ZipFile(io.BytesIO(story_only.content)).read("image_brief.json"))["images"])
    assert api.client.get("/api/stories/nope/image-brief.zip").status_code == 404
    assert (api.client.get(f"/api/stories/{story_id}/image-brief.zip?ep=9").status_code
            == api.client.get(f"/api/stories/{story_id}/image-brief?ep=9").status_code)
    assert api.client.get(f"/api/stories/{story_id}/image-brief.zip?ep=x").status_code == 404


# ================================================================ the core without the web layer

def test_the_handoff_and_its_memory_need_no_web_layer(store, monkeypatch):
    """The document and the memory are the step's and the workflow's own (they run where the web stack is not
    installed): the default platform and the model asked for, an unknown model refused with its sentence, the
    memory written to ``assets.json`` (valid, carried by the assets step's own rewrite, never in the
    approval's fingerprint) and the briefs left as they were."""
    from clipping.aistory import platforms, workflow
    from clipping.aistory.steps import assets, brief
    from test_story_native_take import FakeVeo, _require_ffmpeg
    from test_story_shot_modes import _host

    _require_ffmpeg()
    monkeypatch.setattr(assets, "keyframe_problem", lambda *_args, **_kwargs: None)
    story_id = nsp.planned_story(store, profile="native_speech_manual")
    ec = tas._ec(store, story_id)
    story = workflow.load(store, story_id)
    env = tas._settings()
    before = (brief.shot_brief(ec, platform="flow"), brief.image_brief(store, story, env=env, ec=ec))

    document = brief.handoff(store, story, env, ec)
    assert document["platform"]["platform"] == "flow" and document["platform"]["model"] == "veo-3.1-fast"
    assert {shot["clip"]["model"] for shot in document["shots"]} == {"veo-3.1-fast"}
    other = brief.handoff(store, story, env, ec, platform="higgsfield", model="seedance-2.0")
    assert other["platform"]["model"] == "seedance-2.0"
    assert {shot["clip"]["model"] for shot in other["shots"]} == {"seedance-2.0"}
    assert [shot["clip"]["prompt"] for shot in other["shots"]] == [
        entry["prompt"] for entry in brief.shot_brief(ec, platform="higgsfield", model="seedance-2.0")["shots"]]
    with pytest.raises(platforms.PresetError, match="sora-9"):
        brief.handoff(store, story, env, ec, platform="flow", model="sora-9")

    def fingerprint():
        fresh = tas._ec(store, story_id)
        return assets.current_fingerprint(fresh, tas._board(store, story_id), tas.eps._script(store, story_id),
                                          tas._assets_doc(store, story_id))

    workflow.patch_shot_mode(store, story_id, 1, tas._board(store, story_id)["shots"][0]["shot_id"],
                             {"clip": "manual"}, now=tas.NOW)  # an assets.json to approve against
    approval_before = fingerprint()
    answer = workflow.patch_handoff(store, story_id, 1, {"platform": "higgsfield", "model": "kling-3.0"}, now=tas.NOW)
    assert answer == {"handoff": {"platform": "higgsfield", "model": "kling-3.0"}, "platform": "higgsfield",
                      "model": "kling-3.0"}
    assert tas._assets_doc(store, story_id)["handoff"] == {"platform": "higgsfield", "model": "kling-3.0"}
    # The same again writes nothing new; no model: the platform's own default.
    assert workflow.patch_handoff(store, story_id, 1, {"platform": "higgsfield"}, now=tas.NOW)["model"] == "veo-3.1"
    assert tas._assets_doc(store, story_id)["handoff"] == {"platform": "higgsfield"}
    for bad in ({}, {"platform": "sora"}, {"platform": "flow", "model": "kling-3.0"}, {"model": "veo-3.1"},
                {"platform": "flow", "colour": "red"}):
        with pytest.raises(workflow.WorkflowError) as caught:
            workflow.patch_handoff(store, story_id, 1, bad, now=tas.NOW)
        assert caught.value.code == workflow.INVALID
    assert fingerprint() == approval_before

    # The assets step rewrites assets.json from what it carries: the memory is among it.
    host, _log, _units = _host(store, story_id, settings={}, veo=FakeVeo([]))
    host.write_assets_doc()
    assert tas._assets_doc(store, story_id)["handoff"] == {"platform": "higgsfield"}
    assert (brief.shot_brief(tas._ec(store, story_id), platform="flow"),
            brief.image_brief(store, story, env=env, ec=tas._ec(store, story_id))) == before

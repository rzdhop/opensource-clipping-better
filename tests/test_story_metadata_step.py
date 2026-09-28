"""The ``metadata`` step and its ``metadata:<ep>:<platform>`` regenerate (AI
Story phase 4, stage 9; spec 2.10, 3 step 12, 4.2 row M1; DEC-115, DEC-159,
DEC-161, DEC-166).

The episode is ``tests/test_story_render_step.py``'s: stage 8's French
fixture with its assets approved, rendered here with stage 7's fake ffmpeg.
M1 answers through phase 3's ``FakeLLM`` (or, for DEC-115, the real
``llm.run_chain`` with a fake client factory); the cover's ffmpeg is a fake
that writes the file it is asked for. Offline and hermetic (stage 8's
``hermetic`` fixture).

The step module is imported inside the tests, so on the parent commit
(``f499d9f``) each test fails on its own.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import functools
import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_aistory_render_runner as rr
import test_story_episode_steps as eps
import test_story_render_step as trs
from clipping.aistory import prompts, schemas
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used here as they are
from test_story_render_step import built  # noqa: F401 -- the session's episode copies

NOW = eps.NOW
TEASER = eps.TEASER  # "Demain, Kiwilo joue sa dernière carte."
HOOK = eps.HOOK_PART["on_screen_text"]  # "Vote surprise ce soir"


def _meta():
    from clipping.aistory.steps import metadata

    return metadata


# ------------------------------------------------------------------ fakes

def _platform_of(user):
    return next(p for p, rules in prompts.M1_PLATFORM_RULES.items() if f"Write the {rules['name']} post" in user)


def m1_reply(call):
    """A reply every platform accepts: three tags (one without "#", one
    with a space in it) and, when asked, the English fields."""
    platform = _platform_of(call["user"])
    reply = {"title": f"Le coco sonne deux fois ({platform})",
             "description": "Kiwilo cache un secret et l alliance vacille.",
             "hashtags": ["tentafruit", "#coco", "télé réalité"], "hook_text": "Le vote tombe ce soir"}
    if "title_en" in call["schema"]["properties"]:
        reply.update(title_en=f"The coconut rings twice ({platform})",
                     hashtags_en=["#tentafruit", "coconut", "reality tv"])
    return reply


def bad_reply(call):
    return dict(m1_reply(call), hashtags=["only-one"])


class FakeCover:
    """The cover's ffmpeg: records the command and writes its output file
    (or fails with *stderr*)."""

    def __init__(self, *, fail=None):
        self.fail = fail
        self.calls = []

    def __call__(self, argv, *, cwd, stdin, capture_output, text, timeout):
        assert stdin is subprocess.DEVNULL and capture_output and text and timeout
        self.calls.append({"argv": list(argv), "cwd": cwd})
        if self.fail:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr=self.fail)
        Path(cwd, argv[-1]).write_bytes(b"\xff\xd8\xff\xe0 fake cover " + str(len(self.calls)).encode())
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def llm(**queues):
    return eps.FakeLLM(default={"M1": m1_reply}, **queues)


# ---------------------------------------------------------------- episode

def rendered(store, tmp_path, built):
    story_id = trs.episode(store, tmp_path, built)
    trs.render(store, story_id, tmp_path=tmp_path)
    return story_id


def ctx_for(store, story_id, *, step="metadata", settings=None):
    return eps._ctx(store, story_id, step=step, settings=eps.SETTINGS if settings is None else settings)


def run_step(store, story_id, *, runner=None, cover=None, settings=None, tmp_path=None):
    runner = runner or llm()
    cover = cover or FakeCover()
    ctx, log = ctx_for(store, story_id, settings=settings)
    fonts_dir = (tmp_path or Path(store.outputs_dir).parent) / "no_custom_fonts"
    summary = _meta().run(ctx, runner=runner, time_fn=eps.Clock(100.0), run_process=cover,
                          custom_fonts_dir=fonts_dir)
    return summary, log, runner, cover


def failed(store, story_id, **kwargs):
    with pytest.raises(eps.steps.StepFailed) as caught:
        run_step(store, story_id, **kwargs)
    return str(caught.value)


def pack(store, story_id):
    return store.read_episode_doc(story_id, 1, "metadata_pack.json")


def ep_dir(store, story_id) -> Path:
    return trs.ep_dir(store, story_id)


def _sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rerender(store, story_id, content=b"another render"):
    """What a new render leaves: another final file, its sha in the manifest."""
    final = ep_dir(store, story_id) / "episode_final.mp4"
    final.write_bytes(content)
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    manifest["output"]["sha256"] = _sha(final)
    store.write_episode_doc(story_id, 1, "render_manifest.json", manifest, now=NOW)


# ================================================================ the step

def test_the_pack_is_one_m1_call_per_platform_and_python_builds_the_rest(store, tmp_path, built):
    story_id = rendered(store, tmp_path, built)
    before = eps._story_bytes(store, story_id)
    script = store.read_episode_doc(story_id, 1, "script.json")

    summary, log, runner, cover = run_step(store, story_id, tmp_path=tmp_path)

    # one M1 per platform, in order, each told its own platform and the episode
    assert runner.prompts() == ["M1", "M1", "M1"]
    assert [_platform_of(call["user"]) for call in runner.calls] == list(schemas.PLATFORMS)
    for call in runner.calls:
        assert call["max_tokens"] == prompts.MAX_TOKENS["M1"] and call["schema_name"] == "episode_metadata"
        assert f"Hook on screen: {HOOK}\n" in call["user"] and f"{TEASER}\n" in call["user"]
        assert "Characters: Kiwilo, Mangella, Broccolia\n" in call["user"]
        assert f"Episode 1: {script['title']}\n" in call["user"]
        assert "Write all user-facing text in French." in call["system"]

    doc = pack(store, story_id)  # read and validated by the store (metadata_pack_v1)
    final = ep_dir(store, story_id) / "episode_final.mp4"
    assert (doc["language"], doc["script_rev"], doc["render_sha256"]) == ("fr", script["rev"], _sha(final))
    assert list(doc["platforms"]) == list(schemas.PLATFORMS)
    for platform, entry in doc["platforms"].items():
        assert entry["title"] == f"Le coco sonne deux fois ({platform})"
        # the teaser appended by Python, the elision repaired (F1)
        assert entry["description"] == f"Kiwilo cache un secret et l'alliance vacille.\n\n{TEASER}"
        assert entry["pinned_comment"] == f"{TEASER} PARTIE 2 →"
        # every tag carries its "#", one word
        assert entry["hashtags"] == ["#tentafruit", "#coco", "#téléréalité"]
        assert entry["title_en"] == f"The coconut rings twice ({platform})"
        assert entry["hashtags_en"] == ["#tentafruit", "#coconut", "#realitytv"]
        assert entry["hook_text"] == "Le vote tombe ce soir" and entry["cover"] == "cover.jpg"
    assert schemas.metadata_pack_errors(doc) == []

    # the cover: the hook scene's first shot, the hook's own text, one ffmpeg
    assert len(cover.calls) == 1
    board = store.read_episode_doc(story_id, 1, "storyboard.json")
    hook_scene = next(s for s in script["scenes"] if s["function"] == "hook")
    first = next(s for s in board["shots"] if s["scene_id"] == hook_scene["scene_id"])
    image = ep_dir(store, story_id) / first["assets"]["image"]
    staged = f"in/{_sha(image)}.png"
    assert cover.calls[0]["argv"] == [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", staged, "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,ass=cover.ass:fontsdir=fonts",
        "-frames:v", "1", "-update", "1", "-q:v", "2", "cover.part.jpg"]
    render_dir = ep_dir(store, story_id) / "render"
    assert Path(cover.calls[0]["cwd"]) == render_dir.resolve()
    assert (render_dir / staged).read_bytes() == image.read_bytes()
    assert "VOTE SURPRISE CE SOIR" in (render_dir / "cover.ass").read_text(encoding="utf-8")
    assert "Montserrat Black" in (render_dir / "cover.ass").read_text(encoding="utf-8")
    assert (ep_dir(store, story_id) / "cover.jpg").read_bytes().startswith(b"\xff\xd8")
    assert not (render_dir / "cover.part.jpg").exists()

    assert summary["asked"] == list(schemas.PLATFORMS) and summary["kept"] == []
    assert summary["cover"]["shot"] == first["shot_id"] and summary["cover"]["text"] == HOOK
    assert summary["platforms"]["shorts"]["hashtags"] == ["#tentafruit", "#coco", "#téléréalité"]
    assert log[-1] == "✅ Episode 1's metadata pack is written (tiktok, shorts, reels)."
    assert eps._story_bytes(store, story_id) == before  # RC-E2


def test_a_complete_rerun_asks_nothing_and_keeps_the_cover(store, tmp_path, built):
    story_id = rendered(store, tmp_path, built)
    run_step(store, story_id, tmp_path=tmp_path)
    doc_before = pack(store, story_id)
    cover_before = (ep_dir(store, story_id) / "cover.jpg").read_bytes()

    summary, log, runner, cover = run_step(store, story_id, tmp_path=tmp_path)

    assert runner.calls == [] and cover.calls == []
    assert pack(store, story_id) == doc_before
    assert (ep_dir(store, story_id) / "cover.jpg").read_bytes() == cover_before
    assert summary["asked"] == [] and summary["kept"] == list(schemas.PLATFORMS) and summary["cover"] is None
    assert "🏷 Every platform of episode 1 is written for this render: nothing to ask." in log


def test_a_failed_platform_keeps_the_others_and_a_rerun_asks_only_what_is_missing(store, tmp_path, built):
    story_id = rendered(store, tmp_path, built)
    runner = llm(M1=[m1_reply, bad_reply, bad_reply])

    message = failed(store, story_id, runner=runner, tmp_path=tmp_path)

    assert message.startswith("Episode 1's metadata for shorts failed: the reply failed validation twice: "
                              "$.hashtags: 1 distinct hashtag(s), expected exactly 3.")
    assert "Written and kept: tiktok." in message
    assert list(pack(store, story_id)["platforms"]) == ["tiktok"]
    assert not (ep_dir(store, story_id) / "cover.jpg").exists()

    summary, _log, runner, cover = run_step(store, story_id, tmp_path=tmp_path)
    assert [_platform_of(call["user"]) for call in runner.calls] == ["shorts", "reels"]
    assert summary["asked"] == ["shorts", "reels"] and summary["kept"] == ["tiktok"]
    assert len(cover.calls) == 1 and list(pack(store, story_id)["platforms"]) == list(schemas.PLATFORMS)


def test_a_new_render_writes_every_platform_and_the_cover_again(store, tmp_path, built):
    story_id = rendered(store, tmp_path, built)
    run_step(store, story_id, tmp_path=tmp_path)
    rerender(store, story_id)

    summary, log, runner, cover = run_step(store, story_id, tmp_path=tmp_path)

    assert len(runner.calls) == 3 and len(cover.calls) == 1
    assert pack(store, story_id)["render_sha256"] == _sha(ep_dir(store, story_id) / "episode_final.mp4")
    assert "♻️ The metadata pack was written for another render or script: every platform is written again." in log
    assert summary["kept"] == []


def test_an_english_story_gets_part_and_no_english_fields(store, tmp_path, built):
    story_id = rendered(store, tmp_path, built)
    store.update(story_id, lambda doc: doc.update(language="en"), now=NOW)

    _summary, _log, runner, _cover = run_step(store, story_id, tmp_path=tmp_path)

    assert all("title_en" not in call["schema"]["properties"] for call in runner.calls)
    assert all("Write all user-facing text in English." in call["system"] for call in runner.calls)
    doc = pack(store, story_id)
    assert doc["language"] == "en"
    for entry in doc["platforms"].values():
        assert entry["pinned_comment"] == f"{TEASER} PART 2 →"
        assert "title_en" not in entry and "hashtags_en" not in entry
        # English prose is never "repaired" as French
        assert entry["description"].startswith("Kiwilo cache un secret et l alliance vacille.")


def test_the_cover_falls_back_on_m1s_hook_text_without_an_on_screen_hook(store, tmp_path, built):
    story_id = rendered(store, tmp_path, built)
    script = store.read_episode_doc(story_id, 1, "script.json")
    script["hook"]["on_screen_text"] = None
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)

    summary, _log, runner, _cover = run_step(store, story_id, tmp_path=tmp_path)

    assert all("Hook on screen: none\n" in call["user"] for call in runner.calls)
    assert summary["cover"]["text"] == "Le vote tombe ce soir"  # tiktok's, the first platform
    text = (ep_dir(store, story_id) / "render" / "cover.ass").read_text(encoding="utf-8")
    assert "LE VOTE TOMBE CE SOIR" in text


def test_a_failed_cover_names_ffmpegs_error_and_keeps_every_platform(store, tmp_path, built):
    story_id = rendered(store, tmp_path, built)
    message = failed(store, story_id, cover=FakeCover(fail="[Parsed_ass_3] fontselect failed\n"), tmp_path=tmp_path)
    assert message.startswith("Episode 1's cover could not be made: ffmpeg exit 1; stderr: [Parsed_ass_3] "
                              "fontselect failed.")
    assert list(pack(store, story_id)["platforms"]) == list(schemas.PLATFORMS)
    summary, _log, runner, cover = run_step(store, story_id, tmp_path=tmp_path)
    assert runner.calls == [] and len(cover.calls) == 1 and summary["cover"]["file"] == "cover.jpg"


# ========================================================= preconditions

def _no_manifest(store, story_id):
    (ep_dir(store, story_id) / "render_manifest.json").unlink()


def _unfinished(store, story_id):
    manifest = store.read_episode_doc(story_id, 1, "render_manifest.json")
    manifest["output"] = None
    manifest["stages"][-1]["state"] = "failed"
    manifest["stages"][-1]["output_sha256"] = None
    store.write_episode_doc(story_id, 1, "render_manifest.json", manifest, now=NOW)


def _other_file(store, story_id):
    final = ep_dir(store, story_id) / "episode_final.mp4"
    final.write_bytes(final.read_bytes() + b"!")


def _no_file(store, story_id):
    (ep_dir(store, story_id) / "episode_final.mp4").unlink()


def _script_unapproved(store, story_id):
    trs._script_unapproved(store, story_id)


@pytest.mark.parametrize("breaks, expected", [
    (_no_manifest, "Episode 1 is not rendered yet: render it first (the render step)."),
    (_unfinished, "Episode 1's last render did not finish (render_manifest.json has no output): render it again."),
    (_other_file, "Episode 1's episode_final.mp4 is not the file its last render made"),
    (_no_file, "Episode 1's episode_final.mp4 is missing: render it again."),
    (_script_unapproved, "Episode 1's script is not approved: approve it, render the episode, then write its "
                         "metadata."),
], ids=["no-manifest", "unfinished", "other-file", "no-file", "script"])
def test_it_needs_a_finished_render_and_asks_nothing_otherwise(store, tmp_path, built, breaks, expected):
    story_id = rendered(store, tmp_path, built)
    breaks(store, story_id)
    runner, cover = llm(), FakeCover()
    message = failed(store, story_id, runner=runner, cover=cover, tmp_path=tmp_path)
    assert message.startswith(expected), message
    assert runner.calls == [] and cover.calls == [] and pack(store, story_id) is None


def test_an_episode_never_rendered_is_refused(store, tmp_path, built):
    story_id = trs.episode(store, tmp_path, built)
    runner = llm()
    assert failed(store, story_id, runner=runner, tmp_path=tmp_path).startswith("Episode 1 is not rendered yet")
    assert runner.calls == []


# ============================================================== DEC-115

M1_SETTINGS = {"LLM_CHAIN": "groq/groq-test,gemini/gemini-test,openrouter/test-model",
               "GOOGLE_API_KEY": "test-gemini-key", "OPENROUTER_API_KEY": "test-openrouter-key"}


def _chain_runner(constructed):
    from clipping.providers import llm as llm_mod

    class Completions:
        def create(self, **kwargs):
            spec = kwargs["response_format"]["json_schema"]
            call = {"schema": spec["schema"], "user": kwargs["messages"][-1]["content"]}
            content = json.dumps(m1_reply(call), ensure_ascii=False)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                                   usage=SimpleNamespace(total_tokens=120))

    def factory(link, **kwargs):
        constructed.append(link.provider)
        if link.provider != "gemini":
            raise AssertionError(f"{link.provider} must never be built")
        return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    def no_sleep(seconds):
        raise AssertionError(f"the chain tried to sleep {seconds}s")

    return functools.partial(llm_mod.run_chain, client_factory=factory, sleep_fn=no_sleep)


def test_a_paid_link_is_never_contacted_while_allow_paid_is_off(store, tmp_path, built):
    story_id = rendered(store, tmp_path, built)
    constructed = []

    _summary, log, _runner, _cover = run_step(store, story_id, runner=_chain_runner(constructed),
                                              settings=M1_SETTINGS, tmp_path=tmp_path)

    assert constructed == ["gemini"] * 3
    assert log.count("   ⏭ Skipping openrouter/test-model: paid link, allow_paid is off "
                     "(AI Story spends only on opt-in).") == 3
    assert list(pack(store, story_id)["platforms"]) == list(schemas.PLATFORMS)


def test_a_chain_whose_only_keyed_link_is_paid_sends_nothing(store, tmp_path, built):
    story_id = rendered(store, tmp_path, built)
    constructed = []
    settings = {"LLM_CHAIN": "gemini/gemini-test,openrouter/test-model", "OPENROUTER_API_KEY": "test-openrouter-key"}

    message = failed(store, story_id, runner=_chain_runner(constructed), settings=settings, tmp_path=tmp_path)

    assert constructed == []
    assert message.startswith("Episode 1's metadata for tiktok failed: The only keyed link of the LLM chain is paid")
    assert "GOOGLE_API_KEY" in message and pack(store, story_id) is None


# ============================================================= regenerate

def regenerate(store, story_id, platform, note=None, *, runner=None):
    from clipping.aistory.steps import episode_regenerate

    runner = runner or llm()
    ctx, log = ctx_for(store, story_id, step="regenerate")
    result = episode_regenerate.run(ctx, f"metadata:1:{platform}", ("metadata", 1, platform), note,
                                    runner=runner, time_fn=eps.Clock(100.0))
    return result, log, runner


def test_regenerating_one_platform_touches_only_that_one(store, tmp_path, built):
    story_id = rendered(store, tmp_path, built)
    run_step(store, story_id, tmp_path=tmp_path)
    before = pack(store, story_id)
    cover_before = (ep_dir(store, story_id) / "cover.jpg").read_bytes()

    def funnier(call):
        return dict(m1_reply(call), title="Le coco rit deux fois")

    result, log, runner = regenerate(store, story_id, "shorts", "Plus drôle, moins de drame.",
                                     runner=llm(M1=[funnier]))

    assert [_platform_of(call["user"]) for call in runner.calls] == ["shorts"]
    assert "Follow the author's note: Plus drôle, moins de drame.\n" in runner.calls[0]["user"]
    after = pack(store, story_id)
    assert after["platforms"]["shorts"]["title"] == "Le coco rit deux fois"
    assert after["platforms"]["tiktok"] == before["platforms"]["tiktok"]
    assert after["platforms"]["reels"] == before["platforms"]["reels"]
    assert (after["render_sha256"], after["script_rev"], after["created_at"]) == (
        before["render_sha256"], before["script_rev"], before["created_at"])
    assert (ep_dir(store, story_id) / "cover.jpg").read_bytes() == cover_before
    assert result == {"target": "metadata:1:shorts", "platform": "shorts", "title": "Le coco rit deux fois",
                      "hashtags": ["#tentafruit", "#coco", "#téléréalité"], "cover": None}
    assert log[-1] == "🔁 Regenerated metadata:1:shorts"


def test_regenerating_a_platform_of_a_stale_pack_is_refused(store, tmp_path, built):
    story_id = rendered(store, tmp_path, built)
    run_step(store, story_id, tmp_path=tmp_path)
    rerender(store, story_id)
    runner = llm()
    with pytest.raises(eps.steps.StepFailed) as caught:
        regenerate(store, story_id, "reels", runner=runner)
    assert str(caught.value) == (
        "Cannot regenerate 'metadata:1:reels': episode 1's metadata pack was written for another render or "
        "script: run the metadata step, which writes every platform again.")
    assert runner.calls == []


@pytest.mark.parametrize("platform, expected", [
    ("youtube", "'youtube' is not a platform (one of tiktok, shorts, reels)."),
])
def test_regenerating_an_unknown_platform_is_refused(store, tmp_path, built, platform, expected):
    story_id = rendered(store, tmp_path, built)
    runner = llm()
    with pytest.raises(eps.steps.StepFailed) as caught:
        regenerate(store, story_id, platform, runner=runner)
    assert str(caught.value) == f"Cannot regenerate 'metadata:1:{platform}': {expected}"
    assert runner.calls == []


def test_the_metadata_kind_is_exposed_for_the_regenerate_grammar():
    from clipping.aistory.steps import episode_regenerate

    assert episode_regenerate.METADATA_KIND == _meta().METADATA_KIND == "metadata"
    assert _meta().target_for(3, "reels") == "metadata:3:reels"


# ============================================================ pure parts

def test_the_cover_argv_golden():
    from clipping.aistory.render import filtergraph

    assert filtergraph.cover_argv("in/0123abcd.png", "cover.ass", "fonts", "cover.part.jpg") == [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", "in/0123abcd.png", "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,ass=cover.ass:fontsdir=fonts",
        "-frames:v", "1", "-update", "1", "-q:v", "2", "cover.part.jpg"]
    for bad in ("/abs/in.png", "C:\\in.png"):
        with pytest.raises(ValueError, match="must be relative"):
            filtergraph.cover_argv(bad, "cover.ass", "fonts", "cover.part.jpg")
    assert filtergraph.cover_argv("in/x.png", "my:cover.ass", "fonts", "o.jpg")[7].endswith(
        "ass=my\\:cover.ass:fontsdir=fonts")


@pytest.mark.parametrize("language, teaser, expected", [
    ("fr", TEASER, f"{TEASER} PARTIE 2 →"),
    ("en", "Tomorrow, he plays his last card.", "Tomorrow, he plays his last card. PART 2 →"),
    ("en", None, "PART 2 →"),
])
def test_the_pinned_comment_is_the_teaser_and_the_next_part(language, teaser, expected):
    assert _meta().pinned_comment(teaser, 1, language) == expected


def test_platform_entry_builds_what_python_owns():
    reply = {"title": " Le  coco ", "description": "Un secret.", "hashtags": ["a b", "#c", "#C", "d"],
             "hook_text": "Vote", "title_en": "The coconut", "hashtags_en": ["x", "y", "z"]}
    entry = _meta().platform_entry(reply, ep=4, language="fr", teaser="Demain tout change.", now=NOW)
    assert entry == {"title": "Le coco", "description": "Un secret.\n\nDemain tout change.",
                     "hashtags": ["#ab", "#c", "#d"], "hook_text": "Vote",
                     "pinned_comment": "Demain tout change. PARTIE 5 →", "cover": "cover.jpg", "written_at": NOW,
                     "title_en": "The coconut", "hashtags_en": ["#x", "#y", "#z"]}
    no_teaser = _meta().platform_entry(dict(reply), ep=1, language="en", teaser=None, now=NOW)
    assert no_teaser["description"] == "Un secret." and "title_en" not in no_teaser

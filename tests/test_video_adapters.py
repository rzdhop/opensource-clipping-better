"""The hosted video adapters (AI Story phase 6, stage 3): fal (seedance,
LTX-2.3, kling) and Gemini Veo turn one keyframe into one clip through the
injected transport. Every clip is paid, so each one is submitted exactly
once, journaled the moment the provider acknowledges it, resumed (never
resubmitted) by the next run, and refused before any byte leaves the machine
when it could not be keyed or bought as asked (DEC-151..154, A-100..A-103).

Fake transports only: nothing leaves the machine and nothing is spent.
Stdlib + pytest only (DEC-012).
"""

import ast
import base64
import json
import pathlib

import pytest

from clipping.aistory import video_plan
from clipping.providers import errors, gencache, images, pricing, video
from clipping.providers.generation import DEFAULT_CHAINS, GenRequest, NoRunnableLink, parse_generation_chain, run_generation_chain
from clipping.providers.registry import Link, describe
from clipping.providers.transport import Response

ROOT = pathlib.Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16
PROMPT = "the kiwi turns to the camera, slow push in"
NEGATIVE = "morphing, flicker"
ENV = {"FAL_KEY": "fk", "GEMINI_PAID_API_KEY": "fake-paid-key"}
TABLE = {("video", "fal"): video.FAL_VIDEO, ("video", "gemini"): video.VEO}

SEEDANCE_APP = "fal-ai/bytedance/seedance/v1/pro/fast/image-to-video"
CDN = "https://v3.fal.media/files/x/clip.mp4"
GEMINI = "https://generativelanguage.googleapis.com/v1beta"
OPERATION = "models/veo-3.1-lite-generate-preview/operations/op-1"
VEO_URI = f"{GEMINI}/files/f-1:download?alt=media"


class FakeTransport:
    """Answers each request from a queue, in order, and records what was sent."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        if not self.answers:
            raise AssertionError(f"unexpected call: {method} {url}")
        status, payload = self.answers.pop(0)
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        return Response(status, {}, payload)

    def json(self, index):
        return json.loads(self.calls[index]["body"].decode())

    def urls(self):
        return [(c["method"], c["url"]) for c in self.calls]

    def posts(self):
        return [c for c in self.calls if c["method"] == "POST"]


class Books(list):
    """The caller's ``book(entry)``."""

    def __call__(self, entry):
        self.append(entry)


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def time(self):
        return self.now


@pytest.fixture
def keyframe(tmp_path):
    path = tmp_path / "shot_01.png"
    path.write_bytes(PNG)
    return str(path)


def clip(keyframe, **kw):
    kw.setdefault("prompt", PROMPT)
    kw.setdefault("negative", NEGATIVE)
    kw.setdefault("seed", 11)
    kw.setdefault("duration_s", 5)
    kw.setdefault("fps", 24)
    kw.setdefault("native_audio", False)
    kw.setdefault("references", (keyframe,))
    kw.setdefault("out_dir", str(pathlib.Path(keyframe).parent / "out"))
    kw.setdefault("extra", {"name": "shot_01"})
    return GenRequest(kind="video", width=720, height=1280, **kw)


def link(spec):
    provider, model = spec.split("/", 1)
    return Link(provider, model)


def fal_queue(app, *, answer=None):
    base = f"https://queue.fal.run/{app}/requests/req-1"
    return [
        (200, {"request_id": "req-1", "status_url": base + "/status", "response_url": base}),
        (200, {"status": "IN_QUEUE", "queue_position": 1}),
        (200, {"status": "COMPLETED"}),
        (200, answer or {"video": {"url": CDN, "content_type": "video/mp4"}, "seed": 11}),
        (200, MP4),
    ]


def veo_operation(**fields):
    return (200, {"name": OPERATION, **fields})


VEO_DONE = veo_operation(done=True, response={"generateVideoResponse": {"generatedSamples": [{"video": {"uri": VEO_URI}}]}})


def run_chain(spec, request, *, transport, cache, clock=None):
    clock = clock or Clock()
    log = []
    result, answered = run_generation_chain(
        "video", parse_generation_chain("video", spec), request, env=ENV, allow_paid=True, on_log=log.append,
        budget_check=lambda estimate, link: None, adapters=TABLE, transport=transport,
        sleep_fn=clock.sleep, time_fn=clock.time, cache=cache)
    return result, log


def entry_of(cache, spec, request):
    return cache.lookup(cache.key("video", link(spec), request))


# ---------------------------------------------------------------------- fal

def test_seedance_submits_once_journals_polls_fetches_and_writes_an_mp4(keyframe):
    submitted = []
    transport = FakeTransport(fal_queue(SEEDANCE_APP))
    result = video.FAL_VIDEO.generate(link("fal/seedance-1-pro-fast"), clip(keyframe), credentials={"FAL_KEY": "fk"},
                                      on_log=lambda *a: None, transport=transport, sleep_fn=lambda s: None,
                                      on_submit=submitted.append)
    base = f"https://queue.fal.run/{SEEDANCE_APP}/requests/req-1"
    assert transport.urls() == [("POST", f"https://queue.fal.run/{SEEDANCE_APP}"), ("GET", base + "/status"),
                                ("GET", base + "/status"), ("GET", base), ("GET", CDN)]
    assert transport.calls[0]["headers"]["Authorization"] == "Key fk"
    # 720p and 9:16 explicitly (the endpoint's default is 1080p at 2.2x the price), the seed, a string duration.
    assert transport.json(0) == {"prompt": PROMPT, "image_url": "data:image/png;base64," + base64.b64encode(PNG).decode(),
                                 "duration": "5", "resolution": "720p", "aspect_ratio": "9:16", "seed": 11}
    assert submitted[0] == {"request_id": "req-1", "status_url": base + "/status", "response_url": base}
    assert submitted[1]["output"]["url"] == CDN  # a failed download resumes from here
    assert pathlib.Path(result.paths[0]).name == "shot_01.mp4" and pathlib.Path(result.paths[0]).read_bytes() == MP4
    assert result.seed == 11 and result.meta["request_id"] == "req-1"
    assert result.meta["has_audio"] is False and result.meta["seed_honoured"] is True


@pytest.mark.parametrize("spec,change,body,meta", [
    ("fal/ltx-2.3-fast", {"duration_s": 6},
     {"duration": 6, "aspect_ratio": "9:16", "resolution": "1080p", "generate_audio": False},
     {"has_audio": False, "seed_honoured": False}),
    ("fal/ltx-2.3-fast", {"duration_s": 8, "native_audio": True},
     {"duration": 8, "aspect_ratio": "9:16", "resolution": "1080p", "generate_audio": True},
     {"has_audio": True, "seed_honoured": False}),
    ("fal/kling-2.5-turbo-std", {"duration_s": 10},
     {"duration": "10", "negative_prompt": NEGATIVE},
     {"has_audio": False, "seed_honoured": False}),
    # LTX-2.5 fast (plan 23 stage C1): a string duration, 720p unless asked, generate_audio always
    # explicit (the server's default is true), no seed, negative_prompt or fps.
    ("fal/ltx-2.5-fast", {"duration_s": 6},
     {"duration": "6", "aspect_ratio": "9:16", "resolution": "720p", "generate_audio": False},
     {"has_audio": False, "seed_honoured": False}),
    ("fal/ltx-2.5-fast", {"duration_s": 10, "native_audio": True, "extra": {"resolution": "1080p"}},
     {"duration": "10", "aspect_ratio": "9:16", "resolution": "1080p", "generate_audio": True},
     {"has_audio": True, "seed_honoured": False}),
])
def test_each_fal_model_gets_its_own_fields(keyframe, spec, change, body, meta):
    app = images.FAL_APPS[link(spec).model]
    transport = FakeTransport(fal_queue(app, answer={"video": {"url": CDN}}))
    result = video.FAL_VIDEO.generate(link(spec), clip(keyframe, **change), credentials={"FAL_KEY": "fk"},
                                      on_log=lambda *a: None, transport=transport, sleep_fn=lambda s: None)
    assert transport.calls[0]["url"] == f"https://queue.fal.run/{app}"
    image_url = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert transport.json(0) == {"prompt": PROMPT, "image_url": image_url, **body}
    assert {name: result.meta[name] for name in meta} == meta
    assert result.seed == 11  # recorded, not honoured


def test_a_failed_fal_clip_ends_failed_and_stays_booked_without_a_second_submit(keyframe, tmp_path):
    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    base = f"https://queue.fal.run/{SEEDANCE_APP}/requests/req-1"
    transport = FakeTransport([
        (200, {"request_id": "req-1", "status_url": base + "/status", "response_url": base}),
        (200, {"status": "FAILED", "error": "content policy"}),
    ])
    request = clip(keyframe)
    with pytest.raises(NoRunnableLink) as excinfo:
        run_chain("fal/seedance-1-pro-fast", request, transport=transport, cache=cache)
    assert "content policy" in str(excinfo.value)
    assert len(transport.posts()) == 1 and len(transport.calls) == 2
    entry = entry_of(cache, "fal/seedance-1-pro-fast", request)
    assert entry["state"] == gencache.FAILED and entry["booked"] and len(books) == 1
    assert entry["est_usd"] == pytest.approx(5 * pricing.PRICES["fal/seedance-1-pro-fast"].usd)


# --------------------------------------------------------------------- veo

def test_veo_posts_once_journals_the_operation_polls_and_downloads_with_the_key(keyframe):
    submitted, slept = [], []
    transport = FakeTransport([veo_operation(), veo_operation(done=False), VEO_DONE, (200, MP4)])
    result = video.VEO.generate(link("gemini/veo-3.1-lite"), clip(keyframe, duration_s=4),
                                credentials={"GEMINI_PAID_API_KEY": "fake-paid-key"}, on_log=lambda *a: None,
                                transport=transport, sleep_fn=slept.append, on_submit=submitted.append)
    assert transport.urls() == [
        ("POST", f"{GEMINI}/models/veo-3.1-lite-generate-preview:predictLongRunning"),
        ("GET", f"{GEMINI}/{OPERATION}"), ("GET", f"{GEMINI}/{OPERATION}"), ("GET", VEO_URI),
    ]
    assert all(call["headers"].get("x-goog-api-key") == "fake-paid-key" for call in transport.calls)
    assert transport.json(0) == {
        "instances": [{"prompt": PROMPT, "image": video.veo_image("image/png", base64.b64encode(PNG).decode())}],
        "parameters": {"aspectRatio": "9:16", "resolution": "720p", "durationSeconds": video.VEO_DURATION_TYPE(4)},
    }
    assert submitted[0]["request_id"] == OPERATION and submitted[0]["status_url"] == f"{GEMINI}/{OPERATION}"
    assert submitted[1]["output"] == {"url": VEO_URI}
    assert slept == [video.VEO_POLL_INTERVAL_SECONDS] * 2
    assert pathlib.Path(result.paths[0]).name == "shot_01.mp4" and pathlib.Path(result.paths[0]).read_bytes() == MP4
    assert result.meta["has_audio"] is True and result.meta["seed_honoured"] is False


def test_a_veo_operation_error_is_request_failed_and_stays_booked(keyframe, tmp_path):
    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    transport = FakeTransport([veo_operation(), veo_operation(done=True, error={"code": 3, "message": "prompt blocked"})])
    request = clip(keyframe, duration_s=4)
    with pytest.raises(NoRunnableLink) as excinfo:
        run_chain("gemini/veo-3.1-lite", request, transport=transport, cache=cache)
    assert "RequestFailed" in str(excinfo.value) and "prompt blocked" in str(excinfo.value)
    assert len(transport.posts()) == 1
    entry = entry_of(cache, "gemini/veo-3.1-lite", request)
    assert entry["state"] == gencache.FAILED and entry["booked"] and len(books) == 1


# ------------------------------------------------------------------ resume

@pytest.mark.parametrize("spec,duration,first,resumed,budget", [
    ("fal/seedance-1-pro-fast", 5,
     fal_queue(SEEDANCE_APP)[:1] + [(200, {"status": "IN_PROGRESS"})] * 300,
     fal_queue(SEEDANCE_APP)[2:], "600s"),
    ("gemini/veo-3.1-lite", 4,
     [veo_operation()] + [veo_operation(done=False)] * 60,
     [VEO_DONE, (200, MP4)], "600s"),
])
def test_a_clip_past_its_poll_budget_is_resumed_next_run_without_a_second_submit(
        keyframe, tmp_path, spec, duration, first, resumed, budget):
    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    request = clip(keyframe, duration_s=duration)
    with pytest.raises(NoRunnableLink) as excinfo:
        run_chain(spec, request, transport=FakeTransport(first), cache=cache)
    assert budget in str(excinfo.value) and "kept for the next run" in str(excinfo.value)
    assert entry_of(cache, spec, request)["state"] == gencache.SUBMITTED and len(books) == 1

    second = FakeTransport(resumed)
    result, _ = run_chain(spec, request, transport=second, cache=cache)
    assert second.posts() == [] and second.answers == []
    assert result.meta["resumed"] is True and pathlib.Path(result.paths[0]).read_bytes() == MP4
    assert entry_of(cache, spec, request)["state"] == gencache.DONE and len(books) == 1


# ------------------------------------------------------------------ refusal

@pytest.mark.parametrize("spec,change,message", [
    ("fal/seedance-1-pro-fast", {"duration_s": None}, "duration_s"),
    ("gemini/veo-3.1-lite", {"duration_s": None}, "duration_s"),
    ("fal/seedance-1-pro-fast", {"seed": None}, "seed"),
    ("gemini/veo-3.1-lite", {"seed": None, "duration_s": 4}, "seed"),
    ("fal/kling-2.5-turbo-std", {"duration_s": 6}, "5, 10"),
    ("gemini/veo-3.1-lite", {"duration_s": 5}, "4, 6, 8"),
    ("fal/seedance-1-pro-fast", {"references": ()}, "one keyframe"),
    ("fal/ltx-2-fast", {"duration_s": 6}, "fal/ltx-2-fast renders 16:9 only; use fal/ltx-2.3-fast"),
])
def test_a_clip_that_cannot_be_keyed_or_bought_as_asked_is_refused_before_sending(keyframe, spec, change, message):
    adapter = TABLE[("video", link(spec).provider)]
    assert adapter.speaks_through_transport is True  # a refusal before sending is booked unbilled (DEC-153)
    transport = FakeTransport([])
    with pytest.raises(ValueError, match=message.replace("(", r"\(")):
        adapter.generate(link(spec), clip(keyframe, **change), credentials=ENV, on_log=lambda *a: None,
                         transport=transport, sleep_fn=lambda s: None, on_submit=lambda info: None)
    assert transport.calls == []


@pytest.mark.parametrize("change,message", [
    ({"duration_s": 6, "extra": {"resolution": "1440p"}}, "1440p"),
    ({"duration_s": 6, "extra": {"resolution": "2160p"}}, "2160p"),
    ({"duration_s": 4}, "6, 8, 10, 12, 14, 16, 18, 20"),
])
def test_ltx25_refuses_1440p_and_4s_before_sending(keyframe, change, message):
    transport = FakeTransport([])
    with pytest.raises(ValueError, match=message):
        video.FAL_VIDEO.generate(link("fal/ltx-2.5-fast"), clip(keyframe, **change), credentials=ENV,
                                 on_log=lambda *a: None, transport=transport, sleep_fn=lambda s: None,
                                 on_submit=lambda info: None)
    assert transport.calls == []


def test_ltx25_sells_20s(keyframe):
    assert max(video.CLIP_LENGTHS["fal/ltx-2.5-fast"]) == 20
    transport = FakeTransport(fal_queue(images.FAL_APPS["ltx-2.5-fast"], answer={"video": {"url": CDN}}))
    video.FAL_VIDEO.generate(link("fal/ltx-2.5-fast"), clip(keyframe, duration_s=20), credentials=ENV,
                             on_log=lambda *a: None, transport=transport, sleep_fn=lambda s: None)
    assert transport.json(0)["duration"] == "20"
    assert transport.calls[0]["url"] == "https://queue.fal.run/fal-ai/ltx-2.5/image-to-video/fast"


# ---------------------------------------------------------------- estimates

def test_the_estimate_is_seconds_times_price_for_every_hosted_link_of_the_default_chain():
    hosted = [l for l in parse_generation_chain("video", DEFAULT_CHAINS["video"]) if l.provider != "local"]
    assert [describe(l) for l in hosted] == ["fal/seedance-1-pro-fast", "fal/ltx-2.3-fast",
                                             "fal/kling-2.5-turbo-std", "gemini/veo-3.1-lite"]
    for each in hosted:
        adapter = TABLE[("video", each.provider)]
        for seconds in video.CLIP_LENGTHS[describe(each)]:
            est = adapter.estimate(each, GenRequest(kind="video", duration_s=seconds))
            assert (est.unit, est.qty, est.paid) == ("second", seconds, True), describe(each)
            assert est.est_usd == round(seconds * pricing.PRICES[describe(each)].usd, 4), (describe(each), seconds)
    assert video_plan.CLIP_LENGTHS is video.CLIP_LENGTHS  # the planner plans against what the adapters accept


def test_no_sdk_is_imported_in_the_video_adapters():
    tree = ast.parse((ROOT / "clipping" / "providers" / "video.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    assert not names & {"fal_client", "google", "openai", "requests", "httpx"}


# ------------------------------------------------------------------- guard

def test_guard_an_image_request_still_polls_for_300_seconds(tmp_path):
    base = "https://queue.fal.run/fal-ai/flux/schnell/requests/r"
    transport = FakeTransport([(200, {"request_id": "r", "status_url": base + "/status", "response_url": base})]
                              + [(200, {"status": "IN_PROGRESS"})] * 200)
    clock = Clock()
    with pytest.raises(errors.ProviderError, match="after 300s"):
        images.FAL.generate(Link("fal", "flux-schnell"), GenRequest(kind="image", prompt=PROMPT, out_dir=str(tmp_path)),
                            credentials={"FAL_KEY": "fk"}, on_log=lambda *a: None, transport=transport,
                            sleep_fn=clock.sleep, time_fn=clock.time)
    assert len(transport.calls) == 1 + 150  # one submit, then a poll every 2 s for 300 s
    assert images.FAL.poll_budget_seconds == 300.0 and video.FAL_VIDEO.poll_budget_seconds == 600.0


# ------------------------------------------------- plan 22: the speaking links

# sha256 of the Lite body's canonical JSON, computed with the parent commit's
# GeminiVeoAdapter._body (74cfbcd) on this file's keyframe and prompt: stored
# clips' keys depend on it never moving (RC-N1).
LITE_BODY_SHA = "acf87404a1a44278607585101b97a1262cc86ad594eb83f010f9cfc7ba08a521"


def _body_sha(body):
    import hashlib
    import json

    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def test_veo_lite_body_unchanged(keyframe):
    """RC-N1: Veo 3.1 lite's body is byte for byte the one it always sent --
    720p, no person policy, no negative prompt -- whatever the request asks."""
    for request in (clip(keyframe, duration_s=4), clip(keyframe, duration_s=4, extra={"name": "shot_01",
                                                                                        "resolution": "1080p"})):
        body = video.VEO._body(request, 4, link("gemini/veo-3.1-lite"))
        assert _body_sha(body) == LITE_BODY_SHA
        assert body == video.VEO._body(request, 4)


@pytest.mark.parametrize("spec,model", [("gemini/veo-3.1-fast", "veo-3.1-fast-generate-preview"),
                                        ("gemini/veo-3.1", "veo-3.1-generate-preview")])
def test_veo_fast_body_says_the_size_adults_only_and_no_captions(keyframe, spec, model):
    """The speaking links post to their own model with the request's size
    (720p unless it asks 1080p), ``personGeneration: allow_adult`` and the
    negative prompt that keeps subtitles and watermarks out."""
    transport = FakeTransport([(200, {"name": f"models/{model}/operations/op-1"}),
                               (200, {"name": f"models/{model}/operations/op-1", "done": True,
                                      "response": {"generateVideoResponse": {"generatedSamples": [
                                          {"video": {"uri": VEO_URI}}]}}}), (200, MP4)])
    video.VEO.generate(link(spec), clip(keyframe, duration_s=6), credentials=ENV, on_log=lambda *a: None,
                       transport=transport, sleep_fn=lambda s: None)
    assert transport.urls()[0] == ("POST", f"{GEMINI}/models/{model}:predictLongRunning")
    body = transport.json(0)
    assert body["parameters"] == {"aspectRatio": "9:16", "resolution": "720p", "durationSeconds": video.VEO_DURATION_TYPE(6),
                                  "personGeneration": "allow_adult",
                                  "negativePrompt": "subtitles, captions, on-screen text, watermark"}
    big = video.VEO._body(clip(keyframe, duration_s=8, extra={"name": "shot_01", "resolution": "1080p"}), 8, link(spec))
    assert big["parameters"]["resolution"] == "1080p"


def test_veo_fast_body_refuses_a_size_veo_does_not_sell(keyframe):
    with pytest.raises(ValueError, match="720p or 1080p"):
        video.VEO._body(clip(keyframe, duration_s=4, extra={"name": "shot_01", "resolution": "4k"}), 4,
                        link("gemini/veo-3.1-fast"))


def test_the_speaking_links_are_paid_on_the_paid_key_alone_and_sold_at_4_6_8_seconds():
    from clipping.providers import generation

    for spec in ("gemini/veo-3.1-fast", "gemini/veo-3.1"):
        assert video.CLIP_LENGTHS[spec] == (4, 6, 8) and video.AUDIO[spec] == "always"
        assert generation.is_paid(link(spec))
        assert generation.env_keys_for(link(spec)) == ("GEMINI_PAID_API_KEY",)
    # reachable by name only: never in the default chain
    assert "veo-3.1-fast" not in DEFAULT_CHAINS["video"]

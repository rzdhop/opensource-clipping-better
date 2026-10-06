"""The MCP's RunPod job client (``mcp_server/runpod_jobs.py``): a template of
any kind rendered and submitted with its images inline, journaled at once,
settled by ``/status`` (files written, cost kept), listed and summed. Fake
transports only: nothing leaves the machine and nothing is spent.
"""

import base64
import json
import pathlib

import pytest

from clipping.providers.transport import Response
from mcp_server import runpod_jobs
from mcp_server.config import Settings, load_settings
from mcp_server.runpod_jobs import JobClient, JobError, Journal, list_templates

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16


class FakeTransport:
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


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def time(self):
        return self.now


def b64(data):
    return base64.b64encode(data).decode()


def completed(files, **fields):
    return (200, {"id": "job-1", "status": "COMPLETED", "output": {"images": files}, "executionTime": 12000,
                  "delayTime": 3000, "workerId": "w-1", **fields})


@pytest.fixture
def settings(tmp_path):
    return Settings(api_key="rpa_fake", endpoints={"video": "vid1", "image": "img1"},
                    rates={"video": 3.49, "image": 1.58}, outputs_dir=str(tmp_path / "outputs"))


@pytest.fixture
def keyframe(tmp_path):
    path = tmp_path / "outputs" / "still.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG)
    return path


def make_client(settings, answers, seed=7):
    transport = FakeTransport(answers)
    client = JobClient(settings, transport=transport, rng=__import__("random").Random(seed))
    return client, transport


# ------------------------------------------------------------------ settings

def test_settings_read_both_endpoints_and_fall_back_to_the_video_one(tmp_path):
    env = {"RUNPOD_API_KEY": " k ", "RUNPOD_COMFY_ENDPOINT_ID": "vid1", "RUNPOD_GPU_USD_PER_HOUR": "3.49",
           "RZDHOP_OUTPUTS_DIR": str(tmp_path), "MCP_PORT": "9000"}
    s = load_settings(env)
    assert (s.api_key, s.endpoint("video"), s.endpoint("image"), s.port) == ("k", "vid1", "vid1", 9000)
    assert s.rate("image") == 3.49  # no image endpoint: the video endpoint's rate
    s2 = load_settings({**env, "RUNPOD_IMAGE_ENDPOINT_ID": "img1"})
    assert s2.endpoint("image") == "img1" and s2.rate("image") is None
    with pytest.raises(RuntimeError, match="RUNPOD_COMFY_ENDPOINT_ID"):
        load_settings({"RZDHOP_OUTPUTS_DIR": str(tmp_path)}).endpoint("video")


def test_the_template_listing_names_every_repo_template_with_its_kind():
    rows = {r["name"]: r for r in list_templates()}
    assert rows["t2i_flux2_klein"]["kind"] == "image" and "frame_rule" not in rows["t2i_flux2_klein"]
    assert rows["edit_flux2_klein_multiref"]["ref_slots"] == 4
    assert rows["i2v_wan22_14b_lightning"]["kind"] == "video"
    assert rows["i2v_wan22_14b_lightning"]["frame_rule"]["lengths"] == [2, 3, 4, 5]
    assert rows["i2v_wan22_14b_lightning"]["verified_live"] is True


# -------------------------------------------------------------------- submit

def test_an_image_job_goes_to_the_image_endpoint_with_the_rendered_graph(settings):
    client, transport = make_client(settings, [(200, {"id": "job-1", "status": "IN_QUEUE"})])
    record = client.submit("t2i_flux2_klein", prompt="a kiwi detective, flat colours", name="kiwi", dest="cast/kiwi")
    assert transport.urls() == [("POST", "https://api.runpod.ai/v2/img1/run")]
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer rpa_fake"
    body = transport.json(0)
    assert body["input"]["images"] == []
    graph = body["input"]["workflow"]
    assert any(n["inputs"].get("text") == "a kiwi detective, flat colours" for n in graph.values())
    assert any(n["inputs"].get("width") == 832 and n["inputs"].get("height") == 1216 for n in graph.values())
    assert record["job_id"] == "job-1" and record["kind"] == "image" and record["state"] == "IN_QUEUE"
    assert record["name"] == "kiwi" and record["dest_dir"].endswith("outputs/cast/kiwi")
    assert isinstance(record["seed"], int) and record["seconds"] is None
    assert client.journal.get("job-1")["template"] == "t2i_flux2_klein"


def test_a_clip_job_inlines_the_keyframe_and_follows_the_frame_rule(settings, keyframe):
    client, transport = make_client(settings, [(200, {"id": "job-2", "status": "IN_QUEUE"})])
    record = client.submit("i2v_wan22_14b_lightning", prompt="slow push in", seed=42, seconds=3,
                           image_path="still.png")
    assert transport.urls() == [("POST", "https://api.runpod.ai/v2/vid1/run")]
    body = transport.json(0)
    (image,) = body["input"]["images"]
    assert image["name"].startswith("rzdhop_") and image["name"].endswith(".png")
    assert image["image"].startswith("data:image/png;base64,")
    graph = body["input"]["workflow"]
    assert any(n["inputs"].get("image") == image["name"] for n in graph.values())
    assert record["frames"] == 49 and record["fps"] == 16 and record["seconds"] == 3
    assert (record["width"], record["height"], record["seed"]) == (480, 832, 42)


def test_an_edit_job_inlines_each_reference_once_and_fills_the_slots(settings, keyframe, tmp_path):
    other = tmp_path / "outputs" / "prop.png"
    other.write_bytes(PNG + b"x")
    client, transport = make_client(settings, [(200, {"id": "job-3", "status": "IN_QUEUE"})])
    client.submit("edit_flux2_klein_multiref", prompt="same kiwi, now in a trench coat",
                  ref_paths=["still.png", str(other)])
    body = transport.json(0)
    names = [i["name"] for i in body["input"]["images"]]
    assert len(names) == 2 and len(set(names)) == 2
    loads = [n["inputs"]["image"] for n in body["input"]["workflow"].values() if n["class_type"] == "LoadImage"]
    assert loads == [names[0], names[1], names[1], names[1]]  # unused slots repeat the last reference


@pytest.mark.parametrize("call, message", [
    (dict(template="nope", prompt="x"), "no template 'nope'"),
    (dict(template="i2v_wan22_14b_lightning", prompt="x"), "needs image_path"),
    (dict(template="i2v_wan22_14b_lightning", prompt="x", image_path="missing.png"), "input file not found"),
    (dict(template="edit_flux2_klein_multiref", prompt="x"), "needs ref_paths"),
])
def test_a_bad_submit_is_refused_before_anything_is_sent(settings, call, message):
    client, transport = make_client(settings, [])
    template = call.pop("template")
    with pytest.raises(JobError, match=message):
        client.submit(template, **call)
    assert transport.calls == [] and client.journal.all() == []


def test_a_clip_length_the_template_does_not_make_is_refused(settings, keyframe):
    client, transport = make_client(settings, [])
    with pytest.raises(JobError, match="makes clips of 2, 3, 4, 5 s, not 9"):
        client.submit("i2v_wan22_14b_lightning", prompt="x", seconds=9, image_path="still.png")
    assert transport.calls == []


def test_a_refused_key_is_said_plainly(settings):
    client, _ = make_client(settings, [(401, {"error": "unauthorized"})])
    with pytest.raises(JobError, match="refused RUNPOD_API_KEY"):
        client.submit("t2i_flux2_klein", prompt="x")
    assert client.journal.all() == []


# -------------------------------------------------------------------- status

def test_a_completed_job_is_settled_once_its_files_written_and_priced(settings):
    client, transport = make_client(settings, [
        (200, {"id": "job-1", "status": "IN_QUEUE"}),
        (200, {"id": "job-1", "status": "IN_PROGRESS"}),
        completed([{"filename": "ComfyUI_00001_.png", "type": "base64", "data": b64(PNG)},
                   {"filename": "ComfyUI_00002_.png", "type": "base64", "data": b64(PNG + b"2")}]),
    ])
    client.submit("t2i_flux2_klein", prompt="x", name="kiwi", dest="cast")
    assert client.status("job-1")["state"] == "IN_PROGRESS"
    record = client.status("job-1")
    assert record["state"] == "COMPLETED"
    paths = [pathlib.Path(p) for p in record["outputs"]]
    assert [p.name for p in paths] == ["kiwi_1.png", "kiwi_2.png"]
    assert paths[0].read_bytes() == PNG and paths[1].read_bytes() == PNG + b"2"
    assert record["gpu_seconds"] == 15.0 and record["billed_usd"] == round(15 * 1.58 / 3600, 4)
    assert record["worker_id"] == "w-1" and record["finished_at"]
    # Settled: no further call to RunPod.
    assert client.status("job-1") == record
    assert len(transport.calls) == 3


def test_a_single_file_keeps_the_plain_name_and_a_clip_its_mp4_extension(settings, keyframe):
    client, _ = make_client(settings, [
        (200, {"id": "job-1", "status": "IN_QUEUE"}),
        completed([{"filename": "clip_00001_.mp4", "type": "base64", "data": b64(MP4)}]),
    ])
    client.submit("i2v_wan22_14b_lightning", prompt="x", image_path="still.png", name="sh01", dest="ep01")
    record = client.status("job-1")
    assert [pathlib.Path(p).name for p in record["outputs"]] == ["sh01.mp4"]
    assert record["billed_usd"] == round(15 * 3.49 / 3600, 4)  # the video rate


def test_a_job_that_ends_without_a_file_or_fails_is_settled_with_its_error(settings):
    client, _ = make_client(settings, [
        (200, {"id": "job-1", "status": "IN_QUEUE"}),
        completed([], errors=None),
        (200, {"id": "job-2", "status": "IN_QUEUE"}),
        (200, {"id": "job-2", "status": "FAILED", "error": "CUDA out of memory", "executionTime": 5000,
               "delayTime": 0}),
    ])
    client.submit("t2i_flux2_klein", prompt="x")
    first = client.status("job-1")
    assert first["state"] == "FAILED" and "without a file" in first["error"]
    client.submit("t2i_flux2_klein", prompt="y")
    second = client.status("job-2")
    assert second["state"] == "FAILED" and second["error"] == "CUDA out of memory" and second["gpu_seconds"] == 5.0


def test_an_s3_output_is_refused_with_the_fix(settings):
    client, _ = make_client(settings, [
        (200, {"id": "job-1", "status": "IN_QUEUE"}),
        completed([{"filename": "a.png", "type": "s3_url", "data": "https://bucket/a.png"}]),
    ])
    client.submit("t2i_flux2_klein", prompt="x")
    record = client.status("job-1")
    assert record["state"] == "FAILED" and "BUCKET_ENDPOINT_URL" in record["error"]


def test_a_job_runpod_forgot_is_marked_gone_and_an_unknown_id_refused(settings):
    client, _ = make_client(settings, [(200, {"id": "job-1", "status": "IN_QUEUE"}), (404, {"error": "not found"})])
    client.submit("t2i_flux2_klein", prompt="x")
    assert client.status("job-1")["state"] == "GONE"
    with pytest.raises(JobError, match="not in the journal"):
        client.status("job-9")


def test_wait_polls_until_the_end_or_the_deadline(settings):
    clock = Clock()
    client, transport = make_client(settings, [
        (200, {"id": "job-1", "status": "IN_QUEUE"}),
        (200, {"id": "job-1", "status": "IN_QUEUE"}),
        (200, {"id": "job-1", "status": "IN_PROGRESS"}),
        (200, {"id": "job-1", "status": "IN_PROGRESS"}),
        completed([{"filename": "a.png", "type": "base64", "data": b64(PNG)}]),
    ])
    client.submit("t2i_flux2_klein", prompt="x")
    still = client.wait("job-1", timeout_s=7, poll_s=5, sleep_fn=clock.sleep, time_fn=clock.time)
    assert still["state"] == "IN_PROGRESS" and clock.now == 7.0  # two polls: at 0 and at 5, then the deadline
    done = client.wait("job-1", timeout_s=60, poll_s=5, sleep_fn=clock.sleep, time_fn=clock.time)
    assert done["state"] == "COMPLETED" and len(transport.calls) == 5


def test_cancel_posts_once_and_the_journal_lists_newest_first_and_sums_the_bill(settings):
    client, transport = make_client(settings, [
        (200, {"id": "job-1", "status": "IN_QUEUE"}),
        completed([{"filename": "a.png", "type": "base64", "data": b64(PNG)}]),
        (200, {"id": "job-2", "status": "IN_QUEUE"}),
        (200, {}),
    ])
    client.submit("t2i_flux2_klein", prompt="x")
    client.status("job-1")
    client.submit("t2i_flux2_klein", prompt="y")
    cancelled = client.cancel("job-2")
    assert cancelled["state"] == "CANCELLED"
    assert transport.urls()[-1] == ("POST", "https://api.runpod.ai/v2/img1/cancel/job-2")
    assert [r["job_id"] for r in client.recent(10)] == ["job-2", "job-1"]
    ledger = client.ledger()
    assert ledger["jobs"] == 1 and ledger["gpu_seconds"] == 15.0
    assert ledger["by_kind"]["image"]["billed_usd"] == round(15 * 1.58 / 3600, 3)
    assert client.ledger(since="2999-01-01")["jobs"] == 0


def test_the_journal_survives_a_restart_and_a_corrupt_file(settings, tmp_path):
    client, _ = make_client(settings, [(200, {"id": "job-1", "status": "IN_QUEUE"})])
    client.submit("t2i_flux2_klein", prompt="x")
    again = JobClient(settings, transport=FakeTransport([]))
    assert again.journal.get("job-1")["state"] == "IN_QUEUE"
    pathlib.Path(again.journal.path).write_text("{not json", encoding="utf-8")
    assert Journal(again.journal.path).all() == []

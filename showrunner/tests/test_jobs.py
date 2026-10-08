"""GPU jobs into a story (stage 2.2): the journal written at submit, settling once, the clip as the shot's next
take, the cost in the ledger, the waiting answer. A scripted endpoint; no network, no GPU."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from showrunner import jobs as J, store as S  # noqa: E402

RATE = 0.00053


class Scripted:
    """``run`` gives job ids in turn; ``status`` answers from a per-job list (the last answer repeats)."""

    def __init__(self, answers: dict):
        self.id, self.answers, self.sent, self.polls = "vid-ep", answers, [], []

    def run(self, payload, *, execution_timeout_s=None):
        self.sent.append(payload)
        return f"job{len(self.sent)}"

    def status(self, job):
        self.polls.append(job)
        seq = self.answers[job]
        return seq.pop(0) if len(seq) > 1 else seq[0]


def done(mp4=b"MP4", png=b"PNG", ms=40000):
    return {"status": "COMPLETED", "executionTime": ms, "delayTime": 900000,
            "output": {"images": [{"filename": "showrunner/x_00001_.mp4", "type": "base64", "data": base64.b64encode(mp4).decode()},
                                  {"filename": "showrunner/x_last_00001_.png", "type": "base64",
                                   "data": base64.b64encode(png).decode()}]}}


@pytest.fixture
def story(tmp_path):
    s = S.Story.create(str(tmp_path), "demo", title="Demo", language="fr")
    s.write_bytes(s.keyframe(1, "s01"), b"\x89PNG" + b"0" * 64)
    return s


def test_submit_needs_a_destination_and_story_files(story):
    ep = Scripted({})
    with pytest.raises(J.JobError, match="dest"):
        J.submit(story, ep, "video", "ltx25_i2v_speech", {"prompt": "p", "seed": 1}, {"image": story.keyframe(1, "s01")})
    with pytest.raises(J.JobError, match="no file"):
        J.submit(story, ep, "video", "ltx25_i2v_speech", {"prompt": "p", "seed": 1}, {"image": "ep01/keyframes/s09.png"},
                 episode=1, shot="s09")
    with pytest.raises(J.JobError, match="leaves"):
        J.submit(story, ep, "images", "t2i_flux2_klein", {"prompt": "p", "seed": 1}, {}, dest="../out")
    assert ep.sent == []


def test_a_clip_job_is_journaled_at_once_then_settled_once_as_the_next_take(story):
    ep = Scripted({"job1": [{"status": "IN_QUEUE", "delayTime": 1000}, done()]})
    row = J.submit(story, ep, "video", "ltx25_i2v_speech", {"prompt": "says: \"Salut\"", "seed": 22, "seconds": 5},
                   {"image": story.keyframe(1, "s01")}, episode=1, shot="s01", rate_per_s=RATE)
    assert row["job"] == "job1" and J.find(story, "job1")["state"] == "SUBMITTED"
    assert row["estimate"]["warm_usd"] == pytest.approx(40 * RATE, abs=1e-4)
    assert ep.sent[0]["workflow"]["pos"]["inputs"]["text"] == "says: \"Salut\""
    waiting = J.fetch(story, ep, "job1", wait_s=0, rate_per_s=RATE)
    assert waiting["waiting"] and waiting["state"] == "IN_QUEUE" and story.costs() == []
    got = J.fetch(story, ep, "job1", wait_s=0, rate_per_s=RATE)
    assert got["state"] == "COMPLETED" and got["take"] == "v1"
    assert got["outputs"] == ["ep01/clips/s01_v1.mp4", "ep01/clips/s01_v1_last.png"]
    assert open(story.path("ep01/clips/s01_v1.mp4"), "rb").read() == b"MP4"
    take = story.read_json(story.takes(1))["s01"]["takes"][0]
    assert (take["seed"], take["job"], take["approved"]) == (22, "job1", False)
    assert [r["usd"] for r in story.costs(1)] == [pytest.approx(40 * RATE, abs=1e-4)]   # execution only, the 15 min queue free
    again = J.fetch(story, ep, "job1", wait_s=30, rate_per_s=RATE)
    assert again["state"] == "COMPLETED" and len(story.costs()) == 1 and len(ep.polls) == 2   # no second bill, no poll


def test_a_second_clip_of_the_shot_is_take_v2_and_never_overwrites_v1(story):
    ep = Scripted({"job1": [done(b"ONE")], "job2": [done(b"TWO")]})
    for seed in (22, 33):
        J.submit(story, ep, "video", "ltx25_i2v_speech", {"prompt": "p", "seed": seed}, {"image": story.keyframe(1, "s01")},
                 episode=1, shot="s01")
    J.fetch(story, ep, "job1", rate_per_s=RATE)
    second = J.fetch(story, ep, "job2", rate_per_s=RATE)
    assert second["take"] == "v2" and open(story.path("ep01/clips/s01_v1.mp4"), "rb").read() == b"ONE"


def test_an_image_job_lands_at_its_destination_and_a_failure_is_billed_and_kept(story):
    ep = Scripted({"job1": [{"status": "COMPLETED", "executionTime": 4000, "output": {"images": [
        {"filename": "showrunner/demo_c1_00001_.png", "type": "base64", "data": base64.b64encode(b"IMG").decode()}]}}],
        "job2": [{"status": "FAILED", "executionTime": 2000, "error": "out of memory"}]})
    J.submit(story, ep, "images", "t2i_flux2_klein", {"prompt": "p", "seed": 3}, {}, dest="02-cast/ana/candidates/c1")
    assert J.fetch(story, ep, "job1", rate_per_s=RATE)["outputs"] == ["02-cast/ana/candidates/c1.png"]
    J.submit(story, ep, "images", "t2i_flux2_klein", {"prompt": "p", "seed": 4}, {}, dest="02-cast/ana/candidates/c2")
    failed = J.fetch(story, ep, "job2", rate_per_s=RATE)
    assert failed["state"] == "FAILED" and "out of memory" in failed["error"]
    assert [r["note"].split()[-1] for r in story.costs()] == ["COMPLETED", "FAILED"]
    assert [r["state"] for r in J.journal(story)] == ["COMPLETED", "FAILED"]


def test_fetch_waits_within_its_budget(story):
    ep = Scripted({"job1": [{"status": "IN_PROGRESS"}, {"status": "IN_PROGRESS"}, done()]})
    J.submit(story, ep, "video", "ltx25_i2v_speech", {"prompt": "p", "seed": 1}, {"image": story.keyframe(1, "s01")},
             episode=1, shot="s01")
    t = [0.0]
    got = J.fetch(story, ep, "job1", wait_s=60, rate_per_s=RATE, poll_s=5, sleep=lambda s: t.__setitem__(0, t[0] + s),
                  clock=lambda: t[0])
    assert got["state"] == "COMPLETED" and t[0] == 10.0


def test_an_unknown_job_is_named(story):
    with pytest.raises(J.JobError, match="no job nope"):
        J.fetch(story, Scripted({}), "nope", rate_per_s=RATE)

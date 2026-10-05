"""``tools/probe_speech_link.py`` (plan 23 stage C2): one speaking clip bought
on one video link, measured, written to a probe JSON.

The link is the adapter-level fake of the native-take tests (``FakeVeo``: one
journaled submit, copying a real mp4 made by ffmpeg) and the STT is their
``Transcriber``; the gates, the journal and the booking are the real ones.
Today's spend is the ``spend.json`` the shared ``hermetic`` fixture puts under
``tmp_path``; the settings file, the probe folder and the journal are there
too. The keys are test values: nothing leaves the process.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import importlib
import json
import os
import shutil

import pytest

from clipping.providers import budget
from test_story_episode_steps import hermetic  # noqa: F401 -- the shared fixture (autouse)
from test_story_native_take import FakeVeo, Transcriber, make_clip, words

LINE = "Tu as vendu mon étal à ma sœur sans me le dire."
# Test values only: every request goes to the fake.
PAID_ON = {"FAL_KEY": "test-fal-key", "ALLOW_PAID": "1", "DAILY_CAP_USD": "4.00"}


@pytest.fixture
def probe():
    return importlib.import_module("tools.probe_speech_link")


@pytest.fixture
def clip(tmp_path):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg and ffprobe are needed to make and measure the fake clip")
    return make_clip(tmp_path / "answer.mp4", 6)


class Run:
    """One probe folder under *tmp_path*: the image, the settings file, the
    out and cache folders, and ``main`` with the fakes handed in."""

    def __init__(self, probe, tmp_path, settings):
        self.probe = probe
        self.root = tmp_path
        self.image = tmp_path / "portrait.png"
        self.image.write_bytes(b"\x89PNG\r\n\x1a\n not really a png, only its bytes are hashed")
        self.settings = tmp_path / "settings.json"
        self.settings.write_text(json.dumps(settings), encoding="utf-8")
        self.out_dir = tmp_path / "probes"
        self.cache_dir = tmp_path / "gen"
        self.lines = []

    def __call__(self, *extra, adapter=None, transcribe=None):
        argv = ["--image", str(self.image), "--settings-file", str(self.settings), "--out-dir",
                str(self.out_dir), "--cache-dir", str(self.cache_dir), *extra]
        adapters = {("video", "fal"): adapter} if adapter is not None else None
        return self.probe.main(argv, adapters=adapters, transcribe=transcribe, sleep_fn=lambda _s: None,
                               out=self.lines.append)

    def probes(self):
        return sorted(name for name in os.listdir(self.out_dir) if name.endswith(".json")) \
            if self.out_dir.exists() else []

    def record(self, name):
        return json.loads((self.out_dir / name).read_text(encoding="utf-8"))


def spent():
    return budget.day_spent()


def test_allow_paid_off_in_settings_refuses_before_any_call(probe, tmp_path, clip):
    run = Run(probe, tmp_path, dict(PAID_ON, ALLOW_PAID="0"))
    fake = FakeVeo([clip])

    code = run("--allow-paid", "--max-usd", "1", adapter=fake, transcribe=Transcriber(words(LINE)))

    assert code == 2
    assert "Settings" in run.lines[-1]
    assert fake.requests == []
    assert spent() == 0.0
    assert run.probes() == []


def test_max_usd_and_the_daily_cap_refuse_unsent(probe, tmp_path, clip):
    run = Run(probe, tmp_path, PAID_ON)
    fake = FakeVeo([clip])

    # --max-usd under the one try's $0.54: refused by the tool, nothing sent.
    assert run("--allow-paid", "--max-usd", "0.50", adapter=fake) == 2
    assert "--max-usd" in run.lines[-1]
    assert fake.requests == [] and spent() == 0.0

    # Today's spend plus $0.54 over the daily cap: refused by the real gate in the runner.
    budget.record(3.80)
    assert run("--allow-paid", "--max-usd", "1", adapter=fake) == 2
    assert "daily cap" in run.lines[-1]
    assert fake.requests == []
    assert spent() == pytest.approx(3.80)
    assert run.probes() == []


def test_a_heard_line_books_once_and_passes(probe, tmp_path, clip):
    run = Run(probe, tmp_path, PAID_ON)
    fake = FakeVeo([clip])
    stt = Transcriber(words(LINE))

    code = run("--allow-paid", "--max-usd", "0.60", adapter=fake, transcribe=stt)

    assert code == 0
    assert len(fake.requests) == 1
    request = fake.requests[0]
    assert request.native_audio is True and request.duration_s == 6
    assert f'"{LINE}"' in request.prompt
    assert spent() == pytest.approx(0.54)
    [name] = run.probes()
    record = run.record(name)
    assert name == f"{budget.today()}-fal__ltx-2.5-fast.json"
    assert record["passed"] is True and record["ear"] is None
    assert record["take"]["matched"] >= 0.8
    assert record["booked_usd"] == pytest.approx(0.54)
    assert record["journal"] == "new"
    assert record["has_audio"] is True
    assert record["wps"] > 0 and record["wps_reference"] == 2.4
    assert os.path.exists(record["clip_path"])


def test_the_same_inputs_again_are_served_from_the_journal(probe, tmp_path, clip):
    run = Run(probe, tmp_path, PAID_ON)
    fake = FakeVeo([clip])
    stt = Transcriber(words(LINE), words(LINE))

    assert run("--allow-paid", "--max-usd", "0.60", adapter=fake, transcribe=stt) == 0
    assert run("--allow-paid", "--max-usd", "0.60", adapter=fake, transcribe=stt) == 0

    assert len(fake.requests) == 1
    assert spent() == pytest.approx(0.54)
    first = f"{budget.today()}-fal__ltx-2.5-fast.json"
    second = first.replace(".json", "-2.json")
    assert run.probes() == sorted([first, second])
    record = run.record(second)
    assert record["journal"] == "kept"
    assert record["booked_usd"] == 0.0
    assert record["passed"] is True


def test_no_stt_link_leaves_the_take_unknown_and_keeps_the_clip(probe, tmp_path, clip):
    run = Run(probe, tmp_path, PAID_ON)
    fake = FakeVeo([clip])

    code = run("--allow-paid", "--max-usd", "0.60", adapter=fake, transcribe=None)

    assert code == 0
    [name] = run.probes()
    record = run.record(name)
    assert record["take"]["state"] == "stt_unavailable"
    assert record["passed"] is None
    assert record["booked_usd"] == pytest.approx(0.54)
    assert os.path.exists(record["clip_path"])


def test_dry_run_sends_and_writes_nothing(probe, tmp_path, clip):
    run = Run(probe, tmp_path, PAID_ON)
    fake = FakeVeo([clip])

    code = run("--allow-paid", "--max-usd", "0.60", "--dry-run", adapter=fake, transcribe=Transcriber())

    assert code == 0
    assert fake.requests == []
    assert spent() == 0.0
    assert not run.out_dir.exists() and not run.cache_dir.exists()
    assert any("$0.54" in line for line in run.lines)

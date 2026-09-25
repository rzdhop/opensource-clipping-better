"""The hardware profiler (spec 8.2): stdlib probes in a fixed order, each
with a fixture-tested parser, mapped to one of seven profiles with a table of
what to run locally. A probe that fails is recorded, never hidden, and a
container without a GPU is its own profile so the hint can say "run ComfyUI
on the host"."""

import json
import pathlib

import pytest

from clipping.aistory import hardware
from clipping.aistory.hardware import (
    PROFILES, HardwareProfile, classify, parse_comfy_system_stats, parse_meminfo, parse_nvidia_smi,
    parse_rocm_smi, parse_system_profiler, parse_wmic, probe, recommendations_for, to_dict,
)
from clipping.providers.local_comfyui import TEMPLATES, load_template

FIX = pathlib.Path(__file__).resolve().parent / "fixtures" / "hardware"


def fixture(name):
    return (FIX / name).read_text(encoding="utf-8")


# ----------------------------------------------------------------- parsers

def test_nvidia_smi_csv_is_parsed_and_the_largest_gpu_wins():
    assert parse_nvidia_smi(fixture("nvidia_smi_4090.csv")) == ("NVIDIA GeForce RTX 4090", 24.0, 22.6)
    assert parse_nvidia_smi(fixture("nvidia_smi_two.csv")) == ("NVIDIA RTX A6000", 48.0, 46.9)
    assert parse_nvidia_smi("") is None
    assert parse_nvidia_smi("NVIDIA-SMI has failed") is None


def test_system_profiler_json_tells_apple_silicon_from_a_discrete_card():
    assert parse_system_profiler(fixture("system_profiler_m2.json")) == ("Apple M2 Pro", None, True)
    assert parse_system_profiler(fixture("system_profiler_intel.json")) == ("AMD Radeon Pro 5500M", 8.0, False)
    assert parse_system_profiler("not json") is None


def test_rocm_wmic_and_meminfo_are_parsed():
    assert parse_rocm_smi(fixture("rocm_smi.json")) == ("AMD GPU card0", 16.0)
    assert parse_wmic(fixture("wmic.txt")) == "NVIDIA GeForce RTX 3060"
    assert parse_wmic("Name\r\n\r\n") is None
    assert parse_meminfo(fixture("meminfo_vps.txt")) == 23.4


def test_comfy_system_stats_is_authoritative_about_the_gpu():
    version, devices = parse_comfy_system_stats(json.loads(fixture("comfy_system_stats.json")))
    assert version == "0.3.9"
    assert devices == [{"name": "cuda:0 NVIDIA GeForce RTX 4090", "type": "cuda", "vram_gb": 24.0, "vram_free_gb": 21.4}]


# ------------------------------------------------------------ classification

@pytest.mark.parametrize("vram,backend,container,expected", [
    (None, "cpu", False, "cpu_only"),
    (None, "cpu", True, "container_no_gpu"),
    (None, "mps", False, "apple_mps"),
    (4.0, "cuda", False, "low"),
    (7.9, "cuda", False, "low"),
    (8.0, "cuda", False, "mid"),
    (12.0, "cuda", False, "mid"),
    (16.0, "rocm", False, "high"),
    (23.9, "cuda", False, "high"),
    (24.0, "cuda", False, "pro"),
    (48.0, "cuda", True, "pro"),
])
def test_the_profile_follows_the_spec_thresholds(vram, backend, container, expected):
    assert classify(vram, backend, container) == expected
    assert expected in PROFILES


# ------------------------------------------------------------------ probing

class Runner:
    """A fake for subprocess: answers by the command's first word."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, command):
        self.calls.append(command[0])
        answer = self.answers.get(command[0])
        if answer is None:
            raise FileNotFoundError(command[0])
        return answer


def files(**contents):
    def read(path):
        for name, text in contents.items():
            if path.endswith(name):
                return text
        raise FileNotFoundError(path)
    return read


def test_an_nvidia_box_is_cuda_and_the_probes_run_in_the_spec_order():
    runner = Runner({"nvidia-smi": fixture("nvidia_smi_4090.csv")})
    profile = probe(run=runner, read_file=files(meminfo=fixture("meminfo_vps.txt")), platform="linux",
                    importer=lambda name: None, container=False, disk_free_gb=lambda: 80.0,
                    comfy=lambda url: (False, "unreachable", None), ollama=lambda url: (False, "unreachable", []))
    assert (profile.gpu_name, profile.vram_gb, profile.backend, profile.profile) == ("NVIDIA GeForce RTX 4090", 24.0, "cuda", "pro")
    assert profile.ram_gb == 23.4 and profile.disk_free_gb == 80.0
    assert runner.calls[0] == "nvidia-smi"
    assert profile.sources[0].startswith("nvidia-smi")
    assert profile.comfyui["reachable"] is False and profile.ollama["reachable"] is False


def test_this_vps_is_cpu_only_and_a_container_without_a_gpu_is_its_own_profile():
    kwargs = dict(run=Runner({}), read_file=files(meminfo=fixture("meminfo_vps.txt")), platform="linux",
                  importer=lambda name: None, disk_free_gb=lambda: 80.0,
                  comfy=lambda url: (False, "unreachable at http://127.0.0.1:8188", None),
                  ollama=lambda url: (False, "unreachable", []))
    host = probe(container=False, **kwargs)
    assert host.profile == "cpu_only" and host.gpu_name is None and host.backend == "cpu"
    assert any("no GPU" in s for s in host.sources)
    inside = probe(container=True, **kwargs)
    assert inside.profile == "container_no_gpu" and inside.in_container is True
    assert any("host.docker.internal" in r["install_hint"] for r in inside.recommendations)


def test_apple_silicon_is_mps():
    runner = Runner({"system_profiler": fixture("system_profiler_m2.json")})
    profile = probe(run=runner, read_file=files(), platform="darwin", importer=lambda name: None, container=False,
                    disk_free_gb=lambda: 200.0, comfy=lambda url: (False, "x", None), ollama=lambda url: (False, "x", []),
                    sysctl_memsize=lambda: 34359738368)
    assert (profile.gpu_name, profile.backend, profile.profile, profile.ram_gb) == ("Apple M2 Pro", "mps", "apple_mps", 32.0)


def test_comfyui_system_stats_override_a_missing_nvidia_smi_and_ollama_lists_its_models():
    stats = json.loads(fixture("comfy_system_stats.json"))
    profile = probe(run=Runner({}), read_file=files(meminfo=fixture("meminfo_vps.txt")), platform="linux",
                    importer=lambda name: None, container=True, disk_free_gb=lambda: 80.0,
                    comfy=lambda url: (True, "ComfyUI 0.3.9", stats),
                    ollama=lambda url: (True, "3 models", ["qwen3:4b", "gemma3:4b", "llama3:8b"]))
    assert profile.gpu_name == "cuda:0 NVIDIA GeForce RTX 4090" and profile.vram_gb == 24.0 and profile.backend == "cuda"
    assert profile.profile == "pro", "a GPU reachable through ComfyUI on the host beats 'container without a GPU'"
    assert profile.comfyui == {"reachable": True, "note": "ComfyUI 0.3.9", "version": "0.3.9", "url": profile.comfyui["url"],
                               "devices": [{"name": "cuda:0 NVIDIA GeForce RTX 4090", "type": "cuda", "vram_gb": 24.0, "vram_free_gb": 21.4}]}
    assert profile.ollama["models"] == ["qwen3:4b", "gemma3:4b", "llama3:8b"]
    assert any("comfyui" in s.lower() for s in profile.sources)


def test_a_failing_probe_is_recorded_not_hidden():
    def explode(command):
        raise PermissionError("nvidia-smi: permission denied")
    profile = probe(run=explode, read_file=files(meminfo=fixture("meminfo_vps.txt")), platform="linux",
                    importer=lambda name: None, container=False, disk_free_gb=lambda: 1.0,
                    comfy=lambda url: (False, "x", None), ollama=lambda url: (False, "x", []))
    assert profile.profile == "cpu_only"
    assert any("permission denied" in e for e in profile.errors)


def test_torch_cuda_is_consulted_only_when_importable_and_nvidia_smi_is_absent():
    class Cuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def get_device_name(index):
            return "NVIDIA GeForce RTX 3080"

        @staticmethod
        def get_device_properties(index):
            return type("P", (), {"total_memory": 10 * 1024 ** 3})()

    torch = type("Torch", (), {"cuda": Cuda})()
    profile = probe(run=Runner({}), read_file=files(meminfo=fixture("meminfo_vps.txt")), platform="linux",
                    importer=lambda name: torch if name == "torch" else None, container=False, disk_free_gb=lambda: 1.0,
                    comfy=lambda url: (False, "x", None), ollama=lambda url: (False, "x", []))
    assert (profile.gpu_name, profile.vram_gb, profile.profile) == ("NVIDIA GeForce RTX 3080", 10.0, "mid")
    assert any(s.startswith("torch.cuda") for s in profile.sources)


# ---------------------------------------------------------- recommendations

RANK = {"cpu_only": 0, "container_no_gpu": 0, "apple_mps": 1, "low": 1, "mid": 2, "high": 3, "pro": 4}


@pytest.mark.parametrize("name", PROFILES)
def test_every_profile_has_recommendations_whose_workflows_it_can_run(name):
    rows = recommendations_for(name)
    assert rows, name
    for row in rows:
        assert set(row) >= {"task", "model", "install_hint"}, row
        workflow = row.get("workflow")
        if workflow:
            assert workflow in TEMPLATES
            assert RANK[load_template(workflow)["min_profile"]] <= RANK[name], (name, workflow)
    if name in ("cpu_only", "container_no_gpu"):
        assert all(not r.get("workflow") for r in rows), "no local image workflow without a GPU"
        assert any("Piper" in r["model"] or "Kokoro" in r["model"] for r in rows)


def test_the_profile_serialises_for_the_api():
    profile = probe(run=Runner({}), read_file=files(meminfo=fixture("meminfo_vps.txt")), platform="linux",
                    importer=lambda name: None, container=False, disk_free_gb=lambda: 80.0,
                    comfy=lambda url: (False, "x", None), ollama=lambda url: (False, "x", []))
    data = json.loads(json.dumps(to_dict(profile)))
    assert data["profile"] == "cpu_only" and data["recommendations"] and data["sources"]
    assert set(data) >= {"gpu_name", "vram_gb", "ram_gb", "disk_free_gb", "backend", "in_container", "comfyui", "ollama",
                         "profile", "recommendations", "sources", "errors", "probed_at"}


def test_the_live_probe_answers_on_this_machine():
    profile = probe()
    assert profile.profile in PROFILES
    assert profile.ram_gb and profile.ram_gb > 0
    assert isinstance(profile, HardwareProfile)


# -------------------------------------------------------------------- route

@pytest.fixture
def client(monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.api.routes import hardware as hardware_route

    monkeypatch.setenv("DISABLE_AUTH", "1")
    hardware_route.reset_cache()
    app = FastAPI()
    app.include_router(hardware_route.router)
    with TestClient(app) as test_client:
        yield test_client
    hardware_route.reset_cache()


def test_get_api_hardware_returns_the_profile_and_caches_it(client, monkeypatch):
    from web.api.routes import hardware as hardware_route

    calls = []

    def fake_probe(**kw):
        calls.append(1)
        return probe(run=Runner({}), read_file=files(meminfo=fixture("meminfo_vps.txt")), platform="linux",
                     importer=lambda name: None, container=False, disk_free_gb=lambda: 80.0,
                     comfy=lambda url: (False, "x", None), ollama=lambda url: (False, "x", []))

    monkeypatch.setattr(hardware_route, "probe", fake_probe)
    first = client.get("/api/hardware")
    assert first.status_code == 200 and first.json()["profile"] == "cpu_only"
    assert client.get("/api/hardware").json()["profile"] == "cpu_only"
    assert calls == [1], "cached for a minute"
    assert client.get("/api/hardware?refresh=1").status_code == 200
    assert calls == [1, 1]


def test_the_router_is_included_by_the_app_and_token_gated():
    src = (pathlib.Path(__file__).resolve().parents[1] / "web" / "api" / "app.py").read_text(encoding="utf-8")
    assert "app.include_router(hardware.router)" in src
    route = (pathlib.Path(__file__).resolve().parents[1] / "web" / "api" / "routes" / "hardware.py").read_text(encoding="utf-8")
    assert "dependencies=[Depends(require_token)]" in route

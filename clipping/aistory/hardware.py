"""The hardware profile behind "what can run locally" (spec 8.2, DEC-101).

Probes, in this order and each with a fixture-tested parser: ``pynvml`` →
``nvidia-smi`` → ``torch.cuda`` → macOS ``system_profiler`` (Apple Silicon =
MPS) → ``rocm-smi`` → Windows ``wmic`` (name only) → RAM → free disk → ComfyUI
``/system_stats`` (authoritative about the GPU when reachable, because a
container without GPU passthrough still reaches the host's ComfyUI) → Ollama
``/api/tags``. Every dependency of the probe is injectable, so the tests
never touch this machine; a probe that fails is recorded in ``errors``, an
absent tool is simply skipped. The profile maps to one of seven names with
the thresholds of spec 8.2 and a table of what to run.

Stdlib only; ``pynvml`` and ``torch`` are imported through the injected
importer and only if present.
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

from clipping.providers import generation
from clipping.providers.local_comfyui import ComfyUIClient
from clipping.providers.local_ollama import OllamaClient
from clipping.providers.transport import APIConnectionError, APITimeoutError

PROFILES = ("cpu_only", "low", "mid", "high", "pro", "apple_mps", "container_no_gpu")
GIB = 1024 ** 3
COMMAND_TIMEOUT_SECONDS = 10


@dataclass
class HardwareProfile:
    gpu_name: str | None = None
    vram_gb: float | None = None
    vram_free_gb: float | None = None
    ram_gb: float | None = None
    disk_free_gb: float | None = None
    backend: str = "cpu"            # cuda | mps | rocm | cpu
    in_container: bool = False
    comfyui: dict = field(default_factory=dict)
    ollama: dict = field(default_factory=dict)
    profile: str = "cpu_only"
    recommendations: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    probed_at: str = ""


# ------------------------------------------------------------------ parsers

def _gb(value, unit_bytes) -> float:
    return round(float(value) * unit_bytes / GIB, 1)


def parse_nvidia_smi(text: str):
    """``name, memory.total, memory.free`` CSV (MiB, no units) → ``(name, total_gb, free_gb)`` of the largest GPU."""
    best = None
    for line in (text or "").splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            total = float(parts[1])
        except ValueError:
            continue
        free = None
        if len(parts) > 2:
            try:
                free = float(parts[2])
            except ValueError:
                free = None
        candidate = (parts[0], _gb(total, 1024 ** 2), _gb(free, 1024 ** 2) if free is not None else None)
        if best is None or candidate[1] > best[1]:
            best = candidate
    return best


def _size_to_gb(text: str):
    match = re.match(r"\s*([\d.]+)\s*(GB|MB|TB)", str(text or ""), re.I)
    if not match:
        return None
    value, unit = float(match.group(1)), match.group(2).upper()
    factor = {"GB": 1.0, "MB": 1 / 1024, "TB": 1024.0}[unit]
    return round(value * factor, 1)


def parse_system_profiler(text: str):
    """``system_profiler SPDisplaysDataType -json`` → ``(name, vram_gb|None, is_apple_silicon)``."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    entries = data.get("SPDisplaysDataType") or []
    if not isinstance(entries, list) or not entries:
        return None
    for entry in entries:
        vendor = str(entry.get("spdisplays_vendor") or entry.get("sppci_vendor") or "")
        name = str(entry.get("_name") or entry.get("sppci_model") or "")
        if "apple" in vendor.lower() or name.lower().startswith("apple "):
            return name, None, True
    best = None
    for entry in entries:
        name = str(entry.get("_name") or entry.get("sppci_model") or "GPU")
        vram = _size_to_gb(entry.get("spdisplays_vram"))
        if best is None or (vram or 0) > (best[1] or 0):
            best = (name, vram, False)
    return best


def parse_rocm_smi(text: str):
    """``rocm-smi --showmeminfo vram --json`` → ``(name, total_gb)`` of the largest card."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    best = None
    for card, info in (data or {}).items():
        if not isinstance(info, dict):
            continue
        total = info.get("VRAM Total Memory (B)")
        if total is None:
            continue
        try:
            total_gb = _gb(float(total), 1)
        except ValueError:
            continue
        if best is None or total_gb > best[1]:
            best = (f"AMD GPU {card}", total_gb)
    return best


def parse_wmic(text: str):
    """``wmic path win32_VideoController get Name`` → the discrete card's name, else the first."""
    names = [line.strip() for line in (text or "").splitlines() if line.strip() and line.strip().lower() != "name"]
    if not names:
        return None
    for name in names:
        if re.search(r"nvidia|geforce|rtx|radeon|amd", name, re.I):
            return name
    return names[0]


def parse_meminfo(text: str):
    match = re.search(r"^MemTotal:\s+(\d+)\s*kB", text or "", re.M)
    return _gb(int(match.group(1)), 1024) if match else None


def parse_comfy_system_stats(payload: dict):
    """``GET /system_stats`` → ``(version, [{name, type, vram_gb, vram_free_gb}])``."""
    system = (payload or {}).get("system") or {}
    version = system.get("comfyui_version")
    devices = []
    for device in (payload or {}).get("devices") or []:
        name = str(device.get("name") or "").split(" : ")[0].strip()
        if not name:
            continue
        devices.append({
            "name": name,
            "type": str(device.get("type") or ""),
            "vram_gb": _gb(device.get("vram_total") or 0, 1),
            "vram_free_gb": _gb(device.get("vram_free") or 0, 1),
        })
    return version, devices


# ----------------------------------------------------------- classification

def classify(vram_gb, backend: str, in_container: bool) -> str:
    if backend == "mps":
        return "apple_mps"
    if vram_gb is None or backend == "cpu":
        return "container_no_gpu" if in_container else "cpu_only"
    vram = round(float(vram_gb), 1)
    if vram < 8:
        return "low"
    if vram < 16:
        return "mid"
    if vram < 24:
        return "high"
    return "pro"


# ---------------------------------------------------------- recommendations

_HOST_HINT = ("run ComfyUI and Ollama on the Docker host and point the container at them: "
              "LOCAL_COMFYUI_URL=http://host.docker.internal:8188, LOCAL_OLLAMA_URL=http://host.docker.internal:11434 "
              "(docker-compose adds the host-gateway extra host)")
_LOCAL_TTS = "pip install 'rzdhop-ai[local-tts]'"
_KLEIN_FILES = ("download flux2-klein-4b.safetensors → ComfyUI/models/diffusion_models, qwen_3_4b.safetensors → "
                "models/text_encoders, flux2-vae.safetensors → models/vae")

_CPU_ROWS = [
    {"task": "render", "model": "FFmpeg (Tier 1 stills + motion)", "install_hint": "already required by the clip pipeline", "workflow": None},
    {"task": "tts", "model": "Piper (fr_FR-siwis-medium, fr_FR-tom-medium)", "install_hint": f"{_LOCAL_TTS}; voices from huggingface.co/rhasspy/piper-voices", "workflow": None},
    {"task": "tts", "model": "Kokoro-82M (ff_siwis)", "install_hint": _LOCAL_TTS, "workflow": None},
    {"task": "llm", "model": "Ollama qwen3:4b (cheap text steps)", "install_hint": "install Ollama, then: ollama pull qwen3:4b", "workflow": None},
    {"task": "images", "model": "hosted chains (IMAGE_CHAIN / IMAGE_EDIT_CHAIN)", "install_hint": "no local image model without a GPU; the free hosted links or a paid editor within the caps", "workflow": None},
]
_LOW_ROWS = [
    {"task": "images", "model": "FLUX.2 [klein] 4B (text to image)", "install_hint": f"ComfyUI; {_KLEIN_FILES}", "workflow": "t2i_flux2_klein"},
    {"task": "images", "model": "SDXL-Turbo", "install_hint": "ComfyUI; sd_xl_turbo_1.0_fp16.safetensors → models/checkpoints", "workflow": None},
    {"task": "video", "model": "LTX-Video 2B (short clips, phase 6)", "install_hint": "ComfyUI; ltx-video-2b → models/checkpoints", "workflow": None},
]
_MID_ROWS = [
    {"task": "image edit", "model": "FLUX.2 [klein] 4B with reference images", "install_hint": f"ComfyUI; {_KLEIN_FILES}", "workflow": "edit_flux2_klein_multiref"},
    {"task": "images", "model": "FLUX.1 schnell fp8", "install_hint": "ComfyUI; flux1-schnell-fp8.safetensors → models/checkpoints", "workflow": None},
    {"task": "video", "model": "Wan 2.1 1.3B / Wan 2.2 5B with offload (phase 6)", "install_hint": "ComfyUI; wan2.2_ti2v_5B_fp8.safetensors → models/diffusion_models", "workflow": None},
    {"task": "tts", "model": "Chatterbox Multilingual (zero-shot voices)", "install_hint": _LOCAL_TTS, "workflow": None},
]
_HIGH_ROWS = [
    {"task": "image edit", "model": "Qwen-Image-Edit-2509 (GGUF q4)", "install_hint": "ComfyUI + ComfyUI-GGUF custom node; qwen_image_edit_2509_q4.gguf → models/unet, qwen_2.5_vl_7b_fp8_scaled.safetensors → models/text_encoders, qwen_image_vae.safetensors → models/vae", "workflow": "edit_qwen_image"},
    {"task": "images", "model": "FLUX.1 dev fp8", "install_hint": "ComfyUI; flux1-dev-fp8.safetensors → models/checkpoints", "workflow": None},
    {"task": "video", "model": "Wan 2.2 14B GGUF + Lightning LoRA (phase 6)", "install_hint": "ComfyUI + ComfyUI-GGUF; wan2.2 14B q4 → models/unet", "workflow": None},
]
_PRO_ROWS = [
    {"task": "video", "model": "Wan 2.2 14B fp8 (phase 6)", "install_hint": "ComfyUI; wan2.2 14B fp8 → models/diffusion_models", "workflow": None},
    {"task": "video", "model": "LTX-2 (phase 6)", "install_hint": "ComfyUI; ltx-2-13b.safetensors → models/checkpoints", "workflow": None},
    {"task": "video", "model": "HunyuanVideo 1.5 (phase 6)", "install_hint": "ComfyUI; hunyuanvideo 1.5 → models/diffusion_models", "workflow": None},
]
_APPLE_ROWS = [
    {"task": "images", "model": "FLUX.2 [klein] 4B via ComfyUI on MPS", "install_hint": f"ComfyUI (MPS); {_KLEIN_FILES}", "workflow": "t2i_flux2_klein"},
    {"task": "video", "model": "none recommended on MPS (no Wan)", "install_hint": "use the hosted VIDEO_CHAIN links (phase 6)", "workflow": None},
]

RECOMMENDATIONS = {
    "cpu_only": _CPU_ROWS,
    "container_no_gpu": [{"task": "local generation", "model": "ComfyUI / Ollama on the host", "install_hint": _HOST_HINT, "workflow": None}] + _CPU_ROWS,
    "low": _CPU_ROWS + _LOW_ROWS,
    "mid": _CPU_ROWS + _LOW_ROWS + _MID_ROWS,
    "high": _CPU_ROWS + _LOW_ROWS + _MID_ROWS + _HIGH_ROWS,
    "pro": _CPU_ROWS + _LOW_ROWS + _MID_ROWS + _HIGH_ROWS + _PRO_ROWS,
    "apple_mps": [r for r in _CPU_ROWS if r["task"] != "images"] + _APPLE_ROWS,
}


def recommendations_for(profile: str) -> list:
    return [dict(row) for row in RECOMMENDATIONS.get(profile, _CPU_ROWS)]


# ------------------------------------------------------------------- probes

def _run(command: list) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=False)
    if completed.returncode != 0 and not completed.stdout.strip():
        raise RuntimeError(f"{command[0]} exited {completed.returncode}: {completed.stderr.strip()[:200]}")
    return completed.stdout


def _read_file(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _import(name: str):
    try:
        return importlib.import_module(name)
    except Exception:  # noqa: BLE001 - absent or broken: either way, not usable
        return None


def _disk_free_gb() -> float:
    return round(shutil.disk_usage(os.path.abspath(os.sep)).free / GIB, 1)


def _comfy(url: str):
    client = ComfyUIClient(url)
    try:
        stats = client.system_stats()
    except (APIConnectionError, APITimeoutError) as exc:
        return False, f"unreachable at {url} ({exc})", None
    version = (stats.get("system") or {}).get("comfyui_version", "?")
    return True, f"ComfyUI {version} at {url}", stats


def _ollama(url: str):
    client = OllamaClient(url)
    try:
        names = client.tags()
    except (APIConnectionError, APITimeoutError) as exc:
        return False, f"unreachable at {url} ({exc})", []
    return True, f"{url}: {len(names)} model(s) pulled", names


def _sysctl_memsize() -> int:
    return int(_run(["sysctl", "-n", "hw.memsize"]).strip())


def probe(*, run=None, read_file=None, platform=None, importer=None, container=None, disk_free_gb=None,
          comfy=None, ollama=None, sysctl_memsize=None, env=None) -> HardwareProfile:
    """Probe this machine. Every collaborator is injectable; the defaults touch the real box."""
    run = run or _run
    read_file = read_file or _read_file
    platform = platform or sys.platform
    importer = importer or _import
    in_container = generation.in_container() if container is None else bool(container)
    disk_free_gb = disk_free_gb or _disk_free_gb
    comfy = comfy or _comfy
    ollama = ollama or _ollama
    sysctl_memsize = sysctl_memsize or _sysctl_memsize
    env = os.environ if env is None else env

    profile = HardwareProfile(in_container=in_container)
    found = False

    def attempt(label, fn):
        nonlocal found
        if found:
            return
        try:
            result = fn()
        except FileNotFoundError:
            return  # the tool is not on this machine: not an error
        except Exception as exc:  # noqa: BLE001 - recorded, never hidden
            profile.errors.append(f"{label}: {exc}")
            return
        if result:
            found = True
            profile.sources.append(label)

    # 1. pynvml
    def via_pynvml():
        pynvml = importer("pynvml")
        if pynvml is None:
            return False
        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        best = None
        for index in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(index)
            name = pynvml.nvmlDeviceGetName(handle)
            name = name.decode() if isinstance(name, bytes) else str(name)
            memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
            candidate = (name, _gb(memory.total, 1), _gb(memory.free, 1))
            if best is None or candidate[1] > best[1]:
                best = candidate
        if best is None:
            return False
        profile.gpu_name, profile.vram_gb, profile.vram_free_gb, profile.backend = best[0], best[1], best[2], "cuda"
        return True

    # 2. nvidia-smi
    def via_nvidia_smi():
        parsed = parse_nvidia_smi(run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"]))
        if not parsed:
            return False
        profile.gpu_name, profile.vram_gb, profile.vram_free_gb, profile.backend = parsed[0], parsed[1], parsed[2], "cuda"
        return True

    # 3. torch.cuda
    def via_torch():
        torch = importer("torch")
        if torch is None or not torch.cuda.is_available():
            return False
        props = torch.cuda.get_device_properties(0)
        profile.gpu_name = str(torch.cuda.get_device_name(0))
        profile.vram_gb = _gb(getattr(props, "total_memory", 0), 1)
        profile.backend = "cuda"
        return True

    # 4. macOS
    def via_system_profiler():
        if not platform.startswith("darwin"):
            return False
        parsed = parse_system_profiler(run(["system_profiler", "SPDisplaysDataType", "-json"]))
        if not parsed:
            return False
        name, vram, apple = parsed
        profile.gpu_name, profile.vram_gb = name, vram
        profile.backend = "mps" if apple else "cpu"
        return True

    # 5. rocm
    def via_rocm():
        parsed = parse_rocm_smi(run(["rocm-smi", "--showmeminfo", "vram", "--json"]))
        if not parsed:
            return False
        profile.gpu_name, profile.vram_gb, profile.backend = parsed[0], parsed[1], "rocm"
        return True

    # 6. windows, name only
    def via_wmic():
        if not platform.startswith("win"):
            return False
        name = parse_wmic(run(["wmic", "path", "win32_VideoController", "get", "Name"]))
        if not name:
            return False
        profile.gpu_name = name
        return True

    for label, fn in (("pynvml", via_pynvml), ("nvidia-smi --query-gpu", via_nvidia_smi), ("torch.cuda", via_torch),
                      ("system_profiler SPDisplaysDataType", via_system_profiler), ("rocm-smi --showmeminfo", via_rocm),
                      ("wmic win32_VideoController", via_wmic)):
        attempt(label, fn)
    if not found:
        profile.sources.append("no GPU found (pynvml, nvidia-smi, torch.cuda, system_profiler, rocm-smi, wmic)")

    # 7. RAM
    try:
        if platform.startswith("darwin"):
            profile.ram_gb = round(int(sysctl_memsize()) / GIB, 1)
        elif platform.startswith("win"):
            text = run(["wmic", "OS", "get", "TotalVisibleMemorySize", "/value"])
            match = re.search(r"TotalVisibleMemorySize=(\d+)", text)
            profile.ram_gb = _gb(int(match.group(1)), 1024) if match else None
        else:
            profile.ram_gb = parse_meminfo(read_file("/proc/meminfo"))
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        profile.errors.append(f"ram: {exc}")

    # 8. disk
    try:
        profile.disk_free_gb = float(disk_free_gb())
    except Exception as exc:  # noqa: BLE001
        profile.errors.append(f"disk: {exc}")

    # 9. ComfyUI, authoritative about the GPU it sees
    comfy_url = generation.local_url("comfyui", env)
    try:
        ok, note, stats = comfy(comfy_url)
    except Exception as exc:  # noqa: BLE001
        ok, note, stats = False, f"probe failed: {exc}", None
        profile.errors.append(f"comfyui: {exc}")
    version, devices = parse_comfy_system_stats(stats) if ok and stats else (None, [])
    profile.comfyui = {"reachable": bool(ok), "note": note, "version": version, "url": comfy_url, "devices": devices}
    if devices:
        best = max(devices, key=lambda d: d["vram_gb"])
        if best["vram_gb"] > 0:
            profile.gpu_name, profile.vram_gb, profile.vram_free_gb = best["name"], best["vram_gb"], best["vram_free_gb"]
            kind = best["type"].lower()
            profile.backend = "mps" if kind == "mps" else "rocm" if "rocm" in kind or "hip" in kind else "cuda"
            profile.sources.append("comfyui /system_stats (authoritative)")

    # 10. Ollama
    ollama_url = generation.local_url("ollama", env)
    try:
        ok, note, models = ollama(ollama_url)
    except Exception as exc:  # noqa: BLE001
        ok, note, models = False, f"probe failed: {exc}", []
        profile.errors.append(f"ollama: {exc}")
    profile.ollama = {"reachable": bool(ok), "note": note, "url": ollama_url, "models": list(models or [])}

    profile.profile = classify(profile.vram_gb, profile.backend, profile.in_container)
    profile.recommendations = recommendations_for(profile.profile)
    profile.probed_at = datetime.now(timezone.utc).isoformat()
    return profile


def to_dict(profile: HardwareProfile) -> dict:
    return dataclasses.asdict(profile)

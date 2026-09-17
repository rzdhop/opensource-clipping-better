"""
clipping.device — decide what hardware Whisper should actually run on.

The pipeline used to default to ``cuda`` / ``float16`` unconditionally, which
crashes outright on any machine without a CUDA-enabled CTranslate2:

    ValueError: This CTranslate2 package was not compiled with CUDA support

and, once the user works around that by forcing ``cpu``, crashes again on the
compute type, because CPU cannot do float16:

    ValueError: Requested float16 compute type, but the target device or
    backend do not support efficient float16 computation.

Why this asks CTranslate2 and not torch
---------------------------------------
``torch.cuda.is_available()`` is the obvious check and it is the WRONG one.
Whisper runs on CTranslate2, and the two are packaged independently: the
default PyPI ``ctranslate2`` wheel is CPU-only, so a CUDA-capable torch can sit
right next to a CTranslate2 that cannot use the GPU at all. Trusting torch
there produces exactly the crash above.

Checking for the NVIDIA container runtime or ``nvidia-smi`` is no better: a
host can have the runtime registered with no usable GPU behind it, which again
selects CUDA wrongly.

So the primary signal is CTranslate2's own device count. torch is consulted
only as a fallback when CTranslate2 will not answer, and a negative result is
always safe: the worst case is running on CPU when a GPU was available, which
is slow rather than broken.
"""

from __future__ import annotations

# Compute types that each device can actually handle. float16 on CPU is the
# specific combination that raises rather than degrading.
_CUDA_COMPUTE_TYPE = "float16"
_CPU_COMPUTE_TYPE = "int8"

VALID_DEVICES = ("auto", "cuda", "cpu")
VALID_COMPUTE_TYPES = ("auto", "float16", "float32", "int8", "int8_float16", "default")


def whisper_cuda_available() -> bool:
    """Whether Whisper can really run on CUDA here. Never raises.

    Asks CTranslate2 first, since it is CTranslate2 that fails. Falls back to
    torch only when CTranslate2 cannot be asked at all.
    """
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        # Not installed, too old to expose the helper, or the driver query
        # itself blew up. Any of those mean "do not promise CUDA".
        pass

    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def resolve_whisper_runtime(
    device: str | None,
    compute_type: str | None,
    *,
    cuda_available=None,
) -> tuple[str, str]:
    """Turn the requested device/compute type into a pair that actually works.

    ``auto`` (the default) picks CUDA with float16 when CUDA is genuinely
    usable, otherwise CPU with int8.

    An explicit request is honoured, with one exception: ``cpu`` together with
    ``float16`` is downgraded to int8 and warned about, because that pair raises
    instead of merely running badly. Silently crashing is worse than silently
    being slightly more accurate than asked.

    ``cuda_available`` is injectable so this is testable without a GPU, and
    without ctranslate2 or torch installed at all.
    """
    detect = whisper_cuda_available if cuda_available is None else cuda_available

    requested_device = (device or "auto").strip().lower()
    requested_compute = (compute_type or "auto").strip().lower()

    if requested_device == "auto":
        resolved_device = "cuda" if detect() else "cpu"
    else:
        resolved_device = requested_device

    # A GPU was asked for by name but is not usable: fall back rather than let
    # CTranslate2 raise, and say so, because this silently costs a lot of time.
    if resolved_device == "cuda" and not detect():
        print(
            "   ⚠️ CUDA was requested but this CTranslate2 build cannot use a GPU "
            "— falling back to CPU. Transcription will be much slower; supply "
            "--transcript <file.vtt> to skip Whisper entirely.",
            flush=True,
        )
        resolved_device = "cpu"

    if requested_compute == "auto":
        resolved_compute = (
            _CUDA_COMPUTE_TYPE if resolved_device == "cuda" else _CPU_COMPUTE_TYPE
        )
    elif resolved_device == "cpu" and requested_compute == "float16":
        print(
            "   ⚠️ float16 is not supported on CPU — using int8 instead. "
            "Pass --whisper-compute-type explicitly to choose another.",
            flush=True,
        )
        resolved_compute = _CPU_COMPUTE_TYPE
    else:
        resolved_compute = requested_compute

    return resolved_device, resolved_compute

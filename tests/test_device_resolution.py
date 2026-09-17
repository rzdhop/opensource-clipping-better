"""Whisper device / compute-type resolution.

Two crashes motivated this, both from defaults that assumed a GPU:

    ValueError: This CTranslate2 package was not compiled with CUDA support
    ValueError: Requested float16 compute type, but the target device or
    backend do not support efficient float16 computation.

And a third trap: ``--whisper-device auto`` was already an accepted CLI choice
and ``WhisperDevice.AUTO`` already existed, but nothing resolved the string, so
it reached ``WhisperModel(device="auto")`` and raised.

Detection is injected throughout, so these run with no GPU and without
ctranslate2 or torch installed -- which is also the CI environment (pytest and
nothing else, see DEC-012).
"""

import pytest

from clipping.device import (
    VALID_COMPUTE_TYPES,
    VALID_DEVICES,
    resolve_whisper_runtime,
    whisper_cuda_available,
)

no_cuda = lambda: False      # noqa: E731 - a stub, reads better inline
has_cuda = lambda: True      # noqa: E731


# ------------------------------------------------------------------ auto

def test_auto_without_cuda_picks_cpu_and_int8():
    """The reported crash: auto must never hand CPU a float16 request."""
    assert resolve_whisper_runtime("auto", "auto", cuda_available=no_cuda) == ("cpu", "int8")


def test_auto_with_cuda_picks_cuda_and_float16():
    assert resolve_whisper_runtime("auto", "auto", cuda_available=has_cuda) == (
        "cuda",
        "float16",
    )


def test_none_is_treated_as_auto():
    """cfg may carry None if a caller omits the field entirely."""
    assert resolve_whisper_runtime(None, None, cuda_available=no_cuda) == ("cpu", "int8")


# -------------------------------------------------- explicit requests

def test_explicit_cpu_float16_is_downgraded():
    """float16 on CPU raises rather than degrading, so it is corrected."""
    assert resolve_whisper_runtime("cpu", "float16", cuda_available=no_cuda) == (
        "cpu",
        "int8",
    )


def test_explicit_cuda_falls_back_when_cuda_is_unusable():
    """Asking for CUDA on a CPU-only CTranslate2 must not crash."""
    device, compute = resolve_whisper_runtime("cuda", "float16", cuda_available=no_cuda)

    assert device == "cpu"
    assert compute == "int8", "float16 must not survive the fallback to CPU"


def test_explicit_cuda_is_respected_when_available():
    assert resolve_whisper_runtime("cuda", "float16", cuda_available=has_cuda) == (
        "cuda",
        "float16",
    )


def test_a_workable_explicit_compute_type_is_left_alone():
    """Only the combination that raises is overridden; nothing else."""
    assert resolve_whisper_runtime("cpu", "float32", cuda_available=no_cuda) == (
        "cpu",
        "float32",
    )
    assert resolve_whisper_runtime("cuda", "int8", cuda_available=has_cuda) == (
        "cuda",
        "int8",
    )


@pytest.mark.parametrize("device", ["CUDA", "Cpu", " auto "])
def test_case_and_whitespace_are_tolerated(device):
    resolved, _ = resolve_whisper_runtime(device, "auto", cuda_available=no_cuda)

    assert resolved == "cpu"


# ------------------------------------------------------- the guarantee

@pytest.mark.parametrize("device", VALID_DEVICES)
@pytest.mark.parametrize("compute", VALID_COMPUTE_TYPES)
@pytest.mark.parametrize("cuda", [no_cuda, has_cuda], ids=["no_cuda", "cuda"])
def test_never_returns_auto_and_never_pairs_float16_with_cpu(device, compute, cuda):
    """The two invariants WhisperModel depends on, over every CLI combination.

    'auto' reaching WhisperModel is the third bug; float16-on-CPU is the second.
    """
    resolved_device, resolved_compute = resolve_whisper_runtime(
        device, compute, cuda_available=cuda
    )

    assert resolved_device in ("cuda", "cpu"), "literal 'auto' would crash WhisperModel"
    assert resolved_compute != "auto", "literal 'auto' is not a compute type"
    assert not (resolved_device == "cpu" and resolved_compute == "float16")


# ------------------------------------------------------------ detection

def test_detection_returns_false_when_neither_library_is_installed(monkeypatch):
    """In CI neither ctranslate2 nor torch exists. Detection must answer False
    rather than raising -- the regression guard for the original crash."""
    import builtins

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name in ("ctranslate2", "torch"):
            raise ImportError(f"No module named {name!r}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)

    result = whisper_cuda_available()

    assert result is False, "must be exactly False, not a falsy value"


def test_detection_returns_a_real_bool_not_a_count(monkeypatch):
    """Callers branch on this with `is`, so a device *count* must not leak out.

    Deliberately stubbed rather than calling the real libraries: importing
    ctranslate2 for real would leave it in sys.modules and break
    test_bypass_does_not_import_ctranslate2, which asserts the --transcript
    path never drags in the ML stack.
    """
    import sys
    import types

    ct2 = types.ModuleType("ctranslate2")
    ct2.get_cuda_device_count = lambda: 3  # truthy int, not a bool
    monkeypatch.setitem(sys.modules, "ctranslate2", ct2)

    result = whisper_cuda_available()

    assert isinstance(result, bool)
    assert result is True


def test_detection_prefers_ctranslate2_over_torch(monkeypatch):
    """torch seeing CUDA is not enough: the default ctranslate2 wheel is
    CPU-only, so torch can report a GPU that Whisper cannot use."""
    import sys
    import types

    ct2 = types.ModuleType("ctranslate2")
    ct2.get_cuda_device_count = lambda: 0
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: True)

    monkeypatch.setitem(sys.modules, "ctranslate2", ct2)
    monkeypatch.setitem(sys.modules, "torch", torch)

    assert whisper_cuda_available() is False, "ctranslate2 must win over torch"


def test_detection_uses_ctranslate2_when_it_reports_a_device(monkeypatch):
    import sys
    import types

    ct2 = types.ModuleType("ctranslate2")
    ct2.get_cuda_device_count = lambda: 1
    monkeypatch.setitem(sys.modules, "ctranslate2", ct2)

    assert whisper_cuda_available() is True


def test_detection_falls_back_to_torch_when_ctranslate2_is_unusable(monkeypatch):
    import sys
    import types

    ct2 = types.ModuleType("ctranslate2")  # present but no such helper
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: True)

    monkeypatch.setitem(sys.modules, "ctranslate2", ct2)
    monkeypatch.setitem(sys.modules, "torch", torch)

    assert whisper_cuda_available() is True


def test_detection_survives_a_driver_error(monkeypatch):
    """A broken driver makes the query itself raise; that means 'no CUDA'."""
    import sys
    import types

    ct2 = types.ModuleType("ctranslate2")

    def boom():
        raise RuntimeError("no CUDA driver")

    ct2.get_cuda_device_count = boom
    monkeypatch.setitem(sys.modules, "ctranslate2", ct2)
    monkeypatch.setitem(sys.modules, "torch", types.ModuleType("torch"))

    assert whisper_cuda_available() is False


# --------------------------------------------------- CLI / schema wiring

def test_cli_defaults_are_auto():
    """A user on a CPU-only box must not have to pass any flag."""
    from clipping import config

    assert config.WHISPER_DEVICE == "auto"
    assert config.WHISPER_COMPUTE_TYPE == "auto"


def test_cli_accepts_every_documented_value(tmp_path):
    """argparse choices come from clipping.device, so they cannot drift."""
    import sys

    from clipping import config

    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00")

    for device in VALID_DEVICES:
        for compute in VALID_COMPUTE_TYPES:
            argv = [
                "main.py", "--video", str(video),
                "--whisper-device", device,
                "--whisper-compute-type", compute,
            ]
            old = sys.argv
            sys.argv = argv
            try:
                cfg = config.build_config()
            finally:
                sys.argv = old
            assert cfg.whisper_device == device
            assert cfg.whisper_compute_type == compute

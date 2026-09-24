"""requirements.txt and pyproject.toml declare the same dependencies.

They had drifted: pyproject.toml lacked fastapi, uvicorn, python-multipart,
pydantic, torch, torchaudio, pyannote.audio, edge-tts and the numpy<2 pin, while
the README offered `uv sync` (which reads pyproject.toml) as an install path. An
install made that way had no web server at all.

The Dockerfile installs from requirements.txt, so that file stays the one the
image is built from; this test only keeps the two from diverging again. Extras
(`yt-dlp[default]`) are ignored: they add optional sub-packages, not a
different requirement.
"""

import pathlib
import re

import pytest

tomllib = pytest.importorskip("tomllib")

ROOT = pathlib.Path(__file__).resolve().parents[1]

_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")


def _normalise(line: str) -> tuple[str, str]:
    match = _REQUIREMENT.match(line)
    assert match, f"unparseable requirement: {line!r}"
    name, _extras, spec = match.groups()
    # PEP 503 name normalisation, so "pyannote.audio" == "pyannote-audio".
    return re.sub(r"[-_.]+", "-", name).lower(), spec.replace(" ", "")


def _from_requirements() -> dict[str, str]:
    deps = {}
    for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            name, spec = _normalise(line)
            deps[name] = spec
    return deps


def _from_pyproject() -> dict[str, str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return dict(_normalise(line) for line in project["dependencies"])


def test_pyproject_declares_everything_requirements_does():
    missing = sorted(set(_from_requirements()) - set(_from_pyproject()))
    assert missing == []


def test_requirements_declares_everything_pyproject_does():
    missing = sorted(set(_from_pyproject()) - set(_from_requirements()))
    assert missing == []


def test_the_version_constraints_agree():
    requirements, pyproject = _from_requirements(), _from_pyproject()
    disagree = {
        name: (requirements[name], pyproject[name])
        for name in set(requirements) & set(pyproject)
        if requirements[name] != pyproject[name]
    }
    assert disagree == {}

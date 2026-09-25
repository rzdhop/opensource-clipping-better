"""The product is called rzdhop AI, and some names must never change.

It was "rzdhop's clips" until 2026-09-25 (DEC-093): the app now has two modes,
Clips and AI Story, so the product name is the umbrella and "Clips" is a mode.

The second half matters more than the first. Several names look like branding
and are not: they are written into existing job directories on disk, or read by
the render layer, and renaming them breaks data rather than appearance.
"""

import ast
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

NEW_NAME = "rzdhop AI"

# Directories that are not ours to rename, or not shipped.
SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".claude", "outputs", "uploads",
    "dist", "venv", ".venv", "assets", "notebooks", "example", "tests",
}

OLD_NAMES = ("OpenSource Clipping", "Opensource Clipping", "OSC Studio",
             "opensource-clipping",
             # The name before the two-mode product (DEC-093). The Clips mode
             # keeps its old wordmark under assets/, which is not scanned.
             "rzdhop's clips")

# Where the OLD name is still correct.
ALLOWED = {
    # This is a fork. Rewriting the upstream project's name in its attribution
    # would misrepresent where the code came from.
    "upstream attribution": ("NaufalRizqullah", "github.com/NaufalRizqullah"),
    # The GitHub repository is still named opensource-clipping-better. Its URL
    # is an address, not the product name: a clone or issue link that avoided
    # it would point at a repository that does not exist, which is what the
    # `your-username/rzdhop-clips` placeholder did. If the repository is ever
    # renamed, GitHub redirects the old URL, and this entry can go.
    "the fork's repository slug": ("opensource-clipping-better",),
}


def shipped_files(suffixes):
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        yield path


# ------------------------------------------------------------- the new name

def test_the_backend_announces_the_new_name():
    src = (ROOT / "web" / "api" / "app.py").read_text(encoding="utf-8")
    assert NEW_NAME in src


def test_the_dashboard_shows_the_new_name():
    for name in ("index.html", "src/App.jsx", "public/manifest.webmanifest"):
        text = (ROOT / "web" / "dashboard" / name).read_text(encoding="utf-8")
        assert NEW_NAME in text, name


def test_the_readmes_carry_the_new_name():
    for name in ("README.md", "README_ID.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert NEW_NAME in text, name


def test_the_logo_assets_name_the_product():
    """The dashboard icon and the README wordmark are derived from the old
    ones (DEC-093); the old wordmark stays for the Clips mode."""
    icon = (ROOT / "web" / "dashboard" / "public" / "icon.svg").read_text(encoding="utf-8")
    assert f'aria-label="{NEW_NAME}"' in icon
    logo = ROOT / "assets" / "images" / "rzdhop-ai-logo.svg"
    assert logo.is_file()
    assert NEW_NAME in logo.read_text(encoding="utf-8")
    assert (ROOT / "assets" / "images" / "rzdhop-clips-logo.svg").is_file()


def test_the_packages_are_renamed():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "rzdhop-ai"' in pyproject

    package = json.loads((ROOT / "web" / "dashboard" / "package.json").read_text())
    assert package["name"] == "rzdhop-ai-dashboard"


def test_the_containers_are_renamed():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "container_name: rzc-backend" in compose
    assert "osc-backend" not in compose


def test_no_user_facing_file_still_carries_the_old_name():
    offenders = []
    for path in shipped_files({".py", ".md", ".html", ".jsx", ".yml", ".yaml", ".toml"}):
        text = path.read_text(encoding="utf-8", errors="replace")
        for old in OLD_NAMES:
            for line in text.splitlines():
                if old not in line:
                    continue
                if any(token in line for group in ALLOWED.values() for token in group):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}: {line.strip()[:80]}")
    assert offenders == [], "old product name still present:\n" + "\n".join(offenders[:15])


# --------------------------------------------- what must NOT have been renamed

@pytest.mark.parametrize("name,why", [
    ("gemini_response.json",
     "every existing job directory holds one; Clone & Rerun reads it"),
    ("transcript.vtt",
     "DEC-022 reads it back to skip a 94-minute transcription"),
    ("highlight_rank_", "the rendered clips on disk are named this"),
    ("thumbnail_rank_", "the thumbnails on disk are named this"),
    ("render_manifest.json", "the uploaders and the dashboard read it"),
])
def test_on_disk_names_are_unchanged(name, why):
    """These look like branding and are not: they name files that already
    exist in job directories. Renaming them orphans real data."""
    found = any(
        name in path.read_text(encoding="utf-8", errors="replace")
        for path in shipped_files({".py"})
    )
    assert found, f"{name} disappeared -- {why}"


def test_the_env_var_the_render_layer_reads_is_unchanged():
    """OSC_VIDEO_SCALE_ALGO is set by the worker and read inside
    clipping/studio/. Renaming it means touching the render layer."""
    worker = (ROOT / "web" / "api" / "worker.py").read_text(encoding="utf-8")
    assert "OSC_VIDEO_SCALE_ALGO" in worker


def test_the_python_package_is_still_called_clipping():
    """Renaming it would rewrite every import in the project and every
    notebook that already exists."""
    assert (ROOT / "clipping" / "__init__.py").is_file()
    assert "from clipping" in (ROOT / "main.py").read_text(encoding="utf-8")


def test_the_cli_entry_point_still_works_by_its_old_name():
    """An existing install has `clipping` on PATH."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'rzclips = "main:main"' in pyproject
    assert 'clipping = "main:main"' in pyproject


# ------------------------------------------------------------------ version

def _declared_version():
    src = (ROOT / "clipping" / "__init__.py").read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "__version__":
            return node.value.value
    raise AssertionError("clipping.__version__ not found")


def test_every_manifest_agrees_on_the_version():
    """It was previously spelled four different ways: pyproject 2.0.0,
    package.json 1.12.0, the FastAPI app 1.12.0, and its own root endpoint
    1.13.0 -- all in the same running program."""
    version = _declared_version()

    pyproject = re.search(
        r'^version = "([^"]+)"',
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M
    ).group(1)
    package = json.loads((ROOT / "web" / "dashboard" / "package.json").read_text())["version"]

    assert pyproject == version
    assert package == version


def test_the_api_reports_the_declared_version_rather_than_a_literal():
    src = (ROOT / "web" / "api" / "app.py").read_text(encoding="utf-8")
    assert "version=__version__" in src
    assert '"version": "1.13.0"' not in src


def test_the_lockfile_names_the_same_package():
    """npm ci refuses to run when package.json and the lockfile disagree."""
    package = json.loads((ROOT / "web" / "dashboard" / "package.json").read_text())
    lock = json.loads((ROOT / "web" / "dashboard" / "package-lock.json").read_text())
    assert lock["name"] == package["name"]
    assert lock["version"] == package["version"]


# ---------------------------------------------------------------- the docs

def test_the_api_doc_covers_every_route():
    """A route nobody documents is a route nobody uses."""
    doc = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")
    for path in (
        "/api/health", "/api/upload", "/api/jobs", "/api/jobs/{id}/status",
        "/api/jobs/{id}/source", "/api/outputs/{id}/{file}", "/api/settings",
    ):
        assert path in doc, path


def test_the_api_doc_names_every_job_status():
    pytest.importorskip("pydantic")
    from web.api.models import JobStatus

    doc = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")
    for status in JobStatus:
        assert status.value in doc, status.value


def test_the_example_client_only_sends_real_fields():
    """An undeclared key is silently dropped by Pydantic -- the same trap the
    'Bypass AI' toggle fell into."""
    pytest.importorskip("pydantic")
    from web.api.models import JobCreateRequest

    src = (ROOT / "examples" / "create_job.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    sent = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    sent.add(key.value)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id == "payload" and isinstance(node.slice, ast.Constant):
                sent.add(node.slice.value)

    declared = set(JobCreateRequest.model_fields)
    unknown = sorted(name for name in sent if name.islower() and "_" in name or name in declared)
    unknown = sorted(set(unknown) - declared)
    assert unknown == [], f"example sends unknown job fields: {unknown}"


def test_the_example_client_is_stdlib_only():
    import sys

    tree = ast.parse((ROOT / "examples" / "create_job.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    assert sorted(imported - set(sys.stdlib_module_names)) == []

"""The notebooks run this fork, with keys that can start a job.

All three cloned the UPSTREAM repository, so a Colab or Kaggle user got the
original project -- no provider chain, no --video/--transcript -- while reading
this fork's README. They also collected only GOOGLE_API_KEY, so even the right
code would have met the chain readiness gate without its first link (Groq).

Quick_Start had its own bugs on top: VIDEO_FILE held a YouTube URL (it is
passed to --video, which takes a local path), TRANSCRIPT_FILE was never
defined, the download cell overwrote the user's URL with a hardcoded one, and
its Whisper settings were never passed to main.py. The Kaggle server declared
NGROK_AUTHTOKEN = "" instead of reading the secret it asks you to add.

Stdlib-only: notebooks are JSON, and these are text checks.
"""

import ast
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOTEBOOKS = sorted((ROOT / "notebooks").glob("*.ipynb"))
FORK_CLONE = "git clone https://github.com/rzdhop/opensource-clipping-better.git"


def _cells(path):
    notebook = json.loads(path.read_text(encoding="utf-8"))
    return [(cell["cell_type"], "".join(cell["source"])) for cell in notebook["cells"]]


def _code(path):
    return [source for kind, source in _cells(path) if kind == "code"]


def _declared_flags():
    config = (ROOT / "clipping" / "config.py").read_text(encoding="utf-8")
    return set(re.findall(r'"(--[a-z0-9][a-z0-9-]*)"', config))


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_the_notebook_clones_this_fork(path):
    clones = [line.strip() for source in _code(path)
              for line in source.splitlines() if "git clone" in line]
    assert clones, "no clone cell"
    assert all(FORK_CLONE in line for line in clones), clones


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_the_notebook_does_not_send_people_upstream(path):
    """Crediting upstream is fine ("a fork of ..."); linking it as *the*
    project, or its GitHub Pages Studio, is not."""
    text = path.read_text(encoding="utf-8")
    assert "naufalrizqullah.github.io" not in text.lower()
    for kind, source in _cells(path):
        for line in source.splitlines():
            if "github.com/NaufalRizqullah/opensource-clipping" in line:
                assert "fork of" in line, line


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_every_main_py_flag_exists(path):
    declared = _declared_flags()
    used = set()
    for source in _code(path):
        if "main.py" in source:
            used |= set(re.findall(r"(?<![\w-])(--[a-z0-9][a-z0-9-]*)", source))
    # yt-dlp's flags live in other cells; only main.py invocations count here.
    assert sorted(used - declared) == []


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_the_notebook_collects_the_chain_keys(path):
    code = "\n".join(_code(path))
    for name in ("GROQ_API_KEY", "GOOGLE_API_KEY", "NVIDIA_API_KEY"):
        assert f'"{name}"' in code, name
    # And says so when neither primary link has a key (DEC-073).
    assert re.search(r"not\s+\(?\s*GROQ_API_KEY\s+or\s+GOOGLE_API_KEY", code)


def test_quick_start_video_file_is_a_local_path():
    code = "\n".join(_code(ROOT / "notebooks" / "Quick_Start.ipynb"))
    video = re.search(r'^VIDEO_FILE\s*=\s*"([^"]*)"', code, re.MULTILINE)
    assert video and not video.group(1).startswith("http")


def test_quick_start_defines_every_input_before_the_run():
    cells = _code(ROOT / "notebooks" / "Quick_Start.ipynb")
    run = next(i for i, source in enumerate(cells) if "main.py" in source)
    before = "\n".join(cells[:run + 1])
    for name in ("VIDEO_FILE", "TRANSCRIPT_FILE", "SOURCE_URL"):
        assert re.search(rf"^{name}\s*=", before, re.MULTILINE), name


def test_quick_start_asks_for_the_url_once():
    """The download cell reassigned SOURCE_URL to a fixed video, silently
    discarding whatever the user typed in the configuration cell."""
    code = "\n".join(_code(ROOT / "notebooks" / "Quick_Start.ipynb"))
    assert len(re.findall(r"^SOURCE_URL\s*=", code, re.MULTILINE)) == 1


def test_quick_start_passes_its_whisper_settings():
    cells = _code(ROOT / "notebooks" / "Quick_Start.ipynb")
    run = next(source for source in cells if "main.py" in source)
    for flag, variable in (("--whisper-model", "WHISPER_MODEL"),
                           ("--whisper-device", "WHISPER_DEVICE"),
                           ("--whisper-compute-type", "WHISPER_COMPUTE_TYPE")):
        assert re.search(rf"{flag}\s+\"?\{{{variable}\}}", run), flag


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_the_download_produces_the_mp4_the_run_expects(path):
    """`-o talk.%(ext)s` with a bv*+ba format merges VP9 sources into webm or
    mkv, so `talk.mp4` would not exist."""
    for source in _code(path):
        if "yt-dlp" in source and "talk.%(ext)s" in source:
            assert "--merge-output-format mp4" in source


DOCS = [ROOT / "README.md", ROOT / "README_ID.md", *sorted((ROOT / "wiki").glob("*.md"))]


def _commands(text):
    """Shell commands with their backslash continuations joined."""
    joined, current = [], ""
    for line in text.splitlines():
        stripped = line.strip().lstrip("#!").strip()
        current = f"{current} {stripped}" if current else stripped
        if not stripped.endswith("\\"):
            joined.append(current)
            current = ""
    return joined


def test_the_docs_download_the_mp4_they_then_use():
    """The same `talk.%(ext)s` recipe, copied into the README and wiki."""
    offenders = [
        f"{path.relative_to(ROOT)}: {command[:70]}"
        for path in DOCS
        for command in _commands(path.read_text(encoding="utf-8"))
        if command.startswith("yt-dlp") and "talk.%(ext)s" in command
        and "--merge-output-format mp4" not in command
    ]
    assert offenders == []


def test_the_readme_colab_recipe_writes_a_primary_key():
    """README's Colab cell wrote NVIDIA_API_KEY alone -- the one key the chain
    readiness gate refuses to start a job with."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    recipe = text[text.index("**Cell 2: Setup API Keys**"):text.index("**Cell 3:")]
    assert "GROQ_API_KEY" in recipe and "GOOGLE_API_KEY" in recipe


def _unguarded_secret_reads(source):
    """`userdata.get` calls that are not inside a `try`. Colab raises
    SecretNotFoundError for a secret that is not set -- it does not return
    None -- so asking for an optional key unguarded crashes the cell for
    everyone who did not set it."""
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith(("!", "%")))
    tree = ast.parse(code)
    guarded = {id(node) for t in ast.walk(tree) if isinstance(t, ast.Try)
               for stmt in t.body for node in ast.walk(stmt)}
    return [node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
            and getattr(node.func.value, "id", None) == "userdata"
            and id(node) not in guarded]


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_an_unset_optional_secret_does_not_crash_the_notebook(path):
    for source in _code(path):
        if "userdata.get" in source:
            assert _unguarded_secret_reads(source) == []


def test_the_readme_colab_recipe_survives_an_unset_secret():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    recipe = text[text.index("**Cell 2: Setup API Keys**"):text.index("**Cell 3:")]
    code = recipe.split("```python", 1)[1].split("```", 1)[0]
    assert _unguarded_secret_reads(code) == []


def test_the_kaggle_server_reads_the_ngrok_secret():
    code = "\n".join(_code(ROOT / "notebooks" / "kaggle-studio-server.ipynb"))
    assert re.search(r'NGROK_AUTHTOKEN\s*=\s*get_secret\("NGROK_AUTHTOKEN"\)', code)
    assert not re.search(r'NGROK_AUTHTOKEN\s*=\s*""', code)

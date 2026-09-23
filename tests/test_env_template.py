"""There is one environment template, and it is .env.example.

A second template, .env.sample, survived from before the provider chain and
said NVIDIA_API_KEY was "[REQUIRED]" and "the DEFAULT AI provider". The chain
readiness gate refuses a job whose only key is NVIDIA's, so a new install that
followed the wiki (`cp .env.sample .env`) booted, accepted an upload and then
refused every job.

Stdlib only: these are text checks, so they run in the pytest-only CI job.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".env.example"

# Everything a user reads when setting the project up.
SHIPPED_DOCS = [
    *ROOT.glob("*.md"),
    *(ROOT / "wiki").glob("*.md"),
    *(ROOT / "docs").rglob("*.md"),
    *(ROOT / "docs").rglob("*.html"),
    *(ROOT / "notebooks").glob("*.ipynb"),
    ROOT / "Dockerfile",
    *ROOT.glob("docker-compose*.yml"),
]


def test_the_stale_template_is_gone():
    assert not (ROOT / ".env.sample").exists()


def test_no_shipped_doc_points_at_the_stale_template():
    offenders = [
        str(path.relative_to(ROOT))
        for path in SHIPPED_DOCS
        if path.is_file() and ".env.sample" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_the_facebook_uploader_settings_moved_to_the_one_template():
    """These four lived only in .env.sample. Deleting it must not lose them."""
    text = TEMPLATE.read_text(encoding="utf-8")
    for name in ("META_PAGE_ID", "META_PAGE_ACCESS_TOKEN",
                 "META_GRAPH_VERSION", "APP_TIMEZONE"):
        assert re.search(rf"^#?\s*{name}=", text, re.MULTILINE), name


def test_settings_with_a_code_default_are_not_shipped_empty():
    """`os.environ.get(NAME, default)` returns "" for `NAME=` in a loaded .env,
    so an empty line would override the default with nothing. These two have
    defaults in code, so the template may only mention them commented out."""
    text = TEMPLATE.read_text(encoding="utf-8")
    for name in ("META_GRAPH_VERSION", "APP_TIMEZONE"):
        assert not re.search(rf"^{name}=\s*$", text, re.MULTILINE), name


def test_the_getting_started_guide_names_a_primary_key():
    """The guide used to tell a new user to set GOOGLE_API_KEY and call NVIDIA
    optional, without saying the chain needs Groq or Gemini."""
    guide = (ROOT / "wiki" / "2-Getting-Started.md").read_text(encoding="utf-8")
    assert "cp .env.example .env" in guide
    assert "GROQ_API_KEY" in guide

"""There is one Studio: the dashboard the API serves.

docs/studio/ was a static GitHub Pages copy of the UI, connected to a notebook
backend over a tunnel. It had fallen far behind: a two-provider select, a
source-platform picker, the dead YouTube-subtitles toggle, and fields the
backend has since deleted -- and no API token, so against the current backend
every request it made was refused. The README still sent people to it, at a
Pages URL on upstream's account.

Stdlib-only text checks.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

DOCS = [
    ROOT / "README.md", ROOT / "README_ID.md",
    *sorted((ROOT / "wiki").glob("*.md")),
    *sorted((ROOT / "docs").glob("*.md")),
    *sorted((ROOT / "docs").glob("*.html")),
]


def test_the_static_studio_is_gone():
    assert not (ROOT / "docs" / "studio").exists()


def test_nothing_links_to_it():
    pattern = re.compile(r"studio/(index|new-job|job|settings)(\.html)?\b|github\.io/[^)\s\"']*/studio")
    offenders = [
        f"{path.relative_to(ROOT)}:{number}"
        for path in DOCS
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if pattern.search(line)
    ]
    assert offenders == []


def test_the_readme_describes_the_dashboard_the_api_serves():
    for name in ("README.md", "README_ID.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "http://localhost:8000/" in text, name
        assert "data/api_token" in text, name
        assert "docs/deploy-tailscale.md" in text, name


def test_every_notebook_the_docs_name_exists():
    """The Studio section named `notebooks/Kaggle_Studio_Server.ipynb`; the file
    is `kaggle-studio-server.ipynb`."""
    missing = sorted({
        f"{path.relative_to(ROOT)}: {name}"
        for path in DOCS
        for name in re.findall(r"notebooks/([\w.-]+\.ipynb)", path.read_text(encoding="utf-8"))
        if not (ROOT / "notebooks" / name).is_file()
    })
    assert missing == []

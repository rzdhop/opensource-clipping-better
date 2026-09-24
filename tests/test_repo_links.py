"""The docs send people to this fork, and their clone commands work.

The README banner's "Report Bug" / "Request Feature" links opened issues on the
upstream project, the wiki's clone commands fetched upstream's code, the README
cloned a placeholder `your-username/rzdhop-clips` that does not exist, and
wiki/2 cloned `opensource-clipping` and then `cd`-ed into `rzdhop-clips`.

Upstream is still credited: the README's "About this fork" line links it, and
that line is the one place its URL may appear.

Stdlib-only text checks.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
FORK = "https://github.com/rzdhop/opensource-clipping-better"
UPSTREAM = "github.com/NaufalRizqullah/opensource-clipping"
ATTRIBUTION = re.compile(r"\[NaufalRizqullah/opensource-clipping\]\(https://" + re.escape(UPSTREAM) + r"\)")

DOCS = [ROOT / "README.md", ROOT / "README_ID.md", *sorted((ROOT / "wiki").glob("*.md"))]


def _lines():
    for path in DOCS:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            yield path.relative_to(ROOT), number, line


def test_upstream_is_linked_only_to_credit_it():
    stray = [f"{path}:{number}" for path, number, line in _lines()
             if UPSTREAM in line and not ATTRIBUTION.search(line)]
    assert stray == []


def test_the_readme_still_credits_upstream():
    assert ATTRIBUTION.search((ROOT / "README.md").read_text(encoding="utf-8"))


def test_the_banners_file_issues_on_the_fork():
    for name in ("README.md", "README_ID.md"):
        head = "\n".join((ROOT / name).read_text(encoding="utf-8").splitlines()[:25])
        assert head.count(f"{FORK}/issues/new") == 2, name


def test_no_doc_clones_a_placeholder_repository():
    placeholders = [f"{path}:{number}" for path, number, line in _lines()
                    if "your-username/rzdhop-clips" in line.lower()]
    assert placeholders == []


def test_every_clone_is_followed_by_a_cd_into_what_it_created():
    """`git clone URL [DIR]` creates DIR, or the URL's last segment."""
    mismatches = []
    for path in DOCS:
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            match = re.search(r"git clone (\S+)(?: (\S+))?\s*$", line)
            if not match:
                continue
            url, target = match.groups()
            created = target or url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
            following = next((l.strip() for l in lines[index + 1:] if l.strip()), "")
            if following.startswith("cd ") and following[3:].strip() != created:
                mismatches.append(f"{path.relative_to(ROOT)}:{index + 1} creates "
                                  f"{created!r} but then runs {following!r}")
    assert mismatches == []

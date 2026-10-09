"""The rzdhop-story skill (plan 36): check it and zip it for the claude.ai account.

    python3 showrunner/tools/build_skills.py --check              # exit 1 if a step file or a link is missing
    python3 showrunner/tools/build_skills.py --zip outputs/skills # outputs/skills/rzdhop-story.zip

One skill, one folder (``.claude/skills/rzdhop-story/``, the source of truth, loaded by Claude Code on this host and
uploaded to the claude.ai account, Settings -> Capabilities -> Skills): ``SKILL.md`` (the rules, Rida's gates, the
routing, what he approved for fruit), ``steps/*.md`` (one file per step, read before doing that step) and
``PROMPTS.md`` (the prompt patterns that worked; Claude writes every prompt). Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NAME = "rzdhop-story"
SKILL_DIR = os.path.join(ROOT, ".claude", "skills", NAME)

# The order Rida goes through (plan 36 §2.2); each step file's "## Next" names the one after it.
STEPS = ("1-concepts", "2-universe", "3-cast", "4-script", "5a-shots", "5b-clips", "6-assemble", "7-next-episode")


def path(rel: str) -> str:
    return os.path.join(SKILL_DIR, rel)


def read(rel: str) -> str:
    with open(path(rel), encoding="utf-8") as fh:
        return fh.read()


def step_file(step: str) -> str:
    return f"steps/{step}.md"


def files() -> list:
    """Every file of the skill, relative to its folder, in a stable order."""
    out = []
    for dirpath, _, names in os.walk(SKILL_DIR):
        for n in sorted(names):
            out.append(os.path.relpath(os.path.join(dirpath, n), SKILL_DIR))
    return sorted(out)


def problems() -> list:
    """What is wrong with the skill: a missing step file, or a link to a file of the skill that does not exist."""
    found = []
    for rel in ("SKILL.md", "PROMPTS.md", *(step_file(s) for s in STEPS)):
        if not os.path.exists(path(rel)):
            found.append(f"missing {rel}")
    for rel in files():
        if not rel.endswith(".md"):
            continue
        for link in re.findall(r"`((?:steps/[\w.-]+|PROMPTS)\.md)`", read(rel)):
            if not os.path.exists(path(link)):
                found.append(f"{rel} links to a missing {link}")
    return found


def zip_skill(out_dir: str) -> str:
    """``<out_dir>/rzdhop-story.zip`` with ``rzdhop-story/<file>`` inside (the claude.ai upload format)."""
    os.makedirs(out_dir, exist_ok=True)
    dest = os.path.join(out_dir, f"{NAME}.zip")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files():
            zf.write(path(rel), f"{NAME}/{rel}")
    return dest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report a missing step file or a broken link")
    ap.add_argument("--zip", metavar="DIR", help="write the skill's zip into DIR")
    args = ap.parse_args()
    found = problems()
    if found:
        print("\n".join(found))
        return 1
    print(f"{NAME}: {len(files())} files, ok")
    if args.zip:
        print(zip_skill(args.zip))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The story skills (plan 36 stage 3): copy the shared rules into each SKILL.md, check them, zip them.

    python3 showrunner/tools/build_skills.py            # write the rules block into every skill
    python3 showrunner/tools/build_skills.py --check    # exit 1 if a skill's rules block is out of date
    python3 showrunner/tools/build_skills.py --zip outputs/skills   # one <name>.zip per skill, for claude.ai

The skills live in the repo (``.claude/skills/story-*/SKILL.md``, the source of truth, loaded by Claude Code on
this host) and are uploaded to the claude.ai account (Settings -> Capabilities -> Skills) as the zips, where
Rida chats with the showrunner connector. The rules are written once in ``showrunner/skills/RULES.md`` and copied
between the ``<!-- rules:start -->`` / ``<!-- rules:end -->`` markers: a skill uploaded alone must carry them. The
prompt guide (``showrunner/skills/PROMPTS.md``: the patterns that worked; Claude writes every prompt) goes the same
way between ``<!-- prompts:start -->`` / ``<!-- prompts:end -->`` into the steps that send GPU jobs.
Stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKILLS_DIR = os.path.join(ROOT, ".claude", "skills")
RULES_PATH = os.path.join(ROOT, "showrunner", "skills", "RULES.md")
PROMPTS_PATH = os.path.join(ROOT, "showrunner", "skills", "PROMPTS.md")
START, END = "<!-- rules:start -->", "<!-- rules:end -->"
BLOCK = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
P_START, P_END = "<!-- prompts:start -->", "<!-- prompts:end -->"
P_BLOCK = re.compile(re.escape(P_START) + r".*?" + re.escape(P_END), re.S)

# The steps that send GPU jobs carry the prompt guide (you write every prompt; the server writes none).
PROMPT_SKILLS = ("story-cast", "story-shots", "story-clips")

# The order Rida goes through (plan 36 §2.2): each skill's "## Next" names the one after it.
ORDER = ("story-concepts", "story-universe", "story-cast", "story-script", "story-shots", "story-clips",
         "story-assemble", "story-next-episode")
# The entry points Rida calls by name (they replace the old account skills of the same names): they ask, find
# where the story stands and load the step skill; they carry the rules, no prompt guide.
ENTRY = ("story-director", "fruit-drama-episode")
ALL = ENTRY + ORDER


def rules() -> str:
    with open(RULES_PATH, encoding="utf-8") as fh:
        return fh.read().strip()


def prompt_guide() -> str:
    with open(PROMPTS_PATH, encoding="utf-8") as fh:
        return fh.read().strip()


def skill_path(name: str) -> str:
    return os.path.join(SKILLS_DIR, name, "SKILL.md")


def with_rules(text: str, block: str) -> str:
    """*text* with its rules block replaced by *block* (the markers are required, exactly once)."""
    if len(BLOCK.findall(text)) != 1:
        raise ValueError(f"a skill needs exactly one {START} ... {END} block")
    return BLOCK.sub(lambda _: f"{START}\n{block}\n{END}", text)


def with_guide(name: str, text: str, guide: str) -> str:
    """*text* with its prompt-guide block filled (the steps of PROMPT_SKILLS only, exactly once)."""
    found = len(P_BLOCK.findall(text))
    if name not in PROMPT_SKILLS:
        if found:
            raise ValueError(f"{name} sends no GPU job: no {P_START} block")
        return text
    if found != 1:
        raise ValueError(f"{name} needs exactly one {P_START} ... {P_END} block")
    return P_BLOCK.sub(lambda _: f"{P_START}\n{guide}\n{P_END}", text)


def expected(name: str, text: str) -> str:
    return with_guide(name, with_rules(text, rules()), prompt_guide())


def build(check: bool = False) -> list:
    """Write (or with *check*, only compare) every skill's rules block; returns the skills out of date."""
    stale = []
    for name in ALL:
        path = skill_path(name)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        new = expected(name, text)
        if new != text:
            stale.append(name)
            if not check:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(new)
    return stale


def zip_all(out_dir: str) -> list:
    """``<out_dir>/<name>.zip`` with ``<name>/SKILL.md`` inside (the claude.ai upload format)."""
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for name in ALL:
        dest = os.path.join(out_dir, f"{name}.zip")
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(skill_path(name), f"{name}/SKILL.md")
        made.append(dest)
    return made


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="only report the skills whose rules block is stale")
    ap.add_argument("--zip", metavar="DIR", help="also write one zip per skill into DIR")
    args = ap.parse_args()
    stale = build(check=args.check)
    if args.check:
        if stale:
            print("stale shared block: " + ", ".join(stale))
            return 1
        print(f"{len(ALL)} skills up to date")
    else:
        print(f"rules written into: {', '.join(stale) or 'nothing (all up to date)'}")
    if args.zip:
        for path in zip_all(args.zip):
            print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

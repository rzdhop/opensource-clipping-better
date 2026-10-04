#!/usr/bin/env python3
"""tools/render_golden.py -- render the AI-Story golden fixture and print its
parity key and framemd5 digest (spec 13; DEC-156).

    python3 tools/render_golden.py                # render, compare, print
    python3 tools/render_golden.py --record       # ... and record this key
    python3 tools/render_golden.py --workdir DIR  # keep the render in DIR
    python3 tools/render_golden.py --tier2 ...    # the tier-2 fixture instead
    python3 tools/render_golden.py --aspect 16:9  # the fixture in another frame

The fixture is ``clipping/aistory/render/golden.py``'s, the same one
``tests/test_aistory_render_golden.py`` renders. Look at the frames
(``DIR/episode_final.mp4``) before recording a new key: ``--record`` adds or
replaces this machine's key in ``tests/fixtures/aistory_golden/framemd5.json``
and keeps every other key. ``--tier2`` renders
``clipping/aistory/render/golden_tier2.py``'s fixture (a shot cut from its
own clip; ``tests/test_aistory_render_golden_tier2.py``) against
``tests/fixtures/aistory_golden_tier2/framemd5.json``. ``--aspect 16:9`` or
``--aspect 1:1`` renders the tier-1 fixture in that frame against its own
keys file (``framemd5_16x9.json`` / ``framemd5_1x1.json`` beside
``framemd5.json``; plan 23 stage B6); the tier-2 fixture is 9:16 only.

Exit status: 0 the digest matches (or was recorded), 1 the render failed,
2 the digest differs from the recorded one, 3 no digest is recorded for this
key. Stdlib only; needs ffmpeg and ffprobe on PATH, not pytest.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from clipping.aistory.render import golden, golden_tier2  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render the AI-Story golden fixture and check its frames.")
    parser.add_argument("--record", action="store_true", help="record this key's digest in the keys file")
    parser.add_argument("--workdir", help="render here and keep the files (default: a temporary folder)")
    parser.add_argument("--tier2", action="store_true", help="the tier-2 fixture (a shot cut from its own clip)")
    parser.add_argument("--keys", help="the framemd5 keys file (default: the fixture's own)")
    parser.add_argument("--aspect", choices=golden.ASPECTS, default="9:16",
                        help="the frame of the tier-1 fixture (default 9:16)")
    args = parser.parse_args(argv)
    if args.tier2 and args.aspect != "9:16":
        parser.error("the tier-2 fixture is 9:16 only")
    if args.tier2:
        keys_path = args.keys or str(golden_tier2.KEYS_PATH)
        command = golden_tier2.RECORD_COMMAND
    else:
        keys_path = args.keys or str(golden.keys_path(args.aspect))
        command = golden.record_command(args.aspect)

    workdir = args.workdir or tempfile.mkdtemp(prefix="aistory-golden-")
    if args.tier2:
        result = golden_tier2.render_fixture(workdir)
    else:
        result = golden.render_fixture(workdir, aspect=args.aspect)
    if result["state"] != "completed":
        print(f"render {result['state']}: {result['error']}", file=sys.stderr)
        manifest = result.get("manifest") or {}
        for stage in manifest.get("stages", []):
            if stage.get("stderr_tail"):
                print(f"--- {stage['id']}\n{stage['stderr_tail']}", file=sys.stderr)
        return 1

    key = result["key"]
    digest = result["output"]["framemd5"]["sha256"]
    print(f"key:     {key}")
    print(f"digest:  {digest}")
    print(f"seconds: {result['seconds']}")
    print(f"frames:  {os.path.join(workdir, result['output']['framemd5']['file'])}")
    print(f"video:   {os.path.join(workdir, result['output']['path'])}")

    if args.record:
        golden.record_key(key, digest, keys_path)
        print(f"recorded {key} in {keys_path}")
        return 0
    keys = golden.load_keys(keys_path)
    problem = golden.parity_problem(key, digest, keys, command=command)
    if problem is None:
        print("status:  matches the recorded digest")
        return 0
    print(f"status:  {problem}")
    return 3 if key not in keys else 2


if __name__ == "__main__":
    sys.exit(main())

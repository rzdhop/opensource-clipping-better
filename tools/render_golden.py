#!/usr/bin/env python3
"""tools/render_golden.py -- render the AI-Story golden fixture and print its
parity key and framemd5 digest (spec 13; DEC-156).

    python3 tools/render_golden.py                # render, compare, print
    python3 tools/render_golden.py --record       # ... and record this key
    python3 tools/render_golden.py --workdir DIR  # keep the render in DIR

The fixture is ``clipping/aistory/render/golden.py``'s, the same one
``tests/test_aistory_render_golden.py`` renders. Look at the frames
(``DIR/episode_final.mp4``) before recording a new key: ``--record`` adds or
replaces this machine's key in ``tests/fixtures/aistory_golden/framemd5.json``
and keeps every other key.

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

from clipping.aistory.render import golden  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render the AI-Story golden fixture and check its frames.")
    parser.add_argument("--record", action="store_true", help="record this key's digest in the keys file")
    parser.add_argument("--workdir", help="render here and keep the files (default: a temporary folder)")
    parser.add_argument("--keys", default=str(golden.KEYS_PATH), help="the framemd5 keys file")
    args = parser.parse_args(argv)

    workdir = args.workdir or tempfile.mkdtemp(prefix="aistory-golden-")
    result = golden.render_fixture(workdir)
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
        golden.record_key(key, digest, args.keys)
        print(f"recorded {key} in {args.keys}")
        return 0
    keys = golden.load_keys(args.keys)
    problem = golden.parity_problem(key, digest, keys)
    if problem is None:
        print("status:  matches the recorded digest")
        return 0
    print(f"status:  {problem}")
    return 3 if key not in keys else 2


if __name__ == "__main__":
    sys.exit(main())

"""The clip render layer is frozen while AI Story grows its own renderer
(RC-A1; plan phase 4 stage 7, "Clip-layer guard"; DEC-156).

``tests/fixtures/render_layer_sha256.json`` holds the sha256 of every file
under ``clipping/studio/`` and ``clipping/story/`` (``__pycache__`` left
out), taken at ``1367d75``. This test compares the tree on disk with it and
names every added, removed or changed file. Unlike ``git diff 1367d75``, it
works on CI's shallow checkout.

A change to the clip layer made on purpose re-records the manifest in the
same commit: ``python3 tests/test_render_layer_guard.py --record``.

Stdlib + pytest only (DEC-012).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "fixtures" / "render_layer_sha256.json"
ROOTS = ("clipping/studio", "clipping/story")
SKIPPED_DIRS = ("__pycache__",)


def tree_hashes(repo_root, roots=ROOTS) -> dict:
    """``{repo-relative path: sha256}`` of every file under *roots*."""
    repo_root = Path(repo_root)
    found = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(repo_root / root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIRS)
            for name in sorted(filenames):
                path = Path(dirpath) / name
                rel = path.relative_to(repo_root).as_posix()
                found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def compare(recorded: dict, current: dict) -> dict:
    """``{"added", "removed", "changed"}``: sorted repo-relative paths."""
    return {
        "added": sorted(set(current) - set(recorded)),
        "removed": sorted(set(recorded) - set(current)),
        "changed": sorted(p for p in set(recorded) & set(current) if recorded[p] != current[p]),
    }


def _recorded() -> dict:
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert doc["$schema"] == "render_layer_sha256_v1" and tuple(doc["roots"]) == ROOTS
    return doc["files"]


def _message(diff: dict) -> str:
    parts = [f"{kind}: {', '.join(paths)}" for kind, paths in diff.items() if paths]
    return ("The clip render layer changed (RC-A1): " + "; ".join(parts)
            + ". If that is intended, re-record with `python3 tests/test_render_layer_guard.py --record`.")


def test_the_clip_render_layer_is_unchanged():
    diff = compare(_recorded(), tree_hashes(ROOT))
    assert diff == {"added": [], "removed": [], "changed": []}, _message(diff)


def test_the_manifest_covers_both_folders():
    recorded = _recorded()
    assert len(recorded) >= 20
    for root in ROOTS:
        assert any(path.startswith(root + "/") for path in recorded), root
    assert all("__pycache__" not in path for path in recorded)


def test_a_one_byte_change_in_a_scratch_copy_is_named(tmp_path):
    for root in ROOTS:
        shutil.copytree(ROOT / root, tmp_path / root, ignore=shutil.ignore_patterns(*SKIPPED_DIRS))
    recorded = _recorded()
    assert compare(recorded, tree_hashes(tmp_path)) == {"added": [], "removed": [], "changed": []}

    target = tmp_path / sorted(recorded)[0]
    data = bytearray(target.read_bytes())
    data[len(data) // 2] ^= 0x01
    target.write_bytes(bytes(data))
    (tmp_path / "clipping" / "story" / "new_module.py").write_text("x = 1\n")
    removed = sorted(recorded)[-1]
    (tmp_path / removed).unlink()
    (tmp_path / "clipping" / "studio" / "__pycache__").mkdir(exist_ok=True)
    (tmp_path / "clipping" / "studio" / "__pycache__" / "x.pyc").write_bytes(b"ignored")

    diff = compare(recorded, tree_hashes(tmp_path))
    assert diff == {"added": ["clipping/story/new_module.py"], "removed": [removed],
                    "changed": [sorted(recorded)[0]]}
    message = _message(diff)
    assert sorted(recorded)[0] in message and removed in message and "new_module.py" in message


if __name__ == "__main__":
    if sys.argv[1:] != ["--record"]:
        sys.exit("usage: python3 tests/test_render_layer_guard.py --record")
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    doc["files"] = tree_hashes(ROOT)
    MANIFEST.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(f"recorded {len(doc['files'])} files in {MANIFEST.relative_to(ROOT)}")

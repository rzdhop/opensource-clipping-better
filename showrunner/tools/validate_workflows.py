"""Validate the showrunner workflow templates against a ComfyUI source tree.

Checks, for every node of every template: the class exists in that ComfyUI version (v1
``NODE_CLASS_MAPPINGS`` or v3 ``node_id=``), every input name the template sets exists on the
node's schema (dotted dynamic-combo keys resolved to their parent), every required input is set,
every link points at an existing node, and the placeholders render. Custom-node classes
(``custom_nodes`` of the template) are checked against an optional second tree.

Usage:
    python showrunner/tools/validate_workflows.py --comfyui /path/to/ComfyUI [--custom /path/to/node_pack ...]

Exit code 1 on any problem. Pure read-only: no ComfyUI import, no GPU.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from showrunner import comfy_templates  # noqa: E402

# Placeholders of a render for the check (any plausible values).
SAMPLE = {"image": "kf.png", "audio": "line.wav", "voice_ref": "voice.wav", "prompt": "p", "negative": "n",
          "seed": 1, "width": 704, "height": 1280, "seconds": 5, "fps": 24, "name": "t", "sampler": "euler",
          "identity_guidance": 3.0, "text": "t", "language": "French (fr)", "exaggeration": 0.5,
          "input": "clip.wav", "target_voice": "voice.wav", "ref1": "a.png", "ref2": "b.png", "ref3": "c.png",
          "ref4": "d.png"}


def _py_files(root: str) -> list:
    return [p for p in glob.glob(os.path.join(root, "**", "*.py"), recursive=True)
            if "/tests/" not in p and "/web/" not in p]


def _find_class_source(files: list, name: str):
    """(file, class body) of the node class whose v3 ``node_id`` or v1 mapping key is *name*."""
    v3 = re.compile(rf'node_id\s*=\s*"{re.escape(name)}"')
    for path in files:
        try:
            src = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        if v3.search(src):
            body = _class_body_containing(src, v3.search(src).start())
            if body:
                return path, body
        m = re.search(rf'"{re.escape(name)}"\s*:\s*([A-Za-z_][A-Za-z0-9_]*)', src)
        if m and "NODE_CLASS_MAPPINGS" in src:
            cls = m.group(1)
            body = _class_body_named(src, cls)
            if body:
                return path, body
            # the class may live in a sibling module (nodes.py style)
            for other in files:
                if other == path:
                    continue
                try:
                    osrc = open(other, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                body = _class_body_named(osrc, cls)
                if body:
                    return other, body
    return None, None


def _class_body_named(src: str, cls: str):
    m = re.search(rf"^class {re.escape(cls)}\b.*?(?=^class |\Z)", src, re.S | re.M)
    return m.group(0) if m else None


def _class_body_containing(src: str, pos: int):
    starts = [m.start() for m in re.finditer(r"^class ", src, re.M) if m.start() <= pos]
    if not starts:
        return None
    start = starts[-1]
    nxt = re.search(r"^class ", src[pos:], re.M)
    end = pos + nxt.start() if nxt else len(src)
    return src[start:end]


def _inputs_of(body: str) -> tuple:
    """(set of input names, set of required input names) of a node class body, best effort."""
    names, required = set(), set()
    # v3: io.X.Input("name", ...) / io.X.Input(id="name", ...), optional=True marks optional
    for m in re.finditer(r'\bInput\(\s*(?:id\s*=\s*)?"([a-zA-Z_][a-zA-Z0-9_]*)"([^\n]*)', body):
        names.add(m.group(1))
        tail = body[m.end(): m.end() + 400]
        if "optional=True" not in m.group(2) and "optional=True" not in tail.split("Input(")[0]:
            required.add(m.group(1))
    # v1: INPUT_TYPES dict -- every input spec is a tuple: "name": (TYPE, {...})
    m = re.search(r"def INPUT_TYPES.*?return\s*\{", body, re.S)
    if m:
        block = _balanced(body, m.end() - 1)
        for section, is_req in (("required", True), ("optional", False)):
            s = re.search(rf'"{section}"\s*:\s*\{{', block)
            if not s:
                continue
            inner = _balanced(block, s.end() - 1)
            for k in re.findall(r'"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:\s*\(', inner):
                names.add(k)
                if is_req:
                    required.add(k)
    return names, required


def _balanced(src: str, open_pos: int) -> str:
    """The text from the brace at *open_pos* to its matching close brace (strings ignored naively)."""
    depth = 0
    for i in range(open_pos, len(src)):
        c = src[i]
        if c in "{([":
            depth += 1
        elif c in "})]":
            depth -= 1
            if depth == 0:
                return src[open_pos:i + 1]
    return src[open_pos:]


def validate(template: dict, core_files: list, custom_files: list) -> list:
    problems = []
    name = template["name"]
    graph = template["graph"]
    try:
        rendered = comfy_templates.render(template, SAMPLE)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"{name}: render failed: {exc}")
        rendered = graph
    files = core_files + custom_files
    for node_id, node in graph.items():
        cls = node["class_type"]
        path, body = _find_class_source(files, cls)
        if body is None:
            problems.append(f"{name}/{node_id}: class {cls} not found in ComfyUI source")
            continue
        names, required = _inputs_of(body)
        if not names:
            problems.append(f"{name}/{node_id}: could not parse inputs of {cls} ({os.path.basename(path)}) -- check by hand")
            continue
        set_keys = set()
        for key, value in node["inputs"].items():
            base = key.split(".")[0]
            set_keys.add(base)
            if base not in names:
                problems.append(f"{name}/{node_id}: {cls} has no input '{key}' (has {sorted(names)})")
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                if value[0] not in graph:
                    problems.append(f"{name}/{node_id}: input '{key}' links to missing node '{value[0]}'")
        for req in required - set_keys:
            if req in ("prompt", "extra_pnginfo", "unique_id"):
                continue
            problems.append(f"{name}/{node_id}: {cls} required input '{req}' not set")
        # placeholders left unrendered
        for key, value in rendered[node_id]["inputs"].items():
            if isinstance(value, str) and "{{" in value:
                problems.append(f"{name}/{node_id}: '{key}' still has a placeholder after render: {value}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--comfyui", required=True, help="path of a ComfyUI checkout (the worker's version)")
    ap.add_argument("--custom", action="append", default=[], help="path of a custom node pack checkout")
    ap.add_argument("--only", help="one template name")
    args = ap.parse_args()
    core_files = _py_files(os.path.join(args.comfyui, "comfy_extras")) + [os.path.join(args.comfyui, "nodes.py")]
    core_files += _py_files(os.path.join(args.comfyui, "comfy_api"))
    custom_files = [f for c in args.custom for f in _py_files(c)]
    total = 0
    for path in sorted(glob.glob(os.path.join(comfy_templates.WORKFLOWS_DIR, "*.json"))):
        template = comfy_templates.load_template(os.path.basename(path)[:-5])
        if args.only and template["name"] != args.only:
            continue
        problems = validate(template, core_files, custom_files)
        total += len(problems)
        print(f"{template['name']}: {len(template['graph'])} nodes, {len(problems)} problem(s)")
        for p in problems:
            print("  -", p)
    sys.exit(1 if total else 0)


if __name__ == "__main__":
    main()

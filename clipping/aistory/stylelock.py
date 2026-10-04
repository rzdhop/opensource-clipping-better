"""Build and evolve a ``style_lock_v1`` document from a style template plus
user overrides (spec 2.2).

A StyleLock is a frozen, fully-expanded copy of a chosen style template: it
carries the same prose fields (``rendering``, ``camera``, ``lighting``, ...)
so it can be handed straight to ``prompting.py``, plus a story-specific
``overrides`` record and a lock/update timestamp pair. Once ``locked_at`` is
set it stops accepting further edits, so a later template revision never
silently changes an in-flight story.

Every function here is pure and takes its timestamp as an explicit ``now``
keyword rather than reading the clock, so the same inputs always produce the
same output and the module is trivially testable.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import re

from . import schemas, templates

# ------------------------------------------------------------------ errors


class StyleLockError(ValueError):
    def __init__(self, name, errors):
        self.name = name
        self.errors = list(errors)
        super().__init__(f"{name}: " + "; ".join(self.errors))

    def __str__(self) -> str:
        lines = "\n".join(f"  - {e}" for e in self.errors)
        return f"{self.name} failed validation:\n{lines}"


# ------------------------------------------------------- override validators

def _is_hex(value) -> bool:
    return isinstance(value, str) and re.match(schemas.HEX_COLOUR, value) is not None


def _validate_hex_list(value, path, min_items=1, max_items=6) -> list:
    if not isinstance(value, list):
        return [f"{path}: expected a list of hex colours, got {type(value).__name__}"]
    errors = []
    if not (min_items <= len(value) <= max_items):
        errors.append(f"{path}: expected {min_items}-{max_items} items, got {len(value)}")
    for i, item in enumerate(value):
        if not _is_hex(item):
            errors.append(f"{path}[{i}]: {item!r} is not a #RRGGBB hex colour")
    return errors


def _validate_string_list(value, path) -> list:
    if not isinstance(value, list):
        return [f"{path}: expected a list of strings, got {type(value).__name__}"]
    errors = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(f"{path}[{i}]: {item!r} is not a string")
    return errors


def _validate_nonempty_str(max_length):
    def _validator(value, path) -> list:
        if not isinstance(value, str):
            return [f"{path}: expected a string, got {type(value).__name__}"]
        if len(value) < 1:
            return [f"{path}: must not be empty"]
        if len(value) > max_length:
            return [f"{path}: length {len(value)} > maxLength {max_length}"]
        return []
    return _validator


def _validate_hex(value, path) -> list:
    if not _is_hex(value):
        return [f"{path}: {value!r} is not a #RRGGBB hex colour"]
    return []


def _validate_bool(value, path) -> list:
    if not isinstance(value, bool):
        return [f"{path}: expected a boolean, got {type(value).__name__}"]
    return []


def _validate_enum(allowed):
    allowed = tuple(allowed)

    def _validator(value, path) -> list:
        if value not in allowed:
            return [f"{path}: {value!r} is not one of {list(allowed)}"]
        return []
    return _validator


# Dotted path -> validator(value, path) -> list of error strings.
# This is the full phase-1 allow-list; any other path is rejected.
OVERRIDABLE = {
    "palette.primary": lambda v, p: _validate_hex_list(v, p, 1, 6),
    "palette.accents": lambda v, p: _validate_hex_list(v, p, 1, 6),
    "palette.forbidden": _validate_string_list,
    "palette.palette_line": _validate_nonempty_str(200),
    "typography.font_family": _validate_nonempty_str(60),
    "typography.highlight_colour": _validate_hex,
    "typography.subtitle_mode": _validate_enum(schemas.SUBTITLE_MODES),
    "typography.ai_label": _validate_bool,
    "episode_defaults.hook_style": _validate_enum(schemas.HOOK_STYLES),
    "episode_defaults.cliffhanger_style": _validate_enum(schemas.CLIFFHANGER_STYLES),
}


def _set_path(doc: dict, dotted_path: str, value) -> None:
    """Set ``value`` at a dotted path known to be exactly two segments deep
    (every entry in OVERRIDABLE is), e.g. ``"palette.accents"``.
    """
    head, _, tail = dotted_path.partition(".")
    doc[head][tail] = value


# --------------------------------------------------------------- building

def build_style_lock(template: dict, overrides: dict | None = None, *, now: str) -> dict:
    """Turn a validated style template into a ``style_lock_v1`` document.

    Pure: ``template`` is never mutated (everything is built on a deep
    copy), and the result depends only on ``template``, ``overrides`` and
    ``now``. Every override is validated before any is applied, so a
    document is never left half-built — either every override is valid and
    the lock is returned, or ``StyleLockError`` lists every problem found
    and nothing changes.
    """
    overrides = dict(overrides) if overrides else {}

    lock = copy.deepcopy(template)
    name = lock.pop("name")
    lock.pop("notes", None)
    # Plan 23 stage D4: the body-rule hooks stay on the template (``lock_style`` reads them there).
    for key in ("body_rules", "default_material", "default_body_rule"):
        lock.pop(key, None)
    lock.pop("$schema", None)
    template_id = lock.pop("template_id")
    template_version = lock.pop("version")

    errors = []
    applied = {}
    for path, value in overrides.items():
        validator = OVERRIDABLE.get(path)
        if validator is None:
            errors.append(f"{path}: not an overridable field")
            continue
        field_errors = validator(value, path)
        if field_errors:
            errors.extend(field_errors)
            continue
        applied[path] = value

    if errors:
        raise StyleLockError("invalid style overrides", errors)

    for path, value in applied.items():
        _set_path(lock, path, value)

    lock["$schema"] = "style_lock_v1"
    lock["template_id"] = template_id
    lock["template_version"] = template_version
    lock["template_name"] = name
    lock["overrides"] = applied
    lock["locked_at"] = None
    lock["updated_at"] = now

    doc_errors = schemas.style_lock_errors(lock)
    if doc_errors:
        raise StyleLockError("style_lock_v1", doc_errors)

    return lock


def apply_overrides(lock: dict, overrides: dict, *, now: str) -> dict:
    """Return a new lock with ``overrides`` merged onto the existing ones
    (a repeated path takes the newer value) and re-derived from scratch from
    the shipped template, so the result is never a patch on top of a patch.

    Refuses when the lock is already frozen (``locked_at`` set), and when
    the shipped template has moved to a different version than the one the
    lock was built from — a template edited under a draft must be re-picked
    deliberately, never silently re-merged.
    """
    if lock.get("locked_at") is not None:
        raise StyleLockError("style is locked", ["cannot override a locked style_lock"])

    template = templates.load_style(lock["template_id"])
    if lock["template_version"] != template["version"]:
        raise StyleLockError(
            "template version mismatch",
            [
                f"lock is on template_version {lock['template_version']} but the "
                f"shipped template {lock['template_id']!r} is now at version "
                f"{template['version']}"
            ],
        )

    merged = dict(lock.get("overrides", {}))
    merged.update(overrides)
    return build_style_lock(template, merged, now=now)


def all_matter_rules(template_id: str, *, material: str | None = None) -> str | None:
    """The ``character_design_rules`` a style gives characters whose whole
    body is their own matter (plan 23 stage D4): the shipped template's
    ``body_rules.all_matter`` with its ``{material}`` slot filled by
    *material* (a universe's rule, a later stage), else the template's
    ``default_material``; None when the style has no such rule (or no
    material to fill it with)."""
    try:
        template = templates.load_style(template_id)
    except KeyError:
        return None
    text = (template.get("body_rules") or {}).get("all_matter")
    material = material or template.get("default_material")
    if not text:
        return None
    if "{material}" in text:
        if not material:
            return None
        text = text.replace("{material}", material)
    return text


def lock_style(lock: dict, *, now: str, body_rule: str | None = None, material: str | None = None) -> dict:
    """Return a copy of ``lock`` with ``locked_at`` set to ``now``.

    Refuses when already locked — locking twice would silently discard the
    original lock timestamp.

    Plan 23 stage D4: with *body_rule* ``"all_matter"`` the copy's
    ``character_design_rules`` are the style's ``body_rules.all_matter``
    (:func:`all_matter_rules`, *material* filling its slot) -- the one moment
    the rules change: a lock already frozen is never touched, and any other
    *body_rule* (None, ``"human_body"``) leaves them as the template wrote
    them. ``StyleLockError`` when the style has no such rule.
    """
    if lock.get("locked_at") is not None:
        raise StyleLockError("style is locked", ["style_lock is already locked"])
    new_lock = copy.deepcopy(lock)
    if body_rule == "all_matter":
        rules = all_matter_rules(lock["template_id"], material=material)
        if rules is None:
            raise StyleLockError("body rule", [
                f"the {lock['template_id']!r} style has no all_matter body rule (body_rules.all_matter)"])
        new_lock["character_design_rules"] = rules
    new_lock["locked_at"] = now
    return new_lock

"""Loaders for the AI Story data templates: style templates (spec 5), the
curated concept library (spec 7), the episode templates (spec 6.2) and the
plot-archetype library (plan 20 stage 2) and the universes (plan 23 stage D2).

Templates are data, not code, and are located relative to this file (no
package-data mechanism exists in this repo). Every loader validates against
the schemas of ``schemas.py`` before handing data back, so a broken template
JSON fails at load time with a readable ``SchemaError`` rather than surfacing
as a confusing bug three layers up. Results are cached (the files do not
change while the process runs) but every public function returns a deep copy,
so a caller can freely mutate what it gets back without corrupting the cache.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import functools
import json
import re
from pathlib import Path

from . import schemas

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Same pattern as template_id / concept_id in schemas.py; kept local so a
# malformed id is rejected before any path is built from it.
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def list_style_ids() -> list:
    """Sorted ids of the shipped style templates (file stems in styles/)."""
    styles_dir = TEMPLATES_DIR / "styles"
    if not styles_dir.is_dir():
        return []
    return sorted(p.stem for p in styles_dir.glob("*.json"))


@functools.lru_cache(maxsize=None)
def _load_style_cached(template_id: str) -> dict:
    path = TEMPLATES_DIR / "styles" / f"{template_id}.json"
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    errors = schemas.style_template_errors(data)
    if errors:
        raise schemas.SchemaError(template_id, errors)
    return data


def load_style(template_id: str) -> dict:
    """Load and validate one style template by id.

    The id is checked against the same pattern as ``template_id`` fields
    *before* any path is built from it, so a value like ``"../x"`` is
    rejected without ever touching the filesystem.
    """
    if not isinstance(template_id, str) or not _ID_PATTERN.match(template_id):
        raise KeyError(template_id)
    if template_id not in list_style_ids():
        raise KeyError(template_id)
    return copy.deepcopy(_load_style_cached(template_id))


def list_episode_template_ids() -> list:
    """Sorted ids of the shipped episode templates (file stems in episodes/)."""
    episodes_dir = TEMPLATES_DIR / "episodes"
    if not episodes_dir.is_dir():
        return []
    return sorted(p.stem for p in episodes_dir.glob("*.json"))


@functools.lru_cache(maxsize=None)
def _load_episode_template_cached(template_id: str) -> dict:
    path = TEMPLATES_DIR / "episodes" / f"{template_id}.json"
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    errors = schemas.episode_template_errors(data)
    if errors:
        raise schemas.SchemaError(template_id, errors)
    return data


def load_episode_template(template_id: str) -> dict:
    """Load and validate one episode template by id.

    Same rule as ``load_style``: the id is checked against the same pattern
    *before* any path is built from it, so a value like ``"../x"`` is
    rejected without ever touching the filesystem.
    """
    if not isinstance(template_id, str) or not _ID_PATTERN.match(template_id):
        raise KeyError(template_id)
    if template_id not in list_episode_template_ids():
        raise KeyError(template_id)
    return copy.deepcopy(_load_episode_template_cached(template_id))


@functools.lru_cache(maxsize=1)
def _load_concepts_cached() -> tuple:
    concepts_dir = TEMPLATES_DIR / "concepts"
    style_ids = list_style_ids()
    concepts = []
    if concepts_dir.is_dir():
        for path in sorted(concepts_dir.glob("*.json")):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            errors = schemas.concept_errors(data, style_ids)
            if errors:
                raise schemas.SchemaError(path.stem, errors)
            concepts.append(data)
    concepts.sort(key=lambda c: c["concept_id"])
    return tuple(concepts)


def load_concepts() -> list:
    """Load and validate every concept in templates/concepts/, sorted by id."""
    return [copy.deepcopy(c) for c in _load_concepts_cached()]


@functools.lru_cache(maxsize=1)
def _load_archetypes_cached() -> tuple:
    archetypes_dir = TEMPLATES_DIR / "archetypes"
    archetypes = []
    if archetypes_dir.is_dir():
        for path in sorted(archetypes_dir.glob("*.json")):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            errors = schemas.archetype_errors(data)
            if not errors and data["id"] != path.stem:
                errors = [f"$.id: {data['id']!r} does not match its file name {path.name!r}"]
            if errors:
                raise schemas.SchemaError(path.stem, errors)
            archetypes.append(data)
    errors = schemas.archetype_library_errors(archetypes)
    if errors:
        raise schemas.SchemaError("archetypes", errors)
    archetypes.sort(key=lambda a: a["id"])
    return tuple(archetypes)


def load_archetypes() -> list:
    """Load and validate every plot archetype in templates/archetypes/, sorted by id."""
    return [copy.deepcopy(a) for a in _load_archetypes_cached()]


def list_archetype_ids() -> list:
    """Every shipped plot-archetype id, sorted."""
    return [a["id"] for a in _load_archetypes_cached()]


def load_archetype(archetype_id: str) -> dict:
    """One plot archetype by id; ``KeyError`` for an id the library lacks."""
    for archetype in _load_archetypes_cached():
        if archetype["id"] == archetype_id:
            return copy.deepcopy(archetype)
    raise KeyError(archetype_id)


def localize_archetype(archetype: dict, language: str) -> dict:
    """Flatten every bilingual field of *archetype* to a single language."""
    if language not in schemas.LANGUAGES:
        raise ValueError(f"language must be one of {schemas.LANGUAGES}, not {language!r}")
    return _flatten_bilingual(archetype, language)


def archetype_beat(archetype: dict, function: str):
    """The beat (a localized archetype's) that plays arc *function*, or None."""
    return next((beat["beat"] for beat in archetype["beats"] if beat["function"] == function), None)


def _flatten_bilingual(value, language: str):
    if isinstance(value, dict):
        if set(value.keys()) == {"fr", "en"}:
            return value[language]
        return {key: _flatten_bilingual(v, language) for key, v in value.items()}
    if isinstance(value, list):
        return [_flatten_bilingual(v, language) for v in value]
    return value


@functools.lru_cache(maxsize=1)
def _load_universes_cached() -> tuple:
    with open(TEMPLATES_DIR / "universes.json", encoding="utf-8") as fh:
        data = json.load(fh)
    errors = schemas.universes_errors(data)
    if errors:
        raise schemas.SchemaError("universes", errors)
    return tuple(data["universes"])


def load_universes() -> list:
    """Every universe of templates/universes.json (plan 23 stage D2), in the
    file's order, validated against ``schemas.UNIVERSES_SCHEMA``."""
    return [copy.deepcopy(u) for u in _load_universes_cached()]


def universe(universe_id: str) -> dict:
    """One universe by id; ``KeyError`` for an id the file lacks."""
    for entry in _load_universes_cached():
        if entry["id"] == universe_id:
            return copy.deepcopy(entry)
    raise KeyError(universe_id)


def localize_concept(concept: dict, language: str) -> dict:
    """Flatten every bilingual field of *concept* to a single language."""
    if language not in schemas.LANGUAGES:
        raise ValueError(f"language must be one of {schemas.LANGUAGES}, not {language!r}")
    return _flatten_bilingual(concept, language)

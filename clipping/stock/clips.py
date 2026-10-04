"""Clips mode's side of the stock library (plan 23 stage B2).

Three small entry points over :mod:`clipping.stock.base`: whether any B-roll
source is usable (the analyzer asks before it has the AI write queries), where a
configured local folder may live, and the one call the clip renderer makes to turn
a query into a downloaded file plus its credit record.

*cfg* is either the run's config (a namespace with ``pexels_api_key``,
``pixabay_api_key``, ``broll_sources`` and ``broll_local_dir``) or a plain mapping
of environment names; a source with no key or folder is skipped, so a Pexels-only
install fetches exactly what it did before."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from . import base
from .credits import credit_record

logger = logging.getLogger("clipping.stock")

CONTAINER_ROOT = "/app/broll"
REPO_ROOT = Path(__file__).resolve().parents[2]


def environment(cfg=None) -> dict:
    """The environment the sources read: the process environment, with the run's
    own keys, folder and cache on top when *cfg* carries them."""
    env = dict(os.environ)
    if isinstance(cfg, Mapping):
        env.update({str(k): str(v) for k, v in cfg.items() if v is not None})
        return env
    for attr, name in (("pexels_api_key", "PEXELS_API_KEY"), ("pixabay_api_key", "PIXABAY_API_KEY"),
                       ("broll_local_dir", "BROLL_LOCAL_DIR")):
        if hasattr(cfg, attr):
            env[name] = str(getattr(cfg, attr) or "")
    return env


def sources_of(cfg=None) -> tuple:
    """The configured source order (``broll_sources`` / ``BROLL_SOURCES``; the process
    environment when *cfg* is None), the default when empty."""
    if cfg is None:
        value = os.environ.get("BROLL_SOURCES")
    elif isinstance(cfg, Mapping):
        value = cfg.get("BROLL_SOURCES")
    else:
        value = getattr(cfg, "broll_sources", None)
    return base.parse_sources(value)


def local_root() -> str:
    """Where a B-roll folder may live: ``/app/broll`` inside the container, else the repo's ``./broll``."""
    return os.path.realpath(CONTAINER_ROOT if os.path.isdir(CONTAINER_ROOT) else str(REPO_ROOT / "broll"))


def resolve_local_dir(value) -> str:
    """The real path of *value* when it is the allowed root or below it ("" stays "");
    a relative value is read from the root. Raises ValueError for any other place."""
    text = str(value or "").strip()
    if not text:
        return ""
    root = local_root()
    path = text if os.path.isabs(text) else os.path.join(root, text)
    real = os.path.realpath(path)
    if real != root and not real.startswith(root.rstrip(os.sep) + os.sep):
        raise ValueError(f"BROLL_LOCAL_DIR must be {root} or a folder inside it")
    return real


def _registry():
    return base._registry()


def local_clip_count(env=None) -> int:
    """How many usable clips the configured local folder holds (0 without one)."""
    from . import local
    env = environment(env)
    if not local.SOURCE.available(env):
        return 0
    try:
        return len(local.SOURCE.index(env).scan())
    except Exception as exc:  # noqa: BLE001
        logger.warning("stock: the local B-roll folder could not be indexed: %s", base.redact(exc))
        return 0


def available_sources(cfg=None) -> list:
    """The configured sources that can answer now, in order: a keyed Pexels or
    Pixabay, a local folder holding at least one clip."""
    env = environment(cfg)
    registry = _registry()
    names = []
    for name in sources_of(cfg):
        source = registry.get(name)
        if source is None or name in names:
            continue
        try:
            if not source.available(env):
                continue
            if name == "local" and not local_clip_count(env):
                continue
        except Exception:  # noqa: BLE001
            continue
        names.append(name)
    return names


def any_source_available(cfg=None) -> bool:
    return bool(available_sources(cfg))


def fetch_for_clips(query, ratio, dest, cfg=None, *, pool=None, rng=None, opener_=None):
    """Search the configured sources in order and download the match to *dest*.

    Returns ``(path, credit_record)``, or None when nothing matched or the download
    failed (the clip then renders without that B-roll, as before)."""
    env = environment(cfg)
    try:
        clip = base.search(query, aspect=ratio, sources=sources_of(cfg), env=env, pool=pool, rng=rng)
    except Exception as exc:  # noqa: BLE001
        print(f"   ⚠️ B-roll search for '{query}' failed: {base.redact(exc)}")
        return None
    if clip is None:
        print(f"   ⚠️ No B-roll found for '{query}'.")
        return None
    try:
        path = base.download(clip, dest, **({"opener_": opener_} if opener_ else {}))
    except base.StockError as exc:
        print(f"   ⚠️ Error while downloading B-roll '{query}': {base.redact(exc)}")
        return None
    return path, credit_record(clip)

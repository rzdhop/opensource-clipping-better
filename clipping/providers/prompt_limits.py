"""How long a prompt each generation link accepts (the human, 2026-10-02:
"some providers have limited prompt size so check that also").

:data:`TABLE` holds one :class:`Limit` per link label (``fal/kling-2.5-turbo-std``)
or per provider (``edge/*``): a maximum in characters, words or tokens, where
it comes from, and whether the vendor publishes it (``verified``) or it is a
conservative choice of ours because the vendor publishes nothing (the
``source`` says which, and why that number). Nothing here could be re-read
from the container it was written in (its egress proxy blocks fal.ai and
Google): the published numbers are the ones recorded in the repository's
notes and the phase-7 brief, and fal's own are re-read live by the free key
check (below).

* :func:`limit_for` -- the link's limit, a live value first; ``None`` when
  nothing is known (``local/*``: our own hardware and templates).
* :func:`fits` / :func:`check` -- measure a prompt against it. The chain
  runner calls :func:`check` before any adapter (``generation``): a prompt
  over its link's limit is refused, never truncated and never sent, and the
  chain moves on as with any other refusal.
* :func:`budget_words` -- a conservative word budget derived from the limit,
  for the prompt builders.

Tokens are counted as ``pacing.estimate_tokens`` counts them, about four
characters each, everywhere in this module: the check, the budget and the
listing agree on one rough measure, never on a vendor's tokenizer.

**Live limits.** fal publishes each endpoint's OpenAPI schema at
:data:`FAL_OPENAPI` (public, free, no generation); its input's ``prompt``
property may carry a ``maxLength``. :func:`read_fal_schema` reads it (the key
check in ``video.check_key`` does, for every fal video link) and
:func:`record_live` keeps what was read in ``provider_limits.json`` next to
the dashboard's Settings file (``data/``, or ``WEB_SETTINGS_FILE``'s folder).
This module reads that file by path, never through ``web`` (the CLI never
imports it), and :func:`limit_for` prefers a published live ``maxLength`` over
the table.

Stdlib only (DEC-012). Never imports ``generation`` (which imports this).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import urllib.parse
from collections import namedtuple
from datetime import datetime, timezone

from . import errors
from .pacing import estimate_tokens
from .registry import describe
from .transport import HttpStatusError, request_json, urllib_transport

# What ``max_chars`` counts: the prompt's own characters, or its characters
# once URL-encoded (Pollinations sends the prompt in the URL path).
CHARS = "chars"
URL = "url"

# ``max_chars``/``max_words``/``max_tokens`` are what the API accepts: a prompt
# over one is refused here. ``window_tokens`` is what the MODEL reads (a text
# encoder's window, e.g. FLUX's T5): the API takes more and the model ignores
# the tail silently, so it bounds :func:`budget_words` but never refuses --
# a v1 shot prompt past it was always sent and served.
Limit = namedtuple("Limit", "max_chars max_words max_tokens source verified measure window_tokens",
                   defaults=(None, None, None, "", False, CHARS, None))

# --------------------------------------------------------------- the table

_LTX2 = "fal's LTX-2 schema's prompt maxLength, 5000"
_FLUX_T5 = ("no API limit published; FLUX.1's T5-XXL text encoder reads 512 tokens (its max_sequence_length; "
            "schnell is often run at 256, which cuts earlier). Text past the window is ignored by the model, not "
            "refused by the API (v1 shot prompts of ~2200 characters were always sent), so the window bounds the "
            "word budget, not the check")

TABLE = {
    # ----------------------------------------------------------- video (fal)
    "fal/kling-2.5-turbo-std": Limit(
        2500, source="fal's OpenAPI schema for fal-ai/kling-video/v2.5-turbo/standard/image-to-video: prompt "
                     "maxLength 2500 (the same cap on negative_prompt)", verified=True),
    "fal/ltx-2-fast": Limit(5000, source=f"{_LTX2} (fal-ai/ltxv-2/image-to-video/fast; refused by the adapter, "
                                         "A-101)", verified=True),
    "fal/ltx-2.3-fast": Limit(
        5000, source=f"assumed equal to {_LTX2}; fal-ai/ltx-2.3/image-to-video/fast's own is not in our notes "
                     "(the key check reads it live)", verified=False),
    "fal/seedance-1-pro-fast": Limit(
        1500, source="unpublished on fal (no maxLength in its schema, A-100/A-110); 56-77-word (~470-char) prompts "
                     "were accepted on 2026-10-01 (A-110); 1500 chars (~3x that) is our conservative cap, under the "
                     "2000-2500 fal publishes for its MiniMax and Kling image-to-video endpoints", verified=False),
    # ---------------------------------------------------------- video (Veo)
    "gemini/veo-3.1-lite": Limit(
        max_tokens=1024, source="Google's Veo documentation: prompts of up to 1024 tokens (counted here at ~4 "
                                "characters each)", verified=True),
    # ---------------------------------------------------------------- images
    "cloudflare/flux-1-schnell": Limit(
        2048, source="Workers AI's model schema for @cf/black-forest-labs/flux-1-schnell: prompt maxLength 2048",
        verified=True),
    "fal/flux-schnell": Limit(window_tokens=512, source=_FLUX_T5, verified=False),
    "fal/flux-kontext-pro": Limit(window_tokens=512, source=f"FLUX.1 Kontext, as FLUX.1: {_FLUX_T5}", verified=False),
    "pollinations/flux": Limit(
        4000, measure=URL, window_tokens=512,
        source="unpublished: the prompt rides in the URL path. Our cap of 4000 characters once URL-encoded is "
               "about 1.3x the longest v1 shot prompts (187-283 words, ~1900-3000 encoded) Pollinations served in "
               "the Tier-2 walks, and far under the 8-16 KB request lines HTTP servers and CDNs accept; FLUX's "
               "512-token T5 window bounds the word budget only", verified=False),
    "pollinations/*": Limit(
        4000, measure=URL, source="unpublished: the prompt rides in the URL path; our cap of 4000 characters once "
                                  "URL-encoded, as pollinations/flux", verified=False),
    "fal/seedream-4-edit": Limit(
        3000, source="unpublished on fal (no maxLength in the Seedream schemas, A-111); 220-word (~1500-char) "
                     "prompts are confirmed accepted on Seedream 4.5 (A-111); 3000 chars (2x) is our conservative cap",
        verified=False),
    "fal/seedream-4.5": Limit(
        3000, source="unpublished on fal (no stated prompt length in its schema, A-111/DEC-235); our conservative "
                     "cap, 2x the 220-word prompts confirmed accepted (A-111)", verified=False),
    "fal/seedream-4.5-edit": Limit(
        3000, source="unpublished on fal (no stated prompt length in its schema, A-111); our conservative cap, 2x "
                     "the 220-word prompts confirmed accepted on 2026-10-01 (A-111)", verified=False),
    "gemini/nano-banana-2-lite": Limit(
        max_tokens=8192, source="no stated prompt limit (A-112): the model's input context bounds it. Our cap: "
                                "32768 input tokens (gemini-2.5-flash-image's, the smallest of the family; the "
                                "3.1 models assumed no smaller) less up to 14 reference images at ~1290 tokens "
                                "each, rounded down to 8192 for the text", verified=False),
    "gemini/nano-banana-2": Limit(
        max_tokens=8192, source="no stated prompt limit (A-112); our cap, as gemini/nano-banana-2-lite: the "
                                "input context less 14 reference images, 8192 tokens kept for the text",
        verified=False),
    "openai/gpt-image-2-low": Limit(
        32000, source="OpenAI's Images API publishes 32000 characters for gpt-image-1; gpt-image-2 assumed the "
                      "same", verified=False),
    # ---------------------------------------------------------------- speech
    "gemini/flash-lite-tts": Limit(
        max_tokens=8192, source="the TTS model's input tokens bound a line: 8192 is what gemini-2.5-flash-preview-"
                                "tts publishes, assumed no smaller for gemini-3.8-flash-lite-tts; one line is far "
                                "below it", verified=False),
    "edge/*": Limit(source="edge-tts splits long text into requests itself (one voice per request)",
                    verified=False),
}

# --------------------------------------------------------------- measuring

# A word budget from a character cap: French/English prose averages ~6.0
# characters a word, its space and accents included (measured on shot
# prompts, 2026-10-02); 6.5 leaves a margin. URL-encoded, the same prose
# averages ~8.4 (English) to 9.0 (French) characters a word; 10 leaves one.
CHARS_PER_WORD = 6.5
URL_CHARS_PER_WORD = 10.0
# The inverse of ``pacing.estimate_tokens`` (``len // 4``).
CHARS_PER_TOKEN = 4

# The head of the chain runner's reason for a refused prompt: the story side
# reads it as "nothing was sent" (``clipping.aistory.steps.pacing``).
REFUSAL_HEAD = "prompt too long: "


class PromptTooLong(errors.ProviderError):
    """A prompt over its link's limit, refused before anything was sent.
    ``label``, ``measured`` (:func:`measure`) and ``limit`` say why."""

    def __init__(self, message, *, label, measured, limit):
        super().__init__(message)
        self.label = label
        self.measured = dict(measured)
        self.limit = limit


def _label(link_or_label) -> str:
    return link_or_label if isinstance(link_or_label, str) else describe(link_or_label)


def sent_text(kind, request) -> str:
    """The text a request sends as its prompt: a TTS line's ``text`` (else its
    prompt), every other kind's ``prompt``."""
    if kind == "tts":
        return (request.text or request.prompt or "").strip()
    return request.prompt or ""


def measure(text, limit=None) -> dict:
    """``{"chars", "words", "tokens"}`` of *text*, plus ``"url_chars"`` when
    *limit* counts the URL-encoded prompt."""
    text = text or ""
    measured = {"chars": len(text), "words": len(text.split()), "tokens": estimate_tokens(text)}
    if limit is not None and limit.measure == URL:
        measured["url_chars"] = len(urllib.parse.quote(text, safe=""))
    return measured


def _over(label, limit, measured):
    """The sentence for the first cap *measured* exceeds, or None."""
    if limit.max_chars:
        if limit.measure == URL and measured["url_chars"] > limit.max_chars:
            return (f"{label} accepts {limit.max_chars} characters of URL-encoded prompt; this prompt is "
                    f"{measured['url_chars']} once encoded ({measured['chars']} characters)")
        if limit.measure != URL and measured["chars"] > limit.max_chars:
            return f"{label} accepts {limit.max_chars} characters; this prompt is {measured['chars']}"
    if limit.max_tokens and measured["tokens"] > limit.max_tokens:
        return (f"{label} accepts {limit.max_tokens} tokens; this prompt is about {measured['tokens']} "
                f"({measured['chars']} characters at ~{CHARS_PER_TOKEN} a token)")
    if limit.max_words and measured["words"] > limit.max_words:
        return f"{label} accepts {limit.max_words} words; this prompt is {measured['words']}"
    return None


def fits(link, text, *, live=None) -> tuple:
    """``(ok, measured)``: whether *text* is within *link*'s limit (always
    ``True`` without one), and its :func:`measure`."""
    limit = limit_for(link, live=live)
    measured = measure(text, limit)
    return (limit is None or _over(_label(link), limit, measured) is None), measured


def check(link, text, *, live=None) -> dict:
    """*text*'s :func:`measure` when it fits *link*; :class:`PromptTooLong`
    naming the link, the size and the limit when it does not."""
    label = _label(link)
    limit = limit_for(label, live=live)
    measured = measure(text, limit)
    sentence = _over(label, limit, measured) if limit is not None else None
    if sentence:
        raise PromptTooLong(sentence, label=label, measured=measured, limit=limit)
    return measured


def budget_words(link, *, default, live=None) -> int:
    """A conservative word budget for a prompt on *link*, for the builders:
    the smallest of ``max_words``, ``max_chars / 6.5`` (``/ 10`` for a
    URL-encoded cap) and ``(max_tokens or window_tokens) * 4 / 6.5``,
    rounded down; *default* when the link has no known limit. A prompt of
    that many words fits the limit (:func:`fits`), and the model's window,
    unless its words average over 6.5 characters."""
    limit = limit_for(link, live=live)
    if limit is None:
        return default
    budgets = []
    if limit.max_words:
        budgets.append(limit.max_words)
    if limit.max_chars:
        budgets.append(limit.max_chars / (URL_CHARS_PER_WORD if limit.measure == URL else CHARS_PER_WORD))
    for tokens in (limit.max_tokens, limit.window_tokens):
        if tokens:
            budgets.append(tokens * CHARS_PER_TOKEN / CHARS_PER_WORD)
    return int(min(budgets)) if budgets else default


def describe_limit(limit) -> str:
    """``"≤ 2500 chars"``, ``"≤ 1024 tokens"``, ``"no API limit, the model
    reads ~512 tokens"``, ... or ``"no limit"``."""
    if limit is None:
        return "no limit known"
    parts = []
    if limit.max_chars:
        parts.append(f"≤ {limit.max_chars} {'URL-encoded chars' if limit.measure == URL else 'chars'}")
    if limit.max_tokens:
        parts.append(f"≤ {limit.max_tokens} tokens")
    if limit.max_words:
        parts.append(f"≤ {limit.max_words} words")
    if not parts and limit.window_tokens:
        parts.append("no API limit")
    if limit.window_tokens:
        parts.append(f"the model reads ~{limit.window_tokens} tokens")
    return ", ".join(parts) or "no limit"


# -------------------------------------------------------------- live values

LIVE_SCHEMA = "provider_limits_v1"
LIVE_FILE_NAME = "provider_limits.json"
PUBLISHED = "published"
NOT_PUBLISHED = "not_published"
FAILED = "failed"

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ``web.api.settings_store.SETTINGS_PATH`` and ``clipping.aistory.cli.SETTINGS_FILE``,
# spelled again: neither may be imported here (tests/test_prompt_limits.py pins them).
_SETTINGS_FILE = os.path.join(_ROOT, "data", "settings.json")

_LIVE_LOCK = threading.Lock()
_LIVE_CACHE = {}  # path -> ((mtime_ns, size), {label: entry})


def live_path() -> str:
    """``provider_limits.json`` in the Settings file's folder: ``data/``, or
    ``WEB_SETTINGS_FILE``'s."""
    settings = os.environ.get("WEB_SETTINGS_FILE") or _SETTINGS_FILE
    return os.path.join(os.path.dirname(os.path.abspath(settings)), LIVE_FILE_NAME)


def read_live(path=None) -> dict:
    """``{label: entry}`` from the live file, or ``{}`` when it is missing or
    unreadable -- never raises: a broken file leaves the table in charge."""
    path = path or live_path()
    try:
        stat = os.stat(path)
    except OSError:
        return {}
    stamp = (stat.st_mtime_ns, stat.st_size)
    with _LIVE_LOCK:
        cached = _LIVE_CACHE.get(path)
        if cached is not None and cached[0] == stamp:
            return cached[1]
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    links = data.get("links") if isinstance(data, dict) and data.get("$schema") == LIVE_SCHEMA else None
    entries = {str(label): entry for label, entry in (links or {}).items() if isinstance(entry, dict)} \
        if isinstance(links, dict) else {}
    with _LIVE_LOCK:
        _LIVE_CACHE[path] = (stamp, entries)
    return entries


def record_live(entries, *, path=None, now=None) -> str:
    """Merge *entries* (``{label: {"status", "prompt_max_chars", "endpoint",
    ...}}``, what :func:`read_fal_schema` returns) into the live file, each
    stamped ``read_at`` (*now*, else the current UTC time); written
    atomically. Returns the path; ``OSError`` when it cannot be written."""
    path = path or live_path()
    stamp = now or datetime.now(timezone.utc).isoformat()
    links = dict(read_live(path))
    for label, entry in entries.items():
        links[str(label)] = {**dict(entry), "read_at": stamp}
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=folder, prefix=".provider_limits-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump({"$schema": LIVE_SCHEMA, "links": links}, fh, indent=2, sort_keys=True, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    with _LIVE_LOCK:
        _LIVE_CACHE.pop(path, None)
    return path


def _live_chars(entry):
    value = (entry or {}).get("prompt_max_chars")
    if (entry or {}).get("status") == PUBLISHED and isinstance(value, int) and not isinstance(value, bool) \
            and value > 0:
        return value
    return None


def table_limit(link_or_label):
    """The table's :class:`Limit` for the link, else its provider's (``edge/*``), else None."""
    label = _label(link_or_label)
    limit = TABLE.get(label)
    if limit is None and "/" in label:
        limit = TABLE.get(f"{label.split('/', 1)[0]}/*")
    return limit


def limit_for(link_or_label, *, live=None):
    """The :class:`Limit` of a link (or its label): a ``maxLength`` the
    provider's schema publishes, read live (*live*: ``{label: entry}``, else
    the live file), replaces the table's character cap; otherwise the table's
    (:func:`table_limit`); ``None`` when nothing is known. Pass ``live={}``
    for the table alone."""
    label = _label(link_or_label)
    table = table_limit(label)
    entry = (read_live() if live is None else live).get(label)
    chars = _live_chars(entry)
    if chars is None:
        return table
    read_at = str(entry.get("read_at") or "")[:10]
    source = f"fal's schema for {entry.get('endpoint') or label}: prompt maxLength {chars}" \
             + (f" (read {read_at})" if read_at else "")
    return (table or Limit())._replace(max_chars=chars, measure=CHARS, source=source, verified=True)


# ------------------------------------------------------------ fal's schema

FAL_OPENAPI = "https://fal.ai/api/openapi/queue/openapi.json"
SCHEMA_TIMEOUT = 20.0


def _resolve(document, node, depth=0):
    """*node* with a local ``$ref`` (``#/components/schemas/X``) followed."""
    while isinstance(node, dict) and isinstance(node.get("$ref"), str) and depth < 10:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return {}
        target = document
        for part in ref[2:].split("/"):
            target = target.get(part) if isinstance(target, dict) else None
        node, depth = target if isinstance(target, dict) else {}, depth + 1
    return node if isinstance(node, dict) else {}


def _properties(document, schema) -> dict:
    """The properties of an object schema, its ``allOf`` parts merged."""
    schema = _resolve(document, schema)
    merged = dict(schema.get("properties") or {}) if isinstance(schema.get("properties"), dict) else {}
    for part in schema.get("allOf") or []:
        merged.update(_properties(document, part))
    return merged


def _max_length(document, prop, depth=0):
    """``maxLength`` on *prop*, or in one of its ``anyOf``/``oneOf``/``allOf``
    branches (pydantic writes an optional field that way); None without one."""
    prop = _resolve(document, prop)
    value = prop.get("maxLength")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if depth < 4:
        for key in ("anyOf", "oneOf", "allOf"):
            for branch in prop.get(key) or []:
                found = _max_length(document, branch, depth + 1)
                if found is not None:
                    return found
    return None


def _input_schema(document, endpoint):
    """The input schema of *endpoint*'s queue POST, or None: the POST on
    ``/<endpoint>``, else any POST with a JSON body, else a component named
    ``...Input`` with a ``prompt``."""
    paths = document.get("paths") if isinstance(document.get("paths"), dict) else {}
    ordered = [f"/{endpoint}"] + [path for path in paths if path != f"/{endpoint}"]
    for path in ordered:
        operation = (paths.get(path) or {}).get("post") if isinstance(paths.get(path), dict) else None
        content = ((operation or {}).get("requestBody") or {}).get("content") or {}
        schema = (content.get("application/json") or {}).get("schema") if isinstance(content, dict) else None
        if isinstance(schema, dict):
            return schema
    components = ((document.get("components") or {}).get("schemas") or {}) \
        if isinstance(document.get("components"), dict) else {}
    for name, schema in components.items():
        if str(name).endswith("Input") and "prompt" in _properties(document, schema):
            return schema
    return None


def schema_prompt_limit(document, endpoint):
    """``(status, max_chars)`` read off fal's OpenAPI *document* for
    *endpoint*: :data:`PUBLISHED` with the input ``prompt``'s ``maxLength``,
    :data:`NOT_PUBLISHED` (no ``maxLength``, or no ``prompt`` field), or
    :data:`FAILED` when the document holds no input schema at all."""
    schema = _input_schema(document, endpoint) if isinstance(document, dict) else None
    if schema is None:
        return FAILED, None
    prompt = _properties(document, schema).get("prompt")
    chars = _max_length(document, prompt) if prompt is not None else None
    return (PUBLISHED, chars) if chars is not None else (NOT_PUBLISHED, None)


def read_fal_schema(endpoint, *, transport=None, timeout=SCHEMA_TIMEOUT) -> dict:
    """Ask fal's public OpenAPI schema of *endpoint* (``fal-ai/...``) for its
    prompt limit, without a key and without generating anything::

        {"status": "published" | "not_published" | "failed",
         "prompt_max_chars": int | None, "endpoint": ..., "source": ..., "text": sentence}

    Never raises for an answer: a failure to read is ``failed`` with why."""
    transport = transport or urllib_transport
    url = f"{FAL_OPENAPI}?endpoint_id={urllib.parse.quote(endpoint, safe='/')}"
    read = {"status": FAILED, "prompt_max_chars": None, "endpoint": endpoint, "source": "fal's OpenAPI schema",
            "text": ""}
    try:
        document = request_json(transport, "GET", url, headers={}, timeout=timeout)
    except HttpStatusError as exc:
        read["text"] = f"fal's schema could not be read (HTTP {exc.status_code})"
        return read
    except Exception as exc:  # noqa: BLE001 - any failure to answer is reported, never raised
        read["text"] = f"fal's schema could not be read ({type(exc).__name__}: {str(exc)[:120]})"
        return read
    status, chars = schema_prompt_limit(document, endpoint)
    read.update(status=status, prompt_max_chars=chars)
    if status == PUBLISHED:
        read["text"] = f"prompt ≤ {chars} chars (fal's schema)"
    elif status == NOT_PUBLISHED:
        read["text"] = "prompt limit not published in fal's schema"
    else:
        read["text"] = f"fal's schema could not be read (no input schema for {endpoint})"
    return read


# ----------------------------------------------------------------- listing

def listing(*, live=None) -> list:
    """One line per table entry: ``label: limit (~N words) -- published |
    our estimate: source``; a live read that published nothing says so."""
    entries = read_live() if live is None else live
    lines = []
    for label in sorted(TABLE):
        limit = limit_for(label, live=entries)
        words = budget_words(label, default=0, live=entries)
        if not words:
            lines.append(f"{label}: {describe_limit(limit)} -- {limit.source}")
            continue
        kind = "published" if limit.verified else "our estimate"
        line = f"{label}: {describe_limit(limit)} (~{words} words) -- {kind}: {limit.source}"
        entry = entries.get(label) or {}
        if entry.get("status") == NOT_PUBLISHED:
            line += f"; fal's schema publishes none (read {str(entry.get('read_at') or '')[:10]})"
        lines.append(line)
    return lines

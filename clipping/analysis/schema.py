"""JSON schemas for the three analysis passes.

Every schema here is small on purpose. The design this replaces asked for 22
required fields per clip in one request — roughly 1200 output tokens each, which
at a measured 12-13 tokens/s cannot finish inside a 300s gateway window for more
than about three clips, and exceeds Groq's 8000 tokens/minute outright. The
largest generation below is ~320 tokens.

Five fields the old schema demanded are simply not asked for any more:
``recommended_visual_broll_hook`` (read by nothing in the entire repo),
``klasifikasi_akun`` with its eight nested keys (printed to the console and
never used), and the three ``tiktok_*_id`` variants (which never reached the
render manifest). Everything else is either derived deterministically in
``derive.py`` or is genuinely editorial.

``additionalProperties: False`` everywhere, because ``strict`` json_schema mode
requires it and because a model that invents a field is usually also ignoring a
real one.

Stdlib only.
"""

from __future__ import annotations

KINDS = ("story", "insight", "conflict", "howto", "punchline")
MOODS = ("chill", "epic", "sad", "upbeat", "suspense")


def _obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required if required is not None else properties),
        "additionalProperties": False,
    }


# ----------------------------------------------------------------- pass A

CANDIDATES_SCHEMA = _obj(
    {
        "candidates": {
            "type": "array",
            "items": _obj(
                {
                    "b0": {"type": "integer", "description": "first beat id"},
                    "b1": {"type": "integer", "description": "last beat id"},
                    "score": {"type": "integer", "minimum": 1, "maximum": 100},
                    "gist": {"type": "string"},
                    "kind": {"type": "string", "enum": list(KINDS)},
                }
            ),
        }
    }
)

# ----------------------------------------------------------------- pass B

# ``topic`` is not editorial output -- nothing renders it. It exists so the
# "prefer variety" instruction can be enforced in Python instead of trusted:
# two picks that name the same subject are the repetition the rule is about,
# and a model that is told to avoid repetition still reliably produces it.
RANKED_SCHEMA = _obj(
    {
        "ranked": {
            "type": "array",
            "items": _obj(
                {
                    "id": {"type": "integer"},
                    "score": {"type": "integer", "minimum": 1, "maximum": 100},
                    "topic": {"type": "string"},
                }
            ),
        }
    }
)

# ----------------------------------------------------------------- pass C

CLIP_META_SCHEMA = _obj(
    {
        "title_native": {"type": "string"},
        "title_en": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "desc_hook": {"type": "string"},
        "desc_context": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "caption_native": {"type": "string"},
        "caption_en": {"type": "string"},
        "reason": {"type": "string"},
        "hook_beat": {"type": "integer"},
        "emphasis": {"type": "array", "items": {"type": "string"}},
        "broll_queries": {"type": "array", "items": {"type": "string"}},
        "mood": {"type": "string", "enum": list(MOODS)},
        "drop_beats": {"type": "array", "items": {"type": "integer"}},
    }
)

# Output budgets, in tokens. Sized from live measurement rather than guessed:
# a candidate costs ~45 tokens and a window yields at most 6, a ranking entry
# costs ~12, and a metadata object measured ~280.
MAX_TOKENS_CANDIDATES = 700
# Raised from 400 when ``topic`` was added: one lowercase word costs ~4 tokens
# and the re-rank can see every surviving candidate in the video.
MAX_TOKENS_RANKED = 600
MAX_TOKENS_CLIP_META = 900

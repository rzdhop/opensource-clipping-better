"""The real pass-A request, on a transcript small enough to send to every key.

Two consumers need to ask a model the question a job will ask it, without a job:

* ``tools/bench_llm.py``, which picks the default model for each provider;
* ``POST /api/settings/test-chain``, which tells a user whether each of their
  keys can carry an analysis before they spend an upload on finding out.

Both used to send something else. The bench sent 45 copies of one sentence
under a hand-copied schema, and the settings test sent "reply with ok". DEC-058
is the record of what that costs: a model that answered the fabricated input
fast and schema-valid answered every real transcript with ``{"candidates": []}``.
A probe that cannot fail that model is not a probe.

So this module has two halves:

``pass_a_work``
    THE builder of a pass-A request. ``config._preflight_work`` calls it too, so
    the preflight, the bench and the settings test cannot drift from each other.
    It is the same system prompt, prompt text, schema and token budget the scan
    sends; only the beats differ.

``FIXTURE_BEATS`` + ``judge``
    A 14-beat monologue with exactly one clip in it. Beats 0-2 are a greeting,
    a sponsor read and a "today I want to talk about"; beats 10-13 are the
    things the prompt tells a model to reject by name (the first of five
    lessons, "next week", like and subscribe, goodbye). Beats 4-9 are a story
    that hooks on a number and a confession, builds, and pays off inside
    itself, at a length that fits the default preset. A model that does the
    job finds it; a model that echoes or pattern-matches does not, and
    ``judge`` says which happened.

The fixture's answer is deliberately not identical to the prompt's worked
example ("I lost forty thousand dollars..."), so a model cannot pass by
recognising the calibration text.

Stdlib only.
"""

from __future__ import annotations

from collections import namedtuple

from . import presets as presets_mod
from . import prompts, schema

# ``(start, end, text)``. Timed at a natural ~2.3 words per second.
_FIXTURE = (
    (0.0, 6.2, "Hey everyone, welcome back to the channel, good to have you here again."),
    (6.2, 15.0, "Quick thanks to today's sponsor, Northwind Hosting. Use the code SHIP20 "
                "at checkout for twenty percent off your first year."),
    (15.0, 21.5, "Okay. Today I finally want to talk about deploy days, and why I changed mine."),
    (21.5, 26.0, "But first, a little context on how our team works."),
    (26.0, 33.0, "Three years ago I promised my whole team I would never ship anything "
                 "on a Friday again."),
    (33.0, 40.5, "The very next Friday, at five in the afternoon, I pushed a one-line "
                 "change to billing."),
    (40.5, 48.0, "By two in the morning, every customer in Australia had been charged twice."),
    (48.0, 56.5, "My phone was buzzing on the nightstand, and the support queue had four "
                 "hundred angry tickets in it."),
    (56.5, 64.0, "It took us three hours to find it. The server compared dates in UTC "
                 "and the billing job ran in local time."),
    (64.0, 72.5, "The fix was one line. We printed that line, framed it, and it still "
                 "hangs above my desk."),
    (72.5, 79.0, "So that is the first of five lessons I want to share with you today."),
    (79.0, 85.5, "We will get into the hiring mistakes properly next week, so stay tuned "
                 "for that."),
    (85.5, 91.0, "If this helped at all, hit like and subscribe, it really helps the channel."),
    (91.0, 95.0, "Alright, that's it for now. See you next time, bye."),
)

FIXTURE_BEATS = [
    {
        "i": i,
        "start": start,
        "end": end,
        "text": text,
        "n_words": len(text.split()),
    }
    for i, (start, end, text) in enumerate(_FIXTURE)
]

# The one clip: the Friday promise (4) through the framed fix (9).
STORY_SPAN = (4, 9)
# A candidate that opens one beat early (the "context" line) or closes one
# beat late still found the story; the snapper trims either way.
STORY_TOLERANCE = 1
# Beats no clip should be made of.
HOUSEKEEPING = frozenset({0, 1, 2, 10, 11, 12, 13})

# The language the fixture is written in, for the prompt's context line.
FIXTURE_LANGUAGE = "en"


def pass_a_work(beats_text, *, preset=None, language=None, total_seconds=None,
                topic="", max_candidates=None):
    """The pass-A request for *beats_text*, as a ``work`` dict.

    The shape ``llm.probe_chain(work=...)`` and ``LlmClient.complete_json``
    take. ``max_candidates`` defaults to the scan's own, imported lazily so
    this module stays importable without the analyzer's dependencies.
    """
    if max_candidates is None:
        from .analyzer import MAX_CANDIDATES_PER_WINDOW

        max_candidates = MAX_CANDIDATES_PER_WINDOW
    return {
        "system": prompts.SYSTEM,
        "user": prompts.candidates_prompt(
            beats_text,
            max_candidates=max_candidates,
            preset=presets_mod.get(preset),
            language=language,
            total_seconds=total_seconds,
            topic=str(topic or "").strip(),
        ),
        "schema": schema.CANDIDATES_SCHEMA,
        "schema_name": "candidates",
        "max_tokens": schema.MAX_TOKENS_CANDIDATES,
    }


def fixture_text():
    """The fixture rendered exactly as a scan window renders its beats."""
    from .beats import render_beats

    return render_beats(FIXTURE_BEATS)


def diagnostic_work():
    """The pass-A request on the fixture: the destined action, ~1.5k tokens."""
    return pass_a_work(
        fixture_text(),
        preset=presets_mod.DEFAULT_PRESET,
        language=FIXTURE_LANGUAGE,
        total_seconds=FIXTURE_BEATS[-1]["end"],
    )


Judgement = namedtuple("Judgement", "candidates found_moment note")


def judge(value):
    """How well an answer to ``diagnostic_work()`` did the job.

    ``candidates`` counts what the scan itself would keep (well-formed, ids
    inside the window). ``found_moment`` is true when one of them covers the
    story. ``note`` is one sentence for a person, empty when the story was
    found.

    Zero candidates is still a *live* provider (DEC-067): one window's empty
    answer is an opinion, not an outage. It is also exactly what DEC-058's
    useless model returned on every real transcript, so it is said out loud.
    """
    from .analyzer import _clean_candidates

    kept = _clean_candidates(value, 0, FIXTURE_BEATS[-1]["i"])
    lo, hi = STORY_SPAN
    t = STORY_TOLERANCE
    found = any(
        lo - t <= c["b0"] <= lo + t and hi - t <= c["b1"] <= hi + t for c in kept
    )
    if found:
        note = ""
    elif not kept:
        note = (
            "Answered, but found no moment in a test transcript that has an "
            "obvious one."
        )
    elif all(set(range(c["b0"], c["b1"] + 1)) <= HOUSEKEEPING for c in kept):
        note = "Picked only the intro and outro, which the prompt says to reject."
    else:
        note = (
            f"Found {len(kept)} moment(s), but not the story at beats "
            f"{lo}-{hi}."
        )
    return Judgement(len(kept), found, note)

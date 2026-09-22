"""Deterministic clip boundaries. Every timing decision happens here, in Python.

The model picks *which* beats are interesting. It never picks where the cut
lands. That division is the point: asking a language model for a timestamp
invites one that is plausible and wrong, and a clip that starts half a syllable
early is obvious to a viewer in a way that is invisible in a JSON diff.

So a candidate arrives as two beat ids and leaves as a span that is guaranteed
to sit on sentence boundaries, fall inside the platform's duration window, carry
a little lead-in and tail, and not overlap another clip.

Stdlib only.
"""

from __future__ import annotations

from collections import namedtuple

# ``gist`` and ``kind`` are the candidate's own description, carried through
# untouched. Nothing in this module reads them -- snapping is a timing decision
# and stays one (DEC-028) -- but they have to travel WITH the span, because
# ``snap`` rewrites b0/b1 while growing and trimming. A caller that tried to
# look the description back up by the ids it sent in missed on every candidate
# whose boundary moved, which is nearly all of them.
#
# Appended last, with defaults, so the five-positional-field construction in
# tests and callers keeps working.
Span = namedtuple("Span", "start end b0 b1 score gist kind", defaults=("", ""))

# ``span`` is None when the candidate could not be made to fit; ``reason`` then
# says why, so the pipeline can print it instead of silently dropping a moment.
SnapResult = namedtuple("SnapResult", "span reason")

DEFAULT_LEAD_IN = 0.15
DEFAULT_TAIL = 0.35
# A clip should not end the instant the last word does; a beat of air lets the
# punchline land. Capped so a long silence after the payoff is not included.
MAX_TAIL = 0.85


def snap(b0, b1, beats, preset, *, lead_in=DEFAULT_LEAD_IN, tail=DEFAULT_TAIL,
         score=0, gist="", kind=""):
    """Turn a beat range into a :class:`Span`, or explain why it cannot be one.

    Growing prefers **forward**: a candidate that is too short usually stops
    before its own payoff, and the sentence after it is more often the
    completion than the sentence before is a better opening.

    Trimming removes beats from the **start**, never the end. The payoff of a
    short-form clip is its last line; trimming from the end to fit a duration
    window produces a clip that stops before the reason it was chosen.

    *gist* and *kind* are passed straight into the result and never consulted.
    """
    by_id = {beat["i"]: beat for beat in beats}
    if b0 not in by_id or b1 not in by_id:
        return SnapResult(None, f"beat #{b0 if b0 not in by_id else b1} does not exist")
    if b1 < b0:
        b0, b1 = b1, b0

    ids = sorted(by_id)
    first, last = ids[0], ids[-1]

    def raw(lo, hi):
        return by_id[hi]["end"] - by_id[lo]["start"]

    # --- grow toward the target ------------------------------------------
    while raw(b0, b1) < preset.target:
        grew = False
        if b1 < last and raw(b0, b1 + 1) <= preset.max:
            b1 += 1
            grew = True
        elif b0 > first and raw(b0 - 1, b1) <= preset.max:
            b0 -= 1
            grew = True
        if not grew:
            break

    # --- trim from the start, keeping the payoff --------------------------
    while raw(b0, b1) > preset.max and b0 < b1:
        b0 += 1

    padded_start, padded_end = pad(b0, b1, beats, lead_in=lead_in, tail=tail)
    duration = padded_end - padded_start

    if duration < preset.min:
        return SnapResult(
            None,
            f"only {duration:.1f}s of speech here; {preset.name} needs "
            f"{preset.min:.0f}s and there is nothing adjacent to grow into",
        )
    if duration > preset.max:
        return SnapResult(
            None,
            f"{duration:.1f}s is longer than {preset.name} allows "
            f"({preset.max:.0f}s) and it cannot be trimmed on a sentence boundary",
        )

    return SnapResult(Span(padded_start, padded_end, b0, b1, score, gist, kind), "")


def pad(b0, b1, beats, *, lead_in=DEFAULT_LEAD_IN, tail=DEFAULT_TAIL):
    """``(start, end)`` for beats *b0*..*b1* with breathing room on both sides.

    Padding is clamped to the neighbouring beats, so a clip never opens on the
    tail of the previous sentence or closes on the head of the next one — which
    is the single most recognisable sign of an auto-generated cut.
    """
    by_id = {beat["i"]: beat for beat in beats}
    start = by_id[b0]["start"]
    end = by_id[b1]["end"]

    prev_beat = by_id.get(b0 - 1)
    lead = lead_in if prev_beat is None else min(lead_in, max(0.0, start - prev_beat["end"]))
    start = max(0.0, start - lead)

    next_beat = by_id.get(b1 + 1)
    if next_beat is None:
        end += min(tail, MAX_TAIL)
    else:
        room = max(0.0, next_beat["start"] - end)
        end += min(tail, room, MAX_TAIL)

    return start, end


def overlap_ratio(a, b):
    """Shared seconds as a fraction of the SHORTER span.

    Measured against the shorter one on purpose: three seconds shared with a
    60-second clip is incidental, while three seconds shared with a 15-second
    clip is most of its hook.
    """
    shared = min(a.end, b.end) - max(a.start, b.start)
    if shared <= 0:
        return 0.0
    shortest = min(a.end - a.start, b.end - b.start)
    return shared / shortest if shortest > 0 else 1.0


def dedupe(spans, *, max_overlap_ratio=0.15):
    """Keep the best-scoring spans that do not substantially repeat each other.

    Greedy by score, because the alternative — an exact maximum-weight
    independent set — optimises a number nobody asked for, and a viewer cannot
    tell the difference between the greedy answer and the optimal one.

    A small overlap is allowed rather than none: adjacent beats routinely share
    a fraction of a second of padding, and rejecting on that would throw away
    genuinely distinct clips.
    """
    kept = []
    for span in sorted(spans, key=lambda s: (-s.score, s.start)):
        if all(overlap_ratio(span, other) <= max_overlap_ratio for other in kept):
            kept.append(span)
    return sorted(kept, key=lambda s: s.start)


def snap_all(candidates, beats, preset, *, on_reject=None, **kwargs):
    """Snap every candidate, dropping the ones that cannot fit.

    A *candidate* is either a ``(b0, b1, score)`` triple or a mapping carrying
    ``b0``/``b1``/``score`` plus the model's own ``gist`` and ``kind``. Both
    shapes are accepted because the triple is what every caller sent before the
    description travelled with the span, and it remains a perfectly good way to
    ask for a snap when there is no description to carry.

    Rejections are reported to *on_reject* rather than swallowed: a moment the
    model liked and the snapper refused is exactly the kind of thing that
    should be visible in the log.
    """
    spans = []
    for candidate in candidates:
        if isinstance(candidate, dict):
            b0, b1 = candidate["b0"], candidate["b1"]
            extra = {
                "score": candidate.get("score", 0),
                "gist": candidate.get("gist", ""),
                "kind": candidate.get("kind", ""),
            }
        else:
            b0, b1, score = candidate
            extra = {"score": score}

        result = snap(b0, b1, beats, preset, **extra, **kwargs)
        if result.span is None:
            if on_reject is not None:
                on_reject(b0, b1, result.reason)
            continue
        spans.append(result.span)
    return spans

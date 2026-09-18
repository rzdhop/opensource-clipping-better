"""
web.api.signals — read the retry ladder out of the pipeline's own output.

Every line the pipeline prints already reaches the dashboard as an activity
event, and the most recent one is shown as the current step's detail. Two lines
deserve more than that, because they are the whole difference between "stuck"
and "working": the NVIDIA and Gemini retry counters.

``analyze_with_gemini`` can legitimately sit on one call for forty minutes —
ten attempts on a 60s, 90s, 120s… backoff ladder, then a fallback model — and
the job holds at 36% the entire time. A user who can see *attempt 3 of 10*
knows to wait. A user staring at an unmoving bar kills the job.

This module is deliberately the ONLY place in ``web/`` that matches on the
pipeline's wording. If one of those prints is reworded the counter simply stops
appearing and the line is still shown verbatim in the feed: the feature
degrades, nothing breaks.
"""

from __future__ import annotations

import re
from typing import Optional

# engine.py:887   "   🔁 NVIDIA attempt 2/3..."
# engine.py:324   "[Gemini] Attempt 3/10..."
# engine.py:343   "[Gemini] Attempt 3/10 failed | status=503 | ..."
_ATTEMPT = re.compile(r"attempt\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)

# Sanity bound. A four-digit "attempt" is a false positive from some other line
# that happens to contain the word, not a retry ladder.
_MAX_PLAUSIBLE_ATTEMPTS = 100


# A progress bar redraws by printing the whole line again with a new number.
# Requiring a percent sign is what keeps the retry counters out of this: they
# carry numbers but never a percentage, and collapsing "attempt 1/3" into
# "attempt 2/3" would destroy the one thing the feed exists to show.
_PERCENT = re.compile(r"\d+\s*%")


def _bar_identity(line: str) -> Optional[str]:
    """What this progress bar IS, with its moving parts removed.

    Everything before the percentage, which is the label — `⏳ Rank 1 Main -
    Face analysis:`. Not a digit-blind normalization: that would make Rank 1 and
    Rank 2 the same bar and fold a whole clip's progress into the previous
    clip's. Everything after the percentage (ffmpeg's `| 00:00:04 / 00:00:12`)
    moves too, so it plays no part in the identity either.
    """
    match = _PERCENT.search(line)
    if match is None:
        return None
    return line[: match.start()].rstrip()


def is_progress_redraw(previous: Optional[str], line: str) -> bool:
    """Whether ``line`` is the previous progress bar, one tick later.

    The pipeline prints its bars with a plain ``print`` rather than a carriage
    return — `⏳ Rank 1 Main - Face analysis:  18%` — so one clip emits hundreds
    of lines that differ only in the number. Unchecked they were 90% of the feed
    and evicted everything worth reading from the ring buffer.
    """
    if not previous:
        return False
    identity = _bar_identity(line)
    return identity is not None and identity == _bar_identity(previous)


def attempt_from(line: str) -> Optional[tuple[int, int]]:
    """``(attempt, max_attempts)`` if this line is a retry counter, else None."""
    match = _ATTEMPT.search(line)
    if not match:
        return None
    attempt, total = int(match.group(1)), int(match.group(2))
    if not 0 < attempt <= total <= _MAX_PLAUSIBLE_ATTEMPTS:
        return None
    return attempt, total

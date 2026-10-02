"""What a free tier pushing back means, chain-agnostic (DEC-168).

Lifted out of ``assets.py`` (phase 5, T2-P5-F4) so the ``cast`` step's own
pacing of held-back sheets and voice samples reads the same rule the
``assets`` step's shots and lines already do -- byte-identical, not a second
copy that could drift. ``assets.py`` re-imports every name here under its own
name, so nothing that already reaches it as ``assets.RATE_LIMIT_PAUSE_S``,
``assets.is_rate_limit``, ``assets._paid_sent`` or ``assets.rate_limited_by``
moves.

:func:`rate_limited_by` is the one a caller uses: given the chain failures of
one held-back item (a shot's image, a line's voice, a character's sheet or
sample -- each a ``NoRunnableLink.failures``-shaped list of ``(label,
reason)`` pairs), it names the free-tier provider that held the item back, or
None when the item is not one to ask again (a paid link got past its gates,
every failure was something else).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import re

from clipping.providers import generation as gen, prompt_limits
from clipping.providers.registry import Link

from .. import imaging

# The pause before a free link that pushed back is asked again: Pollinations
# refills about one image a minute, Gemini's speech quota is per minute
# (Tier-2, 2026-09-28).
RATE_LIMIT_PAUSE_S = 60

# A chain failure's reason is ``"<ExceptionName>: <message>"``; an HTTP
# answer's message starts ``HTTP <status> from <url>`` (``transport.
# HttpStatusError``). Only that head is read: a detail further on is the
# provider's own text.
_FAILURE_HEAD = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*): (?:HTTP (?P<status>\d{3})\b)?")
# Why the runner passed a paid link by before sending anything: its route,
# no adapter, no key (``run_generation_chain``'s skips), ``allow_paid`` off,
# a cap's refusal (``budget.check``), a prompt over the link's size limit
# (``prompt_limits``).
_UNSENT = ("route is ", "no adapter yet", "no API key", imaging.PAID_OFF, "refused: ",
           prompt_limits.REFUSAL_HEAD)


def _failure_link(label):
    provider, _, model = str(label or "").partition("/")
    return Link(provider, model) if model and provider in gen.GEN_PROVIDERS else None


def is_rate_limit(label, reason) -> bool:
    """Whether one link's failure (a ``(label, reason)`` pair of
    ``NoRunnableLink.failures``) is a free tier asking to slow down: HTTP 429
    (or an SDK ``RateLimitError``) from a free hosted link, or HTTP 402 from
    ``pollinations`` -- its empty pollen balance, refilled over time. A 402
    anywhere else, any other status, a paid or a local link: not one."""
    link = _failure_link(label)
    if link is None or link.provider == "local" or gen.is_paid(link):
        return False
    head = _FAILURE_HEAD.match(str(reason or ""))
    if head is None:
        return False
    status = int(head.group("status")) if head.group("status") else None
    if status == 429 or head.group("name") == "RateLimitError":
        return True
    return status == 402 and link.provider == "pollinations"


def _paid_sent(label, reason) -> bool:
    """Whether a paid link got past its gates: its request may be billed, and
    a second one could be billed again (DEC-106)."""
    link = _failure_link(label)
    return link is not None and gen.is_paid(link) and not str(reason or "").startswith(_UNSENT)


def rate_limited_by(failures):
    """The provider whose free tier held an item back, or None (pure).

    *failures* are the item's chain failures, ``(label, reason)`` pairs
    (``NoRunnableLink.failures``: one per link tried or skipped). The first
    link whose failure :func:`is_rate_limit` names it -- unless a paid link
    of the same chain got past its gates, which makes the item never asked
    again. Every other failure (no key, not reachable, a paid refusal, a
    spent day) leaves the item to the rate-limited link."""
    pairs = list(failures or ())
    if any(_paid_sent(label, reason) for label, reason in pairs):
        return None
    return next((str(label).partition("/")[0] for label, reason in pairs if is_rate_limit(label, reason)), None)

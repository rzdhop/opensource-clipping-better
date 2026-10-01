"""One image link per episode, and one video link (A-087; AI Story phase 6,
stage 6).

An episode's shots are made on **one** link of its image chain: the first
link that serves one of its images. A provider switch in the middle of an
episode changes how its characters are drawn (A-087: Cloudflare drew the
heroes as flat mascots, Pollinations as humans, in one episode), so a later
shot never falls through to the next link of the chain. A link that pushes
back is waited on through DEC-168's paced rounds; a link that is gone for the
day stops the step and asks whether to switch -- never a silent mix.

The record lives in the episode's ``assets.json`` (``episode_assets_v1``)::

    "links": {"image": {"link": "<provider>/<model>", "since": <ISO time>,
                        "switched_from"?: "<provider>/<model>"},
              "video": {...the same...}}

``link`` is the chain link's label (``registry.describe``), as the shot
records it (``provider``/``model``); a like-for-like model swap of that link
(``generation.FALLBACK_LINKS``, DEC-089) is the same link. ``video`` is the
clips' (stage 8): the first link that serves one of the episode's clips, the
only link every later clip is asked of.

Pure helpers (nothing here reads or writes a document): the assets step does
the wiring (``assets.episode_image_link``, ``assets.image_quote``,
``_Assets.make_image``; for the clips ``_Assets.make_clip``,
``_Assets.keep_video_link`` and ``assets.video_offer``) and
``workflow.patch_assets`` the switch of either link (the video link's since
phase 6 stage 11).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import re

from clipping.providers import generation as gen

from .. import imaging
from .pacing import is_rate_limit

# The slots of ``assets.json``'s ``links``.
IMAGE, VIDEO = "image", "video"
KINDS = (IMAGE, VIDEO)

# Why a gate turned the link away before sending anything: its route, no
# adapter, no key (``run_generation_chain``'s skips), ``allow_paid`` off, a
# cap's refusal (``budget.check``), the day's free allowance (``limits``).
_GATE_HEADS = ("route is ", "no adapter yet", "no API key", imaging.PAID_OFF, "refused: ", "daily allowance spent")
# A failure the attempt itself raised: ``"<ExceptionName>: <message>"``, an
# HTTP answer's message starting ``HTTP <status> from <url>``.
_ATTEMPT_HEAD = re.compile(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*): (?:HTTP (?P<status>\d{3})\b)?")
_KEY_REFUSED = ("AuthenticationError", "PermissionDeniedError")


def label(provider, model):
    """``provider/model``, the link a shot's image was made on; None when
    either is not recorded."""
    if not provider or not model:
        return None
    return f"{provider}/{model}"


def family(link) -> tuple:
    """*link* and its like-for-like model swaps (``FALLBACK_LINKS``): what
    counts as that link."""
    return (link,) + tuple(gen.FALLBACK_LINKS.get(link, ()))


def on_link(served, link) -> bool:
    """Whether an image made on *served* was made on *link*."""
    return served is not None and served in family(link)


def head_of(served, chain_labels) -> str:
    """The chain link *served* answered for: itself when it is a link of the
    chain, else the first chain link it is a model swap of, else itself."""
    chain_labels = list(chain_labels or ())
    if served in chain_labels:
        return served
    return next((link for link in chain_labels if served in family(link)), served)


def recorded(doc, kind=IMAGE):
    """The episode's ``links.<kind>`` record in *doc* (``assets.json``), or None."""
    entry = ((doc or {}).get("links") or {}).get(kind)
    return entry if isinstance(entry, dict) and entry.get("link") else None


def record(link, *, now, switched_from=None) -> dict:
    """A ``links.<kind>`` record: *link* since *now*, and the link it
    replaced when it was switched to."""
    entry = {"link": link, "since": now}
    if switched_from and switched_from != link:
        entry["switched_from"] = switched_from
    return entry


def gone_why(failures, link):
    """Why *link* cannot serve today, read off one request's chain failures
    (``NoRunnableLink.failures``: ``(label, reason)`` pairs), or None when
    it may still serve -- a free tier that pushed back
    (``pacing.is_rate_limit``: waited on, never given up for another link),
    or a failure of this one request (a 5xx, a timeout, a bad answer).

    Gone: a gate turned it away (no key, the day's free allowance spent,
    ``allow_paid`` off, a cap, its route, no adapter), the provider refused
    its key (HTTP 401/403), or -- a local link -- its server did not answer
    the runner's probe."""
    own = family(link)
    pairs = [(str(name), str(reason or "")) for name, reason in failures or () if str(name) in own]
    if any(is_rate_limit(name, reason) for name, reason in pairs):
        return None
    for name, reason in pairs:
        if reason.startswith(_GATE_HEADS):
            return reason
        head = _ATTEMPT_HEAD.match(reason)
        if head is not None and (head.group("status") in ("401", "403") or head.group("name") in _KEY_REFUSED):
            return reason
        if name.startswith("local/") and head is None:
            # The runner's probe note ("unreachable at ..."): no attempt was made.
            return reason
    return None


def _and(items) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _images(count) -> str:
    return f"{count} image{'' if count == 1 else 's'}"


def _clips(count) -> str:
    return f"{count} clip{'' if count == 1 else 's'}"


class StickyLinkGone(Exception):
    """The episode's image link -- or, *kind* :data:`VIDEO`, its video link
    (stage 8) -- cannot serve today, so the step stops and asks (DEC-117's
    "stop and ask" shape, ``refimages.NeedsEditor``): the link, why, the
    next link of the chain that could run, the shots a switch makes again
    (those the old link served) with the ones still to make, and what that
    would cost. ``str()`` is the sentence; :meth:`as_dict` the structured
    offer a caller shows (``switch`` is the assets edit that takes it:
    ``{"links": {"image" | "video": next_link}}`` -- the video link's since
    phase 6 stage 11)."""

    def __init__(self, *, ep, link, why, chain, next_link=None, next_route=None, next_reason=None, redo=(),
                 todo=(), est_usd=0.0, paid=False, before_any_call=True, kind=IMAGE):
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}, not {kind!r}")
        self.kind = kind
        self.ep = ep
        self.link = link
        self.why = why
        self.chain = chain
        self.next_link = next_link
        self.next_route = next_route
        self.next_reason = next_reason
        self.redo = list(redo)
        self.todo = list(todo)
        self.est_usd = round(float(est_usd or 0.0), 4)
        self.paid = bool(paid)
        self.before_any_call = before_any_call
        super().__init__(self.sentence())

    @property
    def qty(self) -> int:
        return len(self.redo) + len(self.todo)

    def switch(self):
        """The assets edit that switches the episode to the next link, or None
        when no other link can run."""
        return {"links": {self.kind: self.next_link}} if self.next_link else None

    def sentence(self) -> str:
        if self.kind == VIDEO:
            return self._video_sentence()
        head = (f"Episode {self.ep}'s image link {self.link} cannot serve now: {self.why}. An episode keeps its "
                "shots on one link, so no other link was tried")
        head += (": nothing was generated or spent." if self.before_any_call
                 else " for the shots left, and they are left as failed.")
        if self.next_link is None:
            return (f"{head} Bring it back and run the assets step again; no other link of {self.chain} can run "
                    f"now either{f' ({self.next_reason})' if self.next_reason else ''}.")
        price = "$0.00" if self.next_route in ("free", "local") else f"est ${self.est_usd:.3f}"
        where = {"free": " (free)", "local": " (on your own hardware)", "paid": " (paid)"}.get(self.next_route, "")
        what = []
        if self.redo:
            many = len(self.redo) > 1
            what.append(f"shot{'s' if many else ''} {_and(self.redo)}, made on {self.link}, "
                        f"{'are' if many else 'is'} made again")
        if self.todo:
            what.append(f"the {len(self.todo)} still to make")
        plan = f": {' with '.join(what)} -- {_images(self.qty)} on {self.next_link}{where}, {price}" if what else ""
        return (f"{head} Bring it back and run the assets step again, or switch the episode's image link to "
                f"{self.next_link} (the assets edit {{\"links\": {{\"image\": \"{self.next_link}\"}}}}){plan}.")

    def _video_sentence(self) -> str:
        head = (f"Episode {self.ep}'s video link {self.link} cannot serve now: {self.why}. An episode keeps its "
                "clips on one link, so no other link was tried")
        head += (": nothing was generated or spent." if self.before_any_call
                 else " for the clips left, and they are left as failed.")
        if self.next_link is None:
            return (f"{head} Bring it back and run the assets step again; no other link of {self.chain} can run "
                    f"now either{f' ({self.next_reason})' if self.next_reason else ''}.")
        price = "$0.00" if self.next_route in ("free", "local") else f"est ${self.est_usd:.3f}"
        where = {"local": " (on your own hardware)", "paid": " (paid)"}.get(self.next_route, "")
        what = []
        if self.redo:
            many = len(self.redo) > 1
            what.append(f"shot{'s' if many else ''} {_and(self.redo)}, animated on {self.link}")
        if self.todo:
            what.append(f"the {len(self.todo)} still to animate")
        plan = f" ({' with '.join(what)}: {_clips(self.qty)}, {price})" if what else ""
        return (f"{head} Bring it back and run the assets step again, or switch the episode's video link to "
                f"{self.next_link}{where} (the assets edit {{\"links\": {{\"video\": \"{self.next_link}\"}}}})"
                f"{plan}; an episode's clips move to another link only when you choose it.")

    def as_dict(self) -> dict:
        return {"kind": self.kind, "link": self.link, "why": self.why, "chain": self.chain,
                "next_link": self.next_link, "next_route_class": self.next_route, "redo": list(self.redo),
                "todo": list(self.todo), "qty": self.qty, "est_usd": self.est_usd, "paid": self.paid,
                "switch": self.switch(), "message": str(self)}

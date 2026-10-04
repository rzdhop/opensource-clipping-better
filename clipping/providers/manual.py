"""The human as a provider: ``manual/upload`` (plan 22 stage 5).

A story whose clips (or images) are the human's own -- made on their own
Google Flow or Higgsfield subscription from the app's shot brief, then
uploaded -- names ``manual/upload`` where another story names a hosted link.
Everything that reads a link (the plan, the estimate, the sticky links, the
brief) reads it as any other; nothing ever calls it:

* ``estimate`` is $0 (never paid, no key, no allowance);
* ``probe`` reaches nothing (there is nothing to reach);
* ``generate`` raises :class:`AwaitingUpload` -- it never sends a request and
  never books a cent (RC-N4). ``generation.run_generation_chain`` lets that
  exception end the chain: a later link never stands in for the human.

The file itself arrives through an upload route
(``clipping.aistory.manual_uploads``), which validates it and records it as
the step would have recorded a made one, with ``link: manual/upload``.

Stdlib only (DEC-012).
"""

from __future__ import annotations

from .generation import IMAGE, IMAGE_EDIT, MANUAL, MANUAL_KINDS, MANUAL_LINK, VIDEO, AwaitingUpload, register_adapter

__all__ = ("AwaitingUpload", "ManualAdapter", "MANUAL_LINK", "MANUAL")


class ManualAdapter:
    """``(kind, "manual")``: the adapter protocol, answering nothing."""

    provider = MANUAL
    # Nothing goes through a transport: the runner's sent-request counter
    # (``generation._Sent``) never wraps it.
    speaks_through_transport = False

    def __init__(self, kind):
        self.kind = kind

    def estimate(self, link, request):
        """$0.00: the human's own subscription pays, never this app."""
        return 0.0

    def probe(self, link, *, credentials=None, **_):
        return True, "your own upload: nothing to reach"

    def generate(self, link, request, *, credentials=None, on_log=None, transport=None, **_):
        """Never sends anything: the file is the human's to make and upload."""
        raise AwaitingUpload(self.kind, (getattr(request, "extra", None) or {}).get("target"))


MANUAL_ADAPTERS = {kind: ManualAdapter(kind) for kind in MANUAL_KINDS}
assert set(MANUAL_ADAPTERS) == {IMAGE, IMAGE_EDIT, VIDEO}

for _kind, _adapter in MANUAL_ADAPTERS.items():
    register_adapter(_kind, MANUAL, _adapter)

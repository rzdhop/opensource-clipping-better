"""Shared name-redaction helper (spec 2.3, 8.1): no entity name of a story
ever reaches an image prompt or a regenerate note.

Lifted out of ``refimages.py`` (phase 2, spec 8.1) so ``shots.py`` (phase 3,
pure, stdlib-only, no I/O) can strip names from a resolved shot prompt too,
without importing the imaging/generation stack that ``refimages.py`` needs.
``refimages._without_names`` now calls :func:`without_names` below with
identical behaviour (RC-E5): its own tests stay unedited.

Stdlib only (DEC-012).
"""

from __future__ import annotations

import re


def without_names(text, names, *, default_word="the character") -> str:
    """*text* with every name of *names* replaced, longest first, as a whole
    word and whatever its case.

    *names* is ``{name: neutral word}``, or an iterable of names -- each then
    mapped to *default_word* (``refimages.py`` passes its own
    ``NEUTRAL_WORDS[CHARACTERS]``, "the character", to keep its previous
    behaviour exactly; a caller with several entity kinds passes a full
    ``{name: neutral word}`` map instead, one neutral word per kind).
    """
    if not isinstance(names, dict):
        names = {name: default_word for name in names}
    words = {}
    for name, word in names.items():
        key = str(name).strip() if name else ""
        if key:
            words.setdefault(key, word)
    for name in sorted(words, key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", words[name], text, flags=re.IGNORECASE)
    return text

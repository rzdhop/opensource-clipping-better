"""Universes at work (plan 23 stage D2): the deterministic species rotation
of a batch of concept cards, the data block that carries a universe into the
concept prompt, and the brand check of the validators.

A universe (``templates/universes.json``) says what a story's cast is made
of. It enters a prompt only when the story has one (``media_policy.universe``):
a story without one is written exactly as it always was (RC-W2).

Stdlib plus the package's own ``schemas``/``templates`` (DEC-012).
"""

from __future__ import annotations

import hashlib

from . import media_policy, schemas, templates

# How many cards one "Generate 10 more" writes, and so how many lead species a
# batch hands out (``prompts.C1_CALLS``; kept here so this module imports no prompt).
BATCH_SIZE = 10

BRAND_DENYLIST = schemas.BRAND_DENYLIST


def _rank(story_id, batch, cycle, species) -> str:
    return hashlib.sha256(f"{story_id}:{batch}:{cycle}:{species}".encode("utf-8")).hexdigest()


def assign_species(story_id, batch, pool, size=BATCH_SIZE) -> list:
    """The lead species of the *size* cards of batch number *batch* of story
    *story_id*, one per card in card order: *pool* ordered by
    ``sha256(story_id + batch + species)``. A species appears once while the
    pool allows (a pool shorter than *size* starts a fresh ordering when it
    runs out, never opening with the species the last one ended on). Pure: the same story, batch and pool always give the same list,
    on any Python (no ``random``)."""
    pool = list(pool)
    if not pool:
        return []
    out = []
    cycle = 0
    while len(out) < size:
        ordered = sorted(pool, key=lambda species: _rank(story_id, batch, cycle, species))
        if out and len(ordered) > 1 and ordered[0] == out[-1]:
            ordered.append(ordered.pop(0))  # never the same species twice in a row across a fresh ordering
        out.extend(ordered[:size - len(out)])
        cycle += 1
    return out


def card_slot(existing_cards, call) -> tuple:
    """``(batch, position)`` of the card the *call*-th call of a run writes
    when the story already has *existing_cards* cards: card number
    ``existing_cards + call - 1`` (0-based) in batches of :data:`BATCH_SIZE`,
    so a run's cards and an agent's one-card runs rotate alike."""
    index = existing_cards + call - 1
    return index // BATCH_SIZE, index % BATCH_SIZE


def species_block(universe, *, batch_species, position) -> str:
    """The data block of one C1v2 call (plan 23 stage D2): the universe, its
    species pool, the lead species of the batch's cards in order and this
    card's. *universe* is a ``templates/universes.json`` entry (or a lock's
    ``universe`` record); *batch_species* the batch's assigned lead species
    (:func:`assign_species`); *position* this card's 0-based place in it."""
    label = universe["label"]
    label = label.get("en") if isinstance(label, dict) else label
    lead = batch_species[position]
    order = ", ".join(f"{i} {species}" for i, species in enumerate(batch_species, start=1))
    return (
        f"Universe: {label} -- every character is {universe['subject_phrase']}.\n"
        f"Species pool: {', '.join(universe['species'])}.\n"
        f"Lead species of this batch's cards, in order: {order}.\n"
        f"This card's lead species: {lead}. Where the brief already says what a character is, keep the "
        "brief's; otherwise the lead is this species and every other character takes a different species "
        "of the pool. Give each character its species."
    )


def brand_gate(story, reply) -> list:
    """The told-why errors for the brands *reply* names -- empty when the
    story has no universe (a story without one is checked as it always was).
    One call in every validator of a text prompt (C1v2, K1, D2, P1, R1,
    R1v2, D3)."""
    if media_policy.universe(story, explicit=True) is None:
        return []
    return schemas.brand_errors(reply)


def universe_of(story):
    """The ``templates/universes.json`` entry *story* chose
    (``media_policy.universe``, explicit only), or None."""
    chosen = media_policy.universe(story, explicit=True)
    return templates.universe(chosen) if chosen else None

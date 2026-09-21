"""Turn a clip's slim metadata into the render layer's richer fields, in Python.

Everything here was previously asked of the model, at roughly 1200 output tokens
per clip. None of it needs a model:

* ``typography_plan`` is a styling decision over words the model already named.
* ``broll_list`` is placement arithmetic over queries it already wrote.
* ``keep_segments`` is set subtraction over beat ids.
* ``hook_v2`` is picking the shortest, densest beats.
* ``bgm_mood`` is validating one enum.

Deriving them also makes them *correct*, which asking did not. The old prompt
asked the model to emphasise words from the transcript and to keep b-roll out of
the hook window; nothing checked either. Here an emphasis word that is not
actually spoken is dropped, and b-roll that would land on the hook is moved.

Stdlib only.
"""

from __future__ import annotations

import string

from . import beats as beats_mod

MOODS = ("chill", "epic", "sad", "upbeat", "suspense")
DEFAULT_MOOD = "chill"

# Matches clipping/studio/subtitles.py:94 and :247 exactly. If that changes,
# emphasis silently stops matching and every word renders unstyled.
def normalize_word(word):
    """``"Sure,"`` -> ``"sure"``. The render layer's own normalization."""
    return str(word).lower().strip(string.punctuation)


# ------------------------------------------------------------------ typography

SCALE_LEVELS = (3, 2, 2, 1, 1, 1)
ANIMATIONS = ("bounce_pop", "stagger_up")


def typography_plan(emphasis, clip_words, *, max_words=6):
    """Style the words the model asked to emphasise, keeping only real ones.

    A word the model invented cannot be matched by ``buat_file_ass`` and would
    simply render unstyled, so the failure was previously invisible. Dropping it
    here makes the plan honest about what it will actually do.

    Scale is assigned by rarity: the least common word in the clip carries the
    most weight, which is a better proxy for "the word that matters" than the
    order the model happened to list them in.
    """
    spoken = {}
    for word in clip_words:
        clean = normalize_word(word.get("word", ""))
        if clean:
            spoken[clean] = spoken.get(clean, 0) + 1

    seen = set()
    matched = []
    for raw in emphasis or []:
        clean = normalize_word(raw)
        if not clean or clean in seen or clean not in spoken:
            continue
        seen.add(clean)
        matched.append(clean)

    # Rarest first, ties broken by length (a longer word carries more weight),
    # then alphabetically so the result is deterministic.
    matched.sort(key=lambda w: (spoken[w], -len(w), w))

    plan = []
    for idx, word in enumerate(matched[:max_words]):
        scale = SCALE_LEVELS[idx] if idx < len(SCALE_LEVELS) else 1
        plan.append(
            {
                "kata_utama": word,
                "scale_level": scale,
                "style": "khusus" if scale >= 2 else "utama",
                "animasi": ANIMATIONS[idx % len(ANIMATIONS)],
            }
        )
    return plan


# ----------------------------------------------------------------------- hook

def hook_window(hook_beat, clip_beats, *, clip_start, clip_end, hook_duration):
    """``(start, end)`` for the teaser, clamped inside the clip.

    Falls back to the clip's opening seconds when the model names a beat outside
    the clip — which is the only thing it can get wrong here, and is not worth
    failing a clip over.
    """
    by_id = {beat["i"]: beat for beat in clip_beats}
    beat = by_id.get(hook_beat)
    if beat is None:
        start = clip_start
    else:
        start = max(clip_start, float(beat["start"]))

    duration = max(0.5, float(hook_duration))
    end = min(clip_end, start + duration)
    if end <= start:
        start = clip_start
        end = min(clip_end, clip_start + duration)
    return start, end


def hook_v2_items(clip_beats, *, want=3, max_seconds=2.5):
    """Micro-hook intro cards: the shortest beats with the most going on.

    Only built when ``--hook-v2`` is on. When it is off the key is omitted
    entirely, so ``studio/core.py``'s own auto-chunking fallback applies.
    """
    usable = [b for b in clip_beats if b["end"] - b["start"] <= max_seconds]
    if len(usable) < 2:
        usable = sorted(clip_beats, key=lambda b: b["end"] - b["start"])[:want]

    chosen = sorted(usable, key=lambda b: -b["n_words"])[:want]
    chosen.sort(key=lambda b: b["start"])

    items = []
    for beat in chosen:
        words = beat["text"].split()[:3]
        items.append(
            {
                "start_time": float(beat["start"]),
                "end_time": float(beat["end"]),
                "text": " ".join(words).upper(),
            }
        )
    return items


# --------------------------------------------------------------------- b-roll

BROLL_SECONDS = 4.0


def broll_list(queries, clip_beats, *, clip_start, clip_end, hook_end, enabled=True):
    """Place each stock-footage query on a beat, never over the hook.

    The old prompt asked the model not to put b-roll inside the hook window and
    nothing verified it. Here a placement that would collide is moved to the
    next beat that starts after the hook, or dropped if there is none — the hook
    is the three seconds that decide whether anyone watches at all.
    """
    if not enabled or not queries:
        return []

    candidates = [b for b in clip_beats if b["start"] >= hook_end]
    if not candidates:
        return []

    out = []
    used = 0
    for query in queries:
        text = str(query or "").strip()
        if not text:
            continue
        # Space them out: every other beat, so two b-rolls never touch.
        idx = min(used * 2, len(candidates) - 1)
        beat = candidates[idx]
        start = max(hook_end, float(beat["start"]))
        end = min(clip_end, start + BROLL_SECONDS)
        if end - start < 1.0:
            continue
        out.append({"start_time": start, "end_time": end, "search_query": text})
        used += 1
        if used * 2 >= len(candidates):
            break
    return out


# -------------------------------------------------------------- keep_segments

MIN_KEPT_SEGMENT = 2.0


def keep_segments(drop_beats, clip_beats, *, clip_start, clip_end):
    """The clip minus the beats the model called dead air.

    Returns ``[]`` — meaning "render the whole clip" — unless the result is at
    least two segments each worth keeping. ``studio/core.py:456`` only activates
    segment trimming for more than one segment, so anything else is a key that
    does nothing, and a sub-two-second fragment is a cut that reads as a glitch.
    """
    drop = {int(b) for b in (drop_beats or [])}
    if not drop or not clip_beats:
        return []

    ids = [b["i"] for b in clip_beats]
    # Never drop the opening or the payoff, whatever the model says.
    drop.discard(ids[0])
    drop.discard(ids[-1])
    if not drop:
        return []

    segments = []
    current = None
    for beat in clip_beats:
        if beat["i"] in drop:
            if current is not None:
                segments.append(current)
                current = None
            continue
        if current is None:
            current = {"start_time": float(beat["start"]), "end_time": float(beat["end"])}
        else:
            current["end_time"] = float(beat["end"])
    if current is not None:
        segments.append(current)

    if segments:
        segments[0]["start_time"] = min(segments[0]["start_time"], clip_start)
        segments[-1]["end_time"] = max(segments[-1]["end_time"], clip_end)

    usable = [s for s in segments if s["end_time"] - s["start_time"] >= MIN_KEPT_SEGMENT]
    return usable if len(usable) > 1 else []


# ----------------------------------------------------------------------- misc

def bgm_mood(mood, *, allowed=MOODS, default=DEFAULT_MOOD):
    """Validate the mood enum. ``studio/core.py`` already defaults the same way."""
    text = str(mood or "").strip().lower()
    return text if text in allowed else default


def hashtags(values, *, want=3):
    """``["#a", "b"]`` -> ``"#a #b"``. 2-3 tags, deduped, lowercase, each with #."""
    out = []
    seen = set()
    for value in values or []:
        tag = str(value or "").strip().lstrip("#").strip()
        if not tag:
            continue
        tag = "".join(ch for ch in tag if ch.isalnum() or ch in "_").lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(f"#{tag}")
        if len(out) >= want:
            break
    return " ".join(out)


def keywords(values, *, low=5, high=8):
    """5-8 deduped keyword strings, in the order given."""
    out = []
    seen = set()
    for value in values or []:
        text = " ".join(str(value or "").split()).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= high:
            break
    return out


def clip_beats_of(all_beats, b0, b1):
    """The beats inside a snapped span, inclusive."""
    return [beat for beat in all_beats if b0 <= beat["i"] <= b1]


def clip_words_of(all_beats, words, b0, b1):
    """The words inside a snapped span."""
    try:
        return beats_mod.words_between(all_beats, words, b0, b1)
    except KeyError:
        return []

"""Sentence-level units built from word timings — the unit the model reasons in.

The old design handed the model raw timestamps and asked it to invent more. It
could, and did, return times that were not in the transcript at all. Beats
remove the possibility: a beat has a **global integer id**, the model only ever
emits ids, and every timestamp in the result is looked up rather than generated.
A hallucinated beat id is a ``KeyError`` at the boundary instead of a clip that
starts mid-syllable or past the end of the video.

Beats also make the request small. The whole transcript at five words per line
is ~600 lines for a 20-minute video; the same content as sentences is ~170.

A beat ends at whichever comes first:
  * sentence-final punctuation on a word,
  * a pause longer than ``max_pause`` (the strongest natural boundary there is,
    and the only one available for a transcript with no punctuation at all —
    which is exactly what scraped auto-captions are),
  * ``max_beat_seconds``, so one unpunctuated monologue cannot become a single
    beat spanning the whole video. This is a soft cap: the cut lands on the
    first word that crosses it, so a beat can exceed it by at most one word's
    duration. Cutting at exactly the threshold would split mid-word for no gain.

Stdlib only.
"""

from __future__ import annotations

# Sentence-final marks across the languages this tool sees. Includes the
# full-width CJK stops and the Arabic question mark, because a transcript in
# those scripts otherwise has no punctuation boundary at all.
SENTENCE_END = ".!?…。！？؟"
# Trailing characters that can sit *after* the real punctuation.
_TRAILING = "\"')]}»”’"

DEFAULT_MAX_PAUSE = 0.6
DEFAULT_MAX_BEAT_SECONDS = 14.0
DEFAULT_MIN_BEAT_SECONDS = 1.2


def flatten_words(data_segmen):
    """Every word in order. Segments are a rendering detail, not a boundary.

    ``data_segmen`` is chunked by ``max_words_per_subtitle`` — five words, by
    default — which is a subtitle-line decision with no relationship to speech.
    Beats are built from the words themselves.
    """
    return [word for segment in data_segmen for word in segment.get("words", [])]


def ends_sentence(word_text):
    """Whether *word_text* carries sentence-final punctuation."""
    stripped = str(word_text).rstrip(_TRAILING)
    return bool(stripped) and stripped[-1] in SENTENCE_END


# Below this share of beats ending in punctuation, a transcript is treated as
# unpunctuated and no inference is drawn from where sentences appear to begin.
# YouTube auto-captions carry none at all, so beats there are cut purely on
# pauses and every one of them looks like a fragment.
PUNCTUATION_THRESHOLD = 0.2


def has_punctuation(beats, *, threshold=PUNCTUATION_THRESHOLD):
    """Whether *beats* carry enough punctuation to reason about sentences.

    The guard this gates is worth having only where the evidence exists. On a
    transcript with no punctuation it would reject every candidate in the
    video — trading a cosmetic defect for an empty result.
    """
    if not beats:
        return False
    ended = sum(1 for beat in beats if ends_sentence(beat.get("text", "")))
    return ended / len(beats) >= threshold


def starts_mid_sentence(beats, b0):
    """Whether beat *b0* clearly opens in the middle of a sentence.

    Two signals, and both have to be safe on their own:

    * The first character is lower case — but only when the script HAS case.
      ``"这"`` and ``"ه"`` are neither upper nor lower, and treating them as
      lower would reject every candidate in every CJK and Arabic transcript.
    * The previous beat did not end in sentence punctuation. Checked second,
      because a sentence genuinely can open in lower case (``iPhone``), and a
      clean stop before it is the stronger evidence.

    Deliberately conservative: it answers False whenever it does not know.
    """
    by_id = {beat["i"]: beat for beat in beats}
    beat = by_id.get(b0)
    if beat is None:
        return False

    text = str(beat.get("text", "")).lstrip()
    if not text:
        return False

    previous = by_id.get(b0 - 1)
    if previous is None:
        # Nothing before it to be in the middle of.
        return False
    if ends_sentence(previous.get("text", "")):
        return False

    first = text[0]
    # An uncased script cannot answer this question either way.
    if first.lower() == first.upper():
        return False
    return first.islower()


def build_beats(
    data_segmen,
    *,
    max_pause=DEFAULT_MAX_PAUSE,
    max_beat_seconds=DEFAULT_MAX_BEAT_SECONDS,
    min_beat_seconds=DEFAULT_MIN_BEAT_SECONDS,
):
    """Turn ``data_segmen`` into a list of beat dicts.

    Each beat is ``{"i", "start", "end", "text", "n_words", "w0", "w1"}`` where
    ``w0``/``w1`` are inclusive indices into the flattened word list, so a caller
    can recover the exact words behind any beat.

    Ids are assigned last and are contiguous from zero, so they stay usable as
    list indices after the short-beat merge.
    """
    words = flatten_words(data_segmen)
    if not words:
        return []

    groups = []
    current = [0]

    for idx in range(len(words)):
        word = words[idx]
        is_last = idx == len(words) - 1

        if is_last:
            groups.append((current[0], idx))
            break

        nxt = words[idx + 1]
        gap = float(nxt["start"]) - float(word["end"])
        span = float(word["end"]) - float(words[current[0]]["start"])

        if ends_sentence(word["word"]) or gap > max_pause or span >= max_beat_seconds:
            groups.append((current[0], idx))
            current[0] = idx + 1

    groups = _merge_short(
        groups,
        words,
        min_beat_seconds=min_beat_seconds,
        max_pause=max_pause,
        max_beat_seconds=max_beat_seconds,
    )

    beats = []
    for i, (w0, w1) in enumerate(groups):
        chunk = words[w0 : w1 + 1]
        beats.append(
            {
                "i": i,
                "start": float(chunk[0]["start"]),
                "end": float(chunk[-1]["end"]),
                "text": " ".join(str(w["word"]).strip() for w in chunk).strip(),
                "n_words": len(chunk),
                "w0": w0,
                "w1": w1,
            }
        )
    return beats


def _merge_short(groups, words, *, min_beat_seconds, max_pause, max_beat_seconds):
    """Fold a beat shorter than *min_beat_seconds* into its predecessor.

    A one-word beat ("Right.") is not a unit anyone would cut on, and leaving it
    in inflates the id space the model has to read. It is merged backwards,
    because a short reaction belongs to the line it answers.

    Two refusals matter more than the merge itself, and both were found by
    measuring a real transcript rather than by reading the code:

    * **Never merge across a pause.** A short beat separated from its
      predecessor by 18 seconds of silence is not a continuation of it. Merging
      anyway produced a 25.3-second "beat" whose text jumped between two
      unrelated scenes — the exact boundary the pause rule had just drawn
      correctly.
    * **Never exceed ``max_beat_seconds``.** Otherwise a run of short beats
      cascades into one long one, and the cap that keeps a monologue from
      becoming a single beat stops meaning anything.

    A short beat that fails both tests simply stays short. That is the right
    outcome: it is a real, isolated utterance.
    """
    if not groups:
        return groups

    def span(w0, w1):
        return float(words[w1]["end"]) - float(words[w0]["start"])

    def gap(prev_w1, next_w0):
        return float(words[next_w0]["start"]) - float(words[prev_w1]["end"])

    merged = [list(groups[0])]
    for w0, w1 in groups[1:]:
        prev = merged[-1]
        too_short = span(w0, w1) < min_beat_seconds or span(*prev) < min_beat_seconds
        can_merge = (
            gap(prev[1], w0) <= max_pause
            and span(prev[0], w1) <= max_beat_seconds
        )
        if too_short and can_merge:
            prev[1] = w1
        else:
            merged.append([w0, w1])

    return [tuple(pair) for pair in merged]


def render_beats(beats, lo=0, hi=None):
    """Format beats ``lo..hi`` (inclusive) for a prompt.

    ``#12 [612.4-618.9] the sentence text`` — the id first, because the id is
    the only thing the model is allowed to answer with.
    """
    hi = len(beats) - 1 if hi is None else hi
    lines = []
    for beat in beats:
        if lo <= beat["i"] <= hi:
            lines.append(
                f"#{beat['i']} [{beat['start']:.1f}-{beat['end']:.1f}] {beat['text']}"
            )
    return "\n".join(lines)


def windows(beats, *, size=45, overlap=5):
    """Split beat ids into overlapping ``(lo, hi)`` ranges, inclusive.

    Overlap exists so a moment straddling a window boundary is visible whole to
    at least one request. Without it the single best clip in a video can be the
    one nobody sees.
    """
    if not beats:
        return []
    last = beats[-1]["i"]
    if size <= 0:
        return [(beats[0]["i"], last)]

    step = max(1, size - max(0, overlap))
    out = []
    lo = beats[0]["i"]
    while lo <= last:
        hi = min(lo + size - 1, last)
        out.append((lo, hi))
        if hi >= last:
            break
        lo += step
    return out


def span_of(beats, b0, b1):
    """``(start, end)`` covering beats *b0*..*b1* inclusive. Raises on a bad id.

    Raising is the point: this is the boundary where a hallucinated id becomes a
    caught error rather than a clip cut from nowhere.
    """
    by_id = {beat["i"]: beat for beat in beats}
    if b0 not in by_id:
        raise KeyError(f"no beat #{b0}")
    if b1 not in by_id:
        raise KeyError(f"no beat #{b1}")
    if b1 < b0:
        b0, b1 = b1, b0
    return by_id[b0]["start"], by_id[b1]["end"]


def words_between(beats, words, b0, b1):
    """The flattened words behind beats *b0*..*b1* inclusive."""
    by_id = {beat["i"]: beat for beat in beats}
    if b0 not in by_id or b1 not in by_id:
        raise KeyError(f"no beat range #{b0}..#{b1}")
    if b1 < b0:
        b0, b1 = b1, b0
    return words[by_id[b0]["w0"] : by_id[b1]["w1"] + 1]

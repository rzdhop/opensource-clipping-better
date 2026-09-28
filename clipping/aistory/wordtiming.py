"""Word timings of one spoken line, in spec 6.4's order of truth (AI Story
phase 4, stage 8; DEC-165).

A line's words are timed, relative to the start of its own audio file, from
the best source there is:

1. **provider** -- the TTS engine's own word timestamps, kept in the line's
   ``line_timing_v1`` sidecar (Edge);
2. **alignment** -- opt-in (``params.align_words`` on the assets step): the
   line's audio transcribed by the STT chain, its words matched to the
   script's with ``difflib``, and every script word the transcription did
   not match placed by interpolation between its matched neighbours
   (:func:`align`);
3. **even_split** -- the line's duration divided evenly over its words,
   labelled "approximate timing" wherever it is shown (:func:`even_split`).

The source is stored per line (``assets.json`` ``lines{lid: {words_source,
aligned_by?}}``); an aligned sidecar says so itself (``words_source:
"alignment"``, ``aligned_by``), so a run that finds its words there never
asks the STT chain again.

Pure: no disk, no clock, no network (DEC-012).
"""

from __future__ import annotations

import difflib
import unicodedata

from . import schemas

PROVIDER, ALIGNMENT, EVEN_SPLIT = schemas.WORD_SOURCES

# The sidecar keys an alignment adds (the TTS adapters never write them).
SIDECAR_SOURCE_KEY = "words_source"
SIDECAR_ALIGNED_BY_KEY = "aligned_by"

_PUNCTUATION = "\"'«»“”‘’.,;:!?…()[]{}-–—"


def tokens(text) -> list:
    """The script's words: *text* split on whitespace (the subtitles' own
    tokenisation, ``render.subtitles._line_word_spans``)."""
    return str(text or "").split()


def normalise(word) -> str:
    """How a script word and a transcribed word are compared: case, accents,
    typographic apostrophes and surrounding punctuation do not count."""
    text = unicodedata.normalize("NFKD", str(word or "")).replace("’", "'").casefold()
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.strip(_PUNCTUATION)


def even_split(text, duration_s) -> list:
    """``[{word, start, end}]``: *duration_s* divided evenly over *text*'s
    words (the approximate timing)."""
    words = tokens(text)
    if not words:
        return []
    duration = max(0.0, float(duration_s or 0.0))
    each = duration / len(words)
    return [{"word": word, "start": round(i * each, 3),
             "end": round(duration if i == len(words) - 1 else (i + 1) * each, 3)}
            for i, word in enumerate(words)]


def _spans(stt_words, duration) -> list:
    """Each transcribed word's ``(start, end)``, clamped into the line's
    audio and never going back in time."""
    spans, floor = [], 0.0
    for word in stt_words:
        try:
            start, end = float(word["start"]), float(word["end"])
        except (KeyError, TypeError, ValueError):
            start = end = floor
        start = min(max(start, floor), duration)
        end = min(max(end, start), duration)
        spans.append((start, end))
        floor = start
    return spans


def align(text, stt_words, duration_s):
    """*text*'s words timed from a transcription of the line's own audio, or
    None when not one word of it matches (the caller falls back to
    :func:`even_split`).

    *stt_words* is ``[{word, start, end}]`` in seconds from the start of the
    line's audio (``stt.transcribe``'s words). The script's words and the
    transcription's are compared normalised (:func:`normalise`) by
    ``difflib.SequenceMatcher``; a matched script word takes its transcribed
    word's span. A run of script words with no match is spread evenly over
    the gap between the matched words around it -- from 0.0 before the
    first match, to *duration_s* after the last. Every span lies inside
    ``[0, duration_s]`` and no word starts before the previous one.
    """
    words = tokens(text)
    if not words:
        return []
    duration = max(0.0, float(duration_s or 0.0))
    heard = [word for word in (stt_words or ()) if isinstance(word, dict)]
    # A word that normalises to nothing (a dash, an ellipsis) never matches.
    ours = [normalise(word) or f"\0{i}" for i, word in enumerate(words)]
    theirs = [normalise(word.get("word")) or f"\1{j}" for j, word in enumerate(heard)]
    spans = _spans(heard, duration)

    matched = [None] * len(words)
    matcher = difflib.SequenceMatcher(None, ours, theirs, autojunk=False)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            matched[block.a + k] = spans[block.b + k]
    if not any(span is not None for span in matched):
        return None

    timed = []
    last_start = previous_end = 0.0
    i = 0
    while i < len(words):
        if matched[i] is not None:
            start = max(matched[i][0], last_start)
            end = max(matched[i][1], start)
            timed.append({"word": words[i], "start": round(start, 3), "end": round(end, 3)})
            last_start, previous_end = start, end
            i += 1
            continue
        # A run of unmatched words: spread over the gap to the next match.
        j = i
        while j < len(words) and matched[j] is None:
            j += 1
        gap_start = previous_end
        gap_end = max(matched[j][0] if j < len(words) else duration, gap_start)
        each = (gap_end - gap_start) / (j - i)
        for k in range(i, j):
            start = gap_start + (k - i) * each
            end = gap_end if k == j - 1 else gap_start + (k - i + 1) * each
            timed.append({"word": words[k], "start": round(start, 3), "end": round(end, 3)})
            last_start = start
        previous_end = gap_end
        i = j
    return timed


def source_of(sidecar):
    """``(words_source, aligned_by)`` of a line from its sidecar (a
    ``line_timing_v1`` dict, or None): its words are the provider's unless
    an alignment wrote them (and says by which STT link); no words at all is
    an even split."""
    sidecar = sidecar if isinstance(sidecar, dict) else {}
    if not sidecar.get("words"):
        return EVEN_SPLIT, None
    if sidecar.get(SIDECAR_SOURCE_KEY) == ALIGNMENT:
        return ALIGNMENT, sidecar.get(SIDECAR_ALIGNED_BY_KEY)
    return PROVIDER, None


def line_words(text, duration_s, sidecar) -> tuple:
    """``(words, source)`` of one line, in spec 6.4's order: the sidecar's
    words (the provider's, or an alignment's), else the even split."""
    source, _aligned_by = source_of(sidecar)
    if source == EVEN_SPLIT:
        return even_split(text, duration_s), EVEN_SPLIT
    return [dict(word) for word in sidecar["words"]], source


def aligned_sidecar(sidecar, words, aligned_by) -> dict:
    """*sidecar* with the aligned *words* and the two keys that say where
    they came from; everything else (duration, source, voice) unchanged."""
    return dict(sidecar, words=[dict(word) for word in words], **{SIDECAR_SOURCE_KEY: ALIGNMENT,
                                                                  SIDECAR_ALIGNED_BY_KEY: aligned_by})

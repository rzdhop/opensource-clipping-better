"""Pure ASS (Advanced SubStation Alpha) document builders for the AI-Story
renderer (spec 6.4, 6.5; plan phase 4 stage 5, "Renderer" -> subtitles.py;
DEC-159). Every burned text layer -- dialogue subtitles, the hook overlay,
the AI-generated label, the end card and the cover -- is an ASS document
built here in plain Python, then burned by ``render/filtergraph.py``'s
``ass=<file>:fontsdir=<dir>`` (never PIL: DEC-159, "there is no PIL in the
render path").

**The font contract.** Every builder that emits a ``[V4+ Styles]`` block
takes a ``typography`` dict and reads its own ``Fontname`` from
``typography["font_family"]``. That value is never the style template's own
literal ``typography.font_family`` field (e.g. "Montserrat ExtraBold") --
it is whatever ``render/fonts.py``'s :func:`~clipping.aistory.render.fonts.resolve_font`
actually resolved for this render (the shipped fallback's declared family
does not match the template's own request, spec: "the family name ASS
styles must reference, i.e. what libass will match"). The caller (a future
``render/plan.py``, not this module) is responsible for overriding the key
before calling anything here; this module never resolves a font itself.

**Word timing.** ``word_timings`` (when given) is keyed by ``line_id`` ->
the TTS sidecar's own ``line_timing_v1["words"]`` list (``clipping.
providers.tts._write_timing``): ``[{"word", "start", "end"}, ...]``, with
``start``/``end`` in seconds **relative to that one line's own audio**
(each line is one ``edge_tts.Communicate`` call producing one audio file,
so its cues are 0-based on that file, never on the episode). The DISPLAYED
text is always the script's own (:func:`_line_word_spans`, Tier-2,
2026-09-29): a provider's bare word cues are used only for TIMING, matched
to the script's own tokens (punctuation, capitals, apostrophes) by
``wordtiming.align``'s difflib match on normalised words -- never the
provider's own tokenisation verbatim, so a punctuation-dropping provider
never drops it from what is shown. A missing or empty entry
(``SOURCE_DURATION``: "no word cues, the audio duration only") falls back
to an even split of the line's own ``duration_s`` across its
``text.split()`` tokens -- spec 6.4.1c's "approximate timing" -- and that
line's id is reported back in :func:`build_subtitles_ass`'s ``meta`` so a
caller can label it in the UI. Nothing here silently drops the
approximation: the label is a return value, not a log line.

**Time formatting** (``H:MM:SS.cc``, spec: "documented rounding rule").
Every timestamp is rounded to the nearest centisecond, half rounding up
(:func:`_to_centiseconds`), clamped so it is never negative, and an
event's end is bumped by one centisecond over its (rounded) start when
rounding would otherwise collapse a positive-duration span to zero width
-- an event's end is always strictly after its start (:func:`event_time_pair`).

**Escaping** (:func:`escape_ass_text`). ASS has no in-band escape for a
literal ``{``/``}`` (they always delimit an override-tag block, wherever
they appear in the Text field) or for a literal ``\\`` (``\\N``/``\\n``/
``\\h`` are recognised directly in plain text, brace-optional) -- so a
literal occurrence of any of the three is replaced by its fullwidth
Unicode look-alike (``｛``/``｝``/``＼``) rather than dropped or rejected;
this can never inject an override tag or a stray text-level escape.
Backslash is replaced FIRST, so the hard line breaks this function inserts
for a real newline are never themselves re-escaped. French text (accents,
``'``/``’``) is untouched -- UTF-8 throughout, no ASCII folding.

Stdlib only (DEC-012): no PIL import here, ever (subtitles are pure text;
``render/fonts.py`` is the one module in this package allowed a late,
optional PIL import).
"""

from __future__ import annotations

import math
import re

from . import profiles
from .. import wordtiming

# --------------------------------------------------------------- constants

_WIDTH = profiles.WIDTH   # 1080
_HEIGHT = profiles.HEIGHT  # 1920
_CENTER_X = round(_WIDTH / 2)  # 540

SUBTITLE_MODES = ("word_pop", "two_line", "none")

# word_pop (spec 6.4.1: "one word at a time, uppercase, bold italic sans,
# white with 3 px black outline, centred at 75-80 % of height, ... scale
# pop on entry"; the plan pins the exact numbers: an5\pos(540,1488) == 1080
# wide / 1920 * 0.775 tall, a 3 px outline, and the pop tag verbatim).
WORD_POP_STYLE_NAME = "WordPop"
WORD_POP_FONT_SIZE = 62
WORD_POP_Y_FRACTION = 0.775
WORD_POP_Y = round(_HEIGHT * WORD_POP_Y_FRACTION)  # 1488
WORD_POP_PRIMARY_HEX = "#FFFFFF"
WORD_POP_OUTLINE_HEX = "#000000"
WORD_POP_OUTLINE_PX = 3
WORD_POP_POP_TAG = r"\fscx80\fscy80\t(0,90,\fscx100\fscy100)"

# two_line (spec 6.4.1: "one ASS Style per character (colour accent) ...
# max 2 lines, <= 32 chars per line, bottom 18 % safe area"; plan: "no \k").
TWO_LINE_FONT_SIZE = 56
TWO_LINE_MAX_CHARS = 32
TWO_LINE_MAX_LINES = 2
TWO_LINE_SAFE_AREA_FRACTION = 0.18
TWO_LINE_MARGIN_V = round(_HEIGHT * TWO_LINE_SAFE_AREA_FRACTION)  # 346
TWO_LINE_OUTLINE_HEX = "#000000"
TWO_LINE_OUTLINE_PX = 2
TWO_LINE_DEFAULT_HIGHLIGHT_HEX = "#FFD400"
TWO_LINE_HIGHLIGHT_POP_TAG_FMT = r"\c{0}\fscx110\fscy110\t(0,90,\fscx100\fscy100)"
TWO_LINE_RESET_TAG = r"\r"

# A speaker's own accent colour is drawn as the BASE colour of every word in
# their line; the currently-spoken word is then drawn in the style's own
# ``highlight_colour`` on top of that base (:func:`_two_line_render_text`).
# When an accent sits close to the highlight in colour, the highlighted word
# stops reading as "the one word that changed" -- the exact bug this
# threshold exists to catch (fruit_drama's own primary #F2C14E sits only
# ~81 units from its own highlight #FFD400 in plain RGB space; against a
# 100-unit gate that is rejected, spec 6.4: "colour accent" AND "the current
# word highlighted (colour + small scale pop)" -- both must actually read as
# distinct, not just be technically different hex values).
#
# :func:`_color_distance` is plain Euclidean distance over 8-bit RGB
# components (range 0 - 441.67, ``sqrt(255**2*3)`` for pure black vs pure
# white) -- simple, stdlib, deterministic; not a full perceptual metric
# (CIEDE2000 etc.), which would need a colour-science dependency this
# renderer does not have (DEC-012). :data:`TWO_LINE_MIN_COLOR_DISTANCE`
# (100) was chosen by measuring both shipped styles' own palettes against
# their own highlights (fruit_drama's closest primary is ~81 units from its
# highlight, family_3d's closest primary is ~51 units -- both must be
# rejected; every other palette/accent entry in both styles sits at 105
# units or farther) -- see ``tests/test_aistory_render_subtitles.py``'s own
# distance assertions for the exact numbers per shipped style.
TWO_LINE_MIN_COLOR_DISTANCE = 100.0

# Tier-2 (2026-09-29), FR fruit_drama episode 1: the live story's own
# style_lock.json (outputs/stories/b1104ec66b05/style_lock.json) swaps the
# shipped fruit_drama accent #FFFFFF for #ffd400 -- the SAME colour as its
# own highlight -- so :func:`_pick_next_accent` (correctly) skipped it for
# every speaker on the highlight-distance rule, but then handed a speaker
# #1E1E24: a near-black accent that, against this style's own 3 px BLACK
# outline (:data:`TWO_LINE_OUTLINE_HEX`), is nearly invisible -- the line
# only read where the highlighted word covered it. :func:`_color_distance`
# never checked readability against the OUTLINE, only against the highlight
# and the other speakers' accents. :func:`_contrast_ratio` is the WCAG 2.x
# relative-luminance contrast ratio (1.0 identical, 21.0 pure black vs pure
# white) computed straight from the formula (no colour-science dependency,
# DEC-012); :data:`TWO_LINE_MIN_CONTRAST_RATIO` (4.5, WCAG AA for normal
# text) is a HARD gate in :func:`_pick_next_accent` -- unlike the
# already-chosen-speaker distinctness check, it is never relaxed under
# pressure, exactly like the highlight-distance rule already wasn't.
TWO_LINE_MIN_CONTRAST_RATIO = 4.5

# Used only once a style's own palette + accents run out of colours that
# clear BOTH thresholds against the highlight/outline AND every accent
# already chosen for an earlier speaker in the same episode
# (:func:`_speaker_accents`): a small fixed grayscale ramp, deterministic,
# and -- because every stop is light enough to clear
# :data:`TWO_LINE_MIN_CONTRAST_RATIO` against a black outline on its own --
# always usable regardless of how a style's own palette reads. Exactly 3
# stops: within the luminance band that clears 4.5:1 against black
# (roughly 117-255 per channel for a pure gray), a 4th stop 100 units below
# the 3rd would drop below that floor -- 3 is the most this metric supports
# while also staying >= :data:`TWO_LINE_MIN_COLOR_DISTANCE` apart from each
# other (see the module's own colour-distance tests for the exact numbers).
TWO_LINE_NEUTRAL_FALLBACK = ("#FFFFFF", "#C5C5C5", "#8B8B8B")

# Hook overlay (spec 6.4.1: "a separate top-third style"; fruit_drama's own
# overlay_style: "uppercase, 84 px, drop shadow, centered upper third, <=6
# words"). Spec does not pin an exact vertical fraction the way word_pop's
# 77.5 % is pinned -- this module picks one 1/6-of-height point (roughly
# the visual centre of the screen's own top third) and documents it here
# as its own MVP choice, not a value taken from the spec.
HOOK_STYLE_NAME = "Hook"
HOOK_FONT_SIZE = 84
HOOK_Y_FRACTION = 1.0 / 6.0
HOOK_Y = round(_HEIGHT * HOOK_Y_FRACTION)
HOOK_PRIMARY_HEX = "#FFFFFF"
HOOK_OUTLINE_HEX = "#000000"
HOOK_OUTLINE_PX = 3
HOOK_SHADOW_PX = 2

# ai_label (spec 6.4.1: "small ai_label (\"AI-generated\", bottom-left,
# default on)"; A-077's exact strings).
AI_LABEL_STYLE_NAME = "AiLabel"
AI_LABEL_FONT_SIZE = 28
AI_LABEL_MARGIN = 24
AI_LABEL_OUTLINE_PX = 1
AI_LABEL_PRIMARY_HEX = "#FFFFFF"
AI_LABEL_OUTLINE_HEX = "#000000"
AI_LABEL_TEXT = {"en": "AI-generated", "fr": "Généré par IA"}

# End card / cover.
END_CARD_LABEL_STYLE_NAME = "EndCardLabel"
END_CARD_TITLE_STYLE_NAME = "EndCardTitle"
END_CARD_LABEL_FONT_SIZE = 64
END_CARD_TITLE_FONT_SIZE = 44
END_CARD_LABEL_Y = round(_HEIGHT * 0.45)
END_CARD_TITLE_Y = round(_HEIGHT * 0.55)
END_CARD_PRIMARY_HEX = "#FFFFFF"
END_CARD_OUTLINE_HEX = "#000000"
END_CARD_OUTLINE_PX = 3

COVER_STYLE_NAME = "Cover"
COVER_FONT_SIZE = 84
COVER_Y_FRACTION = HOOK_Y_FRACTION
COVER_Y = round(_HEIGHT * COVER_Y_FRACTION)
COVER_PRIMARY_HEX = "#FFFFFF"
COVER_OUTLINE_HEX = "#000000"
COVER_OUTLINE_PX = 3
COVER_EVENT_DURATION_S = 5.0  # generous: only a single frame is ever extracted from this document


# ------------------------------------------------------------------- errors

class SubtitleError(ValueError):
    """Raised for a caller mistake this module can catch early (an unknown
    ``subtitle_mode``, a hook overlay requested with no hook scene on the
    timeline) -- never for a plain KeyError a bad *script*/*timeline* pair
    would raise on its own."""


# -------------------------------------------------------------- ASS escaping

def escape_ass_text(text) -> str:
    """Neutralise ASS control characters in literal *text* (module
    docstring): backslash first (so this function's OWN inserted ``\\N``
    hard breaks are never re-escaped), then the brace pair, then every
    newline becomes a hard line break. ``None`` becomes ``""``."""
    if text is None:
        return ""
    out = str(text)
    out = out.replace("\\", "＼")
    out = out.replace("{", "｛").replace("}", "｝")
    out = out.replace("\r\n", "\n").replace("\r", "\n")
    out = out.replace("\n", r"\N")
    return out


# ---------------------------------------------------------------- time code

def _to_centiseconds(seconds: float) -> int:
    """*seconds* rounded to the nearest centisecond, half rounding up,
    clamped to >= 0 (module docstring's rounding rule)."""
    if seconds is None:
        seconds = 0.0
    cs = int(math.floor(float(seconds) * 100.0 + 0.5))
    return max(0, cs)


def _format_centiseconds(cs_total: int) -> str:
    cs = cs_total % 100
    s_total = cs_total // 100
    s = s_total % 60
    m_total = s_total // 60
    m = m_total % 60
    h = m_total // 60
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def format_ass_time(seconds: float) -> str:
    """``H:MM:SS.cc`` for one timestamp (module docstring's rounding
    rule). ``H`` is unpadded (ASS allows any width); ``MM``/``SS``/``cc``
    are always exactly 2 digits."""
    return _format_centiseconds(_to_centiseconds(seconds))


def event_time_pair(start_s: float, end_s: float) -> tuple:
    """The ``(start, end)`` ASS time-code pair for one Dialogue event:
    both rounded to the nearest centisecond (half up), clamped so neither
    is negative, and *end* bumped by one centisecond when rounding
    collapsed a positive-duration span to zero width or *end_s* <=
    *start_s* to begin with -- an event's end is always strictly after its
    start (module docstring)."""
    start_cs = _to_centiseconds(start_s)
    end_cs = _to_centiseconds(end_s)
    if end_cs <= start_cs:
        end_cs = start_cs + 1
    return _format_centiseconds(start_cs), _format_centiseconds(end_cs)


# -------------------------------------------------------------------- color

def _rgb(hex_str: str) -> tuple:
    h = str(hex_str).lstrip("#")
    if len(h) != 6:
        raise ValueError(f"expected a 6-digit hex colour, got {hex_str!r}")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _color_distance(hex_a: str, hex_b: str) -> float:
    """Plain Euclidean distance between two ``#RRGGBB`` colours over their
    8-bit RGB components (module constants' own docstring): 0 for an
    identical colour, ``sqrt(255**2*3) ~= 441.67`` for pure black vs pure
    white."""
    ra, ga, ba = _rgb(hex_a)
    rb, gb, bb = _rgb(hex_b)
    return ((ra - rb) ** 2 + (ga - gb) ** 2 + (ba - bb) ** 2) ** 0.5


def _relative_luminance(hex_str: str) -> float:
    """The WCAG 2.x relative luminance of a ``#RRGGBB`` colour (0.0 for pure
    black to 1.0 for pure white), the sRGB gamma-decode + weighted-sum
    formula from the spec, computed directly (stdlib only, DEC-012 -- no
    colour-science dependency)."""
    def channel(c: int) -> float:
        v = c / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(hex_str)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    """The WCAG 2.x contrast ratio between two ``#RRGGBB`` colours: 1.0 for
    two identical colours, ``21.0`` for pure black against pure white.
    Symmetric (the lighter of the two always sits on top of the ratio)."""
    la, lb = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def _hex_to_bgr(hex_str: str) -> str:
    r, g, b = _rgb(hex_str)
    return f"{b:02X}{g:02X}{r:02X}"


def _ass_style_color(hex_str: str, *, alpha: str = "00") -> str:
    """``&HAABBGGRR`` for a ``[V4+ Styles]`` colour field. ``alpha`` is
    ASS's own inverted alpha (``"00"`` opaque, ``"FF"`` fully
    transparent)."""
    return f"&H{alpha}{_hex_to_bgr(hex_str)}"


def _ass_override_color(hex_str: str) -> str:
    """``&HBBGGRR&`` for an inline ``\\c`` override tag (no alpha byte)."""
    return f"&H{_hex_to_bgr(hex_str)}&"


# --------------------------------------------------------------- word spans

def _spans_from_words(words) -> list:
    """``[(word_text, start_s, end_s), ...]`` from a ``[{"word","start",
    "end"}, ...]`` list (a provider's own cues, or :func:`wordtiming.align`'s
    script-timed ones): each entry's own ``start``/``end`` used as-is, an
    end at or before its own start (a zero-length cue) nudged forward by
    one millisecond."""
    spans = []
    for word in words:
        start = float(word["start"])
        end = float(word["end"])
        if end <= start:
            end = start + 0.001
        spans.append((str(word["word"]), start, end))
    return spans


def _line_word_spans(text: str, duration_s: float, words) -> tuple:
    """``([(word_text, start_offset_s, end_offset_s), ...], is_approximate)``
    for one line, offsets relative to the line's OWN start (never the
    episode's).

    *words* is the TTS sidecar's own list (module docstring) or falsy
    (``None``/``[]``, ``SOURCE_DURATION``'s "no word cues"). When given, the
    DISPLAYED tokens are the SCRIPT's own (``text.split()``, so punctuation,
    capitals and apostrophes always come from the script -- Tier-2,
    2026-09-29: Edge's own word-boundary cues carry bare words, so a
    provider's own tokenisation used to drop the period off "sécurité." and
    the "?"/"!"/"," a word_pop line ended on), each timed from the provider
    cue it matches: :func:`wordtiming.align`'s order-preserving difflib
    match on normalised words (case, accents, surrounding punctuation do
    not count -- reused as-is, this module's own contract for it is
    identical: "the script's words timed from ... the line's own audio",
    whether the transcription came from an STT chain or, here, the
    provider's own boundary events). A script token the provider missed is
    interpolated between its matched neighbours, never overlapping, never
    negative, its own end never past *duration_s* (:func:`wordtiming.align`'s
    own contract). When not one script token matches at all (:func:`
    wordtiming.align` returns ``None`` -- an unrelated *words* list), the
    provider's own bare tokens are used as-is instead of dropping real
    timing data on the floor. Either way ``is_approximate`` is ``False``.
    Otherwise (no *words* at all) *text* is split on whitespace and
    *duration_s* is divided evenly across the tokens (spec 6.4.1c):
    ``is_approximate`` is ``True``. An end offset at or before its own start
    is nudged forward by one millisecond (:func:`_spans_from_words`) so it
    always occupies non-zero time before the centisecond rounding in
    :func:`event_time_pair` runs."""
    if words:
        aligned = wordtiming.align(text, words, duration_s)
        spans = _spans_from_words(aligned if aligned is not None else words)
        return _join_spaced_punctuation(spans), False

    tokens = text.split() if text else []
    if not tokens:
        return [], True
    n = len(tokens)
    each = duration_s / n if duration_s > 0 else 0.0
    spans = []
    for i, token in enumerate(tokens):
        start = i * each
        end = duration_s if i == n - 1 else (i + 1) * each
        spans.append((token, start, end))
    return _join_spaced_punctuation(spans), True


def _join_spaced_punctuation(spans) -> list:
    """*spans* with every punctuation-only token joined to its neighbour.

    French writes a space before ``? ! : ;`` and inside ``« »``, so a split
    on whitespace (or a provider's cues) leaves a lone ``?`` that would pop
    as a word of its own (Tier-2, 2026-09-29). A token with no letter or
    digit joins the word before it, keeping the space and extending that
    word's end; an opening one (``«`` first) joins the word after it. A line
    of punctuation alone keeps its single span."""
    joined = []
    pending = None  # leading punctuation waiting for its word: (text, start)
    for text, start, end in spans:
        if not any(ch.isalnum() for ch in text):
            if joined:
                prev_text, prev_start, prev_end = joined[-1]
                joined[-1] = (f"{prev_text} {text}", prev_start, max(prev_end, end))
            elif pending:
                pending = (f"{pending[0]} {text}", pending[1])
            else:
                pending = (text, start)
            continue
        if pending:
            text, start = f"{pending[0]} {text}", pending[1]
            pending = None
        joined.append((text, start, end))
    if pending:
        joined.append((pending[0], pending[1], spans[-1][2]))
    return joined


def _wrap_word_indices(word_count: int, word_lengths, max_chars: int, max_lines: int) -> list:
    """Greedy word-wrap: a list of *max_lines* lists of word indices (module
    docstring's "2 x 32" rule). A new line is only started while fewer than
    ``max_lines - 1`` lines already exist -- so the LAST line simply
    absorbs whatever is left, even past *max_chars*, rather than ever
    producing more than *max_lines* lines (a single token longer than
    *max_chars* is likewise kept whole, never split mid-word)."""
    if word_count == 0:
        return [[]]
    lines = []
    current = []
    current_len = 0
    for idx in range(word_count):
        length = word_lengths[idx]
        added = length if not current else length + 1  # +1 for the joining space
        if current and current_len + added > max_chars and len(lines) < max_lines - 1:
            lines.append(current)
            current = [idx]
            current_len = length
        else:
            current.append(idx)
            current_len += added
    lines.append(current)
    return lines


# ------------------------------------------------------------------- styles

def _style_line(name, font_family, size, primary_hex, outline_hex, *, bold=True, italic=False,
                 outline_px=2, shadow=0, alignment=5, margin_l=0, margin_r=0, margin_v=0) -> str:
    primary = _ass_style_color(primary_hex)
    outline = _ass_style_color(outline_hex)
    back = _ass_style_color("#000000", alpha="80")
    b = -1 if bold else 0
    i = -1 if italic else 0
    return (
        f"Style: {name},{font_family},{size},{primary},{primary},{outline},{back},"
        f"{b},{i},0,0,100,100,0,0,1,{outline_px},{shadow},{alignment},{margin_l},{margin_r},{margin_v},1"
    )


def _document(*, title: str, styles: list, events: list) -> str:
    lines = [
        "[Script Info]",
        f"Title: {title}",
        "ScriptType: v4.00+",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {_WIDTH}",
        f"PlayResY: {_HEIGHT}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
    ]
    lines.extend(styles)
    lines.append("")
    lines.append("[Events]")
    lines.append("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")
    lines.extend(events)
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------ line merging

def merge_timeline_lines(timeline: dict, script: dict) -> list:
    """``[{"line_id", "scene_id", "start_s", "duration_s", "text",
    "speaker"}, ...]``, in timeline order: ``render.timeline``'s own
    per-line placement (``start_s``/``duration_s``, already absolute on the
    output timeline) joined with the script's own ``text``/``speaker`` for
    that ``line_id``. A timeline line whose id is not found in the script
    (should not happen for a covering board) is silently skipped rather
    than raised -- this module never re-validates the board/script pair,
    ``render.timeline.build_timeline`` already did (module docstring)."""
    lookup = {}
    for scene in script["scenes"]:
        for line in scene["lines"]:
            lookup[line["line_id"]] = (line["text"], line["speaker"])

    merged = []
    for entry in timeline["lines"]:
        found = lookup.get(entry["line_id"])
        if found is None:
            continue
        text, speaker = found
        merged.append({
            "line_id": entry["line_id"],
            "scene_id": entry["scene_id"],
            "start_s": entry["start_s"],
            "duration_s": entry["duration_s"],
            "text": text,
            "speaker": speaker,
        })
    return merged


# ------------------------------------------------------------------ word_pop

def _apply_word_card_floor(spans: list, min_card_s: float) -> list:
    """*spans* (:func:`_line_word_spans`'s own shape: chronological, never
    overlapping) with every card stretched to at least *min_card_s* seconds
    -- phase 7 stage 6c's ``typography.word_min_card_ms``, the caller's to
    read and convert.

    A card too short to reach the floor on its own is MERGED with the next
    card(s) -- their text joined by a space, becoming one Dialogue event --
    until the combined span reaches the floor or there is nothing left to
    merge with. Chosen over shifting the next card's start: a shift would
    drift every later word in the line forward, compounding across a long
    run of short words and potentially past the line's own end; a merge
    only ever uses time the short cards already had between them. Every
    merged card's new end is one of the original spans' own end (never
    invented), so two cards never overlap; only the very last card of the
    line -- nothing left to merge with -- is stretched past its own
    original end.
    """
    if min_card_s <= 0 or not spans:
        return list(spans)
    result = []
    i, n = 0, len(spans)
    while i < n:
        text, start, end = spans[i]
        j = i
        while end - start < min_card_s and j + 1 < n:
            j += 1
            next_text, _next_start, next_end = spans[j]
            text = f"{text} {next_text}"
            end = next_end
        if end - start < min_card_s:
            end = start + min_card_s  # the line's last card(s): nothing follows to overlap
        result.append((text, start, end))
        i = j + 1
    return result


def word_pop_dialogue(lines: list, *, word_timings=None, typography: dict) -> tuple:
    """``(style_lines, event_lines, approx_by_line)`` for ``subtitle_mode
    == "word_pop"`` (module constants for the exact numbers): one
    uppercase Dialogue event per word, each visible for its own span
    (:func:`_line_word_spans`), fixed ``\\an5\\pos(540,1488)`` (77.5 % of
    height), and the pop tag verbatim.

    When *typography* carries ``word_min_card_ms`` (a v2 story's style lock
    only, phase 7 stage 6c), every card is stretched to at least that many
    milliseconds (:func:`_apply_word_card_floor`) before the Dialogue events
    are built. Absent (every shipped template, every legacy lock, and
    render/golden.py's STYLE_LOCK -- RC-M2), nothing here changes: byte-for-
    byte the same output as before this key existed.
    """
    style_lines = [_style_line(
        WORD_POP_STYLE_NAME, typography["font_family"], WORD_POP_FONT_SIZE,
        WORD_POP_PRIMARY_HEX, WORD_POP_OUTLINE_HEX,
        bold=True, italic=True, outline_px=WORD_POP_OUTLINE_PX, alignment=5,
    )]
    event_lines = []
    approx_by_line = {}
    word_timings = word_timings or {}
    min_card_ms = typography.get("word_min_card_ms")
    min_card_s = min_card_ms / 1000.0 if min_card_ms else 0.0

    override = f"{{\\an5\\pos({_CENTER_X},{WORD_POP_Y}){WORD_POP_POP_TAG}}}"

    for line in lines:
        spans, is_approx = _line_word_spans(line["text"], line["duration_s"], word_timings.get(line["line_id"]))
        if min_card_s:
            spans = _apply_word_card_floor(spans, min_card_s)
        approx_by_line[line["line_id"]] = is_approx
        for word_text, off_start, off_end in spans:
            start_tc, end_tc = event_time_pair(line["start_s"] + off_start, line["start_s"] + off_end)
            text = escape_ass_text(word_text.upper())
            event_lines.append(
                f"Dialogue: 0,{start_tc},{end_tc},{WORD_POP_STYLE_NAME},,0,0,0,,{override}{text}"
            )
    return style_lines, event_lines, approx_by_line


# ------------------------------------------------------------------ two_line

def _speaker_style_name(speaker: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", str(speaker)).strip("_") or "spk"
    return f"TwoLine_{slug}"


def _is_readable(color: str) -> bool:
    """*color* clears :data:`TWO_LINE_MIN_CONTRAST_RATIO` against
    :data:`TWO_LINE_OUTLINE_HEX` -- the outline colour every two_line style
    is actually built with (:func:`two_line_dialogue`'s own ``_style_line``
    call), read from that one constant rather than a fresh literal."""
    return _contrast_ratio(color, TWO_LINE_OUTLINE_HEX) >= TWO_LINE_MIN_CONTRAST_RATIO


def _pick_next_accent(candidates: list, highlight_hex: str, already_chosen: list) -> str:
    """The next speaker's accent colour, deterministic for fixed inputs
    (module constants' own docstring): the first *candidates* entry that is
    readable against the outline (:func:`_is_readable`), clears
    :data:`TWO_LINE_MIN_COLOR_DISTANCE` from *highlight_hex*, AND clears it
    from every colour in *already_chosen*. Readability and the
    highlight-distance rule are HARD gates, never relaxed -- an unreadable
    or highlight-coloured accent is always wrong, no matter how many
    speakers there are (module constants' own docstring: the #1E1E24
    "broccolia" bug this exists to fix). When every candidate that is ALSO
    far enough from every already-chosen accent has been used up (more
    speakers than the palette can keep mutually distinct), the search
    relaxes to "readable AND far enough from the highlight only" and cycles
    the candidates again from the start. The absolute last resort (every
    candidate, including the neutral ramp, somehow fails one of the two
    hard gates -- not reachable by either shipped style, since the neutral
    ramp is built to always clear both) is plain white, itself readable
    against any outline this module ever builds."""
    for color in candidates:
        if not _is_readable(color):
            continue
        if _color_distance(color, highlight_hex) < TWO_LINE_MIN_COLOR_DISTANCE:
            continue
        if any(_color_distance(color, chosen) < TWO_LINE_MIN_COLOR_DISTANCE for chosen in already_chosen):
            continue
        return color
    for color in candidates:
        if _is_readable(color) and _color_distance(color, highlight_hex) >= TWO_LINE_MIN_COLOR_DISTANCE:
            return color
    return "#FFFFFF"


def _speaker_accents(lines: list, palette: dict, highlight_hex: str) -> dict:
    """``{speaker: hex_colour}``, one entry per distinct speaker in *lines*,
    assigned in first-appearance order (deterministic for a fixed script)
    from ``palette["primary"] + palette["accents"]`` (plan: "accent taken
    from the palette"), then :data:`TWO_LINE_NEUTRAL_FALLBACK` -- each pick
    made by :func:`_pick_next_accent` so every accent stays readable
    against the outline, clearly distinct from *highlight_hex*, AND from
    every other speaker's own accent (module constants' own docstring: the
    bugs this exists to fix)."""
    candidates = list(palette.get("primary") or []) + list(palette.get("accents") or []) + list(TWO_LINE_NEUTRAL_FALLBACK)
    accents = {}
    chosen = []
    for line in lines:
        speaker = line["speaker"]
        if speaker in accents:
            continue
        color = _pick_next_accent(candidates, highlight_hex, chosen)
        accents[speaker] = color
        chosen.append(color)
    return accents


def _two_line_render_text(wrap_lines, word_texts, highlight_idx, highlight_ass_color) -> str:
    highlight_open = f"{{{TWO_LINE_HIGHLIGHT_POP_TAG_FMT.format(highlight_ass_color)}}}"
    highlight_close = f"{{{TWO_LINE_RESET_TAG}}}"
    rendered_lines = []
    for indices in wrap_lines:
        parts = []
        for idx in indices:
            token = escape_ass_text(word_texts[idx])
            if idx == highlight_idx:
                parts.append(f"{highlight_open}{token}{highlight_close}")
            else:
                parts.append(token)
        rendered_lines.append(" ".join(parts))
    return r"\N".join(rendered_lines)


def two_line_dialogue(lines: list, *, word_timings=None, palette: dict, typography: dict) -> tuple:
    """``(style_lines, event_lines, approx_by_line)`` for ``subtitle_mode
    == "two_line"``: one ``Style`` per speaker (:func:`_speaker_accents`,
    each accent kept clearly distinct from the highlight colour and from
    every other speaker's own accent -- module constants' own docstring),
    one Dialogue event per word showing the WHOLE wrapped line (<= 2 lines
    x 32 chars, :func:`_wrap_word_indices`) with only the word currently
    being spoken drawn in the style template's own ``highlight_colour`` and
    a small scale pop, reset with a bare ``\\r`` (never ``\\k`` -- plan:
    "no \\k") so later words in the same event fall back to the speaker
    Style's own colour."""
    word_timings = word_timings or {}
    highlight_hex = typography.get("highlight_colour") or TWO_LINE_DEFAULT_HIGHLIGHT_HEX
    highlight_ass_color = _ass_override_color(highlight_hex)

    accents = _speaker_accents(lines, palette, highlight_hex)
    style_names = {speaker: _speaker_style_name(speaker) for speaker in accents}
    style_lines = [
        _style_line(
            style_names[speaker], typography["font_family"], TWO_LINE_FONT_SIZE,
            hex_color, TWO_LINE_OUTLINE_HEX, bold=True, italic=False,
            outline_px=TWO_LINE_OUTLINE_PX, alignment=2, margin_v=TWO_LINE_MARGIN_V,
        )
        for speaker, hex_color in accents.items()
    ]

    event_lines = []
    approx_by_line = {}
    for line in lines:
        spans, is_approx = _line_word_spans(line["text"], line["duration_s"], word_timings.get(line["line_id"]))
        approx_by_line[line["line_id"]] = is_approx
        if not spans:
            continue
        word_texts = [w for w, _s, _e in spans]
        word_lengths = [len(w) for w in word_texts]
        wrap_lines = _wrap_word_indices(len(word_texts), word_lengths, TWO_LINE_MAX_CHARS, TWO_LINE_MAX_LINES)
        style_name = style_names[line["speaker"]]

        for i, (_word_text, off_start, off_end) in enumerate(spans):
            start_tc, end_tc = event_time_pair(line["start_s"] + off_start, line["start_s"] + off_end)
            text = _two_line_render_text(wrap_lines, word_texts, i, highlight_ass_color)
            event_lines.append(
                f"Dialogue: 0,{start_tc},{end_tc},{style_name},,0,0,0,,{text}"
            )
    return style_lines, event_lines, approx_by_line


# --------------------------------------------------------------- hook / label

def hook_overlay_events(hook_text: str, duration_s: float, *, typography: dict) -> tuple:
    """``(style_lines, event_lines)`` for the hook's on-screen text (spec
    6.4.1: "a separate top-third style"), uppercase, from the episode's own
    ``0.0`` s for *duration_s* (the hook scene's own span, module
    docstring / plan: "from 0.0 s for the hook scene's duration"). Returns
    ``([], [])`` for empty/``None`` *hook_text* -- a caller never needs to
    guard this itself."""
    if not hook_text:
        return [], []
    style_lines = [_style_line(
        HOOK_STYLE_NAME, typography["font_family"], HOOK_FONT_SIZE,
        HOOK_PRIMARY_HEX, HOOK_OUTLINE_HEX, bold=True, italic=False,
        outline_px=HOOK_OUTLINE_PX, shadow=HOOK_SHADOW_PX, alignment=5,
    )]
    start_tc, end_tc = event_time_pair(0.0, duration_s)
    text = escape_ass_text(str(hook_text).upper())
    override = f"{{\\an5\\pos({_CENTER_X},{HOOK_Y})}}"
    event_lines = [f"Dialogue: 0,{start_tc},{end_tc},{HOOK_STYLE_NAME},,0,0,0,,{override}{text}"]
    return style_lines, event_lines


def ai_label_events(total_s: float, *, language: str, typography: dict) -> tuple:
    """``(style_lines, event_lines)`` for the ``ai_label`` disclosure (spec
    6.4.1, A-077): bottom-left, small, for the whole episode
    (``[0, total_s]``), "AI-generated" (en) or "Généré par IA" (fr) --
    falling back to the English string for any other language code."""
    text = AI_LABEL_TEXT.get(language, AI_LABEL_TEXT["en"])
    style_lines = [_style_line(
        AI_LABEL_STYLE_NAME, typography["font_family"], AI_LABEL_FONT_SIZE,
        AI_LABEL_PRIMARY_HEX, AI_LABEL_OUTLINE_HEX, bold=False, italic=False,
        outline_px=AI_LABEL_OUTLINE_PX, shadow=0, alignment=1,
        margin_l=AI_LABEL_MARGIN, margin_r=AI_LABEL_MARGIN, margin_v=AI_LABEL_MARGIN,
    )]
    start_tc, end_tc = event_time_pair(0.0, total_s)
    event_lines = [f"Dialogue: 0,{start_tc},{end_tc},{AI_LABEL_STYLE_NAME},,0,0,0,,{escape_ass_text(text)}"]
    return style_lines, event_lines


# --------------------------------------------------------- end card / cover

def end_card_ass(language: str, next_ep: int, story_title: str, typography: dict, duration_s: float) -> str:
    """A standalone ASS document for the 1.0 s end card (spec 6.2/6.4,
    plan: "PART {n+1} / PARTIE {n+1} + the story title, centered, style
    typography"): burned by ``render/filtergraph.py``'s ``end_card_argv``
    over a plain ``color=black`` source. ``typography["font_family"]`` is
    the resolved ASS Fontname (module docstring's font contract)."""
    label_word = "PARTIE" if language == "fr" else "PART"
    label_text = escape_ass_text(f"{label_word} {next_ep}")
    title_text = escape_ass_text(story_title)

    label_style = _style_line(
        END_CARD_LABEL_STYLE_NAME, typography["font_family"], END_CARD_LABEL_FONT_SIZE,
        END_CARD_PRIMARY_HEX, END_CARD_OUTLINE_HEX, bold=True, italic=False,
        outline_px=END_CARD_OUTLINE_PX, alignment=5,
    )
    title_style = _style_line(
        END_CARD_TITLE_STYLE_NAME, typography["font_family"], END_CARD_TITLE_FONT_SIZE,
        END_CARD_PRIMARY_HEX, END_CARD_OUTLINE_HEX, bold=False, italic=False,
        outline_px=END_CARD_OUTLINE_PX, alignment=5,
    )
    start_tc, end_tc = event_time_pair(0.0, duration_s)
    events = [
        f"Dialogue: 0,{start_tc},{end_tc},{END_CARD_LABEL_STYLE_NAME},,0,0,0,,"
        f"{{\\an5\\pos({_CENTER_X},{END_CARD_LABEL_Y})}}{label_text}",
        f"Dialogue: 1,{start_tc},{end_tc},{END_CARD_TITLE_STYLE_NAME},,0,0,0,,"
        f"{{\\an5\\pos({_CENTER_X},{END_CARD_TITLE_Y})}}{title_text}",
    ]
    return _document(title="End Card", styles=[label_style, title_style], events=events)


def cover_ass(text: str, typography: dict) -> str:
    """A standalone ASS document for the cover's overlay text (spec 6.2,
    plan: "the cover's overlay text (upper third, uppercase, <= 6 words
    shown as given)"): the word cap is enforced by the caller/schema
    (``schemas._word_cap_errors``, hook/M1 text), never re-truncated here
    -- *text* is shown as given, only uppercased for display. Burned over
    the hook shot's single extracted frame."""
    style = _style_line(
        COVER_STYLE_NAME, typography["font_family"], COVER_FONT_SIZE,
        COVER_PRIMARY_HEX, COVER_OUTLINE_HEX, bold=True, italic=False,
        outline_px=COVER_OUTLINE_PX, shadow=HOOK_SHADOW_PX, alignment=5,
    )
    start_tc, end_tc = event_time_pair(0.0, COVER_EVENT_DURATION_S)
    rendered = escape_ass_text(str(text).upper())
    events = [f"Dialogue: 0,{start_tc},{end_tc},{COVER_STYLE_NAME},,0,0,0,,"
              f"{{\\an5\\pos({_CENTER_X},{COVER_Y})}}{rendered}"]
    return _document(title="Cover", styles=[style], events=events)


# --------------------------------------------------------------- composition

def _scene_span(timeline: dict, scene_id: str) -> tuple:
    shots = [s for s in timeline["shots"] if s["scene_id"] == scene_id]
    if not shots:
        raise SubtitleError(f"scene {scene_id!r} has no shots on this timeline")
    start = min(s["start_s"] for s in shots)
    end = max(s["start_s"] + s["duration_s"] for s in shots)
    return start, end


def build_subtitles_ass(*, timeline: dict, script: dict, subtitle_mode: str, language: str,
                         hook_style: str, ai_label_enabled: bool, palette: dict, typography: dict,
                         word_timings=None) -> tuple:
    """The full episode ``subtitles.ass`` document (everything burned by
    the final pass's ``ass=subtitles.ass:fontsdir=fonts`` in one file,
    spec 6.5): dialogue text for *subtitle_mode* (``word_pop``/
    ``two_line``/``none`` -- spec 6.3's closed list), the hook overlay only
    when *hook_style* == ``"text_overlay"``, and the ``ai_label`` when
    *ai_label_enabled*.

    Returns ``(document_text, meta)``, ``meta == {"approx_line_ids": [...]}``
    -- the sorted ids of every line whose word timing was an even split
    rather than real provider timestamps (module docstring), for a caller
    to label "approximate timing" in the UI (spec 6.4.1c). Raises
    :class:`SubtitleError` for an unknown *subtitle_mode*.
    """
    if subtitle_mode not in SUBTITLE_MODES:
        raise SubtitleError(f"unknown subtitle_mode {subtitle_mode!r}, expected one of {SUBTITLE_MODES}")

    lines = merge_timeline_lines(timeline, script)
    style_lines: list = []
    event_lines: list = []
    approx_by_line: dict = {}

    if subtitle_mode == "word_pop":
        s, e, a = word_pop_dialogue(lines, word_timings=word_timings, typography=typography)
        style_lines += s
        event_lines += e
        approx_by_line.update(a)
    elif subtitle_mode == "two_line":
        s, e, a = two_line_dialogue(lines, word_timings=word_timings, palette=palette, typography=typography)
        style_lines += s
        event_lines += e
        approx_by_line.update(a)
    # "none": no dialogue Style/Events at all -- the other layers still apply.

    if hook_style == "text_overlay":
        hook_text = (script.get("hook") or {}).get("on_screen_text")
        hook_scene = next((sc for sc in script["scenes"] if sc["function"] == "hook"), None)
        if hook_text and hook_scene is not None:
            start, end = _scene_span(timeline, hook_scene["scene_id"])
            hook_duration = end - start
            s, e = hook_overlay_events(hook_text, hook_duration, typography=typography)
            style_lines += s
            event_lines += e

    if ai_label_enabled:
        s, e = ai_label_events(timeline["total_s"], language=language, typography=typography)
        style_lines += s
        event_lines += e

    document = _document(title="Episode Subtitles", styles=style_lines, events=event_lines)
    meta = {"approx_line_ids": sorted(line_id for line_id, is_approx in approx_by_line.items() if is_approx)}
    return document, meta

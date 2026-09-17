"""
clipping.transcript — Local transcript ingestion (VTT / SRT / YouTube JSON3)

This module is the Layer-2 bypass: when the user supplies a transcript on the
command line, the pipeline reads it here and never loads Whisper at all.

It deliberately imports nothing heavier than the standard library. That is a
hard constraint, not a preference — the whole point of ``--transcript`` is that
a run can complete on a machine with no working CUDA stack and no CTranslate2.

Every parser here returns the same pair::

    (transkrip_lengkap, data_segmen)

``transkrip_lengkap`` is the human-readable string fed to the LLM, one line per
chunk, in the format ``get_analysis_prompt`` documents to the model::

    [12.3 - 15.8] some spoken words here

``data_segmen`` is the render contract consumed by
``clipping/studio/subtitles.buat_file_ass``::

    [{"start": float, "end": float,
      "words": [{"word": str, "start": float, "end": float}, ...]}, ...]

Two properties of that contract are load-bearing and easy to get wrong:

1. **There is no ``"text"`` key, and ``"words"`` may never be empty.**
   ``buat_file_ass`` indexes ``seg["words"]`` with ``[]`` (subtitles.py:172 and
   :247) to drive the per-word karaoke highlight. A missing key is a KeyError
   mid-render; an empty list is an invisible subtitle.
2. **Timestamps are absolute source-video seconds, never rebased.**
   ``buat_file_ass`` windows each clip itself by subtracting ``start_clip`` and
   *silently dropping* segments whose span inverts. A transcript that is offset
   relative to its video therefore produces missing subtitles rather than an
   error — which is why ``parse_vtt_subs`` refuses to return empty and why the
   runner cross-checks the transcript span against the video duration.
"""

import html
import json
import os
import re


class TranscriptParseError(ValueError):
    """Raised when a transcript file cannot be turned into usable segments.

    Deliberately loud. The previous JSON3 parser swallowed every exception and
    returned ``("", [])``, which in local-first mode degrades silently into a
    multi-hour Whisper run instead of failing in 40ms.
    """


# ==============================================================================
# Timestamp handling
# ==============================================================================

# Matches both WebVTT (``00:01:02.345``) and SRT (``00:01:02,345``) stamps, with
# the hours field optional — WebVTT allows ``MM:SS.mmm`` and yt-dlp emits it.
_TS_PAT = r"(?:\d+:)?\d{1,2}:\d{2}[.,]\d{1,3}"

_CUE_RE = re.compile(rf"^\s*({_TS_PAT})\s*-->\s*({_TS_PAT})\s*(.*)$")
_TS_PARSE = re.compile(rf"^(?:(\d+):)?(\d{{1,2}}):(\d{{2}})[.,](\d{{1,3}})$")

# YouTube auto-caption word tags: ``hello<00:00:01.234><c> world</c>``
_INLINE_TS = re.compile(rf"<({_TS_PAT})>")


def _parse_timestamp(text: str) -> float:
    """``"01:02:03.450"`` -> ``3723.45``; ``"02:03.450"`` -> ``123.45``."""
    match = _TS_PARSE.match(text.strip())
    if not match:
        raise TranscriptParseError(f"Timestamp tidak valid: {text!r}")

    hours, minutes, seconds, frac = match.groups()
    # ".5" means 500ms, not 5ms — pad right, not left.
    millis = int(frac.ljust(3, "0"))
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + millis / 1000.0


# ==============================================================================
# Text cleaning — shared by every parser
# ==============================================================================

def _clean_subtitle_text(text: str, *, unescape: bool = False) -> str:
    """Strip caption artifacts.

    The regex chain and its order are carried over verbatim from the original
    ``engine.parse_youtube_json3_subs`` so that JSON3 output stays byte-identical
    (pinned by ``tests/test_json3_parser.py::test_json3_golden``).

    ``unescape`` is opt-in and used only for VTT/SRT, where entities are common.
    It must run *first*, so that ``&gt;&gt;`` becomes ``>>`` in time to be caught
    by the speaker-marker rule below. Leaving it off for JSON3 keeps the golden
    test honest.
    """
    if unescape:
        text = html.unescape(text)

    clean = text.replace("\n", " ").replace("​", "").strip()
    # HTML/VTT markup: <i>, </i>, <font ...>, <c>, and inline <00:00:01.234> tags.
    clean = re.sub(r"<[^>]+>", "", clean)
    # Annotation brackets: [Music], [Applause], [Laughter].
    clean = re.sub(r"\[[\w\s]+\]", "", clean)
    # Speaker-change markers.
    clean = re.sub(r">>\s*", "", clean)
    clean = re.sub(r"[♪♫♬♩]", "", clean)
    # Leading dash used for speaker identification.
    clean = re.sub(r"^\s*-\s+", "", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip()
    return clean


def _dedupe_key(cleaned_line: str) -> str:
    """Normalize a cleaned line for rolling-caption comparison."""
    return re.sub(r"[^\w\s]", "", cleaned_line.lower()).strip()


# ==============================================================================
# Word-list post-processing — shared by every parser
# ==============================================================================

def _deoverlap(flat_words: list[dict]) -> None:
    """Trim each word's end back to the next word's start, in place.

    Carried over from the original JSON3 parser. Overlapping words make the
    karaoke highlight light up two words at once.
    """
    for i in range(len(flat_words) - 1):
        if flat_words[i]["end"] > flat_words[i + 1]["start"]:
            flat_words[i]["end"] = max(
                flat_words[i]["start"] + 0.1, flat_words[i + 1]["start"]
            )


def _enforce_monotonic(flat_words: list[dict], source_label: str) -> list[dict]:
    """Drop words that start before their predecessor.

    Rolling auto-captions occasionally emit inline timestamps that run backwards
    across a cue boundary. ``buat_file_ass`` would silently drop the resulting
    inverted segments, so a bad transcript would show up as *missing subtitles*
    with no error anywhere. Catch it here and say so.
    """
    kept: list[dict] = []
    dropped = 0
    last_start = float("-inf")

    for word in flat_words:
        if word["start"] < last_start - 0.001:
            dropped += 1
            continue
        kept.append(word)
        last_start = word["start"]

    if dropped and flat_words:
        ratio = dropped / len(flat_words)
        if ratio > 0.05:
            print(
                f"   ⚠️ {dropped} dari {len(flat_words)} kata ({ratio:.0%}) di "
                f"{source_label} punya timestamp mundur dan dibuang. "
                "Kemungkinan besar transkrip ini bukan milik video tersebut."
            )
    return kept


def _apply_offset(flat_words: list[dict], offset: float) -> None:
    """Shift every timestamp by *offset* seconds, clamped at zero, in place."""
    if not offset:
        return
    for word in flat_words:
        word["start"] = max(0.0, word["start"] + offset)
        word["end"] = max(word["start"] + 0.01, word["end"] + offset)


def _chunk_into_segments(
    flat_words: list[dict], max_words_per_subtitle: int
) -> tuple[str, list[dict]]:
    """Group a flat word list into the ``data_segmen`` contract.

    Carried over verbatim from the original JSON3 parser so both formats produce
    identically-shaped output and identically-formatted transcript lines.
    """
    transkrip_lengkap = ""
    data_segmen: list[dict] = []

    chunk_words: list[dict] = []
    chunk_start = 0.0

    for i, word in enumerate(flat_words):
        if not chunk_words:
            chunk_start = word["start"]

        chunk_words.append(word)

        if len(chunk_words) == max_words_per_subtitle or i == len(flat_words) - 1:
            chunk_text = " ".join(cw["word"] for cw in chunk_words)
            chunk_end = word["end"]
            transkrip_lengkap += f"[{chunk_start:.1f} - {chunk_end:.1f}] {chunk_text}\n"

            data_segmen.append(
                {"start": chunk_start, "end": chunk_end, "words": chunk_words}
            )
            chunk_words = []

    return transkrip_lengkap, data_segmen


def _expand_run_to_words(
    text: str, run_start: float, run_end: float
) -> list[dict]:
    """Split a text run across ``[run_start, run_end)`` by even division.

    This is the same fallback the JSON3 parser uses: without per-word timing
    information, dividing the span evenly is the best available approximation and
    is what makes per-word karaoke work at all.
    """
    words = text.split()
    if not words:
        return []

    if run_end <= run_start:
        # Inline timestamps occasionally run past their own cue end.
        run_end = run_start + 0.1 * len(words)

    per_word = (run_end - run_start) / len(words)
    return [
        {
            "word": word,
            "start": run_start + idx * per_word,
            "end": run_start + (idx + 1) * per_word,
        }
        for idx, word in enumerate(words)
    ]


# ==============================================================================
# WebVTT / SRT
# ==============================================================================

def _iter_cue_blocks(raw: str, path: str):
    """Yield ``(cue_start, cue_end, payload_lines)`` for each real cue.

    Header, ``NOTE``, ``STYLE`` and ``REGION`` blocks are skipped *before* any
    search for ``-->``, so an arrow inside a CSS comment in a STYLE block can
    never be mistaken for a cue timing line.
    """
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")

    for block in re.split(r"\n[ \t]*\n", raw):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue

        head = lines[0].strip()

        if head.startswith("WEBVTT"):
            # The magic line and its contiguous metadata (Kind:, Language:,
            # X-TIMESTAMP-MAP=...) share this block.
            if "X-TIMESTAMP-MAP" in block:
                print(
                    f"   ⚠️ {os.path.basename(path)} berisi X-TIMESTAMP-MAP "
                    "(offset HLS). Offset diabaikan; timestamp dipakai apa adanya."
                )
            continue

        if head.split(None, 1)[0] in {"NOTE", "STYLE", "REGION"}:
            continue

        timing_idx = None
        for idx, line in enumerate(lines):
            if _CUE_RE.match(line):
                timing_idx = idx
                break

        if timing_idx is None:
            # A stray cue identifier or junk block.
            continue

        match = _CUE_RE.match(lines[timing_idx])
        cue_start = _parse_timestamp(match.group(1))
        cue_end = _parse_timestamp(match.group(2))

        if cue_end <= cue_start:
            # Rolling auto-captions emit ~10ms bridge cues; they are almost
            # always pure repeats and get dropped by the de-duplication below.
            cue_end = cue_start + 0.05

        # Lines before the timing line are the cue identifier (an SRT sequence
        # number or a WebVTT cue id) and carry no text.
        yield cue_start, cue_end, lines[timing_idx + 1:]


def parse_vtt_subs(
    vtt_path: str,
    max_words_per_subtitle: int = 5,
    dedupe: bool = True,
    offset: float = 0.0,
) -> tuple[str, list[dict]]:
    """Parse a local WebVTT (or SRT) file into the transcript contract.

    Parameters
    ----------
    vtt_path:
        Path to a ``.vtt`` or ``.srt`` file.
    max_words_per_subtitle:
        Words per rendered subtitle chunk (``--words-per-sub``).
    dedupe:
        Strip rolling-caption repetition. YouTube auto-captions repeat the
        previous cue's trailing line in the next cue; left in, the transcript
        roughly doubles and the LLM sees every sentence twice. Turn this off for
        hand-authored subtitles.
    offset:
        Seconds to add to every timestamp, for a video that was trimmed after
        its transcript was produced.

    Raises
    ------
    TranscriptParseError
        If the file is not a subtitle file, or contains no usable words.
    """
    with open(vtt_path, "r", encoding="utf-8-sig", errors="replace") as handle:
        raw = handle.read()

    if not raw.strip():
        raise TranscriptParseError(f"File transkrip kosong: {vtt_path}")

    if "-->" not in raw:
        raise TranscriptParseError(
            f"{vtt_path} tidak berisi satu pun cue WebVTT/SRT "
            "(tidak ditemukan '-->'). Apakah file ini benar-benar subtitle?"
        )

    flat_words: list[dict] = []
    prev_keys: set[str] = set()

    for cue_start, cue_end, payload_lines in _iter_cue_blocks(raw, vtt_path):
        cleaned_lines = [
            _clean_subtitle_text(line, unescape=True) for line in payload_lines
        ]

        keep_from = 0
        if dedupe:
            # Drop only *leading* carried-over lines, and compare only against
            # the immediately preceding cue. Both limits are deliberate: a
            # genuine mid-cue repetition ("no, no, no") survives, and so does a
            # line that legitimately recurs later in the video.
            while keep_from < len(payload_lines):
                key = _dedupe_key(cleaned_lines[keep_from])
                if key and key in prev_keys:
                    keep_from += 1
                else:
                    break

            prev_keys = {_dedupe_key(c) for c in cleaned_lines if _dedupe_key(c)}

        kept_lines = payload_lines[keep_from:]
        if not kept_lines:
            continue  # cue was entirely a repeat (incl. the 10ms bridge cues)

        payload = "\n".join(kept_lines)

        # Split on inline word timestamps. re.split with one capture group
        # alternates text/timestamp/text/..., so odd indices are the stamps.
        #
        # A cue with no inline tags yields exactly one run spanning the whole
        # cue, which is identical to plain even division. There is deliberately
        # no `if has_inline_timings:` branch to keep in sync — tags simply mean
        # more runs, each with a tighter span.
        tokens = _INLINE_TS.split(payload)
        runs: list[tuple[float, str]] = []
        current_t = cue_start
        for idx, token in enumerate(tokens):
            if idx % 2:
                current_t = _parse_timestamp(token)
                continue
            runs.append((current_t, token))

        for idx, (run_start, run_text) in enumerate(runs):
            cleaned = _clean_subtitle_text(run_text, unescape=True)
            if not cleaned:
                continue
            run_end = runs[idx + 1][0] if idx + 1 < len(runs) else cue_end
            flat_words.extend(_expand_run_to_words(cleaned, run_start, run_end))

    label = os.path.basename(vtt_path)
    flat_words = _enforce_monotonic(flat_words, label)

    if not flat_words:
        raise TranscriptParseError(
            f"Tidak ada kata yang bisa diekstrak dari {vtt_path}. "
            "Periksa apakah file benar-benar berisi subtitle (bukan hanya "
            "[Music]/tag kosong)."
        )

    _deoverlap(flat_words)
    _apply_offset(flat_words, offset)

    return _chunk_into_segments(flat_words, max_words_per_subtitle)


# ==============================================================================
# YouTube JSON3
# ==============================================================================

def parse_youtube_json3_subs(
    json_path: str, max_words_per_subtitle: int = 5
) -> tuple[str, list[dict]]:
    """Parse YouTube JSON3 subtitles into the transcript contract.

    Moved here from ``clipping.engine`` so that all transcript parsing lives in
    one dependency-free module. Behaviour is unchanged and pinned by
    ``tests/test_json3_parser.py::test_json3_golden``, including the historical
    decision to return ``("", [])`` rather than raise: this parser is used as an
    *opportunistic* fallback, where an empty result correctly means "no usable
    sidecar, go transcribe". ``parse_vtt_subs`` raises instead, because a VTT is
    something the user explicitly asked for.
    """
    print("[2/3] Memproses subtitle JSON3 dari YouTube...")

    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            subs_data = json.load(handle)

        flat_words: list[dict] = []

        for event in subs_data.get("events", []):
            # YouTube timestamps are in milliseconds.
            t_start = event.get("tStartMs", 0) / 1000.0
            event_end = t_start + event.get("dDurationMs", 0) / 1000.0

            segs = event.get("segs", [])
            for i, seg in enumerate(segs):
                text = seg.get("utf8", "")
                if not text.strip() or text == "\n":
                    continue

                seg_start = t_start + seg.get("tOffsetMs", 0) / 1000.0

                if i < len(segs) - 1:
                    seg_end = t_start + segs[i + 1].get("tOffsetMs", 0) / 1000.0
                else:
                    seg_end = event_end

                if seg_end <= seg_start:
                    seg_end = seg_start + 1.0  # fallback duration

                clean_text = _clean_subtitle_text(text)
                if clean_text:
                    flat_words.extend(
                        _expand_run_to_words(clean_text, seg_start, seg_end)
                    )

        _deoverlap(flat_words)
        return _chunk_into_segments(flat_words, max_words_per_subtitle)

    except Exception as exc:  # noqa: BLE001 - preserved opportunistic behaviour
        print(f"⚠️ Gagal memparsing JSON3: {exc}")
        return "", []


# ==============================================================================
# Dispatch
# ==============================================================================

_VTT_LIKE = {".vtt", ".srt"}
_JSON3_LIKE = {".json3", ".json"}
SUPPORTED_EXTENSIONS = sorted(_VTT_LIKE | _JSON3_LIKE)


def load_transcript(
    path: str,
    max_words_per_subtitle: int = 5,
    offset: float = 0.0,
    dedupe: bool = True,
) -> tuple[str, list[dict]]:
    """Load a local transcript file, dispatching on its extension.

    This is the single entry point the pipeline uses for the Whisper bypass.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"File transkrip tidak ditemukan: {os.path.abspath(path)}")

    ext = os.path.splitext(path)[1].lower()

    if ext in _VTT_LIKE:
        transkrip, segmen = parse_vtt_subs(
            path,
            max_words_per_subtitle=max_words_per_subtitle,
            dedupe=dedupe,
            offset=offset,
        )
    elif ext in _JSON3_LIKE:
        transkrip, segmen = parse_youtube_json3_subs(
            path, max_words_per_subtitle=max_words_per_subtitle
        )
        if not segmen:
            # The JSON3 parser is intentionally forgiving, but an explicit
            # --transcript must never degrade into a silent Whisper run.
            raise TranscriptParseError(
                f"Tidak ada segmen yang bisa diekstrak dari {path}."
            )
        if offset:
            flat = [w for seg in segmen for w in seg["words"]]
            _apply_offset(flat, offset)
            transkrip, segmen = _chunk_into_segments(flat, max_words_per_subtitle)
    else:
        raise TranscriptParseError(
            f"Format transkrip tidak didukung: {ext or '(tanpa ekstensi)'} "
            f"— gunakan salah satu dari {', '.join(SUPPORTED_EXTENSIONS)}"
        )

    return transkrip, segmen

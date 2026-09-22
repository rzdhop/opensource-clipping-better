"""The three prompts. English instructions; output language is a parameter.

What replaced the old ~330-line Indonesian prompt, and what did not:

* The **editorial judgement** is carried over almost intact — hook/tension/
  payoff structure, the standalone-clarity rule, the scoring rubric, the
  instruction to prefer a strong opening line. That part was good, and it is
  the part that decides whether a clip is worth watching.
* The **Indonesian-specific rules went**: ``TARGET_ACCOUNTS`` (four hardcoded
  account personas), the ``klasifikasi_akun`` routing taxonomy, and the rule
  that three output fields must be written in Indonesian regardless of the
  video's language. A French video now gets French titles.
* The **timing instructions went entirely**, because the model no longer
  chooses timings. It names beat ids; ``snap.py`` decides the cut.

Stdlib only.
"""

from __future__ import annotations

from .langdetect import language_name

# Bumped whenever the wording of any prompt below changes in a way that could
# change an answer. Anything caching a reply keys on it, so a reworded prompt
# invalidates what the old one produced instead of serving it back.
PROMPT_VERSION = "a3"

SYSTEM = (
    "You are a short-form video editor who has published thousands of clips on "
    "TikTok, Reels and Shorts. You judge a moment by whether a stranger "
    "scrolling past would stop, keep watching, and understand it without any "
    "context. You answer only with JSON that matches the schema you are given."
)


# --------------------------------------------------------------- pass A

def video_context(*, language=None, total_seconds=None, topic=None):
    """The one-line preface telling a window what it is part of.

    Each window used to judge "standalone clarity" knowing nothing about the
    video: not its length, not its language, not its subject. Everything here
    is either already known to Python or typed by the uploader — no request is
    made to find any of it out.

    Returns "" when nothing is known, so an unset ``--topic`` cannot leave a
    heading with nothing under it.
    """
    bits = []
    if total_seconds:
        bits.append(f"{float(total_seconds) / 60:.0f} minutes long")
    if language:
        bits.append(f"spoken in {language_name(language)}")

    out = f"VIDEO: {', '.join(bits)}.\n" if bits else ""
    if topic:
        out += f"WHAT IT IS ABOUT, per the uploader: {topic}\n"
    return out


def candidates_prompt(beats_text, *, max_candidates=6, preset=None,
                      language=None, total_seconds=None, topic=None):
    """Ask for the strongest self-contained moments in one window of beats.

    The beats come before the instructions. Rules read against material the
    model has already seen are rules about something; the same rules read first
    are rules about nothing, and small models drop them.
    """
    window = (
        f"Each clip will be cut to roughly {preset.min:.0f}-{preset.max:.0f} "
        f"seconds, so prefer moments that are naturally about "
        f"{preset.target:.0f} seconds long."
        if preset is not None
        else "Prefer moments that are naturally under a minute and a half."
    )
    context = video_context(
        language=language, total_seconds=total_seconds, topic=topic
    )

    return f"""{context}Below is one part of its transcript, split into numbered beats. Each line is:

#<id> [<start>-<end>] <what is said>

BEATS:
{beats_text}

TASK
Find the moments in the beats above that would work as standalone short-form
clips. Use only beat ids that appear above. Do not invent timestamps; the ids
are all you need to give.

RULES
1. A moment must HOOK. Something in its first sentence makes a stranger stop
   scrolling: a claim, a number, a question, a confession, conflict, or an
   unexpected turn.
2. It must build TENSION — set something up that the viewer wants resolved.
3. It must PAY OFF inside itself. A moment that pays off later, or never, is
   not a clip.
4. It must STAND ALONE. It makes sense to someone who has not seen the rest of
   the video. If it depends on an earlier explanation, an unnamed "he", or a
   visual you cannot hear, it is not a clip.
5. b0 must be the beat where the hook is spoken, and it must be the start of a
   sentence, not the middle of one.
6. Reject, however interesting they sound: introductions, sponsor reads,
   housekeeping, lists that never finish, and anything whose point arrives
   after the beats shown.
7. {window}
8. Returning two strong moments is better than six weak ones. Returning none is
   a valid answer for a window that is all housekeeping. Most of a transcript
   is not clippable.

WORKED EXAMPLES (invented for calibration — do not look for them above)
GOOD  b0 opens "I lost forty thousand dollars in one afternoon." and b1 ends
      "...and the bank said it was my own fault."
      score 88, kind story, gist "loses $40,000 and is blamed for it"
      Why: the first line is a number and a confession, and the thing it opens
      is closed before the clip ends.
BAD   b0 opens "So that's the second thing I wanted to mention." and b1 ends
      "...which we'll get into properly next week."
      Why: it opens mid-list, it points at something the viewer has not heard,
      and its payoff is outside the clip. Score it below 50, or omit it.

SCORING
90-100    a stranger watches to the end and sends it to someone
70-89     a stranger watches to the end
50-69     watchable, but nothing makes it travel
below 50  do not return it at all

OUTPUT
Choose at most {max_candidates} moments. For each give:
- b0: the beat id where it starts, where the hook is spoken
- b1: the beat id where it ends, where it pays off
- score: 1-100, on the scale above
- gist: at most 12 words, in English, saying what happens
- kind: one of story, insight, conflict, howto, punchline"""


# --------------------------------------------------------------- pass B

def rerank_prompt(lines, *, want):
    """Rank every surviving candidate from the whole video against each other.

    The candidates come first here, as in the scan prompt: the instructions are
    about material the model has already read by the time it reaches them.
    """
    return f"""These are the candidate moments found across an entire video. Each line is:

#<id> [<duration>s] score=<the score it was given> <kind> | <gist> | hook: "<the words the clip opens on>"

CANDIDATES:
{lines}

Pick the {want} best and rank them, best first.

Judge them against each other, not in isolation:
1. The hook line decides almost everything. A stranger sees three seconds before
   deciding. A weaker moment that opens on a number, a claim or a confession
   beats a stronger one that opens on "so anyway, the other thing is".
2. Prefer a moment that stands completely alone over one that is slightly
   stronger but needs context.
3. A high score given in isolation is a hint, not an instruction. You are seeing
   the whole video for the first time; the earlier scores were not.

For each pick give:
- id: the candidate id, exactly as written above
- score: 1-100, reflecting its rank among all of these
- topic: ONE lowercase word for what it is about ("pricing", "burnout",
  "latency"). Two clips about the same thing must get the same word. This is
  how repetition is detected, so be literal: name the subject, not the emotion.

Return at most {want} entries. If fewer than {want} are genuinely worth
publishing, return fewer. Do not invent ids."""


# --------------------------------------------------------------- pass C

def clip_meta_prompt(beats_text, *, language, kind=None, gist=None, want_broll=True):
    """Ask for one clip's publishing metadata."""
    native = language_name(language)
    english_note = (
        "Write title_native, caption_native, desc_hook and desc_context in "
        f"{native}, because that is the language spoken in the clip. Write "
        "title_en, keywords, hashtags and broll_queries in English."
        if native != "English"
        else "Write everything in English."
    )
    context = ""
    if kind or gist:
        context = f"\nThis moment was picked as a {kind or 'clip'}: {gist or ''}\n"

    broll = (
        "- broll_queries: 0-2 short English stock-footage searches for visuals "
        "that would illustrate this clip. Concrete nouns work; abstractions do "
        "not. Return an empty list if nothing obvious fits.\n"
        if want_broll
        else "- broll_queries: return an empty list.\n"
    )

    return f"""Below is one clip's transcript, as numbered beats.
{context}
{english_note}

Give:
- title_native / title_en: at most 70 characters. Say the most surprising
  specific thing in the clip. No "In this video", no colons introducing a topic,
  no clickbait that the clip does not deliver.
- hashtags: exactly 3, lowercase, each starting with #. One broad, one about the
  topic, one specific to this clip.
- desc_hook: one sentence that makes someone want to watch.
- desc_context: one or two sentences saying what the clip is about.
- keywords: 5-8 English search terms.
- caption_native / caption_en: a one-line social caption. It may use the hook.
- reason: one sentence, in English, on why this works as a short clip.
- hook_beat: the id of the beat carrying the single strongest line. It must be
  one of the ids below, and it should normally be the first.
- emphasis: 3-6 single words that should be visually emphasised in the
  subtitles. Copy them EXACTLY as they appear in the beats below, including
  their spelling and accents. Choose words that carry meaning: numbers, names,
  and the words a speaker leans on. Never pick articles or prepositions.
{broll}- mood: the background-music mood, one of chill, epic, sad, upbeat, suspense.
- drop_beats: ids of any beats that are dead air, a false start, or a tangent
  and could be cut without harming the clip. Usually empty. Never include the
  first or last beat.

BEATS:
{beats_text}"""

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

SYSTEM = (
    "You are a short-form video editor who has published thousands of clips on "
    "TikTok, Reels and Shorts. You judge a moment by whether a stranger "
    "scrolling past would stop, keep watching, and understand it without any "
    "context. You answer only with JSON that matches the schema you are given."
)


# --------------------------------------------------------------- pass A

def candidates_prompt(beats_text, *, max_candidates=6, preset=None):
    """Ask for the strongest self-contained moments in one window of beats."""
    window = (
        f"Each clip will be cut to roughly {preset.min:.0f}-{preset.max:.0f} "
        f"seconds, so prefer moments that are naturally about "
        f"{preset.target:.0f} seconds long.\n"
        if preset is not None
        else ""
    )
    return f"""Below is part of a video transcript, split into numbered beats. Each line is:

#<id> [<start>-<end>] <what is said>

Find the moments that would work as standalone short-form clips.

A moment worth clipping has all four of these:
- A HOOK. Something in its first sentence makes a stranger stop scrolling: a
  claim, a number, a question, a confession, conflict, or an unexpected turn.
- TENSION. It sets something up that the viewer wants resolved.
- A PAYOFF. It resolves inside the clip. A moment that pays off later, or never,
  is not a clip.
- STANDALONE CLARITY. It makes sense to someone who has not seen the rest of the
  video. If it depends on an earlier explanation, an unnamed "he", or a visual
  you cannot hear, it is not a clip.

Reject, however interesting they sound: introductions, sponsor reads, housekeeping,
lists that never finish, and anything whose point arrives after the beats shown.

{window}Score each moment 1-100 on how likely a stranger is to watch it to the end and
send it to someone. Be harsh: most of a transcript is not clippable. Returning
two strong moments is better than six weak ones, and returning none is a valid
answer for a window that is all housekeeping.

Choose at most {max_candidates} moments. For each, give:
- b0: the beat id where it should start, which must be where the hook is spoken
- b1: the beat id where it should end, which must be where it pays off
- score: 1-100
- gist: at most 12 words, in English, saying what happens
- kind: one of story, insight, conflict, howto, punchline

Use only beat ids that appear below. Do not invent timestamps; the ids are all
you need to give.

BEATS:
{beats_text}"""


# --------------------------------------------------------------- pass B

def rerank_prompt(lines, *, want):
    """Rank every surviving candidate from the whole video against each other."""
    return f"""These are the candidate moments found across an entire video. Each line is:

#<id> [<duration>s] score=<the score it was given> <kind> <gist>

Pick the {want} best and rank them, best first.

Judge them against each other, not in isolation. In particular:
- Prefer variety. Five versions of the same point is worse than five different
  points, even if that one point is the strongest thing in the video.
- Prefer a moment that stands completely alone over one that is slightly
  stronger but needs context.
- A high score given in isolation is a hint, not an instruction. You are seeing
  the whole video for the first time; the earlier scores were not.

Return at most {want} entries: the candidate id, and a final score 1-100
reflecting its rank among all of these. If fewer than {want} are genuinely worth
publishing, return fewer. Do not invent ids.

CANDIDATES:
{lines}"""


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

"""The M1 prompt of ``clipping.aistory.prompts``: one platform's metadata of a
rendered episode (spec 2.10, 3 step 12, 4.1, 4.2 row M1; AI Story phase 4,
stage 9; DEC-138, DEC-166, A-078).

Same conventions as ``tests/test_story_prompts_episode.py``: the builder is
a pure function of a ``Pack`` plus plain kwargs, pinned with golden strings
(French and English); the post-validator gets a good reply and a violation
per rule; the output cap is proved by the French-cap method (DEC-107,
DEC-138: the largest French reply the ask allows -- every stated limit hit
-- must fit it, with 15 % to spare, and a much smaller cap must not), with
that file's own ``_fr_words`` filler and ``FRENCH_TOKEN_FACTOR``; the input
fits the pack budget on live-sized data (``test_story_episode_prompt_budgets``'s
filler at the live story's density).

On the parent commit (``f499d9f``) ``prompts`` has no ``build_m1``/``M1``:
every test below fails on its own (AttributeError / KeyError).

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import json
import re

import pytest

from clipping.aistory import context, prompts, schemas
from clipping.providers.pacing import estimate_tokens
from test_story_episode_prompt_budgets import LIVE_BIBLE_DENSITY, _at_density
from test_story_prompts_episode import FRENCH_TOKEN_FACTOR, _fr_words, _walk_llm_schema

STORY_FR = {
    "logline": "Sur une île de téléréalité, des fruits forment des couples et complotent.",
    "premise": "Chaque semaine, les concurrents forment des couples pour ne pas être éliminés.",
    "tone": "mélodramatique, complice, rapide",
}
STORY_EN = {
    "logline": "On a reality island, fruit form couples and scheme.",
    "premise": "Every week the contestants pair up to avoid elimination.",
    "tone": "melodramatic, knowing, fast",
}
FR_KWARGS = dict(ep=1, story_title="L'Île des Tentafruits", episode_title="Le coco sonne deux fois",
                 hook_text="Vote surprise ce soir", teaser="Demain, Kiwilo joue sa dernière carte.",
                 cast_names=["Kiwilo", "Mangella", "Broccolia"])
EN_KWARGS = dict(ep=2, story_title="Tentafruit Island", episode_title="The Coconut Rings Twice", hook_text=None,
                 teaser="Tomorrow, Kiwilo plays his last card.", cast_names=["Kiwilo"])


def _pack(language, **kwargs):
    return context.build_pack(language=language, story=STORY_FR if language == "fr" else STORY_EN, **kwargs)


# ================================================================= goldens

FR_TIKTOK_USER = (
    "Series: L'Île des Tentafruits\n"
    "Story: Sur une île de téléréalité, des fruits forment des couples et complotent. Chaque semaine, les "
    "concurrents forment des couples pour ne pas être éliminés. Tone: mélodramatique, complice, rapide.\n"
    "\n"
    "Episode 1: Le coco sonne deux fois\n"
    "Hook on screen: Vote surprise ce soir\n"
    "Next-episode teaser (the app adds it after your description): Demain, Kiwilo joue sa dernière carte.\n"
    "Characters: Kiwilo, Mangella, Broccolia\n"
    "\n"
    "Write the TikTok post for episode 1.\n"
    "\n"
    "Give:\n"
    "- title: at most 60 characters, no hashtags\n"
    "- description: one to three sentences, at most 30 words, that make people watch without giving away how "
    "the episode ends; do not repeat the teaser, the app adds it after your text\n"
    "- hashtags: 3 to 5 hashtags, each one word of at most 25 characters with no spaces (join several words in "
    "camelCase); the \"#\" is optional\n"
    "- hook_text: the text on the cover image, at most 6 words\n"
    "- title_en (English): the title for English speakers, at most 60 characters\n"
    "- hashtags_en (English): 3 to 5 hashtags for English speakers, same rules as hashtags\n"
    "\n"
    "TikTok rules: the title opens the caption, so put the hook first.\n"
    "\n"
    "Write French elisions with their apostrophe (l'eau, d'État, qu'il), never a space.\n"
    "\n"
    "Never use real people, brands, studio names or copyrighted characters."
)

EN_SHORTS_USER = (
    "Series: Tentafruit Island\n"
    "Story: On a reality island, fruit form couples and scheme. Every week the contestants pair up to avoid "
    "elimination. Tone: melodramatic, knowing, fast.\n"
    "\n"
    "Episode 2: The Coconut Rings Twice\n"
    "Hook on screen: none\n"
    "Next-episode teaser (the app adds it after your description): Tomorrow, Kiwilo plays his last card.\n"
    "Characters: Kiwilo\n"
    "\n"
    "Follow the author's note: Funnier, less drama.\n"
    "\n"
    "Write the YouTube Shorts post for episode 2.\n"
    "\n"
    "Give:\n"
    "- title: at most 70 characters, no hashtags\n"
    "- description: one to three sentences, at most 40 words, that make people watch without giving away how "
    "the episode ends; do not repeat the teaser, the app adds it after your text\n"
    "- hashtags: exactly 3 hashtags, each one word of at most 25 characters with no spaces (join several words "
    "in camelCase); the \"#\" is optional\n"
    "- hook_text: the text on the cover image, at most 6 words\n"
    "\n"
    "YouTube Shorts rules: the title is what people search and see under the video, and the three hashtags "
    "show above it: make them the series, its genre and its hook.\n"
    "\n"
    "Never use real people, brands, studio names or copyrighted characters."
)

SYSTEM_FR = (
    "You write the publishing text of a serialized vertical-video fiction series for TikTok, YouTube Shorts and "
    "Instagram Reels: titles, descriptions and hashtags that make someone stop scrolling and come back for the "
    "next episode. Reply with JSON only, matching the schema. Never output durations, timestamps or file paths. "
    "Never use real people, brands, studio names or copyrighted characters. Write all user-facing text in "
    "French. Fields marked (English) are written in English."
)


def test_build_m1_golden_fr():
    system, user, schema = prompts.build_m1(_pack("fr"), platform="tiktok", **FR_KWARGS)
    assert system == SYSTEM_FR
    assert user == FR_TIKTOK_USER
    assert list(schema["properties"]) == ["title", "description", "hashtags", "hook_text", "title_en", "hashtags_en"]


def test_build_m1_golden_en_with_a_note():
    pack = _pack("en", note="Funnier, less drama.")
    system, user, schema = prompts.build_m1(pack, platform="shorts", note=pack.note, **EN_KWARGS)
    assert system == SYSTEM_FR.replace("in French.", "in English.")
    assert user == EN_SHORTS_USER
    assert list(schema["properties"]) == ["title", "description", "hashtags", "hook_text"]


@pytest.mark.parametrize("language, name", [("fr", "French"), ("en", "English")])
def test_the_language_line_is_in_the_system_prompt(language, name):
    system, user, _schema = prompts.build_m1(_pack(language), platform="reels", **FR_KWARGS)
    assert f"Write all user-facing text in {name}." in system
    assert ("Write French elisions" in user) is (language == "fr")


def test_the_instructions_are_english_whatever_the_story_language():
    pack = context.build_pack(language="fr")
    kwargs = dict(FR_KWARGS, story_title="Tentafruit", episode_title="Coconut", hook_text="Surprise vote",
                  teaser="Tomorrow.", cast_names=["Kiwilo"])
    system, user, _schema = prompts.build_m1(pack, platform="tiktok", **kwargs)
    assert system.isascii() and user.replace("d'État", "d'Etat").isascii()


@pytest.mark.parametrize("platform", schemas.PLATFORMS)
def test_each_platform_states_its_own_rules(platform):
    rules = prompts.M1_PLATFORM_RULES[platform]
    _system, user, _schema = prompts.build_m1(_pack("en"), platform=platform, **EN_KWARGS)
    assert f"Write the {rules['name']} post for episode 2." in user
    assert f"- title: at most {rules['title_chars']} characters, no hashtags" in user
    assert f"at most {rules['description_words']} words" in user
    assert f"{rules['name']} rules: {rules['rule']}." in user


def test_the_authored_platform_limits_a078():
    """A-078: conservative, authored as of 2026-09, each inside the
    platform's own hard limit and the pack schema's 3-6 tags."""
    rules = prompts.M1_PLATFORM_RULES
    assert list(rules) == list(schemas.PLATFORMS)
    assert {p: (r["title_chars"], r["description_words"], r["hashtags"]) for p, r in rules.items()} == {
        "tiktok": (60, 30, (3, 5)), "shorts": (70, 40, (3, 3)), "reels": (60, 40, (3, 5)),
    }
    lo, hi = schemas.METADATA_HASHTAGS_RANGE
    for rule in rules.values():
        assert rule["title_chars"] <= 100  # metadata_pack_v1's title (YouTube's own hard limit)
        assert lo <= rule["hashtags"][0] <= rule["hashtags"][1] <= hi
    assert (prompts.M1_HASHTAG_MAX_CHARS, prompts.M1_HOOK_TEXT_MAX_WORDS) == (25, 6)


def test_the_english_fields_are_asked_of_a_french_story_only():
    _s, fr_user, fr_schema = prompts.build_m1(_pack("fr"), platform="shorts", **FR_KWARGS)
    _s, en_user, en_schema = prompts.build_m1(_pack("en"), platform="shorts", **FR_KWARGS)
    assert "title_en" in fr_schema["properties"] and "hashtags_en" in fr_schema["properties"]
    assert "- title_en (English)" in fr_user and "- hashtags_en (English): exactly 3 hashtags" in fr_user
    assert "title_en" not in en_schema["properties"] and "_en" not in en_user


def test_an_unknown_platform_is_refused():
    with pytest.raises(ValueError, match="unknown platform 'youtube'"):
        prompts.build_m1(_pack("en"), platform="youtube", **EN_KWARGS)


# ============================================================ schema shape

# Built inside each test, so the parent commit fails each test on its own.
M1_SCHEMAS = [f"{p}-{lang}" for p in schemas.PLATFORMS for lang in ("fr", "en")]


def _m1_schema(name):
    platform, lang = name.split("-")
    return prompts.m1_schema(platform, english=lang == "fr")


@pytest.mark.parametrize("name", M1_SCHEMAS)
def test_the_m1_schema_is_strict_mode_shaped(name):
    assert _walk_llm_schema(_m1_schema(name)) == []


def _property_names(schema):
    for key, sub in schema.get("properties", {}).items():
        yield key
        yield from _property_names(sub)
        if sub.get("type") == "array":
            yield from _property_names(sub.get("items", {}))


@pytest.mark.parametrize("name", M1_SCHEMAS)
def test_m1_asks_for_no_duration_path_timestamp_url_or_id(name):
    names = list(_property_names(_m1_schema(name)))
    for forbidden in ("duration", "second", "timestamp", "path", "url", "file", "_id", "cover", "teaser",
                      "pinned"):
        assert not any(forbidden in n for n in names), (name, forbidden, names)


@pytest.mark.parametrize("platform", schemas.PLATFORMS)
def test_the_ask_asks_for_no_duration_or_path(platform):
    _system, user, _schema = prompts.build_m1(_pack("fr"), platform=platform, **FR_KWARGS)
    ask = user.split(f"Write the {prompts.M1_PLATFORM_RULES[platform]['name']} post", 1)[1].lower()
    for forbidden in ("duration", "second", "timestamp", "path", "url", "file"):
        assert forbidden not in ask, (platform, forbidden)


def test_m1_is_registered_in_the_catalogue():
    assert prompts.SCHEMA_NAMES["M1"] == "episode_metadata"
    assert prompts.TEMPERATURE["M1"] is prompts.WRITING_TEMPERATURE
    assert prompts.PROMPT_VERSION == "s7"
    assert "M1" not in prompts.INPUT_BUDGET  # the default 1,200-token pack budget holds (below)


# ============================================================== validator

def _reply(platform="tiktok", *, english=True, **changes):
    lo, _hi = prompts.M1_PLATFORM_RULES[platform]["hashtags"]
    reply = {"title": "Le coco sonne deux fois", "description": "Kiwilo cache un secret. Mangella veut le vote.",
             "hashtags": ["#Tentafruit", "téléréalité", "#coco"][:max(lo, 3)], "hook_text": "Vote surprise ce soir"}
    if english:
        reply.update(title_en="The Coconut Rings Twice", hashtags_en=["Tentafruit", "realityTV", "coconut"])
    reply.update(changes)
    return reply


@pytest.mark.parametrize("platform", schemas.PLATFORMS)
def test_a_good_reply_passes(platform):
    assert prompts.validate_m1(_reply(platform), platform=platform, english=True) == []
    assert prompts.validate_m1(_reply(platform, english=False), platform=platform, english=False) == []


@pytest.mark.parametrize("change, mentions", [
    (dict(title=""), "$.title: must be a non-empty string"),
    (dict(title="x" * 61), "$.title: 61 characters, expected at most 60"),
    (dict(title="one\ntwo"), "$.title: must be one line"),
    (dict(description=_fr_words(31)), "$.description: 31 words, expected at most 30"),
    (dict(hashtags=["#a", "#b"]), "$.hashtags: 2 distinct hashtag(s), expected 3 to 5"),
    (dict(hashtags=["#a", "#A", "a", "#b"]), "$.hashtags: 2 distinct hashtag(s)"),
    (dict(hashtags=["#a", "#b", "#c", "#d", "#e", "#f"]), "$.hashtags: 6 distinct hashtag(s), expected 3 to 5"),
    (dict(hashtags=["#a", "#b", "x" * 26]), "$.hashtags[2]: 26 characters, expected at most 25"),
    (dict(hook_text=_fr_words(7)), "$.hook_text: 7 words, expected at most 6"),
    (dict(title_en="y" * 61), "$.title_en: 61 characters"),
    (dict(hashtags_en=["#a"]), "$.hashtags_en: 1 distinct hashtag(s)"),
])
def test_each_rule_is_checked(change, mentions):
    errors = prompts.validate_m1(_reply("tiktok", **change), platform="tiktok", english=True)
    assert any(mentions in error for error in errors), errors


def test_shorts_wants_exactly_three_tags():
    errors = prompts.validate_m1(_reply("shorts", hashtags=["a", "b", "c", "d"]), platform="shorts", english=True)
    assert errors == ["$.hashtags: 4 distinct hashtag(s), expected exactly 3"]


def test_the_english_fields_are_required_of_a_french_story_and_refused_otherwise():
    fr_missing = {k: v for k, v in _reply().items() if k != "title_en"}
    assert prompts.validate_m1(fr_missing, platform="tiktok", english=True)
    assert prompts.validate_m1(_reply(), platform="tiktok", english=False)  # additionalProperties: false


@pytest.mark.parametrize("tags, expected", [
    (["#Tentafruit", "coco", " # téléréalité "], ["#Tentafruit", "#coco", "#téléréalité"]),
    (["##Double", "two words", "#two#words"], ["#Double", "#twowords"]),
    (["#Same", "same", "SAME", "", "#", None, 3], ["#Same"]),
])
def test_normalize_hashtags_gives_every_tag_one_hash_and_keeps_each_once(tags, expected):
    kept = prompts.normalize_hashtags(tags)
    assert kept == expected
    assert all(re.fullmatch(schemas.HASHTAG_PATTERN, tag) for tag in kept)


# ====================================================== caps (DEC-138)

def _largest_french_reply(platform):
    """Every limit the ask states, hit exactly (DEC-107's method)."""
    rules = prompts.M1_PLATFORM_RULES
    _lo, hi = rules[platform]["hashtags"]
    title = _fr_words(20)[:rules[platform]["title_chars"]]
    tag = ("trahisonalliancesecretcomplot" * 2)[:prompts.M1_HASHTAG_MAX_CHARS - 1]
    tag_en = ("betrayalalliancesecretplot" * 2)[:prompts.M1_HASHTAG_MAX_CHARS - 1]
    return {
        "title": title, "description": _fr_words(rules[platform]["description_words"]),
        "hashtags": [f"#{tag}{i}" for i in range(hi)], "hook_text": _fr_words(prompts.M1_HOOK_TEXT_MAX_WORDS),
        "title_en": ("betrayal alliance secret plot truth lie rivalry " * 3)[:rules[platform]["title_chars"]],
        "hashtags_en": [f"#{tag_en}{i}" for i in range(hi)],
    }


def _needed(platform):
    reply = _largest_french_reply(platform)
    assert prompts.validate_m1(reply, platform=platform, english=True) == [], platform
    return estimate_tokens(json.dumps(reply, ensure_ascii=False)) * FRENCH_TOKEN_FACTOR


@pytest.mark.parametrize("platform", schemas.PLATFORMS)
def test_the_largest_french_reply_fits_the_m1_cap_but_not_a_much_smaller_one(platform):
    """The French-cap test of ``test_story_prompts_episode.py``, extended to
    M1 at its stated limits (every platform)."""
    needed = _needed(platform)
    cap = prompts.MAX_TOKENS["M1"]
    assert needed > cap // 2, f"{platform}: ~{needed:.0f} tokens proves nothing about a {cap}-token cap"
    assert needed <= cap, f"{platform}: the largest French reply needs ~{needed:.0f} tokens, over {cap}"


def test_the_m1_cap_is_the_worst_case_plus_15_percent_from_the_spec_300():
    """DEC-138: the spec's 300 is raised to the worst platform's largest
    French reply + 15 %, rounded up to ten -- and no further."""
    worst = max(_needed(platform) for platform in schemas.PLATFORMS)
    assert worst == pytest.approx(280.8)  # Reels: 216 tokens by chars/4, x 1.3
    assert prompts.MAX_TOKENS["M1"] == max(300, -(-round(worst * 1.15, 1) // 10) * 10) == 330


def test_the_worst_case_m1_input_fits_the_pack_budget():
    """Live-sized data (the live story's bible density, at its 120-word cut),
    the longest title, hook, teaser and note the documents allow, a full
    cast: well inside the default 1,200-token pack budget."""
    story = {"logline": _at_density(30, LIVE_BIBLE_DENSITY), "premise": _at_density(120, LIVE_BIBLE_DENSITY),
             "tone": _at_density(15, LIVE_BIBLE_DENSITY)}
    pack = context.build_pack(language="fr", story=story, note=_fr_words(80))
    assert pack.trimmed == ["bible", "note"]
    kwargs = dict(ep=12, story_title=_fr_words(18)[:120], episode_title=_fr_words(12)[:80],
                  hook_text=_fr_words(6), teaser=_fr_words(15), cast_names=[_fr_words(2)] * 12)
    for platform in schemas.PLATFORMS:
        system, user, _schema = prompts.build_m1(pack, platform=platform, note=pack.note, **kwargs)
        tokens = context.check_budget(system, user)
        assert tokens <= 0.85 * context.PACK_TOKEN_BUDGET, (platform, tokens)

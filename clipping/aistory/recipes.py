"""Genre recipes (plan 32 stage 2, DEC-315): what a story made with one bakes
into its writers.

A recipe is data (``templates/recipes/<id>.json``, ``schemas.RECIPE_SCHEMA``):
the names (a French telenovela pun on the species, ``-ito/-ita``), the fixed
cast and its roles, the beats of an episode, the closing "Team X ou Team Y ?"
question, the end card, the voice direction and the content guardrails. A
story names one in its ``recipe`` field (a preset sets it,
``presets.apply``); :func:`for_story` is the one gate every writer reads it
through -- None for a story without one, or with an id no recipe has, so the
prompts of such a story stay byte-identical (the way ``media_policy.is_v2``
gates the set-up block). An unknown id never raises at prompt time;
``store.create`` refuses one up front (:func:`check_id`).

The texts below are what each writer is told: the set-up block's RECIPE
section (:func:`setup_parts`), C1v2's names ask (:func:`c1v2_names_ask`) and
its check (:func:`name_errors`), E1v3's beats (:func:`e1_beats_line`), E3v3's
teaser (:func:`teaser_ask`), M1's line (:func:`m1_line`) and the pinned
comment's question (:func:`closing_question`).

Stdlib only (DEC-012).
"""

from __future__ import annotations

import copy
import functools
import json
import re
import unicodedata
from pathlib import Path

from . import schemas

RECIPES_DIR = Path(__file__).resolve().parent / "templates" / "recipes"
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
# A concept card holds 3 to 5 characters (``schemas.c1_errors``): the recipe's fixed cast grows past it later
# (the cast step, ``propose_next``).
CARD_CAST_MAX = 5

# The plain human first names a recipe's cast never carries ("plain human names", ``naming.forbidden``): the
# most common French and English first names, accent- and case-folded, matched as whole words.
HUMAN_FIRST_NAMES = (
    "marie", "jean", "pierre", "paul", "sophie", "lucas", "emma", "lea", "hugo", "thomas", "julie", "chloe",
    "camille", "nicolas", "sarah", "louis", "gabriel", "jules", "manon", "ines", "nathan", "alice", "louise",
    "antoine", "julien", "laura", "francois", "isabelle", "michel", "nathalie", "anna", "john", "mary", "james",
    "michael", "david", "emily", "olivia", "william", "robert", "jennifer", "jessica",
)
# The fruit and drink brands a recipe's names never carry (``naming.forbidden``: "brands"), matched as whole
# words with or without their space, hyphen or apostrophe.
NAME_BRANDS = (
    "Chiquita", "Dole", "Del Monte", "Haribo", "Oasis", "Tropicana", "Andros", "Bonne Maman", "Innocent",
    "Minute Maid", "Fanta", "Capri-Sun", "Pom'Potes",
)


def _fold(text) -> str:
    folded = unicodedata.normalize("NFKD", str(text).lower().replace("’", "'"))
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _word_pattern(words) -> re.Pattern:
    parts = [part for part in re.split(r"[\s\-']+", _fold(words)) if part]
    return re.compile(r"(?<![a-z0-9])" + r"[\s\-']*".join(map(re.escape, parts)) + r"(?![a-z0-9])")


_HUMAN_PATTERNS = tuple((name, _word_pattern(name)) for name in HUMAN_FIRST_NAMES)
_BRAND_PATTERNS = tuple((brand, _word_pattern(brand)) for brand in NAME_BRANDS)


# ------------------------------------------------------------------ the records

def list_recipe_ids() -> list:
    """Sorted ids of the shipped recipes (file stems in templates/recipes/)."""
    if not RECIPES_DIR.is_dir():
        return []
    return sorted(p.stem for p in RECIPES_DIR.glob("*.json"))


@functools.lru_cache(maxsize=None)
def _load_cached(recipe_id: str) -> dict:
    with open(RECIPES_DIR / f"{recipe_id}.json", encoding="utf-8") as fh:
        data = json.load(fh)
    errors = schemas.recipe_errors(data)
    if not errors and data["id"] != recipe_id:
        errors = [f"$.id: {data['id']!r} is not the file's name {recipe_id!r}"]
    if errors:
        raise schemas.SchemaError(recipe_id, errors)
    return data


def load(recipe_id) -> dict:
    """One shipped recipe, validated (``schemas.recipe_errors``); ``KeyError``
    for an id that is not one (checked before any path is built from it)."""
    if not isinstance(recipe_id, str) or not _ID_PATTERN.match(recipe_id) or recipe_id not in list_recipe_ids():
        raise KeyError(recipe_id)
    return copy.deepcopy(_load_cached(recipe_id))


def for_story(story):
    """The recipe *story* is made with, or None: no ``recipe`` (a story
    created before plan 32, or without a preset), a null one, or an id no
    shipped recipe has -- never an error at prompt time."""
    recipe_id = (story or {}).get("recipe")
    if not recipe_id:
        return None
    try:
        return load(recipe_id)
    except KeyError:
        return None


def ends_on_card(story, cliffhanger_style) -> bool:
    """Whether *story*'s episodes end on the end card (plan 32 stage 4): the
    style's cliffhanger cuts to black (the card follows the fade), or the
    story's recipe asks for a card (``end_card``) even when the cliffhanger
    is a ``hard_stop`` -- the fruit drama's "Partie {n} demain". The script
    carries the answer as ``cliffhanger.cut_to_black``
    (``steps/script.skeleton``), which the timing and the render read. A
    story without a recipe: the style's answer alone, as before."""
    if cliffhanger_style == "cut_to_black":
        return True
    return bool((for_story(story) or {}).get("end_card"))


def end_card_line(recipe, next_ep):
    """The call-to-action line a recipe puts on the end card of the episode
    that leads to part *next_ep*: the recipe's ``end_card.text`` with ``{n}``
    filled, as written (the recipe's own language, whatever the story's), or
    None for no recipe or a recipe without an end card."""
    card = (recipe or {}).get("end_card")
    if not card:
        return None
    return card["text"].format(n=next_ep)


def check_id(recipe_id) -> None:
    """``store.create``'s check: None, or the id of a shipped recipe; else a
    ``ValueError`` in one sentence naming the shipped ones."""
    if recipe_id is None:
        return
    if not (isinstance(recipe_id, str) and re.fullmatch(schemas.RECIPE_ID_PATTERN, recipe_id)):
        raise ValueError(f"recipe must be an id of lowercase letters, digits and underscores, not {recipe_id!r}")
    if recipe_id not in list_recipe_ids():
        raise ValueError(f"unknown recipe {recipe_id!r} (shipped: {', '.join(list_recipe_ids())})")


# ------------------------------------------------------------------ what the writers read

def _names(recipe, count=None) -> str:
    examples = recipe["naming"]["examples"]
    return ", ".join(example["name"] for example in examples[:count])


def _roles(recipe) -> str:
    roles = [f"the {role['role']}" for role in recipe["cast"]["roles"]]
    return ", ".join(roles[:-1]) + " and " + roles[-1] if len(roles) > 1 else "".join(roles)


def _never(recipe) -> str:
    forbidden = {"brands": "a brand", "plain human names": "a plain human first name"}
    return " or ".join(forbidden[item] for item in recipe["naming"]["forbidden"])


def _question(recipe) -> str:
    return recipe["closing_question"]["template"].format(a="X", b="Y")


def _end_card(recipe) -> str:
    return recipe["end_card"]["text"].format(n="N")


def setup_parts(recipe) -> list:
    """The RECIPE section of the set-up block (``context.setup_context``): the
    names, the fixed cast, the beats, the closing question and the end card,
    the voice direction and the guardrails -- the episode's seconds stay
    FORMAT AND TIMING's."""
    naming, cast, beats = recipe["naming"], recipe["cast"], recipe["beats"]
    lo, hi = recipe["format"]["scenes"]
    label = recipe["label"]["en"]
    return [
        f"{label}.",
        f"Names: {naming['rule']}, {naming['style']} style ({_names(recipe, 3)}); one species per character; "
        f"never {_never(recipe)}.",
        f"Fixed cast of {cast['size'][0]} to {cast['size'][1]} recurring characters: {_roles(recipe)}.",
        f"Episodes: {lo} to {hi} scenes, {'one conflict' if beats['one_conflict'] else 'its conflicts'}: a recap "
        f"of at most {beats['recap_max_words']} words (from episode {beats['recap_from_episode']}), confrontation, "
        f"peak ({beats['peak_note']}), a cliffhanger of at most {beats['cliffhanger_max_words']} words, then "
        f"\"{_question(recipe)}\" and the end card \"{_end_card(recipe)}\".",
        f"Voices: {recipe['voice_direction']}.",
        f"Always: {'; '.join(recipe['guardrails'])}.",
    ]


def c1v2_names_ask(recipe) -> str:
    """C1v2's names and cast line (the cast ask's rule on a recipe story)."""
    naming, cast = recipe["naming"], recipe["cast"]
    first, second = naming["examples"][:2]
    card = min(CARD_CAST_MAX, cast["size"][1])
    return (f"- names: {naming['rule']}, {naming['style']} style ({first['name']} the {first['species']}, "
            f"{second['name']} the {second['species']}); one species per character; never {_never(recipe)} "
            "(a name the brief gives is kept). The card's characters open the series' fixed cast of "
            f"{cast['size'][0]} to {cast['size'][1]} recurring characters: give {card}, in its roles "
            f"({_roles(recipe)})\n")


def name_refusal(name, recipe, *, brief="") -> str:
    """Why *name* breaks *recipe*'s naming rule (a plain sentence the writer can act on), or "": a brand
    (:data:`NAME_BRANDS`) or a plain human first name (:data:`HUMAN_FIRST_NAMES`), whole words, accent- and
    case-folded. A first name the *brief* itself gives is kept (the brief is binding); a brand never is."""
    folded = _fold(name)
    told = _fold(brief or "")
    first, second = recipe["naming"]["examples"][:2]
    rename = (f"rename this character with a French pun on its own species, {recipe['naming']['style']} style "
              f"(like {first['name']} or {second['name']})")
    if "brands" in recipe["naming"]["forbidden"]:
        for brand, pattern in _BRAND_PATTERNS:
            if pattern.search(folded):
                return f"{name!r} carries the brand {brand}; {rename}, no brand"
    if "plain human names" in recipe["naming"]["forbidden"]:
        for _human, pattern in _HUMAN_PATTERNS:
            if pattern.search(folded) and not pattern.search(told):
                return f"{name!r} is a plain human first name; {rename}"
    return ""


def name_errors(doc, recipe, *, brief="") -> list:
    """C1v2's names check on a recipe story: one told-why error per cast name of *doc* (``{"concepts": [...]}``)
    that is a plain human first name or carries a brand (:func:`name_refusal`)."""
    errors = []
    for i, concept in enumerate(doc.get("concepts") or ()):
        for j, member in enumerate(concept.get("cast_sketch") or ()):
            why = name_refusal(member.get("name") or "", recipe, brief=brief)
            if why:
                errors.append(f"$.concepts[{i}].cast_sketch[{j}].name: {why}")
    return errors


def e1_beats_line(recipe, ep) -> str:
    """E1v3's shape line on a recipe story: the beats in order (the recap from its episode on), one conflict,
    the scene count."""
    beats = recipe["beats"]
    lo, hi = recipe["format"]["scenes"]
    said = {
        "recap": f"the recap (at most {beats['recap_max_words']} words on screen)",
        "confrontation": "the confrontation (the setup and rising scenes)",
        "peak": f"the peak ({beats['peak_note']}: the scene a viewer sends a friend)",
        "cliffhanger": f"the cliffhanger (its reveal at most {beats['cliffhanger_max_words']} words)",
    }
    order = [said[beat] for beat in beats["order"] if beat != "recap" or ep >= beats["recap_from_episode"]]
    conflict = ("One conflict carries the whole episode: no subplot, no second conflict. " if beats["one_conflict"]
                else "")
    return (f"The recipe's beats, in order: {', '.join(order)}. {conflict}The recipe keeps an episode to {lo} to "
            f"{hi} scenes.\n\n")


def teaser_ask(recipe) -> str:
    """E3v3's teaser ask on a recipe story whose question goes in the teaser (else None)."""
    if "teaser" not in recipe["closing_question"]["where"]:
        return None
    return ("- teaser: one sentence about the next episode, at most 15 words, story language, ending on the "
            f"question \"{_question(recipe)}\" (X and Y: the two characters this episode sets against each other, "
            "by name)")


def m1_line(recipe) -> str:
    """M1's line on a recipe story: the teaser already asks the side-taking question."""
    return (f"The teaser ends on the series' side-taking question ({_question(recipe)}); the app adds it after your "
            "description and pins it in the comments: do not ask it again.\n\n")


_HAS_QUESTION = re.compile(r"\bteam\b[^?]*\?", re.IGNORECASE)


def closing_question(recipe, teaser, names) -> str:
    """The question the pinned comment adds on a recipe story when the *teaser* lacks one: the recipe's
    template on the episode's first two characters (*names*, cast order). None without a recipe, when the
    recipe does not pin it, when the teaser already asks it, or with fewer than two names."""
    if not recipe or "pinned_comment" not in recipe["closing_question"]["where"]:
        return None
    if _HAS_QUESTION.search(teaser or ""):
        return None
    names = [name for name in names or () if name]
    if len(names) < 2:
        return None
    return recipe["closing_question"]["template"].format(a=names[0], b=names[1])

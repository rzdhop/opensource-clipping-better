"""AI Story plan 23 stage D4: the body rule.

``generation_profile.body_rule`` (absent = the style's ``default_body_rule``,
``human_body`` for every shipped style: the rules each template wrote) can be
``all_matter``: when the style is locked, the lock's ``character_design_rules``
become the style's ``body_rules.all_matter`` -- "the entire body including
arms, hands, legs and feet is made of {material}; no human skin anywhere" and
the dress rule -- with ``{material}`` the style's ``default_material`` (a
later stage lets a universe override it: ``stylelock.lock_style(material=)``).

What these tests pin: Fruit Drama's own ``character_design_rules`` byte for
byte (and the other six styles, which gain nothing); the lock built from a
template carries none of the new keys; ``lock_style`` rewrites the rules only
for ``all_matter``; a lock already frozen is never touched; the story's
approval of its style does it, once, with a sentence when the style defines no
such rule; the rules reach a character sheet's prompt.

Stdlib + pytest (DEC-012); offline.
"""

from __future__ import annotations

import copy

import pytest

from clipping.aistory import defaults, media_policy, prompting, schemas, stylelock, templates, workflow
from clipping.aistory.store import StoryStore

NOW = "2026-10-04T10:00:00+00:00"
LATER = "2026-10-04T11:00:00+00:00"

# Fruit Drama's character_design_rules, as every locked Fruit Drama style carries them.
FRUIT_DRAMA_RULES = (
    "The head is one recognisable whole fruit or vegetable at human head scale; the face (eyes, brows, mouth with "
    "teeth) is carved into its surface, not pasted on. Bodies are human, dressed in realistic contemporary clothes "
    "that carry the character's signature items and tell their social status (a torn tee and backpack vs a black "
    "suit and tie). No hands as fruit — hands are human. Keep exact fruit species, ripeness, colour and outfit "
    "identical in every image.")
MATERIAL = ("the character's own fruit or vegetable flesh with hyper-detailed natural texture, subsurface "
            "scattering, pores, seeds, juice reflections and small imperfections")
ALL_MATTER = (
    f"The entire body including arms, hands, legs and feet is made of {MATERIAL}; no human skin anywhere. Real "
    "hair. Realistic adult proportions, never chibi, never baby-cartoon. Realistic eyes with a detailed iris. "
    "Fully dressed from shoulders to feet: a complete top, a complete bottom below the knee and shoes; no bare "
    "legs, no visible underwear.")
NEW_KEYS = ("body_rules", "default_material", "default_body_rule")
OTHER_STYLES = [style for style in templates.list_style_ids() if style != "fruit_drama"]


# ================================================================ the template

def test_fruit_drama_gains_only_its_body_rules_and_its_own_rules_are_byte_identical():
    template = templates.load_style("fruit_drama")
    assert template["character_design_rules"] == FRUIT_DRAMA_RULES
    assert template["body_rules"] == {"all_matter": ALL_MATTER.replace(MATERIAL, "{material}")}
    assert template["default_material"] == MATERIAL
    assert template["default_body_rule"] == "human_body"
    assert schemas.style_template_errors(template) == []
    assert templates.load_style("fruit_drama")["version"] == 1  # a bump would stale every draft lock


@pytest.mark.parametrize("style_id", OTHER_STYLES)
def test_the_other_styles_define_no_body_rule_and_default_to_human_bodies(style_id):
    template = templates.load_style(style_id)
    assert not set(NEW_KEYS) & set(template)
    assert media_policy.body_rule({"style_template_id": style_id}) == "human_body"
    assert stylelock.all_matter_rules(style_id) is None


def test_the_template_schema_checks_the_three_optional_keys():
    template = templates.load_style("fruit_drama")
    for key, bad in (("default_body_rule", "no_skin"), ("default_material", ""), ("body_rules", {"all_matter": ""}),
                     ("body_rules", {"other": "x"}), ("body_rules", {})):
        assert schemas.style_template_errors(dict(template, **{key: bad})), (key, bad)
    plain = {key: value for key, value in template.items() if key not in NEW_KEYS}
    assert schemas.style_template_errors(plain) == []  # the keys are optional


# ================================================================ the lock

def test_a_lock_built_from_the_template_carries_none_of_the_new_keys_and_the_same_rules():
    lock = stylelock.build_style_lock(templates.load_style("fruit_drama"), {}, now=NOW)
    assert not set(NEW_KEYS) & set(lock)
    assert lock["character_design_rules"] == FRUIT_DRAMA_RULES
    assert schemas.style_lock_errors(lock) == []


def test_locking_rewrites_the_rules_only_for_all_matter():
    draft = stylelock.build_style_lock(templates.load_style("fruit_drama"), {}, now=NOW)
    unchanged = stylelock.lock_style(draft, now=LATER)
    assert unchanged == dict(copy.deepcopy(draft), locked_at=LATER)
    assert stylelock.lock_style(draft, now=LATER, body_rule="human_body") == unchanged
    assert stylelock.lock_style(draft, now=LATER, body_rule=None) == unchanged

    matter = stylelock.lock_style(draft, now=LATER, body_rule="all_matter")
    assert matter["character_design_rules"] == ALL_MATTER
    assert {key: value for key, value in matter.items() if key != "character_design_rules"} == {
        key: value for key, value in unchanged.items() if key != "character_design_rules"}
    assert schemas.style_lock_errors(matter) == []
    # The draft is not touched (a copy is returned).
    assert draft["character_design_rules"] == FRUIT_DRAMA_RULES and draft["locked_at"] is None


def test_the_material_slot_takes_a_hook_for_a_universe_and_the_style_default_otherwise():
    draft = stylelock.build_style_lock(templates.load_style("fruit_drama"), {}, now=NOW)
    cola = stylelock.lock_style(draft, now=LATER, body_rule="all_matter", material="glossy red aluminium")
    assert "is made of glossy red aluminium; no human skin anywhere." in cola["character_design_rules"]
    assert "fruit or vegetable flesh" not in cola["character_design_rules"]
    assert "{material}" not in cola["character_design_rules"]
    assert stylelock.all_matter_rules("fruit_drama") == ALL_MATTER


def test_a_style_with_no_all_matter_rule_cannot_be_locked_that_way_and_a_frozen_lock_is_never_touched():
    plain = stylelock.build_style_lock(templates.load_style("cartoon_flat"), {}, now=NOW)
    with pytest.raises(stylelock.StyleLockError, match="no all_matter body rule"):
        stylelock.lock_style(plain, now=LATER, body_rule="all_matter")
    assert plain["locked_at"] is None
    locked = stylelock.lock_style(plain, now=LATER)
    with pytest.raises(stylelock.StyleLockError, match="already locked"):
        stylelock.lock_style(locked, now=LATER, body_rule="all_matter")
    assert locked["character_design_rules"] == templates.load_style("cartoon_flat")["character_design_rules"]


# ================================================================ the accessor

def test_the_body_rule_is_the_stories_own_else_the_styles_default_else_human():
    assert media_policy.body_rule({"generation_profile": {"body_rule": "all_matter"}}) == "all_matter"
    assert media_policy.body_rule({"generation_profile": {"body_rule": "human_body"},
                                   "style_template_id": "fruit_drama"}) == "human_body"
    assert media_policy.body_rule({"generation_profile": {}, "style_template_id": "fruit_drama"}) == "human_body"
    assert media_policy.body_rule({}) == "human_body" and media_policy.body_rule(None) == "human_body"
    assert media_policy.body_rule({"style_template_id": "no_such_style"}) == "human_body"
    assert media_policy.body_rule({"generation_profile": {"body_rule": "bogus"}}) == "human_body"
    # The lock's own template wins over the story's field (the style being approved).
    assert media_policy.body_rule({"style_template_id": "cartoon_flat"}, "fruit_drama") == "human_body"


# ================================================================ the approval

def _story(tmp_path, *, style="fruit_drama", body_rule=None):
    from clipping.aistory import schemas as schemas_mod

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    profile = {"body_rule": body_rule} if body_rule else None
    story_id = store.create(language="fr", style_template_id=style, generation_profile=profile, now=NOW)["story_id"]
    draft = stylelock.build_style_lock(templates.load_style(style), {}, now=NOW)
    store.write_doc(story_id, "style_lock.json", draft, now=NOW, validator=schemas_mod.style_lock_errors)
    return store, story_id


def test_approving_the_style_writes_the_all_matter_rules_once(tmp_path):
    store, story_id = _story(tmp_path, body_rule="all_matter")
    story = workflow.approve_style(store, story_id, now=LATER)
    lock = store.read_doc(story_id, "style_lock.json")
    assert story["approvals"]["style"] == LATER and lock["locked_at"] == LATER
    assert lock["character_design_rules"] == ALL_MATTER
    # Frozen: the story can say what it likes afterwards, the lock is what it was.
    store.update(story_id, lambda doc: doc["generation_profile"].update(body_rule="human_body"), now=LATER)
    with pytest.raises(workflow.WorkflowError, match="already locked"):
        workflow.approve_style(store, story_id, now=LATER)
    assert store.read_doc(story_id, "style_lock.json")["character_design_rules"] == ALL_MATTER


@pytest.mark.parametrize("body_rule", [None, "human_body"])
def test_approving_the_style_of_an_ordinary_story_changes_nothing(tmp_path, body_rule):
    store, story_id = _story(tmp_path, body_rule=body_rule)
    draft = store.read_doc(story_id, "style_lock.json")
    workflow.approve_style(store, story_id, now=LATER)
    lock = store.read_doc(story_id, "style_lock.json")
    assert lock == dict(draft, locked_at=LATER, updated_at=LATER)  # as before this stage: the write stamps it
    assert lock["character_design_rules"] == FRUIT_DRAMA_RULES


def test_a_style_without_the_rule_refuses_the_approval_with_a_sentence_and_stays_a_draft(tmp_path):
    store, story_id = _story(tmp_path, style="cartoon_flat", body_rule="all_matter")
    with pytest.raises(workflow.WorkflowError, match="does not define") as caught:
        workflow.approve_style(store, story_id, now=LATER)
    assert "cartoon_flat" in str(caught.value) and "Bodies" in str(caught.value)
    lock = store.read_doc(story_id, "style_lock.json")
    assert lock["locked_at"] is None and store.get(story_id)["approvals"]["style"] is None


# ================================================================ the sheets

def test_the_all_matter_rules_reach_a_sheet_prompt_when_the_budget_allows_and_the_goldens_stay():
    look = "a tall kiwi man with a fuzzy brown head, wearing a white linen shirt"
    draft = stylelock.build_style_lock(templates.load_style("fruit_drama"), {}, now=NOW)
    human = stylelock.lock_style(draft, now=LATER)
    matter = stylelock.lock_style(draft, now=LATER, body_rule="all_matter")
    for builder, kwargs in ((prompting.two_view_prompt_v2, {}), (prompting.portrait_prompt_v2, {"budget": 220})):
        plain = builder(human, look_text=look, signature_items=["gold chain"], **kwargs)
        made = builder(matter, look_text=look, signature_items=["gold chain"], **kwargs)
        assert "Bodies are human" in plain and "no human skin anywhere" not in plain
        assert "is made of the character's own fruit or vegetable flesh" in made and "Bodies are human" not in made
    # The three-sheet prompts of a human-bodied style are byte for byte what the template's rules give.
    portrait = prompting.portrait_prompt_v2(human, look_text=look, signature_items=["gold chain"])
    assert portrait == prompting.portrait_prompt_v2(
        templates.load_style("fruit_drama"), look_text=look, signature_items=["gold chain"])
    assert defaults.BODY_ALL_MATTER == "all_matter" and defaults.BODY_HUMAN == "human_body"

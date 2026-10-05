"""Concept fidelity (plan 22 stage 2, DEC-274): with a non-empty ``seed_text``
and ``generation_profile.writing == "v3"``, "Generate 10 more" writes every
card to tell the user's brief -- C1v2 instead of C1, one angle per call
(``prompts.C1_ANGLES``), a rule check inside the call's own validator
(:func:`prompts.c1v2_errors`) and a brief judge (C1J) once a reply is
accepted. Without the gate, nothing changes (RC-W2).

Same harness as ``test_story_steps.py`` (``FakeRunner``, ``_ctx``): imported
as a module, the precedent ``test_story_fast_track_story.py`` already sets.
Stdlib + pytest only (DEC-012); no network.
"""

from __future__ import annotations

import pytest

import test_story_steps as tss
from clipping.aistory import context, defaults, prompts, schemas, templates
from clipping.aistory.steps import concepts as concepts_step
from clipping.aistory.steps.llm_call import StepFailed

STYLE_IDS = templates.list_style_ids()
FRUIT_DRAMA = templates.load_style("fruit_drama")
NOW = tss.NOW
SETTINGS = tss.SETTINGS
LINK = tss.LINK

BRIEF = (
    "Dans le couloir d'une academie de maquillage, Rouge, une heritiere hautaine, "
    "coince Nude, une nouvelle eleve qui ne decapuchonne jamais son rouge a levres. "
    "Elle la traite d'imposteure devant toute la classe et exige qu'elle avoue "
    "son secret avant la fin de la journee, sinon elle la fera renvoyer de l'ecole."
)


def _v3_story(store, *, seed=BRIEF, style="fruit_drama", **profile):
    """A French story on the v3 gate: a non-empty brief and
    ``generation_profile.writing == "v3"``."""
    generation_profile = {"writing": defaults.WRITING_V3, **profile}
    return store.create(language="fr", seed_text=seed, style_template_id=style,
                        generation_profile=generation_profile, now=NOW)["story_id"]


def _card(*, title="Le Couloir des Masques", names=("Rouge", "Nude"), style_fit="fruit_drama"):
    """A C1v2-shaped concept whose cast names *names* (so the rule check
    passes on its own)."""
    cast = [{"name": name, "role": "lead" if i == 0 else "support",
            "one_line": f"{name} tient tete a l'academie."} for i, name in enumerate(names)]
    cast.append({"name": "Pamplemousse", "role": "recurring", "one_line": "La surveillante qui voit tout."})
    return {
        "title": title,
        "logline": f"{names[0]} affronte {names[1]} dans le couloir de l'academie de maquillage.",
        "world": "Une academie de maquillage ou chaque eleve doit prouver sa place dans le couloir.",
        "cast_sketch": cast,
        "hook_formula": "Une accusation publique des la premiere seconde.",
        "value": "La peur de ne pas etre a sa place.",
        "retention_mechanics": "Le secret n'est pas encore revele.",
        "style_fit": style_fit,
    }


# ====================================================================== golden

def test_build_c1v2_golden_fr():
    pack = context.build_pack(
        language="fr",
        template=FRUIT_DRAMA,
        brief_text=BRIEF,
        avoid_titles=["Le Couloir de la Honte", "Academie des Levres"],
    )
    system, user, schema = prompts.build_c1_v2(pack, style_ids=STYLE_IDS, batch=3, of=10,
                                               angle=prompts.C1_ANGLES[2])

    expected_system = (
        "You are the head writer of a serialized vertical-video fiction "
        "series for TikTok, YouTube Shorts and Instagram Reels. Each "
        "episode lasts about 60 seconds and ends on a cliffhanger, so "
        "every idea must pay off in seconds and make people come back. "
        "Reply with JSON only, matching the schema. Never output "
        "durations, timestamps or file paths. Never use real people, "
        "brands, studio names or copyrighted characters. Write all "
        "user-facing text in French. Fields marked (English) are for "
        "image and voice models: write them in English."
    )
    expected_user = (
        "The user's brief -- binding:\n<<<\n" + BRIEF + "\n>>>\n\n"
        "Visual style: Fruit Drama — saturated natural fruit colours "
        "against warm neutral sets. Performance: over-acted telenovela "
        "delivery, exaggerated emotion, crisp diction, quick pace.\n\n"
        "Do not repeat or closely imitate these existing titles: Le "
        "Couloir de la Honte, Academie des Levres\n\n"
        "Write exactly 1 concept for a serialized vertical-video fiction "
        "series that tells this brief (call 3 of 10).\n\n"
        "The brief is binding. Keep exactly what it gives: every named "
        "character (same name, role and relationships), the setting, the "
        "premise and its central conflict, the genre, the tone, and every "
        "event it describes. Invent only what it leaves open. Never "
        "rename, replace or drop a named character, never move the story "
        "elsewhere, never change what the conflict is about.\n\n"
        "This call's angle: from the antagonist's want. The angle chooses "
        "which side of the brief the concept leads with and what it puts "
        "first -- never the premise. It never adds an antagonist, a place "
        "or a secret the brief does not have, unless the brief leaves that "
        "open. Where the angle and the brief disagree, follow the brief.\n\n"
        "Give:\n"
        "- title: at most 8 words\n"
        "- logline: one complete sentence, at most 30 words: who wants "
        "what, who stands in the way, and what is at stake -- the "
        "brief's own conflict\n"
        "- world: the setting and premise as the brief gives them; if it "
        "gives none, invent one; at most 60 words\n"
        "- cast_sketch: 3 to 5 characters; every character the brief "
        "names comes first, with the brief's name and role; if the brief "
        "names more than 5, keep the 5 who carry the conflict and name the "
        "others in the world; each with a role (one of lead, support, "
        "recurring, guest) and a one-line description, at most 25 words\n"
        "- hook_formula: at most 25 words: what makes someone stop "
        "scrolling in the first seconds of episode 1\n"
        "- value: the real substance this story carries (a dilemma, a "
        "lesson, a truth about people)\n"
        "- retention_mechanics: at most 25 words: why someone who watched "
        "episode 1 comes back for episode 2\n"
        f"- style_fit: the visual style that best fits this concept, one "
        f"of {', '.join(STYLE_IDS)}\n\n"
        "Never use real people, brands, studio names or copyrighted "
        "characters."
    )
    assert system == expected_system
    assert user == expected_user
    assert schema == schemas.c1_schema(STYLE_IDS)


def test_c1_angles_ten_one_per_call():
    assert len(prompts.C1_ANGLES) == 10
    assert prompts.C1_ANGLES[0] == "the brief played straight"
    pack = context.build_pack(language="fr", brief_text=BRIEF)
    for n in range(1, 11):
        _, user, _ = prompts.build_c1_v2(pack, style_ids=STYLE_IDS, batch=n, of=10, angle=prompts.C1_ANGLES[n - 1])
        assert f"This call's angle: {prompts.C1_ANGLES[n - 1]}." in user
        assert f"(call {n} of 10)" in user


# ====================================================================== brief_entities

def test_brief_entities_names_quotes_and_named_after():
    brief = (
        'Rouge coince Nude. Elle la traite d\'imposteure. '
        'Une fille surnommee "la Souris" observe la scene. '
        "Un gardien appele Fernand surveille le couloir."
    )
    entities = [context._fold(e) for e in context.brief_entities(brief)]
    assert context._fold("Nude") in entities
    # The quoted span itself is "la Souris", lowercase-led (a French nickname's own
    # article) -- it no longer binds as a quoted span (a quote only binds when it
    # starts with a capital, DEC-274's quote-safety fix), but "Souris" alone is
    # still picked up by the general mid-sentence capitalisation rule.
    assert context._fold("la Souris") not in entities
    assert context._fold("Souris") in entities
    assert context._fold("Fernand") in entities
    # "Rouge" opens the first sentence: never an entity from capitalisation alone.
    assert context._fold("Rouge") not in entities


def test_brief_entities_skips_sentence_initial_and_stop_words():
    brief = "Elle la traite d'imposteure. Le Directeur observe. Lundi, tout commence."
    entities = [context._fold(e) for e in context.brief_entities(brief)]
    # "Elle" opens its sentence (a French pronoun, not a name).
    assert context._fold("Elle") not in entities
    # "Le" is on the stop-list even though capitalised mid-brief would not apply here;
    # "Directeur" (capitalised, non-initial, not stop-listed) is kept.
    assert context._fold("Directeur") in entities
    # "Lundi" (a day name) opens its own sentence and is also stop-listed.
    assert context._fold("Lundi") not in entities


def test_brief_entities_ignores_a_long_quoted_sentence():
    """A quote can hold a line of dialogue, not a name: bound whole, it would
    make ``c1v2_errors`` demand the card repeat that whole sentence verbatim
    and refuse every reply. Only a short (at most 4 words), capitalised quote
    reads as a name."""
    brief = 'Rouge dit : «Ce soir, quelqu\'un quitte l\'ile» a toute la classe.'
    entities = [context._fold(e) for e in context.brief_entities(brief)]
    assert context._fold("Ce soir, quelqu'un quitte l'ile") not in entities


def test_brief_entities_quoted_capitalised_name_still_binds():
    """A short, capitalised quote still reads as a name (unchanged)."""
    brief = 'Dans le couloir, «Rouge» coince une nouvelle venue.'
    entities = [context._fold(e) for e in context.brief_entities(brief)]
    assert context._fold("Rouge") in entities


def test_named_after_does_not_bind_a_preposition():
    """"named"/"called"/"appelé(e)" bind the name that follows, never a
    preposition: "named after", "called for" and "appelee pour" name
    nothing."""
    brief = "Le prix est named after the founder. Le signal est called for help. Elle est appelee pour temoigner."
    entities = [context._fold(e) for e in context.brief_entities(brief)]
    assert context._fold("after") not in entities
    assert context._fold("for") not in entities
    assert context._fold("pour") not in entities


# ====================================================================== rule check

def test_c1v2_errors_refuses_a_dropped_name():
    card = _card(names=("Mangue", "Kiwi"))  # neither name is in BRIEF
    reply = {"concepts": [card]}
    errors = prompts.c1v2_errors(reply, style_ids=STYLE_IDS, brief=BRIEF)
    assert "$.cast_sketch: the brief names Rouge; the concept never does -- keep it" in errors
    assert "$.cast_sketch: the brief names Nude; the concept never does -- keep it" in errors


def test_c1v2_errors_passes_when_names_are_kept():
    card = _card(names=("Rouge", "Nude"))
    reply = {"concepts": [card]}
    assert prompts.c1v2_errors(reply, style_ids=STYLE_IDS, brief=BRIEF) == []


def test_c1j_is_in_premium_prompt_ids():
    assert "C1J" in prompts.PREMIUM_PROMPT_IDS
    assert "C1v2" in prompts.PREMIUM_PROMPT_IDS


# ====================================================================== the step, end to end

def test_no_seed_or_no_stamp_keeps_c1_byte_identical(tmp_path):
    """RC-W2: without the gate (no seed, or no "writing": "v3"), the concepts
    step writes exactly today's C1 -- never C1v2, never a judge call."""
    from clipping.aistory.store import StoryStore

    store = StoryStore(tmp_path, on_log=lambda line: None)

    no_seed = store.create(language="fr", seed_text=None, style_template_id="fruit_drama",
                           generation_profile={"writing": "v3"}, now=NOW)["story_id"]
    assert concepts_step._writing_gate(store.get(no_seed)) is False

    no_stamp = store.create(language="fr", seed_text=BRIEF, style_template_id="fruit_drama",
                            generation_profile={"writing": "v2"}, now=NOW)["story_id"]
    assert concepts_step._writing_gate(store.get(no_stamp)) is False

    ctx, _log = tss._ctx(store, no_stamp, params={"count": 1})
    runner = tss.FakeRunner(tss.c1_reply(1))
    concepts_step.run(ctx, runner=runner)
    assert len(runner.calls) == 1
    assert "Invent exactly 1 original concept" in runner.calls[0]["user"]
    assert "The user's brief" not in runner.calls[0]["user"]


def test_concepts_step_kept_card_is_stored_with_brief_fit(tmp_path):
    from clipping.aistory.store import StoryStore

    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = _v3_story(store)
    assert concepts_step._writing_gate(store.get(story_id)) is True

    card = _card()
    ctx, log = tss._ctx(store, story_id, params={"count": 1}, settings_env=SETTINGS)
    runner = tss.FakeRunner(
        {"concepts": [card]},                       # C1v2, call 1
        {"kept": True, "missing": []},               # C1J
        link=LINK,
    )
    result = concepts_step.run(ctx, runner=runner)

    assert result["generated"] == 1
    assert len(runner.calls) == 2
    stored = schemas.validate(store.read_doc(story_id, "concepts.json"), schemas.STORY_CONCEPTS_SCHEMA)
    assert stored == []
    doc = store.read_doc(story_id, "concepts.json")
    written = doc["concepts"][0]
    assert written["brief_fit"] == {"kept": True, "missing": [], "checked_by": "C1J", "checked_at": written["brief_fit"]["checked_at"]}
    # The angle used is angle 1 (count: 1, DEC-270: "the idea is the concept").
    assert f"This call's angle: {prompts.C1_ANGLES[0]}." in runner.calls[0]["user"]


def test_concepts_step_not_kept_retries_once_then_flags_the_card(tmp_path):
    from clipping.aistory.store import StoryStore

    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = _v3_story(store)

    first = _card(title="Premiere Tentative")
    retried = _card(title="Deuxieme Tentative")
    ctx, log = tss._ctx(store, story_id, params={"count": 1}, settings_env=SETTINGS)
    runner = tss.FakeRunner(
        {"concepts": [first]},                                     # C1v2, call 1
        {"kept": False, "missing": ["le ton comique n'est pas garde"]},  # C1J, 1st verdict
        {"concepts": [retried]},                                   # C1v2 retry
        {"kept": False, "missing": ["le ton comique n'est toujours pas garde"]},  # C1J, 2nd verdict
        link=LINK,
    )
    concepts_step.run(ctx, runner=runner)

    assert len(runner.calls) == 4
    # The retry is told why (DEC-259): the misses are in its prompt.
    assert "le ton comique n'est pas garde" in runner.calls[2]["user"]
    assert runner.calls[2]["user"].startswith(runner.calls[0]["user"])

    doc = store.read_doc(story_id, "concepts.json")
    written = doc["concepts"][0]
    assert written["title"] == "Deuxieme Tentative"
    assert written["brief_fit"]["kept"] is False
    assert written["brief_fit"]["missing"] == ["le ton comique n'est toujours pas garde"]


def test_a_failed_rejudge_keeps_the_first_verdicts_drift_flag(tmp_path):
    """A known drift must never vanish into an approve-by-rule: when the
    retry's re-judge call fails outright, the retried card is still stored
    with the FIRST verdict's ``kept: false`` and ``missing`` -- never
    ``brief_fit`` left off (which would read as "never judged", not "judged,
    found drifted, re-judge failed"), and never ``kept: true`` by omission."""
    from clipping.aistory.store import StoryStore
    from clipping.providers.errors import ProviderError

    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = _v3_story(store)

    first = _card(title="Premiere Tentative")
    retried = _card(title="Deuxieme Tentative")
    ctx, log = tss._ctx(store, story_id, params={"count": 1}, settings_env=SETTINGS)
    runner = tss.FakeRunner(
        {"concepts": [first]},                                     # C1v2, call 1
        {"kept": False, "missing": ["le ton comique n'est pas garde"]},  # C1J, 1st verdict
        {"concepts": [retried]},                                   # C1v2 retry
        ProviderError("every provider failed"),                    # C1J re-judge: fails outright
        link=LINK,
    )
    result = concepts_step.run(ctx, runner=runner)

    assert result["generated"] == 1
    assert len(runner.calls) == 4  # the re-judge was attempted, once
    doc = store.read_doc(story_id, "concepts.json")
    written = doc["concepts"][0]
    # The retried reply is kept as the card...
    assert written["title"] == "Deuxieme Tentative"
    # ...but its brief_fit is the first verdict's drift, not None and not kept.
    assert written["brief_fit"]["kept"] is False
    assert written["brief_fit"]["missing"] == ["le ton comique n'est pas garde"]
    assert written["brief_fit"]["checked_by"] == "C1J"
    assert any("C1J could not re-judge the retried card" in line for line in log)


def test_judge_usable_is_false_without_a_usable_premium_link(tmp_path):
    """``_judge_usable`` (the precondition the judge reads before a call,
    never attempting one it already knows would be refused): false when the
    premium chain names only a paid link with no key and ``allow_paid`` is
    off -- the exact precondition ``llm_call.call_json`` itself enforces."""
    from clipping.aistory.store import StoryStore

    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = _v3_story(store)
    ctx, _log = tss._ctx(
        store, story_id, params={"count": 1},
        settings_env={"STORY_LLM_PREMIUM_CHAIN": "openrouter/some-paid-model"},
    )
    assert concepts_step._judge_usable(ctx) is False

    usable_ctx, _log2 = tss._ctx(store, story_id, params={"count": 1}, settings_env=SETTINGS)
    assert concepts_step._judge_usable(usable_ctx) is True


def test_concepts_step_logs_and_keeps_the_card_when_the_judge_call_itself_fails(tmp_path):
    """A judge call that fails outright (every link's reply rejected, say)
    never fails the card's own generation -- logged, ``brief_fit`` left off,
    never silent (module docstring)."""
    from clipping.aistory.store import StoryStore
    from clipping.providers.errors import ProviderError

    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = _v3_story(store)

    card = _card()
    ctx, log = tss._ctx(store, story_id, params={"count": 1}, settings_env=SETTINGS)
    runner = tss.FakeRunner(
        {"concepts": [card]},                 # C1v2, call 1
        ProviderError("every provider failed"),  # C1J: fails outright
        link=LINK,
    )
    result = concepts_step.run(ctx, runner=runner)

    assert result["generated"] == 1
    assert len(runner.calls) == 2  # the judge was attempted, once
    doc = store.read_doc(story_id, "concepts.json")
    assert "brief_fit" not in doc["concepts"][0]
    assert any("C1J could not judge this card" in line for line in log)


# ====================================================================== agent mode

def test_agent_mode_stops_on_a_drifted_card(tmp_path):
    """Plan 21/22 (DEC-270, DEC-274): a drifted v3 card stops the agent run
    before it is approved by rule."""
    from clipping.aistory.steps import story_fast_track
    from clipping.aistory.store import StoryStore
    from clipping.aistory import steps as steps_mod
    from clipping.cancel import CancelToken

    store = StoryStore(tmp_path, on_log=lambda line: None)
    story_id = _v3_story(store, mode="agent")

    # The concept already generated and flagged as drifted (as the step
    # would have left it after a failed retry) -- isolates the agent run's
    # own stop-on-drift rule from the generation/judge machinery above.
    now = NOW
    card = dict(_card(), concept_id="gen_01", source="generated", prompt_version=prompts.PROMPT_VERSION,
               created_at=now, language="fr",
               brief_fit={"kept": False, "missing": ["le secret n'est jamais revele"],
                         "checked_by": "C1J", "checked_at": now})
    store.write_doc(story_id, "concepts.json",
                    {"$schema": schemas.STORY_CONCEPTS_SCHEMA_NAME, "concepts": [card], "updated_at": now},
                    now=now, validator=schemas.story_concepts_errors)

    ctx = steps_mod.StepContext(
        job_id="job000000002", story_id=story_id, step=story_fast_track.STEP, ep=None, params={},
        cancel=CancelToken(), settings_env=SETTINGS, outputs_dir=store.outputs_dir,
        on_log=lambda line: None, on_sub_step=lambda name: None,
    )
    run = story_fast_track._AgentRun(
        ctx, runner=None, time_fn=lambda: 0.0, adapters=None, transport=None, sleep_fn=lambda s: None,
        transcribe=None, run_process=None, popen=None, clock=lambda: 0.0, detect=None, cover_process=None,
        custom_fonts_dir=None, profile="final",
    )
    with pytest.raises(StepFailed) as excinfo:
        run.concepts()
    assert "The concept drifted from your brief: le secret n'est jamais revele" in str(excinfo.value)

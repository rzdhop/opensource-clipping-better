"""The master prompt and the prompt templates (AI Story plan 26 stage 2; the
human, 2026-10-05: "the prompts are supposed to have very detailed
descriptions of personas, universe, context", "the handoff prompt >= 500
words", "the longer the context the better").

``clipping.aistory.prompt_templates`` builds, from the story's own records,
a MASTER block (SERIES, ART STYLE, CHARACTERS, PLACES, PROPS, AVOID), a
SCENE block per shot, and fits the whole to a link's word limit by dropping
sections in a fixed order; the hashed core (today's prompt) is always last
and never touched. ``prompt_budgets.link_words`` / ``chain_words`` give the
limit: none for a manual or a local link.

Pure and offline (no store, no network); stdlib + pytest (DEC-012). The
fixture is shaped like Dragon Fruit (e7412a3efcc6): a dragon-fruit lead
called by his name, a creature (an eggplant) called by its handle, three
humans, two places, two props, one speaking scene.
"""

from __future__ import annotations

import copy
import types

from clipping.aistory import prompt_budgets, prompt_templates as pt, shots

# ------------------------------------------------------------------ fixture

STYLE = {
    "template_id": "fruit_drama",
    "rendering": ("photorealistic 3D render of anthropomorphic fruits and vegetables with expressive human-like faces "
                  "(eyes, brows, mouths) on realistic fruit heads, human-proportioned bodies in real fabric outfits, "
                  "subsurface scattering on fruit skin, visible pores and fuzz, glossy highlights, high-end CGI "
                  "commercial quality, Octane-style render"),
    "camera": ("telenovela coverage: medium two-shots for dialogue, tight close-ups for reactions, slow push-in on "
               "reveals, 50mm look, shallow depth of field"),
    "lighting": "warm key light with a soft cool fill, golden-hour or practical interior lamps, dramatic rim light",
    "character_design_rules": ("The head is one recognisable whole fruit or vegetable at human head scale; the face "
                               "(eyes, brows, mouth with teeth) is carved into its surface, not pasted on. Bodies are "
                               "human, dressed in realistic contemporary clothes. No hands as fruit -- hands are "
                               "human. Keep exact fruit species, ripeness, colour and outfit identical in every "
                               "image."),
    "environment_rules": ("real-world sets photographed like a reality-TV show or a live-action comedy, props at "
                          "human scale; interiors cold blue-grey with warm lamp practicals"),
    "negative_prompt": "cartoon, 2D, flat shading, anime, fruit with stick limbs, fruit bowl, food photography, "
                       "human head",
    "quality_tail": "ultra detailed, 8k, sharp focus",
    "palette": {"primary": ["#F2C14E", "#E4572E", "#3A7D44"], "accents": ["#FFFFFF", "#1E1E24"],
                "forbidden": ["neon green", "hot pink backgrounds"],
                "palette_line": "saturated natural fruit colours against warm neutral sets"},
    "motion_rules": {"tier2_prompt_suffix": "subtle natural movement",
                     "tier2_prompt_suffix_v2": ("expressive character animation, clear gestures, lively faces, cloth "
                                                "sway, no morphing, no extra characters entering")},
    "audio": {"voice_direction": "over-acted telenovela delivery, exaggerated emotion, crisp diction, quick pace"},
}

STORY = {
    "story_id": "e7412a3efcc6", "title": "Dragon Fruit & Sales Queen", "language": "fr",
    "logline": ("Hacker Rida breaches the CRM; Sales Head Marie-Jeanne catches him live while CEO Victor watches, "
                "both fighting for the same client data and their secret attraction."),
    "premise": ("In a modern tech startup, charismatic hacker Rida infiltrates the CRM to steal a high-stakes "
                "contract. Sales director Marie-Jeanne catches the breach in real time. Victor pits them together."),
    "tone": "Sexy tech thriller, witty romantic tension",
    "genre_tags": ["Romantic Thriller", "Tech Noir", "Office Romance"],
    "world": {
        "setting_summary": ("A sleek, open-plan tech startup where glass-walled offices blur into a neon-lit "
                            "server room; the CRM is the beating heart, pulsing with high-stakes contracts."),
        "rules": ["Every breach triggers a live dashboard alert.", "No real names in comms -- handles only.",
                  "Victor assigns joint tasks to force collisions."],
        "time_period": "Near-future present day",
        "recurring_motifs": ["Split-screen countdown vs. dashboard spike", "Dragon-fruit curls catching neon light"],
    },
    "themes_and_values": ["Power and vulnerability share the same source code",
                          "Love is the only exploit you cannot patch"],
    "generation_profile": {"pipeline": "v2"},
}


def _wardrobe(*sets):
    return [{"id": sid, "context": context, "items": items} for sid, context, items in sets]


RIDA = {
    "char_id": "char_rida", "name": "Rida", "role": "lead",
    "descriptor": ("Curly dragon-fruit hair, black eyes, sharp jawline, athletic build, wearing a torn black hoodie "
                   "and jeans with a backpack slung over one shoulder."),
    "signature_items": ["Backpack with glowing USB drive", "Silver hoop earring in left ear",
                        "Fingerless gloves with circuit patterns"],
    "personality": {"traits": ["Brilliant", "Charming", "Rebellious", "Secretly vulnerable"],
                    "wants": "To win the trust of Marie-Jeanne", "fears": "Being exposed as a fraud",
                    "speech_style": "Witty, fast-paced, poetic tech metaphors laced with flirtation"},
    "relationships": {"char_marie_jeanne": "Forbidden attraction turned project partners.",
                      "char_victor": "Amused mentor who keeps pushing them together."},
    "voice": {"direction": "Speak with energetic charm, quick wit and a teasing rhythm",
              "sample_line": "You can't firewall what you feel."},
    "voice_hints": {"gender": "male", "age": "young", "style_tags": ["bright", "playful"],
                    "direction": "Speak with energetic charm, quick wit and a teasing rhythm",
                    "sample_line": "You can't firewall what you feel."},
    "look": {
        "build": "lean athletic frame, broad shoulders, narrow waist",
        "silhouette": "upright hacker stance, backpack slung low",
        "face": "dragon fruit head, carved black eyes, sharp jawline",
        "hair": "curly magenta dragon fruit scales, wild volume",
        "skin_material": "dragon fruit skin, subtle pores, glossy highlights",
        "height_cm": 180, "palette": ["magenta", "green", "black", "silver"],
        "wardrobe_sets": _wardrobe(("daily", "startup office and late night coding",
                                    "torn black hoodie, faded jeans, fingerless circuit gloves, silver hoop earring"),
                                   ("night_out", "underground tech meetups", "black bomber jacket, slim dark jeans")),
        "presentation": "man in late twenties", "bearing": "relaxed lean, one hand on backpack strap",
    },
}

MARIE_JEANNE = {
    "char_id": "char_marie_jeanne", "name": "Marie-Jeanne", "role": "lead",
    "descriptor": ("Light-chestnut curly hair, vivid green eyes, sharp cheekbones, fair skin, athletic build, "
                   "tailored charcoal blazer, silk ivory blouse, slim black trousers, leather loafers"),
    "signature_items": ["Gold pen clipped to blazer pocket", "Vintage mechanical watch on left wrist"],
    "personality": {"traits": ["Ruthlessly strategic", "Wickedly witty", "Control-obsessed"],
                    "wants": "Prove she runs the game, not Victor", "fears": "Losing control",
                    "speech_style": "Rapid-fire precision, surgical sarcasm"},
    "relationships": {"char_rida": "Rival hacker turned forced partner, secret attraction."},
    "voice": {"direction": "Crisp French with English tech terms, commanding yet flirtatious"},
    "look": {
        "build": "Athletic, toned frame, narrow waist, broad shoulders",
        "silhouette": "Sharp tailored lines, confident upright posture",
        "face": "Sharp cheekbones, vivid green eyes, fair skin",
        "hair": "Light-chestnut curls, voluminous, shoulder-length",
        "skin_material": "Fair human skin, smooth texture",
        "height_cm": 170, "palette": ["charcoal", "ivory", "black", "gold"],
        "wardrobe_sets": _wardrobe(("daily", "Office sales director daily wear",
                                    "Charcoal blazer, ivory silk blouse, black trousers, leather loafers, gold pen")),
        "presentation": "Woman in her thirties", "bearing": "Stands very straight, chin up, commanding presence",
    },
}

CHLOE = {
    "char_id": "char_chloe", "name": "Chloe", "role": "support",
    "descriptor": ("Sleek silver bob, piercing hazel eyes, porcelain skin, slender frame in crisp ivory blazer with "
                   "structured shoulders, tailored charcoal trousers, minimalist silver watch"),
    "signature_items": ["Tablet with holographic pipeline dashboard", "Single pearl ear cuff"],
    "personality": {"traits": ["Ruthlessly efficient", "Ice-cold composure"], "wants": "Control every variable",
                    "fears": "Unpredictable human emotions", "speech_style": "Clipped sentences, zero filler"},
    "relationships": {"char_marie_jeanne": "Fiercely loyal executor, anticipates every order"},
    "look": {
        "build": "Slender athletic frame, narrow shoulders, long legs",
        "silhouette": "Sharp vertical line, structured shoulders, narrow waist",
        "face": "Porcelain skin, piercing hazel eyes, defined cheekbones",
        "hair": "Sleek silver bob, chin-length, blunt cut",
        "skin_material": "Smooth porcelain skin, matte finish",
        "height_cm": 168, "palette": ["ivory", "charcoal", "silver", "pearl"],
        "wardrobe_sets": _wardrobe(("daily", "Office, pipeline monitoring, meetings",
                                    "Crisp ivory blazer, charcoal trousers, silver watch, pearl ear cuff")),
        "presentation": "Woman in early thirties", "bearing": "Stands very straight, chin up, precise gestures",
    },
}

SAM = {
    "char_id": "char_sam", "name": "Sam", "role": "support",
    "descriptor": ("Messy dark curls, olive skin, sharp brown eyes behind rectangular glasses, lean frame, perpetual "
                   "five-o'clock shadow, wears oversized hoodies"),
    "signature_items": ["Sticker-covered mechanical keyboard", "Vintage Game Boy in pocket"],
    "personality": {"traits": ["Sarcastic loyalist", "Chaotic genius"], "wants": "Keep Rida out of prison",
                    "fears": "Rida getting caught", "speech_style": "Rapid-fire tech slang, deadpan humor"},
    "relationships": {"char_rida": "Ride-or-die dev partner since bootcamp",
                      "char_marie_jeanne": "Calls her \"Ice Queen\" behind her back"},
    "look": {
        "build": "Lean lanky frame, narrow shoulders, long limbs",
        "silhouette": "Slouching posture, hoodie drapes, hands in pockets",
        "face": "Olive skin, sharp brown eyes, rectangular glasses, stubble",
        "hair": "Messy dark curls, medium length, uncontrolled volume",
        "skin_material": "Human skin, visible pores, olive undertone",
        "height_cm": 175, "palette": ["charcoal", "olive", "amber"],
        "wardrobe_sets": _wardrobe(("daily", "Dev cave, all-nighters", "Oversized charcoal hoodie, black joggers")),
        "presentation": "Man in late twenties", "bearing": "Perpetual slouch, leans into screens",
    },
}

# A creature: its descriptor names a species, so it is called by its handle, never by its name (DEC-302).
VICTOR = {
    "char_id": "char_victor", "name": "Victor", "role": "recurring",
    "descriptor": "Tall anthropomorphic eggplant with a glossy purple head and a tailored navy suit",
    "signature_items": ["gold pocket watch", "polished oxford shoes"],
    "personality": {"traits": ["calculating", "charming", "amused by chaos"],
                    "wants": "Watch Rida and Marie-Jeanne clash and fall for each other",
                    "fears": "Losing power", "speech_style": "Slow, precise, a sly half smile"},
    "relationships": {"char_rida": "Manipulates him with challenges he cannot refuse.",
                      "char_marie_jeanne": "Plays with her ambition like a cat with a mouse."},
    "look": {
        "build": "tall, broad-shouldered, athletic torso, narrow waist",
        "silhouette": "imposing vertical line, sharp shoulders, tailored taper",
        "face": "shiny purple eggplant head, deep-set dark eyes, sharp carved features",
        "hair": "smooth eggplant skin, green calyx crown, no hair",
        "skin_material": "glossy eggplant skin, subsurface scattering",
        "height_cm": 185, "palette": ["purple", "navy", "gold"],
        "wardrobe_sets": _wardrobe(("daily", "office, boardroom", "tailored navy suit, crisp white shirt")),
        "presentation": "man in his forties, authoritative presence",
        "bearing": "stands perfectly straight, hands clasped behind back",
    },
}

BULLPEN = {
    "place_id": "place_glass_walled_bullpen", "name": "Glass-Walled Bullpen",
    "descriptor": ("Glass-Walled Bullpen: open-plan workspace with transparent walls overlooking the server hub, "
                   "minimalist desks, standing monitors, and ambient LED strips tracing the ceiling edges."),
    "layout_notes": "Left: sales pods. Right: dev islands. Back: transparent wall. Foreground: collaboration table.",
    "time_variants": {"day": None, "night": None},
    "look": {
        "layout_map": {"left": "sales pods with dual screens facing the glass wall",
                       "right": "dev islands with ergonomic chairs and cable trays",
                       "back": "transparent wall revealing the server hub's neon glow",
                       "foreground": "central collaboration table with half-empty coffee cups", "centre": ""},
        "scale_note": "Spacious open-plan area, roughly 8 meters wide",
        "lighting": {"day": "bright natural daylight flooding through glass walls, soft shadows on desks",
                     "night": "ambient LED strips glowing cyan along the ceiling"},
        "props_here": ["prop_glowing_usb_drive"],
    },
}

SERVER_HUB = {
    "place_id": "place_neon_server_hub", "name": "Neon Server Hub",
    "descriptor": ("Neon Server Hub: a dimly lit server room with humming cooling fans, glass panels reflecting neon "
                   "light, and a pulsing CRM dashboard casting green and cyan glows across sleek metal racks."),
    "layout_notes": "Left: tall server racks. Right: glass wall. Back: main CRM dashboard screen.",
    "time_variants": {"day": None},
    "look": {
        "layout_map": {"left": "tall server racks with blinking LEDs", "right": "glass wall showing the office",
                       "back": "main CRM dashboard screen glowing with live data streams",
                       "foreground": "raised access floor with scattered fiber-optic cables", "centre": ""},
        "scale_note": "Spacious room, roughly 8 meters wide",
        "lighting": {"day": "diffuse daylight through high windows",
                     "night": "dominant neon cyan and green glows, deep shadows in corners"},
        "props_here": ["prop_glowing_usb_drive"],
    },
}

PEN = {
    "prop_id": "prop_marie_jeanne_s_silver_pen", "name": "Marie-Jeanne's Silver Pen",
    "descriptor": "Sleek silver metal pen with a satisfying click mechanism, weighty and balanced",
    "owner_char_id": "char_marie_jeanne",
    "look": {"scale_cm": 14, "material": "solid brushed stainless steel", "colour": "cool silver with mirror polish",
             "scale_phrase": "fits perfectly in one hand"},
}

USB = {
    "prop_id": "prop_glowing_usb_drive", "name": "Glowing USB Drive",
    "descriptor": "A sleek USB drive clipped to a backpack strap, pulsing with soft cyan light",
    "owner_char_id": "char_rida",
    "look": {"scale_cm": 10, "material": "matte black polymer with cyan LED strip",
             "colour": "black casing with soft cyan glow", "scale_phrase": "fits in one hand"},
}


def _entities():
    return {"characters": {doc["char_id"]: copy.deepcopy(doc) for doc in (RIDA, MARIE_JEANNE, SAM, CHLOE, VICTOR)},
            "places": {doc["place_id"]: copy.deepcopy(doc) for doc in (BULLPEN, SERVER_HUB)},
            "props": {doc["prop_id"]: copy.deepcopy(doc) for doc in (PEN, USB)}}


SCRIPT = {
    "ep": 1, "language": "fr",
    "scenes": [
        {"scene_id": "s01", "function": "hook", "place_id": "place_neon_server_hub", "time_variant": "night",
         "characters": ["char_rida", "char_sam", "char_victor"], "props": ["prop_glowing_usb_drive"],
         "summary": "Rida slips into the CRM while Victor watches from the dark.", "emotion": "tension",
         "lines": [{"line_id": "l01", "speaker": "narrator", "text": "Minuit. Le CRM respire encore.",
                    "emotion": "tension", "delivery": "Low and slow."}]},
        {"scene_id": "s02", "function": "setup", "place_id": "place_glass_walled_bullpen", "time_variant": "day",
         "characters": ["char_rida", "char_marie_jeanne", "char_chloe"], "props": ["prop_marie_jeanne_s_silver_pen"],
         "summary": "Marie-Jeanne wants to lead the call to lock Titan, while Rida wants to prove his charm.",
         "emotion": "scheming",
         "lines": [{"line_id": "l09", "speaker": "char_rida",
                    "text": "Laisse-moi charmer ce directeur, mon audace scellera notre victoire.",
                    "emotion": "scheming",
                    "delivery": "Charming, confident smirk, playful yet fiercely competitive tone."}]},
    ],
}

SHOT = {
    "shot_id": "sh21", "scene_id": "s02", "order": 1, "framing": "medium_two_shot", "camera_motion": "push_in",
    "subject_tags": ["@char_rida", "@char_marie_jeanne", "#place_glass_walled_bullpen:day",
                     "%prop_marie_jeanne_s_silver_pen"],
    "action": "@char_rida leans over the shared tablet while @char_marie_jeanne clicks %prop_marie_jeanne_s_silver_pen",
    "lines": ["l09"], "speaks": True,
    "staging": [{"subject": "@char_rida", "position": "right", "facing": "toward her", "expression": "smirk"},
                {"subject": "@char_marie_jeanne", "position": "left", "facing": "toward him",
                 "expression": "cool appraisal"},
                {"subject": "%prop_marie_jeanne_s_silver_pen", "position": "centre", "facing": "upward",
                 "expression": "none"}],
    "negative_prompt": "text, watermark, logo, extra fingers, cartoon, 2D",
}

# Today's hashed text: the action, the quoted line, the Audio sentence, the closing (kept byte for byte, last).
CORE = ("Slow push-in toward the subject. Rida, a man in late twenties in a torn black hoodie, leans over the shared "
        "tablet, looks at Marie-Jeanne and says in French, in a bright playful voice, \"Laisse-moi charmer ce "
        "directeur, mon audace scellera notre victoire.\" Marie-Jeanne listens without speaking, mouth closed. "
        "Audio: only Rida's voice speaking French, close and clear, lips in sync with the words. No music, no "
        "narrator, no other voice. No subtitles, no captions, no on-screen text.")


def _ec(entities=None, story=None, style=None):
    return types.SimpleNamespace(story=story or copy.deepcopy(STORY), style_lock=style or copy.deepcopy(STYLE),
                                 entities=entities or _entities(), language="fr", ep=1)


def _master_part(result, core=CORE):
    assert result["text"].endswith(core)
    return result["text"][:-len(core)]


# ================================================== (a) the size of the template

def test_the_clip_template_is_long_starts_with_the_series_and_ends_with_the_core():
    """On a Dragon-Fruit-shaped story the clip template is >= 500 words
    unbounded, opens on the SERIES paragraph, ends on the core byte for byte
    and nothing is dropped; its short-prompt warning is silent."""
    result = pt.shot_clip_prompt(_ec(), SHOT, SCRIPT, CORE, limit_words=None)

    assert result["full_words"] >= pt.MIN_PROMPT_WORDS == 500
    assert result["words"] == result["full_words"] == len(result["text"].split())
    assert result["text"].startswith("SERIES:")
    assert result["text"].endswith("\n\n" + CORE)
    assert result["dropped"] == [] and result["limit"] is None
    assert pt.short_warning(result["full_words"]) is None
    for label in ("ART STYLE:", "CHARACTER", "PLACE", "PROP", "AVOID:", "SCENE:"):
        assert label in result["text"]


def test_a_thin_story_gets_the_short_prompt_warning():
    """A story whose records are bare (a title, a rendering line, one
    character with a descriptor only) builds a short template, and
    ``short_warning`` says so with the count."""
    story = {"title": "Thin", "language": "en"}
    style = {"rendering": "flat colours"}
    entities = {"characters": {"char_a": {"char_id": "char_a", "name": "Ann", "descriptor": "a tall woman"}},
                "places": {}, "props": {}}
    script = {"scenes": [{"scene_id": "s01", "characters": ["char_a"], "lines": []}]}
    shot = {"shot_id": "sh01", "scene_id": "s01", "framing": "close_up", "camera_motion": "hold",
            "subject_tags": ["@char_a"], "lines": []}
    result = pt.shot_clip_prompt(_ec(entities, story, style), shot, script, "Ann waits.", limit_words=None)

    assert result["full_words"] < 500
    warning = pt.short_warning(result["full_words"])
    assert warning == (f"Short prompt: {result['full_words']} words — the template expects at least 500; the cast "
                       "and place records are thin.")


# ================================================== (b) the fit ladder

def test_the_fit_ladder_drops_in_order_keeps_the_shot_and_falls_to_the_core_alone():
    """Over a limit, sections go in ``DROP_ORDER`` (AVOID, the characters,
    places and props not in the shot, the lore, ...; the last of a rank
    first) until the text fits; the present character's look and the
    style's rendering survive a 230-word fit; a limit under the core gives
    the core alone with every section dropped; no limit drops nothing."""
    # A shot of Rida alone in the bullpen, a short core and a short style: the shot's own sections fit 230 words.
    style = copy.deepcopy(STYLE)
    style["rendering"] = "photorealistic 3D render of anthropomorphic fruits with carved faces on human bodies"
    style["character_design_rules"] = "The head is one whole fruit with a carved face; bodies and hands are human."
    style["environment_rules"] = "real-world sets at human scale"
    style["medium"] = "a CGI animated film"
    shot = dict(SHOT, subject_tags=["@char_rida", "#place_glass_walled_bullpen:day"], staging=[], speaks=False,
                lines=[])
    core = "Slow push-in toward the subject. The hacker leans over the tablet."
    ec = _ec(style=style)

    full = pt.shot_clip_prompt(ec, shot, SCRIPT, core, limit_words=None)
    fitted = pt.shot_clip_prompt(ec, shot, SCRIPT, core, limit_words=230)

    assert full["dropped"] == [] and full["text"].startswith("SERIES:")
    assert full["full_words"] > 230 >= fitted["words"] == len(fitted["text"].split())
    assert fitted["full_words"] == full["full_words"] and fitted["limit"] == 230
    assert fitted["text"].endswith("\n\n" + core)
    # The order: the ranks of what was dropped never go back down the ladder; the first is AVOID.
    ranks = [pt.DROP_ORDER.index(key) for key in _dropped_keys(ec, shot, fitted["dropped"])]
    assert ranks == sorted(ranks) and ranks[0] == pt.DROP_ORDER.index("avoid")
    assert fitted["dropped"][:5] == ["avoid", "Victor", "Chloe", "Sam", "Marie-Jeanne"]  # not in the shot, last first
    # What is never dropped: the style's rendering, the present character's look, the place and its light.
    assert style["rendering"] in fitted["text"]
    assert "Rida (in this shot) is an anthropomorphic character whose head is a whole dragon fruit" in fitted["text"]
    assert "curly magenta dragon fruit scales" in fitted["text"]
    assert "bright natural daylight" in fitted["text"]

    # A limit smaller than the core: the core alone, every section listed as dropped.
    tiny = pt.shot_clip_prompt(ec, shot, SCRIPT, core, limit_words=5)
    assert tiny["text"] == core and tiny["words"] == len(core.split())
    assert sorted(tiny["dropped"]) == sorted(s.label for s in _sections(ec, shot))

    # fits() is obeyed as well as the word count.
    chars = pt.shot_clip_prompt(ec, shot, SCRIPT, core, limit_words=None, fits=lambda text: len(text) <= 2000)
    assert len(chars["text"]) <= 2000 and chars["dropped"]


def test_the_style_rules_are_the_last_rung_so_a_small_cap_keeps_the_style_and_the_looks():
    """Plan 26 stage 4c, B. The style is two sections: ``style`` (the rendering sentence, the palette line, the
    forbidden colours -- never dropped) and ``style_rules`` (character design and environment rules, quality tail,
    motion, voice -- the last rung, after the secondary details and before the core alone). A cap that holds
    the never-dropped sections and the core but not the rules gives the style, the present look and the core --
    never the core alone; the rules are the last label dropped; a cap above them keeps them."""
    shot = dict(SHOT, subject_tags=["@char_rida", "#place_glass_walled_bullpen:day"], staging=[], speaks=False,
                lines=[])
    core = "Slow push-in toward the subject. The hacker leans over the tablet."
    ec = _ec()
    sections = _sections(ec, shot)
    by_key = {section.key: section for section in sections}
    rules, style = by_key["style_rules"], by_key["style"]
    assert pt.DROP_ORDER[-1] == "style_rules" and style.rank is None and rules.rank == len(pt.DROP_ORDER) - 1
    assert style.text.startswith("ART STYLE: Medium: ") and STYLE["rendering"] in style.text
    assert STYLE["palette"]["palette_line"] in style.text
    assert "Character design rules" not in style.text and "Environment rules" not in style.text
    assert rules.text.startswith("STYLE RULES: Character design rules:") and "Finish: ultra detailed" in rules.text
    assert "Motion:" in rules.text and "Voice direction:" in rules.text and "Camera" not in rules.text
    never = sum(len(section.text.split()) for section in sections if section.rank is None) + len(core.split())

    small = pt.shot_clip_prompt(ec, shot, SCRIPT, core, limit_words=never)
    assert small["text"] != core and small["text"].endswith("\n\n" + core) and small["words"] <= never
    assert small["dropped"][-1] == "style rules" and "art style" not in small["dropped"]
    assert style.text in small["text"] and rules.text not in small["text"]
    assert "Rida (in this shot) is an anthropomorphic character whose head is a whole dragon fruit" in small["text"]

    roomy = pt.shot_clip_prompt(ec, shot, SCRIPT, core, limit_words=never + len(rules.text.split()))
    assert rules.text in roomy["text"] and "style rules" not in roomy["dropped"]

    # Below the never-dropped sections and the core: the core alone, as before.
    assert pt.shot_clip_prompt(ec, shot, SCRIPT, core, limit_words=never - 1)["text"] == core


def test_a_prop_s_reference_prompt_has_no_size_and_no_owner_but_a_shot_s_prop_paragraph_keeps_both():
    """Plan 26 stage 4c, A. ``entity_prompt(..., kind="prop", for_reference=True)``: the prop's paragraph is
    its descriptor, material and colour -- no size phrase, no ``cm``, no owner (they invited a hand holding
    the object and a character into its reference image). Without the flag, and in the master's PROP
    paragraph of a shot, both stay."""
    entities = _entities()
    plain = pt.entity_prompt(STORY, STYLE, "prop", copy.deepcopy(PEN), "Prop core.", limit_words=None,
                             entities=entities, for_reference=True)
    paragraph = plain["text"].split("PROP:", 1)[1]
    assert "solid brushed stainless steel" in paragraph and "cool silver with mirror polish" in paragraph
    for word in ("cm", "Size", "belongs to", "one hand", "Marie-Jeanne", "the character"):
        assert word not in paragraph, word
    assert plain["text"].endswith("Prop core.")

    unflagged = pt.entity_prompt(STORY, STYLE, "prop", copy.deepcopy(PEN), "Prop core.", limit_words=None,
                                 entities=entities)
    assert "Size: fits perfectly in one hand, about 14 cm." in unflagged["text"]
    assert "belongs to" in unflagged["text"]

    master = "\n\n".join(section.text for section in pt.master_sections(
        STORY, STYLE, entities, language="fr", present=["char_rida", "char_marie_jeanne"],
        place_ids=["place_glass_walled_bullpen"], prop_ids=["prop_marie_jeanne_s_silver_pen"]))
    prop = master.split("PROP (in this shot):", 1)[1].split("\n\n", 1)[0]
    assert "about 14 cm" in prop and "Size:" in prop and "It belongs to" in prop


def test_a_two_character_speaking_shot_keeps_who_they_are_within_veos_limit():
    """On the rich records, a two-character speaking shot fitted to Veo's
    630 words keeps both looks -- what each is made of -- the place, the
    staging and the core: the ladder never falls to the core alone there."""
    veo = prompt_budgets.link_words("gemini/veo-3.1-lite", live={})
    result = pt.shot_clip_prompt(_ec(), SHOT, SCRIPT, CORE, limit_words=veo)

    assert result["full_words"] > veo >= result["words"]
    assert result["text"].endswith("\n\n" + CORE)
    assert "Rida (in this shot) is an anthropomorphic character whose head is a whole dragon fruit" in result["text"]
    assert "Marie-Jeanne (in this shot) is a human" in result["text"]
    assert "ART STYLE:" in result["text"] and "PLACE (in this shot):" in result["text"]
    assert "Staging: Rida on the right" in result["text"]
    assert {"Sam", "Chloe", "Victor"} <= set(result["dropped"])


def _sections(ec, shot):
    """The sections the clip template fits (master + scene), via the fit's own input."""
    captured = {}
    real = pt.fit

    def spy(sections, core, **kwargs):
        captured["sections"] = sections
        return real(sections, core, **kwargs)

    pt.fit = spy
    try:
        pt.shot_clip_prompt(ec, shot, SCRIPT, "x", limit_words=None)
    finally:
        pt.fit = real
    return captured["sections"]


def _dropped_keys(ec, shot, labels):
    by_label = {s.label: s for s in _sections(ec, shot)}
    return [pt.DROP_ORDER[by_label[label].rank] for label in labels]


# ================================================== (c) hygiene

def test_the_master_carries_no_quote_no_audio_sentence_and_names_only_the_named_casts():
    """Veo speaks quoted text: the master holds no double quote, no
    ``Audio:``, no ``says in`` and no dialogue line. A creature cast (the
    eggplant) is its handle, never its name; a cast called by its name
    (Rida, Marie-Jeanne) by its name. Rida's paragraph says what he is made
    of (dragon fruit); a human's says it is a human."""
    ec = _ec()
    result = pt.shot_clip_prompt(ec, SHOT, SCRIPT, CORE, limit_words=None)
    master = _master_part(result)

    assert '"' not in master and "“" not in master and "”" not in master
    assert "Audio:" not in master and "says in" not in master
    assert "Laisse-moi charmer" not in master and "firewall" not in master
    handles = shots.character_handles(ec.entities["characters"])
    assert not shots.named_character(VICTOR) and shots.named_character(RIDA)
    assert "Victor" not in master and handles["char_victor"] in master
    assert "Rida" in master and "Marie-Jeanne" in master
    paragraphs = master.split("\n\n")
    rida = next(p for p in paragraphs if p.startswith("CHARACTER") and "Rida (in this shot)" in p)
    assert "dragon fruit" in rida
    marie = next(p for p in paragraphs if p.startswith("CHARACTER") and "Marie-Jeanne (in this shot)" in p)
    assert "a human" in marie
    sam = next(p for p in paragraphs if p.startswith("CHARACTER") and p.split(":", 1)[1].strip().startswith("Sam"))
    assert "a human" in sam and "(in this shot)" not in sam
    # One paragraph per section: no newline inside one.
    assert all("\n" not in p for p in paragraphs if p)
    # The speaker's voice direction is in the clip, its line's delivery in the scene, not the line.
    assert "Voice direction: Speak with energetic charm" in master
    assert "Charming, confident smirk" in master


def test_the_keyframe_and_image_master_leave_out_the_title_and_the_voice():
    """An image prompt carries no title (it could be drawn as lettering), no
    voice direction and no line delivery."""
    ec = _ec()
    keyframe = pt.shot_keyframe_prompt(ec, SHOT, SCRIPT, "A keyframe core.", limit_words=None)
    master = _master_part(keyframe, "A keyframe core.")
    assert keyframe["text"].startswith("SERIES:")
    assert "Dragon Fruit & Sales Queen" not in master
    assert "Voice direction" not in master and "voice_direction" not in master
    assert "over-acted telenovela delivery" not in master
    assert "Charming, confident smirk" not in master
    assert "Dialogue language" not in master
    sections = pt.master_sections(ec.story, ec.style_lock, ec.entities, language="fr", image=True)
    assert all("Dragon Fruit & Sales Queen" not in s.text for s in sections)

    clip = pt.master_prompt(ec.story, ec.style_lock, ec.entities, language="fr")
    assert clip["text"].startswith("SERIES: Dragon Fruit & Sales Queen")
    assert "Dialogue language: French" in clip["text"]
    assert clip["words"] == len(clip["text"].split()) >= 500
    assert [s["key"] for s in clip["sections"]][:2] == ["series", "series_lore"]


def test_the_entity_template_is_series_style_and_its_own_paragraph():
    """A sheet's template: SERIES + ART STYLE + that one entity's paragraph
    + the core; a creature's name never in it."""
    result = pt.entity_prompt(STORY, STYLE, "character", copy.deepcopy(VICTOR), "Sheet core.", limit_words=None,
                              entities=_entities())
    assert result["text"].startswith("SERIES:") and result["text"].endswith("Sheet core.")
    assert "ART STYLE:" in result["text"] and "CHARACTER:" in result["text"]
    assert "Victor" not in result["text"] and "eggplant" in result["text"]
    assert "Rida" not in result["text"].split("CHARACTER:", 1)[1]  # no other cast in its paragraph
    place = pt.entity_prompt(STORY, STYLE, "place", copy.deepcopy(BULLPEN), "Plate core.", limit_words=None,
                             variant="night")
    assert "ambient LED strips glowing cyan" in place["text"] and "bright natural daylight" not in place["text"]


# ================================================== (d) the link's limit

def test_link_words_is_unbounded_for_manual_and_local_links_and_chain_words_takes_the_smallest():
    """``link_words``: the link's ``prompt_limits.budget_words`` with no
    quality ceiling; None (no bound) for no link, the manual link and a
    local one. ``chain_words``: the smallest over a role chain."""
    no_live = {}
    assert prompt_budgets.link_words(None) is None
    assert prompt_budgets.link_words("manual/upload", live=no_live) is None
    assert prompt_budgets.link_words("local/comfyui", live=no_live) is None
    veo = prompt_budgets.link_words("gemini/veo-3.1-lite", live=no_live)
    seedance = prompt_budgets.link_words("fal/seedance-1-pro-fast", live=no_live)
    assert veo == 630 and seedance == 230  # 1024 tokens; 1500 characters -- no 160/200-word ceiling
    assert prompt_budgets.chain_words(["gemini/veo-3.1-lite", "fal/seedance-1-pro-fast", "manual/upload"],
                                      live=no_live) == 230
    assert prompt_budgets.chain_words(["manual/upload", "local/comfyui"], live=no_live) is None
    assert prompt_budgets.chain_words([], live=no_live) is None

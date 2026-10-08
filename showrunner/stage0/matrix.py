"""The stage-0 test matrix (plan 36, D1/D6): three voice paths x seeds, plus 2- and 3-speaker
clips, on the existing Fruit Drama cast of ``productions/faille_damour`` plus one non-fruit
character (a 3D cartoon human, D8) so the voice path is not chosen on fruit faces only.

Everything creative lives here as data so a run is reproducible and a prompt change is a diff:
the universes (medium sentence + negative block; the fruit one is the validated fix for the "fruit
mask on an actor" failure), each character's universe and master head prompt, the voice descriptions, the lines in French and English, the
keyframe each test starts from, and the prompt builders of the three paths.
"""

from __future__ import annotations

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRODUCTION = os.path.join(REPO_ROOT, "productions", "faille_damour")
KF = os.path.join(PRODUCTION, "ep01", "kf")
CHARS = os.path.join(PRODUCTION, "chars")

STAGE0_DIR = os.path.join(REPO_ROOT, "stories", "_stage0")

# One universe = the medium sentence that opens every prompt + its negative block. In a story these
# come from its 01-universe.md; here they are the two looks stage 0 compares.
UNIVERSES = {
    # Kept from clipping/aistory/prompt_templates.py, the human's 2026-10-05 fix.
    "fruit": {
        "medium": ("A stylised 3D cartoon animation in the manner of a Pixar feature, fully computer-generated. The "
                   "characters are fruit people: each one's whole head IS the fruit itself, stem, leaves and skin "
                   "intact, with large expressive cartoon eyes and a wide mouth drawn on the fruit's skin; cartoon "
                   "proportions (an oversized fruit head on a slim body) in real-looking human clothes, with human "
                   "hands. Smooth CGI surfaces; never a human face, never real human skin, never a mask or costume "
                   "on a person; no live-action footage, no real people, no photographs."),
        "negative": ("live action, real people, photograph, human skin, human face, mask, costume, blurry, deformed "
                     "mouth, extra limbs, extra characters, text, subtitles, captions, watermark, pc game, console "
                     "game, video game, childish, ugly"),
    },
    # The human's pick on 2026-10-08 for the non-fruit test character.
    "cartoon_human": {
        "medium": ("A stylised 3D cartoon animation in the manner of a Pixar feature, fully computer-generated. The "
                   "characters are cartoon humans: gently stylised proportions (a slightly larger head and eyes than "
                   "in life), smooth soft skin shading, expressive faces with a clearly modelled mouth, lips and "
                   "teeth; real-looking clothes, human hands. Smooth CGI surfaces; no live-action footage, no real "
                   "people, no photographs."),
        "negative": ("live action, real people, photograph, photorealistic skin, skin pores, uncanny valley, blurry, "
                     "deformed mouth, crooked teeth, extra limbs, extra characters, text, subtitles, captions, "
                     "watermark, pc game, console game, video game, childish, ugly"),
    },
}

CHARACTERS = {
    "paloma": {
        "universe": "fruit",
        "name": "Paloma",
        "head": ("Paloma, an anthropomorphic mango woman: her whole head is a single ripe mango, golden-yellow at "
                 "the bottom blushing to red-orange on top, with a short green stem and two glossy leaves; large "
                 "brown cartoon eyes with long lashes, pink blush cheeks, a wide open smile drawn on the mango skin, "
                 "gold hoop earrings; slim human body in a mustard-yellow knit cardigan over a knee-length turquoise "
                 "floral wrap dress, white sneakers, human hands"),
        "voice": {"fr": "une jeune femme pétillante et joueuse, voix claire et rapide, taquine, qui sourit en parlant",
                  "en": "a bubbly, playful young woman, bright and quick, teasing, smiling as she speaks"},
        "keyframe": os.path.join(KF, "kf10_team.png"),
        "ref": os.path.join(CHARS, "char_paloma.png"),
        "setting": "a bright modern open-plan office with plants and large windows, daylight",
    },
    "marie_jeanne": {
        "universe": "fruit",
        "name": "Marie-Jeanne",
        "head": ("Marie-Jeanne, an anthropomorphic strawberry woman: her whole head is a single glossy red "
                 "strawberry dotted with golden seeds, a crown of spiky green leaves on top; large almond brown "
                 "cartoon eyes with long lashes and arched dark brows, a confident knowing smile drawn on the "
                 "strawberry skin; slim human body in a cream tailored blazer over a burgundy silk blouse, slim "
                 "black trousers, black pointed heels, a gold wristwatch, human hands"),
        "voice": {"fr": "une jeune femme assurée et rapide, fière, énergie de telenovela, articulée",
                  "en": "a confident, fast-talking young woman, proud, telenovela energy, crisp diction"},
        "keyframe": os.path.join(KF, "kf08_realisation.png"),
        "ref": os.path.join(CHARS, "char_marie_jeanne.png"),
        "setting": "a dim conference room at night, warm practical lamps, a red glow from a screen",
    },
    "rida": {
        "universe": "fruit",
        "name": "Rida",
        "head": ("Rida, an anthropomorphic kiwi man: his whole head is a single fuzzy olive-brown kiwi fruit with a "
                 "short brown stem on top; large round brown cartoon eyes under thick dark brows, rosy cheeks, a "
                 "small rounded nose and a wide mouth with a sly half-smile drawn on the kiwi skin; slim human body "
                 "in a charcoal zip hoodie over a green t-shirt, black over-ear headphones around his neck, dark "
                 "grey jeans, white sneakers, human hands"),
        "voice": {"fr": "un jeune homme calme, sec et ironique, légèrement amusé, débit rapide et posé",
                  "en": "a calm young man, dry and ironic, slightly amused, quick but composed"},
        "keyframe": os.path.join(KF, "kf01_loft.png"),
        "ref": os.path.join(CHARS, "char_rida.png"),
        "setting": "a dark attic loft at night, three monitors glowing red, a city skyline through the window",
    },
    "camille": {
        "universe": "cartoon_human",
        "name": "Camille",
        "head": ("Camille, a French woman in her early thirties: an oval face with soft rounded features, large "
                 "expressive hazel eyes, thick dark arched brows, a small straight nose, full rose-tinted lips with "
                 "white teeth that show when she speaks; dark brown wavy hair in a loose low bun, a few strands "
                 "framing her face; slim body in a charcoal tailored blazer over a cream silk blouse, small gold "
                 "stud earrings, a thin wristwatch, human hands"),
        "voice": {"fr": "une femme de trente ans, posée mais blessée, voix grave et maîtrisée, colère contenue",
                  "en": "a woman in her thirties, composed but hurt, a low controlled voice, restrained anger"},
        # Not in the repo yet: `run_stage0 keyframe --character camille` makes candidates, `--pick` locks one.
        "keyframe": os.path.join(STAGE0_DIR, "kf_camille.png"),
        "ref": None,
        "setting": "a glass-walled meeting room in a modern office at dusk, city lights behind her, a warm desk lamp",
    },
}

# One line per character for the single-speaker tests (≈ 10 words: a 5 s clip at 2.4 words/s).
LINES = {
    "paloma": {"fr": "Tu souris à ton téléphone. C'est qui, le kiwi ?",
               "en": "You're smiling at your phone. Who's the kiwi?"},
    "marie_jeanne": {"fr": "R… comme Rida ? Non. Non, non, non.",
                     "en": "R… as in Rida? No. No, no, no."},
    "rida": {"fr": "Vaulta… t'as laissé la porte grande ouverte.",
             "en": "Vaulta… you left the front door wide open."},
    "camille": {"fr": "Tu as signé sans moi ? Alors on n'est plus associés.",
                "en": "You signed without me? Then we're not partners anymore."},
}

# The multi-speaker exchanges (10 s clips). Two speakers on kf02 (the café collision), three
# speakers on a keyframe stage 0 generates with the images endpoint (see run_stage0 keyframe3).
EXCHANGES = {
    "two": {
        "keyframe": os.path.join(KF, "kf02_collision.png"),
        "speakers": ["marie_jeanne", "rida"],
        "setting": "a sunny café counter in the morning, plants, a spilled coffee cup",
        "lines": {"fr": [("marie_jeanne", "Pardon ! Je signe le contrat de l'année dans une heure !"),
                         ("rida", "Et vous êtes en retard même sur le café ?")],
                  "en": [("marie_jeanne", "Sorry! I'm signing the deal of the year in an hour!"),
                         ("rida", "And you're late even for coffee?")]},
    },
    "three": {
        "keyframe": None,  # made by `run_stage0 keyframe3`
        "speakers": ["paloma", "marie_jeanne", "rida"],
        "setting": "a bright open-plan office, three characters standing close together by a desk",
        "lines": {"fr": [("paloma", "C'est qui, le kiwi ?"),
                         ("marie_jeanne", "Personne, Paloma !"),
                         ("rida", "Personne ? Sympa.")],
                  "en": [("paloma", "Who's the kiwi?"),
                         ("marie_jeanne", "Nobody, Paloma!"),
                         ("rida", "Nobody? Nice.")]},
    },
}

LANGUAGE_NAME = {"fr": "French", "en": "English"}
CHATTERBOX_LANGUAGE = {"fr": "French (fr)", "en": "English (en)"}

# Clip sizes: 9:16 on a 64 px grid (stage 1 at half size on the 32 px latent grid).
WIDTH, HEIGHT, FPS = 704, 1280, 24
SINGLE_SECONDS, EXCHANGE_SECONDS = 5, 10
SEEDS = [11, 22, 33]


# ------------------------------------------------------------------ universes

def medium(char_id: str) -> str:
    return UNIVERSES[CHARACTERS[char_id]["universe"]]["medium"]


def negative(char_id: str) -> str:
    return UNIVERSES[CHARACTERS[char_id]["universe"]]["negative"]


def _exchange_universe(key: str) -> dict:
    kinds = {CHARACTERS[s]["universe"] for s in EXCHANGES[key]["speakers"]}
    if len(kinds) != 1:
        raise ValueError(f"exchange '{key}' mixes universes {sorted(kinds)}: one medium sentence per clip")
    return UNIVERSES[kinds.pop()]


def exchange_medium(key: str) -> str:
    return _exchange_universe(key)["medium"]


def exchange_negative(key: str) -> str:
    return _exchange_universe(key)["negative"]


def prompt_keyframe(char_id: str) -> str:
    """The start image of a character that has none yet (text to image on the images endpoint)."""
    c = CHARACTERS[char_id]
    return (f"{medium(char_id)} {c['head']}. Setting: {c['setting']}. Medium close shot from the waist up, "
            f"{c['name']} faces the camera, mouth closed, a tense and composed expression, soft cinematic light, "
            f"vertical 9:16 framing, only this one character, nobody else.")


# ------------------------------------------------------------------ prompt builders

# Rida, 2026-10-08: in an episode a character rarely speaks to the lens. The single-speaker tests use
# the drama's real framing: talking to someone just off-screen, three-quarter view, eyeline past the lens.
def dialogue_framing(name: str) -> str:
    return (f"{name} talks to someone just off-screen beside the camera, in three-quarter view, the eyeline "
            f"passing just past the lens and never looking into it, as in a conversation scene of a drama")


def prompt_path_a(char_id: str, lang: str, line: str | None = None) -> str:
    """Path (a): LTX-2.5 I2V, the voice described and the line quoted in the prompt."""
    c = CHARACTERS[char_id]
    line = line or LINES[char_id][lang]
    return (f"Use the provided start image as the first frame. {medium(char_id)} {c['head']}. Setting: {c['setting']}. "
            f"{dialogue_framing(c['name'])}, and says in {LANGUAGE_NAME[lang]}, with the voice of "
            f"{c['voice']['en']}: \"{line}\" The mouth moves naturally with every word, a small head tilt, "
            f"a breath before and a beat of silence after the line. Medium close-up, the camera holds still on "
            f"the speaker, soft natural motion only. Audio: the clear voice close to the microphone, quiet "
            f"room tone, no music. No subtitles, no on-screen text, no black frames.")


def prompt_path_b(char_id: str, lang: str, setting: str | None = None) -> str:
    """Path (b): LTX-2.5 A2V, the audio is given; the prompt describes the performance only."""
    c = CHARACTERS[char_id]
    return (f"Use the provided start image as the first frame. {medium(char_id)} {c['head']}. Setting: "
            f"{setting or c['setting']}. {dialogue_framing(c['name'])}, speaking the line that is heard, in "
            f"{LANGUAGE_NAME[lang]}, "
            f"lips in sync with every syllable, natural blinks and small head movements, expressive brows, "
            f"a beat of stillness after the last word. Medium close-up, the camera holds still on the speaker. "
            f"No subtitles, no on-screen text, no black frames.")


def prompt_path_c(char_id: str, lang: str, line: str | None = None) -> str:
    """Path (c): LTX-2.3 ID-LoRA, the [VISUAL]/[SPEECH]/[SOUNDS] sections of the official template."""
    c = CHARACTERS[char_id]
    line = line or LINES[char_id][lang]
    return (f"[VISUAL]: {medium(char_id)} Medium close-up, the camera slowly pushes in toward the character. {c['head']}. "
            f"Setting: {c['setting']}. {dialogue_framing(c['name'])}; the mouth opens and closes "
            f"naturally while speaking, a small head tilt, expressive brows.\n"
            f"[SPEECH]: {line}\n"
            f"[SOUNDS]: The speaker talks in {LANGUAGE_NAME[lang]} with the voice of {c['voice']['en']}, "
            f"moderate volume, close to the microphone. Quiet room tone, no music.")


def prompt_exchange_a(key: str, lang: str) -> str:
    ex = EXCHANGES[key]
    heads = " ".join(CHARACTERS[s]["head"] + "." for s in ex["speakers"])
    turns = " ".join(f"{CHARACTERS[who]['name']} says in {LANGUAGE_NAME[lang]}, with the voice of "
                     f"{CHARACTERS[who]['voice']['en']}: \"{line}\"" for who, line in ex["lines"][lang])
    return (f"Use the provided start image as the first frame. {exchange_medium(key)} {heads} Setting: {ex['setting']}. "
            f"They speak in turn, each one's mouth moving only on their own line, the other listening and "
            f"reacting: {turns} Medium two-shot, the camera holds still. Audio: two distinct voices close to "
            f"the microphone, quiet room tone, no music. No subtitles, no on-screen text, no black frames.")


def prompt_exchange_b(key: str, lang: str) -> str:
    ex = EXCHANGES[key]
    heads = " ".join(CHARACTERS[s]["head"] + "." for s in ex["speakers"])
    order = ", then ".join(CHARACTERS[who]["name"] for who, _ in ex["lines"][lang])
    return (f"Use the provided start image as the first frame. {exchange_medium(key)} {heads} Setting: {ex['setting']}. "
            f"The characters speak the lines that are heard, in {LANGUAGE_NAME[lang]}, in this order: {order}; "
            f"only the speaking character's mouth moves on each line, the others listen and react with "
            f"their eyes and brows. Medium two-shot, the camera holds still. No subtitles, no on-screen text, "
            f"no black frames.")

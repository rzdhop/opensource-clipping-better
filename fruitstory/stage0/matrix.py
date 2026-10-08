"""The stage-0 test matrix (plan 36, D1/D6): three voice paths x seeds, plus 2- and 3-speaker
clips, on the existing Fruit Drama cast of ``productions/faille_damour``.

Everything creative lives here as data so a run is reproducible and a prompt change is a diff:
the medium sentence (the validated fix for the "fruit mask on an actor" failure), the master
head prompt of each character, the voice descriptions, the lines in French and English, the
keyframe each test starts from, and the prompt builders of the three paths.
"""

from __future__ import annotations

import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRODUCTION = os.path.join(REPO_ROOT, "productions", "faille_damour")
KF = os.path.join(PRODUCTION, "ep01", "kf")
CHARS = os.path.join(PRODUCTION, "chars")

# The medium sentence (kept from clipping/aistory/prompt_templates.py, the human's 2026-10-05 fix).
MEDIUM = ("A stylised 3D cartoon animation in the manner of a Pixar feature, fully computer-generated. The "
          "characters are fruit people: each one's whole head IS the fruit itself, stem, leaves and skin "
          "intact, with large expressive cartoon eyes and a wide mouth drawn on the fruit's skin; cartoon "
          "proportions (an oversized fruit head on a slim body) in real-looking human clothes, with human "
          "hands. Smooth CGI surfaces; never a human face, never real human skin, never a mask or costume "
          "on a person; no live-action footage, no real people, no photographs.")

NEGATIVE = ("live action, real people, photograph, human skin, human face, mask, costume, blurry, deformed "
            "mouth, extra limbs, extra characters, text, subtitles, captions, watermark, pc game, console "
            "game, video game, childish, ugly")

CHARACTERS = {
    "paloma": {
        "name": "Paloma",
        "head": ("Paloma, an anthropomorphic mango woman: her whole head is a single ripe mango, golden-yellow "
                 "blushing to red-orange on top, with a short green stem and two leaves; large dark cartoon "
                 "eyes with long lashes, pink blush cheeks, a wide friendly mouth drawn on the mango skin; "
                 "slim human body in a mustard-yellow knit cardigan over a turquoise floral wrap dress, gold "
                 "hoop earrings, human hands"),
        "voice": {"fr": "une jeune femme pétillante et joueuse, voix claire et rapide, taquine, qui sourit en parlant",
                  "en": "a bubbly, playful young woman, bright and quick, teasing, smiling as she speaks"},
        "keyframe": os.path.join(KF, "kf10_team.png"),
        "ref": os.path.join(CHARS, "char_paloma.png"),
        "setting": "a bright modern open-plan office with plants and large windows, daylight",
    },
    "marie_jeanne": {
        "name": "Marie-Jeanne",
        "head": ("Marie-Jeanne, an anthropomorphic strawberry woman: her whole head is a single glossy red "
                 "strawberry with yellow seeds and a green leafy calyx on top; large almond cartoon eyes with "
                 "long lashes and arched brows, a confident mouth drawn on the strawberry skin; slim human body "
                 "in a white tailored blazer over a burgundy silk blouse, a gold bracelet, human hands"),
        "voice": {"fr": "une jeune femme assurée et rapide, fière, énergie de telenovela, articulée",
                  "en": "a confident, fast-talking young woman, proud, telenovela energy, crisp diction"},
        "keyframe": os.path.join(KF, "kf08_realisation.png"),
        "ref": os.path.join(CHARS, "char_marie_jeanne.png"),
        "setting": "a dim conference room at night, warm practical lamps, a red glow from a screen",
    },
    "rida": {
        "name": "Rida",
        "head": ("Rida, an anthropomorphic kiwi man: his whole head is a single fuzzy brown kiwi fruit with a "
                 "short stem; large round cartoon eyes with thick dark brows, a wide mouth drawn on the kiwi "
                 "skin; slim human body in a black zip hoodie over a green t-shirt, headphones around his "
                 "neck, dark jeans, human hands"),
        "voice": {"fr": "un jeune homme calme, sec et ironique, légèrement amusé, débit rapide et posé",
                  "en": "a calm young man, dry and ironic, slightly amused, quick but composed"},
        "keyframe": os.path.join(KF, "kf01_loft.png"),
        "ref": os.path.join(CHARS, "char_rida.png"),
        "setting": "a dark attic loft at night, three monitors glowing red, a city skyline through the window",
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


# ------------------------------------------------------------------ prompt builders

def prompt_path_a(char_id: str, lang: str, line: str | None = None) -> str:
    """Path (a): LTX-2.5 I2V, the voice described and the line quoted in the prompt."""
    c = CHARACTERS[char_id]
    line = line or LINES[char_id][lang]
    return (f"Use the provided start image as the first frame. {MEDIUM} {c['head']}. Setting: {c['setting']}. "
            f"{c['name']} looks toward the camera and says in {LANGUAGE_NAME[lang]}, with the voice of "
            f"{c['voice']['en']}: \"{line}\" The mouth moves naturally with every word, a small head tilt, "
            f"a breath before and a beat of silence after the line. Medium shot, the camera holds still on "
            f"the speaker, soft natural motion only. Audio: the clear voice close to the microphone, quiet "
            f"room tone, no music. No subtitles, no on-screen text, no black frames.")


def prompt_path_b(char_id: str, lang: str, setting: str | None = None) -> str:
    """Path (b): LTX-2.5 A2V, the audio is given; the prompt describes the performance only."""
    c = CHARACTERS[char_id]
    return (f"Use the provided start image as the first frame. {MEDIUM} {c['head']}. Setting: "
            f"{setting or c['setting']}. {c['name']} speaks the line that is heard, in {LANGUAGE_NAME[lang]}, "
            f"lips in sync with every syllable, natural blinks and small head movements, expressive brows, "
            f"a beat of stillness after the last word. Medium shot, the camera holds still on the speaker. "
            f"No subtitles, no on-screen text, no black frames.")


def prompt_path_c(char_id: str, lang: str, line: str | None = None) -> str:
    """Path (c): LTX-2.3 ID-LoRA, the [VISUAL]/[SPEECH]/[SOUNDS] sections of the official template."""
    c = CHARACTERS[char_id]
    line = line or LINES[char_id][lang]
    return (f"[VISUAL]: {MEDIUM} Medium shot, the camera slowly pushes in toward the character. {c['head']}. "
            f"Setting: {c['setting']}. {c['name']} looks toward the camera, the mouth opens and closes "
            f"naturally while speaking, a small head tilt, expressive brows.\n"
            f"[SPEECH]: {line}\n"
            f"[SOUNDS]: The speaker talks in {LANGUAGE_NAME[lang]} with the voice of {c['voice']['en']}, "
            f"moderate volume, close to the microphone. Quiet room tone, no music.")


def prompt_exchange_a(key: str, lang: str) -> str:
    ex = EXCHANGES[key]
    heads = " ".join(CHARACTERS[s]["head"] + "." for s in ex["speakers"])
    turns = " ".join(f"{CHARACTERS[who]['name']} says in {LANGUAGE_NAME[lang]}, with the voice of "
                     f"{CHARACTERS[who]['voice']['en']}: \"{line}\"" for who, line in ex["lines"][lang])
    return (f"Use the provided start image as the first frame. {MEDIUM} {heads} Setting: {ex['setting']}. "
            f"They speak in turn, each one's mouth moving only on their own line, the other listening and "
            f"reacting: {turns} Medium two-shot, the camera holds still. Audio: two distinct voices close to "
            f"the microphone, quiet room tone, no music. No subtitles, no on-screen text, no black frames.")


def prompt_exchange_b(key: str, lang: str) -> str:
    ex = EXCHANGES[key]
    heads = " ".join(CHARACTERS[s]["head"] + "." for s in ex["speakers"])
    order = ", then ".join(CHARACTERS[who]["name"] for who, _ in ex["lines"][lang])
    return (f"Use the provided start image as the first frame. {MEDIUM} {heads} Setting: {ex['setting']}. "
            f"The characters speak the lines that are heard, in {LANGUAGE_NAME[lang]}, in this order: {order}; "
            f"only the speaking character's mouth moves on each line, the others listen and react with "
            f"their eyes and brows. Medium two-shot, the camera holds still. No subtitles, no on-screen text, "
            f"no black frames.")

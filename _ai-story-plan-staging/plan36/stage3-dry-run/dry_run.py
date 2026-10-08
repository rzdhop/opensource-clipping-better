"""Plan 36 stage 3 gate: a dry run of steps 1-4 (concepts, universe, cast, script) on a new pitch, through the
showrunner server's own tools, in process, with NO GPU endpoint configured (every paid tool is refused).

    python dry_run.py <repo root> <report.md>

Claude is the writer; this script only replays what the skills say, tool by tool, and records what came back.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

REPO = os.path.abspath(sys.argv[1])
REPORT = os.path.abspath(sys.argv[2])
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fastmcp  # noqa: E402

from cast import CAST  # noqa: E402
from showrunner import mcp_server as S, prompts as P, story_prompts as SP  # noqa: E402

SLUG = "les-heritiers-du-fournil"
LOG: list = []


def call(server, tool, **args):
    async def go():
        async with fastmcp.Client(server) as client:
            return await client.call_tool(tool, args, raise_on_error=False)
    res = asyncio.run(go())
    text = res.content[0].text if res.content and hasattr(res.content[0], "text") else ""
    LOG.append({"tool": tool, "args": {k: v for k, v in args.items() if k != "text"}, "error": res.is_error,
                "answer": text[:4000]})
    return res, (json.loads(text) if text and not res.is_error and text.lstrip().startswith(("{", "[")) else text)


# ------------------------------------------------------------------ step 1: concepts

CONCEPTS = """### 1. Le Testament du Fournil — le père meurt, la boulangerie va à la vendeuse
- Logline: à la mort de Gaspard, boulanger de quartier à Paris, ses deux enfants attendent l'héritage du Fournil ;
  le notaire le lègue à Suzon, la vendeuse de toujours.
- The secret: Suzon sait pourquoi — Mireille n'est pas la fille de Gaspard. Le public le devine avant Mireille.
- Cast: Mireille (renarde, l'aînée) — veut enfin diriger le Fournil. Théo (lièvre, le cadet revenu de Londres) —
  veut l'argent, vite. Suzon (hérissonne, la vendeuse) — veut que la boutique vive sans blesser personne.
  Maître Corbeau (corbeau, le notaire) — veut lire le testament tel qu'il est écrit, et regarder.
- The last 5 s of episode 1: dans l'étude du notaire, Mireille se tourne vers Suzon : « Suzon ? Notre vendeuse ? »
  Coupe sur le visage de Suzon, avant sa réponse. Question : pourquoi elle ?
- Trope: the inheritance (+ who's the father, slowly).
- Comment bait: « Team Mireille ou Team Suzon ? »
- Why they come back: Suzon « sait pourquoi » — chaque épisode lâche une miette du secret.
- Season sketch: E1–3 le testament, la guerre de la boutique, Théo vend en douce ; E4 le renversement (Mireille
  trouve une lettre de sa mère) ; E7 tout est perdu (la boutique fermée par les créanciers de Théo) ; E10 le
  second testament de Corbeau.

### 2. Pain Perdu — le fils vend la recette en secret
- Logline: Théo, revenu de Londres criblé de dettes, vend la recette du pain de son père à une chaîne industrielle
  pendant que Mireille se bat pour garder la boutique ouverte.
- The secret: le public voit Théo signer ; Mireille ne le sait pas.
- Cast: Théo — veut effacer ses dettes. Mireille — veut sauver le Fournil. Suzon — voit tout. Monsieur Brioche
  (un porc, patron de la chaîne) — veut le Fournil fermé.
- The last 5 s of episode 1: Mireille plonge la main dans le sac de farine et en sort le contrat signé. « Théo… »
- Trope: the double life / the betrayal by family.
- Comment bait: « Théo, traître ou victime ? »
- Why they come back: la date de livraison de la recette approche.
- Season sketch: E1–3 la vente cachée ; E4 Mireille découvre tout ; E7 la chaîne ouvre en face ; E10 Théo choisit.

### 3. Le Mitron — l'apprenti qui dit être le fils
- Logline: le lendemain de l'enterrement, un jeune taupe apprenti frappe au Fournil avec une lettre de Gaspard :
  « Voici ton frère. »
- The secret: la lettre est vraie, mais le garçon ment sur sa mère.
- Cast: Lucien (taupe, l'apprenti) — veut une famille. Mireille — veut la vérité. Théo — voit un rival. Suzon —
  reconnaît l'écriture.
- The last 5 s of episode 1: Lucien sort la photo : Gaspard tenant un bébé taupe. « Il m'a appelé Gaspard aussi. »
- Trope: the new arrival / who's the father.
- Comment bait: « Vrai fils ou imposteur ? »
- Why they come back: le test de paternité, épisode après épisode.
- Season sketch: E1–3 l'arrivée ; E4 la mère de Lucien ; E7 Lucien chassé ; E10 la vérité sur les deux enfants."""

BRIEF = f"""# Brief

## Pitch
DRY RUN of plan 36 stage 3 (no GPU, no Rida pick yet): a vertical telenovela in claymation, a family bakery in a
Paris neighbourhood run by animal people, French. Pitch written by Claude for the dry run (Rida: "for the rest of
questions decide for me", 2026-10-09).

## Concepts
{CONCEPTS}

## Chosen concept
Concept 1, Le Testament du Fournil — picked by Claude for the dry run; Rida confirms, mixes or picks another.

## Language
fr
"""

SEASON = """# Season 1

## Arc
Gaspard's will gives the family bakery to Suzon, the shop assistant, and not to his children; Suzon knows why,
and the reason is Mireille's birth. The season pays that secret one crumb at a time.

## Episodes
- ep01 — Le testament: the will is read; the Fournil goes to Suzon. Cliffhanger (revelation): « Suzon ? Notre vendeuse ? »
- ep02 — La guerre de la boutique: Mireille locks Suzon out at dawn; Théo offers to buy Suzon's share. (deadline)
- ep03 — Le coffre: Théo opens his father's safe and finds a woman's photo. (intrusion)
- ep04 — La lettre: Mireille finds a letter from her mother addressed to Suzon. (reversal)
- ep05 — Les créanciers: a man from London waits outside for Théo. (intrusion)
- ep06 — La recette: Suzon bakes Gaspard's bread; the street queues for her, not for Mireille. (reversal)
- ep07 — Fermé: the creditors seize the shop; all is lost. (deadline)
- ep08 — La vérité: Suzon tells Mireille who her father is. (revelation)
- ep09 — Le choix: Mireille must choose between the shop and her brother. (deadline)
- ep10 — Le second testament: Maître Corbeau opens the sealed envelope. (revelation)
"""

UNIVERSE = """# Universe — Claymation animal people of Paris

## Medium
A stop-motion claymation film, every frame handmade: the characters are plasticine animal people sculpted by hand,
with visible thumbprints and soft tool marks on the clay, glossy bead eyes and wide expressive mouths sculpted in the
clay; rounded cartoon proportions (a big head on a short body) in felt and cotton clothes with real stitching, with
four-fingered clay hands. Miniature handmade sets of wood, card and fabric, warm practical lights, a shallow depth of
field like a tabletop model.

## Negative
live action, real people, photograph, human skin, smooth plastic CGI, glossy 3D render, blurry, deformed hands,
extra limbs, extra characters, text, subtitles, captions, watermark

## Heads allowed
Animal heads only (fox, hare, hedgehog, crow, badger, mole…), sculpted in plasticine, each species readable at a
glance; no human heads.

## Proportions
Head about one third of the height; short bodies; four-fingered clay hands; adults stand at the same scale whatever
the species (a hedgehog is as tall as a fox).

## Palette
- #E8743B fox russet (Mireille) · #9B5DE5 burgundy-violet (Théo) · #F7A1C4 smock pink (Suzon) · #4D96FF ink blue (Corbeau)
- #F4E3C1 flour cream (the shop) · #B5651D crust brown · #2E2A26 bakehouse dark · #FFB347 oven glow

## Lighting
Day: warm lamplight and soft window light, a little flour dust in the air. Night: the bakehouse oven glowing orange,
one bare bulb. The notary's study: a green desk lamp and late afternoon light.

## Camera
Vertical 9:16; medium close-ups and two-shots; the camera holds still on the speaker; the characters talk to each
other, rarely into the lens.

## Forbidden
Human characters; readable signs, labels or screens; crowds; brand names; anything that looks like smooth 3D CGI.
"""

PLACES = {
    "boutique": ("Le Fournil — the shop", "a tiny handmade Paris bakery shop at dawn: a wooden counter with baskets of "
                 "golden baguettes, glass jars of sweets, wooden shelves of round loaves, warm lamplight and a misted window"),
    "fournil": ("Le Fournil — the bakehouse", "a cramped bakehouse behind the shop at night: a big brick oven glowing "
                "orange, flour sacks against the wall, a long wooden kneading table dusted with flour, copper pans hanging"),
    "notaire": ("The notary's study", "an old notary's study in the late afternoon: dark wood panelling, a green-shaded "
                "lamp on a leather-topped desk, stacks of tied paper folders, heavy velvet curtains"),
}

# The episode-1 script: (shot id, seconds, place, characters in frame, lines or None, reaction)
SHOTS = [
    ("s01", 5, "fournil", ["mireille"], [("mireille", "Tu n'as pas pleuré à l'enterrement, Théo. Pas une larme.")], None),
    ("s02", 5, "fournil", ["theo"], [("theo", "Je pleure en privé, Mireille. Comme papa le faisait.")], None),
    ("s03", 10, "boutique", ["suzon", "mireille"], [("suzon", "Le notaire a appelé. Il vous attend tous à dix-sept heures."),
                                                    ("mireille", "Tous ? Pourquoi « tous » ?")], None),
    ("s04", 5, "boutique", ["suzon"], None, "her smile freezes, then she looks down at her flour-dusted hands"),
    ("s05", 10, "notaire", ["corbeau"], [("corbeau", "Votre père a laissé une lettre pour chacun de vous.")], None),
    ("s06", 10, "notaire", ["corbeau"], [("corbeau", "« À ma fille Mireille, je laisse mes recettes et ma confiance. »")], None),
    ("s07", 5, "notaire", ["theo"], None, "the lazy grin slowly drops and the long ears flatten"),
    ("s08", 10, "notaire", ["corbeau", "theo"], [("corbeau", "« À mon fils Théo, je laisse le remboursement de ses dettes. »"),
                                                 ("theo", "C'est une blague ?")], None),
    ("s09", 10, "notaire", ["mireille", "theo"], [("mireille", "Et le Fournil, Maître ? La boutique, les murs ?"),
                                                  ("theo", "Dites-le. C'est elle, hein ? Évidemment, c'est elle.")], None),
    ("s10", 5, "notaire", ["corbeau"], [("corbeau", "« Le Fournil revient à Suzon. Elle sait pourquoi. »")], None),
    ("s11", 5, "notaire", ["mireille", "suzon"], [("mireille", "Suzon ? Notre vendeuse ?")], None),
]

CASTING = {  # ep00: one casting shot per character, a line in their own way of speaking (≤ 10 words)
    "mireille": "Ici, c'est moi qui décide. Même le pain m'obéit.",
    "theo": "Je suis revenu pour papa. Enfin… surtout pour son coffre.",
    "suzon": "Trente ans que je garde ses secrets. Et les vôtres.",
    "corbeau": "Le testament sera lu ce soir. Pas avant.",
}


def script_md() -> str:
    names = {k: c["name"] for k, c in CAST.items()}
    out = ["# Le Testament du Fournil — épisode 1 (dry run)", ""]
    for sid, _, place, frame, lines, reaction in SHOTS:
        out.append(f"## {sid} — {place}")
        if lines:
            out += [f"- **{names[who]}**: {text}" for who, text in lines]
        else:
            out.append(f"- *(silent — {names[frame[0]]}: {reaction})*")
        out.append("")
    return "\n".join(out)


def shots_json(ep_shots) -> dict:
    rows = []
    for sid, seconds, place, frame, lines, reaction in ep_shots:
        row = {"id": sid, "seconds": seconds, "place": place, "characters": frame}
        if lines:
            row["lines"] = [{"speaker": who, "text": text} for who, text in lines]
        else:
            row["reaction"] = reaction
        rows.append(row)
    return {"bgm": None, "hook": None, "card": None, "shots": rows}


def main() -> None:
    import tempfile
    state = tempfile.mkdtemp(prefix="dry-state-")
    settings = S.Settings(stories_dir=os.path.join(REPO, "stories"), state_dir=state, endpoints={}, keys={})
    server = S.build_server(S.Backend(settings))
    report = ["# Plan 36 stage 3 — dry run of steps 1–4 (no GPU, $0)", "",
              f"Story `stories/{SLUG}/`, French, claymation animal people (a non-fruit universe, D8). Every step went "
              "through the showrunner server's own tools, in process, with no GPU endpoint configured.", ""]

    # step 1
    _, listed = call(server, "story_list")
    assert SLUG not in [r["slug"] for r in listed["stories"]], "the dry-run story exists already"
    _, made = call(server, "story_create", title="Les Héritiers du Fournil", language="fr",
                   universe_name="Claymation animal people of Paris")
    assert made["slug"] == SLUG, made
    call(server, "store_write", story=SLUG, path="00-brief.md", text=BRIEF)
    call(server, "store_write", story=SLUG, path="04-season.md", text=SEASON)
    report += ["## Step 1 — concepts (`story-concepts`)", "",
               "Three concepts written to `00-brief.md` (inheritance / double life / the new arrival), each with the last "
               "5 s of episode 1 first; concept 1 picked for the dry run; the 10-episode season map in `04-season.md` "
               "with a rotating cliffhanger shape.", ""]

    # step 2
    call(server, "store_write", story=SLUG, path="01-universe.md", text=UNIVERSE)
    medium = SP.medium(S.st.Story.open(os.path.join(REPO, "stories", SLUG)))
    report += ["## Step 2 — universe (`story-universe`)", "", f"`## Medium` ({P.words(medium)} words), the style lock of "
               "every prompt:", "", f"> {medium}", ""]

    # step 3: sheets (text), then the prompts the cast step would send — no picture made
    for cid, c in CAST.items():
        sheet = P.render("character_sheet", oneline=False, name=c["name"], head=c["head"], voice_en=c["voice_en"],
                         voice_fr=c["voice_fr"], colour=c["colour"])
        sheet = (sheet.replace("## Wants\n", f"## Wants\n{c['wants']}\n").replace("## Fears\n", f"## Fears\n{c['fears']}\n")
                 .replace("## Secret\n", f"## Secret\n{c['secret']}\n")
                 .replace("## How they speak\n", f"## How they speak\n{c['speaks']}\n")
                 .replace("## Signature item\n", f"## Signature item\n{c['item']}\n"))
        call(server, "store_write", story=SLUG, path=f"02-cast/{cid}/sheet.md", text=sheet)
    for place, (title, setting) in PLACES.items():
        call(server, "store_write", story=SLUG, path=f"03-places/{place}/plate.md",
             text=f"# {title}\n\n## Setting\n{setting}\n")
    call(server, "store_write", story=SLUG, path="ep00/shots.json", text=json.dumps(
        {"shots": [{"id": f"s{k + 1:02d}", "seconds": 5, "place": "boutique", "characters": [cid],
                    "lines": [{"speaker": cid, "text": line}]} for k, (cid, line) in enumerate(CASTING.items())]},
        ensure_ascii=False, indent=2))
    report += ["## Step 3 — cast (`story-cast`): the sheets, and the prompts it would send", "",
               "| Character | Head words | full_body prompt words | template | size |", "|---|---|---|---|---|"]
    cast_prompts = {}
    for cid, c in CAST.items():
        _, fb = call(server, "prompt_cast", story=SLUG, character=cid, kind="full_body")
        cast_prompts[cid] = fb["prompt"]
        report.append(f"| {c['name']} | {P.words(c['head'])} | {P.words(fb['prompt'])} | {fb['template']} | "
                      f"{fb['width']}x{fb['height']} |")
    res, refused = call(server, "prompt_cast", story=SLUG, character="mireille", kind="turnaround")
    report += ["", f"- Turnaround before a full body is locked: refused as it should be — *{refused}*"]
    casting = []
    for k, cid in enumerate(CASTING):
        sid = f"s{k + 1:02d}"
        _, kf = call(server, "prompt_keyframe", story=SLUG, episode=0, shot=sid)
        _, clip = call(server, "prompt_clip", story=SLUG, episode=0, shot=sid)
        casting.append((cid, sid, kf, clip))
        assert P.framing_intruders(P.KEYFRAME_FRAMINGS[kf["framing"]]) == [] and P.unwanted_words(clip["prompt"]) == []
    report += ["- The casting reel `ep00/shots.json`: one 5 s shot per character, a line in their voice; every keyframe "
               "and clip prompt built (t2i until the full bodies are locked), budgets:", ""]
    report += [f"  - {CAST[c]['name']} ({sid}): « {CASTING[c]} » — {clip['budget']['words']}/{clip['budget']['max_words']} "
               f"words, fits: {clip['budget']['fits']}" for c, sid, kf, clip in casting]
    res, paid = call(server, "comfy_submit", story=SLUG, template="t2i_flux2_klein", values={"seed": 1},
                     prompt_from="cast:mireille:full_body", dest="02-cast/mireille/candidates/full_body_c1")
    assert res.is_error
    report += ["", f"- A paid call in the dry run is refused (no endpoint): *{paid}*", "",
               "What the cast step would cost, after Rida's go per batch (4 characters): 4 × (4 full-body candidates + "
               "2 turnaround + 2 expression grids + 2 casting keyframes) = 40 images ≈ $0.40 warm / $1.20 cold; 4 × 3 "
               "casting clips = 12 clips ≈ $0.40–0.60 warm / ≈ $1.60 cold. Total ≈ $0.80–2.80.", ""]

    # step 4: the script, the places, the budgets
    call(server, "store_write", story=SLUG, path="ep01/script.md", text=script_md())
    # The shot list is step 5 — written here as a scratch copy only to run the budget check through prompt_clip.
    call(server, "store_write", story=SLUG, path="ep01/shots.json",
         text=json.dumps(shots_json(SHOTS), ensure_ascii=False, indent=2))
    report += ["## Step 4 — script (`story-script`)", "",
               "`ep01/script.md`: 11 shots, 12 lines, 80 s of clips (+ the end card), cliffhanger first "
               "(« Suzon ? Notre vendeuse ? », cut on Suzon before her answer), two silent reactions, four two-character "
               "shots (three with both speaking, D7). The word budget of every shot, from `prompt_clip`:", "",
               "| Shot | s | In frame | Words / max | Fits |", "|---|---|---|---|---|"]
    for sid, seconds, _, frame, _, _ in SHOTS:
        _, clip = call(server, "prompt_clip", story=SLUG, episode=1, shot=sid)
        b = clip["budget"]
        report.append(f"| {sid} | {seconds} | {', '.join(CAST[c]['name'] for c in frame)} | {b['words']} / "
                      f"{b['max_words']} | {'yes' if b['fits'] else 'NO'} |")
        assert P.unwanted_words(clip["prompt"]) == []
    lines = sum(len(s[4] or []) for s in SHOTS)
    words = [P.words(t) for s in SHOTS for _, t in (s[4] or [])]
    report += ["", f"Lines: {lines} (≤ 12); words per line {min(words)}–{max(words)}.", ""]

    # the prompts themselves, for Rida to read
    report += ["## The prompts the GPU would receive (built by the server, not typed)", "",
               "### Cast: Mireille, full body", "", "```", cast_prompts["mireille"], "```", "",
               "### ep00 s01: Mireille's casting keyframe", "", "```", casting[0][2]["prompt"], "```", "",
               "### ep01 s03: a two-speaker clip (Suzon, Mireille)", "", "```"]
    _, s03 = call(server, "prompt_clip", story=SLUG, episode=1, shot="s03")
    report += [s03["prompt"], "```", "", "### ep01 s04: a silent reaction (Suzon)", "", "```"]
    _, s04 = call(server, "prompt_clip", story=SLUG, episode=1, shot="s04")
    report += [s04["prompt"], "```", ""]

    report += ["## What the dry run found (for Rida)", "",
               "1. **The silent-reaction template still says \"listens to someone just off-screen\"** "
               "(`prompts/clip_reaction.md`, s04 and s07 above). That is the wording that drew a stray human in a keyframe "
               "(A-222). In a clip the start image holds the frame, and the one-speaker golden (s33) says the same and "
               "passed batch a, so it is left as it is; watch the first reaction clips, and if one invents a person, "
               "the fix is the reaction template, not a re-roll.",
               "2. **\"animal people\" in the Medium.** The universe's own noun (like the demo's \"fruit people\") "
               "contains *people*; batch a held with \"fruit people\". Watch the first full bodies for a human.",
               "3. **A one-line shot with a silent listener (s11, the cliffhanger) uses the exchange golden**, whose audio "
               "sentence says \"two distinct voices\" (kept word for word, `clip_exchange.md`). The clip check will show "
               "whether the listener starts talking; if so, the cliffhanger becomes a one-character shot of Mireille "
               "followed by a silent reaction of Suzon.",
               "4. Nothing was locked: every lock needs Rida's words. To go on with this story: Rida picks the concept "
               "(or another), approves the universe, then the cast step's first paid batch (4 full-body candidates per "
               "character, ≈ $0.16–0.48).", ""]
    _, ledger = call(server, "cost_ledger", story=SLUG)
    report += ["## Money", "", f"Spent by the dry run: ${ledger['total_usd']:.2f} ({len(LOG)} tool calls, "
               f"{sum(1 for r in LOG if r['error'])} refusals, all expected).", ""]
    report += ["## Tool calls", "", "| # | Tool | Error |", "|---|---|---|"]
    report += [f"| {k + 1} | {r['tool']} | {'refused' if r['error'] else ''} |" for k, r in enumerate(LOG)]
    with open(REPORT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(report) + "\n")
    print(f"{len(LOG)} calls, {sum(1 for r in LOG if r['error'])} refused; report: {REPORT}")


if __name__ == "__main__":
    main()

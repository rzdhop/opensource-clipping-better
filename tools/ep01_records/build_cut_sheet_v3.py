"""The cut sheet of ep01 v3: a wide beat per scene for rhythm, then one talking close-up per line, trimmed to the line."""
import json, os, wave
os.chdir("/home/ubuntu/Documents/tools/opensource-clipping-better/outputs")
TAIL, LEAD = 0.35, 0.12
def wav_s(p):
    with wave.open(p) as w: return w.getnframes() / w.getframerate()
TEXTS = {l.split("_",1)[0]: t for l, t in {
 "l01_rida":"Vaulta… t'as laissé la porte grande ouverte.","l02_marie_jeanne":"Pardon ! Je signe le contrat de l'année dans une heure !",
 "l03_rida":"Et vous êtes en retard même sur le café ?","l04_marie_jeanne":"Marie-Jeanne. Retenez le nom, il sera dans les journaux.",
 "l05_rida":"Rida. Le mien aussi, peut-être.","l06_paloma":"Tu souris à ton téléphone. C'est qui, le kiwi ?","l07_marie_jeanne":"Personne ! La démo, Paloma.",
 "l08_leonardo":"Ce contrat, c'est ma promotion. Zéro surprise.","l09_marie_jeanne":"Non, non, non… pas maintenant !","l10_leonardo":"Trouve ce hacker. Et fais-le taire.",
 "l11_marie_jeanne":"R… comme Rida ?","l12_don_maximiliano":"Enfin… quelqu'un a trouvé ma porte.","l13_paloma":"Alors… Team Rida ou Team Marie-Jeanne ?"}.items()}
WHO = {"l01":"rida","l02":"marie_jeanne","l03":"rida","l04":"marie_jeanne","l05":"rida","l06":"paloma","l07":"marie_jeanne","l08":"leonardo","l09":"marie_jeanne","l10":"leonardo","l11":"marie_jeanne","l12":"don_maximiliano","l13":"paloma"}
# (wide clip, seconds of the wide beat, the lines that follow it)
SCENES = [("clip01", 2.2, ["l01"]), ("clip02", 2.6, ["l02", "l03"]), ("clip03", 1.6, ["l04", "l05"]), ("clip04", 1.6, ["l06", "l07"]),
          ("clip05", 1.5, ["l08"]), ("clip06", 2.6, ["l09"]), ("clip07", 1.8, ["l10"]), ("clip08", 1.2, ["l11"]), ("clip09", 2.0, ["l12"]), ("clip10", 1.4, ["l13"])]
segments = []
for wide, beat, lines in SCENES:
    segments.append({"clip": f"faille_damour/ep01/clips_v2/{wide}.mp4", "seconds": beat})
    for lid in lines:
        name = f"{lid}_{WHO[lid]}"
        wav = f"faille_damour/ep01/voices_v2/{name}.wav"
        seconds = round(min(4.85, LEAD + wav_s(wav) + TAIL), 3)
        segments.append({"clip": f"faille_damour/ep01/talk/{name}.mp4", "seconds": seconds,
                         "lines": [{"id": lid, "wav": wav, "text": TEXTS[lid], "at": LEAD}]})
sheet = {"fps": 16, "hook": {"text": "Il a hacké sa boîte… et son cœur ?", "seconds": 2.2}, "segments": segments,
         "card": {"lines": ["Team Rida", "ou Team Marie-Jeanne ?", "Partie 2 demain"], "seconds": 1.5}}
json.dump(sheet, open("faille_damour/ep01/cut_sheet_v3.json", "w"), ensure_ascii=False, indent=1)
print(len(segments), "segments,", round(sum(s["seconds"] for s in segments), 1), "s + card")

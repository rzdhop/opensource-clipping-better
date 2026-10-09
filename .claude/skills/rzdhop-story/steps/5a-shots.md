# Story, step 5a: the shot list and the keyframes (GPU: images, no questions)

The locked script becomes `epNN/shots.json`, one entry per clip, and each shot gets its start picture: the clip
model keeps the keyframe's pose and framing, so the keyframe is drawn the way the shot must look. You do this
without asking Rida anything; he sees the result in the finished episode.

**Before writing any prompt, read `PROMPTS.md`** (in this skill's folder): the patterns that worked and the checks
before sending.

## When to use

- `epNN/script.md` is locked (the episode's cost was said in one line when Rida approved it).
- A shot to redo after Rida's episode review.

## Read first

- `store_read epNN/script.md`, the sheets of the characters in it, the plates of its places, `01-universe.md`.

## Run (no questions)

1. Write `epNN/shots.json` from the script (template below), with your `keyframe_prompt` per shot.
2. For each shot: 2 keyframe candidates, 704×1280, `edit_flux2_klein_multiref` with the locked full bodies of the
   characters in frame as `ref1`–`ref4` (in order, repeated to fill the four slots):
   `comfy_submit(story, "edit_flux2_klein_multiref", {"prompt": <yours>, "seed": <k>, "width": 704, "height":
   1280}, files={"ref1": "02-cast/<a>/full_body.png", …}, dest="epNN/keyframes/candidates/sNN_c<k>")`.
   Send the whole episode's batch at once; the workers run in parallel.
3. `comfy_fetch` each; look at each: the right characters and only them, identity held (head, colours, outfit,
   signature item), the universe's look, hands, the shot's framing, nothing written. Pick the better one →
   `store_copy` to `epNN/keyframes/sNN.png` → `store_lock` ("Claude's pick: <why>"). Both wrong → 2 new seeds,
   with the prompt adjusted to what went wrong (in the positive).
4. Go straight on to `steps/5b-clips.md`.

## Template

`epNN/shots.json` (the fields the assembly reads, plus your prompts):

```json
{
  "bgm": null,
  "hook": null,
  "card": null,
  "shots": [
    {"id": "s01", "seconds": 10, "place": "cafe", "characters": ["ana", "bo"],
     "lines": [{"speaker": "ana", "text": "…"}, {"speaker": "bo", "text": "…"}],
     "expression": "a tense and composed expression", "keyframe_prompt": "…", "clip_prompt": "…"},
    {"id": "s02", "seconds": 5, "place": "loft", "characters": ["bo"],
     "lines": [{"speaker": "bo", "text": "…"}], "framing": "faces_camera"},
    {"id": "s03", "seconds": 5, "place": "loft", "characters": ["ana"],
     "reaction": "the smile drops, the eyes harden"}
  ]
}
```

- `id`: `s01`, `s02`… in script order. `seconds`: 5 or 10 (the clip model makes nothing else).
- `characters`: who is in the picture, 1–3, in order; a speaking shot's speakers are all in it.
- `lines`: the script's exact words, in the order spoken. No lines: a silent shot, with `reaction`.
- `framing` (one character): `faces_camera` unless the three-quarter framing has been checked on this story.
- `bgm`, `hook`, `card`: `null` here, filled at the assembly.

## Checklist

- [ ] One shot per script block, same order, same words; every shot 5 or 10 s and within its word budget.
- [ ] Every keyframe prompt written from the pattern (Medium and Heads word for word, nobody else named).
- [ ] Every pick looked at and explained in its note; never a keyframe with an extra person, text or a broken face.
- [ ] The spend stays under twice the episode's estimate (else stop and ask).

## Gate question

None: this step does not stop. Rida's next gate is the whole episode (`steps/6-assemble.md`).

## What to show Rida

Nothing during the step (a one-line progress note at most, e.g. "keyframes done, 12/12 — making the clips").

## Tools

- `store_read`, `store_write` — free: the script, sheets, plates; `epNN/shots.json`.
- `templates_list` — free: the image templates, their sizes and reference slots.
- `comfy_submit` — COSTS MONEY: one keyframe candidate per call, inside the episode's estimate.
- `comfy_fetch`, `comfy_jobs`, `view_file` — free: collect and look.
- `store_copy`, `store_lock`, `store_unlock` — free: the chosen candidate becomes `epNN/keyframes/sNN.png`, locked.
- `cost_ledger`, `runpod_health` — free.

## Next

`steps/5b-clips.md`, straight away.

# Story, step 5b: the clips (GPU: clips and voice conversion, no questions)

One clip per shot, made from its locked keyframe with the lines spoken in the clip itself, checked by the server,
re-voiced with each speaker's locked voice, chosen and locked by you. Rida sees the locked clips and the finished
episode at the next gate; nothing is assembled from a take that is not approved.

**Before writing any prompt, read `PROMPTS.md`** (in this skill's folder): the patterns that worked and the checks
before sending.

## When to use

- Every shot of `epNN/shots.json` has a locked `epNN/keyframes/sNN.png`.
- A shot to redo after Rida's episode review (his reason in the note).

## Read first

- `store_read epNN/shots.json` and `epNN/takes.json` (what exists, what is approved).
- `comfy_jobs(open_only=True)`: jobs already sent and not collected — collect them before sending more.

## Run (no questions)

For every shot, the whole episode in one batch:

1. Write the clip prompt (the one-speaker, two/three-speaker or silent pattern of `PROMPTS.md`) into the shot's
   `clip_prompt`.
2. 2 seeds per shot: `comfy_submit(story, "ltx25_i2v_speech", {"prompt": <yours>, "seed": <s>, "seconds": <5|10>},
   files={"image": "epNN/keyframes/sNN.png"}, episode=N, shot="sNN")`. Each clip becomes the shot's next take.
3. `comfy_fetch(story, job, wait_s=240)` each (a long queue is normal: fetch again later), then
   `verify_take(story, N, "sNN", "vK")`. States: `ok` · `mismatch` (a line not heard, or out of order) · `late`
   (the last word too close to the end) · `no_speech`.
4. Among the `ok` takes, look at the contact sheets: identity held, mouths moving only on their own lines, no
   extra person, no burned-in text, the universe kept, no camera drift. Keep the best.
5. `vc_clip(story, N, "sNN", "vK")` → `vc_fetch(…)`: the same picture with every line in its speaker's locked voice
   (a new take). → `approve_take(story, N, "sNN", <the voiced take>, note="Claude's pick: <why>")`.

When a shot fails (no `ok` take, or every take has a picture defect):

- 2 new seeds, with a line in `epNN/defects.md` (shot, takes, what was wrong).
- Still failing after 3 rounds on a two- or three-speaker shot: split it into one shot per speaker (same lines, same
  order) in `shots.json`, make their keyframes (`steps/5a-shots.md` step 2–3), and go on — the script's words never change.
- A one-speaker shot still failing after 3 rounds: stop and tell Rida what fails, with the takes to see.
- Never fill a failed shot with a still, a hold or a slowed clip.

When every shot has an approved voiced take, go straight on to `steps/6-assemble.md`.

## Template

`epNN/defects.md`, one line per failed round (it tells the next episode what to avoid):

```
- s04 v1, v2 — mismatch: Théo's line not heard (the strawberry spoke it); next: one-speaker shot of Théo
- s07 v3 — picture: a second kiwi appeared at the edge; next: new seed, "only this one character" kept
```

## Checklist

- [ ] No open jobs left behind before a new batch.
- [ ] Every clip prompt written from its pattern (Medium and Heads word for word, the script's exact lines, each
      speaker's voice, the closing sentence).
- [ ] Every take checked with `verify_take`; only an `ok` take is kept.
- [ ] Every kept take re-voiced with the locked voices before it is approved.
- [ ] Every approval's note says why; every defect written in `epNN/defects.md`.
- [ ] The spend stays under twice the episode's estimate (else stop and ask).

## Gate question

None: this step does not stop. Rida's next gate is the whole episode (`steps/6-assemble.md`).

## What to show Rida

Nothing during the step (a one-line progress note at most). If a one-speaker shot fails three rounds: the failing
takes as files (`file_download`), what the check heard, and your read.

## Tools

- `store_read`, `store_write` — free: `shots.json`, `takes.json`, `defects.md`.
- `comfy_submit` — COSTS MONEY: one clip per call, inside the episode's estimate.
- `comfy_fetch`, `comfy_jobs`, `view_file` — free: collect and look.
- `verify_take` — free: the clip check (this server's CPU, ≈ 35 s a clip).
- `vc_clip` — COSTS MONEY (tiny): re-voice a checked take with the locked voices; then `vc_fetch` (free).
- `approve_take` — free: the chosen voiced take, with why.
- `file_download` — free: a take for Rida, only when a shot fails.
- `cost_ledger`, `runpod_health` — free.

## Next

`steps/6-assemble.md`, straight away.

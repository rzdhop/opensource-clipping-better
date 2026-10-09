# Story, step 7: the next episode (free)

The series remembers. After an episode Rida confirmed, you write what happened into `memory.md` and propose three
directions for the next episode; he picks one or corrects it, and the loop goes back to the script. The universe,
the cast pictures and the voices stay locked: the next episode reuses them.

## When to use

- Rida confirmed an episode and said yes to the next one.
- Rida pastes comments, numbers or his own notes on an episode.

## Read first

- `store_read memory.md`, `04-season.md`, the last `epNN/script.md`, `epNN/metadata.md` and `epNN/defects.md`.
- The audience's feedback, if Rida gave any.

## Propose

No questions first: the memory, then three directions. One line at the end invites what Rida knows and you don't:
"If you have comments or numbers from the audience, paste them and I'll adjust."

## Template

`memory.md` (rewrite it whole each time; it is the only thing the next script reads about the past):

```
# Series memory

## What happened
- ep01: <two lines: the conflict, the turn, the cliffhanger as it ended>
- ep02: …

## Relationships
- <Name> → <Name>: <what each knows, wants, hides — one line per pair that matters>

## Open threads
- <a promise to the audience not yet paid, one line each, oldest first>

## Last frame
<one line: the picture and the line the last episode ended on — the next hook starts from it>

## Audience
- ep01: <what the comments argued about; what to give more of; what fell flat>
```

Three directions for episode N+1, each: the hook (first 5 s, picking up from the last frame), the escalation in one
line, the cliffhanger and its shape (rotate: revelation / reversal / deadline / intrusion), which open thread it pays
or opens, which characters it needs (a new character → `steps/3-cast.md` first), and its estimated cost (from
`cost_ledger` of the last episode).

## Checklist

- [ ] `memory.md` covers every episode so far; the relationships are current; the last frame is written.
- [ ] The audience's feedback is in it when Rida gave some.
- [ ] Three directions that differ; each picks up from the last frame; each cliffhanger shape differs from the
      last episode's.
- [ ] A new character or a new place is flagged.
- [ ] `04-season.md` updated once Rida picks.

## Gate question

"For episode N+1: direction 1, 2 or 3 — or a mix? Correct anything."

## What to show Rida

The memory in short (what happened, who knows what, the open threads), then the three directions as cards with
their cost.

## Tools

- `store_read` — free: memory, season, the last script, metadata, defects.
- `store_write` — free: `memory.md`, `04-season.md`.
- `cost_ledger` — free: what the last episode cost (the base of the estimate).
- `story_list` — free: the story's episodes so far.

## Next

`steps/4-script.md` for episode N+1 (and `steps/3-cast.md` first if a new character joins).

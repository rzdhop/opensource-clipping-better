# CHECKPOINT — where the work stands (replace this file, never append)

**2026-10-09 — the first real episode is done (Bouc émissaire ep01, Rida: "the full runtime test was successful"),
and the story look is upgraded for every story.**

## Live on Rida's host
- `showrunner-mcp` (21 tools) and the `rzdhop-story` skill on claude.ai; the claude.ai project "AI gen App" holds the
  current context (CLAUDE.md, .claude/*, docs/MCP.md, showrunner/README.md) — refresh it after changing them here.
- Story `bouc-emissaire` (French; Rida the apple, Marie-Jeanne the peach, Octave the pineapple, Paloma the mango from
  Faille d'amour): brief, universe, cast with locked voices, ep01 script and approved takes, final.mp4 (not locked).
  Remaining ep01 defects are script-level; Rida does not want ep01 redone in the new style.

## Just changed (deploy: `git pull && sudo systemctl restart showrunner-mcp`, then upload the new skill zip)
- `assemble.py`: automatic punch-in edit (DEC-335). The skill: busy sets, extras, emotions, multi-character clips,
  readable text (DEC-334). Tested on real ep01 clips through `assemble()` (length kept, 8 cuts, framings right).

## Next
- Rida starts a **new story** in a fresh claude.ai chat with the upgraded skill. Watch A-230 (extras stay in
  universe), A-231 (punch-in crop on edge characters), A-232 (burned subtitles, solo push-in); log defects in
  `epNN/defects.md`; fix PROMPTS.md / steps / server as you go.
- Then episode 2 and series memory (plan 36 stage 6): `memory.md` from the episode, Rida's audience feedback,
  three directions for the next episode, continuity from the last frames.

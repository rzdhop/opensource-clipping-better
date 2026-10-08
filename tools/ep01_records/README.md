# How "Faille d'amour" episode 1 (v3, the talking cut) was made on 2026-10-07

**2026-10-08:** steps 1–3 ran through the rzdhop-story MCP job client, which was deleted with that server
(DEC-325); their scripts are in git history before that commit. Step 4 still runs.

The record of the chat-driven production that the pipeline now does by itself (DEC-318). Each
script is resumable (a file on disk is skipped, an in-flight job is waited for) and capped in
dollars; they use the MCP job client in-process, the same code the tools run.

1. `../regen_faille_ep01_clips.py` — the ten wide shots again with the 8-step motion recipe
   (plan 34) + continuation clips (superseded by the per-line talking parts).
2. `closeups.py` — eight per-speaker close-ups (FLUX.2 klein multi-reference edit: the character's
   sheet + the shot's keyframe), $0.097.
3. `talking_clips.py` — one `s2v_wan22` talking clip per line from the speaker's close-up and the
   line's WAV (`outputs/faille_damour/ep01/talk_plan.json`), 13 clips, $0.886.
4. `build_cut_sheet_v3.py` — the cut sheet: a wide beat per scene, then each talking clip trimmed
   to its line (+0.35 s), the hook and the end card; rendered by `../episode_cut.py`.

The voices: Gemini prebuilt voices re-cast from free samples (`voices_v2/cast.json`), through the
MCP's `tts_batch`.

# ASSUMPTIONS — open questions only

`A-NNN | area | the open question | how it gets checked`. Delete a line when it is settled (say how in the action
log). History: `git show 9f0ce43:.claude/ASSUMPTIONS.md`. Next free id: **A-233**.

## AI stories (showrunner + skill)
A-230 | skill / extras | Does naming each background person with the universe's head rule keep every extra in-universe (Bouc émissaire test: generic "coworkers" drew human faces 4/6, named ones held) across other universes? | The next story's keyframes
A-231 | assemble.py / punch-in | Does the 1.25× side crop keep the speaker's head whole when they stand near the frame edge or the keyframe swaps sides? | Contact sheet of the next episode (fix with `positions`)
A-232 | skill / clips | How often does the clip model burn subtitle-like text or push in on a one-character clip, now that sets carry readable text? | Rate in the next episode's defects.md
A-222 | skill / keyframes | Does the three-quarter wording ("head turned three-quarters to the right, eyes looking past the right edge of the frame") draw no extra person? Until then keyframes face the camera. | One keyframe (≈ $0.03)
A-228 | skill / reaction clip | Does "listens to someone just off-screen beside the camera" avoid inventing a person in an I2V clip? | First silent reaction clip
A-229 | skill / exchange prompt | Does the exchange wording ("two distinct voices") keep the listener silent when only one line is scripted? | `verify_take` on the first one-line two-character shot
A-216 | showrunner billing | RunPod bills only `executionTime` (queue and cold start unbilled), as the ledger assumes. | Ledger vs the RunPod balance
A-223 | RunPod | Were the 20–35 min queues of 2026-10-08 a time-of-day shortage, not the norm? | Queue times on later runs
A-201 | worker volume | With `<volume>/models/chatterbox` present, the node's first download writes through the symlink once. | Fresh volume + first VC job
A-197 | worker image | Fill-ChatterBox says MIT but has no LICENSE file (risk accepted by Rida). | Upstream adds a LICENSE
A-068 | assets/bgm | The 15 `assets/bgm/` tracks have no recorded licence; Clips and the assembly use them. | Find each track's source

## Clips
A-009 | LLM chain | Can a usable free Groq key be obtained? (Gemini is the fallback to recommend.) | Next run with a Groq key
A-026 | LLM chain | Can `mistral-small-latest` (in the default chain) carry the analysis? Never measured. | Chain test with a Mistral key
A-027 | LLM chain | Can Groq's `openai/gpt-oss-120b` carry the analysis (watch `content=null`)? | `bench_llm.py` with a Groq key
A-010 | LLM chain | NIM default model ids die within weeks. | `bench_llm.py --nim-shortlist` when NIM fails
A-019 | LLM chain | Is NVIDIA's 120 s probe timeout enough for its free-tier queue? | Settings → Test provider chain
A-153 | anthropic_llm | Are `claude-sonnet-5-5` / `claude-opus-5-5` served on Rida's `ANTHROPIC_API_KEY`? | Free Settings probe
A-154 | anthropic_llm | With server-side fallbacks on, does the reply's `model` name the served model? | First live Anthropic call
A-097 | transport | Does any provider need its credential header to follow a cross-origin redirect (also STT/Pexels, A-099)? | Suspect first on a 401/403 after a 30x
A-022 | worker (VPS) | Does `Popen.kill` free a cancelled job's slot as fast on the VPS as on Windows? | Cancel a running job
A-025 | render | Does camera-switch rendering still work (never rendered since the refactor; needs pyannote + HF token)? | One camera-switch job
A-023 | notebooks | Do the three notebooks run end to end on Colab/Kaggle? Structurally verified only. | A real run
A-024 | tests | `test_bypass_does_not_import_ctranslate2` fails where ctranslate2 is installed: real leak or not? | Run it with ctranslate2
A-083 | tests | The suite is safe under `-n 4` (no shared state across workers). | Compare serial vs parallel counts
A-017 | web media URLs | Is a 12 h `MEDIA_URL_TTL` right? | Rida's reports
A-018 | web files route | Nothing relies on `/api/outputs/...` defaulting to attachment (now inline unless `?download=1`). | Broken consumers after deploy
A-169 | dashboard | Over plain http, does `execCommand('copy')` copy on a phone tap (iOS too)? | Tap the feed's copy button
A-033 | docker-compose | `extra_hosts: host.docker.internal:host-gateway` works on Docker engines other than 29.1. | Another engine version

# API

Auth is opt-in: every route needs the API token except `GET /api/health`, but
only when the server has `API_TOKEN` set. Pass it as
`Authorization: Bearer <token>` or `X-API-Key: <token>`. With no token set on
the server, the header can be dropped entirely. See
[deploy-tailscale.md](deploy-tailscale.md) for where the token comes from.

**The one exception: media URLs inside a job response.** A `<video src>` and an
`<a href download>` are requests the *browser* makes, so they cannot carry a
header — which is why, before this existed, the dashboard's player showed
nothing and its Download button saved the 401's JSON body under a `.json`
extension. The `download_url`, `thumbnail_url` and `srt_url` returned by
`GET /api/jobs/{id}` therefore arrive carrying `?exp=…&sig=…` and may be
fetched with no header at all.

That is not the token in a URL, and the rule against putting it there still
holds. The signature is an HMAC over one `(job_id, filename, exp)` triple keyed
by a value *derived* from the token: it opens exactly that one file, expires
(12 h by default, `MEDIA_URL_TTL`), cannot be reversed into the token, and is
refused on every other path — including `GET /api/outputs/{id}`, the directory
listing, which stays header-only. Holding a valid token is what mints these
URLs, because they are generated when a job is serialized.

Interactive docs are at `/docs` on the running server.

## The short version

```bash
BASE=https://your-machine.your-tailnet.ts.net
AUTH="Authorization: Bearer $API_TOKEN"

# 1. Upload the video, and the subtitles if you have them.
curl -H "$AUTH" -F "file=@talk.mp4"  $BASE/api/upload
curl -H "$AUTH" -F "file=@talk.vtt"  $BASE/api/upload

# 2. Start the job.
curl -H "$AUTH" -H 'Content-Type: application/json' -X POST $BASE/api/jobs -d '{
  "upload_filename": "talk.mp4",
  "transcript_filename": "talk.vtt",
  "clips": 5,
  "platform": "tiktok"
}'

# 3. Watch it, or just poll.
curl -H "$AUTH" -N $BASE/api/jobs/<id>/status
curl -H "$AUTH"    $BASE/api/jobs/<id>
```

One command does all of that from a machine with a home connection:

```bash
python tools/rzclips-fetch.py --url "https://..." --server $BASE --token $API_TOKEN
```

## Endpoints

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/health` | ffmpeg, GPU and job counts. **No token needed.** |
| `POST` | `/api/upload` | Upload a video or a subtitle file. Returns `{filename}`. |
| `POST` | `/api/jobs` | Create a job. 201 with the job. |
| `GET` | `/api/jobs` | List jobs, newest first. |
| `GET` | `/api/jobs/{id}` | One job, with its clips when finished. |
| `GET` | `/api/jobs/{id}/status` | Server-Sent Events: progress and log lines. |
| `POST` | `/api/jobs/{id}/source` | Attach a video to a job in `needs_upload`. |
| `POST` | `/api/jobs/{id}/cancel` | Stop a queued or running job. `202` with the job, now `cancelled`; `409` if it already finished. Its ffmpeg is killed at once; a provider request already in flight finishes or times out first. Files are kept. |
| `DELETE` | `/api/jobs/{id}` | Delete the job, its `outputs/{id}/` and the uploads no other job uses. `200` with `removed`/`kept` lists; `202` for a running job, which is cancelled first and removed once it stops. |
| `GET` | `/api/outputs/{id}` | List a job's output files. Header-only; a media signature never opens it. |
| `GET` | `/api/outputs/{id}/{file}` | Serve one, including `.srt`. Served `inline` so a `<video>` or `poster` can use it; add `?download=1` for `Content-Disposition: attachment`. Accepts a header **or** an `?exp=&sig=` pair. Range requests are supported, so seeking works. |
| `GET`/`PUT` | `/api/settings` | API keys (write-only), defaults, `allow_slow_chain`, `chain_blocked_reason` (why a chain job would be refused right now, or `""`), and AI Story's `story_llm_premium_chain` (the provider/model chain the premium writing calls run on; empty uses the default). `budget_timezone` (an IANA name, `""` = UTC) is validated on `PUT` (`400` naming `BUDGET_TIMEZONE`). `GET` adds the budget day, response only: `spend_day`, `spend_zone`, `spend_zone_error`, `day_extra_usd`, `daily_cap_below_spend`, `day_contributors`. |
| `POST` | `/api/settings/test-chain` | Send every keyed link a small real analysis request and report each one. Can take a few minutes. |
| `POST` | `/api/settings/check-anthropic-key` | Free. Asks Anthropic whether `ANTHROPIC_API_KEY` is accepted and each `anthropic/` link of the story premium chain is available (one model lookup per link, never a completion: every request on that provider is billed). `{"results": [{label, provider, model, status, text}], "verdict": "ready" \| "blocked", "message"}`; a row's `status` is `ok`, `no_key`, `bad_key`, `no_model`, `unreachable` or `failed`. `400` when the chain is unusable; `409` while a chain test runs; the key's value is never returned. |
| `GET` | `/api/broll/status` | Clips mode's B-roll sources: `{sources, available, local_dir, local_clips, root}` -- the configured order, the ones that can answer now (a keyed Pexels or Pixabay, a local folder holding a clip), the folder and how many clips it holds, and the root a folder may live under (`/app/broll` in Docker). |

### AI Story: the manual link's routes (plan 22)

A story whose clips are its own (the `native_speech_manual` profile) is served by a few extra routes under `/api/stories/{id}`:

| Method | Path | What it does |
|---|---|---|
| `GET` | `/episodes/{ep}/brief?platform=flow\|higgsfield` | The episode's shot brief (prompts, references, the line, the checks, each shot's state). `200`; `404` unknown story/episode; `400` unknown platform; `409` no storyboard yet. |
| `GET` | `/episodes/{ep}/brief.zip?platform=…` | The brief as a zip (`.md`, `.json`, every reference image). Same refusals as `.../brief`. |
| `GET` | `/image-brief?ep=` | The brief for a story's own cast sheets, place plates, props (and, with `ep`, one episode's keyframes). `200`; `409` while the style lock cannot be read. |
| `POST` | `/episodes/{ep}/shots/{shot_id}/clip` | Upload a shot's clip (multipart `file`). `201`; `404` unknown story/episode/shot; `409` a step running, or the storyboard not yet approved; `413` over the 500 MB cap; `400` with the reason (not MP4/MOV, under 2 s, not the story's frame within 2 % (9:16 unless the story was made at 16:9), no sound for a speaking shot). |
| `POST` | `/cast/{char_id}/sheet?which=portrait\|turnaround\|expressions` | Upload a character's own sheet. `201`; `404` unknown story/character; `400` a story whose images are the app's, a bad slot, an image too small; `415` not an image; `409` a step running. |
| `POST` | `/places/{place_id}/plate?variant=day\|…` | Upload a place's own plate; the rules of the sheet upload above. |
| `POST` | `/props/{prop_id}/image` | Upload a prop's own image; the rules of the sheet upload above. |
| `POST` | `/episodes/{ep}/shots/{shot_id}/keyframe` | Upload a shot's own keyframe (the story's frame within 2 %, cropped to the exact even frame). `201`; `409` storyboard not yet approved; the sheet upload's refusals otherwise. |

Each upload answers `{"...", "missing", "waiting", "resumed"}`: `missing` and `waiting` describe what the episode still needs ("Waiting for N clips"), and `resumed` is the paused job the upload restarted, if any (see "Job statuses" below).

### AI Story: the daily cap (plan 23)

The daily paid cap counts the whole app's spending in one budget day (00:00 to 24:00 in `BUDGET_TIMEZONE`, UTC by default). Three routes under `/api/budget` read it and allow more for today only:

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/budget/today` | Today's paid spending against the cap. `200`. |
| `POST` | `/api/budget/today/extra` | Allow `usd` more for today only; it ends with the day. Body `{usd, story_id?, note?, estimate_usd?}`. `200` with the new state; `400` for an amount that is not positive, one that would take the day's total allowed past **$25**, an unknown `story_id`, a negative `estimate_usd` or a `note` over 200 characters. |
| `DELETE` | `/api/budget/today/extra` | Take today's extra back; the saved cap applies again. `200` with the new state. |

The body of all three (`BudgetTodayResponse`): `day` (`YYYY-MM-DD` in the zone), `zone`, `zone_error` (null unless the configured zone could not be used, and the day is UTC), `spent_usd`, `extra_usd`, `daily_cap_usd` (the saved cap), `effective_cap_usd` (cap plus extra), `cap_below_spend`, `stories` (the five that spent most: `{story_id, title, usd}`), `other_usd` (the rest), `grants_today` (`{day, at, usd, story_id, note}`) and `resets_at` (the next local midnight, ISO). An allowance is logged three ways: `spend.json` (`grants`, the last 200), the server's output and, when `story_id` is given, that story's `activity.log`. Like every write, it is refused from another website's page (`403`) while the server has no token.

**The 409 of a job the daily cap alone refuses.** `POST /api/stories/{id}/steps/{step}` (and a clip regenerate) answers `409` with an object `detail`, not a string, when the daily cap is what stops the job; every other refusal keeps its plain-string `detail`.

```json
{
  "message": "Today's paid spending is already $8.38, over the $4.00 daily cap: this cast (est $0.60: 5 portraits $0.20 + 10 sheet edits $0.40) would bring it to $8.98. Allow $4.99 more for today only, raise the daily cap in Settings, or wait for the day to reset at 00:00 UTC.",
  "code": "budget_daily_cap",
  "errors": ["fal/seedream-4.5: refused: ..."],
  "today": {"day": "2026-10-04", "zone": "UTC", "spent_usd": 8.384, "extra_usd": 0.0,
            "stories": [{"story_id": "d0ee5ebd745d", "title": "...", "usd": 5.0267}],
            "story_count": 2, "other_usd": 0.0},
  "estimate": {"usd": 0.6, "parts": [{"what": "portraits", "qty": 5, "usd": 0.2, "link": "fal/seedream-4.5"},
                                     {"what": "sheet edits", "qty": 10, "usd": 0.4, "link": "fal/seedream-4.5-edit"}],
               "llm_worst_usd": 0.0},
  "cap": {"daily_usd": 4.0, "effective_usd": 4.0},
  "needed_usd": 4.99,
  "other_cap_refusal": null
}
```

`needed_usd` is today's spend, plus the estimate, plus `llm_worst_usd` (the worst the job's LLM calls may add), minus `cap.effective_usd`, rounded up to the cent: the amount to allow. `other_cap_refusal` is the refusal the job would still meet with an unlimited day (the episode's or the story's cap, as a sentence), or `null`: when it is set, an extra for today does not lift the refusal. A client that reads `detail` as text should handle both shapes (the dashboard does).

**The estimate's `today` block.** `GET /api/stories/{id}/estimate/{step}` carries, on every step, a `today` object: the `GET /api/budget/today` body above, plus `story_count` (how many stories spent today). A cast or places estimate also counts the whole sum the gate checks (images and edits), so its `est_usd` equals the 409's `estimate.usd`.

### AI Story: other routes added by plan 23

| Method | Path | What it does |
|---|---|---|
| `GET` | `/api/stories/universes` | The ten universes and which style takes which: `{"universes": [{id, label, audience_note?}], "by_style": {style_id: {"universes": [id, ...], "default": id \| null}}}`. A style that takes none is absent from `by_style`. |
| `PATCH` | `/api/stories/{id}/subtitle-style` | Set the story's own subtitle look, or clear it. The body is the whole object, `{font_family, size_pct, position_pct, text_colour, highlight_colour, outline_px, outline_colour, box}` (every field optional; `box` is `{colour, opacity_pct}` or `null`), or `null` / `{}` to clear. It replaces the stored look and is allowed at any time, the style lock included; the next render burns it. `200` with the story; `404` unknown story; `409` while a render, re-render or fast track of the story is queued or running; `400` with `{"message", "errors"}` for a value out of range (`size_pct` 60-160, `position_pct` 15-95, `outline_px` 0-8, `opacity_pct` 0-100, colours `#RRGGBB`), a font the app does not ship, or a text or highlight colour whose contrast against the outline or the box is below **4.5** (the ratio is named). |
| `PATCH` | `/api/stories/{id}` | Also takes, in `generation_profile`, the plan-23 keys `universe`, `sheet_mode`, `body_rule`, `image_preference`, `prompt_style` and `variants` (`null` clears one), and `stock_cutaways` (`"on"`; `null` clears it; it only affects the next assets run). A change of `prompt_style` answers with `stale_clips` (how many current clips it makes stale, uploads included) and a `warning`. `aspect` is not editable: a `PATCH` that changes the story's frame, or moves a 16:9 or 1:1 story off the v2 pipeline, answers `409` "the frame is chosen when the story is made" (resending the frame it has, or `null` on a 9:16 story, changes nothing); a profile that can no longer make the story's frame is a `400` with the reason. |
| `POST` | `/api/stories` | Also takes, in `generation_profile`, `aspect` (`"16:9"` or `"1:1"`; left out = 9:16, the frame of every earlier story) and `stock_cutaways` (`"on"`). The frame is set here and nowhere else. `400` with the reason when the profile cannot make it: not the v2 pipeline, the `local` route or the `free` profile at tier 2+ (local ComfyUI clips are 9:16 only), 1:1 with native speech on Veo or with the manual (Flow, Higgsfield) link. The links make 9:16 + 16:9 + 1:1 (seedance, kling), 9:16 + 16:9 (LTX, Veo, your own Flow or Higgsfield clips) or 9:16 (local). A non-9:16 story's metadata pack carries `aspect` and `aspect_note`. |
| `GET` | `/api/stories/{id}/estimate/assets` | The assets estimate also carries `units.stock` while `stock_cutaways` is on and a shot is eligible or already filled: `{count, shots, stock, saves_usd, sources, message}` (`count` = eligible shots with no keyframe or clip yet, "up to N shots may be stock (free, saves about $x)"; `stock` = shots already filled; `sources` = the stock sources that can answer). The price stays the generated one until the fill has run. |
| `POST` | `/api/stories/{id}/characters/{char_id}/variants` | Add an appearance variant to a character: `{label, delta_text}` (label up to 40 characters, delta up to 60 words, no entity name). `201` with `{character, variant, target}`; the variant's `variant_id` is a slug of the label. No image is made: `target` (`character:{char_id}:variant:{variant_id}`) is the regenerate that draws its sheets, behind the estimate gate (`GET /estimate/regenerate?target=` says "N variant sheets" and the price), and `POST /api/stories/{id}/approve/variant:{char_id}:{variant_id}` approves them on their own. `404` unknown story or character; `409` a story without variants (no sheet mode, no `variants: "on"`), a character not written yet or already holding three, or a step running; `400` a missing or too-long label or delta. A shot's variant is set with `PATCH /api/stories/{id}/episodes/{ep}/storyboard` (`shots: [{shot_id, variants: {char_id: variant_id \| null}}]`). |
| `GET` | `/api/stories/{id}/characters/{char_id}/voice-reference` | What the cast step shows for a character's own voice recording: `{"voice_reference": {name, sha256, duration_s, uploaded_at, consent} \| null, "pinned": bool, "engine": {"ready": bool, "reason": str}}`. `engine` says whether chatterbox, the local engine that clones it, is installed on this server (an import lookup, nothing is loaded), with the probe's own sentence as `reason`. `404` unknown story or character. |
| `POST` | `/api/stories/{id}/characters/{char_id}/voice-reference?consent=true` | Give a character a voice recording to be cloned locally. Multipart, field `file`: 5 to 30 s of audio, up to 10 MB, re-encoded to `voice_reference.wav` (mono, 24 kHz, 16-bit, no metadata, the original name never kept). `consent=true` is required ("this is my voice, or I have the speaker's permission", DEC-281) and checked before the body is read. `201` with the entry `{name, sha256, duration_s, uploaded_at, consent}`; a new upload replaces the old one (if it was the pinned voice, the character's sample and the cast approval go). `400` no consent, or the recording is shorter than 5 s or longer than 30 s; `413` over 10 MB (a declared size is refused before the body, the stream stops at the cap); `415` no audio track or not a file ffprobe can read; `404` unknown story or character; `409` while a step of the story is queued or running, or when a symlink sits in the way; `503` ffmpeg or ffprobe missing. Pinning it is `POST /api/stories/{id}/regenerate` with `{"target": "character:{char_id}:voice", "voice": {"provider": "chatterbox", "voice_id": "reference"}}`, accepted only with the recording present and chatterbox installed. |
| `DELETE` | `/api/stories/{id}/characters/{char_id}/voice-reference` | Remove the recording and its file: `{name, entry_removed, file_removed}`. `404` unknown story, character or no recording; `409` while the character's voice is the recording (pin another voice first), while a step runs, or when a symlink is in the way. |

## Creating a job

A job needs exactly one source:

| Field | Meaning |
|---|---|
| `upload_filename` | A file you uploaded. The reliable path. |
| `reuse_job_id` | Re-run an earlier job, reusing its video and its analysis. |
| `source_url` | Ask the server to download it. Best-effort — see below. |

The settings worth knowing:

| Field | Default | Notes |
|---|---|---|
| `clips` | `7` | How many to produce. Fewer are returned if the video does not contain that many self-contained moments, and the log says so. |
| `platform` | `auto` | `tiktok`/`reels` 15-90s, `shorts` 15-59s, `auto` 20-75s, `long` 60-179s. Sets the duration window. |
| `output_language` | `auto` | ISO-639-1 for titles and captions. `auto` follows the video. English titles and tags are produced either way. |
| `transcript_filename` | – | A `.vtt`, `.srt` or `.json3` you uploaded. Skips transcription entirely. |
| `llm_chain` | – | Override the provider chain for this job. |
| `stt_chain` | – | Override the transcription chain. `none` disables it. |
| `dry_run_analysis` | `false` | Analyse, save the result, stop before rendering. |
| `ratio` | `9:16` | Also `16:9`, `1:1`, `3:4`, `4:5`. |
| `use_broll`, `hook_v2`, `use_split_screen`, … | | Every CLI flag has a field; see `/docs`. |

### A chain that can only run on its floor is refused

If the chain names a fast link (Groq, Gemini, OpenRouter, Mistral) but none of
them has a key, and only NVIDIA does, `POST /api/jobs` answers **400** before the
job exists. The `detail` names each keyless link, its env var and its free
signup page. NVIDIA's free tier runs at about 12 tokens/s behind a queue, so it
cannot carry the analysis on its own.

To run anyway, `PUT /api/settings` with `{"allow_slow_chain": true}`. Sending
`false` clears the override. A chain that names no fast link at all, such as
`llm_chain: "nvidia/..."`, is taken as written and is not refused. Render-only
reruns (`reuse_job_id` with the cached analysis) call no provider and are never
refused.

### Testing the chain

```bash
curl -H "$AUTH" -H "Content-Type: application/json" -d '{}' \
     $BASE/api/settings/test-chain
```

Every keyed link is sent the analysis's own first request on a short test
transcript with one clip in it, not only up to the first that answers.
Providers are asked at the same time; links on one provider one after another.
Each result has a `status`:

| status | meaning |
|---|---|
| `ok` | Completed the real request. `candidates` and `found_moment` say what it found. |
| `alive` | Failed the real request (`reason`) but answered a plain ping: the key works, the model cannot do the job. |
| `failed` | Neither. `reason` says why. |
| `no_key` | Skipped; `env_key` and `signup_url` say what to set. |
| `unused` | A key is set for a provider this chain does not name. Never contacted; `note` names the link to add. |

Rows also carry `latency_seconds`, `work_timeout_seconds` (as long as a job
would wait for that provider: 120-180s for the fast tiers, 280s for NVIDIA), `level` (the structured-output mode that worked),
`used_model` (differs from `model` when a retired model was swapped for the
provider's fallback) and a `note`. `verdict` is `ready` (a fast link completed
it), `floor_only` (only NVIDIA did), `blocked` (a job would be refused, see
above) or `dead` (nothing did); `ready` is `verdict == "ready"` and `message`
explains the others. The whole test waits for the slowest provider, 290s at
most for the default chain; a healthy one answers in seconds. Send `{"llm_chain": "..."}` to test a
different chain. The request never carries a base URL: `custom/...` uses
`LLM_CUSTOM_BASE_URL` from the server's environment. A second test while one is
running gets `409`, and a test that outlives its budget gets `504`.

### `source_url` is best-effort

Sites score a download request by the IP's reputation before anything else, and
a server in a datacenter range is refused often. When that happens the job moves
to **`needs_upload`** rather than failing: it keeps its id, its settings and
anything it already produced, and `error` explains what to do. Attach the file
and it resumes:

```bash
curl -H "$AUTH" -F "file=@talk.mp4" -F "subtitle=@talk.vtt" \
     $BASE/api/jobs/<id>/source
```

## Job statuses

`queued` → `downloading` → `transcribing` → `analyzing` → `rendering` →
`completed`, plus `failed`, `cancelled`, and `needs_upload`.

A job interrupted by a server restart is marked `failed` at startup rather than
sitting in a running state forever.

`cancelled` is final: a cancelled job does not turn `failed` when its interrupted
work unwinds, and a job that completes before the cancel lands stays `completed`
(the cancel gets `409`).

An AI Story step is a job too, with `"kind": "story_step"` (a clip job says
`"kind": "clip"`) and its `story_id`, `ep`, `step` and `params`. It goes
`queued` → `running` → `awaiting_approval`, or `failed` / `cancelled`.
`awaiting_approval` frees the worker and ends the status stream, and a cancel
gets `409`; it survives a restart, where a `running` step is marked `failed`.
Approving the step, or regenerating it, moves it on to `completed`.

A step whose clips are the user's own (the `native_speech_manual` profile:
every clip on `manual/upload`) ends `awaiting_uploads` instead when clips are
missing -- the assets step, or the fast track or agent run paused there -- with
`uploads` (`count`, `missing`, `message`, `brief`) on the job. Like
`awaiting_approval` it frees the worker, ends the status stream and survives a
restart. `GET /api/stories/{id}/episodes/{ep}/brief?platform=flow|higgsfield`
(JSON) and `.../brief.zip` hand out the shot brief; each clip goes to
`POST /api/stories/{id}/episodes/{ep}/shots/{shot_id}/clip` (multipart, field
`file`), and the upload that leaves nothing missing runs the paused step
again as a new job (the paused one moves on to `completed`, `resumed_by` it).

## Watching a job

`GET /api/jobs/{id}/status` is Server-Sent Events. `EventSource` cannot send
headers, so read it with a normal request:

```python
import httpx, json
with httpx.stream("GET", f"{base}/api/jobs/{job_id}/status",
                  headers={"Authorization": f"Bearer {token}"}, timeout=None) as r:
    for line in r.iter_lines():
        if line.startswith("data:"):
            print(json.loads(line[5:]))
```

## Errors

| Code | Meaning |
|---|---|
| `400` | The payload is missing a source, a field is out of range, or the chain would run on its slow floor alone (see above). |
| `401` | Missing or wrong token. Only happens when the server has `API_TOKEN` set. |
| `403` | A browser's cross-site write, while the server has no `API_TOKEN` set -- the request came from another site's page, not this app. |
| `404` | No such job or file. |
| `409` | Attaching a source to a job that is not waiting for one, a chain test already running, cancelling a job that already finished, or rerunning (`reuse_job_id`) a job that is still running. An AI Story step the daily cap alone refuses answers `409` with an object `detail`, `code: "budget_daily_cap"` (see "AI Story: the daily cap"). |
| `413` | Upload over 2 GB. |
| `429` | The queue is full: `MAX_QUEUED_JOBS` jobs (default 20; `0` = no limit) are already waiting for a worker. Retry once one has started. |

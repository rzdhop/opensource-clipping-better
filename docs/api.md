# API

Every route needs the API token except `GET /api/health`. Pass it as
`Authorization: Bearer <token>` or `X-API-Key: <token>`. See
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
| `DELETE` | `/api/jobs/{id}` | Cancel if running, then delete. |
| `GET` | `/api/outputs/{id}` | List a job's output files. Header-only; a media signature never opens it. |
| `GET` | `/api/outputs/{id}/{file}` | Serve one, including `.srt`. Served `inline` so a `<video>` or `poster` can use it; add `?download=1` for `Content-Disposition: attachment`. Accepts a header **or** an `?exp=&sig=` pair. Range requests are supported, so seeking works. |
| `GET`/`PUT` | `/api/settings` | API keys (write-only), defaults, `allow_slow_chain`, and `chain_blocked_reason` (why a chain job would be refused right now, or `""`). |
| `POST` | `/api/settings/test-chain` | Send every keyed link a small real analysis request and report each one. Can take a few minutes. |

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
| `401` | Missing or wrong token. |
| `404` | No such job or file. |
| `409` | Attaching a source to a job that is not waiting for one, or a chain test already running. |
| `413` | Upload over 2 GB. |

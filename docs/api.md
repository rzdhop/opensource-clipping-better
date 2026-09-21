# API

Every route needs the API token except `GET /api/health`. Pass it as
`Authorization: Bearer <token>` or `X-API-Key: <token>`. See
[deploy-tailscale.md](deploy-tailscale.md) for where the token comes from.

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
| `GET` | `/api/outputs/{id}` | List a job's output files. |
| `GET` | `/api/outputs/{id}/{file}` | Download one, including `.srt`. |
| `GET`/`PUT` | `/api/settings` | API keys (write-only) and defaults. |

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
| `400` | The payload is missing a source, or a field is out of range. |
| `401` | Missing or wrong token. |
| `404` | No such job or file. |
| `409` | Attaching a source to a job that is not waiting for one. |
| `413` | Upload over 2 GB. |

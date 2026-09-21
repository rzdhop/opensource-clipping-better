#!/usr/bin/env python3
"""A minimal client: upload, create a job, follow it to the end.

Standard library only, so it runs anywhere Python does.

    python examples/create_job.py talk.mp4 --server https://host --token ...
    python examples/create_job.py talk.mp4 --transcript talk.vtt --clips 5
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.request
import uuid


def upload(base, token, path):
    """POST one file to /api/upload. Returns the stored filename."""
    boundary = uuid.uuid4().hex
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"

    with open(path, "rb") as handle:
        payload = handle.read()

    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + payload + f"\r\n--{boundary}--\r\n".encode()

    request = urllib.request.Request(
        f"{base}/api/upload", data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(request, timeout=3600) as response:
        return json.load(response)["filename"]


def api(base, token, path, payload=None, method="GET"):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{base}{path}", data=data, method=method, headers=headers
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        raw = response.read().decode()
    return json.loads(raw) if raw.strip() else {}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("video")
    parser.add_argument("--transcript", help="A .vtt/.srt/.json3 to skip transcription.")
    parser.add_argument("--server", default=os.environ.get("RZCLIPS_SERVER", ""))
    parser.add_argument("--token", default=os.environ.get("RZCLIPS_TOKEN", ""))
    parser.add_argument("--clips", type=int, default=5)
    parser.add_argument("--platform", default="auto",
                        choices=["tiktok", "reels", "shorts", "auto", "long"])
    parser.add_argument("--language", default="")
    args = parser.parse_args(argv)

    if not args.server or not args.token:
        parser.error("--server and --token are required "
                     "(or set RZCLIPS_SERVER / RZCLIPS_TOKEN)")

    base = args.server.rstrip("/")

    print("Uploading the video...")
    payload = {
        "upload_filename": upload(base, args.token, args.video),
        "clips": args.clips,
        "platform": args.platform,
    }
    if args.transcript:
        print("Uploading the transcript...")
        payload["transcript_filename"] = upload(base, args.token, args.transcript)
    if args.language:
        payload["output_language"] = args.language

    job = api(base, args.token, "/api/jobs", payload, method="POST")
    job_id = job["id"]
    print(f"Job {job_id} created.\n")

    # Polling rather than SSE, to keep this readable. The stream is documented
    # in docs/api.md.
    last = None
    while True:
        job = api(base, args.token, f"/api/jobs/{job_id}")
        status = job.get("status")
        progress = (job.get("progress") or {}).get("message", "")
        line = f"{status}: {progress}".strip()
        if line != last:
            print(f"  {line}")
            last = line
        if status in {"completed", "failed", "cancelled", "needs_upload"}:
            break
        time.sleep(5)

    if job.get("status") != "completed":
        print(f"\n{job.get('error') or 'The job did not complete.'}")
        return 1

    print("\nClips:")
    for clip in job.get("clips") or []:
        print(f"  #{clip.get('rank')}  {clip.get('filename')}")
        print(f"      {base}{clip.get('download_url', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

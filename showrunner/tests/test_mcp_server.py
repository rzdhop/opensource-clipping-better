"""The showrunner MCP server (stage 2): its settings, its two doors and its tools, in process. Needs fastmcp
(the repo's .venv); skipped elsewhere. Run: PYTHONPATH=<a pytest> .venv/bin/python -m pytest showrunner/tests"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import os
import secrets
import sys
from urllib.parse import parse_qs, urlparse

import pytest

fastmcp = pytest.importorskip("fastmcp")
try:  # fastmcp 4 ships against httpx2
    import httpx2 as httpx
except ImportError:  # pragma: no cover
    httpx = pytest.importorskip("httpx")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from showrunner import mcp_auth, mcp_server as S  # noqa: E402

PUBLIC = "https://node.example.ts.net:8443"
TOKEN = "s3cret-token"
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class FakeEndpoint:
    """A RunPod endpoint that answers from a script; records what it was sent."""

    def __init__(self, kind, eid, key, log):
        self.kind, self.id, self.key, self.log = kind, eid, key, log

    script: dict = {}   # job id -> list of status answers (the last repeats); shared by the fakes of one test

    def health(self):
        self.log.append(("health", self.kind, self.id, self.key))
        return {"workers": {"idle": 1, "running": 0, "throttled": 2}, "jobs": {"inQueue": 0}}

    def run(self, payload, *, execution_timeout_s=None):
        self.log.append(("run", self.kind, self.id, self.key, payload["workflow"]))
        return f"{self.kind}-job{sum(1 for e in self.log if e[0] == 'run')}"

    def status(self, job):
        seq = FakeEndpoint.script[job]
        return seq.pop(0) if len(seq) > 1 else seq[0]


def make_backend(tmp_path, **over):
    log: list = []
    settings = S.Settings(stories_dir=str(tmp_path / "stories"), state_dir=str(tmp_path / "outputs"),
                          endpoints={"video": "vid-ep", "images": "img-ep"}, keys={"video": "kv", "images": "ki"}, **over)
    backend = S.Backend(settings, endpoint_factory=lambda kind, eid, key: FakeEndpoint(kind, eid, key, log))
    backend.log = log
    return backend


def call(server, tool, **args):
    async def go():
        async with fastmcp.Client(server) as client:
            return await client.call_tool(tool, args, raise_on_error=False)
    return asyncio.run(go())


def payload(result):
    return json.loads(result.content[0].text)


# ------------------------------------------------------------------ settings

def test_settings_read_their_own_names_never_the_old_servers_or_the_live_endpoint():
    env = {"MCP_PORT": "8787", "MCP_PUBLIC_URL": "https://live.example", "MCP_HOST": "0.0.0.0", "MCP_TOKEN": "t",
           "RUNPOD_COMFY_ENDPOINT_ID": "LIVE", "RUNPOD_API_KEY": "main"}
    s = S.load_settings(env)
    assert (s.host, s.port, s.public_url, s.token) == ("127.0.0.1", 8787, "", "t")   # MCP_PORT/PUBLIC_URL ignored
    assert s.endpoints == {} and "LIVE" not in json.dumps(s.endpoints) + json.dumps(s.keys)
    assert s.forbidden == {"LIVE"}                       # read only to be refused (test below)
    s = S.load_settings({"SHOWRUNNER_MCP_PORT": "9000", "SHOWRUNNER_MCP_PUBLIC_URL": "https://x.ts.net:8443/",
                         "RUNPOD_SHOWRUNNER_VIDEO_ENDPOINT_ID": "v", "RUNPOD_SHOWRUNNER_VIDEO_KEY": "kv",
                         "RUNPOD_IMAGE_ENDPOINT_ID": "i", "RUNPOD_API_KEY": "main"})
    assert (s.port, s.public_url) == (9000, "https://x.ts.net:8443")
    assert s.endpoints == {"video": "v", "images": "i"} and s.keys == {"video": "kv", "images": "main"}


def test_templates_route_to_their_endpoint():
    assert S.template_kind("t2i_flux2_klein") == "images" and S.template_kind("edit_flux2_klein_multiref") == "images"
    assert S.template_kind("ltx25_i2v_speech") == "video" and S.template_kind("vc_chatterbox") == "video"


# ------------------------------------------------------------------ tools

def test_runpod_health_asks_both_endpoints_with_their_own_keys(tmp_path):
    backend = make_backend(tmp_path)
    got = payload(call(S.build_server(backend), "runpod_health"))
    assert got["video"]["endpoint"] == "vid-ep" and got["video"]["workers"]["throttled"] == 2
    assert sorted(backend.log) == [("health", "images", "img-ep", "ki"), ("health", "video", "vid-ep", "kv")]


def test_templates_list_names_values_files_and_endpoint(tmp_path):
    rows = {r["name"]: r for r in payload(call(S.build_server(make_backend(tmp_path)), "templates_list"))}
    assert set(rows) >= {"ltx25_i2v_speech", "vc_chatterbox", "t2i_flux2_klein", "edit_flux2_klein_multiref"}
    assert rows["ltx25_i2v_speech"]["endpoint"] == "video" and "image" in rows["ltx25_i2v_speech"]["files"]
    assert "prompt" in rows["ltx25_i2v_speech"]["values"] and "image" not in rows["ltx25_i2v_speech"]["values"]
    assert rows["edit_flux2_klein_multiref"]["endpoint"] == "images"


# ------------------------------------------------------------------ the doors (as the live server's auth test)

@contextlib.asynccontextmanager
async def client_for(app):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=PUBLIC) as client:
            yield client


def initialize(client, headers):
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    return client.post("/mcp", json=body, headers={**MCP_HEADERS, **headers})


def app_for(tmp_path):
    return S.build_server(make_backend(tmp_path, token=TOKEN, public_url=PUBLIC)).http_app()


@pytest.mark.anyio
async def test_no_token_is_refused_and_the_bearer_opens(tmp_path):
    async with client_for(app_for(tmp_path)) as c:
        assert (await initialize(c, {})).status_code == 401
        assert (await initialize(c, {"Authorization": "Bearer nope"})).status_code == 401
        ok = await initialize(c, {"Authorization": f"Bearer {TOKEN}"})
        assert ok.status_code == 200, ok.text
        meta = (await c.get("/.well-known/oauth-authorization-server")).json()
        assert meta["authorization_endpoint"].startswith(PUBLIC) and "S256" in meta["code_challenge_methods_supported"]


@pytest.mark.anyio
async def test_the_connector_flow_logs_in_with_the_token_and_keeps_its_own_state(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_auth.time, "sleep", lambda s: None)
    async with client_for(app_for(tmp_path)) as c:
        meta = (await c.get("/.well-known/oauth-authorization-server")).json()
        reg = await c.post(meta["registration_endpoint"], json={
            "client_name": "Claude", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
            "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
            "token_endpoint_auth_method": "none", "scope": "story"})
        assert reg.status_code == 201, reg.text
        cid = reg.json()["client_id"]
        verifier = secrets.token_urlsafe(40)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        auth = await c.get(meta["authorization_endpoint"], params={
            "response_type": "code", "client_id": cid, "code_challenge": challenge, "code_challenge_method": "S256",
            "redirect_uri": "https://claude.ai/api/mcp/auth_callback", "state": "xyz", "scope": "story"},
            follow_redirects=False)
        login = auth.headers["location"]
        assert login.startswith(PUBLIC + mcp_auth.LOGIN_PATH + "?pending=")
        page = await c.get(login)
        assert "showrunner" in page.text and "MCP_TOKEN" in page.text
        pending = parse_qs(urlparse(login).query)["pending"][0]
        wrong = await c.post(mcp_auth.LOGIN_PATH, data={"pending": pending, "secret": "guess"}, follow_redirects=False)
        assert wrong.status_code == 403 and "Wrong token" in wrong.text
        ok = await c.post(mcp_auth.LOGIN_PATH, data={"pending": pending, "secret": TOKEN}, follow_redirects=False)
        assert ok.status_code == 303
        code = parse_qs(urlparse(ok.headers["location"]).query)["code"][0]
        tok = await c.post(meta["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
            "client_id": cid, "code_verifier": verifier})
        assert tok.status_code == 200, tok.text
        assert (await initialize(c, {"Authorization": f"Bearer {tok.json()['access_token']}"})).status_code == 200
    state = tmp_path / "outputs" / "showrunner-mcp" / "oauth.json"
    assert cid in json.load(open(state, encoding="utf-8"))["clients"]
    assert oct(os.stat(state).st_mode)[-3:] == "600"


# ------------------------------------------------------------------ story tools (2.1)

import shutil  # noqa: E402
import subprocess  # noqa: E402

FFMPEG = shutil.which("ffmpeg") is not None


def make_story(tmp_path):
    backend = make_backend(tmp_path)
    server = S.build_server(backend)
    made = payload(call(server, "story_create", title="Été à Paris", language="fr", universe_name="Cartoon humans"))
    return backend, server, made["slug"]


def test_story_create_list_read_write_and_refusals(tmp_path):
    backend, server, slug = make_story(tmp_path)
    assert slug == "ete-a-paris"
    listed = payload(call(server, "story_list"))["stories"]
    assert [r["slug"] for r in listed] == ["ete-a-paris"] and listed[0]["language"] == "fr"
    tree = payload(call(server, "store_read", story=slug))
    assert {"path": "01-universe.md", "locked": False} in tree["files"]
    w = call(server, "store_write", story=slug, path="02-cast/ana/sheet.md", text="# Ana\n\n## Head\nAna, a pear woman\n")
    assert not w.is_error
    assert "Ana, a pear woman" in payload(call(server, "store_read", story=slug, path="02-cast/ana/sheet.md"))["text"]
    for bad in ({"path": "../escape.md"}, {"path": "/etc/passwd.md"}, {"path": "02-cast/ana/x.png"}):
        assert call(server, "store_write", story=slug, text="x", **bad).is_error
    assert "not valid JSON" in call(server, "store_write", story=slug, path="ep01/shots.json", text="{oops").content[0].text
    assert call(server, "store_read", story="../stories", path="00-brief.md").is_error
    assert call(server, "store_read", story="_stage0").is_error


def test_lock_refuses_writes_until_an_unlock_with_a_reason(tmp_path):
    backend, server, slug = make_story(tmp_path)
    call(server, "store_write", story=slug, path="02-cast/ana/sheet.md", text="v1")
    assert not call(server, "store_lock", story=slug, path="02-cast/ana/sheet.md", note="Rida: ok").is_error
    refused = call(server, "store_write", story=slug, path="02-cast/ana/sheet.md", text="v2")
    assert refused.is_error and "locked" in refused.content[0].text
    assert call(server, "store_unlock", story=slug, path="02-cast/ana/sheet.md", reason="").is_error
    assert not call(server, "store_unlock", story=slug, path="02-cast/ana/sheet.md", reason="new outfit").is_error
    assert not call(server, "store_write", story=slug, path="02-cast/ana/sheet.md", text="v2").is_error


def test_a_link_out_of_the_story_is_refused(tmp_path):
    backend, server, slug = make_story(tmp_path)
    secret = tmp_path / "secret.md"
    secret.write_text("nope")
    os.symlink(secret, tmp_path / "stories" / slug / "link.md")
    got = call(server, "store_read", story=slug, path="link.md")
    assert got.is_error and "leaves the story" in got.content[0].text


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg")
def test_view_file_shows_images_and_clips_and_file_download_returns_the_bytes(tmp_path):
    backend, server, slug = make_story(tmp_path)
    root = tmp_path / "stories" / slug
    (root / "02-cast" / "ana").mkdir(parents=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=orange:s=832x1216", "-frames:v", "1",
                    str(root / "02-cast" / "ana" / "full_body.png")], check=True, capture_output=True)
    (root / "ep01" / "clips").mkdir(parents=True)
    clip = root / "ep01" / "clips" / "s01_v1.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc=s=704x1280:r=24:d=2", "-f", "lavfi",
                    "-i", "sine=frequency=300:duration=2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", str(clip)], check=True, capture_output=True)
    img = call(server, "view_file", story=slug, path="02-cast/ana/full_body.png", max_px=256)
    assert type(img.content[0]).__name__ == "ImageContent" and img.content[0].mime_type == "image/jpeg"
    vid = call(server, "view_file", story=slug, path="ep01/clips/s01_v1.mp4")
    assert type(vid.content[0]).__name__ == "ImageContent"
    assert json.loads(vid.content[1].text)["has_audio"] is True
    assert call(server, "view_file", story=slug, path="00-brief.md").is_error
    dl = call(server, "file_download", story=slug, path="ep01/clips/s01_v1.mp4")
    res = dl.content[0].resource
    assert res.mime_type == "video/mp4" and base64.b64decode(res.blob) == clip.read_bytes()
    assert not call(server, "file_download", story=slug, path="ep01/clips/s01_v1.mp4", max_mib=0).is_error  # clamped to 1 MiB
    big = root / "ep01" / "big.mp4"
    big.write_bytes(b"0" * (2 * 1024 * 1024))
    refused = call(server, "file_download", story=slug, path="ep01/big.mp4", max_mib=1)
    assert refused.is_error and "over the 1 MiB limit" in refused.content[0].text



# ------------------------------------------------------------------ GPU jobs (2.2)

def _clip_b64(tmp_path):
    clip = tmp_path / "gen.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "testsrc=s=704x1280:r=24:d=2", "-f", "lavfi",
                    "-i", "sine=frequency=300:duration=2", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", str(clip)], check=True, capture_output=True)
    return base64.b64encode(clip.read_bytes()).decode()


def test_the_live_video_endpoint_is_refused_even_if_configured(tmp_path):
    backend = make_backend(tmp_path)
    backend.settings.endpoints["video"] = "LIVE"
    backend.settings.forbidden = {"LIVE"}
    server = S.build_server(backend)
    slug = payload(call(server, "story_create", title="T", language="en"))["slug"]
    got = call(server, "comfy_submit", story=slug, template="ltx25_i2v_speech", values={"prompt": "p", "seed": 1},
               episode=1, shot="s01")
    assert got.is_error and "live app" in got.content[0].text
    assert not [e for e in backend.log if e[0] == "run"]


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg")
def test_submit_then_fetch_a_clip_through_the_tools(tmp_path):
    backend, server, slug = make_story(tmp_path)
    kf = tmp_path / "stories" / slug / "ep01" / "keyframes"
    kf.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=orange:s=704x1280", "-frames:v", "1",
                    str(kf / "s01.png")], check=True, capture_output=True)
    sub = payload(call(server, "comfy_submit", story=slug, template="ltx25_i2v_speech",
                       values={"prompt": "Ana says: \"Salut\"", "seed": 33, "seconds": 5},
                       files={"image": "ep01/keyframes/s01.png"}, episode=1, shot="s01"))
    assert sub["kind"] == "video" and sub["job"] == "video-job1" and sub["estimate"]["cold_usd"] > sub["estimate"]["warm_usd"]
    run = [e for e in backend.log if e[0] == "run"][0]
    assert run[1:4] == ("video", "vid-ep", "kv")
    FakeEndpoint.script = {"video-job1": [{"status": "IN_QUEUE", "delayTime": 5000},
                                          {"status": "COMPLETED", "executionTime": 40000, "delayTime": 600000,
                                           "output": {"images": [{"filename": "showrunner/x_00001_.mp4", "type": "base64",
                                                                  "data": _clip_b64(tmp_path)}]}}]}
    waiting = payload(call(server, "comfy_fetch", story=slug, job="video-job1"))
    assert waiting["state"] == "IN_QUEUE" and waiting["waiting"] is True
    assert [r["job"] for r in payload(call(server, "comfy_jobs", story=slug, open_only=True))["jobs"]] == ["video-job1"]
    done = call(server, "comfy_fetch", story=slug, job="video-job1")
    assert type(done.content[0]).__name__ == "ImageContent"
    info = json.loads(done.content[1].text)
    assert info["take"] == "v1" and info["outputs"] == ["ep01/clips/s01_v1.mp4"] and info["usd"] == pytest.approx(0.0212)
    ledger = payload(call(server, "cost_ledger", story=slug, episode=1))
    assert ledger["total_usd"] == pytest.approx(0.0212) and ledger["by_kind"] == {"video": pytest.approx(0.0212)}
    assert payload(call(server, "comfy_jobs", story=slug, open_only=True)) == {"count": 0, "jobs": []}


def test_an_image_template_goes_to_the_images_endpoint_with_its_key(tmp_path):
    backend, server, slug = make_story(tmp_path)
    sub = payload(call(server, "comfy_submit", story=slug, template="t2i_flux2_klein", values={"prompt": "p", "seed": 2},
                       dest="02-cast/ana/candidates/c1"))
    assert sub["kind"] == "images" and [e[1:4] for e in backend.log if e[0] == "run"] == [("images", "img-ep", "ki")]
    refused = call(server, "comfy_submit", story=slug, template="t2i_flux2_klein", values={"prompt": "p", "seed": 2})
    assert refused.is_error and "dest" in refused.content[0].text
    assert call(server, "comfy_submit", story=slug, template="nope", values={}, dest="x").is_error


# ------------------------------------------------------------------ gates end to end (2.3)

def _flac_b64(tmp_path, name, seconds, freq):
    path = tmp_path / f"{name}.flac"
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-ar", "24000", "-ac", "1", str(path)], check=True, capture_output=True)
    return base64.b64encode(path.read_bytes()).decode()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg")
def test_a_two_speaker_shot_from_clip_to_episode_through_the_tools(tmp_path, monkeypatch):
    from showrunner import verify as V
    backend, server, slug = make_story(tmp_path)
    root = tmp_path / "stories" / slug
    for cid, name in (("ana", "Ana"), ("bo", "Bo")):
        call(server, "store_write", story=slug, path=f"02-cast/{cid}/sheet.md",
             text=f"# {name}\\n\\n## Head\\n{name}, a test character\\n\\n## Voice (en)\\nwarm\\n")
        subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "sine=frequency=200:duration=5", "-ar", "24000",
                        str(root / "02-cast" / cid / "voice_ref.wav")], check=True, capture_output=True)
    call(server, "store_write", story=slug, path="ep01/shots.json", text=json.dumps({"shots": [
        {"id": "s01", "lines": [{"speaker": "ana", "text": "C'est qui ?"}, {"speaker": "bo", "text": "Personne."}]}]}))
    (root / "ep01" / "keyframes").mkdir(parents=True)
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=orange:s=704x1280", "-frames:v", "1",
                    str(root / "ep01" / "keyframes" / "s01.png")], check=True, capture_output=True)
    # 1. the clip (fake GPU)
    job = payload(call(server, "comfy_submit", story=slug, template="ltx25_i2v_speech", values={"prompt": "p", "seed": 22},
                       files={"image": "ep01/keyframes/s01.png"}, episode=1, shot="s01"))["job"]
    FakeEndpoint.script = {job: [{"status": "COMPLETED", "executionTime": 40000, "output": {"images": [
        {"filename": "showrunner/x_00001_.mp4", "type": "base64", "data": _clip_b64(tmp_path)}]}}]}
    call(server, "comfy_fetch", story=slug, job=job)
    # 2. its check (an injected transcript: the model is not in the test)
    heard = [{"word": w, "start": a, "end": b, "prob": 1.0} for w, a, b in
             (("C'est", 0.1, 0.3), ("qui", 0.3, 0.6), ("Personne", 1.1, 1.6))]
    real_check = V.verify_take
    monkeypatch.setattr(S.verify, "verify_take", lambda clip, lines, lang: real_check(clip, lines, lang, words=heard))
    assert call(server, "vc_clip", story=slug, episode=1, shot="s01", take="v1").is_error   # no check yet
    checked = payload(call(server, "verify_take", story=slug, episode=1, shot="s01", take="v1"))
    assert checked["state"] == "ok" and [r["speaker"] for r in checked["lines"]] == ["ana", "bo"]
    # 3. the locked voices: one job per speaker
    vc = payload(call(server, "vc_clip", story=slug, episode=1, shot="s01", take="v1"))
    assert [p["speaker"] for p in vc["parts"]] == ["ana", "bo"] and vc["parts"][0]["end_s"] == pytest.approx(0.85)
    sent = [e for e in backend.log if e[0] == "run"][1:]
    assert [e[4]["vc"]["class_type"] for e in sent] == ["FL_ChatterboxVC", "FL_ChatterboxVC"]
    FakeEndpoint.script.update({vc["jobs"][0]: [{"status": "IN_QUEUE"}]})
    FakeEndpoint.script.update({vc["jobs"][1]: [{"status": "COMPLETED", "executionTime": 1000, "output": {"audio": [
        {"filename": "showrunner/b_00001_.flac", "type": "base64", "data": _flac_b64(tmp_path, "b", 1.2, 500)}]}}]})
    waiting = payload(call(server, "vc_fetch", story=slug, episode=1, shot="s01", take="v1"))
    assert waiting["waiting"] == [vc["jobs"][0]]
    FakeEndpoint.script[vc["jobs"][0]] = [{"status": "COMPLETED", "executionTime": 1000, "output": {"audio": [
        {"filename": "showrunner/a_00001_.flac", "type": "base64", "data": _flac_b64(tmp_path, "a", 0.9, 400)}]}}]
    got = call(server, "vc_fetch", story=slug, episode=1, shot="s01", take="v1")
    info = json.loads(got.content[1].text)
    assert info["take"] == "v2" and type(got.content[0]).__name__ == "ImageContent"
    v1, v2 = V.probe(str(root / "ep01/clips/s01_v1.mp4")), V.probe(str(root / "ep01/clips/s01_v2.mp4"))
    assert v2["frames"] == v1["frames"] and v2["duration_s"] == pytest.approx(v1["duration_s"], abs=0.05)
    assert json.loads(call(server, "vc_fetch", story=slug, episode=1, shot="s01", take="v1").content[1].text)["take"] == "v2"
    # 4. Rida's approval, then the episode
    assert call(server, "approve_take", story=slug, episode=1, shot="s01", take="v2", note=" ").is_error
    assert call(server, "assemble_episode", story=slug, episode=1, preset="ultrafast").is_error   # nothing approved
    assert not call(server, "approve_take", story=slug, episode=1, shot="s01", take="v2", note="Rida: ok").is_error
    ep = call(server, "assemble_episode", story=slug, episode=1, preset="ultrafast")
    assert not ep.is_error and type(ep.content[0]).__name__ == "ImageContent"
    report = json.loads(ep.content[1].text)
    assert report["segments"][0]["take"] == "v2" and report["width"] == 1080
    ledger = payload(call(server, "cost_ledger", story=slug, episode=1))
    assert ledger["by_kind"] == {"video": pytest.approx((40 + 1 + 1) * S.RATES["video"], abs=1e-4)}


def test_vc_clip_names_the_missing_locked_voices(tmp_path, monkeypatch):
    backend, server, slug = make_story(tmp_path)
    sto = S.st.Story.open(str(tmp_path / "stories" / slug))
    sto.write_bytes("ep01/clips/s01_v1.mp4", b"mp4")
    sto.add_take(1, "s01", "ep01/clips/s01_v1.mp4", verdict={"duration_s": 5.0, "lines": [
        {"speaker": "ana", "start_s": 0.0, "end_s": 2.0}]})
    got = call(server, "vc_clip", story=slug, episode=1, shot="s01", take="v1")
    assert got.is_error and "no locked voice" in got.content[0].text and "ana" in got.content[0].text


# ------------------------------------------------------------------ prompts built by the server (stage 3)

def _cast_story(tmp_path):
    backend, server, slug = make_story(tmp_path)
    call(server, "store_write", story=slug, path="01-universe.md",
         text="# Universe\n\n## Medium\nA 3D cartoon, fully computer-generated.\n")
    call(server, "store_write", story=slug, path="02-cast/ana/sheet.md",
         text="# Ana\n\n## Head\nAna, a cartoon woman with a red scarf\n\n## Voice (en)\na warm voice\n")
    call(server, "store_write", story=slug, path="03-places/cafe/plate.md", text="# Café\n\n## Setting\na café\n")
    call(server, "store_write", story=slug, path="ep01/shots.json", text=json.dumps({"shots": [
        {"id": "s01", "seconds": 5, "place": "cafe", "characters": ["ana"], "lines": [{"speaker": "ana", "text": "Salut."}]}]}))
    return backend, server, slug


def test_comfy_submit_sends_claudes_prompt_as_written_and_journals_it(tmp_path):
    """The server writes no prompt: what Claude wrote in the chat is what the GPU gets, kept in the journal."""
    backend, server, slug = _cast_story(tmp_path)
    written = "A 3D cartoon, fully computer-generated. Ana, a cartoon woman with a red scarf. A full-body reference."
    sub = payload(call(server, "comfy_submit", story=slug, template="t2i_flux2_klein",
                       values={"prompt": written, "seed": 7, "width": 832, "height": 1216},
                       dest="02-cast/ana/candidates/full_body_c1"))
    sto = S.st.Story.open(str(tmp_path / "stories" / slug))
    row = [r for r in S.jobs.journal(sto) if r["job"] == sub["job"]][0]
    assert row["prompt"] == written and row["values"]["width"] == 832
    sent = [e for e in backend.log if e[0] == "run"][0][4]
    assert written in json.dumps(sent)
    tools = {t.name for t in asyncio.run(_list_tools(server))}
    assert not {t for t in tools if t.startswith("prompt_")}            # no prompt writer on the server


async def _list_tools(server):
    async with fastmcp.Client(server) as client:
        return await client.list_tools()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg")
def test_voice_ref_from_take_locks_the_voice_of_an_approved_take(tmp_path):
    backend, server, slug = _cast_story(tmp_path)
    sto = S.st.Story.open(str(tmp_path / "stories" / slug))
    clip = sto.writable("ep00/clips/s01_v1.mp4")
    subprocess.run(["ffmpeg", "-hide_banner", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x568:r=24:d=5", "-f", "lavfi",
                    "-i", "sine=frequency=300:duration=5", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", clip], check=True, capture_output=True)
    sto.add_take(0, "s01", "ep00/clips/s01_v1.mp4", verdict={"duration_s": 5.0, "lines": [
        {"speaker": "ana", "start_s": 0.4, "end_s": 3.6}]})
    args = dict(story=slug, episode=0, shot="s01", take="v1", speaker="ana")
    assert "needs a note" in call(server, "voice_ref_from_take", note=" ", **args).content[0].text
    assert "not approved" in call(server, "voice_ref_from_take", note="Rida: this voice", **args).content[0].text
    call(server, "approve_take", story=slug, episode=0, shot="s01", take="v1", note="Rida: s33 for Ana")
    got = payload(call(server, "voice_ref_from_take", note="Rida: this voice", **args))
    assert got["path"] == "02-cast/ana/voice_ref.wav" and got["locked"] and got["speech_s"] == pytest.approx(3.2)
    assert sto.is_locked("02-cast/ana/voice_ref.wav")
    record = json.loads(payload(call(server, "store_read", story=slug, path="02-cast/ana/voice_ref.json"))["text"])
    assert record["from"]["take"] == "v1" and record["note"] == "Rida: this voice"
    again = call(server, "voice_ref_from_take", note="Rida: again", **args)
    assert again.is_error and "locked" in again.content[0].text
    nobody = call(server, "voice_ref_from_take", story=slug, episode=0, shot="s01", take="v1", speaker="ghost", note="x")
    assert nobody.is_error and "no character sheet" in nobody.content[0].text


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg")
def test_a_casting_clip_in_episode_zero_becomes_a_take(tmp_path):
    """ep00 (the casting reel of the cast step): episode 0 is a real episode, not 'no episode'."""
    backend, server, slug = _cast_story(tmp_path)
    call(server, "store_write", story=slug, path="ep00/shots.json", text=json.dumps({"shots": [
        {"id": "s01", "seconds": 5, "place": "cafe", "characters": ["ana"],
         "lines": [{"speaker": "ana", "text": "Je ne mens jamais, sauf le mardi."}]}]}))
    sto = S.st.Story.open(str(tmp_path / "stories" / slug))
    sto.write_bytes("ep00/keyframes/s01.png", b"png")
    sub = payload(call(server, "comfy_submit", story=slug, template="ltx25_i2v_speech",
                       values={"prompt": "Ana says in French: \"Je ne mens jamais, sauf le mardi.\"", "seed": 11},
                       files={"image": "ep00/keyframes/s01.png"}, episode=0, shot="s01"))
    assert (sub["episode"], sub["shot"]) == (0, "s01")
    FakeEndpoint.script = {sub["job"]: [{"status": "COMPLETED", "executionTime": 40000, "output": {"images": [
        {"filename": "showrunner/x_00001_.mp4", "type": "base64", "data": _clip_b64(tmp_path)}]}}]}
    info = json.loads(call(server, "comfy_fetch", story=slug, job=sub["job"]).content[1].text)
    assert info["take"] == "v1" and info["outputs"] == ["ep00/clips/s01_v1.mp4"]
    assert payload(call(server, "cost_ledger", story=slug, episode=0))["total_usd"] > 0

# Plan — credential headers never follow a redirect to another origin

## Problem and root cause
`clipping/providers/transport.py` `urllib_transport` hands a `Request` to `urllib.request.urlopen`, which uses the
default opener. On Python 3.12.3, `HTTPRedirectHandler.redirect_request` copies every header except
`Content-Length`/`Content-Type` onto the redirected request, whatever its host. So a 30x to another origin would carry:
- fal's `Authorization: Key …`;
- Gemini's `x-goog-api-key`;
- the Cloudflare and pollinations bearer tokens.

fal's `status_url`/`response_url` come from fal's answer. Phase 6's Veo `_download` checks the host only before it
sends. Neither can see a redirect that happens inside `urlopen`.

## Map (EXPLORE, one Sonnet Explore agent)
- Transport callers that send a credential:
  - `images.py`: Cloudflare, pollinations, Gemini, fal submit/poll/fetch. fal's `_download` already sends `{}`.
  - `tts.py`: Gemini.
  - phase 6's `video.py`: Veo, fal video.
- No credentials: `local_ollama.py`, `local_comfyui.py`.
- The only test of the real transport is `tests/test_provider_http.py::test_the_urllib_transport_maps_errors`. It
  patches `transport.urllib.request.urlopen`.
- Every adapter test uses a `FakeTransport` above `urlopen`, so no test sees a redirect today.
- The repo has no custom opener, `install_opener` or proxy handling. No proxy env is set here.
- Same bug class outside this scope, left as follow-ups:
  - `stt.py` `_post_multipart` (Groq/Mistral bearer) calls `urlopen` directly;
  - `studio/broll.py` does the same for the Pexels key.

## Stage 1 — a credential-safe redirect handler in the transport (the only code stage; the RISKIEST)
- **Goal:** a module-level opener, built from an `HTTPRedirectHandler` subclass, carries the redirects:
  - its `redirect_request` calls the stdlib's, then removes `Authorization`, `Proxy-Authorization`,
    `x-goog-api-key` and `x-api-key` (case-insensitive; constant `CREDENTIAL_HEADERS`) when the new URL's
    (scheme, host, default-normalised port) differs from the previous hop's;
  - a same-origin redirect keeps them;
  - once dropped, a header never comes back later in the chain.
  - `urllib_transport` calls `_OPENER.open(request, timeout=timeout)` instead of `urlopen`. The signature,
    `Response` and the whole except-mapping stay unchanged.
- **Files:**
  - `clipping/providers/transport.py`;
  - new `tests/test_transport_redirects.py`;
  - `tests/test_provider_http.py`: only the four `monkeypatch.setattr` targets move from `urllib.request.urlopen` to
    `transport._OPENER.open`, because the transport no longer goes through the global `urlopen`. The fakes and all
    four assertions stay byte-identical.
- **Tests:** two local `ThreadingHTTPServer`s on 127.0.0.1, on different ports.
  - (1) A answers 302 to B. B never receives any of the four credential headers, but a non-credential header still
    arrives. It must fail before the fix.
  - (2) A answers 302 to its own `/end`. The credential headers arrive. This is the working-path guard: it passes
    before and after the fix, and it is shown to catch an "always drop" mutation.
- **Verified by:** the new file + `test_provider_http.py` + `test_image_adapters.py` + `test_generation_chain.py` +
  `test_generation_chain_api.py`, in both the local and the `/tmp/cilibs` environments.
- **Rollback:** `git revert` of the stage commit.
- **Regression contract at risk:** RC-T1 (the transport contract) and RC-T2 (the adapters).

## Stage 2 — artifacts
- DEC-195 and A-097 (the next free ids on `main`; phase 6 uses DEC-200+ / A-100+).
- Action-log lines and the CHECKPOINT close-out.
- Rollback: revert the commit.

## Rejected alternatives
- `Request.add_unredirected_header` for the credentials. It drops them on **every** redirect, same-origin
  included. Whether Google's or fal's API hosts redirect within their own origin (Veo's file `uri`, for example) is
  outside behaviour the code cannot show. The human's acceptance test also requires a same-origin redirect to keep
  the header.
- `urllib.request.install_opener` with the safe handler. It is a process-wide side effect on every `urlopen` user
  (stt, broll, studio), which is out of scope and invisible at the call site.
- Host checks in each adapter, like phase 6's Veo `_download`. They run once before the request and cannot see a
  redirect inside `urlopen`.

## DECISIONS check
- DEC-100 (stdlib urllib transports) and DEC-012 (stdlib only; errors classified by class name): kept. The fix adds
  no dependency and no exception class.
- No DEC covers redirects. Checked, no conflicts.
- Phase 6's branch doesn't touch `transport.py` or `test_provider_http.py`.

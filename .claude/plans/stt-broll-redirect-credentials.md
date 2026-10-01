# Plan — stt's upload and the Pexels search open through the credential-safe opener

## Problem and root cause
DEC-195 made `transport.urllib_transport` open through `transport._OPENER`. That opener's
`_CredentialSafeRedirectHandler` drops the `CREDENTIAL_HEADERS` when a redirect changes scheme, host or port. Two
callers build their own `Request` and hand it to `urllib.request.urlopen`, so they never reach that handler:
- `clipping/providers/stt.py` `_post_multipart` (line 275). Groq and Mistral reach it through `_transcribe_with` and
  send `Authorization: Bearer <key>`.
- `clipping/studio/broll.py` `download_pexels_broll` (line 94). It sends the Pexels key as `Authorization` to
  `https://api.pexels.com/videos/search`.

The stdlib's default `HTTPRedirectHandler` copies every non-content header to any host. So a 30x from either API to
another origin would carry the key there. On a 302, a POST becomes a GET, and the `Authorization` header still
goes with it.

## Map (EXPLORE: one Haiku Explore agent, plus inline reads of the two named functions)
- `main` `0ca00b8` already contains ded664f (DEC-195), so this worktree's branch `Feature/musing-lamport-e6a02a` is
  based on it.
- stt:
  - `_post_multipart` is the only `urlopen` call in `clipping/providers/` outside the transport.
  - Its tests never reach the network: `test_stt_stitching.py` injects `post=`; `test_transcript_dispatch.py`,
    `test_cancel.py` and `test_story_assets_step.py` patch `stt.transcribe`.
  - No test patches `urllib.request.urlopen`, so no patch target moves (unlike A-098).
  - `stt` imports in both environments.
- broll:
  - No test calls `download_pexels_broll`. The only caller is `studio/core.py:246`.
  - The module imports cv2, mediapipe, numpy and PIL at module scope, and `studio/__init__` → `core` → `effects`
    also pulls in `yt_dlp`. It imports in neither environment.
  - `tests/conftest.py::render_stack_stubbed` is the repo's own way to load a studio module in the pytest-only env:
    it mocks the heavy modules and discards the stubbed `clipping.studio` afterwards.
  - `test_studio_package.py`'s SIBLINGS and import-cycle guards read only level-1 `from . import x`. A level-2
    `from ..providers import transport` does not touch them.
  - `clipping.providers` imports nothing from `clipping.studio` (no cycle).
- Neither module is loaded by path any more (`test_no_module_is_loaded_by_path_any_more`). No code calls
  `install_opener`.
- The second `urlopen` in broll (line 137, the CDN download) sends only `User-Agent`, so it has nothing to leak.
  It stays as it is.
- Behaviour kept identical:
  - `_OPENER` is `build_opener(_CredentialSafeRedirectHandler)`: the same default handlers as `urlopen`'s opener,
    with only the redirect handler replaced.
  - `_OPENER.open(req)` with no timeout uses `socket._GLOBAL_DEFAULT_TIMEOUT`, as `urlopen(req)` does.
  - An `HTTPError` is still raised for a 4xx/5xx, so stt's `SttError` mapping and broll's `except Exception` see
    the same exceptions.

## Stage 0 — checkpoint
- The tree is clean at `0ca00b8` (== `main`). CHECKPOINT.md records the hash and the baseline. The plan file is
  committed.
- **Tier-1 baseline** (scope from the chat: the existing tests of the touched modules):
  - Files: `test_stt_stitching.py`, `test_transcript_dispatch.py`, `test_cancel.py`, `test_story_assets_step.py`,
    `test_studio_package.py`, `test_render_temp_cleanup.py`.
  - Local: 117 passed / 1 skipped. CI env: 117 passed / 1 skipped.
  - The skip is `test_the_real_package_imports_where_the_render_stack_exists` (no cv2).

## Stage 1 — stt's multipart POST opens through `transport._OPENER`
- **Goal:**
  - Add `from . import transport` to `stt.py`.
  - In `_post_multipart`, replace `urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT)` with
    `transport._OPENER.open(request, timeout=REQUEST_TIMEOUT)`. Nothing else changes: the same return value
    (the parsed JSON) and the same `HTTPError` → `SttError` mapping.
- **Files:** `clipping/providers/stt.py`; new `tests/test_stt_broll_redirects.py` (its server helper and the stt
  test).
- **Test** (fail-first, run against the unmodified `stt.py`):
  - Two `ThreadingHTTPServer`s on 127.0.0.1, on different ports.
  - `_post_multipart` goes to A with key `test-groq`. A answers the POST with a 302 to B's `/moved`. B answers
    `{"text": "ok"}`.
  - The test asserts that:
    - the result is `{"text": "ok"}`;
    - A got `Authorization: Bearer test-groq`;
    - B got exactly one `GET /moved`, and no `authorization` header.
  - `REQUEST_TIMEOUT` is patched to 5 s, so a hang fails fast.
- **Verified by:** the new file plus the four stt test files, in both environments.
- **Risk:** low. It is one call, and the opener is the one DEC-195 already proved (TLS included).
- **Rollback:** `git revert` of the stage commit.
- **Regression contract at risk:** RC-SB1.

## Stage 2 — the Pexels search opens through `transport._OPENER` (the RISKIEST stage)
- **Goal:**
  - Add `from ..providers import transport` to `broll.py`, as one line after `from . import utils`. It is kept apart
    from the stdlib/third-party block, which the unmerged `Feature/eager-chaplygin-e0a0a3` rewrites.
  - Add a module constant `PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"` next to
    `USED_PEXELS_IDS`. `search_url` becomes `f"{PEXELS_VIDEO_SEARCH_URL}?{params}"`: the same string, and the only
    seam a local test can point at.
  - Replace `urllib.request.urlopen(req)` in the search with `transport._OPENER.open(req)`.
  - Return values and every `except`/print branch stay as they are. The CDN download is not changed.
- **Files:** `clipping/studio/broll.py`; `tests/test_stt_broll_redirects.py` (the broll test).
- **Test** (fail-first: the constant goes in first, then the test is shown failing with `urlopen` still in place,
  then the switch):
  - A fixture on `render_stack_stubbed` imports `clipping.studio.broll` and resets `USED_PEXELS_IDS`.
  - `PEXELS_VIDEO_SEARCH_URL` points at A. A answers 302 to B's `/moved`. B answers a one-video JSON whose mp4
    link is B's `/clip.mp4`, and serves that clip.
  - The test asserts that:
    - `download_pexels_broll` returns `True` and the file holds B's bytes;
    - A got `Authorization: test-pexels`;
    - B got exactly `GET /moved` and `GET /clip.mp4`, neither with `authorization`.
- **Verified by:** the new file plus `test_studio_package.py` and `test_render_temp_cleanup.py` (they load the
  studio package, and broll with it, under the same stubs), in both environments.
- **Why riskiest:**
  - It adds the studio package's first import from `clipping.providers`. `studio/__init__` → `core` → `broll` then
    loads `clipping.providers`' `__init__` (registry, errors) in the render path.
  - It adds a module constant to a file another branch is editing.
- **Rollback:** `git revert` of the stage commit.
- **Regression contract at risk:** RC-SB2, RC-SB3.

## Stage 3 — artifacts
- DEC-196: the decision and its consequence. A-099: Groq, Mistral and Pexels need no credential on a cross-origin
  redirect (A-097's twin for these three). Action-log lines. The CHECKPOINT close-out.
- **Rollback:** revert the commit.

## Regression contract
| ID | Must keep working | Proven by |
|---|---|---|
| RC-SB1 | Hosted STT (Groq/Mistral) still posts, parses and maps an HTTP error to `SttError` | `test_stt_stitching.py`, `test_transcript_dispatch.py`, `test_cancel.py`, `test_story_assets_step.py` unedited and green; the new stt test's 200 path |
| RC-SB2 | The studio package still loads, binds its siblings and has no import cycle | `test_studio_package.py`, `test_render_temp_cleanup.py` unedited and green |
| RC-SB3 | `download_pexels_broll` still searches, picks, downloads and returns `True`/`False` as before | the new broll test's full path (search → pick → download → `True`); the error branches are unchanged code (UNVERIFIED by a test, per DEC-192's essential-only scope) |

## Tier 2
- There is no dashboard or API surface, and no E2E spec covers either call.
- Option: a keyless live probe through the new code, with no credential sent:
  - `_post_multipart` to Groq's transcription URL with an empty key → the expected `SttError` "HTTP 401";
  - `download_pexels_broll` with a dummy key → the expected `False` after Pexels' 401.
- The alternative is deferral. The human chooses.

## Rejected alternatives
- **Route both calls through `transport.urllib_transport`:** it turns an `HTTPError` into a `Response` and maps
  connection errors to `APIConnectionError`. stt's `SttError` detail and both functions' error handling would have
  to be rewritten, which is what the task forbids.
- **A new public helper in `transport.py`** (for example `open_request(req, timeout)`): it would only forward to
  `_OPENER.open`. That is one more name to keep, for two callers, and it would be a third file in the diff.
  `test_provider_http.py` already treats `_OPENER.open` as the seam.
- **Point the broll test at A by wrapping `_OPENER.open` to rewrite the URL:** before the fix, broll does not go
  through `_OPENER`, so the fail-first run would send a real request to api.pexels.com.
- **Own sys.modules stubs in the broll test:** they duplicate `render_stack_stubbed`, which already handles the
  cleanup both ways.
- **`add_unredirected_header` on the two requests:** it drops the key on same-origin redirects too (the reason
  DEC-195 rejected it), and it would be a second mechanism next to the opener.

## DECISIONS check
- DEC-195: this plan extends it to the two callers it named as follow-ups. No conflict.
- DEC-012: stdlib-only providers and tests; nothing new is imported.
- DEC-192: one fail-first test per fix.
- DEC-176: Tier-1 in both environments; the scope comes from the chat.
- Checked, no conflicts.

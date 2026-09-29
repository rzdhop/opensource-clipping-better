# Auth becomes opt-in: no token unless `API_TOKEN` is set

Status: DRAFT for approval (2026-09-29). This is a separate FULL task that runs **before AI Story phase 5**.
Base: `origin/main` `b60938e`. Next free ids: **DEC-173, A-080** (phase 5 then starts at DEC-174 / A-081).

## Context

The human, 2026-09-29: "remove all access restrictions to the app, it's only local or via tailscale". Answers
taken:
- **Auth off by default, with an opt-in token.** A token is required only when `API_TOKEN` is set.
- **This is its own task**, done before phase 5.

**Today** (auth map, `origin/main`):
- **A token always exists.** `web/api/auth.py` `load_or_create_token` (:54-88) reads `$API_TOKEN`, then the stored
  `data/api_token`, and otherwise generates one and stores it 0600.
- **Every router is gated.** `require_token` (:437) sits on files, hardware, jobs, settings, stories and shutdown.
- **The VPS runs tokenless** only through a gitignored override setting `DISABLE_AUTH=1` (DEC-092/105). Even so,
  `announce` (:142) still writes a token file.

**What the flip must not break**, because two paths are public:
- the Caddy `domain` profile (`docker-compose.yml:118-134`, `deploy/Caddyfile`), which serves public 80/443;
- the Kaggle notebook's ngrok URL (`notebooks/kaggle-studio-server.ipynb`, where `API_TOKEN` is only an optional
  secret).

If the default flipped naively, both would be open to the internet. That includes `POST /api/shutdown` and
`PUT /api/settings` (provider keys, paid spending).

**Two traps:**
- `token_is_valid` never accepts an empty value (:111-115), so "no token" must short-circuit inside
  `require_token`. Passing an empty token would turn every route into a 401.
- The signing keys use `str(token or current_token())` (:190, :353). With no token they would sign with a
  publicly known key, so signing must be skipped when auth is off.

## The rule (becomes DEC-173)

1. **Auth is on if and only if `API_TOKEN` is non-empty** after trimming.
   - `data/api_token` is **no longer read or created**. An old file is left on disk, and the banner names it:
     "set API_TOKEN to its value to keep token auth".
   - `DISABLE_AUTH=1` still forces auth off, for compatibility with the VPS override and about 14 test fixtures.
2. **With auth off:**
   - `require_token` lets every request through.
   - `media_url` / `story_media_url` return the **plain path**, with no `exp`/`sig`.
   - The startup banner says loudly that the API is open, and to whom (the bound address).
3. **Public paths never start open.**
   - The backend gets `DOMAIN=${DOMAIN:-}` from compose and **refuses to start** when `DOMAIN` is set without
     `API_TOKEN`.
   - The Kaggle notebook **generates** a token (`secrets.token_urlsafe(32)`) when no `API_TOKEN` secret exists,
     and prints it, as it does today.
   - The Funnel section of `docs/deploy-tailscale.md` says to set `API_TOKEN`.
4. **Cross-site guard, open mode only.** A state-changing request (POST/PUT/PATCH/DELETE) carrying
   `Sec-Fetch-Site: cross-site` gets a 403.
   - This is invisible to the dashboard (same-origin) and to non-browser clients (no header).
   - It blocks any website open in the same browser from triggering shutdown, uploads or settings writes.
   - It's the one restriction kept, and it only blocks *other sites*, never the user. **The human may veto it.**
5. **Token-on deployments behave exactly as today:** header, clip signature, story signature, 401 (not 422).

## Stages

Tier-1 after every stage: `python -m pytest -p no:warnings` (never `-q`), the CI env, `compileall`, and
`npm run build` to a scratch outDir. Fail-first is shown for every behaviour test. Commits use explicit paths
and carry no trailers.

### Stage 0 — checkpoint + baseline [inline]
- **Goal.** A clean tree at a known hash on branch `fix/auth-opt-in`. CHECKPOINT in-progress header with that hash
  and the regression contract below. Tier-1 baseline (phase 4 close: local 5643/1, CI 4936/677).
- **Rollback:** delete the branch.

### Stage 1 — opt-in auth in the backend (**RISKIEST**) [Opus: auth]
- **Goal.** The rule above, points 1–4, in `web/api/auth.py` and `web/api/app.py`:
  - an `auth_enabled()` single source;
  - the `require_token` short-circuit before any header or signature check;
  - signing skipped when off;
  - `announce` without file creation, plus the stale-file notice;
  - the `DOMAIN` start refusal;
  - the cross-site guard as a small middleware, active only when auth is off.
- **Files:** `web/api/auth.py`, `web/api/app.py`, `tests/test_auth_token.py`, new `tests/test_auth_opt_in.py`,
  `tests/test_generation_chain_api.py`.
- **Tests that change on purpose** (each named in the log with its reason):
  - `test_auth_token.py:93/99/109/115/127`: token generation and persistence become "no file is read or created".
  - `test_generation_chain_api.py:293`: it tests signed-URL refusal, which only exists with auth on, so it now sets
    `API_TOKEN`.
- **Kept unedited:**
  - `test_empty_never_matches…` :43;
  - `test_auth_can_be_disabled_only_explicitly` :157;
  - the compose guards :165/:182/:243;
  - `test_every_router_requires_a_token` :209 and `test_the_shutdown_endpoint_is_protected` :218 (the dependency
    stays on every router);
  - `test_the_token_never_travels_in_a_query_string` :249;
  - every `API_TOKEN`-set test (the client fixture :263, the 401-not-422 test :310, signatures :525-602);
  - `test_story_media_serving.py`, including its branch-order pin :232.
- **Risk:** high.
  - A wrong short-circuit would either lock everyone out (401) or open a token-on server.
  - Signed URLs must not break for token-on deployments.
- **Verify:**
  - New tests: no `API_TOKEN` → 200 on every router, no `data/api_token` written, media URLs unsigned and served.
  - `API_TOKEN` set → 401 without the header, 200 with it, signatures accepted and scoped as before.
  - A stale `data/api_token` → ignored and named in the banner.
  - `DOMAIN` without a token → start refused, with the message.
  - Cross-site POST open → 403; same-origin or no header → allowed; with auth on, the guard is inactive.
- **Rollback:** revert the commit.
- **Regression items at risk:** RC-U1, RC-U2, RC-A5.

### Stage 2 — exposure paths, notebook, docs [Sonnet]
- **Goal.**
  - `docker-compose.yml` passes `DOMAIN` to the backend and rewords its comments (:15, :50-52).
  - `deploy/Caddyfile` comment: the token is required and the backend refuses to start without it.
  - The Kaggle notebook generates a token when the secret is absent.
  - `.env.example` (:172-174), `README.md` (:207-212), `README_ID.md` (:137), `docs/deploy-tailscale.md`
    (:35-40, :56-87), `docs/api.md` and `web/dashboard/src/pages/Login.jsx` (:48) copy say "auth is opt-in: set
    API_TOKEN".
  - `CHANGELOG.md` gets a **Security** entry: the default changed, and an existing `data/api_token` is no longer
    used.
- **Tests:**
  - `tests/test_notebooks.py` gains "the server always has a token behind ngrok".
  - `tests/test_static_studio_retired.py:41` is re-pinned from the `data/api_token` string to the `API_TOKEN`
    wording (named).
- **Risk:** low.
- **Verify:** Tier-1; notebook JSON still parses; branding guard green.
- **Rollback:** revert.
- **Regression items at risk:** RC-U4.

### Stage 3 — deploy + Tier-2 on the VPS (me; the human confirms on the phone)
- **Deploy.** Fast-forward `main` at 0 running jobs, then `docker compose rm -sfv backend && docker compose up -d
  --build backend`.
- **Remove the now-redundant gitignored override** (`DISABLE_AUTH=1`) so the VPS runs the new default. The file is
  moved to a backup, not deleted.
- **Checks:**
  - (a) The phone over the tailnet opens the dashboard with no sign-in.
  - (b) A clip plays and range-seeks (RC-P1), and the FR episode's Preview plays from the unsigned URL.
  - (c) The banner in the container log says "open".
  - (d) `curl` from the VPS with `Sec-Fetch-Site: cross-site` on a harmless POST gets 403. Without the header it
    gets 200.
  - (e) A scratch server on another port with `API_TOKEN` set gets 401, then 200 with the header, and the
    dashboard shows sign-in.
  - (f) A scratch run with `DOMAIN` set and no token refuses to start.
- **Human:** acknowledges.
- **Rollback:** restore the override and redeploy the previous hash.

### Stage 4 — decisions + artifacts [inline]
- **DEC-173:** auth is opt-in, with the rule above. It **supersedes** DEC-037's default and DEC-092's override
  need, and keeps DEC-048/163's mechanics for token-on deployments. It records the accepted risk: a third-party
  install that relied on the generated token becomes open on upgrade, with a banner and a CHANGELOG note.
- **A-080:** "no public deployment besides Caddy-domain and Kaggle-ngrok relies on the generated token",
  UNCONFIRMED.
- CHECKPOINT close-out, VISION note, and the log.

## Regression contract

| ID | Must keep working | Proven by |
|---|---|---|
| RC-U1 | With `API_TOKEN` set, auth behaves exactly as before (header, clip/story signatures, 401 not 422) | `test_auth_token.py` client-fixture tests + `test_story_media_serving.py` unedited |
| RC-U2 | With no token, every page, clip and episode video works unsigned | new `test_auth_opt_in.py` + Tier-2 (a)(b) |
| RC-U3 | No `DISABLE_AUTH` in committed compose files; the backend port is bound to loopback | `test_auth_token.py:165/182/243` unedited |
| RC-U4 | The public paths never start open (Caddy `DOMAIN`; Kaggle ngrok) | new tests + Tier-2 (f) |
| RC-A5 | A story signature opens exactly one file (token on) | stage-12 phase-4 tests unedited |

## Rejected alternatives
- **Delete auth entirely:** the human chose opt-in. Deleting it would make the Kaggle/ngrok and Caddy paths public
  and open.
- **Treat an existing `data/api_token` as "auth on":** it would keep the login on the human's local clones,
  against the request, and hide the rule behind the presence of a file.
- **A random per-process signing key when auth is off:** signatures are pointless when the gate is open. Plain
  paths are simpler and can't be forged into anything.

## DECISIONS check
- **Supersedes:** DEC-037's "a token always exists" and DEC-092's override need.
- **Keeps:** DEC-048, DEC-113, DEC-163 (token-on).
- **Satisfies:** DEC-105's "revisit before leaving a trusted tailnet", via the `DOMAIN` refusal.
- No other conflicts.

## Riskiest stage
**Stage 1.** A wrong short-circuit either locks everyone out or opens a token-on server; both directions are
tested.

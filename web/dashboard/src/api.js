const API_BASE = '/api'

/**
 * The API token, kept in localStorage.
 *
 * Every request carries it in a header rather than a query parameter: a token
 * in a URL ends up in access logs, browser history and any Referer the page
 * sends. That choice is also why the job stream below is read with fetch
 * instead of EventSource -- EventSource cannot send headers at all.
 *
 * The media URLs on a clip are the apparent exception and are not one. A
 * <video src> and an <a href download> are requests the BROWSER makes, so no
 * amount of JS can attach a header to them -- which is exactly why the player
 * used to show nothing and the Download button used to save a .json. Those URLs
 * carry ?exp=&sig=, which is an HMAC over ONE (job, file, expiry) triple keyed
 * by a value derived from the token. It opens one file, it expires, and it
 * cannot be turned back into the token. The credential itself still never
 * appears in a URL, and no query parameter in this file carries it -- a guard
 * test asserts exactly that, and rejected an earlier draft of this very comment
 * for spelling out the parameter name it forbids.
 */
const TOKEN_KEY = 'rzc_token'

export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''   // private window, or site data blocked
  }
}

export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* nothing to do: the request below will 401 and the UI will ask again */
  }
}

export function clearToken() {
  setToken('')
}

function authHeaders(extra) {
  const token = getToken()
  return {
    ...(extra || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  }
}

/** Thrown on 401 so callers can route to the login screen. */
export class UnauthorizedError extends Error {
  constructor() {
    super('Missing or invalid API token')
    this.name = 'UnauthorizedError'
  }
}

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: authHeaders(options.headers),
  })
  if (res.status === 401) {
    clearToken()
    throw new UnauthorizedError()
  }
  return res
}

/** Verify a token against the API without storing it first. */
export async function checkToken(token) {
  const res = await fetch(`${API_BASE}/settings`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  return res.ok
}

export async function fetchJobs() {
  const res = await request('/jobs')
  if (!res.ok) throw new Error('Failed to fetch jobs')
  return res.json()
}

export async function fetchJob(jobId) {
  const res = await request(`/jobs/${jobId}`)
  if (!res.ok) throw new Error('Job not found')
  return res.json()
}

export async function createJob(payload) {
  const res = await request('/jobs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'Failed to create job')
  }
  return res.json()
}

/**
 * A response's `detail` as `{message, errors}`. `detail` is either a plain
 * string (most routes) or `{message, errors}` (the AI Story validation
 * routes, e.g. a story that would not pass its schema) -- `errors` is null
 * for the former.
 */
async function parseDetail(res, fallback) {
  try {
    const body = await res.json()
    const detail = body && body.detail
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      return {
        message: detail.message != null ? String(detail.message) : fallback,
        errors: Array.isArray(detail.errors) ? detail.errors.map(String) : null,
      }
    }
    if (detail != null) return { message: String(detail), errors: null }
  } catch {}
  return { message: fallback, errors: null }
}

/** The API's own explanation of a refusal (409, 429, ...), else *fallback*.
 * A structured `{message, errors}` detail collapses to its message string
 * here, so every existing caller keeps getting the same string it always
 * has. */
async function detailOf(res, fallback) {
  return (await parseDetail(res, fallback)).message
}

/**
 * Thrown by the AI Story functions below: `message` (always a string, ready
 * to display), an optional `errors` list (the story schema's per-field
 * complaints, when the refusal had any), and the response's `status`.
 */
export class ApiError extends Error {
  constructor(message, { errors = null, status = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.errors = errors
    this.status = status
  }
}

/** Build the `ApiError` for a failed response, from the same parsing `detailOf` uses. */
async function apiError(res, fallback) {
  const { message, errors } = await parseDetail(res, fallback)
  return new ApiError(message, { errors, status: res.status })
}

/**
 * Stop a queued or running job. It stops at its next step; a provider request
 * already in flight can take a few minutes to return. Its files are kept.
 */
export async function cancelJob(jobId) {
  const res = await request(`/jobs/${jobId}/cancel`, { method: 'POST' })
  if (!res.ok) throw new Error(await detailOf(res, 'Failed to cancel the job'))
  return res.json()
}

/** Delete a job and its files (a running one is cancelled first). */
export async function deleteJob(jobId) {
  const res = await request(`/jobs/${jobId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(await detailOf(res, 'Failed to delete job'))
  return res.json()
}

/**
 * Upload a file, reporting progress as it goes.
 *
 * Uses XMLHttpRequest rather than fetch: fetch cannot report *upload*
 * progress at all, which is why the onProgress argument here used to be
 * accepted and then silently ignored. Videos are up to 2GB, so a spinner
 * with no numbers is close to useless.
 *
 * onProgress receives { loaded, total, percent, done }. `total` is 0 and
 * `percent` null when the browser reports the length as not computable.
 * Resolves and rejects exactly as the old fetch version did, so callers
 * that ignore progress keep working unchanged.
 */
export function uploadVideo(file, onProgress) {
  return new Promise((resolve, reject) => {
    const formData = new FormData()
    formData.append('file', file)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}/upload`)
    const token = getToken()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)

    xhr.upload.addEventListener('progress', (event) => {
      if (!onProgress) return
      const total = event.lengthComputable ? event.total : 0
      onProgress({
        loaded: event.loaded,
        total,
        percent: total ? (event.loaded / total) * 100 : null,
        done: false,
      })
    })

    // The bytes are all sent, but the backend still has to finish writing
    // them to disk. Without this the bar sticks at 99% with no explanation.
    xhr.upload.addEventListener('load', () => {
      if (onProgress) {
        onProgress({ loaded: file.size, total: file.size, percent: 100, done: true })
      }
    })

    xhr.addEventListener('load', () => {
      let body = {}
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        body = {}
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body)
        return
      }
      // Always surface the status. A bare "Upload failed" is undiagnosable,
      // and the interesting failures for a large file (502/504 from the dev
      // proxy, 413 from the backend) are told apart by the status alone.
      if (body.detail) {
        reject(new Error(`${body.detail} (HTTP ${xhr.status})`))
      } else if (xhr.status === 502 || xhr.status === 504) {
        reject(new Error(
          `Upload failed: the dev proxy gave up (HTTP ${xhr.status}). The file ` +
          `probably took longer than the proxy's request timeout. Check ` +
          `"docker compose logs frontend".`
        ))
      } else {
        reject(new Error(`Upload failed (HTTP ${xhr.status || 'no response'})`))
      }
    })

    // Fires on a connection reset or a timeout that closed the socket. The
    // request never produced a status, so there is nothing more specific to
    // report -- but say where to look, since the backend will have logged
    // nothing at all in this case.
    xhr.addEventListener('error', () => reject(new Error(
      'Upload failed: the connection dropped before the server replied. ' +
      'For a large file this is usually the dev proxy timing out -- check ' +
      '"docker compose logs frontend".'
    )))
    xhr.addEventListener('timeout', () => reject(new Error('Upload failed: timed out')))
    xhr.addEventListener('abort', () => reject(new Error('Upload cancelled')))

    xhr.send(formData)
  })
}

export async function fetchSettings() {
  const res = await request('/settings')
  if (!res.ok) throw new Error('Failed to fetch settings')
  return res.json()
}

export async function updateSettings(payload) {
  const res = await request('/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error('Failed to update settings')
  return res.json()
}

/**
 * Ping every link of the provider chain and report each one. Can take a couple
 * of minutes: NVIDIA's free tier queues, and its probe is allowed 120s.
 */
export async function testChain(payload = {}) {
  const res = await request('/settings/test-chain', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'The chain test failed')
  }
  return res.json()
}

/**
 * Ask each keyed hosted video link's provider whether its key is accepted and
 * its model is live (fal pricing, Gemini models.get): free, nothing generated.
 * `{results: [{label, provider, model, status, text, endpoint, price}], verdict, message}`.
 */
export async function checkVideoKeys() {
  const res = await request('/settings/check-video-keys', { method: 'POST' })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'The video key check failed')
  }
  return res.json()
}

/**
 * Run a generation chain's free and local links and report the paid ones;
 * `link` names one link to run (the only way a paid link is called, once).
 */
export async function testGenerationChain(payload) {
  const res = await request('/settings/test-generation-chain', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({}))
    throw new Error(err.detail || 'The generation chain test failed')
  }
  return res.json()
}

export async function fetchHardware(refresh = false) {
  const res = await request(refresh ? '/hardware?refresh=1' : '/hardware')
  if (!res.ok) throw new Error('Failed to fetch the hardware profile')
  return res.json()
}

export async function fetchHealth() {
  // Public: no token needed, so the login screen can show system status.
  const res = await fetch(`${API_BASE}/health`)
  if (!res.ok) throw new Error('Health check failed')
  return res.json()
}

// `onStatus` reports 'live' | 'closed'. Without it the UI cannot tell a job
// that is quiet from a stream that died, which are the two cases a user staring
// at an unmoving progress bar most needs told apart.
//
// Read with fetch + ReadableStream rather than EventSource. EventSource cannot
// send headers, so using it would have meant putting the API token in the query
// string -- and from there into access logs, browser history and any Referer
// the page sends. The returned object keeps EventSource's `close()` so callers
// do not change.
export function createSSEConnection(jobId, onMessage, onStatus) {
  const controller = new AbortController()
  let closed = false

  const close = () => {
    if (closed) return
    closed = true
    controller.abort()
  }

  const finish = () => {
    if (closed) return
    close()
    if (onStatus) onStatus('closed')
  }

  ;(async () => {
    try {
      const res = await fetch(`${API_BASE}/jobs/${jobId}/status`, {
        headers: authHeaders({ Accept: 'text/event-stream' }),
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        if (res.status === 401) clearToken()
        finish()
        return
      }
      if (onStatus) onStatus('live')

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (!closed) {
        const { value, done } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE frames are separated by a blank line; a frame can hold several
        // `data:` lines, which are joined with newlines before parsing.
        let split
        while ((split = buffer.indexOf('\n\n')) !== -1) {
          const frame = buffer.slice(0, split)
          buffer = buffer.slice(split + 2)
          const payload = frame
            .split('\n')
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.slice(5).trimStart())
            .join('\n')
          if (!payload) continue
          try {
            onMessage(JSON.parse(payload))
          } catch (e) {
            console.error('SSE parse error:', e)
          }
        }
      }
      finish()
    } catch (e) {
      if (e.name !== 'AbortError') console.error('SSE connection error:', e)
      finish()
    }
  })()

  return { close }
}

// ---------------------------------------------------------------------------
// AI Story (web/api/routes/stories.py). Every call below goes through
// `request()`, so it carries the bearer header and never a token in the URL,
// same as every other function in this file.
// ---------------------------------------------------------------------------

/** `{"stories": [index entries]}`, most recently updated first. */
export async function fetchStories() {
  const res = await request('/stories')
  if (!res.ok) throw await apiError(res, 'Failed to fetch stories')
  return res.json()
}

/**
 * What a story created now would get: `{profile, quality, missing_keys,
 * allow_paid}` -- the quality preset (v2, every shot animated) when
 * Settings hold FAL_KEY, else the story defaults. Not cached: adding a key
 * in Settings must show on the next visit to the form.
 */
export async function fetchNewStoryProfile() {
  const res = await request('/stories/new-profile')
  if (!res.ok) throw await apiError(res, 'Failed to fetch the new-story profile')
  return res.json()
}

// Several places on a story page (the picker, the concept filter chips, each
// concept card's style fit, the wizard header, the StoriesList badge) all
// want the same seven shipped templates, which never change while the server
// is running -- so the fetch is cached at module scope for the life of the
// page instead of being repeated once per component. A failed fetch clears
// the cache so a later call can retry.
let stylesCache = null

/** The seven shipped style templates, for the style step's picker. */
export async function fetchStyles() {
  if (!stylesCache) {
    stylesCache = (async () => {
      const res = await request('/stories/styles')
      if (!res.ok) throw await apiError(res, 'Failed to fetch styles')
      return res.json()
    })().catch((err) => {
      stylesCache = null
      throw err
    })
  }
  return stylesCache
}

/**
 * A style template's display name -- English, falling back to the id itself
 * when the templates have not loaded yet or the id is unknown. Every place
 * that shows a story's style to a user should go through this rather than
 * printing the raw `style_template_id`.
 */
export function styleNameOf(styles, templateId) {
  if (!templateId) return null
  const style = (styles || []).find((s) => s.template_id === templateId)
  return (style && style.name && (style.name.en || style.name.fr)) || templateId
}

/** Create a draft story; the story's own `POST /api/stories`. */
export async function createStory(payload) {
  const res = await request('/stories', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await apiError(res, 'Failed to create the story')
  return res.json()
}

/** Everything the story page shows: `{story, style_lock, style_preview, concepts_generated, jobs, cost_total_usd, route}`. */
export async function fetchStory(storyId) {
  const res = await request(`/stories/${storyId}`)
  if (!res.ok) throw await apiError(res, 'Story not found')
  return res.json()
}

/** Edit only the fields sent; answers the story. */
export async function patchStory(storyId, payload) {
  const res = await request(`/stories/${storyId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await apiError(res, 'Failed to update the story')
  return res.json()
}

/** Delete the story and its step jobs; 409 while one of its steps is in flight. */
export async function deleteStory(storyId) {
  const res = await request(`/stories/${storyId}`, { method: 'DELETE' })
  if (!res.ok) throw await apiError(res, 'Failed to delete the story')
  return res.json()
}

/** `{"library": [cards], "generated": [cards]}`; `language`/`style` default to the story's own. */
export async function fetchConcepts(storyId, { language, style } = {}) {
  const params = new URLSearchParams()
  if (language) params.set('language', language)
  if (style) params.set('style', style)
  const qs = params.toString()
  const res = await request(`/stories/${storyId}/concepts${qs ? `?${qs}` : ''}`)
  if (!res.ok) throw await apiError(res, 'Failed to fetch concepts')
  return res.json()
}

/** "Generate 10 more": queues a `concepts` step job; 201 with the job. */
export async function generateConcepts(storyId) {
  const res = await request(`/stories/${storyId}/concepts/generate`, { method: 'POST' })
  if (!res.ok) throw await apiError(res, 'Failed to queue concept generation')
  return res.json()
}

/** Choose the story's concept (`{concept_id}` or `{concept}`); answers the story. */
export async function chooseConcept(storyId, payload) {
  const res = await request(`/stories/${storyId}/concepts/choose`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await apiError(res, 'Failed to choose the concept')
  return res.json()
}

/**
 * Run one step of spec 9.1 (`body` is `{ep?, params?}`). `concepts` and
 * `bible` answer 201 with the queued job; `style` runs inline and answers
 * `{story, style_lock}`; `style_preview` answers 201 with the queued job.
 */
export async function runStoryStep(storyId, step, body = {}) {
  const res = await request(`/stories/${storyId}/steps/${step}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw await apiError(res, `Failed to run step '${step}'`)
  return res.json()
}

/**
 * Approve one document of the story (`bible`, `style`, `character:<id>`,
 * `place:<id>`, `prop:<id>`, `season`, or -- phase 3 -- `script:<ep>` /
 * `storyboard:<ep>`); answers the story (an episode document: the episode
 * page). `body` is only sent for `script:<ep>` (`{approve_anyway}`, to
 * approve over a consistency report with issues); every other caller keeps
 * posting with no body, exactly as before.
 */
export async function approveStoryDoc(storyId, doc, body) {
  const res = await request(`/stories/${storyId}/approve/${doc}`, {
    method: 'POST',
    ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
  })
  if (!res.ok) throw await apiError(res, `Failed to approve '${doc}'`)
  return res.json()
}

/** Regenerate one piece (`{target, note?}`, spec 9.2 grammar); 201 with the queued job. */
export async function regenerateStory(storyId, payload) {
  const res = await request(`/stories/${storyId}/regenerate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await apiError(res, 'Failed to queue regeneration')
  return res.json()
}

/**
 * What one step would cost and where it would run: `{est_usd, units,
 * route_class, link, ready, message}`. `target` only matters for
 * `regenerate` (its own estimate differs by what is being regenerated);
 * `selected` only for `cast` (the ticked cast-sketch names, repeated as
 * `?selected=`); `episodes` only for `season` (3 to 12, default 8 -- the
 * estimate is `1 + episodes` LLM calls); `places`/`props` only for `places`
 * (the names on screen in the proposal editor, repeated as `?place=`/
 * `?prop=`; omitted, the estimate falls back to the saved proposal).
 * `subtitles`/`encoder` only for `render` (its own params -- `step === 'render'` --
 * priced as the render would use them; PreviewPane.jsx sends `subtitles` only).
 * `route` only for `assets` (phase 6 stage 11): `auto|local|api` prices that
 * route instead of the story's own, without patching the story.
 * `fillFailedWithMotion` only for `render` (phase 6 stage 12 follow-up):
 * priced as the real render params would be, so a failed/stale/still-
 * generating clip's refusal here clears exactly when the real run's would --
 * PreviewPane.jsx re-fetches with it on every toggle of its own checkbox.
 */
export async function fetchStoryEstimate(storyId, step, {
  target, selected, episodes, places, props, ep, measure, alignWords, storyboard, subtitles, encoder, route,
  fillFailedWithMotion,
} = {}) {
  const params = new URLSearchParams()
  if (target) params.set('target', target)
  if (selected) selected.forEach((name) => params.append('selected', name))
  if (episodes != null) params.set('episodes', episodes)
  // `places`/`props`: the list the "places" step would receive (the current
  // proposal editor's, not the saved places_proposal.json). Omitted leaves
  // the estimate on its default (the saved proposal); given, each name is
  // sent as its own repeated `place=`/`prop=` (an empty array sends none,
  // which -- like omitting it -- reads as "use the saved proposal" server
  // side; the proposal editor always starts from at least the saved names).
  if (places) places.forEach((name) => params.append('place', name))
  if (props) props.forEach((name) => params.append('prop', name))
  // `ep` (phase 3, `script`/`storyboard`): the episode number. `measure`:
  // also report the `measure` block (real-voice measurement's own cost) --
  // only meaningful with `step === 'script'`.
  if (ep != null) params.set('ep', ep)
  if (measure) params.set('measure', '1')
  // Phase 4: `alignWords` -> `?align_words=1` (the assets step's own opt-in,
  // `step === 'assets'`); `storyboard` -> `?storyboard=t1|fast` (the fast
  // track's own choice of storyboard plan, `step === 'fast-track'` --
  // distinct from the storyboard *step*'s own `fast` param above).
  if (alignWords) params.set('align_words', '1')
  if (storyboard) params.set('storyboard', storyboard)
  // Phase 4, stage 15: `subtitles` -> `?subtitles=style|word_pop|two_line|none`,
  // `encoder` -> `?encoder=libx264|auto` (`step === 'render'`'s own params,
  // priced the same way whichever changed -- render.SUBTITLE_CHOICES /
  // ENCODER_CHOICES). PreviewPane.jsx only ever sends `subtitles`; `encoder`
  // is carried for completeness and left unsent (the render step defaults it).
  if (subtitles) params.set('subtitles', subtitles)
  if (encoder) params.set('encoder', encoder)
  if (route) params.set('route', route)
  if (fillFailedWithMotion) params.set('fill_failed_with_motion', '1')
  const qs = params.toString()
  const res = await request(`/stories/${storyId}/estimate/${step}${qs ? `?${qs}` : ''}`)
  if (!res.ok) throw await apiError(res, 'Failed to fetch the estimate')
  return res.json()
}

/**
 * The voice picker's data for one character: `{pinned, alternates, taken}`
 * (`GET /stories/{id}/characters/{cid}/voices`, phase 2).
 */
export async function fetchCharacterVoices(storyId, charId) {
  const res = await request(`/stories/${storyId}/characters/${charId}/voices`)
  if (!res.ok) throw await apiError(res, 'Failed to fetch the voice picker')
  return res.json()
}

/** Edit a character inline; only the fields sent are applied. Answers what was written. */
export async function patchCharacter(storyId, charId, payload) {
  const res = await request(`/stories/${storyId}/characters/${charId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await apiError(res, 'Failed to update the character')
  return res.json()
}

/** Delete a character (its folder, its id from the story); 409 while a step is in flight. */
export async function deleteCharacter(storyId, charId) {
  const res = await request(`/stories/${storyId}/characters/${charId}`, { method: 'DELETE' })
  if (!res.ok) throw await apiError(res, 'Failed to delete the character')
  return res.json()
}

/** Edit a place inline; only the fields sent are applied. Answers what was written. */
export async function patchPlace(storyId, placeId, payload) {
  const res = await request(`/stories/${storyId}/places/${placeId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await apiError(res, 'Failed to update the place')
  return res.json()
}

/** Edit a prop inline; only the fields sent are applied. Answers what was written. */
export async function patchProp(storyId, propId, payload) {
  const res = await request(`/stories/${storyId}/props/${propId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await apiError(res, 'Failed to update the prop')
  return res.json()
}

/**
 * Edit a v2 story's knowledge base inline (phase 7 stage 7): `world` (merged),
 * `beats` ([{ep, beat, ...}], a beat by episode and 1-based position),
 * `props_registry` (the whole list), `ledger_seed` (merged per character).
 * Any write moves `rev`, so an approved base must be approved again. Answers
 * knowledge.json as written.
 */
export async function patchKnowledge(storyId, payload) {
  const res = await request(`/stories/${storyId}/knowledge`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await apiError(res, 'Failed to update the knowledge base')
  return res.json()
}

/** Delete a place (its folder, its id from the story); 409 while a step is in flight. */
export async function deletePlace(storyId, placeId) {
  const res = await request(`/stories/${storyId}/places/${placeId}`, { method: 'DELETE' })
  if (!res.ok) throw await apiError(res, 'Failed to delete the place')
  return res.json()
}

/** Delete a prop (its folder, its id from the story); 409 while a step is in flight. */
export async function deleteProp(storyId, propId) {
  const res = await request(`/stories/${storyId}/props/${propId}`, { method: 'DELETE' })
  if (!res.ok) throw await apiError(res, 'Failed to delete the prop')
  return res.json()
}

/** Remove one design reference from a character's uploads. */
export async function deleteCharacterUpload(storyId, charId, name) {
  const res = await request(`/stories/${storyId}/characters/${charId}/uploads/${encodeURIComponent(name)}`,
    { method: 'DELETE' })
  if (!res.ok) throw await apiError(res, 'Failed to remove the reference')
  return res.json()
}

/**
 * One entity media file (a reference image, a design reference, a voice
 * sample), as a blob URL -- same reasoning as `fetchStoryFileUrl`: the route
 * is token-gated, so it is fetched with the auth header rather than used
 * directly as a `src`. The caller is responsible for revoking the URL.
 */
export async function fetchStoryMediaUrl(storyId, kind, eid, name) {
  const res = await request(`/stories/${storyId}/media/${kind}/${eid}/${encodeURIComponent(name)}`)
  if (!res.ok) throw await apiError(res, 'Failed to load the file')
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

/**
 * Add a design reference to a character (multipart, field `file`), reporting
 * upload progress. XHR rather than fetch, same reasoning and the same shape
 * as `uploadVideo` -- fetch cannot report upload progress -- with the bearer
 * header attached by hand since this bypasses `request()`. Rejects with the
 * API's own `detail.message` (a 415 for a file that is not a PNG, JPEG,
 * WebP or GIF image, a 413 for one over the size cap, ...).
 */
export function uploadCharacterReference(storyId, charId, file, onProgress) {
  return new Promise((resolve, reject) => {
    const formData = new FormData()
    formData.append('file', file)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${API_BASE}/stories/${storyId}/characters/${charId}/uploads`)
    const token = getToken()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)

    xhr.upload.addEventListener('progress', (event) => {
      if (!onProgress) return
      const total = event.lengthComputable ? event.total : 0
      onProgress({ loaded: event.loaded, total, percent: total ? (event.loaded / total) * 100 : null, done: false })
    })

    xhr.addEventListener('load', () => {
      let body = {}
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        body = {}
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        if (onProgress) onProgress({ loaded: file.size, total: file.size, percent: 100, done: true })
        resolve(body)
        return
      }
      const detail = body && body.detail
      const message = detail && typeof detail === 'object' ? detail.message : detail
      reject(new ApiError(message || `Upload failed (HTTP ${xhr.status})`, {
        errors: detail && typeof detail === 'object' && Array.isArray(detail.errors)
          ? detail.errors.map(String) : null,
        status: xhr.status,
      }))
    })

    xhr.addEventListener('error', () => reject(new Error(
      'Upload failed: the connection dropped before the server replied.'
    )))
    xhr.addEventListener('timeout', () => reject(new Error('Upload failed: timed out')))
    xhr.addEventListener('abort', () => reject(new Error('Upload cancelled')))

    xhr.send(formData)
  })
}

/**
 * One style-preview image, as a blob URL. The route is token-gated like
 * every other story route (DEC-113: no signed URL), so it is fetched with
 * the auth header rather than used directly as an <img src> -- the caller is
 * responsible for revoking the URL (`URL.revokeObjectURL`) once done with it.
 */
export async function fetchStoryFileUrl(storyId, name) {
  const res = await request(`/stories/${storyId}/files/${encodeURIComponent(name)}`)
  if (!res.ok) throw await apiError(res, 'Failed to load the preview image')
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

// ------------------------------------------------------ episodes (phase 3)

/**
 * One episode's page: `{ep, script, storyboard, template, state, jobs}`
 * (`GET /stories/{id}/episodes/{ep}`).
 */
export async function fetchEpisode(storyId, ep) {
  const res = await request(`/stories/${storyId}/episodes/${ep}`)
  if (!res.ok) throw await apiError(res, 'Failed to fetch the episode')
  return res.json()
}

/**
 * Edit an episode's script inline (`ScriptPatchRequest`: `lines`, `scenes`,
 * `hook_on_screen_text`, `cliffhanger_reveal`, `next_episode_teaser`; only
 * the fields sent are applied). Answers the episode page.
 */
export async function patchEpisodeScript(storyId, ep, payload) {
  const res = await request(`/stories/${storyId}/episodes/${ep}/script`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await apiError(res, 'Failed to update the script')
  return res.json()
}

/**
 * Edit an episode's storyboard inline (`StoryboardPatchRequest`: `shots`,
 * `transitions`, `refresh_prompts`; only the fields sent are applied). Used
 * from stage 11's storyboard pane. Answers the episode page.
 */
export async function patchEpisodeStoryboard(storyId, ep, payload) {
  const res = await request(`/stories/${storyId}/episodes/${ep}/storyboard`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await apiError(res, 'Failed to update the storyboard')
  return res.json()
}

/**
 * One line's measured take (`line_NN.mp3`/`.wav`, `name` from that line's
 * `timing.audio`), as a blob URL -- same reasoning as `fetchStoryMediaUrl`:
 * the route is token-gated, so it is fetched with the auth header rather
 * than used directly as an <audio src>. The caller is responsible for
 * revoking the URL.
 */
export async function fetchEpisodeVoiceUrl(storyId, ep, name) {
  const res = await request(`/stories/${storyId}/episodes/${ep}/voice/${encodeURIComponent(name)}`)
  if (!res.ok) throw await apiError(res, 'Failed to load the voice line')
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

// ------------------------------------------------------- episodes (phase 4)

/**
 * One shot's generated image (`shot_NN.<ext>`, `name` from that shot's
 * `assets.shots[].image_name`), as a blob URL -- same reasoning as
 * `fetchEpisodeVoiceUrl`: the route is behind the bearer header, so it is
 * fetched rather than used directly as an `<img src>`. The caller is
 * responsible for revoking the URL.
 */
export async function fetchShotImageUrl(storyId, ep, imageName) {
  const res = await request(`/stories/${storyId}/episodes/${ep}/shots/${encodeURIComponent(imageName)}`)
  if (!res.ok) throw await apiError(res, 'Failed to load the shot image')
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

/**
 * One shot's clip (`shot_NN.mp4`, `name` from that shot's `assets.shots[].
 * clip.name`), as a blob URL -- same reasoning as `fetchShotImageUrl`
 * (DEC-113): the route is behind the bearer header, so it is fetched rather
 * than used directly as a `<video src>`. The caller is responsible for
 * revoking the URL. Phase 6 stage 11/12.
 */
export async function fetchEpisodeClipUrl(storyId, ep, name) {
  const res = await request(`/stories/${storyId}/episodes/${ep}/clips/${encodeURIComponent(name)}`)
  if (!res.ok) throw await apiError(res, 'Failed to load the clip')
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

/**
 * Edit an episode's assets inline (`AssetsPatchRequest`'s `shots` field):
 * each `{shot_id, locked?, keep_still?, animate?, keep_native_audio?}` --
 * only an imaged shot may be locked, and locking one stales the assets
 * approval; phase 6 stage 11's clip flags (true, false, or null to clear).
 * Answers the episode page.
 */
export async function patchEpisodeAssets(storyId, ep, shots) {
  const res = await request(`/stories/${storyId}/episodes/${ep}/assets`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ shots }),
  })
  if (!res.ok) throw await apiError(res, 'Failed to update the assets')
  return res.json()
}

/**
 * Switch an episode's image or video link (`AssetsPatchRequest`'s `links`
 * field, phase 6 stage 11, A-087): `{image?, video?}`, one of the Settings
 * chain's links -- a separate function from `patchEpisodeAssets` because
 * `AssetsPatchRequest` now carries two independent optional top-level
 * fields, and a caller editing shots never means to touch the episode's
 * link (and vice versa). `links` is the sticky offer's own `switch` shape
 * (`offer.switch.links`), sent as is. Answers the episode page.
 */
export async function patchEpisodeAssetsLinks(storyId, ep, links) {
  const res = await request(`/stories/${storyId}/episodes/${ep}/assets`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ links }),
  })
  if (!res.ok) throw await apiError(res, 'Failed to update the assets')
  return res.json()
}

// ------------------------------------------------------- episodes (phase 5)

/**
 * Paste episode `ep`'s audience feedback (`StoryEpisodeFeedbackRequest`:
 * `{text, stats?}`, each at most 6,000 characters -- the same cap as
 * `clipping.aistory.schemas.FEEDBACK_TEXT_MAX_LENGTH` /
 * `FEEDBACK_STATS_MAX_LENGTH`; refused whole, never trimmed, over the cap
 * (422) -- `SeasonStep.jsx` checks the cap client-side before calling this).
 * Stores the item -- replacing any earlier one of this episode -- and queues
 * the `feedback` step; 201 with the queued job.
 */
export async function postEpisodeFeedback(storyId, ep, payload) {
  const res = await request(`/stories/${storyId}/episodes/${ep}/feedback`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await apiError(res, 'Failed to submit the feedback')
  return res.json()
}

/**
 * Accept or reject one item of episode `ep`'s proposals
 * (`StoryProposalDecisionRequest`: `{accept, role?}` -- `role` only on an
 * accepted character). Answers `workflow.proposal_request`'s payload: an
 * accepted character carries `job` (the queued cast job); a rejection or an
 * accepted twist never does. A decision is final -- the caller confirms
 * before calling this.
 */
export async function decideProposal(storyId, ep, itemId, payload) {
  const res = await request(`/stories/${storyId}/episodes/${ep}/proposals/${itemId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw await apiError(res, 'Failed to decide the proposal')
  return res.json()
}

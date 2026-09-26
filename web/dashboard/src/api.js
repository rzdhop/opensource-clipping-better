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

/** Approve one document (`bible` or `style`) of the story; answers the story. */
export async function approveStoryDoc(storyId, doc) {
  const res = await request(`/stories/${storyId}/approve/${doc}`, { method: 'POST' })
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
 * `regenerate` (its own estimate differs by what is being regenerated).
 */
export async function fetchStoryEstimate(storyId, step, { target } = {}) {
  const qs = target ? `?target=${encodeURIComponent(target)}` : ''
  const res = await request(`/stories/${storyId}/estimate/${step}${qs}`)
  if (!res.ok) throw await apiError(res, 'Failed to fetch the estimate')
  return res.json()
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

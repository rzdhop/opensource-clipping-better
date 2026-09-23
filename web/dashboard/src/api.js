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

/** The API's own explanation of a refusal (409, 429, ...), else *fallback*. */
async function detailOf(res, fallback) {
  try {
    const body = await res.json()
    if (body && body.detail) return String(body.detail)
  } catch {}
  return fallback
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

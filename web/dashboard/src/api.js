const API_BASE = '/api'

export async function fetchJobs() {
  const res = await fetch(`${API_BASE}/jobs`)
  if (!res.ok) throw new Error('Failed to fetch jobs')
  return res.json()
}

export async function fetchJob(jobId) {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`)
  if (!res.ok) throw new Error('Job not found')
  return res.json()
}

export async function createJob(payload) {
  const res = await fetch(`${API_BASE}/jobs`, {
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

export async function deleteJob(jobId) {
  const res = await fetch(`${API_BASE}/jobs/${jobId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error('Failed to delete job')
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
  const res = await fetch(`${API_BASE}/settings`)
  if (!res.ok) throw new Error('Failed to fetch settings')
  return res.json()
}

export async function updateSettings(payload) {
  const res = await fetch(`${API_BASE}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error('Failed to update settings')
  return res.json()
}

export async function fetchHealth() {
  const res = await fetch(`${API_BASE}/health`)
  if (!res.ok) throw new Error('Health check failed')
  return res.json()
}

// `onStatus` reports 'live' | 'closed'. Without it the UI cannot tell a job
// that is quiet from a stream that died, which are the two cases a user staring
// at an unmoving progress bar most needs told apart.
export function createSSEConnection(jobId, onMessage, onStatus) {
  const eventSource = new EventSource(`${API_BASE}/jobs/${jobId}/status`)

  eventSource.onopen = () => { if (onStatus) onStatus('live') }

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      onMessage(data)
    } catch (e) {
      console.error('SSE parse error:', e)
    }
  }

  eventSource.onerror = () => {
    eventSource.close()
    if (onStatus) onStatus('closed')
  }

  return eventSource
}

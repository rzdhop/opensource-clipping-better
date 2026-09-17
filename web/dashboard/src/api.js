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
      } else {
        reject(new Error(body.detail || 'Upload failed'))
      }
    })

    xhr.addEventListener('error', () => reject(new Error('Upload failed: network error')))
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

export function createSSEConnection(jobId, onMessage) {
  const eventSource = new EventSource(`${API_BASE}/jobs/${jobId}/status`)

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
  }

  return eventSource
}

import { useState, useEffect } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import { fetchJob, cancelJob, deleteJob, createSSEConnection } from '../api'
import { ActivityConsole, LiveActivity, TERMINAL, mergeEvents as mergeEventsPure } from '../components/ActivityFeed'

const STEPS = [
  { key: 'download', label: 'Source' },
  { key: 'transcribe', label: 'Transcribe' },
  { key: 'analyze', label: 'AI Analysis' },
  { key: 'metadata', label: 'Metadata' },
  { key: 'diarization', label: 'Speakers' },
  { key: 'render', label: 'Render' },
  { key: 'done', label: 'Done' },
]

/**
 * Diarization only runs for split-screen or camera-switch jobs. Showing the dot
 * unconditionally marks a step green that never ran; omitting it entirely --
 * which is what the list did before -- breaks every dot the moment it does run,
 * because the current step matches nothing.
 */
function stepsFor(job) {
  const config = job.config || {}
  const diarizes = Boolean(config.use_split_screen || config.use_camera_switch)
  const ran = job.progress?.step === 'diarization'
  if (diarizes || ran) return STEPS
  return STEPS.filter(step => step.key !== 'diarization')
}

function JobDetail() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const [job, setJob] = useState(null)
  const [events, setEvents] = useState([])
  const [streamState, setStreamState] = useState('connecting')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')

  // Both ask first: one stops work the job has already paid for in quota and
  // CPU, the other removes rendered clips and the upload for good.
  const onCancel = async () => {
    if (!window.confirm(
      'Cancel this job?\n\nIt stops at its next step. A provider request already '
      + 'in flight can take a few minutes to return. Its files are kept.'
    )) return
    setBusy(true)
    setActionError('')
    try {
      setJob(await cancelJob(jobId))
    } catch (err) {
      setActionError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const onDelete = async () => {
    if (!window.confirm(
      'Delete this job and its files?\n\nIts rendered clips and its upload are '
      + 'removed from the server. This cannot be undone.'
    )) return
    setBusy(true)
    setActionError('')
    try {
      await deleteJob(jobId)
      navigate('/clips')
    } catch (err) {
      setActionError(err.message)
      setBusy(false)
    }
  }

  // Merged by sequence number, never replaced: the REST poll and the SSE stream
  // both deliver events and routinely overlap. The merge rule itself lives in
  // components/ActivityFeed so it is shared with useJobFeed.
  const mergeEvents = (incoming) => {
    if (!incoming || incoming.length === 0) return
    setEvents(previous => mergeEventsPure(previous, incoming))
  }

  // The media URLs carry ?exp=&sig= (web/api/auth.py), so appending a flag needs
  // & and not ? -- getting that wrong produces a 401 that looks like a signing bug.
  const asDownload = (url) => (url ? `${url}${url.includes('?') ? '&' : '?'}download=1` : url)

  // An expiring URL can go stale in a tab left open overnight. The bytes already
  // buffered keep playing and the NEXT range request 401s, so the player just
  // stalls -- which is the exact symptom this whole change exists to remove. One
  // re-fetch mints a fresh signature. Guarded so a genuinely broken file cannot
  // become a reload loop.
  const [recovered, setRecovered] = useState(false)
  const recoverExpiredMedia = async () => {
    if (recovered) return
    setRecovered(true)
    try {
      setJob(await fetchJob(jobId))
    } catch (err) {
      console.error('Could not refresh the clip URLs:', err)
    }
  }

  useEffect(() => {
    let sse = null

    const load = async () => {
      try {
        const data = await fetchJob(jobId)
        setJob(data)
        mergeEvents(data.events)

        if (!TERMINAL.includes(data.status)) {
          sse = createSSEConnection(
            jobId,
            (event) => {
              if (event.type === 'completed') {
                fetchJob(jobId).then(fresh => { setJob(fresh); mergeEvents(fresh.events) })
              } else if (event.type === 'progress') {
                setJob(prev => prev ? { ...prev, status: event.status, progress: event.progress, error: event.error } : prev)
              } else if (event.type === 'events') {
                mergeEvents(event.events)
              }
            },
            setStreamState,
          )
        } else {
          setStreamState('closed')
        }
      } catch (err) {
        console.error(err)
      } finally {
        setLoading(false)
      }
    }

    load()
    return () => { if (sse) sse.close() }
  }, [jobId])

  // Also poll for updates
  useEffect(() => {
    if (!job) return
    if (TERMINAL.includes(job.status)) return

    const interval = setInterval(async () => {
      try {
        const data = await fetchJob(jobId)
        setJob(data)
        mergeEvents(data.events)
      } catch {}
    }, 3000)
    return () => clearInterval(interval)
  }, [jobId, job?.status])

  if (loading) return <div className="empty-state"><div className="spinner"></div></div>
  if (!job) return <div className="empty-state"><h3>Job not found</h3></div>

  const currentStep = job.progress?.step || ''
  const percent = job.progress?.percent || 0
  const running = !TERMINAL.includes(job.status)
  const steps = stepsFor(job)

  return (
    <div className="fade-in">
      <div className="page-header">
        <div>
          <h2>Job #{job.id}</h2>
          <p>{job.upload_filename || job.source_url || job.url || 'Unknown source'}</p>
        </div>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center' }}>
          <span className={`badge badge-${job.status}`}>{job.status}</span>
          <Link to="/clips/new" state={{ reuseJob: job }} className="btn btn-secondary btn-sm">🔁 Clone & Rerun</Link>
          {running
            ? <button type="button" className="btn btn-danger btn-sm" onClick={onCancel} disabled={busy}>⏹ Cancel</button>
            : <button type="button" className="btn btn-danger btn-sm" onClick={onDelete} disabled={busy}>🗑 Delete</button>}
          <Link to="/clips" className="btn btn-ghost btn-sm">← Back</Link>
        </div>
      </div>

      {actionError && (
        <div className="card" role="alert" style={{ marginBottom: '16px', borderColor: 'rgba(239,68,68,0.2)' }}>
          <p style={{ fontSize: '13px', color: 'var(--error)', whiteSpace: 'pre-wrap' }}>{actionError}</p>
        </div>
      )}

      {/* Progress */}
      {running && (
        <div className="card" style={{ marginBottom: '16px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px' }}>
            <span style={{ fontSize: '13px', fontWeight: 600 }}>Progress</span>
            <span style={{ fontSize: '13px', color: 'var(--accent-hover)' }}>{Math.round(percent)}%</span>
          </div>
          <div className="progress-bar-bg">
            <div className="progress-bar-fill" style={{ width: `${percent}%` }}></div>
          </div>
          <div className="progress-steps" style={{ marginTop: '14px' }}>
            {steps.map(s => {
              const stepIdx = steps.findIndex(x => x.key === s.key)
              const currentIdx = steps.findIndex(x => x.key === currentStep)
              let cls = ''
              if (stepIdx < currentIdx) cls = 'done'
              else if (stepIdx === currentIdx) cls = 'active'
              return (
                <div key={s.key} className={`progress-step ${cls}`}>
                  <span className="step-dot"></span>
                  {s.label}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* What it is doing right now */}
      {(running || events.length > 0) && (
        <LiveActivity job={job} events={events} streamState={streamState} />
      )}

      {/* Error */}
      {job.error && (
        <div className="card" style={{ marginBottom: '16px', borderColor: 'rgba(239,68,68,0.2)' }}>
          <h3 style={{ color: 'var(--error)', fontSize: '14px', marginBottom: '8px' }}>❌ Error</h3>
          {/* pre-wrap: the preflight and chain-readiness errors are one line
              per link, and a <p> collapsed them into one run-on sentence. */}
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap' }}>{job.error}</p>
        </div>
      )}

      {/* Clips */}
      {job.clips && job.clips.length > 0 && (
        <>
          <h3 style={{ fontSize: '16px', fontWeight: 700, marginBottom: '16px' }}>
            🎞️ Generated Clips ({job.clips.length})
          </h3>
          <div className="clip-grid">
            {job.clips.map((clip, i) => (
              <div key={i} className="clip-card">
                <video
                  className="clip-video"
                  controls
                  preload="metadata"
                  poster={clip.thumbnail_url || undefined}
                  src={clip.download_url}
                  onError={recoverExpiredMedia}
                />
                <div className="clip-body">
                  <div className="clip-title">{clip.title || clip.title_en || `Clip ${clip.rank}`}</div>
                  <div className="clip-stats">
                    {clip.viral_score && <span className="viral-score">🔥 {clip.viral_score}</span>}
                    {clip.duration && <span>{Math.round(clip.duration)}s</span>}
                    <span>Rank #{clip.rank}</span>
                  </div>
                  <div className="clip-actions">
                    {/* download={clip.filename} so the saved name is right even in a
                        browser that ignores Content-Disposition on a same-origin link. */}
                    <a
                      href={asDownload(clip.download_url)}
                      download={clip.filename || undefined}
                      className="btn btn-secondary btn-sm"
                    >⬇️ Download</a>
                    {clip.srt_url && (
                      <a
                        href={asDownload(clip.srt_url)}
                        download={clip.srt_url.split('/').pop().split('?')[0]}
                        className="btn btn-secondary btn-sm"
                      >💬 .srt</a>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Log */}
      {job.log && job.log.length > 0 && (
        <div style={{ marginTop: '24px' }}>
          <h3 style={{ fontSize: '14px', fontWeight: 700, marginBottom: '10px' }}>📋 Step summary</h3>
          <div className="log-viewer">
            {job.log.map((line, i) => <div key={i}>{line}</div>)}
          </div>
        </div>
      )}
    </div>
  )
}

export default JobDetail

import { useState, useEffect, useRef } from 'react'
import { useParams, Link } from 'react-router-dom'
import { fetchJob, deleteJob, createSSEConnection } from '../api'
import { parseTime, formatDuration, formatClock, useSecondsTicker } from '../time'

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

const TERMINAL = ['completed', 'failed', 'cancelled']

/**
 * The console. Follows the tail unless the user has scrolled up to read
 * something -- yanking them back to the bottom mid-sentence is the fastest way
 * to make a live log useless.
 */
function ActivityConsole({ events }) {
  const boxRef = useRef(null)
  const followRef = useRef(true)

  const onScroll = () => {
    const box = boxRef.current
    if (!box) return
    const distanceFromBottom = box.scrollHeight - box.scrollTop - box.clientHeight
    followRef.current = distanceFromBottom < 40
  }

  useEffect(() => {
    const box = boxRef.current
    if (box && followRef.current) box.scrollTop = box.scrollHeight
  }, [events])

  return (
    <div className="log-viewer activity-console" ref={boxRef} onScroll={onScroll}>
      {events.map(event => (
        <div key={event.seq} className={`activity-line activity-${event.level}`}>
          <span className="activity-time">{formatClock(event.ts)}</span>
          <span className="activity-message">{event.message}</span>
        </div>
      ))}
    </div>
  )
}

/**
 * What is happening right now, and how long it has been happening.
 *
 * The percentage alone cannot distinguish a job that is working from one that
 * died: a single AI call holds at 36% for as long as the provider's retry
 * ladder runs, and one clip can render for minutes.
 */
function LiveActivity({ job, events, streamState }) {
  const progress = job.progress || {}
  const running = !TERMINAL.includes(job.status)
  useSecondsTicker(running)

  const now = Date.now()
  const stepStarted = parseTime(progress.step_started_at)
  const created = parseTime(job.created_at)
  const inStep = stepStarted ? formatDuration(now - stepStarted.getTime()) : null
  const inJob = created ? formatDuration(now - created.getTime()) : null

  const lastEvent = events.length ? events[events.length - 1] : null
  const sinceSignal = lastEvent && parseTime(lastEvent.ts)
    ? now - parseTime(lastEvent.ts).getTime()
    : null
  // Long enough that it is not just a slow tick, short enough to reassure
  // before the user reaches for the kill switch.
  const quiet = running && sinceSignal != null && sinceSignal > 45000

  const clipPercent = progress.clip_total
    ? Math.round((progress.clip_index / progress.clip_total) * 100)
    : null

  return (
    <div className="card activity-card">
      <div className="activity-header">
        <span className="activity-title">
          <span className={`activity-pulse ${running ? 'running' : 'idle'}`}></span>
          Live activity
        </span>
        <span className="activity-clocks">
          {inStep && <span title="Time on the current step">{inStep} on this step</span>}
          {inJob && <span className="activity-dim">· {inJob} total</span>}
        </span>
      </div>

      <p className="activity-headline">{progress.message || 'Waiting to start...'}</p>
      {progress.detail && progress.detail !== progress.message && (
        <p className="activity-detail">{progress.detail}</p>
      )}

      <div className="activity-chips">
        {progress.provider && (
          <span className="chip chip-accent" title="The AI provider and model being asked">
            🤖 {progress.provider.toUpperCase()}
            {progress.model && <span className="chip-sub">{progress.model}</span>}
          </span>
        )}
        {progress.attempt && progress.max_attempts && (
          <span className={`chip ${progress.attempt > 1 ? 'chip-warn' : ''}`}>
            attempt {progress.attempt} of {progress.max_attempts}
          </span>
        )}
        {progress.clip_total && (
          <span className="chip">clip {progress.clip_index} of {progress.clip_total}</span>
        )}
        {streamState === 'closed' && running && (
          <span className="chip chip-warn" title="Falling back to polling every 3 seconds">
            live stream dropped
          </span>
        )}
      </div>

      {clipPercent != null && (
        <div className="progress-bar-bg activity-subbar">
          <div className="progress-bar-fill" style={{ width: `${clipPercent}%` }}></div>
        </div>
      )}

      {quiet && (
        <p className="activity-quiet">
          Nothing printed for {formatDuration(sinceSignal)}. This step is a single
          long call — an AI request on its retry ladder, a model download, or one
          clip encoding. It is still running.
        </p>
      )}

      {events.length > 0 && (
        <>
          <div className="activity-console-header">
            <span>Pipeline output ({events.length} lines)</span>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => navigator.clipboard?.writeText(
                events.map(e => `${formatClock(e.ts)}  ${e.message}`).join('\n')
              )}
            >
              Copy
            </button>
          </div>
          <ActivityConsole events={events} />
        </>
      )}
    </div>
  )
}

function JobDetail() {
  const { jobId } = useParams()
  const [job, setJob] = useState(null)
  const [events, setEvents] = useState([])
  const [streamState, setStreamState] = useState('connecting')
  const [loading, setLoading] = useState(true)

  // Merged by sequence number, never replaced: the REST poll and the SSE stream
  // both deliver events and routinely overlap.
  const mergeEvents = (incoming) => {
    if (!incoming || incoming.length === 0) return
    setEvents(previous => {
      const bySeq = new Map(previous.map(e => [e.seq, e]))
      incoming.forEach(e => bySeq.set(e.seq, e))
      return [...bySeq.values()].sort((a, b) => a.seq - b.seq)
    })
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
        <div style={{ display: 'flex', gap: '8px' }}>
          <span className={`badge badge-${job.status}`}>{job.status}</span>
          <Link to="/new" state={{ reuseJob: job }} className="btn btn-secondary btn-sm">🔁 Clone & Rerun</Link>
          <Link to="/" className="btn btn-ghost btn-sm">← Back</Link>
        </div>
      </div>

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
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>{job.error}</p>
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
                <video className="clip-video" controls preload="metadata" src={clip.download_url} />
                <div className="clip-body">
                  <div className="clip-title">{clip.title || clip.title_en || `Clip ${clip.rank}`}</div>
                  <div className="clip-stats">
                    {clip.viral_score && <span className="viral-score">🔥 {clip.viral_score}</span>}
                    {clip.duration && <span>{Math.round(clip.duration)}s</span>}
                    <span>Rank #{clip.rank}</span>
                  </div>
                  <div className="clip-actions">
                    <a href={clip.download_url} download className="btn btn-secondary btn-sm">⬇️ Download</a>
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

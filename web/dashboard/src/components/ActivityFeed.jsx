import { useEffect, useRef, useState } from 'react'
import { fetchJob, createSSEConnection } from '../api'
import { parseTime, formatDuration, formatClock, useSecondsTicker } from '../time'

/**
 * Statuses a job (clip or story-step) never leaves. A story step also stops
 * here: it is finished for the worker once it awaits the user's approval --
 * the slot is freed and the stream closes -- so the page and its feed must
 * stop polling it exactly like a completed job, even though the *story* is
 * not done.
 */
export const TERMINAL = ['completed', 'failed', 'cancelled', 'awaiting_approval']

/**
 * Merge incoming events into the events already held, by `seq`. Pure, so the
 * live page and any future feed (a story step's own activity view) share one
 * de-duplication rule -- the REST poll and the SSE stream both deliver
 * events and routinely overlap.
 */
export function mergeEvents(previous, incoming) {
  if (!incoming || incoming.length === 0) return previous
  const bySeq = new Map(previous.map(e => [e.seq, e]))
  incoming.forEach(e => bySeq.set(e.seq, e))
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq)
}

/**
 * The console. Follows the tail unless the user has scrolled up to read
 * something -- yanking them back to the bottom mid-sentence is the fastest
 * way to make a live log useless.
 */
export function ActivityConsole({ events }) {
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
export function LiveActivity({ job, events, streamState }) {
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
            {progress.model && <span className="chip-sub" title={progress.model}>{progress.model}</span>}
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

/**
 * What JobDetail does today, as a hook: initial fetch, SSE via
 * createSSEConnection, merge incoming events by `seq`, a 3s poll fallback,
 * and stop (both the stream and the poll) once the job reaches a terminal
 * status -- `awaiting_approval` included, so a step job's feed goes quiet the
 * moment it is the user's turn.
 *
 * Not wired into JobDetail yet (phase-1 stage 10): JobDetail also lets
 * onCancel/onDelete/recoverExpiredMedia push a fresh job into its own state,
 * which this hook does not expose a setter for. `onJob`, called whenever the
 * hook obtains a new job (initial load, SSE completion, or poll), is here for
 * the story step page of stage 11, which only needs to observe the job, not
 * mutate it from outside.
 */
export function useJobFeed(jobId, { onJob } = {}) {
  const [job, setJob] = useState(null)
  const [events, setEvents] = useState([])
  const [streamState, setStreamState] = useState('connecting')

  const mergeIncoming = (incoming) => {
    if (!incoming || incoming.length === 0) return
    setEvents(previous => mergeEvents(previous, incoming))
  }

  const applyJob = (data) => {
    setJob(data)
    if (onJob) onJob(data)
  }

  useEffect(() => {
    let sse = null
    let cancelled = false

    const load = async () => {
      try {
        const data = await fetchJob(jobId)
        if (cancelled) return
        applyJob(data)
        mergeIncoming(data.events)

        if (!TERMINAL.includes(data.status)) {
          sse = createSSEConnection(
            jobId,
            (event) => {
              if (event.type === 'completed') {
                fetchJob(jobId).then(fresh => {
                  applyJob(fresh)
                  mergeIncoming(fresh.events)
                })
              } else if (event.type === 'progress') {
                setJob(prev => prev ? { ...prev, status: event.status, progress: event.progress, error: event.error } : prev)
              } else if (event.type === 'events') {
                mergeIncoming(event.events)
              }
            },
            setStreamState,
          )
        } else {
          setStreamState('closed')
        }
      } catch (err) {
        console.error(err)
      }
    }

    load()
    return () => { cancelled = true; if (sse) sse.close() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId])

  // Also poll for updates
  useEffect(() => {
    if (!job) return
    if (TERMINAL.includes(job.status)) return

    const interval = setInterval(async () => {
      try {
        const data = await fetchJob(jobId)
        applyJob(data)
        mergeIncoming(data.events)
      } catch {}
    }, 3000)
    return () => clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, job?.status])

  return { job, events, streamState }
}

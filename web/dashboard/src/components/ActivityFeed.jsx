import { useEffect, useMemo, useRef, useState } from 'react'
import { fetchJob, createSSEConnection } from '../api'
import { parseTime, formatDuration, formatClock, useSecondsTicker, jobClocks } from '../time'
import { IconButton, useToast } from '../ui'
import {
  AlertTriangle, ArrowDownToLine, Ban, BookOpen, Bot, Brain, Calendar, ChevronRight, CircleCheck, CircleDollarSign,
  CircleX, Clapperboard, Copy, Dot, Eye, FastForward, FileText, Film, Flag, ImageIcon, Info, KeyRound, Lightbulb,
  LinkIcon, Lock, MapPin, Mic, OctagonX, RefreshCw, Repeat, Save, SkipForward, Sparkles, Timer, Trash2, User, Users,
  Volume2, Wrench,
} from '../ui/icons'

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

// ------------------------------------------------------------------ the feed
//
// The console is a grouped timeline (dashboard overhaul stage 5, DEC-257).
// The backend is consistent about the emoji that opens each line, so they
// are read here as an icon, a tone and a group header -- the raw line stays
// in each row's tooltip and in "Copy log", untouched.

// A fast-track sub-step ("⏩ Fast track 3/6: paid check"): when a job prints
// these, they alone (with the worker's own step lines) start the groups.
const FAST_TRACK_HEADER = /^⏩ Fast track \d+\/\d+: /
// The section starts a step prints (voices, images, the keyframe check, its
// auto-fix, the clips and the render). A run of lines opening with the same
// one ("🎬 Animating 8 shots", then "🎬 sh01 via …") stays one group.
const SECTION_EMOJI = new Set(['🎬', '🎙', '🖼', '👁', '🛠'])
// One leading pictograph (its variation selector dropped), after any indent.
const LEADING_EMOJI = /^([\p{Extended_Pictographic}↻↷])\uFE0F?\s*/u

// The emoji the pipeline opens its lines with, as icons. A tone colours the
// icon only; the line's own tone comes from lineTone below.
const LINE_ICONS = {
  '✅': { icon: CircleCheck, tone: 'success' },
  '✖': { icon: CircleX, tone: 'danger' },
  '❌': { icon: CircleX, tone: 'danger' },
  '⚠': { icon: AlertTriangle, tone: 'warning' },
  '🛑': { icon: OctagonX, tone: 'danger' },
  '⛔': { icon: Ban, tone: 'danger' },
  '⏹': { icon: OctagonX, tone: 'danger' },
  '⏩': { icon: FastForward, tone: 'accent' },
  '🏁': { icon: Flag, tone: 'success' },
  '🎬': { icon: Clapperboard },
  '🎞': { icon: Film },
  '🎥': { icon: Film },
  '🎙': { icon: Mic },
  '🎤': { icon: Mic },
  '🔊': { icon: Volume2 },
  '🔇': { icon: Volume2 },
  '🎧': { icon: Mic },
  '🎵': { icon: Volume2 },
  '🖼': { icon: ImageIcon },
  '📸': { icon: ImageIcon },
  '👁': { icon: Eye },
  '👀': { icon: Eye },
  '🛠': { icon: Wrench },
  '🩹': { icon: Wrench },
  '🔁': { icon: Repeat },
  '🔄': { icon: RefreshCw },
  '♻': { icon: RefreshCw },
  '↻': { icon: RefreshCw },
  '⏭': { icon: SkipForward },
  '⏳': { icon: Timer },
  '⏱': { icon: Timer },
  '⏸': { icon: Timer },
  '🧠': { icon: Brain },
  '📝': { icon: FileText },
  '📄': { icon: FileText },
  '✍': { icon: FileText },
  '📚': { icon: BookOpen },
  '🗺': { icon: MapPin },
  '💡': { icon: Lightbulb },
  '💸': { icon: CircleDollarSign },
  '💲': { icon: CircleDollarSign },
  '🧾': { icon: CircleDollarSign },
  '👤': { icon: User },
  '👥': { icon: Users },
  '🗣': { icon: User },
  '🔒': { icon: Lock },
  '🔓': { icon: Lock },
  '🔑': { icon: KeyRound },
  '🔐': { icon: KeyRound },
  '💾': { icon: Save },
  '🔍': { icon: Eye },
  '🔎': { icon: Eye },
  '🔗': { icon: LinkIcon },
  '✋': { icon: Ban },
  '🪝': { icon: Sparkles },
  '⬇': { icon: ArrowDownToLine },
  '📥': { icon: ArrowDownToLine },
  '🚀': { icon: Sparkles },
  '✨': { icon: Sparkles },
  '🧹': { icon: Sparkles },
  '🟡': { icon: Info, tone: 'warning' },
  '🏷': { icon: FileText },
  '📅': { icon: Calendar },
  '🔤': { icon: FileText },
  '🗑': { icon: Trash2 },
  '🤖': { icon: Bot },
}

const DANGER_EMOJI = new Set(['✖', '❌', '🛑', '⛔'])

/** "danger", "warning" or null: the pipeline's level, then the markers the feed highlights. */
function lineTone(event, emoji, body) {
  if (event.level === 'error' || DANGER_EMOJI.has(emoji)) return 'danger'
  if (emoji === '⚠' || /\b(failed|refused)\b/i.test(body)) return 'warning'
  // A retry (🔁) is logged as a warning by the backend; it is routine, and
  // highlighting every attempt would bury the lines that matter.
  if (event.level === 'warn' && emoji !== '🔁') return 'warning'
  return null
}

const TONE_RANK = { danger: 2, warning: 1 }
const worse = (a, b) => ((TONE_RANK[b] || 0) > (TONE_RANK[a] || 0) ? b : a)

/**
 * One feed line read for display: its icon (from the leading emoji), the text
 * after it, its tone, its indent depth, and whether it starts a group. Pure.
 */
export function classifyLine(event, { fastTrack = false } = {}) {
  const raw = event.message || ''
  const body = raw.trimStart()
  const indent = raw.length - body.length
  const match = body.match(LEADING_EMOJI)
  const emoji = match ? match[1] : null
  const entry = emoji ? LINE_ICONS[emoji] : null
  const isFastTrack = FAST_TRACK_HEADER.test(body)
  const header = indent === 0 && (
    event.level === 'step' || isFastTrack || (!fastTrack && SECTION_EMOJI.has(emoji))
  )
  return {
    emoji,
    icon: entry ? entry.icon : null,
    iconTone: entry ? entry.tone || null : null,
    // With an icon in its place the emoji is dropped from the text; an
    // unknown one stays as it was printed.
    text: entry ? body.slice(match[0].length) : body,
    tone: lineTone(event, emoji, body),
    depth: indent >= 6 ? 2 : indent >= 2 ? 1 : 0,
    header,
    fastTrack: isFastTrack,
  }
}

/**
 * The events as groups: `[{key, head: {event, line} | null, lines: [{event,
 * line}], tone}]`. A group opens at each header line -- a fast-track
 * sub-step, a step line of the worker, or (in a job with no fast track) a
 * section start -- and holds every line until the next one; lines before the
 * first header form a group with no head. Pure.
 */
export function groupEvents(events) {
  const fastTrack = events.some(event => FAST_TRACK_HEADER.test((event.message || '').trimStart()))
  const groups = []
  let current = null
  for (const event of events) {
    const line = classifyLine(event, { fastTrack })
    const sameSection = current && current.head && !line.fastTrack && event.level !== 'step'
      && current.head.event.level !== 'step' && current.head.line.emoji === line.emoji
    if (line.header && !sameSection) {
      current = { key: `g${event.seq}`, head: { event, line }, lines: [], tone: line.tone }
      groups.push(current)
      continue
    }
    if (!current) {
      current = { key: 'g-start', head: null, lines: [], tone: null }
      groups.push(current)
    }
    current.lines.push({ event, line })
    current.tone = worse(current.tone, line.tone)
  }
  return groups
}

const TONE_LABEL = { danger: 'Error: ', warning: 'Warning: ' }

function FeedIcon({ line }) {
  const Icon = line.icon || Dot
  const tone = line.tone || line.iconTone
  return (
    <span className={`activity-icon${tone ? ` activity-icon-${tone}` : ''}`} aria-hidden="true">
      <Icon size={line.icon ? 13 : 14} />
    </span>
  )
}

function FeedLine({ event, line }) {
  const classes = ['activity-line', `activity-${event.level}`]
  if (line.tone) classes.push(`activity-tone-${line.tone}`)
  if (line.depth) classes.push(`activity-depth-${line.depth}`)
  return (
    <div className={classes.join(' ')} title={event.message}>
      <span className="activity-time">{formatClock(event.ts)}</span>
      <FeedIcon line={line} />
      <span className="activity-message">
        {line.tone && <span className="sr-only">{TONE_LABEL[line.tone]}</span>}
        {line.text}
      </span>
    </div>
  )
}

function FeedGroup({ group, open, onToggle }) {
  const { head, lines, tone } = group
  const bodyId = `activity-${group.key}`
  const counts = lines.reduce((acc, { line }) => {
    if (line.tone) acc[line.tone] = (acc[line.tone] || 0) + 1
    return acc
  }, {})
  const headInner = head && (
    <>
      <span className="activity-time">{formatClock(head.event.ts)}</span>
      <FeedIcon line={head.line} />
      <span className="activity-group-title">
        {head.line.tone && <span className="sr-only">{TONE_LABEL[head.line.tone]}</span>}
        {head.line.text}
      </span>
    </>
  )
  return (
    <div className={`activity-group${tone ? ` activity-group-${tone}` : ''}`}>
      {head && lines.length > 0 && (
        <button
          type="button"
          className="activity-group-head"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => onToggle(group.key, !open)}
          title={head.event.message}
        >
          <ChevronRight size={14} className="activity-group-chevron" aria-hidden="true" />
          {headInner}
          <span className="activity-group-meta">
            {counts.danger > 0 && (
              <span className="activity-count activity-count-danger">{counts.danger} error{counts.danger === 1 ? '' : 's'}</span>
            )}
            {counts.warning > 0 && (
              <span className="activity-count activity-count-warning">{counts.warning} warning{counts.warning === 1 ? '' : 's'}</span>
            )}
            <span className="activity-count">{lines.length} line{lines.length === 1 ? '' : 's'}</span>
          </span>
        </button>
      )}
      {head && lines.length === 0 && (
        <div className="activity-group-head activity-group-static" title={head.event.message}>
          <span className="activity-group-chevron" aria-hidden="true" />
          {headInner}
        </div>
      )}
      {lines.length > 0 && (!head || open) && (
        <div className="activity-group-lines" id={bodyId}>
          {lines.map(({ event, line }) => <FeedLine key={event.seq} event={event} line={line} />)}
        </div>
      )}
    </div>
  )
}

function prefersReducedMotion() {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches
  } catch {
    return false
  }
}

/**
 * The console: the feed as groups, the newest one open and any group holding
 * an error or a warning opened too; a click opens or closes one. It follows
 * the tail unless the user has scrolled up to read something -- yanking them
 * back to the bottom mid-sentence is the fastest way to make a live log
 * useless -- and then offers "Jump to latest". `live` pins a "Live" badge to
 * its top while the job runs.
 */
export function ActivityConsole({ events, live = false }) {
  const boxRef = useRef(null)
  const followRef = useRef(true)
  const [following, setFollowing] = useState(true)
  const [toggled, setToggled] = useState({})
  const groups = useMemo(() => groupEvents(events), [events])

  const onScroll = () => {
    const box = boxRef.current
    if (!box) return
    const distanceFromBottom = box.scrollHeight - box.scrollTop - box.clientHeight
    followRef.current = distanceFromBottom < 40
    setFollowing(followRef.current)
  }

  useEffect(() => {
    const box = boxRef.current
    if (box && followRef.current) box.scrollTop = box.scrollHeight
  }, [events])

  const jumpToLatest = () => {
    const box = boxRef.current
    followRef.current = true
    setFollowing(true)
    if (box) box.scrollTo({ top: box.scrollHeight, behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
  }

  const onToggle = (key, open) => setToggled(previous => ({ ...previous, [key]: open }))
  const lastKey = groups.length ? groups[groups.length - 1].key : null

  return (
    <div className="activity-console-wrap">
      <div
        className="log-viewer activity-console"
        ref={boxRef}
        onScroll={onScroll}
        role="region"
        aria-label="Pipeline output"
        tabIndex={0}
      >
        {live && (
          <div className="activity-console-live">
            <span className="activity-live-badge">
              <span className="activity-pulse running" aria-hidden="true"></span>
              Live
            </span>
          </div>
        )}
        {groups.map(group => {
          const open = group.key in toggled ? toggled[group.key] : (group.key === lastKey || Boolean(group.tone))
          return <FeedGroup key={group.key} group={group} open={open} onToggle={onToggle} />
        })}
      </div>
      {!following && (
        <button type="button" className="activity-jump" onClick={jumpToLatest}>
          <ArrowDownToLine size={13} aria-hidden="true" />
          Jump to latest
        </button>
      )}
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
  const { inStep, inJob } = jobClocks(job, events, running, now)

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

  const toast = useToast()
  const copyLog = async () => {
    const text = events.map(e => `${formatClock(e.ts)}  ${e.message}`).join('\n')
    try {
      await navigator.clipboard.writeText(text)
      toast.success(`Copied ${events.length} line${events.length === 1 ? '' : 's'} of the log.`)
    } catch {
      toast.error('The browser refused the clipboard: select the lines and copy them instead.')
    }
  }

  return (
    <div className="card activity-card">
      <div className="activity-header">
        <span className="activity-title">
          <span className={`activity-pulse ${running ? 'running' : 'idle'}`}></span>
          Live activity
        </span>
        <span className="activity-clocks">
          {inStep && <span title="Time on the current step">{inStep} on this step</span>}
          {inJob && <span className="activity-dim">{inStep ? '· ' : ''}{inJob} total</span>}
        </span>
      </div>

      {/* The one line a screen reader hears change while the job runs (never every log line). */}
      <p className="activity-headline" aria-live={running ? 'polite' : undefined}>
        {progress.message || 'Waiting to start...'}
      </p>
      {progress.detail && progress.detail !== progress.message && (
        <p className="activity-detail">{progress.detail}</p>
      )}

      <div className="activity-chips">
        {progress.provider && (
          <span className="chip chip-accent" title="The AI provider and model being asked">
            <Bot size={12} aria-hidden="true" />
            {progress.provider.toUpperCase()}
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
            <IconButton icon={Copy} size="sm" aria-label="Copy log" onClick={copyLog} />
          </div>
          <ActivityConsole events={events} live={running} />
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
    // No job to watch yet -- the story wizard (stage 11) calls this hook on
    // every render with `myJob ? myJob.id : null`, since a hook cannot be
    // called conditionally. Skip the request rather than asking the API for
    // `/jobs/null` and logging a spurious failure every time nothing is running.
    if (!jobId) {
      setJob(null)
      setEvents([])
      setStreamState('closed')
      return
    }

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
                // A step ends in awaiting_approval, failed or cancelled with
                // no 'completed' frame, and the poll below stops once the
                // status is terminal: fetch the finished job here so the
                // caller's onJob hears about it.
                if (TERMINAL.includes(event.status)) {
                  fetchJob(jobId).then(fresh => {
                    applyJob(fresh)
                    mergeIncoming(fresh.events)
                  }).catch(() => {})
                }
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

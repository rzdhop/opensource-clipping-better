import { useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { Spinner } from '../../../ui'
import { AlertTriangle, Check } from '../../../ui/icons'

// The episode studio's progress stepper (dashboard overhaul stage 4,
// DEC-256): Script -> Storyboard -> Keyframes -> Clips -> Render -> Review,
// each one done / active / pending, read from the episode page the studio
// already holds (GET /stories/{id}/episodes/{ep}) -- never from the job
// feed's log lines. The active step is the first one not done; a step whose
// approval went stale (a keyframe, the assets, the render out of date) keeps
// a warning mark until it is done again.

const REVIEW_STATUS_TEXT = {
  not_made: 'Not made yet',
  keyframes_pending: 'Keyframes to approve',
  assets_pending: 'Assets to approve',
  render_needed: 'Render needed',
  ready: 'Ready',
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? '' : 's'}`
}

/**
 * The six steps of one episode, in order: `[{key, label, status, stale,
 * detail}]`, `status` one of done | active | pending. `reviewTab` is whether
 * the episode has a review screen (a v2 episode: its review carries the
 * keyframe approval); a legacy episode's last step is its metadata instead.
 */
export function episodeSteps(episode, reviewTab) {
  const state = episode.state || {}
  const script = episode.script
  const storyboard = episode.storyboard
  const assets = episode.assets
  const render = episode.render
  const review = episode.review
  const metadata = episode.metadata
  const keyframes = assets && assets.keyframes
  const tier = assets ? assets.tier : 1
  const shots = (assets && assets.shots) || []
  const hasAssetsDoc = Boolean(assets && assets.doc)
  const fingerprint = assets ? assets.fingerprint : 'none'
  const imagesMade = shots.filter((shot) => shot.image_name).length
  const imagesCurrent = hasAssetsDoc && shots.length > 0 && shots.every((shot) => shot.image_name && shot.state === 'current')

  const scriptDone = Boolean(script && script.approved_at)
  const storyboardDone = Boolean(storyboard && storyboard.approved_at)
  const keyframesDone = keyframes ? keyframes.approval === 'current' : (fingerprint === 'current' || imagesCurrent)
  const clipsDone = fingerprint === 'current'
  const renderDone = Boolean(render && render.output && render.state === 'completed' && !render.out_of_date)
  const reviewDone = reviewTab
    ? Boolean(review && review.ready)
    : Boolean(renderDone && metadata && metadata.pack && metadata.current)

  const flagged = review && review.flagged ? review.flagged.length : 0
  const clipsCurrent = shots.filter((shot) => shot.clip && shot.clip.state === 'current').length
  const scriptState = state.script || 'none'
  const storyboardState = state.storyboard || 'none'

  const steps = [
    {
      key: 'script',
      label: 'Script',
      done: scriptDone,
      stale: false,
      detail: scriptDone ? 'Approved'
        : scriptState === 'none' ? 'Not written'
        : scriptState === 'writing' ? 'Writing'
        : 'To approve',
    },
    {
      key: 'storyboard',
      label: 'Storyboard',
      done: storyboardDone,
      stale: (state.stale_scenes || []).length > 0 || Boolean(state.prompts_outdated),
      detail: storyboardDone ? plural(storyboard.shots.length, 'shot')
        : storyboardState === 'none' ? 'Not planned'
        : storyboardState === 'partial' ? 'Partly planned'
        : 'To approve',
    },
    {
      key: 'keyframes',
      label: 'Keyframes',
      done: keyframesDone,
      stale: Boolean(keyframes && keyframes.approval === 'stale'),
      detail: keyframes && keyframes.approval === 'current'
        ? (flagged ? `Approved · ${flagged} flagged` : `Approved${keyframes.anyway ? ' anyway' : ''}`)
        : keyframes && keyframes.approval === 'stale' ? 'Changed since approval'
        : !hasAssetsDoc ? 'Not made'
        : imagesMade < shots.length ? `${imagesMade} of ${shots.length} made`
        : keyframes ? (flagged ? `${flagged} flagged` : 'To approve')
        : 'Made',
    },
    {
      key: 'clips',
      // Tier 1 makes no clips (the stills move with the camera motion): the
      // step is the assets' approval there, named as such.
      label: tier >= 2 ? 'Clips' : 'Assets',
      done: clipsDone,
      stale: fingerprint === 'stale',
      detail: clipsDone ? (tier >= 2 ? plural(clipsCurrent, 'clip') : 'Approved')
        : fingerprint === 'stale' ? 'Changed since approval'
        : tier >= 2 && clipsCurrent > 0 ? `${clipsCurrent} of ${shots.length} made`
        : tier >= 2 ? 'Not made' : 'To approve',
    },
    {
      key: 'render',
      label: 'Render',
      done: renderDone,
      stale: Boolean(render && (render.out_of_date || render.state === 'failed')),
      detail: renderDone ? (render.duration_s != null ? `${render.duration_s.toFixed(1)} s` : 'Rendered')
        : render && render.out_of_date ? 'Out of date'
        : render && render.state === 'failed' ? 'Failed'
        : 'Not rendered',
    },
    {
      key: 'review',
      label: reviewTab ? 'Review' : 'Metadata',
      done: reviewDone,
      stale: false,
      detail: reviewTab
        ? (REVIEW_STATUS_TEXT[review && review.status] || 'Not made yet')
        : reviewDone ? 'Written' : metadata && metadata.pack ? 'Out of date' : 'Not written',
    },
  ]

  const activeIndex = steps.findIndex((step) => !step.done)
  return steps.map((step, index) => ({
    key: step.key,
    label: step.label,
    detail: step.detail,
    stale: step.stale && !step.done,
    status: step.done ? 'done' : index === activeIndex ? 'active' : 'pending',
  }))
}

/**
 * The step a running job works on, from the job's own `step` (never its log
 * lines); the one click (fast-track) and anything unknown mark the active step.
 */
export function stepOfJob(job, steps) {
  if (!job) return null
  const keyframes = steps.find((step) => step.key === 'keyframes')
  const byStep = {
    script: 'script',
    storyboard: 'storyboard',
    assets: keyframes && keyframes.status === 'done' ? 'clips' : 'keyframes',
    render: 'render',
    rerender: 'render',
    metadata: 'review',
  }
  if (byStep[job.step]) return byStep[job.step]
  const active = steps.find((step) => step.status === 'active')
  return active ? active.key : null
}

const STATUS_TEXT = { done: 'Done', active: 'Next', pending: 'To do' }

/**
 * The stepper itself: one button per step (a click selects the matching tab,
 * or scrolls to the matching section on the wide layout), with a done mark,
 * the step's number, or a warning mark when its approval went stale, its
 * one-line state, and a spinner on the step a job is running. It scrolls
 * sideways in its own box on a narrow screen, the active step kept in view.
 * Plan 25 stage 3: `handoffLinks` (`{keyframes, clips}`) marks the nodes
 * where something is the human's to make; each gets a "Handoff →" link to
 * `handoffTo`, the episode's Handoff page.
 */
export default function EpisodeStepper({ steps, runningKey, onSelect, handoffTo = null, handoffLinks = null }) {
  const listRef = useRef(null)
  const activeKey = (steps.find((step) => step.status === 'active') || {}).key

  useEffect(() => {
    const list = listRef.current
    if (!list || list.scrollWidth <= list.clientWidth) return
    const item = list.querySelector('[aria-current="step"]')
    if (item) list.scrollLeft = item.offsetLeft - (list.clientWidth - item.offsetWidth) / 2
  }, [activeKey])

  return (
    <nav className="episode-stepper" aria-label="Episode progress">
      <ol className="episode-stepper-list" ref={listRef}>
        {steps.map((step, index) => {
          const running = runningKey === step.key
          const classes = [
            'episode-stepper-item',
            `episode-stepper-${step.status}`,
            step.stale ? 'episode-stepper-stale' : '',
            running ? 'episode-stepper-running' : '',
          ].filter(Boolean).join(' ')
          return (
            <li key={step.key} className={classes}>
              <button
                type="button"
                className="episode-stepper-node"
                onClick={() => onSelect(step.key)}
                aria-current={step.status === 'active' ? 'step' : undefined}
                title={`${step.label}: ${step.detail}`}
              >
                <span className="episode-stepper-mark" aria-hidden="true">
                  {step.status === 'done' ? <Check size={13} strokeWidth={3} />
                    : step.stale ? <AlertTriangle size={12} />
                    : index + 1}
                </span>
                <span className="episode-stepper-text">
                  <span className="episode-stepper-label">{step.label}</span>
                  <span className="episode-stepper-detail">
                    <span className="sr-only">{STATUS_TEXT[step.status]}: </span>
                    {running ? 'Running…' : step.detail}
                  </span>
                </span>
                {running && <Spinner size={13} className="episode-stepper-spinner" label="A job runs on this step" />}
              </button>
              {handoffTo && handoffLinks && handoffLinks[step.key] && (
                <Link to={handoffTo} className="episode-stepper-handoff"
                  aria-label={`Handoff: the ${step.label.toLowerCase()} that are yours to make`}>
                  Handoff →
                </Link>
              )}
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchEpisode, fetchStory, runStoryStep, fetchStoryEstimate } from '../../api'
import { LiveActivity, useJobFeed } from '../../components/ActivityFeed'
import Tabs from '../../components/Tabs'
import { StepError } from './fields'
import ScriptPane from './episode/ScriptPane'
import StoryboardPane from './episode/StoryboardPane'
import PreviewPane from './episode/PreviewPane'

// Same sub-cent formatting as ScriptPane.jsx's / StoryboardPane.jsx's fmtUsd
// (duplicated: this file shares no component module with the panes).
function fmtUsd(value) {
  const amount = Number(value) || 0
  return amount === 0 ? '0.00' : amount.toFixed(3)
}

const TAB_IDS = ['script', 'storyboard', 'preview']

const IN_FLIGHT = ['queued', 'running']
const EPISODE_POLL_MS = 4000
const WIDE_BREAKPOINT = 1100

function tabFromHash() {
  if (typeof window === 'undefined') return 'script'
  const id = window.location.hash.slice(1)
  return TAB_IDS.includes(id) ? id : 'script'
}

/**
 * Script/Storyboard/Preview, each labelled with its own approval. Phase 5
 * stage 7 let script and storyboard approval diverge (a text-only edit clears
 * the script's while keeping the storyboard's, "storyboard approved, script
 * not approved"), so a single combined "approved" reading of the episode
 * would misstate one pane or the other; each tab shows only its own
 * document's state -- never a contradictory badge (plan 11 stage 11).
 */
function tabsFor(episode) {
  const scriptApproved = Boolean(episode.script && episode.script.approved_at)
  const storyboardApproved = Boolean(episode.storyboard && episode.storyboard.approved_at)
  return [
    { id: 'script', label: `Script${scriptApproved ? ' ✓' : ''}` },
    { id: 'storyboard', label: `Storyboard${storyboardApproved ? ' ✓' : ''}` },
    { id: 'preview', label: 'Preview' },
  ]
}

/** Three panes side by side at >=1100px; Tabs below that -- tracked with
 * matchMedia (not CSS alone) so only one layout is ever mounted: a pane
 * hidden by CSS would still hold a live job feed and poll the episode. */
function useIsWide(breakpoint) {
  const [wide, setWide] = useState(() => (
    typeof window !== 'undefined' ? window.innerWidth >= breakpoint : true
  ))
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mq = window.matchMedia(`(min-width: ${breakpoint}px)`)
    const onChange = () => setWide(mq.matches)
    onChange()
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [breakpoint])
  return wide
}

/**
 * The header's "Fast track" button (spec 3 steps 10-12 + the fast track,
 * stage 14): script -> storyboard -> assets -> render -> metadata as one
 * job, stopping before any paid spending unless it is allowed and every cap
 * fits. The confirm dialog lists the estimate's split (GET
 * /estimate/fast-track) before the click starts anything.
 */
function FastTrackHeader({ storyId, ep, busy, onChange }) {
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  // As every other estimate-driven header (stage-10 lesson, browser-check
  // finding): a 409 must show its own sentence, not leave the button
  // disabled with nothing said about why (fast_track.estimate does refuse a
  // document that fails to validate, StepFailed -> conflict).
  const [estimateError, setEstimateError] = useState('')

  useEffect(() => {
    setEstimate(null)
    setEstimateError('')
    fetchStoryEstimate(storyId, 'fast-track', { ep, storyboard: 't1' })
      .then((data) => { setEstimate(data); setEstimateError('') })
      .catch((err) => { setEstimate(null); setEstimateError(err.message) })
  }, [storyId, ep])

  const confirmMessage = (est) => [
    `Fast track episode ${ep} to a finished render?`,
    `LLM calls: ${est.llm_calls.total} (script ${est.llm_calls.script}, storyboard ${est.llm_calls.storyboard}, ` +
      `metadata ${est.llm_calls.metadata})`,
    `Images: ${est.images.count} shot${est.images.count === 1 ? '' : 's'}, est. $${fmtUsd(est.images.est_usd)}`,
    `Voices: ${est.tts.lines} line${est.tts.lines === 1 ? '' : 's'} / ${est.tts.chars} chars, ` +
      `est. $${fmtUsd(est.tts.est_usd)}`,
    `Render: about ${est.render.minutes} min`,
    `Total: est. $${fmtUsd(est.est_usd)}`,
    'It stops before any paid spending, unless paid generation is allowed and every cap fits.',
  ].join('\n')

  const handleRun = async () => {
    if (!estimate || !window.confirm(confirmMessage(estimate))) return
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      const fastTrackParams = { storyboard: 't1' }
      await runStoryStep(storyId, 'fast-track', { ep, params: fastTrackParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="episode-studio-fast-track">
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-secondary"
          onClick={handleRun}
          disabled={busy || running || !estimate || Boolean(estimateError)}
        >
          {running ? <><span className="spinner"></span> Fast tracking…</> : 'Fast track'}
        </button>
        {estimateError ? (
          <span className="chip chip-warn chip-wrap">{estimateError}</span>
        ) : estimate && (
          <span className="chip" title={estimate.message || ''}>est. ${fmtUsd(estimate.est_usd)} total</span>
        )}
      </div>
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

export default function EpisodeStudio() {
  const { storyId, ep } = useParams()
  const epNumber = Number(ep)
  const [story, setStory] = useState(null)
  const [episode, setEpisode] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [tab, setTab] = useState(tabFromHash)
  const wide = useIsWide(WIDE_BREAKPOINT)
  // The last job that stopped with a reason worth reading, kept on screen
  // past the moment it leaves episode.jobs (phase-4 follow-up, plan 11 stage
  // 11): episode.jobs lists only queued/running work, so a fast-track (or
  // any step) that fails clears inFlightJob the instant the page refreshes
  // -- which used to erase its own reason from the screen at the same
  // moment, leaving only the job's own (by then unreachable) feed to have
  // ever shown it.
  const [stoppedJob, setStoppedJob] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const [storyData, episodeData] = await Promise.all([fetchStory(storyId), fetchEpisode(storyId, ep)])
      setStory(storyData)
      setEpisode(episodeData)
      setLoadError('')
    } catch (err) {
      setLoadError(err.message)
    } finally {
      setLoading(false)
    }
  }, [storyId, ep])

  useEffect(() => {
    setLoading(true)
    setEpisode(null)
    refresh()
  }, [storyId, ep, refresh])

  const onTabChange = (id) => {
    setTab(id)
    if (typeof window !== 'undefined') window.location.hash = id
  }

  // While a job of this episode's documents is queued or running, poll the
  // episode page -- same reasoning as the wizard (ActivityFeed's stream can
  // miss the moment a step finishes).
  const inFlightJob = episode ? (episode.jobs || []).find((j) => IN_FLIGHT.includes(j.status)) || null : null
  useEffect(() => {
    if (!inFlightJob) return
    const timer = setInterval(refresh, EPISODE_POLL_MS)
    return () => clearInterval(timer)
  }, [inFlightJob, refresh])

  // A new job starting (a fresh id in episode.jobs) means whatever the
  // previous one stopped with is no longer this page's news.
  const inFlightJobId = inFlightJob ? inFlightJob.id : null
  useEffect(() => {
    if (inFlightJobId) setStoppedJob(null)
  }, [inFlightJobId])

  const { job: liveJob, events, streamState } = useJobFeed(inFlightJobId, {
    onJob: (job) => {
      // Captured before the refresh below can drop it from episode.jobs and
      // null out inFlightJob -- the same render that hides the live feed
      // must not also erase the one sentence that explains why it stopped.
      if (job && job.status === 'failed' && job.error) setStoppedJob(job)
      if (job && job.status !== 'queued' && job.status !== 'running') refresh()
    },
  })

  if (loading) {
    return <div className="fade-in"><div className="empty-state"><span className="spinner"></span></div></div>
  }
  if (loadError) {
    return <div className="fade-in"><div className="card"><p className="story-error">{loadError}</p></div></div>
  }
  if (!story || !episode) return null

  const arcEntry = ((story.season && story.season.arc) || []).find((entry) => entry.ep === epNumber)
  const tabs = tabsFor(episode)

  const panes = {
    script: (
      <ScriptPane
        episode={episode}
        storyDoc={story.story}
        characters={story.characters}
        places={story.places}
        episodes={story.episodes}
        storyId={storyId}
        ep={epNumber}
        inFlightJob={inFlightJob}
        onChange={refresh}
      />
    ),
    storyboard: (
      <StoryboardPane
        episode={episode}
        characters={story.characters}
        places={story.places}
        props={story.props}
        storyId={storyId}
        ep={epNumber}
        inFlightJob={inFlightJob}
        onChange={refresh}
      />
    ),
    preview: (
      <PreviewPane
        episode={episode}
        story={story}
        storyId={storyId}
        ep={epNumber}
        inFlightJob={inFlightJob}
        onChange={refresh}
      />
    ),
  }

  return (
    <div className="fade-in episode-studio">
      <div className="page-header episode-studio-header">
        <div>
          <Link to={`/story/${storyId}`} className="episode-studio-back">← {story.story.title || 'Untitled story'}</Link>
          <h2>Episode {ep}</h2>
          {arcEntry && <p>{arcEntry.summary}</p>}
        </div>
        <FastTrackHeader storyId={storyId} ep={epNumber} busy={Boolean(inFlightJob)} onChange={refresh} />
      </div>

      {inFlightJob && liveJob ? (
        liveJob.status === 'queued'
          ? <p className="form-hint">queued — waiting for the worker</p>
          : <LiveActivity job={liveJob} events={events} streamState={streamState} />
      ) : stoppedJob && (
        // The stop reason, visible on this first screen without scrolling
        // (phase-4 follow-up, plan 11 stage 11) -- kept until a new step
        // starts (the effect above) or the human dismisses it.
        <div className="card episode-studio-stopped">
          <StepError message={`${stoppedJob.step || 'The step'} stopped: ${stoppedJob.error}`} />
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setStoppedJob(null)}>
            Dismiss
          </button>
        </div>
      )}

      {wide ? (
        <div className="episode-studio-panes">
          <div className="episode-studio-pane">
            <h3 className="card-title episode-studio-pane-heading">{tabs[0].label}</h3>
            {panes.script}
          </div>
          <div className="episode-studio-pane">
            <h3 className="card-title episode-studio-pane-heading">{tabs[1].label}</h3>
            {panes.storyboard}
          </div>
          <div className="episode-studio-pane">
            <h3 className="card-title episode-studio-pane-heading">{tabs[2].label}</h3>
            {panes.preview}
          </div>
        </div>
      ) : (
        <div className="episode-studio-tabs">
          <Tabs tabs={tabs} active={tab} onChange={onTabChange}>
            {(id) => panes[id]}
          </Tabs>
        </div>
      )}
    </div>
  )
}

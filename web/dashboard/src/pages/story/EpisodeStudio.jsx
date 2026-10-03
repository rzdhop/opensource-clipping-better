import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchEpisode, fetchStory, runStoryStep, fetchStoryEstimate } from '../../api'
import { LiveActivity, useJobFeed } from '../../components/ActivityFeed'
import Tabs from '../../components/Tabs'
import { StepError } from './fields'
import ScriptPane from './episode/ScriptPane'
import StoryboardPane from './episode/StoryboardPane'
import PreviewPane from './episode/PreviewPane'
import ReviewPane from './episode/ReviewPane'

// Same sub-cent formatting as ScriptPane.jsx's / StoryboardPane.jsx's fmtUsd
// (duplicated: this file shares no component module with the panes).
function fmtUsd(value) {
  const amount = Number(value) || 0
  return amount === 0 ? '0.00' : amount.toFixed(3)
}

// A cap is a round figure: two decimals, as the fast track's own caps line.
function fmtCap(value) {
  return (Number(value) || 0).toFixed(2)
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? '' : 's'}`
}

const TAB_IDS = ['script', 'storyboard', 'preview', 'review']

const IN_FLIGHT = ['queued', 'running']
const EPISODE_POLL_MS = 4000
const WIDE_BREAKPOINT = 1100

// The line the fast track prints for each sub-step ("⏩ Fast track 3/6: paid
// check", clipping.aistory.steps.fast_track.LABELS): the header shows the
// latest one while the job runs (stage C).
const FAST_TRACK_STEP_LINE = /⏩ Fast track (\d+)\/(\d+): (.+)/

function tabFromHash() {
  if (typeof window === 'undefined') return 'script'
  const id = window.location.hash.slice(1)
  return TAB_IDS.includes(id) ? id : 'script'
}

/** Whether the episode has a review screen (stage C): a v2 episode's
 * review carries its keyframe approval; a legacy episode keeps the three
 * panes it always had. */
function hasReview(episode) {
  return Boolean(episode.review && episode.review.approvals && episode.review.approvals.keyframes)
}

/**
 * Script/Storyboard/Preview, each labelled with its own approval. Phase 5
 * stage 7 let script and storyboard approval diverge (a text-only edit clears
 * the script's while keeping the storyboard's, "storyboard approved, script
 * not approved"), so a single combined "approved" reading of the episode
 * would misstate one pane or the other; each tab shows only its own
 * document's state -- never a contradictory badge (plan 11 stage 11). The
 * Review tab (stage C) joins them, last, on a v2 episode -- the three panes
 * keep their places (the wide layout reads tabs[0..2] for its headings); it
 * is where "Generate episode" ends, switched to when the job does.
 */
function tabsFor(episode) {
  const scriptApproved = Boolean(episode.script && episode.script.approved_at)
  const storyboardApproved = Boolean(episode.storyboard && episode.storyboard.approved_at)
  const tabs = [
    { id: 'script', label: `Script${scriptApproved ? ' ✓' : ''}` },
    { id: 'storyboard', label: `Storyboard${storyboardApproved ? ' ✓' : ''}` },
    { id: 'preview', label: 'Preview' },
  ]
  if (hasReview(episode)) tabs.push({ id: 'review', label: `Review${episode.review.ready ? ' ✓' : ''}` })
  return tabs
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

/** The latest sub-step the fast track's feed named, or null. */
function fastTrackProgress(events) {
  for (let i = (events || []).length - 1; i >= 0; i--) {
    const match = FAST_TRACK_STEP_LINE.exec(events[i].message || '')
    if (match) return { number: Number(match[1]), total: Number(match[2]), label: match[3] }
  }
  return null
}

/**
 * The header's "Generate episode" button (spec 3 steps 10-12 + the fast
 * track, stage 14; stage C: one click up to the finished render): script ->
 * storyboard -> paid check -> assets -> render -> metadata as one job,
 * stopping before any paid spending unless it is allowed and every cap fits.
 * On a v2 episode the keyframes are checked (J2) and auto-fixed, then
 * approved by the run itself with the assets -- the click is the approval,
 * which the confirm says in words -- unless "Stop at the keyframes" is
 * ticked (the fast track's `stop_at_keyframes`). The confirm dialog lists the
 * estimate's split (GET /estimate/fast-track) before the click starts
 * anything; while the job runs the button names the sub-step its feed
 * reports (`job`, `events`: the in-flight fast-track job and its feed).
 */
function FastTrackHeader({ storyId, ep, busy, job, events, onChange }) {
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [stopAtKeyframes, setStopAtKeyframes] = useState(false)
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

  // "this episode $0.96 of $4.00, today ..., this story ...", the fast track's own caps line.
  const capsText = (caps) => [['episode', 'this episode'], ['day', 'today'], ['story', 'this story']]
    .filter(([name]) => caps && caps[name])
    .map(([name, label]) => `${label} $${fmtCap(caps[name].spent_usd)} of $${fmtCap(caps[name].cap_usd)}`)
    .join(', ')

  // What the click does and costs, line by line, from the estimate's own
  // shape (fast_track.estimate): the keyframes block (stage C) says whether
  // this is a v2 episode whose keyframes are checked, auto-fixed and
  // approved by the run itself.
  const confirmMessage = (est, stop) => {
    const kf = est.keyframes || {}
    const v2 = Boolean(kf.v2)
    const fix = Number(kf.fix_usd) || 0
    const lines = [
      `Generate episode ${ep} — the whole thing, up to the finished render?`,
      `Script: ${plural(est.llm_calls.script, 'LLM call')} · Storyboard: ${plural(est.llm_calls.storyboard, 'T1 call')}` +
        ` · Metadata: ${plural(est.llm_calls.metadata, 'M1 call')}`,
      `${v2 ? 'Keyframes' : 'Images'}: ${plural(est.images.count, 'shot image')}, est. $${fmtUsd(est.images.est_usd)}` +
        (v2 ? ` — each checked (J2)${fix > 0
          ? `, flagged ones redrawn automatically (up to $${fmtUsd(fix)} more)`
          : ', flagged ones redrawn automatically when the profile allows it'}` : ''),
      `Voices: ${plural(est.tts.lines, 'line')} / ${est.tts.chars} chars, est. $${fmtUsd(est.tts.est_usd)}`,
    ]
    if (est.video) {
      lines.push(est.video.count != null
        ? `Clips: ${plural(est.video.count, 'clip')} (${est.video.seconds} s) on ${est.video.link || 'the video link'}, ` +
          `est. $${fmtUsd(est.video.est_usd)}`
        : 'Clips: planned once the storyboard is approved; the paid check prices them before any is bought')
    }
    lines.push(`Render: about ${est.render.minutes} min`)
    const caps = capsText(est.paid.caps)
    lines.push(`Total: est. $${fmtUsd(est.est_usd)}${caps ? ` — caps: ${caps}` : ''}`)
    if (v2 && kf.tier >= 2) {
      lines.push(stop
        ? 'It stops once the keyframes are made and checked (J2), for your review; the clips are bought after you approve them.'
        : 'No stop for keyframe review — this click approves the keyframes and the assets for you; you review the finished episode.')
    }
    lines.push('It stops before any paid spending, unless paid generation is allowed and every cap fits.')
    return lines.join('\n')
  }

  const handleRun = async () => {
    if (!estimate || !window.confirm(confirmMessage(estimate, stopAtKeyframes))) return
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      const fastTrackParams = { storyboard: 't1', stop_at_keyframes: stopAtKeyframes }
      await runStoryStep(storyId, 'fast-track', { ep, params: fastTrackParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  const progress = job ? fastTrackProgress(events) : null
  const generating = Boolean(job) || running

  return (
    <div className="episode-studio-fast-track">
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleRun}
          disabled={busy || running || !estimate || Boolean(estimateError)}
        >
          {generating ? (
            <>
              <span className="spinner"></span> Generating…
              {progress ? ` ${progress.number}/${progress.total} ${progress.label}` : ''}
            </>
          ) : 'Generate episode'}
        </button>
        {estimateError ? (
          <span className="chip chip-warn chip-wrap">{estimateError}</span>
        ) : estimate && (
          <span className="chip" title={estimate.message || ''}>est. ${fmtUsd(estimate.est_usd)} total</span>
        )}
      </div>
      {progress && (
        <div className="progress-bar-bg episode-studio-fast-track-bar" title={`${progress.number} of ${progress.total}: ${progress.label}`}>
          <div className="progress-bar-fill" style={{ width: `${Math.round(((progress.number - 0.5) / progress.total) * 100)}%` }}></div>
        </div>
      )}
      <label className="story-checkbox episode-studio-stop">
        <input
          type="checkbox"
          checked={stopAtKeyframes}
          onChange={(e) => setStopAtKeyframes(e.target.checked)}
          disabled={busy || running}
        />
        Stop at the keyframes for my review
      </label>
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
  // Stage C: a fast track that ended -- the finished episode, or its stop --
  // is read on the review screen: switch (or scroll) to it once the page
  // has refreshed.
  const [reviewAfterJob, setReviewAfterJob] = useState(false)

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
      if (job && job.status !== 'queued' && job.status !== 'running') {
        if (job.step === 'fast-track') setReviewAfterJob(true)
        refresh()
      }
    },
  })

  useEffect(() => {
    if (!reviewAfterJob || !episode || inFlightJob) return
    setReviewAfterJob(false)
    if (!hasReview(episode)) return
    if (wide) {
      const section = document.getElementById('episode-review')
      if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' })
    } else {
      onTabChange('review')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reviewAfterJob, episode, inFlightJob, wide])

  if (loading) {
    return <div className="fade-in"><div className="empty-state"><span className="spinner"></span></div></div>
  }
  if (loadError) {
    return <div className="fade-in"><div className="card"><p className="story-error">{loadError}</p></div></div>
  }
  if (!story || !episode) return null

  const arcEntry = ((story.season && story.season.arc) || []).find((entry) => entry.ep === epNumber)
  const tabs = tabsFor(episode)
  const activeTab = tabs.some((entry) => entry.id === tab) ? tab : 'script'
  const fastTrackJob = inFlightJob && inFlightJob.step === 'fast-track' ? inFlightJob : null

  const panes = {
    review: (
      <div id="episode-review" className="episode-studio-review">
        <ReviewPane
          episode={episode}
          characters={story.characters}
          storyId={storyId}
          ep={epNumber}
          inFlightJob={inFlightJob}
          onChange={refresh}
        />
      </div>
    ),
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
        <FastTrackHeader storyId={storyId} ep={epNumber} busy={Boolean(inFlightJob)} job={fastTrackJob}
          events={events} onChange={refresh} />
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
        <>
          {/* Stage C: the review screen above the three panes, full width. */}
          {hasReview(episode) && panes.review}
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
        </>
      ) : (
        <div className="episode-studio-tabs">
          <Tabs tabs={tabs} active={activeTab} onChange={onTabChange}>
            {(id) => panes[id]}
          </Tabs>
        </div>
      )}
    </div>
  )
}

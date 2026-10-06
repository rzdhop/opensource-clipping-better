import { useCallback, useEffect, useState } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { fetchEpisode, fetchHandoff, fetchStory, runStoryStep, fetchStoryEstimate } from '../../api'
import { LiveActivity, useJobFeed } from '../../components/ActivityFeed'
import Tabs from '../../components/Tabs'
import { StepError } from './fields'
import { formatUsd } from '../../lib/format'
import { useConfirm } from '../../ui'
import ScriptPane from './episode/ScriptPane'
import StoryboardPane from './episode/storyboard/StoryboardPane'
import PreviewPane from './episode/PreviewPane'
import ReviewPane from './episode/ReviewPane'
import EpisodeApproveAll from './episode/EpisodeApproveAll'
import { handoffLinks, handoffPath } from './episode/HandoffPage'
import { GenerateClipsButton, GenerateWarnings } from './episode/HandoffCard'
import EpisodeStepper, { episodeSteps, stepOfJob } from './episode/EpisodeStepper'
import { imagesManual } from './ManualUploadSlot'
import { jobLabel } from './storySteps'
import MoreFold from './MoreFold'

// A cap is a round figure: two decimals, as the fast track's own caps line.
function fmtCap(value) {
  return (Number(value) || 0).toFixed(2)
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? '' : 's'}`
}

// 'shots' is the Handoff (plan 25 stage 3, DEC-301 retired the Shot list): the
// tab and a #shots link open its own route, /story/:id/episodes/:ep/handoff.
const TAB_IDS = ['script', 'storyboard', 'preview', 'review', 'shots']

// Plan 22 stage 5: the budget profile whose clips are the user's own uploads.
const MANUAL_PROFILE = 'native_speech_manual'
const MANUAL_LINK = 'manual/upload'

/** Whether the episode's clips are the user's own (the manual link): the
 * story's profile, or the speech link its clips estimate names. */
export function clipsManual(storyDoc, episode) {
  const profile = storyDoc && storyDoc.generation_profile
  if (profile && profile.budget_profile === MANUAL_PROFILE) return true
  const speech = episode && episode.assets && episode.assets.video && episode.assets.video.speech
  return Boolean(speech && speech.speech_link === MANUAL_LINK)
}

/** The episode's job paused awaiting the user's clips (status `awaiting_uploads`), or null. */
export function pausedJobOf(episode) {
  return ((episode && episode.jobs) || []).find((job) => job.status === 'awaiting_uploads') || null
}

const IN_FLIGHT = ['queued', 'running']
const EPISODE_POLL_MS = 4000
const WIDE_BREAKPOINT = 1100

// The line the fast track prints for each sub-step ("⏩ Fast track 3/6: paid
// check", clipping.aistory.steps.fast_track.LABELS): the header shows the
// latest one while the job runs (stage C).
const FAST_TRACK_STEP_LINE = /⏩ Fast track (\d+)\/(\d+): (.+)/

// Where each stepper node leads (dashboard overhaul stage 4, DEC-256): the
// tab it selects below 1100 px, and the section it scrolls to -- on the wide
// layout every section is on screen, so a click only scrolls. Keyframes and
// Clips are cards inside the Storyboard tab, so they carry a hash of their
// own (#keyframes, #clips) that opens that tab at that card, on a reload too.
const STEP_TARGETS = {
  script: { tab: 'script', anchor: 'episode-pane-script' },
  storyboard: { tab: 'storyboard', anchor: 'episode-pane-storyboard' },
  keyframes: { tab: 'storyboard', anchor: 'episode-keyframes', hash: 'keyframes' },
  clips: { tab: 'storyboard', anchor: 'episode-clips', hash: 'clips' },
  render: { tab: 'preview', anchor: 'episode-pane-preview' },
  review: { tab: 'review', anchor: 'episode-review' },
}
const HASH_TARGETS = { keyframes: STEP_TARGETS.keyframes, clips: STEP_TARGETS.clips }

function tabFromHash() {
  if (typeof window === 'undefined') return 'script'
  const id = window.location.hash.slice(1)
  if (HASH_TARGETS[id]) return HASH_TARGETS[id].tab
  return TAB_IDS.includes(id) ? id : 'script'
}

/** The card a #keyframes / #clips hash names, to scroll to once the page has loaded. */
function anchorFromHash() {
  if (typeof window === 'undefined') return null
  const target = HASH_TARGETS[window.location.hash.slice(1)]
  return target ? target.anchor : null
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

/** Panes side by side at >=1100px; Tabs below that -- tracked with
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

/** "Waiting for 5 clips" (and keyframes, when the images are the user's own too). */
export function waitingLabel(uploads) {
  const clips = uploads.clips != null ? uploads.clips : uploads.count
  const keyframes = uploads.keyframes || 0
  const parts = []
  if (keyframes) parts.push(plural(keyframes, 'keyframe'))
  if (clips || !keyframes) parts.push(plural(clips, 'clip'))
  return `Waiting for ${parts.join(' and ')}`
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
 * ticked (the fast track's `stop_at_keyframes`). Plan 19 stage 3: once the
 * script step's repair passes are spent, the run approves the script anyway
 * over the blocking issues they could not fix, naming them for the review --
 * unless "Stop at the script" is ticked (`stop_on_script_issues`). The confirm dialog lists the
 * estimate's split (GET /estimate/fast-track) before the click starts
 * anything; while the job runs the button names the sub-step its feed
 * reports (`job`, `events`: the in-flight fast-track job and its feed).
 */
function FastTrackHeader({ storyId, ep, busy, job, events, paused, handoff, approveAll, onChange }) {
  const confirm = useConfirm()
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [stopAtKeyframes, setStopAtKeyframes] = useState(false)
  const [stopOnScriptIssues, setStopOnScriptIssues] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  const [errorCode, setErrorCode] = useState(null)
  const [errorDetail, setErrorDetail] = useState(null)
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
  const confirmMessage = (est, stop, scriptStop) => {
    const kf = est.keyframes || {}
    const v2 = Boolean(kf.v2)
    const fix = Number(kf.fix_usd) || 0
    const lines = [
      `Make episode ${ep} — the whole thing, up to the finished video?`,
      `Writing: script ${plural(est.llm_calls.script, 'step')} · shot plan ${plural(est.llm_calls.storyboard, 'step')}` +
        ` · title and description ${plural(est.llm_calls.metadata, 'step')}`,
      `${v2 ? 'Keyframes' : 'Images'}: ${plural(est.images.count, 'shot image')}, est. $${formatUsd(est.images.est_usd)}` +
        (v2 ? ` — each one checked${fix > 0
          ? `, flagged ones redrawn automatically (up to $${formatUsd(fix)} more)`
          : ', flagged ones redrawn automatically when the spending plan allows it'}` : ''),
      `Voices: ${plural(est.tts.lines, 'line')} / ${est.tts.chars} chars, est. $${formatUsd(est.tts.est_usd)}`,
    ]
    if (est.video) {
      lines.push(est.video.count != null
        ? `Clips: ${plural(est.video.count, 'clip')} (${est.video.seconds} s), ` +
          `est. $${formatUsd(est.video.est_usd)}${est.video.basis === 'plan' ? ' (from the plan)' : ''}`
        : 'Clips: planned once the shot plan is approved; their price is checked before any is bought')
    }
    lines.push(`Render: about ${est.render.minutes} min`)
    const caps = capsText(est.paid.caps)
    lines.push(`Total: est. $${formatUsd(est.est_usd)}${caps ? ` — caps: ${caps}` : ''}`)
    if (v2 && kf.tier >= 2) {
      lines.push(stop
        ? 'It stops once the keyframes are made and checked, for your review; the clips are bought after you approve them.'
        : 'No stop for keyframe review — this click approves the keyframes (when every check passes; a shot that does not match stops it) and the assets for you; you review the finished episode.')
    }
    if (v2) {
      lines.push(scriptStop
        ? 'It stops at the script if its checks still find blocking issues after the repair passes.'
        : 'Script issues the repair passes cannot fix are approved anyway and named for your review.')
    }
    for (const warning of est.warnings || []) lines.push(`Warning: ${warning}`)
    lines.push('It stops before any paid spending, unless paid generation is allowed and every cap fits.')
    return lines.join('\n')
  }

  const handleRun = async () => {
    if (!estimate) return
    const confirmed = await confirm({
      title: `Make episode ${ep}`,
      message: confirmMessage(estimate, stopAtKeyframes, stopOnScriptIssues),
      confirmLabel: `Make episode ${ep}`,
    })
    if (!confirmed) return
    setRunning(true)
    setError('')
    setErrors(null)
    setErrorCode(null)
    setErrorDetail(null)
    try {
      const fastTrackParams = { storyboard: 't1', stop_at_keyframes: stopAtKeyframes, stop_on_script_issues: stopOnScriptIssues }
      await runStoryStep(storyId, 'fast-track', { ep, params: fastTrackParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
      setErrorCode(err.code || null)
      setErrorDetail(err.detail || null)
    } finally {
      setRunning(false)
    }
  }

  const progress = job ? fastTrackProgress(events) : null
  const generating = Boolean(job) || running
  // Plan 22 stage 5: a run paused for the user's own clips goes on by itself once they are uploaded.
  const waiting = paused && paused.uploads && paused.uploads.count > 0 ? paused.uploads : null

  return (
    <div className="episode-studio-fast-track">
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleRun}
          disabled={busy || running || !estimate || Boolean(estimateError) || Boolean(waiting)}
        >
          {generating ? (
            <>
              <span className="spinner"></span> Making…
              {progress ? ` ${progress.number}/${progress.total} ${progress.label}` : ''}
            </>
          ) : waiting ? waitingLabel(waiting) : `Make episode ${ep}`}
        </button>
        {approveAll}
        {estimateError ? (
          <span className="chip chip-warn chip-wrap">{estimateError}</span>
        ) : estimate && (
          <span className="chip" title={estimate.message || ''}>est. ${formatUsd(estimate.est_usd)} total</span>
        )}
      </div>
      {/* Plan 28 A6: what would make the click fail or check less, said before it. */}
      <GenerateWarnings warnings={estimate && estimate.warnings} />
      {/* Plan 28 A6: your own clips still missing, bought only on this click, at the price it shows. */}
      {handoff && !generating && (
        <GenerateClipsButton storyId={storyId} ep={ep} price={handoff.generate_price} onDone={onChange} />
      )}
      {progress && (
        <div className="progress-bar-bg episode-studio-fast-track-bar" title={`${progress.number} of ${progress.total}: ${progress.label}`}>
          <div className="progress-bar-fill" style={{ width: `${Math.round(((progress.number - 0.5) / progress.total) * 100)}%` }}></div>
        </div>
      )}
      <MoreFold>
        <label className="story-checkbox episode-studio-stop">
          <input
            type="checkbox"
            checked={stopAtKeyframes}
            onChange={(e) => setStopAtKeyframes(e.target.checked)}
            disabled={busy || running}
          />
          Stop at the keyframes for my review
        </label>
        <label className="story-checkbox episode-studio-stop">
          <input
            type="checkbox"
            checked={stopOnScriptIssues}
            onChange={(e) => setStopOnScriptIssues(e.target.checked)}
            disabled={busy || running}
          />
          Stop at the script if its repairs leave issues
        </label>
      </MoreFold>
      <StepError message={error} errors={errors} code={errorCode} detail={errorDetail}
        storyId={storyId} retryLabel={`Make episode ${ep}`} className="story-step-error" />
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
  // A section a stepper click (or a #keyframes / #clips hash) asked to
  // scroll to, once the tab holding it has rendered.
  const [pendingAnchor, setPendingAnchor] = useState(anchorFromHash)
  const navigate = useNavigate()
  // A #shots link (the Shot list's old deep link) opens the Handoff instead.
  const [shotsLink] = useState(() => typeof window !== 'undefined' && window.location.hash === '#shots')
  // The handoff document, read for the stepper's "Handoff →" links (what is the human's).
  const [handoffDoc, setHandoffDoc] = useState(null)

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
    if (id === 'shots') {
      navigate(handoffPath(storyId, ep))
      return
    }
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

  // Read again when the episode gains a storyboard and whenever a job ends.
  const hasStoryboard = Boolean(episode && episode.storyboard)
  useEffect(() => {
    if (!hasStoryboard || inFlightJobId || shotsLink) return undefined
    let cancelled = false
    fetchHandoff(storyId, ep)
      .then((body) => { if (!cancelled) setHandoffDoc(body) })
      .catch(() => { if (!cancelled) setHandoffDoc(null) })
    return () => { cancelled = true }
  }, [storyId, ep, hasStoryboard, inFlightJobId, shotsLink])

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

  useEffect(() => {
    if (!pendingAnchor || !episode) return undefined
    const frame = window.requestAnimationFrame(() => {
      const section = document.getElementById(pendingAnchor)
      if (section) section.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setPendingAnchor(null)
    })
    return () => window.cancelAnimationFrame(frame)
  }, [pendingAnchor, episode, tab, wide])

  if (shotsLink) return <Navigate to={handoffPath(storyId, ep)} replace />
  if (loading) {
    return <div className="fade-in"><div className="empty-state"><span className="spinner"></span></div></div>
  }
  if (loadError) {
    return <div className="fade-in"><div className="card"><p className="story-error">{loadError}</p></div></div>
  }
  if (!story || !episode) return null

  const arcEntry = ((story.season && story.season.arc) || []).find((entry) => entry.ep === epNumber)
  const manual = clipsManual(story.story, episode)
  const pausedJob = pausedJobOf(episode)
  const tabs = tabsFor(episode)
  // Plan 25 stage 3: the Handoff (prompts, modes and uploads, shot by shot) -- last, so the three
  // panes keep their places; the tab opens its own page.
  if (episode.storyboard) tabs.push({ id: 'shots', label: 'Handoff →' })
  // The stepper's Keyframes / Clips nodes link to it when something there is the human's; until the
  // document is read, the story's own profile says so.
  const handoffNodes = handoffDoc ? handoffLinks(handoffDoc)
    : { keyframes: imagesManual(story.story), clips: manual }
  const activeTab = tabs.some((entry) => entry.id === tab) ? tab : 'script'
  const fastTrackJob = inFlightJob && inFlightJob.step === 'fast-track' ? inFlightJob : null
  const steps = episodeSteps(episode, hasReview(episode))

  // A stepper click: the matching tab (and card) below 1100 px; on the wide
  // layout, where every pane is on screen, a scroll to the matching section.
  const selectStep = (key) => {
    const target = STEP_TARGETS[key === 'review' && !hasReview(episode) ? 'render' : key]
    if (!target) return
    if (!wide) {
      setTab(target.tab)
      if (typeof window !== 'undefined') window.location.hash = target.hash || target.tab
      if (!target.hash) return
    }
    setPendingAnchor(target.anchor)
  }

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
          events={events} paused={pausedJob} handoff={handoffDoc} onChange={refresh}
          approveAll={<EpisodeApproveAll storyId={storyId} ep={epNumber} episode={episode} busy={Boolean(inFlightJob)}
            onChange={refresh} inline />} />
      </div>

      <EpisodeStepper steps={steps} runningKey={stepOfJob(inFlightJob, steps)} onSelect={selectStep}
        handoffTo={episode.storyboard ? handoffPath(storyId, ep) : null} handoffLinks={handoffNodes} />

      {inFlightJob && liveJob ? (
        liveJob.status === 'queued'
          ? <p className="form-hint">Waiting to start…</p>
          : <LiveActivity job={liveJob} events={events} streamState={streamState} />
      ) : stoppedJob && (
        // The stop reason, visible on this first screen without scrolling
        // (phase-4 follow-up, plan 11 stage 11) -- kept until a new step
        // starts (the effect above) or the human dismisses it.
        <div className="card episode-studio-stopped">
          <StepError message={`${jobLabel(stoppedJob)} stopped: ${stoppedJob.error}`} />
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setStoppedJob(null)}>
            Dismiss
          </button>
        </div>
      )}

      {wide ? (
        <>
          {/* Stage C: the review screen above the three panes, full width. */}
          {hasReview(episode) && panes.review}
          {/* Stage 4 (DEC-256): Script | Storyboard side by side, the Preview
              (render, metadata, ledger) full width under them. */}
          <div className="episode-studio-panes">
            <section className="episode-studio-pane" id="episode-pane-script">
              <h3 className="card-title episode-studio-pane-heading">{tabs[0].label}</h3>
              {panes.script}
            </section>
            <section className="episode-studio-pane" id="episode-pane-storyboard">
              <h3 className="card-title episode-studio-pane-heading">{tabs[1].label}</h3>
              {panes.storyboard}
            </section>
          </div>
          {episode.storyboard && (
            <p className="episode-studio-handoff" id="episode-pane-shots">
              <Link to={handoffPath(storyId, ep)}>Handoff →</Link>
              <span className="form-hint"> the prompts to copy and the clips to upload, shot by shot</span>
            </p>
          )}
          <section className="episode-studio-pane episode-studio-pane-wide" id="episode-pane-preview">
            <h3 className="card-title episode-studio-pane-heading">{tabs[2].label}</h3>
            {panes.preview}
          </section>
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

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import { fetchStory, fetchStyles } from '../../api'
import { LiveActivity, useJobFeed } from '../../components/ActivityFeed'
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, Skeleton } from '../../ui'
import { ArrowRight, Film, Lock } from '../../ui/icons'
import StoryHeader from './StoryHeader'
import AgentRunCard from './AgentRunCard'
import StepRail from './StepRail'
import {
  IN_FLIGHT, currentStepKey, disabledReason, stepLabel, stepOfJob, stepsFor, statusOf, summaryFor,
} from './storySteps'
import ConceptsStep from './steps/ConceptsStep'
import BibleStep from './steps/BibleStep'
import StyleStep from './steps/StyleStep'
import CastStep from './steps/CastStep'
import PlacesStep from './steps/PlacesStep'
import SeasonStep from './steps/SeasonStep'
import KnowledgeStep from './steps/KnowledgeStep'

// The story workspace (dashboard overhaul stage 3, DEC-255): `/story/:storyId`
// opens the step to do next (`/story/:storyId/:step`), one step per screen,
// with a sticky header (StoryHeader) and the step rail (StepRail). Each step
// component gets the same props and makes the same calls as in the old
// wizard shell; its activity feed stays docked inside it while its job runs.

const STORY_POLL_MS = 4000

const STATUS_BADGES = {
  done: { tone: 'success', text: 'Done' },
  active: { tone: 'accent', text: 'To do' },
  disabled: { tone: 'neutral', text: 'Locked' },
}

function StoryJobActivity({ jobId }) {
  const { job, events, streamState } = useJobFeed(jobId)
  if (!job) return null
  return <LiveActivity job={job} events={events} streamState={streamState} />
}

function StoryJobList({ jobs }) {
  const [openId, setOpenId] = useState(null)
  if (!jobs || jobs.length === 0) return null

  return (
    <div className="card story-job-list-card">
      <h3 className="card-title">Step jobs</h3>
      <div className="story-job-list">
        {jobs.slice().reverse().map((job) => (
          <div key={job.id} className="story-job-row">
            <button
              type="button"
              className="story-job-row-header"
              onClick={() => setOpenId(openId === job.id ? null : job.id)}
            >
              <span className="story-job-row-step">
                {job.step}
                {job.params && job.params.target ? ` · ${job.params.target}` : ''}
              </span>
              <span className="chip">
                {job.status === 'queued' ? 'queued — waiting for the worker' : job.status}
              </span>
              <span className="story-job-row-time">{new Date(job.updated_at).toLocaleTimeString()}</span>
            </button>
            {openId === job.id && <StoryJobActivity jobId={job.id} />}
          </div>
        ))}
      </div>
    </div>
  )
}

function WorkspaceSkeleton() {
  return (
    <div className="fade-in story-workspace-loading" aria-busy="true">
      <span className="sr-only" role="status">Loading the story…</span>
      <div className="story-header story-header-skeleton" aria-hidden="true">
        <span className="ui-skeleton story-header-cover" />
        <div className="story-header-text"><Skeleton lines={2} /></div>
      </div>
      <div className="story-workspace">
        <div className="story-rail" aria-hidden="true"><Skeleton lines={6} /></div>
        <div className="story-workspace-main"><Skeleton variant="card" height="320px" /></div>
      </div>
    </div>
  )
}

export default function StoryWorkspace() {
  const { storyId, step } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [styles, setStyles] = useState([])
  // Set by an action that advances the story (a concept chosen, a document
  // approved): once the story is fetched again, open the step that unlocked.
  const advancePending = useRef(false)

  const refresh = useCallback(async () => {
    try {
      const fresh = await fetchStory(storyId)
      setData(fresh)
      setLoadError('')
    } catch (err) {
      setLoadError(err.message)
    } finally {
      setLoading(false)
    }
  }, [storyId])

  useEffect(() => {
    fetchStyles().then((d) => setStyles(d.styles || [])).catch(() => {})
  }, [])

  useEffect(() => {
    setLoading(true)
    setData(null)
    advancePending.current = false
    refresh()
  }, [storyId, refresh])

  // While the story has a step job queued or running, poll the story itself.
  // The step feeds can miss the moment a job finishes -- a refresh can land
  // between the step's last write and the job's flip to awaiting_approval,
  // and the feed has stopped by then -- which left a finished preview shown
  // as "Generating…" (Tier-2, 2026-09-26). This makes the page converge.
  const inFlightId = data
    ? ((data.jobs || []).find((j) => IN_FLIGHT.includes(j.status)) || {}).id || null
    : null
  useEffect(() => {
    if (!inFlightId) return
    const timer = setInterval(refresh, STORY_POLL_MS)
    return () => clearInterval(timer)
  }, [inFlightId, refresh])

  // A step opened from the rail starts at the top of the page (the window
  // keeps its scroll on a client-side route change); a #char_id deep link
  // is scrolled to by the step itself.
  useEffect(() => {
    if (!window.location.hash) window.scrollTo(0, 0)
  }, [storyId, step])

  useEffect(() => {
    if (!advancePending.current || !data) return
    advancePending.current = false
    const next = currentStepKey(data.story, data)
    if (next !== step) navigate(`/story/${storyId}/${next}`)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  const afterAction = () => {
    refresh()
  }

  const afterAdvance = () => {
    advancePending.current = true
    refresh()
  }

  // Coordinator fix attempt 2 (F1, phase 5 stage 13b, live walk): a series
  // panel action (memory, feedback, propose-next, a proposal decision, its
  // own "Approve proposals") -- and the Season step's own job feed -- must
  // never move the page off the Season step: it only fetches the story again.
  const afterSeriesAction = () => {
    refresh()
  }

  if (loading) return <WorkspaceSkeleton />
  if (loadError && !data) {
    return <div className="fade-in"><div className="card"><p className="story-error">{loadError}</p></div></div>
  }
  if (!data) return null

  const { story } = data
  const steps = stepsFor(story)
  const currentKey = currentStepKey(story, data)
  // `/story/:storyId` and an unknown step (or knowledge on a v1 story) open the step to do next.
  const active = steps.find((s) => s.key === step)
  if (!active) return <Navigate to={`/story/${storyId}/${currentKey}`} replace />

  const inFlightJob = data.jobs.find((j) => IN_FLIGHT.includes(j.status)) || null
  const runningKey = stepOfJob(inFlightJob)
  // Keyed on the server's own derived status (store.derive_status), not
  // approvals.season alone: a voice regeneration can clear a character's
  // approval, which clears approvals.cast and drops the status back to
  // style_approved even though approvals.season was never touched -- the
  // workspace must not keep claiming the story is ready once that happens.
  const allDone = story.status === 'ready'

  const railSteps = steps.map((s) => {
    const status = statusOf(s.key, story, data)
    return {
      ...s,
      status,
      tooltip: status === 'disabled' ? disabledReason(s.key)
        : status === 'done' ? summaryFor(s.key, story, data) : '',
    }
  })
  const status = statusOf(active.key, story, data)
  const index = steps.indexOf(active)
  const nextStep = index < steps.length - 1 ? steps[index + 1] : null
  const badge = STATUS_BADGES[status]
  // Plan 21 stage 3: an agent-mode story (generation_profile.mode, DEC-270)
  // gets the agent run card; a Studio story sees nothing new here.
  const isAgentStory = Boolean(story.generation_profile && story.generation_profile.mode === 'agent')

  const headerActions = (
    <>
      <Badge tone={badge.tone} dot>{badge.text}</Badge>
      {status === 'done' && nextStep && (
        <Button as={Link} to={`/story/${storyId}/${nextStep.key}`} size="sm" iconRight={ArrowRight}>
          {nextStep.label}
        </Button>
      )}
    </>
  )

  return (
    <div className="fade-in">
      <StoryHeader
        storyId={storyId}
        data={data}
        styles={styles}
        allDone={allDone}
        currentKey={currentKey}
        activeKey={active.key}
        inFlightJob={inFlightJob}
        onChange={refresh}
      />

      {isAgentStory && <AgentRunCard storyId={storyId} data={data} onChange={refresh} />}

      <div className="story-workspace">
        <StepRail storyId={storyId} steps={railSteps} activeKey={active.key} runningKey={runningKey} />

        <div className="story-workspace-main">
          {loadError && <p className="story-error">{loadError}</p>}

          <Card className="story-step-card" aria-labelledby={`story-step-${active.key}`}>
            <CardHeader
              id={`story-step-${active.key}`}
              icon={active.icon}
              title={active.label}
              subtitle={active.help}
              actions={headerActions}
            />
            <CardBody>
              {status === 'disabled' ? (
                <EmptyState
                  icon={Lock}
                  title={`${active.label} is locked`}
                  text={disabledReason(active.key)}
                  action={(
                    <Button as={Link} to={`/story/${storyId}/${currentKey}`} variant="primary" iconRight={ArrowRight}>
                      {`Go to ${stepLabel(currentKey)}`}
                    </Button>
                  )}
                />
              ) : (
                <div key={active.key}>
                  {active.key === 'concepts' && (
                    <ConceptsStep storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {active.key === 'bible' && (
                    <BibleStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {active.key === 'style' && (
                    <StyleStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {active.key === 'cast' && (
                    <CastStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {active.key === 'places' && (
                    <PlacesStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {active.key === 'season' && (
                    <SeasonStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onSeriesChange={afterSeriesAction} onAdvance={afterAdvance} />
                  )}
                  {active.key === 'knowledge' && (
                    <KnowledgeStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterSeriesAction} />
                  )}
                </div>
              )}
            </CardBody>
          </Card>

          {allDone && (
            <div className="card story-ready-card">
              <h3 className="card-title">Ready</h3>
              <p>The story is ready: bible, style, cast, places and a planned season.</p>
              <Link to={`/story/${storyId}/episodes/1`} className="story-ready-open-episode">
                Open episode 1 →
              </Link>
              {data.episodes.length > 0 && (
                <ul className="story-field-list story-ready-episodes">
                  {data.episodes.map((entry) => (
                    <li key={entry.ep}>
                      <Film size={14} aria-hidden="true" />
                      <Link to={`/story/${storyId}/episodes/${entry.ep}`}>
                        Episode {entry.ep}
                      </Link>
                      {': '}{entry.script_state}
                      {entry.total_s != null ? ` · ${entry.total_s.toFixed(1)} s` : ''}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          <StoryJobList jobs={data.jobs} />
        </div>
      </div>
    </div>
  )
}

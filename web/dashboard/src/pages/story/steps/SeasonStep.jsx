import { useEffect, useState } from 'react'
import { runStoryStep, approveStoryDoc, regenerateStory, fetchStoryEstimate } from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import RouteChip from '../../../components/RouteChip'
import { LiveActivity, useJobFeed } from '../../../components/ActivityFeed'
import { RegenerateControl, StepError } from '../fields'

// The season arc's function badges (spec 2.6): the closed list
// clipping.aistory.schemas.ARC_FUNCTIONS also uses, and their human labels.
const ARC_FUNCTION_LABELS = {
  setup: 'Setup',
  escalation: 'Escalation',
  complication: 'Complication',
  midpoint_twist: 'Midpoint twist',
  crisis: 'Crisis',
  climax_and_reset: 'Climax & reset',
}

// clipping.aistory.schemas.EPISODES_PLANNED_MIN/MAX and season.DEFAULT_EPISODES.
const MIN_EPISODES = 3
const MAX_EPISODES = 12
const DEFAULT_EPISODES = 8

function clampEpisodes(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return MIN_EPISODES
  return Math.min(MAX_EPISODES, Math.max(MIN_EPISODES, Math.round(n)))
}

// ------------------------------------------------------------- no arc yet

function NoSeasonYet({ storyId, onChange }) {
  const [episodes, setEpisodes] = useState(DEFAULT_EPISODES)
  const [estimate, setEstimate] = useState(null)
  const [planning, setPlanning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  useEffect(() => {
    fetchStoryEstimate(storyId, 'season', { episodes }).then(setEstimate).catch(() => setEstimate(null))
  }, [storyId, episodes])

  const handlePlan = async () => {
    setPlanning(true)
    setError('')
    setErrors(null)
    try {
      // The season step's own request payload: the payload contract test
      // reads this literal and checks its keys against
      // clipping.aistory.workflow.SEASON_PARAMS.
      const seasonParams = { episodes }
      await runStoryStep(storyId, 'season', { params: seasonParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setPlanning(false)
    }
  }

  return (
    <div className="story-step-body">
      <div className="form-group">
        <label className="form-label">Episodes</label>
        <input
          type="number"
          className="form-input story-season-episodes-input"
          min={MIN_EPISODES}
          max={MAX_EPISODES}
          value={episodes}
          onChange={(e) => setEpisodes(clampEpisodes(e.target.value))}
        />
        <p className="form-hint">{MIN_EPISODES} to {MAX_EPISODES}; {DEFAULT_EPISODES} by default.</p>
      </div>
      <div className="story-step-actions">
        <button type="button" className="btn btn-primary" onClick={handlePlan} disabled={planning}>
          {planning ? <><span className="spinner"></span> Planning…</> : 'Plan the season'}
        </button>
        <EstimateChip estimate={estimate} />
        {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      </div>
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// ------------------------------------------------------------ one arc entry

function ArcEntry({ storyId, entry, characters, disabled, onChange }) {
  const nameOf = (id) => {
    const doc = characters.find((c) => c.char_id === id)
    return doc ? doc.name : id
  }

  const regenerate = async (note) => {
    await regenerateStory(storyId, { target: `season:${entry.ep}`, note })
    onChange()
  }

  return (
    <div className="story-season-entry">
      <div className="story-season-entry-header">
        <span className="story-season-entry-number">Ep {entry.ep}</span>
        <span className="chip">{ARC_FUNCTION_LABELS[entry.function] || entry.function}</span>
      </div>
      <p className="story-field-value">{entry.summary}</p>
      {entry.open_hooks_in.length > 0 && (
        <div className="story-field">
          <div className="story-field-label">Hooks in</div>
          <ul className="story-field-list">
            {entry.open_hooks_in.map((hook, i) => <li key={i}>{hook}</li>)}
          </ul>
        </div>
      )}
      {entry.open_hooks_out.length > 0 && (
        <div className="story-field">
          <div className="story-field-label">Hooks out</div>
          <ul className="story-field-list">
            {entry.open_hooks_out.map((hook, i) => <li key={i}>{hook}</li>)}
          </ul>
        </div>
      )}
      {entry.characters.length > 0 && (
        <p className="story-season-entry-characters">{entry.characters.map(nameOf).join(', ')}</p>
      )}
      <RegenerateControl disabled={disabled} onRegenerate={regenerate} />
    </div>
  )
}

// ---------------------------------------------------------------- memory

function SeriesMemoryPanel() {
  return (
    <div className="card story-season-memory">
      <h4 className="card-title">Series memory</h4>
      <p className="form-hint">Filled by the memory step once an episode's script is approved.</p>
    </div>
  )
}

// -------------------------------------------------------------- approve/re-plan

function SeasonActions({ storyId, season, disabled, onChange }) {
  const [approving, setApproving] = useState(false)
  const [approveError, setApproveError] = useState('')
  const [approveErrors, setApproveErrors] = useState(null)
  const [replanning, setReplanning] = useState(false)
  const [replanError, setReplanError] = useState('')
  const [replanErrors, setReplanErrors] = useState(null)

  const handleApprove = async () => {
    setApproving(true)
    setApproveError('')
    setApproveErrors(null)
    try {
      await approveStoryDoc(storyId, 'season')
      onChange()
    } catch (err) {
      setApproveError(err.message)
      setApproveErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  const handleReplan = async () => {
    if (!window.confirm('Re-plan the season? This replaces the current arc entirely.')) return
    setReplanning(true)
    setReplanError('')
    setReplanErrors(null)
    try {
      const replanParams = { episodes: season.episodes_planned }
      await runStoryStep(storyId, 'season', { params: replanParams })
      onChange()
    } catch (err) {
      setReplanError(err.message)
      setReplanErrors(err.errors || null)
    } finally {
      setReplanning(false)
    }
  }

  return (
    <div className="story-season-actions">
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleApprove}
          disabled={disabled || approving || Boolean(season.approved_at)}
        >
          {approving ? 'Approving…' : season.approved_at ? 'Approved' : 'Approve season'}
        </button>
      </div>
      <StepError message={approveError} errors={approveErrors} className="story-step-error" />

      <div className="story-season-replan">
        <button type="button" className="btn btn-secondary" onClick={handleReplan} disabled={disabled || replanning}>
          {replanning ? <><span className="spinner"></span> Re-planning…</> : 'Re-plan'}
        </button>
      </div>
      <StepError message={replanError} errors={replanErrors} className="story-step-error" />
    </div>
  )
}

// --------------------------------------------------------------------- page

export default function SeasonStep({ data, storyId, inFlightJob, onChange }) {
  const { season, characters } = data

  const myJob = inFlightJob && (
    inFlightJob.step === 'season'
    || (inFlightJob.step === 'regenerate' && inFlightJob.params
        && typeof inFlightJob.params.target === 'string'
        && inFlightJob.params.target.startsWith('season:'))
  ) ? inFlightJob : null

  const { job: liveJob, events, streamState } = useJobFeed(myJob ? myJob.id : null, {
    onJob: (job) => {
      if (job && job.status !== 'queued' && job.status !== 'running') onChange()
    },
  })

  const busy = Boolean(inFlightJob)

  if (!season || !season.arc || season.arc.length === 0) {
    return (
      <div className="story-step-body">
        <NoSeasonYet storyId={storyId} onChange={onChange} />
        {myJob && liveJob && (
          liveJob.status === 'queued'
            ? <p className="form-hint">queued — waiting for the worker</p>
            : <LiveActivity job={liveJob} events={events} streamState={streamState} />
        )}
      </div>
    )
  }

  return (
    <div className="story-step-body">
      {myJob && liveJob && (
        liveJob.status === 'queued'
          ? <p className="form-hint">queued — waiting for the worker</p>
          : <LiveActivity job={liveJob} events={events} streamState={streamState} />
      )}

      <div className="story-season-timeline">
        {season.arc.map((entry) => (
          <ArcEntry
            key={entry.ep}
            storyId={storyId}
            entry={entry}
            characters={characters}
            disabled={busy}
            onChange={onChange}
          />
        ))}
      </div>

      <SeriesMemoryPanel />

      <SeasonActions storyId={storyId} season={season} disabled={busy} onChange={onChange} />
    </div>
  )
}

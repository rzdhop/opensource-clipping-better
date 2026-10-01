import { useEffect, useState } from 'react'
import { runStoryStep, approveStoryDoc, fetchStoryEstimate } from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import RouteChip from '../../../components/RouteChip'
import { LiveActivity, useJobFeed } from '../../../components/ActivityFeed'
import { StepError } from '../fields'

// Phase 7 stage 5b (A19, DEC-228): a v2 story's knowledge base, read and
// approved here before episode 1 (the human's CLARIFY answer 8). Read-only:
// editing the knowledge base is a later stage. `data.knowledge` is
// knowledge.json (clipping.aistory.schemas.KNOWLEDGE_SCHEMA) or null.

// clipping.aistory.schemas.KNOWLEDGE_SECTIONS: an approval needs all four.
const SECTIONS = ['world', 'timeline', 'props_registry', 'ledger_seed']

/**
 * Where the knowledge base stands -- the same four states as
 * clipping.aistory.steps.episode_common.knowledge_state: 'none', 'draft',
 * 'stale' (approved, then written again: approved_rev is not rev) or
 * 'approved'.
 */
export function knowledgeState(knowledge) {
  if (!knowledge) return 'none'
  if (!knowledge.approved_at) return 'draft'
  return knowledge.approved_rev === knowledge.rev ? 'approved' : 'stale'
}

function nameIn(list, idKey) {
  return (id) => {
    const doc = (list || []).find((item) => item[idKey] === id)
    return doc ? doc.name : id
  }
}

function World({ world }) {
  if (!world) return <p className="form-hint">World notes not written yet.</p>
  return (
    <div className="story-field">
      <div className="story-field-label">World</div>
      <p className="story-field-value">{world.geography}</p>
      <p className="story-field-value">{world.period_details}</p>
      {world.visual_motifs.length > 0 && (
        <ul className="story-field-list">
          {world.visual_motifs.map((motif, i) => <li key={i}>{motif}</li>)}
        </ul>
      )}
    </div>
  )
}

function Timeline({ timeline, planned, charName, placeName, propName }) {
  const written = timeline || []
  return (
    <div className="story-field">
      <div className="story-field-label">Timeline ({written.length} of {planned || '?'} episodes)</div>
      {written.map((entry) => (
        <div key={entry.ep} className="story-season-entry">
          <div className="story-season-entry-header">
            <span className="story-season-entry-number">Ep {entry.ep}</span>
          </div>
          <ol className="story-field-list">
            {entry.beats.map((beat, i) => (
              <li key={i}>
                <span className="story-field-value">{beat.what}</span>
                {beat.place_id && <span className="chip">{placeName(beat.place_id)}</span>}
                {beat.who.length > 0 && (
                  <span className="form-hint"> · {beat.who.map(charName).join(', ')}</span>
                )}
                {beat.objects.length > 0 && (
                  <span className="form-hint"> · {beat.objects.map(propName).join(', ')}</span>
                )}
                {(beat.new_objects || []).length > 0 && (
                  <span className="form-hint"> · new: {beat.new_objects.join(', ')}</span>
                )}
                {Object.entries(beat.knows_after).map(([id, fact]) => (
                  <div key={id} className="form-hint">{charName(id)} now knows: {fact}</div>
                ))}
              </li>
            ))}
          </ol>
        </div>
      ))}
    </div>
  )
}

function Registry({ registry, propName }) {
  if (!registry) return <p className="form-hint">Props registry not written yet.</p>
  return (
    <div className="story-field">
      <div className="story-field-label">Props registry</div>
      {registry.length === 0
        ? <p className="form-hint">No prop registered.</p>
        : <ul className="story-field-list">{registry.map((id) => <li key={id}>{propName(id)}</li>)}</ul>}
    </div>
  )
}

function Ledger({ ledger, charName, placeName, propName }) {
  if (!ledger) return <p className="form-hint">Ledger seed not written yet.</p>
  return (
    <div className="story-field">
      <div className="story-field-label">Before episode 1</div>
      <ul className="story-field-list">
        {Object.entries(ledger).map(([id, state]) => (
          <li key={id}>
            {charName(id)}
            {state.location ? ` · at ${placeName(state.location)}` : ''}
            {state.wardrobe_set ? ` · wears ${state.wardrobe_set}` : ''}
            {state.possessions.length > 0 ? ` · holds ${state.possessions.map(propName).join(', ')}` : ''}
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function KnowledgeStep({ data, storyId, inFlightJob, onChange }) {
  const { knowledge, characters, places, props, season } = data
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [approving, setApproving] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const myJob = inFlightJob && inFlightJob.step === 'knowledge' ? inFlightJob : null
  const { job: liveJob, events, streamState } = useJobFeed(myJob ? myJob.id : null, {
    onJob: (job) => {
      if (job && job.status !== 'queued' && job.status !== 'running') onChange()
    },
  })
  const busy = Boolean(inFlightJob)
  const state = knowledgeState(knowledge)
  const complete = Boolean(knowledge) && SECTIONS.every((key) => key in knowledge)
    && (knowledge.timeline || []).length === ((season && season.episodes_planned) || 0)

  useEffect(() => {
    fetchStoryEstimate(storyId, 'knowledge').then(setEstimate).catch(() => setEstimate(null))
  }, [storyId, knowledge && knowledge.rev])

  const handleRun = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      // The knowledge step's own request payload: the payload contract test
      // reads this literal and checks its keys against
      // clipping.aistory.workflow.KNOWLEDGE_PARAMS (none).
      const knowledgeParams = {}
      await runStoryStep(storyId, 'knowledge', { params: knowledgeParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  const handleApprove = async () => {
    setApproving(true)
    setError('')
    setErrors(null)
    try {
      await approveStoryDoc(storyId, 'knowledge')
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  const charName = nameIn(characters, 'char_id')
  const placeName = nameIn(places, 'place_id')
  const propName = nameIn(props, 'prop_id')

  return (
    <div className="story-step-body">
      <p className="form-hint">
        What the writers keep for the whole season: the world, every episode&apos;s beats, the props that matter and
        where everyone starts. Episode 1 is written once it is approved.
      </p>
      {state === 'stale' && (
        <p className="story-error">The knowledge base changed since it was approved: review it and approve it again.</p>
      )}
      {myJob && liveJob && (
        liveJob.status === 'queued'
          ? <p className="form-hint">queued — waiting for the worker</p>
          : <LiveActivity job={liveJob} events={events} streamState={streamState} />
      )}

      {knowledge && (
        <>
          <World world={knowledge.world} />
          <Timeline
            timeline={knowledge.timeline}
            planned={season && season.episodes_planned}
            charName={charName}
            placeName={placeName}
            propName={propName}
          />
          <Registry registry={knowledge.props_registry} propName={propName} />
          <Ledger ledger={knowledge.ledger_seed} charName={charName} placeName={placeName} propName={propName} />
        </>
      )}

      <div className="story-step-actions">
        <button type="button" className="btn btn-secondary" onClick={handleRun} disabled={busy || running || complete}>
          {running ? <><span className="spinner"></span> Starting…</> : knowledge ? 'Run step (write what is missing)' : 'Run step'}
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleApprove}
          disabled={busy || approving || !complete || state === 'approved'}
        >
          {approving ? 'Approving…' : state === 'approved' ? 'Approved' : 'Approve knowledge base'}
        </button>
        {!complete && <EstimateChip estimate={estimate} />}
        {!complete && estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      </div>
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

import { useEffect, useState } from 'react'
import { runStoryStep, approveStoryDoc, fetchStoryEstimate, patchKnowledge } from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import RouteChip from '../../../components/RouteChip'
import { LiveActivity, useJobFeed } from '../../../components/ActivityFeed'
import MoreFold from '../MoreFold'
import { EditableList, EditableText, StepError } from '../fields'

// Phase 7 stage 5b (A19, DEC-228): a v2 story's knowledge base, read and
// approved here before episode 1 (the human's CLARIFY answer 8). Stage 7
// makes it editable inline: the world, each timeline beat, the props
// registry and each character's starting state, saved through
// PATCH /api/stories/{id}/knowledge (workflow.patch_knowledge). Every saved
// edit moves the document's `rev`, so an approved base reads stale and must
// be approved again. `data.knowledge` is knowledge.json
// (clipping.aistory.schemas.KNOWLEDGE_SCHEMA) or null.

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

/** A save handler's busy flag and error slot, for the hand-made editors below. */
function useSaver(onSaved) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  const run = async (send) => {
    setSaving(true)
    setError('')
    setErrors(null)
    try {
      await send()
      onSaved()
      return true
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
      return false
    } finally {
      setSaving(false)
    }
  }
  const reset = () => { setError(''); setErrors(null) }
  return { saving, error, errors, run, reset }
}

function toggled(list, id) {
  return list.includes(id) ? list.filter((item) => item !== id) : [...list, id]
}

function CheckList({ items, idKey, chosen, onToggle }) {
  return (
    <ul className="story-cast-sketch-list">
      {items.map((item) => (
        <li key={item[idKey]}>
          <label className="story-cast-sketch-item">
            <input type="checkbox" checked={chosen.includes(item[idKey])} onChange={() => onToggle(item[idKey])} />
            <span>{item.name}</span>
          </label>
        </li>
      ))}
    </ul>
  )
}

function SaveCancel({ saving, onSave, onCancel }) {
  return (
    <div className="story-field-actions">
      <button type="button" className="btn btn-primary btn-sm" onClick={onSave} disabled={saving}>
        {saving ? 'Saving…' : 'Save'}
      </button>
      <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel} disabled={saving}>Cancel</button>
    </div>
  )
}

// ------------------------------------------------------------------- world

function World({ storyId, world, disabled, onSaved }) {
  if (!world) return <p className="form-hint">World notes not written yet.</p>
  const save = async (patch) => {
    await patchKnowledge(storyId, { world: patch })
    onSaved()
  }
  return (
    <div className="story-field">
      <div className="story-field-label">World</div>
      <EditableText label="Geography" value={world.geography} disabled={disabled} rows={3}
        onSave={(value) => save({ geography: value })} />
      <EditableText label="Period details" value={world.period_details} disabled={disabled} rows={2}
        onSave={(value) => save({ period_details: value })} />
      <EditableList label="Visual motifs (at most 4)" value={world.visual_motifs} disabled={disabled}
        onSave={(value) => save({ visual_motifs: value })} />
    </div>
  )
}

// ------------------------------------------------------------------- timeline

/** One beat, read or edited: what happens, where, who, with which objects,
 * and what each character knows afterwards. Saved as `beats: [{ep, beat}]`,
 * the beat named by its episode and 1-based position. */
function Beat({ storyId, episode, position, beat, cast, places, props, names, disabled, onSaved }) {
  const [draft, setDraft] = useState(null)
  const saver = useSaver(onSaved)

  const start = () => {
    saver.reset()
    setDraft({ what: beat.what, place_id: beat.place_id || '', who: [...beat.who], objects: [...beat.objects],
               knows_after: { ...beat.knows_after } })
  }
  const save = async () => {
    const knows = Object.fromEntries(Object.entries(draft.knows_after)
      .map(([id, fact]) => [id, (fact || '').trim()]).filter(([, fact]) => fact))
    const patch = { what: draft.what.trim(), place_id: draft.place_id || null, who: draft.who,
                    objects: draft.objects, knows_after: knows }
    const ok = await saver.run(() => patchKnowledge(storyId, { beats: [{ ep: episode, beat: position, ...patch }] }))
    if (ok) setDraft(null)
  }

  if (!draft) {
    return (
      <li>
        <span className="story-field-value">{beat.what}</span>
        {beat.place_id && <span className="chip">{names(beat.place_id)}</span>}
        {beat.who.length > 0 && <span className="form-hint"> · {beat.who.map(names).join(', ')}</span>}
        {beat.objects.length > 0 && <span className="form-hint"> · {beat.objects.map(names).join(', ')}</span>}
        {(beat.new_objects || []).length > 0 && (
          <span className="form-hint"> · new: {beat.new_objects.join(', ')}</span>
        )}
        {Object.entries(beat.knows_after).map(([id, fact]) => (
          <div key={id} className="form-hint">{names(id)} now knows: {fact}</div>
        ))}
        {!disabled && (
          <div><button type="button" className="btn btn-ghost btn-sm" onClick={start}>Edit beat</button></div>
        )}
      </li>
    )
  }

  const knowers = cast.filter((c) => draft.who.includes(c.char_id) || c.char_id in draft.knows_after)
  return (
    <li className="story-field">
      <textarea className="form-input" rows={2} value={draft.what}
        onChange={(e) => setDraft({ ...draft, what: e.target.value })} />
      <label className="form-label">Place</label>
      <select className="form-select" value={draft.place_id}
        onChange={(e) => setDraft({ ...draft, place_id: e.target.value })}>
        <option value="">no place</option>
        {places.map((p) => <option key={p.place_id} value={p.place_id}>{p.name}</option>)}
      </select>
      <label className="form-label">Who</label>
      <CheckList items={cast} idKey="char_id" chosen={draft.who}
        onToggle={(id) => setDraft({ ...draft, who: toggled(draft.who, id) })} />
      {props.length > 0 && <label className="form-label">Objects</label>}
      <CheckList items={props} idKey="prop_id" chosen={draft.objects}
        onToggle={(id) => setDraft({ ...draft, objects: toggled(draft.objects, id) })} />
      {knowers.map((c) => (
        <div key={c.char_id}>
          <label className="form-label">{c.name} now knows (empty: nothing new)</label>
          <input className="form-input" value={draft.knows_after[c.char_id] || ''}
            onChange={(e) => setDraft({ ...draft, knows_after: { ...draft.knows_after, [c.char_id]: e.target.value } })} />
        </div>
      ))}
      <StepError message={saver.error} errors={saver.errors} />
      <SaveCancel saving={saver.saving} onSave={save} onCancel={() => setDraft(null)} />
    </li>
  )
}

function Timeline({ storyId, timeline, planned, cast, places, props, names, disabled, onSaved }) {
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
              <Beat key={i} storyId={storyId} episode={entry.ep} position={i + 1} beat={beat} cast={cast}
                places={places} props={props} names={names} disabled={disabled} onSaved={onSaved} />
            ))}
          </ol>
        </div>
      ))}
    </div>
  )
}

// ------------------------------------------------------------------- props registry

function Registry({ storyId, registry, props, names, disabled, onSaved }) {
  const [draft, setDraft] = useState(null)
  const saver = useSaver(onSaved)
  if (!registry) return <p className="form-hint">Props registry not written yet.</p>

  const save = async () => {
    const ok = await saver.run(() => patchKnowledge(storyId, { props_registry: draft }))
    if (ok) setDraft(null)
  }
  return (
    <div className="story-field">
      <div className="story-field-label">Props registry</div>
      {draft ? (
        <>
          <CheckList items={props} idKey="prop_id" chosen={draft} onToggle={(id) => setDraft(toggled(draft, id))} />
          <StepError message={saver.error} errors={saver.errors} />
          <SaveCancel saving={saver.saving} onSave={save} onCancel={() => setDraft(null)} />
        </>
      ) : (
        <>
          {registry.length === 0
            ? <p className="form-hint">No prop registered.</p>
            : <ul className="story-field-list">{registry.map((id) => <li key={id}>{names(id)}</li>)}</ul>}
          {!disabled && props.length > 0 && (
            <button type="button" className="btn btn-ghost btn-sm"
              onClick={() => { saver.reset(); setDraft([...registry]) }}>
              Edit
            </button>
          )}
        </>
      )}
    </div>
  )
}

// ------------------------------------------------------------------- ledger seed

function LedgerEntry({ storyId, charId, state, character, places, props, names, disabled, onSaved }) {
  const [draft, setDraft] = useState(null)
  const saver = useSaver(onSaved)
  const sets = ((character && character.look) || {}).wardrobe_sets || []

  const start = () => {
    saver.reset()
    setDraft({ location: state.location || '', wardrobe_set: state.wardrobe_set || '',
               possessions: [...state.possessions], injuries: state.injuries || '',
               relationship_notes: state.relationship_notes || '' })
  }
  const save = async () => {
    const entry = {
      location: draft.location || null,
      wardrobe_set: draft.wardrobe_set || null,
      possessions: draft.possessions,
      injuries: draft.injuries.trim() || null,
      relationship_notes: draft.relationship_notes.trim() || null,
    }
    const ok = await saver.run(() => patchKnowledge(storyId, { ledger_seed: { [charId]: entry } }))
    if (ok) setDraft(null)
  }

  if (!draft) {
    return (
      <li>
        {names(charId)}
        {state.location ? ` · at ${names(state.location)}` : ''}
        {state.wardrobe_set ? ` · wears ${state.wardrobe_set}` : ''}
        {state.possessions.length > 0 ? ` · holds ${state.possessions.map(names).join(', ')}` : ''}
        {state.injuries ? ` · hurt: ${state.injuries}` : ''}
        {state.relationship_notes ? <div className="form-hint">{state.relationship_notes}</div> : null}
        {!disabled && (
          <div><button type="button" className="btn btn-ghost btn-sm" onClick={start}>Edit</button></div>
        )}
      </li>
    )
  }
  return (
    <li className="story-field">
      <strong>{names(charId)}</strong>
      <label className="form-label">Where</label>
      <select className="form-select" value={draft.location}
        onChange={(e) => setDraft({ ...draft, location: e.target.value })}>
        <option value="">nowhere in particular</option>
        {places.map((p) => <option key={p.place_id} value={p.place_id}>{p.name}</option>)}
      </select>
      <label className="form-label">Wears</label>
      <select className="form-select" value={draft.wardrobe_set}
        onChange={(e) => setDraft({ ...draft, wardrobe_set: e.target.value })}>
        <option value="">no wardrobe set</option>
        {sets.map((set) => <option key={set.id} value={set.id}>{set.id} — {set.context}</option>)}
      </select>
      {props.length > 0 && <label className="form-label">Holds</label>}
      <CheckList items={props} idKey="prop_id" chosen={draft.possessions}
        onToggle={(id) => setDraft({ ...draft, possessions: toggled(draft.possessions, id) })} />
      <label className="form-label">Injuries (empty: none)</label>
      <input className="form-input" value={draft.injuries}
        onChange={(e) => setDraft({ ...draft, injuries: e.target.value })} />
      <label className="form-label">Relationship notes (empty: none)</label>
      <input className="form-input" value={draft.relationship_notes}
        onChange={(e) => setDraft({ ...draft, relationship_notes: e.target.value })} />
      <StepError message={saver.error} errors={saver.errors} />
      <SaveCancel saving={saver.saving} onSave={save} onCancel={() => setDraft(null)} />
    </li>
  )
}

function Ledger({ storyId, ledger, characters, places, props, names, disabled, onSaved }) {
  if (!ledger) return <p className="form-hint">Ledger seed not written yet.</p>
  return (
    <div className="story-field">
      <div className="story-field-label">Before episode 1</div>
      <ul className="story-field-list">
        {Object.entries(ledger).map(([id, state]) => (
          <LedgerEntry key={id} storyId={storyId} charId={id} state={state}
            character={(characters || []).find((c) => c.char_id === id)} places={places} props={props}
            names={names} disabled={disabled} onSaved={onSaved} />
        ))}
      </ul>
    </div>
  )
}

// ------------------------------------------------------------------- the step

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

  const cast = characters || []
  const placeList = places || []
  const propList = props || []
  const charName = nameIn(cast, 'char_id')
  const placeName = nameIn(placeList, 'place_id')
  const propName = nameIn(propList, 'prop_id')
  // Any id the base names: a character, a place or a prop.
  const names = (id) => (id.startsWith('char_') ? charName(id) : id.startsWith('place_') ? placeName(id) : propName(id))

  return (
    <div className="story-step-body">
      <p className="form-hint">
        What the writers keep for the whole season: the world, every episode&apos;s beats, the props that matter and
        where everyone starts. Edit anything below; episode 1 is written once the base is approved.
      </p>
      {state === 'stale' && (
        <p className="story-error">
          The knowledge base changed since it was approved (version {knowledge.rev}, approved at version{' '}
          {knowledge.approved_rev}): review it and approve it again — episode scripts wait for it.
        </p>
      )}
      {state === 'approved' && (
        <p className="form-hint">Approved. Any edit below makes it a new version to approve again.</p>
      )}
      {myJob && liveJob && (
        liveJob.status === 'queued'
          ? <p className="form-hint">Waiting to start…</p>
          : <LiveActivity job={liveJob} events={events} streamState={streamState} />
      )}

      {knowledge && (
        <>
          <World storyId={storyId} world={knowledge.world} disabled={busy} onSaved={onChange} />
          <Timeline
            storyId={storyId}
            timeline={knowledge.timeline}
            planned={season && season.episodes_planned}
            cast={cast}
            places={placeList}
            props={propList}
            names={names}
            disabled={busy}
            onSaved={onChange}
          />
          <Registry storyId={storyId} registry={knowledge.props_registry} props={propList} names={names}
            disabled={busy} onSaved={onChange} />
          <Ledger storyId={storyId} ledger={knowledge.ledger_seed} characters={cast} places={placeList}
            props={propList} names={names} disabled={busy} onSaved={onChange} />
        </>
      )}

      {/* One primary action: write what is missing until the base is complete, then approve it. */}
      {(() => {
        const writeButton = (
          <button type="button" className={`btn ${complete ? 'btn-secondary' : 'btn-primary'}`} onClick={handleRun}
            disabled={busy || running || complete}>
            {running ? <><span className="spinner"></span> Starting…</> : knowledge ? 'Generate what is missing' : 'Generate'}
          </button>
        )
        const approveButton = (
          <button
            type="button"
            className={`btn ${complete ? 'btn-primary' : 'btn-secondary'}`}
            onClick={handleApprove}
            disabled={busy || approving || !complete || state === 'approved'}
          >
            {approving ? 'Approving…' : state === 'approved' ? 'Approved'
              : state === 'stale' ? 'Approve again' : 'Approve knowledge base'}
          </button>
        )
        return (
          <div className="story-step-actions">
            {complete ? approveButton : writeButton}
            <MoreFold>{complete ? writeButton : approveButton}</MoreFold>
            {!complete && <EstimateChip estimate={estimate} />}
            {!complete && estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
          </div>
        )
      })()}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

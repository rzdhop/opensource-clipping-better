import { useEffect, useRef, useState } from 'react'
import {
  patchStory, runStoryStep, approveStoryDoc, regenerateStory, fetchStoryEstimate,
  patchPlace, patchProp, deletePlace, deleteProp, fetchStoryMediaUrl,
} from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import RouteChip from '../../../components/RouteChip'
import { LiveActivity, useJobFeed } from '../../../components/ActivityFeed'
import { EditableText, RegenerateControl, StepError } from '../fields'

// The place time-variant choices a user may add (spec 2.4): the closed list
// clipping.aistory.schemas.TIME_VARIANT_CHOICES also uses. "day" is always
// the master plate and always present; the rest are made on demand.
const TIME_VARIANT_CHOICES = ['day', 'night', 'dusk', 'rain', 'dawn']

// The places/props proposal's own cap (spec 2.4/2.5): clipping.aistory
// .schemas.PROPOSAL_MAX_ITEMS.
const PROPOSAL_MAX_ITEMS = 6

// -------------------------------------------------------------- consistency

/** "base" (drawn without references, by design) is not worth a chip; a
 * degraded "prompt_only" image gets the warn styling used everywhere else
 * for a compromise the user should notice. */
function ConsistencyChip({ consistency }) {
  if (!consistency || consistency === 'base') return null
  if (consistency === 'prompt_only') return <span className="chip chip-warn">consistency: prompt-only</span>
  return <span className="chip">consistency: references</span>
}

// ------------------------------------------------------------ no proposal yet

function NoProposalYet({ storyId, onChange }) {
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  useEffect(() => {
    fetchStoryEstimate(storyId, 'places_proposal').then(setEstimate).catch(() => setEstimate(null))
  }, [storyId])

  const handlePropose = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      await runStoryStep(storyId, 'places_proposal')
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="story-step-body">
      <p>Propose places and props for this story, drawn from the bible and the cast.</p>
      <div className="story-step-actions">
        <button type="button" className="btn btn-primary" onClick={handlePropose} disabled={running}>
          {running ? <><span className="spinner"></span> Proposing…</> : 'Propose places & props'}
        </button>
        <EstimateChip estimate={estimate} />
        {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      </div>
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// -------------------------------------------------------- proposal, editable

function ProposalEditor({ storyId, proposal, characters, onChange }) {
  const [placeDrafts, setPlaceDrafts] = useState(() => {
    const initial = (proposal.places || []).map((p) => ({ name: p.name, one_line: p.one_line }))
    return initial.length ? initial : [{ name: '', one_line: '' }]
  })
  const [propDrafts, setPropDrafts] = useState(() =>
    (proposal.props || []).map((p) => ({ name: p.name, one_line: p.one_line, owner: p.owner || '' })))
  const [estimate, setEstimate] = useState(null)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  // The names on screen right now, not the saved proposal: the user may have
  // dropped or added places/props since it was proposed, and the estimate
  // chip above "Create places & props" must reflect that (spec 10 finding:
  // "the places estimate ignores the edited list").
  const placeNames = placeDrafts.map((p) => p.name.trim()).filter(Boolean)
  const propNames = propDrafts.map((p) => p.name.trim()).filter(Boolean)
  const placeKey = placeNames.join('|')
  const propKey = propNames.join('|')

  useEffect(() => {
    const timer = setTimeout(() => {
      fetchStoryEstimate(storyId, 'places', { places: placeNames, props: propNames })
        .then(setEstimate).catch(() => setEstimate(null))
    }, 300)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId, placeKey, propKey])

  const updatePlace = (i, field, value) => {
    setPlaceDrafts((prev) => prev.map((p, idx) => (idx === i ? { ...p, [field]: value } : p)))
  }
  const addPlace = () => {
    if (placeDrafts.length >= PROPOSAL_MAX_ITEMS) return
    setPlaceDrafts((prev) => [...prev, { name: '', one_line: '' }])
  }
  const removePlace = (i) => {
    if (placeDrafts.length <= 1) return
    setPlaceDrafts((prev) => prev.filter((_, idx) => idx !== i))
  }

  const updateProp = (i, field, value) => {
    setPropDrafts((prev) => prev.map((p, idx) => (idx === i ? { ...p, [field]: value } : p)))
  }
  const addProp = () => {
    if (propDrafts.length >= PROPOSAL_MAX_ITEMS) return
    setPropDrafts((prev) => [...prev, { name: '', one_line: '', owner: '' }])
  }
  const removeProp = (i) => setPropDrafts((prev) => prev.filter((_, idx) => idx !== i))

  const handleCreate = async () => {
    setCreating(true)
    setError('')
    setErrors(null)
    try {
      const places = placeDrafts
        .filter((p) => p.name.trim())
        .map((p) => ({ name: p.name.trim(), one_line: p.one_line.trim() }))
      const props = propDrafts
        .filter((p) => p.name.trim())
        .map((p) => ({ name: p.name.trim(), one_line: p.one_line.trim(), owner: p.owner || null }))
      // The places step's own request payload, one key per line: the payload
      // contract test reads this block as text and checks each key against
      // clipping.aistory.workflow.PLACES_PARAMS.
      const placesParams = { places, props }
      await runStoryStep(storyId, 'places', { params: placesParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setCreating(false)
    }
  }

  const canCreate = placeDrafts.some((p) => p.name.trim()) || propDrafts.some((p) => p.name.trim())

  return (
    <div className="story-step-body">
      <h4 className="story-section-title">Places</h4>
      <ul className="story-places-proposal-list">
        {placeDrafts.map((place, i) => (
          <li key={i} className="story-places-proposal-item">
            <input
              className="form-input"
              placeholder="Name"
              value={place.name}
              onChange={(e) => updatePlace(i, 'name', e.target.value)}
            />
            <input
              className="form-input"
              placeholder="One line"
              value={place.one_line}
              onChange={(e) => updatePlace(i, 'one_line', e.target.value)}
            />
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => removePlace(i)}
              disabled={placeDrafts.length <= 1}
              aria-label={`Remove ${place.name.trim() || `place ${i + 1}`}`}
              title={`Remove ${place.name.trim() || `place ${i + 1}`}`}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="btn btn-ghost btn-sm" onClick={addPlace} disabled={placeDrafts.length >= PROPOSAL_MAX_ITEMS}>
        + Add place
      </button>

      <h4 className="story-section-title">Props</h4>
      {propDrafts.length === 0 && <p className="form-hint">No props yet; add one below, or leave it empty.</p>}
      <ul className="story-places-proposal-list">
        {propDrafts.map((prop, i) => (
          <li key={i} className="story-places-proposal-item">
            <input
              className="form-input"
              placeholder="Name"
              value={prop.name}
              onChange={(e) => updateProp(i, 'name', e.target.value)}
            />
            <input
              className="form-input"
              placeholder="One line"
              value={prop.one_line}
              onChange={(e) => updateProp(i, 'one_line', e.target.value)}
            />
            <select className="form-select" value={prop.owner} onChange={(e) => updateProp(i, 'owner', e.target.value)}>
              <option value="">none</option>
              {characters.map((c) => <option key={c.char_id} value={c.char_id}>{c.name}</option>)}
            </select>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => removeProp(i)}
              aria-label={`Remove ${prop.name.trim() || `prop ${i + 1}`}`}
              title={`Remove ${prop.name.trim() || `prop ${i + 1}`}`}
            >
              ✕
            </button>
          </li>
        ))}
      </ul>
      <button type="button" className="btn btn-ghost btn-sm" onClick={addProp} disabled={propDrafts.length >= PROPOSAL_MAX_ITEMS}>
        + Add prop
      </button>

      <div className="story-step-actions">
        <button type="button" className="btn btn-primary" onClick={handleCreate} disabled={creating || !canCreate}>
          {creating ? <><span className="spinner"></span> Creating…</> : 'Create places & props'}
        </button>
        <EstimateChip estimate={estimate} />
        {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      </div>
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// ------------------------------------------------------------- image slots

function emptyVariantReason(variantKey, dayReady, textMissing) {
  if (textMissing) return 'Write the place first.'
  if (variantKey !== 'day' && !dayReady) return 'Make the day plate first.'
  return 'Not made yet.'
}

function VariantSlot({ storyId, place, variantKey, imageRef, dayReady, textMissing, disabled, onChange, consistencyMode }) {
  const [url, setUrl] = useState(null)
  const urlRef = useRef(null)
  const target = `place:${place.place_id}:image:${variantKey}`
  const [estimate, setEstimate] = useState(null)

  useEffect(() => {
    let cancelled = false
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
    setUrl(null)
    if (imageRef) {
      fetchStoryMediaUrl(storyId, 'places', place.place_id, imageRef.name).then((fresh) => {
        if (cancelled) { URL.revokeObjectURL(fresh); return }
        urlRef.current = fresh
        setUrl(fresh)
      }).catch(() => {})
    }
    return () => {
      cancelled = true
      if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = null }
    }
  }, [storyId, place.place_id, imageRef && imageRef.name])

  useEffect(() => {
    // consistencyMode is not part of the target, but it flips this slot's
    // units between images and edit_images (workflow.target_units): refetch
    // when the story switches mode, or this chip would show a stale count.
    fetchStoryEstimate(storyId, 'regenerate', { target }).then(setEstimate).catch(() => setEstimate(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId, target, consistencyMode])

  const regenerate = async (note) => {
    await regenerateStory(storyId, { target, note })
    onChange()
  }

  const slotDisabled = disabled || (variantKey !== 'day' && !dayReady)

  return (
    <div className={`story-places-image-slot${variantKey === 'day' ? ' story-places-image-day' : ''}`}>
      <div className="story-places-image-box">
        {url
          ? <img src={url} alt={`${place.name} ${variantKey}`} />
          : <div className="story-places-image-empty">{emptyVariantReason(variantKey, dayReady, textMissing)}</div>}
      </div>
      <div className="story-places-image-meta">
        <span className="story-places-image-label">{variantKey}</span>
        <ConsistencyChip consistency={imageRef && imageRef.consistency} />
      </div>
      <RegenerateControl
        disabled={slotDisabled}
        onRegenerate={regenerate}
        estimateChip={<EstimateChip estimate={estimate} />}
        empty={!imageRef}
        label={variantKey}
      />
    </div>
  )
}

// -------------------------------------------------------------- one place

function PlaceCard({ storyId, place, missing, disabled, onChange, consistencyMode }) {
  const [approveError, setApproveError] = useState('')
  const [approveErrors, setApproveErrors] = useState(null)
  const [approving, setApproving] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [addVariant, setAddVariant] = useState('')
  const [addVariantError, setAddVariantError] = useState('')

  // Each call site below sends one literal top-level key of
  // PlacePatchRequest; tests/test_story_payload_contract.py checks every
  // patchPlace(storyId, place.place_id, { <key>: ... }) call site in this
  // file against that model's fields.
  const saveDescriptor = async (value) => {
    await patchPlace(storyId, place.place_id, { descriptor: value })
    onChange()
  }
  const saveLayoutNotes = async (value) => {
    await patchPlace(storyId, place.place_id, { layout_notes: value })
    onChange()
  }

  const regenerateText = async (note) => {
    await regenerateStory(storyId, { target: `place:${place.place_id}:text`, note })
    onChange()
  }

  const handleApprove = async () => {
    setApproving(true)
    setApproveError('')
    setApproveErrors(null)
    try {
      await approveStoryDoc(storyId, `place:${place.place_id}`)
      onChange()
    } catch (err) {
      setApproveError(err.message)
      setApproveErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  const handleDelete = async () => {
    if (!window.confirm(`Delete ${place.name}? This cannot be undone.`)) return
    setDeleting(true)
    setDeleteError('')
    try {
      await deletePlace(storyId, place.place_id)
      onChange()
    } catch (err) {
      setDeleteError(err.message)
    } finally {
      setDeleting(false)
    }
  }

  const handleAddVariant = async () => {
    if (!addVariant) return
    const chosen = addVariant
    setAddVariant('')
    setAddVariantError('')
    try {
      await regenerateStory(storyId, { target: `place:${place.place_id}:image:${chosen}` })
      onChange()
    } catch (err) {
      setAddVariantError(err.message)
    }
  }

  const cardBusy = disabled || approving || deleting
  const variantKeys = Object.keys(place.time_variants)
  const dayReady = Boolean(place.time_variants.day)
  const availableToAdd = TIME_VARIANT_CHOICES.filter((v) => !variantKeys.includes(v))
  const textMissing = missing.includes('text')

  return (
    <div className="card story-places-card">
      <div className="story-places-card-header">
        <h4 className="card-title">{place.name}</h4>
        {place.approved_at && <span className="chip chip-accent">approved</span>}
      </div>

      <div className="story-places-images">
        <VariantSlot
          storyId={storyId}
          place={place}
          variantKey="day"
          imageRef={place.time_variants.day}
          dayReady={dayReady}
          textMissing={textMissing}
          disabled={cardBusy}
          onChange={onChange}
          consistencyMode={consistencyMode}
        />
        {variantKeys.filter((key) => key !== 'day').map((key) => (
          <VariantSlot
            key={key}
            storyId={storyId}
            place={place}
            variantKey={key}
            imageRef={place.time_variants[key]}
            dayReady={dayReady}
            textMissing={textMissing}
            consistencyMode={consistencyMode}
            disabled={cardBusy}
            onChange={onChange}
          />
        ))}
      </div>

      {availableToAdd.length > 0 && (
        <div className="story-places-add-variant">
          <select
            className="form-select"
            value={addVariant}
            onChange={(e) => setAddVariant(e.target.value)}
            disabled={cardBusy || !dayReady}
          >
            <option value="">Add a time variant…</option>
            {availableToAdd.map((v) => <option key={v} value={v}>{v}</option>)}
          </select>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={handleAddVariant}
            disabled={cardBusy || !dayReady || !addVariant}
          >
            + Add
          </button>
        </div>
      )}
      <StepError message={addVariantError} className="story-step-error" />

      <div className="story-field">
        <div className="story-field-label">Descriptor (English)</div>
        <EditableText value={place.descriptor} onSave={saveDescriptor} disabled={cardBusy} rows={3} />
      </div>
      <EditableText
        label="Layout notes"
        value={place.layout_notes}
        onSave={saveLayoutNotes}
        disabled={cardBusy}
        rows={2}
      />
      <RegenerateControl disabled={cardBusy} onRegenerate={regenerateText} />

      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={handleApprove}
          disabled={cardBusy || Boolean(place.approved_at)}
        >
          {approving ? 'Approving…' : place.approved_at ? 'Approved' : 'Approve'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={handleDelete} disabled={cardBusy}>
          {deleting ? 'Deleting…' : 'Delete place'}
        </button>
      </div>
      <StepError message={approveError} errors={approveErrors} className="story-step-error" />
      <StepError message={deleteError} className="story-step-error" />
    </div>
  )
}

// --------------------------------------------------------------- one prop

function PropImage({ storyId, prop, disabled, onChange }) {
  const [url, setUrl] = useState(null)
  const urlRef = useRef(null)
  const target = `prop:${prop.prop_id}:image`
  const [estimate, setEstimate] = useState(null)

  useEffect(() => {
    let cancelled = false
    if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = null }
    setUrl(null)
    if (prop.image) {
      fetchStoryMediaUrl(storyId, 'props', prop.prop_id, prop.image.name).then((fresh) => {
        if (cancelled) { URL.revokeObjectURL(fresh); return }
        urlRef.current = fresh
        setUrl(fresh)
      }).catch(() => {})
    }
    return () => {
      cancelled = true
      if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = null }
    }
  }, [storyId, prop.prop_id, prop.image && prop.image.name])

  useEffect(() => {
    fetchStoryEstimate(storyId, 'regenerate', { target }).then(setEstimate).catch(() => setEstimate(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId, target])

  const regenerate = async (note) => {
    await regenerateStory(storyId, { target, note })
    onChange()
  }

  return (
    <div className="story-places-prop-image">
      <div className="story-places-image-box story-places-prop-image-box">
        {url
          ? <img src={url} alt={prop.name} />
          : <div className="story-places-image-empty">{prop.descriptor ? 'Not made yet.' : 'Write the prop first.'}</div>}
      </div>
      <div className="story-places-image-meta">
        <ConsistencyChip consistency={prop.image && prop.image.consistency} />
      </div>
      <RegenerateControl
        disabled={disabled}
        onRegenerate={regenerate}
        estimateChip={<EstimateChip estimate={estimate} />}
        empty={!prop.image}
        label="image"
      />
    </div>
  )
}

function PropCard({ storyId, prop, characters, disabled, onChange }) {
  const [approveError, setApproveError] = useState('')
  const [approveErrors, setApproveErrors] = useState(null)
  const [approving, setApproving] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [ownerError, setOwnerError] = useState('')

  // Each call site below sends one literal top-level key of PropPatchRequest;
  // tests/test_story_payload_contract.py checks every patchProp(storyId,
  // prop.prop_id, { <key>: ... }) call site in this file against that
  // model's fields.
  const saveDescriptor = async (value) => {
    await patchProp(storyId, prop.prop_id, { descriptor: value })
    onChange()
  }
  const saveOwner = async (value) => {
    setOwnerError('')
    try {
      await patchProp(storyId, prop.prop_id, { owner_char_id: value || null })
      onChange()
    } catch (err) {
      setOwnerError(err.message)
    }
  }

  const regenerateText = async (note) => {
    await regenerateStory(storyId, { target: `prop:${prop.prop_id}:text`, note })
    onChange()
  }

  const handleApprove = async () => {
    setApproving(true)
    setApproveError('')
    setApproveErrors(null)
    try {
      await approveStoryDoc(storyId, `prop:${prop.prop_id}`)
      onChange()
    } catch (err) {
      setApproveError(err.message)
      setApproveErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  const handleDelete = async () => {
    if (!window.confirm(`Delete ${prop.name}? This cannot be undone.`)) return
    setDeleting(true)
    setDeleteError('')
    try {
      await deleteProp(storyId, prop.prop_id)
      onChange()
    } catch (err) {
      setDeleteError(err.message)
    } finally {
      setDeleting(false)
    }
  }

  const cardBusy = disabled || approving || deleting

  return (
    <div className="card story-places-prop-card">
      <div className="story-places-card-header">
        <h4 className="card-title">{prop.name}</h4>
        {prop.approved_at && <span className="chip chip-accent">approved</span>}
      </div>

      <PropImage storyId={storyId} prop={prop} disabled={cardBusy} onChange={onChange} />

      <div className="story-field">
        <div className="story-field-label">Owner</div>
        <select
          className="form-select"
          value={prop.owner_char_id || ''}
          onChange={(e) => saveOwner(e.target.value)}
          disabled={cardBusy}
        >
          <option value="">none</option>
          {characters.map((c) => <option key={c.char_id} value={c.char_id}>{c.name}</option>)}
        </select>
        <StepError message={ownerError} className="story-step-error" />
      </div>

      <div className="story-field">
        <div className="story-field-label">Descriptor (English)</div>
        <EditableText value={prop.descriptor} onSave={saveDescriptor} disabled={cardBusy} rows={2} />
      </div>
      <RegenerateControl disabled={cardBusy} onRegenerate={regenerateText} />

      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={handleApprove}
          disabled={cardBusy || Boolean(prop.approved_at)}
        >
          {approving ? 'Approving…' : prop.approved_at ? 'Approved' : 'Approve'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={handleDelete} disabled={cardBusy}>
          {deleting ? 'Deleting…' : 'Delete prop'}
        </button>
      </div>
      <StepError message={approveError} errors={approveErrors} className="story-step-error" />
      <StepError message={deleteError} className="story-step-error" />
    </div>
  )
}

// ------------------------------------------------------------- needs an editor

function NeedsEditorBanner({ storyId, editReadiness, disabled, onChange }) {
  const [switching, setSwitching] = useState(false)
  const [error, setError] = useState('')

  const switchToPromptOnly = async () => {
    if (!window.confirm(
      'Switch this story to prompt-only consistency? Shots will be prompted without reference images, so ' +
      'characters and places may drift slightly across shots. This cannot be undone from here.',
    )) return
    setSwitching(true)
    setError('')
    try {
      await patchStory(storyId, { generation_profile: { consistency_mode: 'prompt_only' } })
      onChange()
    } catch (err) {
      setError(err.message)
    } finally {
      setSwitching(false)
    }
  }

  return (
    <div className="card story-places-editor-banner">
      <h4 className="card-title">Some time variants need an editor</h4>
      <p>{(editReadiness && editReadiness.message) || 'No editor can make the missing variants right now.'}</p>
      <ul className="story-field-list">
        <li>Set up local ComfyUI — see Settings → Local hardware.</li>
        <li>Allow paid generation — see Settings → Budget.</li>
      </ul>
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={switchToPromptOnly}
          disabled={disabled || switching}
        >
          {switching ? 'Switching…' : 'Switch this story to prompt-only consistency'}
        </button>
      </div>
      <StepError message={error} className="story-step-error" />
    </div>
  )
}

// ------------------------------------------------------------------- continue

function ContinuePlaces({ storyId, disabled, onChange, consistencyMode }) {
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  useEffect(() => {
    // consistencyMode is not a request parameter, but it can flip missing
    // items between images and edit_images: refetch when the story switches
    // mode, or this chip would show a stale count (spec 10 finding:
    // "prompt-only sheets estimated as edits").
    fetchStoryEstimate(storyId, 'places').then(setEstimate).catch(() => setEstimate(null))
  }, [storyId, consistencyMode])

  const handleContinue = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      await runStoryStep(storyId, 'places', { params: {} })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="story-step-actions">
      <button type="button" className="btn btn-secondary" onClick={handleContinue} disabled={disabled || running}>
        {running ? <><span className="spinner"></span> Continuing…</> : 'Continue places & props'}
      </button>
      <EstimateChip estimate={estimate} />
      {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// --------------------------------------------------------------------- page

export default function PlacesStep({ data, storyId, inFlightJob, onChange }) {
  const { story, places, props, places_proposal: placesProposal, characters, progress } = data

  const myJob = inFlightJob && (
    inFlightJob.step === 'places_proposal'
    || inFlightJob.step === 'places'
    || (inFlightJob.step === 'regenerate' && inFlightJob.params
        && typeof inFlightJob.params.target === 'string'
        && (inFlightJob.params.target.startsWith('place:') || inFlightJob.params.target.startsWith('prop:')))
  ) ? inFlightJob : null

  const { job: liveJob, events, streamState } = useJobFeed(myJob ? myJob.id : null, {
    onJob: (job) => {
      if (job && job.status !== 'queued' && job.status !== 'running') onChange()
    },
  })

  const busy = Boolean(inFlightJob)

  if (places.length === 0 && props.length === 0) {
    return (
      <div className="story-step-body">
        {placesProposal
          ? <ProposalEditor storyId={storyId} proposal={placesProposal} characters={characters} onChange={onChange} />
          : <NoProposalYet storyId={storyId} onChange={onChange} />}
        {myJob && liveJob && (
          liveJob.status === 'queued'
            ? <p className="form-hint">queued — waiting for the worker</p>
            : <LiveActivity job={liveJob} events={events} streamState={streamState} />
        )}
      </div>
    )
  }

  const placeProgress = progress.places || {}
  const propProgress = progress.props || {}
  const readiness = progress.edit_readiness
  const consistencyMode = story.generation_profile.consistency_mode
  const referencesMode = consistencyMode === 'references'
  const needsEditor = referencesMode && Boolean(readiness) && readiness.ready === false
  const anyMissing = Object.values(placeProgress).some((info) => (info.missing || []).length > 0)
    || Object.values(propProgress).some((info) => (info.missing || []).length > 0)

  return (
    <div className="story-step-body">
      {needsEditor && (
        <NeedsEditorBanner storyId={storyId} editReadiness={readiness} disabled={busy} onChange={onChange} />
      )}

      {myJob && liveJob && (
        liveJob.status === 'queued'
          ? <p className="form-hint">queued — waiting for the worker</p>
          : <LiveActivity job={liveJob} events={events} streamState={streamState} />
      )}

      {places.length > 0 && (
        <>
          <h4 className="story-section-title">Places</h4>
          <div className="story-places-grid">
            {places.map((place) => (
              <PlaceCard
                key={place.place_id}
                storyId={storyId}
                place={place}
                missing={(placeProgress[place.place_id] || {}).missing || []}
                disabled={busy}
                onChange={onChange}
                consistencyMode={consistencyMode}
              />
            ))}
          </div>
        </>
      )}

      {props.length > 0 && (
        <>
          <h4 className="story-section-title">Props</h4>
          <div className="story-places-grid">
            {props.map((prop) => (
              <PropCard
                key={prop.prop_id}
                storyId={storyId}
                prop={prop}
                characters={characters}
                disabled={busy}
                onChange={onChange}
              />
            ))}
          </div>
        </>
      )}

      {anyMissing && (
        <ContinuePlaces storyId={storyId} disabled={busy} onChange={onChange} consistencyMode={consistencyMode} />
      )}
    </div>
  )
}

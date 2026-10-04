import { useEffect, useRef, useState } from 'react'
import {
  patchStory, runStoryStep, approveStoryDoc, regenerateStory, fetchStoryEstimate,
  fetchCharacterVoices, patchCharacter, deleteCharacter, deleteCharacterUpload,
  fetchStoryMediaUrl, uploadCharacterReference,
} from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import RouteChip from '../../../components/RouteChip'
import { LiveActivity, useJobFeed } from '../../../components/ActivityFeed'
import { EditableText, EditableList, RegenerateControl, StepError } from '../fields'
import EntityGallery, { useHashAccordion } from '../EntityGallery'
import { EntityImageSlots, imagesManual } from '../ManualUploadSlot'
import { Badge, Chip, useConfirm } from '../../../ui'
import { Mic } from '../../../ui/icons'

// The character roles a custom entry may pick (spec 2.3): the closed list
// clipping.aistory.schemas.CHARACTER_ROLES also uses.
const CHARACTER_ROLES = ['lead', 'support', 'recurring', 'guest']

const IMAGE_SLOTS = ['portrait', 'turnaround', 'expressions']
const SLOT_LABELS = { portrait: 'portrait', turnaround: 'turnaround', expressions: 'expressions sheet' }

const EMPTY_PERSONALITY = { traits: [], wants: '', fears: '', speech_style: '' }

const ACCEPTED_UPLOAD_TYPES = 'image/png,image/jpeg,image/webp,image/gif'
const MAX_UPLOADS = 4

// -------------------------------------------------------------- consistency

/** "base" (drawn without references, by design) is not worth a chip; a
 * degraded "prompt_only" image gets the warn styling used everywhere else
 * for a compromise the user should notice. */
function ConsistencyChip({ consistency }) {
  if (!consistency || consistency === 'base') return null
  if (consistency === 'prompt_only') return <span className="chip chip-warn">consistency: prompt-only</span>
  return <span className="chip">consistency: references</span>
}

// ------------------------------------------------------------ no cast yet

function NoCastYet({ storyId, story, onChange }) {
  const sketch = (story.concept && story.concept.cast_sketch) || []
  const [checked, setChecked] = useState(() => new Set(sketch.map((entry) => entry.name)))
  const [custom, setCustom] = useState([])
  const [newName, setNewName] = useState('')
  const [newRole, setNewRole] = useState('support')
  const [newOneLine, setNewOneLine] = useState('')
  const [estimate, setEstimate] = useState(null)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const selected = sketch.filter((entry) => checked.has(entry.name)).map((entry) => entry.name)

  useEffect(() => {
    fetchStoryEstimate(storyId, 'cast', { selected }).then(setEstimate).catch(() => setEstimate(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId, selected.join('|'), custom.length])

  const toggle = (name) => {
    setChecked((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const addCustom = () => {
    if (!newName.trim()) return
    setCustom((prev) => [...prev, { name: newName.trim(), role: newRole, one_line: newOneLine.trim() }])
    setNewName('')
    setNewOneLine('')
  }

  const removeCustom = (index) => setCustom((prev) => prev.filter((_, i) => i !== index))

  const handleCreate = async () => {
    setCreating(true)
    setError('')
    setErrors(null)
    try {
      // The cast step's own request payload: the payload contract test reads
      // this literal and checks its keys against clipping.aistory.workflow
      // .CAST_PARAMS.
      const castParams = { selected, custom }
      await runStoryStep(storyId, 'cast', { params: castParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setCreating(false)
    }
  }

  const canCreate = selected.length > 0 || custom.length > 0

  return (
    <div className="story-step-body">
      <h4 className="story-section-title">From the concept's cast sketch</h4>
      {sketch.length === 0 && <p className="form-hint">This concept has no cast sketch; add your own below.</p>}
      <ul className="story-cast-sketch-list">
        {sketch.map((entry) => (
          <li key={entry.name}>
            <label className="story-cast-sketch-item">
              <input type="checkbox" checked={checked.has(entry.name)} onChange={() => toggle(entry.name)} />
              <span><strong>{entry.name}</strong> — {entry.role} · {entry.one_line}</span>
            </label>
          </li>
        ))}
      </ul>

      <h4 className="story-section-title">Add your own</h4>
      <div className="story-cast-add-row">
        <input
          className="form-input"
          placeholder="Name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <select className="form-select" value={newRole} onChange={(e) => setNewRole(e.target.value)}>
          {CHARACTER_ROLES.map((role) => <option key={role} value={role}>{role}</option>)}
        </select>
        <input
          className="form-input"
          placeholder="One line"
          value={newOneLine}
          onChange={(e) => setNewOneLine(e.target.value)}
        />
        <button type="button" className="btn btn-ghost btn-sm" onClick={addCustom} disabled={!newName.trim()}>
          + Add
        </button>
      </div>
      {custom.length > 0 && (
        <ul className="story-field-list">
          {custom.map((entry, i) => (
            <li key={i}>
              {entry.name} ({entry.role}) — {entry.one_line}
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => removeCustom(i)}
                aria-label={`Remove ${entry.name}`}
                title={`Remove ${entry.name}`}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="story-step-actions">
        <button type="button" className="btn btn-primary" onClick={handleCreate} disabled={creating || !canCreate}>
          {creating ? <><span className="spinner"></span> Creating…</> : 'Create cast'}
        </button>
        <EstimateChip estimate={estimate} />
        {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      </div>
      {estimate && estimate.edit && estimate.edit.ready === false && (
        <p className="form-hint">Sheets will need an editor or prompt-only consistency.</p>
      )}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// ------------------------------------------------------------- image slots

function emptySlotReason(info, slot) {
  const missing = (info && info.missing) || []
  if (!missing.includes(slot)) return null
  if (info.needs_editor && slot !== 'portrait') return 'Needs an editor, or prompt-only consistency.'
  if (slot !== 'portrait' && missing.includes('portrait')) return 'Make the portrait first.'
  if (missing.includes('text')) return 'Write the character first.'
  return 'Not made yet.'
}

function ImageSlot({ storyId, character, slot, info, disabled, onChange, consistencyMode }) {
  const ref = character.refs[slot]
  const [url, setUrl] = useState(null)
  const urlRef = useRef(null)
  const target = `character:${character.char_id}:image:${slot}`
  const [estimate, setEstimate] = useState(null)

  useEffect(() => {
    let cancelled = false
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
    setUrl(null)
    if (ref) {
      fetchStoryMediaUrl(storyId, 'characters', character.char_id, ref.name).then((fresh) => {
        if (cancelled) { URL.revokeObjectURL(fresh); return }
        urlRef.current = fresh
        setUrl(fresh)
      }).catch(() => {})
    }
    return () => {
      cancelled = true
      if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = null }
    }
  }, [storyId, character.char_id, ref && ref.name])

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

  return (
    <div className={`story-cast-image-slot story-cast-image-${slot}`}>
      <div className="story-cast-image-box">
        {url
          ? <img src={url} alt={`${character.name} ${slot}`} />
          : <div className="story-cast-image-empty">{emptySlotReason(info, slot) || SLOT_LABELS[slot]}</div>}
      </div>
      <div className="story-cast-image-meta">
        <span className="story-cast-image-label">{SLOT_LABELS[slot]}</span>
        <ConsistencyChip consistency={ref && ref.consistency} />
      </div>
      <RegenerateControl
        disabled={disabled}
        onRegenerate={regenerate}
        estimateChip={<EstimateChip estimate={estimate} />}
        empty={!ref}
        label={SLOT_LABELS[slot]}
      />
    </div>
  )
}

// ------------------------------------------------------------------- voice

function VoiceSection({ storyId, character, pickVoiceIds, disabled, onChange }) {
  const voice = character.voice
  const [sampleUrl, setSampleUrl] = useState(null)
  const sampleRef = useRef(null)
  const [picker, setPicker] = useState(null)
  const [loadingPicker, setLoadingPicker] = useState(false)
  const [picking, setPicking] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  useEffect(() => {
    let cancelled = false
    if (sampleRef.current) { URL.revokeObjectURL(sampleRef.current); sampleRef.current = null }
    setSampleUrl(null)
    if (voice) {
      fetchStoryMediaUrl(storyId, 'characters', character.char_id, 'voice_sample.mp3')
        .catch(() => fetchStoryMediaUrl(storyId, 'characters', character.char_id, 'voice_sample.wav'))
        .then((fresh) => {
          if (cancelled) { URL.revokeObjectURL(fresh); return }
          sampleRef.current = fresh
          setSampleUrl(fresh)
        })
        .catch(() => {})
    }
    return () => {
      cancelled = true
      if (sampleRef.current) { URL.revokeObjectURL(sampleRef.current); sampleRef.current = null }
    }
  }, [storyId, character.char_id, voice && voice.provider, voice && voice.voice_id])

  const togglePicker = () => {
    if (picker) { setPicker(null); return }
    setLoadingPicker(true)
    fetchCharacterVoices(storyId, character.char_id)
      .then(setPicker)
      .catch((err) => setError(err.message))
      .finally(() => setLoadingPicker(false))
  }

  const pickVoice = async (alt) => {
    setPicking(true)
    setError('')
    setErrors(null)
    try {
      await regenerateStory(storyId, {
        target: `character:${character.char_id}:voice`,
        voice: { provider: alt.provider, voice_id: alt.voice_id },
      })
      setPicker(null)
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setPicking(false)
    }
  }

  const needsPick = pickVoiceIds.includes(character.char_id)

  return (
    <div className="story-cast-voice">
      <div className="story-field-label">Voice</div>
      {voice ? (
        <div className="story-cast-voice-current">
          <span>{voice.provider}/{voice.voice_id}</span>
          {sampleUrl && <audio controls src={sampleUrl} />}
        </div>
      ) : (
        <p className="story-field-value">
          <em>{needsPick ? 'Pick a voice: no catalogue voice was left for this character.' : 'Not pinned yet.'}</em>
        </p>
      )}
      <button type="button" className="btn btn-ghost btn-sm" onClick={togglePicker} disabled={disabled}>
        {loadingPicker ? 'Loading…' : picker ? 'Hide other voices' : 'Other voices'}
      </button>
      {picker && (
        <ul className="story-field-list story-cast-voice-alternates">
          {picker.alternates.length === 0 && <li><em>No other catalogue voice for this language.</em></li>}
          {picker.alternates.map((alt) => (
            <li key={`${alt.provider}/${alt.voice_id}`}>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => pickVoice(alt)}
                disabled={disabled || picking}
              >
                {alt.provider}/{alt.voice_id}
              </button>
              <span className="story-cast-voice-tags">
                {alt.gender}, {alt.age}{alt.style_tags.length ? `, ${alt.style_tags.join(', ')}` : ''}
              </span>
            </li>
          ))}
        </ul>
      )}
      <StepError message={error} errors={errors} />
    </div>
  )
}

// ----------------------------------------------------------------- uploads

function UploadsSection({ storyId, character, disabled, onChange }) {
  const uploads = character.refs.uploads || []
  const [progress, setProgress] = useState(null)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const handleFile = async (e) => {
    const file = e.target.files && e.target.files[0]
    e.target.value = ''
    if (!file) return
    setError('')
    setErrors(null)
    setProgress({ percent: 0 })
    try {
      await uploadCharacterReference(storyId, character.char_id, file, setProgress)
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setProgress(null)
    }
  }

  const removeUpload = async (name) => {
    setError('')
    setErrors(null)
    try {
      await deleteCharacterUpload(storyId, character.char_id, name)
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    }
  }

  return (
    <div className="story-cast-uploads">
      <div className="story-field-label">Design references</div>
      <p className="form-hint">Design references for stylised characters. Imitating real people is not supported.</p>
      {uploads.length > 0 && (
        <ul className="story-field-list">
          {uploads.map((upload) => (
            <li key={upload.name}>
              <span>{upload.description || 'not described yet — it will be described before the text is written'}</span>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => removeUpload(upload.name)}
                disabled={disabled}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <input
        type="file"
        aria-label="Add a design reference"
        accept={ACCEPTED_UPLOAD_TYPES}
        onChange={handleFile}
        disabled={disabled || uploads.length >= MAX_UPLOADS || Boolean(progress)}
      />
      {progress && progress.percent != null && (
        <div className="progress-bar-bg">
          <div className="progress-bar-fill" style={{ width: `${progress.percent}%` }}></div>
        </div>
      )}
      <StepError message={error} errors={errors} />
    </div>
  )
}

// ------------------------------------------------- look and dossier (v2 stories)
//
// Phase 7 stage 7 (A19): what D2 (the look) and D1 (the dossier) wrote, edited
// field by field. Each save sends one block, `{ look: {...} }` or
// `{ dossier: {...} }`, merged onto the stored one by the server
// (workflow.patch_entity), checked by the character schema's word caps (its
// errors show under the field), and -- like any edit -- clears the
// character's approval and outdates the shot prompts made from it.

const LOOK_TEXT_FIELDS = [
  ['build', 'Build'], ['silhouette', 'Silhouette'], ['face', 'Face'], ['hair', 'Hair'],
  ['skin_material', 'Skin / material'], ['presentation', 'Age & presentation'], ['bearing', 'Bearing'],
]
const DOSSIER_TEXT_FIELDS = [
  ['backstory', 'Backstory', 3], ['goal', 'Goal', 1], ['need', 'Need', 1], ['fears', 'Fears', 1], ['arc', 'Arc', 2],
]

function BlockNotWritten({ what }) {
  return <p className="form-hint">No {what} yet: the Cast step writes it (run it again to fill it).</p>
}

function LookSection({ storyId, character, disabled, onChange }) {
  const look = character.look
  const save = async (patch) => {
    await patchCharacter(storyId, character.char_id, { look: patch })
    onChange()
  }
  if (!look) return <BlockNotWritten what="look" />
  const sets = look.wardrobe_sets || []
  const saveSet = (index, key) => (value) => save({
    wardrobe_sets: sets.map((set, i) => (i === index ? { ...set, [key]: value } : set)),
  })
  return (
    <>
      {LOOK_TEXT_FIELDS.map(([key, label]) => (
        <EditableText key={key} label={label} value={look[key]} disabled={disabled} rows={1}
          onSave={(value) => save({ [key]: value })} />
      ))}
      <EditableText
        label="Height (cm, on the story's own scale)"
        value={look.height_cm != null ? String(look.height_cm) : ''}
        disabled={disabled}
        rows={1}
        onSave={(value) => save({ height_cm: /^\d+$/.test(value) ? Number(value) : value })}
      />
      <EditableList label="Palette (1–4 colours)" value={look.palette} disabled={disabled}
        onSave={(value) => save({ palette: value })} />
      {sets.map((set, index) => (
        <div key={set.id} className="story-field">
          <div className="story-field-label">Wardrobe “{set.id}”</div>
          <EditableText label="When" value={set.context} disabled={disabled} rows={1} onSave={saveSet(index, 'context')} />
          <EditableText label="Wears" value={set.items} disabled={disabled} rows={2} onSave={saveSet(index, 'items')} />
        </div>
      ))}
      <EditableText
        label="Season change (empty: none)"
        value={look.season_change}
        disabled={disabled}
        rows={1}
        emptyText="None."
        onSave={(value) => save({ season_change: value || null })}
      />
    </>
  )
}

function DossierSection({ storyId, character, castNames, disabled, onChange }) {
  const dossier = character.dossier
  const save = async (patch) => {
    await patchCharacter(storyId, character.char_id, { dossier: patch })
    onChange()
  }
  if (!dossier) return <BlockNotWritten what="dossier" />
  const relationships = dossier.relationships || []
  const voice = dossier.voice || { patterns: '', vocabulary: '', catchphrases: [] }
  const saveRelationship = (index, key) => (value) => save({
    relationships: relationships.map((item, i) => (i === index ? { ...item, [key]: value } : item)),
  })
  return (
    <>
      {DOSSIER_TEXT_FIELDS.map(([key, label, rows]) => (
        <EditableText key={key} label={label} value={dossier[key]} disabled={disabled} rows={rows}
          onSave={(value) => save({ [key]: value })} />
      ))}
      <EditableList label="Secrets (at most 2)" value={dossier.secrets} disabled={disabled}
        onSave={(value) => save({ secrets: value })} />
      {relationships.map((item, index) => (
        <div key={item.with} className="story-field">
          <div className="story-field-label">With {castNames[item.with] || item.with}</div>
          <EditableText label="History" value={item.history} disabled={disabled} rows={2}
            onSave={saveRelationship(index, 'history')} />
          <EditableText label="Now" value={item.now} disabled={disabled} rows={1}
            onSave={saveRelationship(index, 'now')} />
        </div>
      ))}
      <EditableText label="Voice patterns" value={voice.patterns} disabled={disabled} rows={1}
        onSave={(value) => save({ voice: { ...voice, patterns: value } })} />
      <EditableText label="Vocabulary" value={voice.vocabulary} disabled={disabled} rows={1}
        onSave={(value) => save({ voice: { ...voice, vocabulary: value } })} />
      <EditableList label="Catchphrases (at most 2)" value={voice.catchphrases} disabled={disabled}
        onSave={(value) => save({ voice: { ...voice, catchphrases: value } })} />
    </>
  )
}

// -------------------------------------------------------------- one character

function CharacterCard({ storyId, character, info, pickVoiceIds, disabled, onChange, consistencyMode, isV2, castNames }) {
  const confirm = useConfirm()
  const [approveError, setApproveError] = useState('')
  const [approveErrors, setApproveErrors] = useState(null)
  const [approving, setApproving] = useState(false)
  const [deleteError, setDeleteError] = useState('')
  const [deleting, setDeleting] = useState(false)

  // Each call site below sends one literal top-level key of
  // CharacterPatchRequest; tests/test_story_payload_contract.py checks every
  // patchCharacter(storyId, character.char_id, { <key>: ... }) call site in
  // this file against that model's fields.
  const saveDescriptor = async (value) => {
    await patchCharacter(storyId, character.char_id, { descriptor: value })
    onChange()
  }
  const saveSignatureItems = async (value) => {
    await patchCharacter(storyId, character.char_id, { signature_items: value })
    onChange()
  }
  const saveVoiceDirection = async (value) => {
    await patchCharacter(storyId, character.char_id, { voice_direction: value })
    onChange()
  }
  const saveSampleLine = async (value) => {
    await patchCharacter(storyId, character.char_id, { sample_line: value })
    onChange()
  }
  const patchPersonalityField = async (patch) => {
    await patchCharacter(storyId, character.char_id,
      { personality: { ...EMPTY_PERSONALITY, ...(character.personality || {}), ...patch } })
    onChange()
  }

  const regenerateText = async (note) => {
    await regenerateStory(storyId, { target: `character:${character.char_id}:text`, note })
    onChange()
  }

  const handleApprove = async () => {
    setApproving(true)
    setApproveError('')
    setApproveErrors(null)
    try {
      await approveStoryDoc(storyId, `character:${character.char_id}`)
      onChange()
    } catch (err) {
      setApproveError(err.message)
      setApproveErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  const handleDelete = async () => {
    if (!(await confirm({
      title: `Delete ${character.name}?`,
      message: 'This cannot be undone.',
      confirmLabel: 'Delete character',
      tone: 'danger',
    }))) return
    setDeleting(true)
    setDeleteError('')
    try {
      await deleteCharacter(storyId, character.char_id)
      onChange()
    } catch (err) {
      setDeleteError(err.message)
    } finally {
      setDeleting(false)
    }
  }

  const personality = character.personality || EMPTY_PERSONALITY
  const cardBusy = disabled || approving || deleting

  return (
    <div className="card story-cast-card">
      <div className="story-cast-card-header">
        <h4 className="card-title">{character.name}</h4>
        <span className="chip">{character.role}</span>
        {character.approved_at && <span className="chip chip-accent">approved</span>}
      </div>

      <div className="story-cast-images">
        {IMAGE_SLOTS.map((slot) => (
          <ImageSlot
            key={slot}
            storyId={storyId}
            character={character}
            slot={slot}
            info={info}
            disabled={cardBusy}
            onChange={onChange}
            consistencyMode={consistencyMode}
          />
        ))}
      </div>

      <div className="story-field">
        <div className="story-field-label">Descriptor (English)</div>
        <EditableText value={character.descriptor} onSave={saveDescriptor} disabled={cardBusy} rows={3} />
      </div>
      <EditableList
        label="Signature items"
        value={character.signature_items}
        onSave={saveSignatureItems}
        disabled={cardBusy}
      />
      <EditableList
        label="Personality traits"
        value={personality.traits}
        onSave={(value) => patchPersonalityField({ traits: value })}
        disabled={cardBusy}
      />
      <EditableText
        label="Wants"
        value={personality.wants}
        onSave={(value) => patchPersonalityField({ wants: value })}
        disabled={cardBusy}
        rows={1}
      />
      <EditableText
        label="Fears"
        value={personality.fears}
        onSave={(value) => patchPersonalityField({ fears: value })}
        disabled={cardBusy}
        rows={1}
      />
      <EditableText
        label="Speech style"
        value={personality.speech_style}
        onSave={(value) => patchPersonalityField({ speech_style: value })}
        disabled={cardBusy}
        rows={1}
      />
      <EditableText
        label="Voice direction"
        value={character.voice ? character.voice.direction : (character.voice_hints || {}).direction}
        onSave={saveVoiceDirection}
        disabled={cardBusy}
        rows={1}
      />
      <EditableText
        label="Sample line"
        value={character.voice ? character.voice.sample_line : (character.voice_hints || {}).sample_line}
        onSave={saveSampleLine}
        disabled={cardBusy}
        rows={1}
      />
      <RegenerateControl disabled={cardBusy} onRegenerate={regenerateText} />

      {isV2 && (
        <>
          <details className="story-profile">
            <summary>Dossier — backstory, goal, secrets, relationships, voice</summary>
            <DossierSection storyId={storyId} character={character} castNames={castNames} disabled={cardBusy}
              onChange={onChange} />
          </details>
          <details className="story-profile">
            <summary>Look — build, face, height, palette, wardrobe</summary>
            <LookSection storyId={storyId} character={character} disabled={cardBusy} onChange={onChange} />
          </details>
        </>
      )}

      <VoiceSection
        storyId={storyId}
        character={character}
        pickVoiceIds={pickVoiceIds}
        disabled={cardBusy}
        onChange={onChange}
      />

      <UploadsSection storyId={storyId} character={character} disabled={cardBusy} onChange={onChange} />

      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={handleApprove}
          disabled={cardBusy || Boolean(character.approved_at)}
        >
          {approving ? 'Approving…' : character.approved_at ? 'Approved' : 'Approve'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={handleDelete} disabled={cardBusy}>
          {deleting ? 'Deleting…' : 'Delete character'}
        </button>
      </div>
      <StepError message={approveError} errors={approveErrors} className="story-step-error" />
      <StepError message={deleteError} className="story-step-error" />
    </div>
  )
}

// ------------------------------------------------------------- needs an editor

function NeedsEditorBanner({ storyId, editReadiness, disabled, onChange }) {
  const confirm = useConfirm()
  const [switching, setSwitching] = useState(false)
  const [error, setError] = useState('')

  const switchToPromptOnly = async () => {
    if (!(await confirm({
      title: 'Switch this story to prompt-only consistency?',
      message: 'Shots will be prompted without reference images, so ' +
        'characters and places may drift slightly across shots. This cannot be undone from here.',
      confirmLabel: 'Switch to prompt-only',
    }))) return
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
    <div className="card story-cast-editor-banner">
      <h4 className="card-title">Some sheets need an editor</h4>
      <p>{(editReadiness && editReadiness.message) || 'No editor can make the missing sheets right now.'}</p>
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

function ContinueCast({ storyId, disabled, onChange, consistencyMode }) {
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  useEffect(() => {
    // consistencyMode is not a request parameter, but it flips missing
    // sheets between images and edit_images (workflow.cast_units): refetch
    // when the story switches mode, or this chip would show a stale count
    // (spec 10 finding: "prompt-only sheets estimated as edits").
    fetchStoryEstimate(storyId, 'cast').then(setEstimate).catch(() => setEstimate(null))
  }, [storyId, consistencyMode])

  const handleContinue = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      await runStoryStep(storyId, 'cast', { params: {} })
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
        {running ? <><span className="spinner"></span> Continuing…</> : 'Continue cast'}
      </button>
      <EstimateChip estimate={estimate} />
      {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// ------------------------------------------------------------ the card grid

/** A character's tile in the cast grid: the portrait, the name, the role, the approval and the voice. */
function characterTile(character, info, pickVoiceIds) {
  const portrait = character.refs && character.refs.portrait
  const missing = (info && info.missing) || []
  const voice = character.voice
  return {
    id: character.char_id,
    name: character.name,
    shape: 'portrait',
    thumb: portrait ? { kind: 'characters', eid: character.char_id, name: portrait.name } : null,
    thumbEmpty: missing.includes('text') ? 'Not written yet' : 'No portrait yet',
    meta: (
      <>
        <Badge>{character.role}</Badge>
        {character.approved_at
          ? <Badge tone="success" dot>Approved</Badge>
          : <Badge tone="warning" dot>To approve</Badge>}
        {voice
          ? <Chip icon={Mic} title={`${voice.provider}/${voice.voice_id}`}>{voice.voice_id}</Chip>
          : <Chip icon={Mic} tone={pickVoiceIds.includes(character.char_id) ? 'warning' : 'neutral'}>No voice</Chip>}
      </>
    ),
  }
}

// --------------------------------------------------------------------- page

export default function CastStep({ data, storyId, inFlightJob, onChange }) {
  const { story, characters, progress } = data
  const consistencyMode = story.generation_profile.consistency_mode
  // The open character's editor: the URL's #char_id (one at a time).
  const [openId, toggleOpen] = useHashAccordion((characters || []).map((c) => c.char_id))

  const myJob = inFlightJob && (
    inFlightJob.step === 'cast'
    || (inFlightJob.step === 'regenerate' && inFlightJob.params
        && typeof inFlightJob.params.target === 'string'
        && inFlightJob.params.target.startsWith('character:'))
  ) ? inFlightJob : null

  const { job: liveJob, events, streamState } = useJobFeed(myJob ? myJob.id : null, {
    onJob: (job) => {
      if (job && job.status !== 'queued' && job.status !== 'running') onChange()
    },
  })

  const busy = Boolean(inFlightJob)

  if (!characters || characters.length === 0) {
    return (
      <div className="story-step-body">
        <NoCastYet storyId={storyId} story={story} onChange={onChange} />
        {myJob && liveJob && (
          liveJob.status === 'queued'
            ? <p className="form-hint">queued — waiting for the worker</p>
            : <LiveActivity job={liveJob} events={events} streamState={streamState} />
        )}
      </div>
    )
  }

  const charProgress = progress.characters || {}
  const needsEditor = Object.values(charProgress).some((info) => info.needs_editor)
  const anyMissing = Object.values(charProgress).some((info) => (info.missing || []).length > 0)
  const pickVoiceIds = progress.pick_voice || []
  // Only a v2 story has a dossier and a look (clipping.aistory.defaults.PIPELINE_V2).
  const isV2 = story.generation_profile.pipeline === 'v2'
  const castNames = Object.fromEntries(characters.map((c) => [c.char_id, c.name]))

  return (
    <div className="story-step-body">
      {needsEditor && (
        <NeedsEditorBanner
          storyId={storyId}
          editReadiness={progress.edit_readiness}
          disabled={busy}
          onChange={onChange}
        />
      )}

      {myJob && liveJob && (
        liveJob.status === 'queued'
          ? <p className="form-hint">queued — waiting for the worker</p>
          : <LiveActivity job={liveJob} events={events} streamState={streamState} />
      )}

      <EntityGallery
        storyId={storyId}
        label="Characters"
        items={characters.map((character) => characterTile(character, charProgress[character.char_id], pickVoiceIds))}
        openId={openId}
        onToggle={toggleOpen}
        renderEditor={(item) => {
          const character = characters.find((c) => c.char_id === item.id)
          return (
            <>
              {imagesManual(story) && (
                // Plan 22 stage 5: the story's images are the user's own -- an upload slot per sheet.
                <EntityImageSlots storyId={storyId} kind="characters" entity={character} disabled={busy}
                  onChange={onChange} />
              )}
            <CharacterCard
              key={character.char_id}
              storyId={storyId}
              character={character}
              info={charProgress[character.char_id]}
              pickVoiceIds={pickVoiceIds}
              disabled={busy}
              onChange={onChange}
              consistencyMode={consistencyMode}
              isV2={isV2}
              castNames={castNames}
            />
            </>
          )
        }}
      />

      {anyMissing && (
        <ContinueCast storyId={storyId} disabled={busy} onChange={onChange} consistencyMode={consistencyMode} />
      )}
    </div>
  )
}

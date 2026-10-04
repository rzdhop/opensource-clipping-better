import { useRef, useState } from 'react'
import { uploadToSlot } from '../../api'
import { Button } from '../../ui'
import { ArrowDownToLine } from '../../ui/icons'

// Plan 22 stage 5 (the manual link): one upload slot -- a file input that
// sends the chosen file to an API upload slot (`upload_slot`, from the shot
// brief or the image brief), shows its progress and then the route's own
// answer, or its refusal's reason. Used by the Shot list (clips, keyframes)
// and by the cast, places and props tiles when the story's images are the
// user's own (`generation_profile.images: "manual"`).

/** The API path an entity's own image is uploaded to (the image brief's `upload_slot`). */
export function entityImageSlot(storyId, kind, eid, slot) {
  if (kind === 'characters') return `/api/stories/${storyId}/cast/${eid}/sheet?which=${encodeURIComponent(slot)}`
  if (kind === 'places') return `/api/stories/${storyId}/places/${eid}/plate?variant=${encodeURIComponent(slot)}`
  return `/api/stories/${storyId}/props/${eid}/image`
}

/** Whether a story's images are the user's own uploads. */
export function imagesManual(story) {
  return Boolean(story && story.generation_profile && story.generation_profile.images === 'manual')
}

export default function ManualUploadSlot({ slot, label, accept = 'video/mp4,video/quicktime', disabled, onDone }) {
  const inputRef = useRef(null)
  const [progress, setProgress] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const onPick = async (event) => {
    const file = event.target.files && event.target.files[0]
    event.target.value = ''
    if (!file) return
    setBusy(true)
    setError('')
    setProgress(0)
    try {
      const result = await uploadToSlot(slot, file, (p) => setProgress(p.percent))
      if (onDone) onDone(result)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
      setProgress(null)
    }
  }

  return (
    <div className="manual-upload-slot">
      <input ref={inputRef} type="file" accept={accept} hidden onChange={onPick} aria-label={label} />
      <Button size="sm" icon={ArrowDownToLine} loading={busy} disabled={disabled || busy}
        onClick={() => inputRef.current && inputRef.current.click()}>
        {label}
      </Button>
      {busy && (
        <span className="form-hint">{progress != null ? `Uploading… ${Math.round(progress)} %` : 'Uploading…'}</span>
      )}
      {error && <p className="story-error">{error}</p>}
    </div>
  )
}

/** The upload slots of one entity's images (a character's sheets, a place's plates, a prop's image). */
export function EntityImageSlots({ storyId, kind, entity, disabled, onChange }) {
  const id = entity.char_id || entity.place_id || entity.prop_id
  let slots
  if (kind === 'characters') {
    slots = ['portrait', 'turnaround', 'expressions']
  } else if (kind === 'places') {
    slots = Array.from(new Set(['day', ...Object.keys(entity.time_variants || {})]))
  } else {
    slots = ['image']
  }
  return (
    <div className="manual-upload-slots">
      <p className="form-hint">Your own images: make each one from the image brief, then upload it here.</p>
      {slots.map((slot) => (
        <ManualUploadSlot
          key={slot}
          slot={entityImageSlot(storyId, kind, id, slot)}
          label={`Upload ${slot.replace(/_/g, ' ')}`}
          accept="image/png,image/jpeg,image/webp"
          disabled={disabled}
          onDone={onChange}
        />
      ))}
    </div>
  )
}

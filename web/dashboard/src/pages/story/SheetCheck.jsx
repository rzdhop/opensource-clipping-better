import { useState } from 'react'
import { approveStoryDoc } from '../../api'
import { Badge } from '../../ui'

// Plan 28 F3 (DEC-305 section 5): the sheet judge's verdict on each image of a
// character, a place or a prop (clipping.aistory.steps.judge, `sheet_checks`),
// said in plain words on the Cast and Places tiles. An image with no verdict
// was made before the check existed and says nothing; the human's own image
// (source manual/upload) is warned about, never refused.
//
// Plan 29 stage 5 (DEC-307, the human: "I can approve what I want, you only
// warn of the risk"): a failed image shows "Approve anyway" beside the
// judge's sentence; once approved, the slot says so in one muted line while
// the record (`approved_anyway.slots[slot]`) is still on that very image --
// a regenerate judges a new file, and the record no longer matches it.

const OWN = 'manual/upload'

/** The entity's image ref in *slot*: a character's refs, a place's time variants, a prop's image. */
function refOf(entity, slot) {
  if (entity.refs) return entity.refs[slot] || null
  if (entity.time_variants) return entity.time_variants[slot] || null
  return slot === 'image' ? entity.image || null : null
}

/** True while the human approved this failed image anyway and it is still the image the check saw. */
function approvedAnyway(entity, slot, entry) {
  const record = ((entity.approved_anyway || {}).slots || {})[slot]
  return Boolean(record && entry && entry.image_hash && record.image_hash === entry.image_hash)
}

/** `{tone, text, failed?}` for one image's verdict, or null when it has none (made before the check). */
export function sheetCheckLine(entity, slot) {
  const entry = (entity.sheet_checks || {})[slot]
  const ref = refOf(entity, slot)
  if (!entry || !ref) return null
  const own = ref.source === OWN
  const issues = (entry.issues || []).join('; ')
  if (entry.passed === null || entry.passed === undefined) {
    return own ? null : { tone: 'warning', text: 'Not checked yet: run the step again (the check is free).' }
  }
  if (entry.passed) return { tone: 'success', text: 'Checked: it matches.' }
  if (own) return { tone: 'warning', text: `Your own image. The check saw: ${issues}. It is yours: kept.` }
  if (approvedAnyway(entity, slot, entry)) return { tone: 'muted', text: `Approved by you despite: ${issues}` }
  return { tone: 'danger', text: `Does not match: ${issues}. Regenerate it, or upload your own.`, failed: true }
}

/**
 * The line under one image slot. Given `storyId` and `doc` (the approval,
 * `character:<id>`, `place:<id>` or `prop:<id>`), a failed image also gets
 * "Approve anyway": it approves the whole character, place or prop over the
 * images that did not match, then `onChange()` reloads it.
 */
export function SheetCheckLine({ entity, slot, storyId, doc, disabled, onChange }) {
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const line = sheetCheckLine(entity, slot)
  if (!line) return null
  const text = <p className={`form-hint story-sheet-check story-sheet-check-${line.tone}`}>{line.text}</p>
  if (!line.failed || !storyId || !doc) return text

  const approveAnyway = async () => {
    setWorking(true)
    setError('')
    try {
      await approveStoryDoc(storyId, doc, { approve_anyway: true })
      if (onChange) onChange()
    } catch (err) {
      setError(err.message)
    } finally {
      setWorking(false)
    }
  }

  return (
    <div className="story-sheet-check-failed">
      {text}
      <button type="button" className="btn btn-ghost btn-sm" onClick={approveAnyway}
        disabled={disabled || working}
        title={`Approve ${entity.name || 'it'} as it is: you take the risk the check named.`}>
        {working ? 'Approving…' : 'Approve anyway'}
      </button>
      {error && <p className="form-hint story-sheet-check story-sheet-check-danger">{error}</p>}
    </div>
  )
}

/** One badge on a tile: the worst verdict of the entity's images, each line in its tooltip. */
export function SheetCheckBadge({ entity, slots }) {
  const lines = slots.map((slot) => sheetCheckLine(entity, slot)).filter(Boolean)
  if (lines.length === 0) return null
  const title = lines.map((line) => line.text).join('\n')
  if (lines.some((line) => line.tone === 'danger')) return <Badge tone="danger" title={title}>Image does not match</Badge>
  if (lines.some((line) => line.tone === 'warning')) return <Badge tone="warning" title={title}>Check pending</Badge>
  if (lines.some((line) => line.tone === 'muted')) return <Badge tone="neutral" title={title}>Approved despite the check</Badge>
  return <Badge tone="success" title={title}>Images checked</Badge>
}

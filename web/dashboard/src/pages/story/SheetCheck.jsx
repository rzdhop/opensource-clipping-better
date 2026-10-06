import { Badge } from '../../ui'

// Plan 28 F3 (DEC-305 section 5): the sheet judge's verdict on each image of a
// character, a place or a prop (clipping.aistory.steps.judge, `sheet_checks`),
// said in plain words on the Cast and Places tiles. An image with no verdict
// was made before the check existed and says nothing; the human's own image
// (source manual/upload) is warned about, never refused.

const OWN = 'manual/upload'

/** The entity's image ref in *slot*: a character's refs, a place's time variants, a prop's image. */
function refOf(entity, slot) {
  if (entity.refs) return entity.refs[slot] || null
  if (entity.time_variants) return entity.time_variants[slot] || null
  return slot === 'image' ? entity.image || null : null
}

/** `{tone, text}` for one image's verdict, or null when it has none (made before the check). */
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
  return { tone: 'danger', text: `Does not match: ${issues}. Regenerate it, or upload your own.` }
}

/** The line under one image slot. */
export function SheetCheckLine({ entity, slot }) {
  const line = sheetCheckLine(entity, slot)
  if (!line) return null
  return <p className={`form-hint story-sheet-check story-sheet-check-${line.tone}`}>{line.text}</p>
}

/** One badge on a tile: the worst verdict of the entity's images, each line in its tooltip. */
export function SheetCheckBadge({ entity, slots }) {
  const lines = slots.map((slot) => sheetCheckLine(entity, slot)).filter(Boolean)
  if (lines.length === 0) return null
  const title = lines.map((line) => line.text).join('\n')
  if (lines.some((line) => line.tone === 'danger')) return <Badge tone="danger" title={title}>Image does not match</Badge>
  if (lines.some((line) => line.tone === 'warning')) return <Badge tone="warning" title={title}>Check pending</Badge>
  return <Badge tone="success" title={title}>Images checked</Badge>
}

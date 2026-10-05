import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchImageBrief, fetchStoryMediaUrl } from '../../../api'
import { Badge, Button, useToast } from '../../../ui'
import { ArrowDownToLine, Copy } from '../../../ui/icons'
import { copyText, revealForManualCopy } from '../../../lib/clipboard'
import { copyLabel, fitNote } from '../../../lib/promptFit'
import { useStoryMediaUrl } from '../EntityGallery'
import ManualUploadSlot from '../ManualUploadSlot'
import './PromptDrawer.css'

// Plan 25 stage 4 (D-3, second half): the Cast, Places and Props tiles of a
// manual-images story show what to make next to where to upload it. One
// entry of the image brief (`GET /api/stories/{id}/image-brief`, tile images
// only) renders as a compact drawer: the label and the size, "Copy prompt",
// the prompt itself folded away, the reference to start from, the state and
// the tile's own upload. Without an entry the tiles render as before.

/** The key of one image inside an entity's entries: its sheet/plate slot, and the variant it belongs to. */
export function entryKey(slot, variantId = null) {
  return variantId ? `${slot}|${variantId}` : slot
}

/**
 * The image brief's entries grouped as `{ characters|places|props: { <eid>: { <entryKey>: entry } } }`.
 * A variant sheet's id is "<eid>:<vid>" (DEC-299): it is filed under the character, keyed with its variant.
 * Keyframe entries (an episode's shots) are not tile images and are left out.
 */
export function groupBrief(images) {
  const grouped = {}
  for (const entry of images || []) {
    if (!['characters', 'places', 'props'].includes(entry.entity)) continue
    const eid = entry.variant_id ? String(entry.id).slice(0, String(entry.id).length - entry.variant_id.length - 1)
      : entry.id
    const byKind = grouped[entry.entity] || (grouped[entry.entity] = {})
    const byEntity = byKind[eid] || (byKind[eid] = {})
    byEntity[entryKey(entry.slot, entry.variant_id || null)] = entry
  }
  return grouped
}

/** One entity's entries (`{ <entryKey>: entry }`), or undefined without a brief or without any for it. */
export function entityBrief(images, kind, eid) {
  return images && images[kind] ? images[kind][eid] : undefined
}

/**
 * The story's image brief, loaded once when its images are the user's own and again on `reload()`
 * (after an upload or an edit). `images` is null while loading, on a failure and for a story whose
 * images the app makes: the tiles then render without drawers.
 */
export function useImageBrief(storyId, enabled) {
  const [images, setImages] = useState(null)
  const [version, setVersion] = useState(0)
  useEffect(() => {
    if (!enabled) { setImages(null); return undefined }
    let cancelled = false
    fetchImageBrief(storyId)
      .then((brief) => { if (!cancelled) setImages(groupBrief(brief.images)) })
      .catch(() => { if (!cancelled) setImages(null) })
    return () => { cancelled = true }
  }, [storyId, enabled, version])
  const reload = useCallback(() => setVersion((current) => current + 1), [])
  return { images, reload }
}

/** "1080×1920, at least 360×640". */
export function sizeLine(entry) {
  return `${entry.size[0]}×${entry.size[1]}, at least ${entry.min_size[0]}×${entry.min_size[1]}`
}

/**
 * A copy button (``lib/clipboard.js``: the Clipboard API, else ``execCommand('copy')``, which
 * works over plain http). When both fail the text is shown in a textarea, selected, for a manual copy.
 */
function CopyText({ text, children, primary = false }) {
  const toast = useToast()
  const areaRef = useRef(null)
  const [selecting, setSelecting] = useState(false)

  // React un-hides the textarea first; selecting needs it laid out.
  useEffect(() => {
    if (selecting) revealForManualCopy(areaRef.current, text)
  }, [selecting, text])

  const copy = async () => {
    if (await copyText(text)) {
      setSelecting(false)
      toast.success('Copied')
      return
    }
    toast.info('Copy failed here — long-press the selected text and copy it.')
    setSelecting(true)
  }

  return (
    <>
      <Button variant={primary ? 'primary' : 'secondary'} size={primary ? 'md' : 'sm'} icon={Copy} onClick={copy}
        className={primary ? 'prompt-drawer-copy' : ''}>
        {children}
      </Button>
      <textarea ref={areaRef} hidden={!selecting} readOnly aria-label="The text to copy" className="prompt-drawer-area"
        rows={4} value={selecting ? text : ''} onChange={() => {}} data-selecting={selecting ? 'true' : undefined} />
      {selecting && <p className="form-hint">Selected: long-press it and choose Copy.</p>}
    </>
  )
}

/** The reference image of an entry: a thumbnail and a download of the full-size file. */
function ReferenceThumb({ storyId, reference, edit }) {
  const parts = (reference.path || '').split('/')
  const kind = parts[0]
  const eid = parts[1]
  const thumb = useStoryMediaUrl(storyId, kind, eid, reference.name, { thumb: true })
  const [busy, setBusy] = useState(false)
  const download = async () => {
    setBusy(true)
    try {
      const url = await fetchStoryMediaUrl(storyId, kind, eid, reference.name)
      const link = document.createElement('a')
      link.href = url
      link.download = reference.file || reference.name
      document.body.appendChild(link)
      link.click()
      link.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      // The thumbnail already says whether the image is there; nothing to add.
    } finally {
      setBusy(false)
    }
  }
  return (
    <figure className="prompt-drawer-ref">
      {thumb ? <img src={thumb} alt={reference.label} loading="lazy" /> : <span className="prompt-drawer-ref-empty" />}
      <figcaption>
        <span>{edit ? 'Edit this image' : 'Reference'}: {reference.label}</span>
        <Button size="sm" variant="ghost" icon={ArrowDownToLine} loading={busy} onClick={download}>Download</Button>
      </figcaption>
    </figure>
  )
}

/** One image-brief entry as a drawer: what to make, how big, from what, and where to upload it. */
export default function PromptDrawer({ storyId, entry, disabled, onDone }) {
  const references = (entry.references || []).map((reference) => ({ reference, edit: false }))
  if (entry.reference) references.push({ reference: entry.reference, edit: true })
  const uploaded = entry.state === 'uploaded'
  const slotName = entry.slot.replace(/_/g, ' ')
  const uploadLabel = `${uploaded ? 'Replace' : 'Upload'} ${entry.variant_label ? `${entry.variant_label} ` : ''}${slotName}`
  return (
    <div className="prompt-drawer">
      <div className="prompt-drawer-head">
        <strong>{entry.label}</strong>
        <Badge tone={uploaded ? 'success' : 'warning'} dot>{uploaded ? 'Uploaded' : 'Missing'}</Badge>
      </div>
      <p className="form-hint">Make it at {sizeLine(entry)}.</p>
      <div className="prompt-drawer-actions">
        <CopyText text={entry.prompt} primary>{copyLabel('Copy prompt', entry.fit)}</CopyText>
        {entry.negative_prompt && <CopyText text={entry.negative_prompt}>Copy negative</CopyText>}
      </div>
      {fitNote(entry.fit) && <p className="form-hint prompt-drawer-fit">{fitNote(entry.fit)}</p>}
      <details className="prompt-drawer-prompt">
        <summary>Prompt</summary>
        <pre>{entry.prompt}</pre>
      </details>
      {references.length > 0 && (
        <div className="prompt-drawer-refs">
          {references.map(({ reference, edit }) => (
            <ReferenceThumb key={reference.path || reference.name} storyId={storyId} reference={reference} edit={edit} />
          ))}
        </div>
      )}
      {'reference' in entry && !entry.reference && (
        <p className="form-hint">Upload the character&apos;s portrait first: every variant sheet is an edit of it.</p>
      )}
      <ManualUploadSlot
        slot={entry.upload_slot}
        label={uploadLabel}
        accept="image/png,image/jpeg,image/webp"
        disabled={disabled}
        onDone={onDone}
      />
    </div>
  )
}

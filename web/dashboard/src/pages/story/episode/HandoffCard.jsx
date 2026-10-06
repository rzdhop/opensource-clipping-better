import { useEffect, useRef, useState } from 'react'
import { downloadApiFile, fetchApiObjectUrl, generateClips, patchShotMode, regenerateStory } from '../../../api'
import { Badge, Button, useToast } from '../../../ui'
import { AlertTriangle, ArrowDownToLine, ChevronDown, Copy, Sparkles } from '../../../ui/icons'
import { copyText, revealForManualCopy } from '../../../lib/clipboard'
import { formatCents, formatUsd } from '../../../lib/format'
import { copyLabel, fitNote } from '../../../lib/promptFit'
import BudgetRefusal, { isBudgetRefusal } from '../BudgetRefusal'
import ManualUploadSlot from '../ManualUploadSlot'

// The Handoff view's cards (plan 25 stage 3, D-3): one per clip, keyframe or
// entity image of the handoff document (GET .../episodes/{ep}/handoff),
// collapsed to a head and opened one at a time. An open shot card carries the
// Mode control (Auto / My own, PATCH .../shots/{sid}/mode); under My own the
// prompt to copy, the references, the checks and the upload slot; under Auto
// the link, the estimate, the gate's verdict and "Generate this shot".

// A clip's state (the shot brief's states).
export const CLIP_STATES = {
  missing: { tone: 'warning', label: 'Missing' },
  uploaded: { tone: 'info', label: 'Uploaded' },
  take_ok: { tone: 'success', label: 'Take ok' },
  mismatch: { tone: 'danger', label: 'Take mismatch' },
  approximate: { tone: 'neutral', label: 'Approximate' },
  // Plan 23 stage B8: a stock cutaway -- footage from a stock source, nothing to make or upload.
  stock: { tone: 'info', label: 'Stock footage' },
}

// A keyframe's or an entity image's state.
export const IMAGE_STATES = {
  missing: { tone: 'warning', label: 'Missing' },
  uploaded: { tone: 'success', label: 'Uploaded' },
  made: { tone: 'success', label: 'Made' },
}

// What the speech check made of an uploaded take (workflow.episode_clips' take.state).
const TAKE_LABELS = { ok: 'matched', mismatch: 'mismatch', no_speech: 'no speech', stt_unavailable: 'unchecked (approximate)' }

const SOURCE_LABELS = { upload: 'your upload', generated: 'generated', stock: 'stock footage' }

const IMAGE_ACCEPT = 'image/png,image/jpeg,image/webp'
const VIDEO_ACCEPT = 'video/mp4,video/quicktime'

const MODE_LOCKED = {
  clip: 'Each clip’s own mode needs a story with native speech; the story’s profile decides here.',
  image: 'Each keyframe’s own mode needs a v2 story; the story’s profile decides here.',
}

/** "1080×1920". */
export function sizeLabel(size) {
  return Array.isArray(size) && size.length === 2 ? `${size[0]}×${size[1]}` : ''
}

/** "8 s (planned 6 s)", or "8 s" when the two agree. */
export function lengthLabel(length, planned) {
  if (length == null) return planned != null ? `${planned} s` : ''
  return planned != null && planned !== length ? `${length} s (planned ${planned} s)` : `${length} s`
}

/**
 * Copy text to the clipboard (``lib/clipboard.js``: the Clipboard API, else a
 * user-gesture ``execCommand('copy')``, which also works over plain http on a
 * phone). When every automatic route fails the text is shown, selected and
 * scrolled into view, for a long-press copy.
 * Returns `{copy, fallback}`: render `fallback` once in the card.
 */
export function useCopy() {
  const toast = useToast()
  const areaRef = useRef(null)
  const [manual, setManual] = useState(null) // {text} while the manual textarea is shown

  // The textarea is un-hidden by React first; selecting needs it laid out.
  useEffect(() => {
    if (manual) revealForManualCopy(areaRef.current, manual.text)
  }, [manual])

  const copy = async (text, what) => {
    if (!text) return
    if (await copyText(text)) {
      setManual(null)
      toast.success(`Copied${what ? ` — ${what}` : ''}`)
      return
    }
    toast.info('Copy failed here — long-press the selected text and copy it.')
    setManual({ text })
  }

  const fallback = (
    <div className="handoff-copy-fallback">
      <textarea ref={areaRef} hidden={!manual} readOnly rows={4} aria-label="Text to copy"
        value={manual ? manual.text : ''} onChange={() => {}} />
      {manual && <p className="form-hint">Selected: long-press it and choose Copy (Ctrl+C, or Cmd+C on a Mac).</p>}
    </div>
  )
  return { copy, fallback }
}

/** One reference image: a thumbnail (fetched with the auth header) and its own download. */
function RefThumb({ reference }) {
  const [url, setUrl] = useState(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    if (!reference.url) return undefined
    let cancelled = false
    let current = null
    fetchApiObjectUrl(reference.url).then((fresh) => {
      if (cancelled) { URL.revokeObjectURL(fresh); return }
      current = fresh
      setUrl(fresh)
    }).catch(() => { if (!cancelled) setFailed(true) })
    return () => {
      cancelled = true
      if (current) URL.revokeObjectURL(current)
    }
  }, [reference.url])
  return (
    <figure className="handoff-ref">
      {url ? <img src={url} alt={reference.label} loading="lazy" />
        : <span className="handoff-ref-empty">{failed ? 'Not available' : ''}</span>}
      <figcaption>
        <span>{reference.number != null ? `${reference.number}. ` : ''}{reference.label}</span>
        {url && (
          <a href={url} download={reference.file || reference.name} className="handoff-ref-download">
            <ArrowDownToLine size={12} aria-hidden="true" /> Download
          </a>
        )}
      </figcaption>
    </figure>
  )
}

/** The references as a thumbnail row, with the shot's zip when it has one. */
function References({ references, zipUrl, zipName }) {
  const toast = useToast()
  const [saving, setSaving] = useState(false)
  if (!references || references.length === 0) return null
  const saveAll = async () => {
    setSaving(true)
    try {
      await downloadApiFile(zipUrl, zipName)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setSaving(false)
    }
  }
  return (
    <div className="handoff-refs-block">
      <div className="handoff-refs">
        {references.map((reference) => <RefThumb key={reference.file || reference.path} reference={reference} />)}
      </div>
      {zipUrl && (
        <Button size="sm" icon={ArrowDownToLine} loading={saving} onClick={saveAll}>
          Download all for this shot
        </Button>
      )}
    </div>
  )
}

/** The checks as real checkboxes: ticked on this screen only, nothing saved. */
function Checks({ checks }) {
  const [ticked, setTicked] = useState({})
  if (!checks || checks.length === 0) return null
  return (
    <fieldset className="handoff-checks">
      <legend className="form-hint">Before you upload</legend>
      {checks.map((check) => (
        <label key={check} className="story-checkbox">
          <input type="checkbox" checked={Boolean(ticked[check])}
            onChange={(event) => setTicked((prev) => ({ ...prev, [check]: event.target.checked }))} />
          {check}
        </label>
      ))}
    </fieldset>
  )
}

/** The prompt itself, folded so the card stays short. */
function PromptText({ prompt, negative }) {
  return (
    <details className="handoff-prompt">
      <summary>Show the prompt</summary>
      <pre>{prompt}</pre>
      {negative && (
        <>
          <p className="form-hint">Negative prompt</p>
          <pre>{negative}</pre>
        </>
      )}
    </details>
  )
}

/**
 * What the fit of a prompt to its link did ("Fitted to 1800 words for this
 * link: ..."), and its short-prompt warning, as a warning row. Nothing when
 * the block carries neither (a v1 story).
 */
function FitNote({ fit, warning }) {
  const note = fitNote(fit)
  if (!note && !warning) return null
  return (
    <>
      {note && <p className="form-hint handoff-fit-note">{note}</p>}
      {warning && <p className="chip chip-warn chip-wrap handoff-prompt-warning" role="status">{warning}</p>}
    </>
  )
}

/** The take's verdict on an uploaded clip: what the speech check heard. */
function TakeVerdict({ take }) {
  if (!take) return null
  const matched = take.matched != null ? ` · ${Math.round(take.matched * 100)} % of the line` : ''
  return (
    <p className="form-hint handoff-take">
      Take: {TAKE_LABELS[take.state] || take.state}{matched}
      {take.heard ? <> — heard “{take.heard}”</> : null}
    </p>
  )
}

/**
 * The segmented "Mode: Auto · My own" of one shot's clip or keyframe. A
 * change PATCHes the shot's mode and shows what it made stale and the gate's
 * verdict on the new mode; the page refetches the document.
 */
function ModeControl({ storyId, ep, shot, which, block, onChanged }) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [note, setNote] = useState(null)
  const locked = !block.mode_editable

  const choose = async (mode) => {
    if (mode === block.mode || locked) return
    setSaving(true)
    setError('')
    try {
      const result = await patchShotMode(storyId, ep, shot.shot_id, { [which]: mode })
      setNote({ stale: result.stale || [], verdict: (result.verdict || {})[which] || null })
      onChanged()
    } catch (err) {
      setError(err.status === 409 ? `${err.message} Try again once the step is done.` : err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="handoff-mode">
      <span className="handoff-mode-label" id={`mode-${which}-${shot.shot_id}`}>Mode</span>
      <div className="story-segmented handoff-segmented" role="group" aria-labelledby={`mode-${which}-${shot.shot_id}`}
        title={locked ? MODE_LOCKED[which] : undefined}>
        <Button size="sm" variant={block.mode === 'manual' ? 'secondary' : 'primary'} aria-pressed={block.mode !== 'manual'}
          disabled={locked || saving} loading={saving && block.mode === 'manual'} onClick={() => choose('auto')}>
          <span>Auto</span>
        </Button>
        <Button size="sm" variant={block.mode === 'manual' ? 'primary' : 'secondary'} aria-pressed={block.mode === 'manual'}
          disabled={locked || saving} loading={saving && block.mode !== 'manual'} onClick={() => choose('manual')}>
          <span>My own</span>
        </Button>
      </div>
      {locked && <p className="form-hint">{MODE_LOCKED[which]}</p>}
      {error && <p className="story-error">{error}</p>}
      {note && note.stale.length > 0 && (
        <p className="chip chip-warn chip-wrap">Now out of date: {note.stale.join(', ')}.</p>
      )}
      {note && note.verdict && (
        <p className="form-hint">
          {note.verdict.allowed
            ? `Made on ${note.verdict.link || 'the API link'} · est. $${formatUsd(note.verdict.est_usd)}.`
            : note.verdict.reason}
        </p>
      )}
    </div>
  )
}

/** Under Auto: the link, the estimate, the gate's verdict and "Generate this shot". */
function AutoBody({ storyId, ep, shot, which, block, onChanged }) {
  const toast = useToast()
  const [running, setRunning] = useState(false)
  const [queued, setQueued] = useState(false)
  const [error, setError] = useState(null)
  const gate = block.gate
  const target = which === 'clip' ? `shot:${ep}:${shot.shot_id}:video` : `shot:${ep}:${shot.shot_id}`

  const generate = async () => {
    setRunning(true)
    setError(null)
    try {
      await regenerateStory(storyId, { target })
      setQueued(true)
      toast.success(`Queued: shot ${shot.shot_id}'s ${which === 'clip' ? 'clip' : 'keyframe'}.`)
      onChanged()
    } catch (err) {
      setError(err)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="handoff-auto">
      <p className="handoff-auto-line">
        {block.link ? <>On <code>{block.link}</code></> : 'On the episode’s API link'}
        {` · est. $${formatUsd(block.est_usd)}`}
        {block.state !== 'missing' && ` · now: ${SOURCE_LABELS[block.source] || block.state}`}
      </p>
      {gate && gate.allowed ? (
        <Button variant="primary" icon={Sparkles} loading={running} disabled={queued} onClick={generate}>
          {queued ? 'Queued' : block.state === 'missing' ? 'Generate this shot' : 'Generate this shot again'}
        </Button>
      ) : gate ? (
        <div className="story-error story-alert" role="status">
          <AlertTriangle size={15} aria-hidden="true" className="story-alert-icon" />
          <div className="story-alert-body">{gate.reason || 'The gate refuses this shot for now.'}</div>
        </div>
      ) : null}
      {error && (isBudgetRefusal(error.code, error.detail)
        ? <BudgetRefusal detail={error.detail} storyId={storyId} retryLabel="Generate this shot" />
        : <p className="story-error">{error.message}</p>)}
    </div>
  )
}

/**
 * Plan 28 A6: the Generate button of one of your own clips still missing
 * (`price`: the handoff's `clip.generate_price`, priced by the server) or,
 * with no `shotId`, of all of them (the document's `generate_price`). The
 * price is on the button; a refusal (a cap, no key) is shown in its place,
 * in plain words, and nothing is bought. A click switches the shot(s) to
 * Auto and starts the clip job.
 */
export function GenerateClipsButton({ storyId, ep, shotId = null, price, onDone }) {
  const toast = useToast()
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)
  if (!price || (shotId == null && !price.count)) return null
  const label = shotId
    ? `Generate this clip — ${formatCents(price.usd)}`
    : `Generate all missing clips — ${formatCents(price.usd)}`

  const run = async () => {
    setRunning(true)
    setError(null)
    try {
      await generateClips(storyId, ep, shotId)
      toast.success(shotId ? `Queued: shot ${shotId}'s clip.` : 'Queued: every missing clip.')
      if (onDone) onDone()
    } catch (err) {
      setError(err)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="handoff-generate">
      {price.allowed ? (
        <Button variant="secondary" icon={Sparkles} loading={running} onClick={run}>{label}</Button>
      ) : (
        <p className="form-hint handoff-generate-refused">{label}: {price.reason}</p>
      )}
      {error && (isBudgetRefusal(error.code, error.detail)
        ? <BudgetRefusal detail={error.detail} storyId={storyId} retryLabel={label} />
        : <p className="story-error">{error.message}</p>)}
    </div>
  )
}

/** Plan 28 A6: the warnings said before any click (no speech check, a provider's refusal). */
export function GenerateWarnings({ warnings }) {
  if (!warnings || warnings.length === 0) return null
  return (
    <ul className="handoff-warnings">
      {warnings.map((line) => <li key={line} className="chip chip-warn chip-wrap">{line}</li>)}
    </ul>
  )
}

/**
 * An exchange shot's lines in order (plan 27): `block.lines` when it holds two
 * or more, else null (a one-line row keeps `block.speaker` / `block.line`).
 */
export function exchangeLines(block) {
  return Array.isArray(block.lines) && block.lines.length >= 2 ? block.lines : null
}

/** What "Copy line" copies: an exchange's lines, one "Speaker: text" per line, else the one line. */
export function lineToCopy(block) {
  const lines = exchangeLines(block)
  return lines ? lines.map((line) => `${line.speaker}: ${line.text}`).join('\n') : block.line
}

/** Under My own: how, the copy buttons, the prompt, references, checks, the upload and the take. */
function ManualBody({ storyId, ep, shot, which, block, platformInfo, onUploaded, copy }) {
  const clip = which === 'clip'
  const how = clip ? (block.how || platformInfo.where_to_paste)
    : `Make it at ${sizeLabel(block.size)} (at least ${sizeLabel(block.min_size)}) in your image tool.`
  const exchange = clip ? exchangeLines(block) : null
  return (
    <div className="handoff-manual">
      <p className="handoff-how">{how}</p>
      {exchange ? (
        <ol className="handoff-lines">
          {exchange.map((line, index) => (
            <li key={line.line_id || index} className="handoff-line">
              <strong>{line.speaker}:</strong> “{line.text}”
              {line.voice_line && <span className="form-hint"> — {line.voice_line}</span>}
            </li>
          ))}
        </ol>
      ) : clip && block.line && (
        <p className="handoff-line">
          <strong>{block.speaker}:</strong> “{block.line}”
          {block.voice_line && <span className="form-hint"> — {block.voice_line}</span>}
        </p>
      )}
      <div className="handoff-copy-row">
        <Button variant="primary" icon={Copy} className="handoff-copy-main" onClick={() => copy(block.prompt, 'prompt')}>
          {copyLabel('Copy prompt', block.fit)}
        </Button>
        {clip && block.line && (
          <Button size="sm" icon={Copy} onClick={() => copy(lineToCopy(block), exchange ? 'lines' : 'line')}>Copy line</Button>
        )}
        {block.negative_prompt && (
          <Button size="sm" icon={Copy} onClick={() => copy(block.negative_prompt, 'negative')}>Copy negative</Button>
        )}
      </div>
      <FitNote fit={block.fit} warning={block.prompt_warning} />
      <PromptText prompt={block.prompt} />
      <References references={block.references} zipUrl={block.zip_url}
        zipName={`${shot.shot_id}_${which}_references.zip`} />
      {clip && <Checks checks={block.checks} />}
      {block.stock && <p className="form-hint">{block.stock}. Upload your own clip to replace it.</p>}
      <ManualUploadSlot
        slot={block.upload_slot}
        label={`${block.state === 'missing' ? 'Upload' : 'Replace'} ${clip ? 'clip' : 'keyframe'}`}
        accept={clip ? VIDEO_ACCEPT : IMAGE_ACCEPT}
        onDone={onUploaded}
      />
      {clip && <TakeVerdict take={block.take} />}
      {clip && block.state === 'missing' && (
        <GenerateClipsButton storyId={storyId} ep={ep} shotId={shot.shot_id} price={block.generate_price}
          onDone={onUploaded} />
      )}
    </div>
  )
}

/** The card's head: a button that opens or closes it, with its chips. */
function CardHead({ open, onToggle, controls, title, chips }) {
  return (
    <button type="button" className="handoff-card-head" aria-expanded={open} aria-controls={controls} onClick={onToggle}>
      <span className="handoff-card-title">{title}</span>
      <span className="handoff-card-chips">{chips}</span>
      <ChevronDown size={16} aria-hidden="true" className="handoff-card-chevron" />
    </button>
  )
}

function modeChip(mode) {
  return <Badge tone={mode === 'manual' ? 'accent' : 'neutral'}>{mode === 'manual' ? 'My own' : 'Auto'}</Badge>
}

/** One shot's clip (`which` = clip) or keyframe (`which` = image) card. */
export function ShotHandoffCard({ storyId, ep, shot, which, platformInfo, domId, open, onToggle, onChanged, onUploaded }) {
  const { copy, fallback } = useCopy()
  const block = which === 'clip' ? shot.clip : shot.image
  const states = which === 'clip' ? CLIP_STATES : IMAGE_STATES
  const state = states[block.state] || states.missing
  const bodyId = `${domId}-body`
  const chips = (
    <>
      <Badge tone={shot.speaks ? 'accent' : 'neutral'}>{shot.speaks ? 'Speaks' : 'Silent'}</Badge>
      <span className="handoff-card-meta">
        {which === 'clip' ? lengthLabel(shot.length_s, shot.clip_s) : sizeLabel(block.size)}
      </span>
      <Badge tone={state.tone} dot>{state.label}</Badge>
      {modeChip(block.mode)}
    </>
  )
  return (
    <li className={`handoff-card${open ? ' handoff-card-open' : ''}`} id={domId}>
      <CardHead open={open} onToggle={onToggle} controls={bodyId} title={`${shot.order}. ${shot.shot_id}`} chips={chips} />
      {open && (
        <div className="handoff-card-body" id={bodyId}>
          <p className="handoff-purpose">{shot.purpose}</p>
          <ModeControl storyId={storyId} ep={ep} shot={shot} which={which} block={block} onChanged={onChanged} />
          {block.mode === 'manual' ? (
            <ManualBody storyId={storyId} ep={ep} shot={shot} which={which} block={block} platformInfo={platformInfo}
              onUploaded={onUploaded} copy={copy} />
          ) : (
            <AutoBody storyId={storyId} ep={ep} shot={shot} which={which} block={block} onChanged={onChanged} />
          )}
          {fallback}
        </div>
      )}
    </li>
  )
}

/** One sheet, plate or prop image: the prompt to copy, its size, its reference and its upload. */
export function EntityHandoffCard({ entity, domId, open, onToggle, onUploaded }) {
  const { copy, fallback } = useCopy()
  const state = IMAGE_STATES[entity.state] || IMAGE_STATES.missing
  const bodyId = `${domId}-body`
  const chips = (
    <>
      <span className="handoff-card-meta">{sizeLabel(entity.size)}</span>
      <Badge tone={state.tone} dot>{state.label}</Badge>
      {modeChip(entity.mode)}
    </>
  )
  const hasReference = Object.prototype.hasOwnProperty.call(entity, 'reference')
  return (
    <li className={`handoff-card${open ? ' handoff-card-open' : ''}`} id={domId}>
      <CardHead open={open} onToggle={onToggle} controls={bodyId} title={entity.label} chips={chips} />
      {open && (
        <div className="handoff-card-body" id={bodyId}>
          <p className="handoff-how">
            Make it at {sizeLabel(entity.size)} (at least {sizeLabel(entity.min_size)}) in your image tool.
          </p>
          <div className="handoff-copy-row">
            <Button variant="primary" icon={Copy} className="handoff-copy-main" onClick={() => copy(entity.prompt, 'prompt')}>
              {copyLabel('Copy prompt', entity.fit)}
            </Button>
            {entity.negative_prompt && (
              <Button size="sm" icon={Copy} onClick={() => copy(entity.negative_prompt, 'negative')}>Copy negative</Button>
            )}
          </div>
          <FitNote fit={entity.fit} />
          <PromptText prompt={entity.prompt} />
          {hasReference && (entity.reference ? (
            <div className="handoff-refs-block">
              <p className="form-hint">Edit this reference into the look{entity.variant_label ? ` “${entity.variant_label}”` : ''}:</p>
              <div className="handoff-refs"><RefThumb reference={entity.reference} /></div>
            </div>
          ) : (
            <p className="form-hint">Upload the character&apos;s portrait first: every variant sheet is an edit of it.</p>
          ))}
          <References references={entity.references} />
          <ManualUploadSlot
            slot={entity.upload_slot}
            label={entity.state === 'missing' ? 'Upload image' : 'Replace image'}
            accept={IMAGE_ACCEPT}
            onDone={onUploaded}
          />
          {fallback}
        </div>
      )}
    </li>
  )
}

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { downloadApiFile, fetchHandoff, patchHandoff } from '../../../api'
import { Button, Spinner, useToast } from '../../../ui'
import { ArrowDownToLine, Copy, FileText, FastForward } from '../../../ui/icons'
import { EntityHandoffCard, ShotHandoffCard, useCopy } from './HandoffCard'

// The Handoff view (plan 25 stage 3, D-3; DEC-301 retired the Shot list for
// it): everything of one episode that is made outside the app -- or could
// be -- on one phone-first page, read from the handoff document (GET
// /stories/{id}/episodes/{ep}/handoff). A sticky header (the progress, the
// "Missing only" filter, "Next missing", the export), the platform row
// (Flow / Higgsfield and the model, remembered by PATCH .../handoff), then
// the Clips, Keyframes and "Sheets, plates & props" sections: one card per
// item, one open at a time. The same page on a desktop, in two columns.

/** The Handoff route of one episode. */
export function handoffPath(storyId, ep) {
  return `/story/${storyId}/episodes/${ep}/handoff`
}

/**
 * Which stepper nodes lead to the Handoff: `{keyframes, clips}`, each true
 * when something there is the human's -- a row of the document's `missing`
 * (the human's rows only) or a shot in My own mode.
 */
export function handoffLinks(doc) {
  if (!doc) return { keyframes: false, clips: false }
  const missing = doc.missing || []
  const shots = doc.shots || []
  return {
    keyframes: missing.some((item) => item.kind !== 'clip')
      || shots.some((shot) => shot.image && shot.image.mode === 'manual'),
    clips: missing.some((item) => item.kind === 'clip')
      || shots.some((shot) => shot.clip && shot.clip.mode === 'manual'),
  }
}

const ENTITY_WORDS = { sheet: 'sheets', plate: 'plates', prop: 'props' }

/** "sheets", "sheets & plates", "sheets, plates & props": the entity kinds present. */
function entityWord(entities) {
  const kinds = Object.keys(ENTITY_WORDS).filter((kind) => entities.some((entity) => entity.kind === kind))
  const words = kinds.map((kind) => ENTITY_WORDS[kind])
  if (words.length <= 1) return words[0] || 'images'
  return `${words.slice(0, -1).join(', ')} & ${words[words.length - 1]}`
}

/** The progress chips: "3 of 10 clips", "2 of 10 keyframes", "4 of 6 sheets" -- an empty group hidden. */
function progressChips(doc) {
  const counts = doc.counts || {}
  return [
    ['clips', 'clips'],
    ['keyframes', 'keyframes'],
    ['entities', entityWord(doc.entities || [])],
  ].filter(([name]) => counts[name] && counts[name].total > 0)
    .map(([name, word]) => {
      const group = counts[name]
      return { name, text: `${group.done} of ${group.total} ${word}`, complete: group.missing === 0 }
    })
}

// The cards' keys: a clip and a keyframe by their shot, an entity image by its upload slot.
const clipKey = (shotId) => `clip:${shotId}`
const keyframeKey = (shotId) => `keyframe:${shotId}`
const entityKey = (slot) => `entity:${slot}`

/** The card key a `missing` row names. */
function keyOfMissing(item) {
  if (!item) return null
  if (item.kind === 'clip') return clipKey(item.shot_id || item.id)
  if (item.kind === 'keyframe') return keyframeKey(item.shot_id || item.id)
  return entityKey(item.upload_slot)
}

/** A DOM id for a card key ("handoff-clip-sh03"). */
function domIdOf(key) {
  return `handoff-${key.replace(/[^A-Za-z0-9_-]+/g, '-')}`
}

/** The cards of the page, in order, with whether each one is missing. */
function cardsOf(doc) {
  const shots = doc.shots || []
  return {
    clips: shots.filter((shot) => shot.clip).map((shot) => ({ key: clipKey(shot.shot_id), shot, missing: shot.clip.state === 'missing' })),
    keyframes: shots.map((shot) => ({ key: keyframeKey(shot.shot_id), shot, missing: shot.image.state === 'missing' })),
    entities: (doc.entities || []).map((entity) => ({ key: entityKey(entity.upload_slot), entity, missing: entity.state === 'missing' })),
  }
}

/** The export menu: the brief as markdown, the clip brief's zip and the image brief's zip. */
function ExportMenu({ doc, platformName }) {
  const toast = useToast()
  const [busy, setBusy] = useState('')
  const menuRef = useRef(null)
  const ep = String(doc.ep).padStart(2, '0')
  const close = () => { if (menuRef.current) menuRef.current.open = false }
  const save = async (what, url, filename) => {
    setBusy(what)
    try {
      await downloadApiFile(url, filename)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setBusy('')
      close()
    }
  }
  const saveMarkdown = () => {
    const blob = new Blob([doc.export.brief_md], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `shot_brief_ep${ep}_${platformName}.md`
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
    close()
  }
  return (
    <details className="handoff-export" ref={menuRef}>
      <summary className="btn btn-secondary btn-sm ui-btn"><FileText size={14} aria-hidden="true" /> Export</summary>
      <div className="handoff-export-menu">
        <Button size="sm" variant="ghost" icon={FileText} onClick={saveMarkdown}>The brief (markdown)</Button>
        <Button size="sm" variant="ghost" icon={ArrowDownToLine} loading={busy === 'clips'}
          onClick={() => save('clips', doc.export.brief_zip, `shot_brief_ep${ep}_${platformName}.zip`)}>
          The clip brief (zip)
        </Button>
        <Button size="sm" variant="ghost" icon={ArrowDownToLine} loading={busy === 'images'}
          onClick={() => save('images', doc.export.image_brief_zip, `image_brief_ep${ep}.zip`)}>
          The image brief (zip)
        </Button>
      </div>
    </details>
  )
}

/**
 * The platform row: a chip per platform the document offers (one that cannot
 * make the episode's frame is disabled, saying so) and the model select.
 */
function PlatformRow({ platformInfo, aspect, saving, onChoose }) {
  const models = platformInfo.models || []
  return (
    <div className="handoff-platform">
      <div className="handoff-platform-chips" role="group" aria-label="Where you make the clips">
        {(platformInfo.choices || []).map((choice) => {
          const active = choice.platform === platformInfo.platform
          return (
            <button key={choice.platform} type="button"
              className={`chip ui-chip ui-chip-button handoff-platform-chip${active ? ' chip-accent' : ''}`}
              aria-pressed={active} disabled={!choice.supported || saving}
              title={choice.supported ? choice.name : `${choice.name} cannot make ${aspect}`}
              onClick={() => { if (!active) onChoose({ platform: choice.platform, model: null }) }}>
              {choice.name}{!choice.supported && ` — cannot make ${aspect}`}
            </button>
          )
        })}
        {saving && <Spinner size={14} label="Saving the platform" />}
      </div>
      {models.length > 1 && (
        <label className="handoff-model">
          <span className="form-hint">Model</span>
          <select className="form-input" value={platformInfo.model || ''} disabled={saving}
            onChange={(event) => onChoose({ platform: platformInfo.platform, model: event.target.value })}>
            {models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
          </select>
        </label>
      )}
      <p className="form-hint handoff-credits">
        {platformInfo.credits}
        {platformInfo.url && <> · <a href={platformInfo.url} target="_blank" rel="noreferrer">Open {platformInfo.name}</a></>}
      </p>
      {(platformInfo.prompt_notes || []).length > 0 && (
        <details className="handoff-notes">
          <summary>How to paste</summary>
          <p className="form-hint">{platformInfo.where_to_paste}</p>
          <ul>{platformInfo.prompt_notes.map((line) => <li key={line}>{line}</li>)}</ul>
        </details>
      )}
    </div>
  )
}

/**
 * The master prompt of a v2 story (`doc.master_prompt`: `{text, words,
 * sections: [{key, label, words}]}`; null on a v1 story, then nothing shows):
 * the block every shot prompt already carries, folded by default, with one
 * "Copy master prompt" and a chip per section.
 */
function MasterPromptCard({ master }) {
  const { copy, fallback } = useCopy()
  if (!master || !master.text) return null
  return (
    <details className="handoff-master">
      <summary className="handoff-master-title">Master prompt · {master.words} words</summary>
      <div className="handoff-master-body">
        {(master.sections || []).length > 0 && (
          <p className="handoff-master-sections">
            {master.sections.map((section) => (
              <span key={section.key} className="chip handoff-master-chip">{section.label} {section.words}</span>
            ))}
          </p>
        )}
        <Button variant="primary" icon={Copy} className="handoff-copy-main" onClick={() => copy(master.text, 'master prompt')}>
          Copy master prompt
        </Button>
        <p className="form-hint">
          Every shot prompt below already carries this block. Paste it alone in a chat that keeps context (Gemini), not on Flow.
        </p>
        {fallback}
      </div>
    </details>
  )
}

export default function HandoffPage() {
  const { storyId, ep } = useParams()
  const toast = useToast()
  const [doc, setDoc] = useState(null)
  const [error, setError] = useState('')
  // The platform and model asked for in the query: null reads what the
  // server remembers; set when the PATCH could not save the choice (a step
  // runs, 409), so the choice still holds on this screen.
  const [choice, setChoice] = useState(null)
  const [saving, setSaving] = useState(false)
  const [openKey, setOpenKey] = useState(null)
  const [opened, setOpened] = useState(false)
  const [missingOnly, setMissingOnly] = useState(false)
  const [scrollTo, setScrollTo] = useState(null)

  const load = useCallback(() => (
    fetchHandoff(storyId, ep, choice || {})
      .then((body) => { setDoc(body); setError('') })
      .catch((err) => setError(err.message))
  ), [storyId, ep, choice])

  useEffect(() => { load() }, [load])

  // The next missing item is open when the page first loads.
  useEffect(() => {
    if (!doc || opened) return
    setOpened(true)
    const key = keyOfMissing(doc.next_missing)
    if (key) {
      setOpenKey(key)
      // On arrival, scroll only when the card is out of sight: the platform row stays in view otherwise.
      setScrollTo({ key, onlyIfHidden: true })
    }
  }, [doc, opened])

  useEffect(() => {
    if (!scrollTo) return undefined
    const frame = window.requestAnimationFrame(() => {
      const card = document.getElementById(domIdOf(scrollTo.key))
      const hidden = card && card.getBoundingClientRect().top > window.innerHeight * 0.6
      if (card && (!scrollTo.onlyIfHidden || hidden)) card.scrollIntoView({ behavior: 'smooth', block: 'start' })
      setScrollTo(null)
    })
    return () => window.cancelAnimationFrame(frame)
  }, [scrollTo])

  const cards = useMemo(() => (doc ? cardsOf(doc) : null), [doc])

  // The order "Next missing" walks: the human's rows first (the document's
  // own order), then anything else still missing on the page.
  const missingKeys = useMemo(() => {
    if (!doc || !cards) return []
    const keys = (doc.missing || []).map(keyOfMissing).filter(Boolean)
    for (const card of [...cards.clips, ...cards.keyframes, ...cards.entities]) {
      if (card.missing && !keys.includes(card.key)) keys.push(card.key)
    }
    return keys
  }, [doc, cards])

  const nextMissing = () => {
    if (missingKeys.length === 0) return
    const at = missingKeys.indexOf(openKey)
    const key = missingKeys[(at + 1) % missingKeys.length]
    setOpenKey(key)
    setScrollTo({ key, onlyIfHidden: false })
  }

  const choosePlatform = async (next) => {
    setSaving(true)
    try {
      await patchHandoff(storyId, ep, next.model ? next : { platform: next.platform })
      if (choice) setChoice(null)
      else await load()
    } catch (err) {
      if (err.status === 409) {
        // Soft: a step runs, so nothing is saved -- the choice holds on this screen.
        toast.info('A step is running: this platform is used on this screen and saved later.')
        setChoice(next.model ? next : { platform: next.platform })
      } else {
        toast.error(err.message)
      }
    } finally {
      setSaving(false)
    }
  }

  const onUploaded = (result) => {
    if (result && result.resumed && result.resumed.job_id) {
      toast.success('Everything is uploaded: the paused run goes on.')
    }
    load()
  }

  const toggle = (key) => setOpenKey((current) => (current === key ? null : key))
  const studio = `/story/${storyId}/episodes/${ep}`

  if (!doc) {
    return (
      <div className="fade-in handoff-page">
        <Link to={studio} className="episode-studio-back">← Episode {ep}</Link>
        {error ? <p className="story-error">{error}</p> : <Spinner label="Loading the handoff" />}
      </div>
    )
  }

  const platformInfo = doc.platform
  const aspect = (doc.shots[0] && doc.shots[0].aspect) || 'this frame'
  const visible = (list) => (missingOnly ? list.filter((card) => card.missing) : list)
  const cardProps = (key) => ({
    domId: domIdOf(key), open: openKey === key, onToggle: () => toggle(key), onUploaded,
  })

  const section = (title, list, render) => {
    const shown = visible(list)
    return (
      <section className="handoff-section" aria-label={title}>
        <h3 className="handoff-section-title">{title}</h3>
        {shown.length === 0
          ? <p className="form-hint">{missingOnly ? 'Nothing missing here.' : 'Nothing here.'}</p>
          : <ol className="handoff-cards">{shown.map(render)}</ol>}
      </section>
    )
  }

  return (
    <div className="fade-in handoff-page">
      <header className="handoff-header">
        <div className="handoff-header-top">
          <Link to={studio} className="episode-studio-back">← Episode {ep}</Link>
          <ExportMenu doc={doc} platformName={platformInfo.platform} />
        </div>
        <h2 className="handoff-title">Episode {doc.ep} — Handoff</h2>
        <p className="handoff-progress">
          {progressChips(doc).map((chip) => (
            <span key={chip.name} className={`chip${chip.complete ? '' : ' chip-warn'}`}>{chip.text}</span>
          ))}
        </p>
        <div className="handoff-header-actions">
          <label className="story-checkbox handoff-filter">
            <input type="checkbox" checked={missingOnly} onChange={(event) => setMissingOnly(event.target.checked)} />
            Missing only
          </label>
          <Button size="sm" variant="primary" icon={FastForward} disabled={missingKeys.length === 0} onClick={nextMissing}>
            {missingKeys.length === 0 ? 'Nothing missing' : 'Next missing'}
          </Button>
        </div>
      </header>
      {error && <p className="story-error">{error}</p>}

      <PlatformRow platformInfo={platformInfo} aspect={aspect} saving={saving} onChoose={choosePlatform} />

      <MasterPromptCard master={doc.master_prompt} />

      {section('Clips', cards.clips, (card) => (
        <ShotHandoffCard key={card.key} storyId={storyId} ep={ep} shot={card.shot} which="clip"
          platformInfo={platformInfo} onChanged={load} {...cardProps(card.key)} />
      ))}
      {section('Keyframes', cards.keyframes, (card) => (
        <ShotHandoffCard key={card.key} storyId={storyId} ep={ep} shot={card.shot} which="image"
          platformInfo={platformInfo} onChanged={load} {...cardProps(card.key)} />
      ))}
      {cards.entities.length > 0 && section('Sheets, plates & props', cards.entities, (card) => (
        <EntityHandoffCard key={card.key} entity={card.entity} {...cardProps(card.key)} />
      ))}
    </div>
  )
}

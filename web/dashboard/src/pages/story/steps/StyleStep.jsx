import { useEffect, useRef, useState } from 'react'
import {
  fetchStyles, runStoryStep, approveStoryDoc, fetchStoryEstimate, fetchStoryFileUrl,
} from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import RouteChip from '../../../components/RouteChip'
import { LiveActivity, useJobFeed } from '../../../components/ActivityFeed'
import { StepError } from '../fields'

const HEX_RE = /^#[0-9a-fA-F]{6}$/
const SUBTITLE_MODES = [
  { value: 'word_pop', label: 'Word pop' },
  { value: 'two_line', label: 'Two line' },
  { value: 'none', label: 'None' },
]

function conceptStyleFit(concept) {
  const fit = concept && concept.style_fit
  return typeof fit === 'string' ? fit : fit && fit.default
}

function sameColour(a, b) {
  return typeof a === 'string' && typeof b === 'string' && a.toLowerCase() === b.toLowerCase()
}

function sameColourList(a, b) {
  const listA = a || []
  const listB = b || []
  return listA.length === listB.length && listA.every((hex, i) => sameColour(hex, listB[i]))
}

/**
 * Which of the six editable style paths differ from *template*'s own values
 * -- only a path that differs belongs in `overrides` (spec 10 finding 5):
 * the lock's `overrides` is meant to say what the user changed, and a path
 * that already equals the template's value was never touched. The two
 * palettes compare element-wise; colours compare case-insensitively
 * (`#FFD400` and `#ffd400` are the same colour). With no template loaded yet
 * every path is sent, since there is nothing to diff against.
 *
 * `GET /stories/styles` (the picker list `template` comes from) trims
 * `typography` down to `font_family`/`font_fallback`/`subtitle_mode`/
 * `highlight_colour` -- it never carries `ai_label`, so `template.typography
 * .ai_label` is always `undefined` here. Every shipped template defaults it
 * to `true` (the same assumption the picker-sync effect above already makes
 * for a freshly-picked template), so that is the fallback used to diff it;
 * without it, ai_label would look "changed" on every single save.
 */
function overridesAgainst(template, current) {
  const overrides = {}
  if (!template || !sameColourList(template.palette.primary, current.primary)) {
    overrides['palette.primary'] = current.primary
  }
  if (!template || !sameColourList(template.palette.accents, current.accents)) {
    overrides['palette.accents'] = current.accents
  }
  if (!template || template.typography.font_family !== current.fontFamily) {
    overrides['typography.font_family'] = current.fontFamily
  }
  if (!template || !sameColour(template.typography.highlight_colour, current.highlightColour)) {
    overrides['typography.highlight_colour'] = current.highlightColour
  }
  if (!template || template.typography.subtitle_mode !== current.subtitleMode) {
    overrides['typography.subtitle_mode'] = current.subtitleMode
  }
  const templateAiLabel = template ? template.typography.ai_label ?? true : undefined
  if (!template || Boolean(templateAiLabel) !== Boolean(current.aiLabel)) {
    overrides['typography.ai_label'] = current.aiLabel
  }
  return overrides
}

function Swatch({ hex }) {
  return <span className="story-swatch" style={{ background: HEX_RE.test(hex) ? hex : '#000' }} title={hex} />
}

function ColorListEditor({ label, colors, onChange, disabled, min = 1, max = 6 }) {
  const list = colors || []
  const setAt = (i, hex) => {
    const next = list.slice()
    next[i] = hex
    onChange(next)
  }
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      <div className="story-swatch-row">
        {list.map((hex, i) => (
          <span key={i} className="story-swatch-edit">
            <input
              type="color"
              aria-label={`${label}, colour ${i + 1}`}
              value={HEX_RE.test(hex) ? hex : '#000000'}
              disabled={disabled}
              onChange={(e) => setAt(i, e.target.value)}
            />
            <span className="story-swatch-hex">{hex}</span>
            {list.length > min && !disabled && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => onChange(list.filter((_, idx) => idx !== i))}
                aria-label={`Remove ${label} color ${hex}`}
                title={`Remove ${label} color ${hex}`}
              >
                ✕
              </button>
            )}
          </span>
        ))}
        {list.length < max && !disabled && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange([...list, '#ffffff'])}>
            + colour
          </button>
        )}
      </div>
    </div>
  )
}

export default function StyleStep({ data, storyId, inFlightJob, onChange, onAdvance }) {
  const { story, style_lock: styleLock, style_preview: stylePreview } = data
  const locked = Boolean(styleLock && styleLock.locked_at)

  const [styles, setStyles] = useState([])
  const [templateId, setTemplateId] = useState(
    (styleLock && styleLock.template_id) || story.style_template_id || conceptStyleFit(story.concept) || '',
  )
  const [primary, setPrimary] = useState((styleLock && styleLock.palette.primary) || [])
  const [accents, setAccents] = useState((styleLock && styleLock.palette.accents) || [])
  const [fontFamily, setFontFamily] = useState((styleLock && styleLock.typography.font_family) || '')
  const [highlightColour, setHighlightColour] = useState((styleLock && styleLock.typography.highlight_colour) || '#ffd400')
  const [subtitleMode, setSubtitleMode] = useState((styleLock && styleLock.typography.subtitle_mode) || 'word_pop')
  const [aiLabel, setAiLabel] = useState(styleLock ? styleLock.typography.ai_label : true)
  const [consistencyMode, setConsistencyMode] = useState(story.generation_profile.consistency_mode)

  const [saveError, setSaveError] = useState('')
  const [saveErrors, setSaveErrors] = useState(null)
  const [saving, setSaving] = useState(false)
  const [approveError, setApproveError] = useState('')
  const [approveErrors, setApproveErrors] = useState(null)
  const [approving, setApproving] = useState(false)
  const [previewEstimate, setPreviewEstimate] = useState(null)
  const [previewError, setPreviewError] = useState('')
  const [previewErrors, setPreviewErrors] = useState(null)

  useEffect(() => {
    fetchStyles().then((d) => setStyles(d.styles || [])).catch(() => {})
  }, [])

  // Re-derive the editable values from the draft lock when it changes on
  // the same template, or from the picked template's own defaults otherwise
  // -- never while the lock is already frozen.
  useEffect(() => {
    if (locked) return
    if (styleLock && styleLock.template_id === templateId) {
      setPrimary(styleLock.palette.primary)
      setAccents(styleLock.palette.accents)
      setFontFamily(styleLock.typography.font_family)
      setHighlightColour(styleLock.typography.highlight_colour)
      setSubtitleMode(styleLock.typography.subtitle_mode)
      setAiLabel(styleLock.typography.ai_label)
      return
    }
    const tmpl = styles.find((s) => s.template_id === templateId)
    if (tmpl) {
      setPrimary(tmpl.palette.primary)
      setAccents(tmpl.palette.accents)
      setFontFamily(tmpl.typography.font_family)
      setHighlightColour(tmpl.typography.highlight_colour)
      setSubtitleMode(tmpl.typography.subtitle_mode)
      setAiLabel(true)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateId, styles.length, styleLock && styleLock.template_id, locked])

  const loadPreviewEstimate = () => {
    fetchStoryEstimate(storyId, 'style_preview').then(setPreviewEstimate).catch(() => setPreviewEstimate(null))
  }
  useEffect(() => { loadPreviewEstimate() }, [storyId, styleLock && styleLock.updated_at]) // eslint-disable-line react-hooks/exhaustive-deps

  const myJob = inFlightJob && inFlightJob.step === 'style_preview' ? inFlightJob : null
  const { job: liveJob, events, streamState } = useJobFeed(myJob ? myJob.id : null, {
    onJob: (job) => {
      if (job && job.status !== 'queued' && job.status !== 'running') {
        onChange()
        loadPreviewEstimate()
      }
    },
  })

  const busy = Boolean(inFlightJob)

  const handleSave = async () => {
    setSaving(true)
    setSaveError('')
    setSaveErrors(null)
    try {
      const template = styles.find((s) => s.template_id === templateId)
      const overrides = overridesAgainst(template, {
        primary, accents, fontFamily, highlightColour, subtitleMode, aiLabel,
      })
      const styleParams = { template_id: templateId, overrides, consistency_mode: consistencyMode }
      await runStoryStep(storyId, 'style', { params: styleParams })
      onChange()
    } catch (err) {
      setSaveError(err.message)
      setSaveErrors(err.errors || null)
    } finally {
      setSaving(false)
    }
  }

  const handlePreview = async () => {
    setPreviewError('')
    setPreviewErrors(null)
    try {
      await runStoryStep(storyId, 'style_preview')
      onChange()
    } catch (err) {
      setPreviewError(err.message)
      setPreviewErrors(err.errors || null)
    }
  }

  const handleApprove = async () => {
    setApproving(true)
    setApproveError('')
    setApproveErrors(null)
    try {
      await approveStoryDoc(storyId, 'style')
      onAdvance()
    } catch (err) {
      setApproveError(err.message)
      setApproveErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  return (
    <div className="story-step-body">
      {locked && (
        <p className="form-hint">
          The style is locked (since {new Date(styleLock.locked_at).toLocaleString()}). It cannot change.
        </p>
      )}

      <div className="form-group">
        <label className="form-label">Style template</label>
        <div className="story-style-grid">
          {styles.map((style) => (
            <button
              key={style.template_id}
              type="button"
              className={`story-style-pick${templateId === style.template_id ? ' active' : ''}`}
              onClick={() => setTemplateId(style.template_id)}
              disabled={locked || busy}
            >
              <span className="story-swatch-row">
                {style.palette.primary.slice(0, 3).map((hex, i) => <Swatch key={i} hex={hex} />)}
              </span>
              <span className="story-style-pick-name">{style.name.en}</span>
            </button>
          ))}
        </div>
      </div>

      <ColorListEditor label="Primary palette" colors={primary} onChange={setPrimary} disabled={locked || busy} />
      <ColorListEditor label="Accent palette" colors={accents} onChange={setAccents} disabled={locked || busy} />

      <div className="form-group">
        <label className="form-label" htmlFor="style-font-family">Font family</label>
        <input
          id="style-font-family"
          className="form-input"
          type="text"
          value={fontFamily}
          disabled={locked || busy}
          onChange={(e) => setFontFamily(e.target.value)}
        />
      </div>

      <div className="form-group">
        <label className="form-label">Highlight colour</label>
        <span className="story-swatch-edit">
          <input
            type="color"
            aria-label="Highlight colour"
            value={HEX_RE.test(highlightColour) ? highlightColour : '#ffd400'}
            disabled={locked || busy}
            onChange={(e) => setHighlightColour(e.target.value)}
          />
          <span className="story-swatch-hex">{highlightColour}</span>
        </span>
      </div>

      <div className="form-group">
        <label className="form-label" htmlFor="style-subtitle-mode">Subtitle mode</label>
        <select
          id="style-subtitle-mode"
          className="form-select"
          value={subtitleMode}
          disabled={locked || busy}
          onChange={(e) => setSubtitleMode(e.target.value)}
        >
          {SUBTITLE_MODES.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
      </div>

      <div className="toggle-row">
        <div>
          <div className="toggle-label">AI label</div>
          <div className="toggle-desc">Marks the episode as AI-generated on screen.</div>
        </div>
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={Boolean(aiLabel)}
            disabled={locked || busy}
            onChange={(e) => setAiLabel(e.target.checked)}
          />
          <span className="toggle-slider"></span>
        </label>
      </div>

      <div className="form-group">
        <label className="form-label">Consistency mode</label>
        <div className="story-segmented">
          {['references', 'prompt_only'].map((mode) => (
            <button
              key={mode}
              type="button"
              className={`btn btn-sm ${consistencyMode === mode ? 'btn-primary' : 'btn-secondary'}`}
              onClick={() => setConsistencyMode(mode)}
              disabled={locked || busy}
            >
              {mode === 'references' ? 'References' : 'Prompt only'}
            </button>
          ))}
        </div>
        {consistencyMode === 'prompt_only' && (
          <p className="form-hint">
            A degraded mode: shots are prompted without reference images, so characters
            and places may drift slightly across shots.
          </p>
        )}
      </div>

      {!locked && (
        <>
          <div className="story-step-actions">
            <button type="button" className="btn btn-secondary" onClick={handleSave} disabled={busy || saving || !templateId}>
              {saving ? 'Saving…' : 'Save draft'}
            </button>
          </div>
          <StepError message={saveError} errors={saveErrors} className="story-step-error" />
        </>
      )}

      <PreviewStrip
        storyId={storyId}
        stylePreview={stylePreview}
        estimate={previewEstimate}
        onGenerate={handlePreview}
        error={previewError}
        errors={previewErrors}
        busy={busy}
        myJob={myJob}
        liveJob={liveJob}
        events={events}
        streamState={streamState}
      />

      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleApprove}
          disabled={busy || approving || locked || !styleLock}
        >
          {approving ? 'Approving…' : locked ? 'Style locked' : 'Approve & lock style'}
        </button>
      </div>
      <StepError message={approveError} errors={approveErrors} className="story-step-error" />
    </div>
  )
}

function PreviewStrip({ storyId, stylePreview, estimate, onGenerate, error, errors, busy, myJob, liveJob, events, streamState }) {
  const [urls, setUrls] = useState({})
  const urlsRef = useRef({})

  useEffect(() => {
    let cancelled = false
    const images = (stylePreview && stylePreview.images) || []

    Object.values(urlsRef.current).forEach((url) => URL.revokeObjectURL(url))
    urlsRef.current = {}
    setUrls({})

    images.forEach((img) => {
      fetchStoryFileUrl(storyId, img.name).then((url) => {
        if (cancelled) { URL.revokeObjectURL(url); return }
        urlsRef.current[img.name] = url
        setUrls((prev) => ({ ...prev, [img.name]: url }))
      }).catch(() => {})
    })

    return () => {
      cancelled = true
      Object.values(urlsRef.current).forEach((url) => URL.revokeObjectURL(url))
      urlsRef.current = {}
    }
  }, [storyId, stylePreview && stylePreview.updated_at])

  const ready = estimate && estimate.ready

  return (
    <div className="story-preview-strip">
      <h4 className="story-section-title">Preview strip</h4>
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-secondary"
          onClick={onGenerate}
          disabled={busy || !ready}
          title={!ready && estimate ? estimate.message : undefined}
        >
          {myJob ? <><span className="spinner"></span> Generating…</> : 'Generate preview'}
        </button>
        <EstimateChip estimate={estimate} />
        {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      </div>
      {!ready && estimate && <p className="form-hint">{estimate.message}</p>}
      <StepError message={error} errors={errors} className="story-step-error" />

      {myJob && liveJob && (
        liveJob.status === 'queued'
          ? <p className="form-hint">queued — waiting for the worker</p>
          : <LiveActivity job={liveJob} events={events} streamState={streamState} />
      )}

      {stylePreview && stylePreview.images.length > 0 && (
        <div className="story-preview-grid">
          {stylePreview.images.map((img) => (
            <div key={img.name} className="story-preview-thumb">
              {urls[img.name]
                ? <img src={urls[img.name]} alt={`Style sample ${img.n}`} />
                : <div className="story-preview-placeholder"><span className="spinner"></span></div>}
              <span className="story-preview-label">
                #{img.n} · {img.paid ? `$${img.est_usd.toFixed(3)}` : 'free'}
              </span>
            </div>
          ))}
        </div>
      )}

      {stylePreview && stylePreview.failed.length > 0 && (
        <ul className="story-field-list">
          {stylePreview.failed.map((f) => (
            <li key={f.n} className="story-error">Sample {f.n} failed: {f.reasons.join(', ')}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

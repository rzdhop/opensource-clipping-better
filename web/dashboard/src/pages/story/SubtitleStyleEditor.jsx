// The story's own subtitle look (plan 23 stage B5): size, position, colours,
// outline and box (and the font, where the page has no font control of its
// own), with an approximate CSS preview of one line. Saved with
// PATCH /stories/{id}/subtitle-style, which is allowed at any time -- the
// style lock's freeze included -- and only changes the next render. Used by
// the style step (beside its font, highlight and mode controls) and by the
// episode page's "Subtitles" panel (episode/PreviewPane.jsx).

import { useEffect, useMemo, useState } from 'react'
import { patchSubtitleStyle } from '../../api'
import { StepError } from './fields'
import {
  SUBTITLE_FONT_FAMILIES, SIZE_PCT, POSITION_PCT, OUTLINE_PX, BOX_OPACITY_PCT, RENDER_DEFAULTS,
  formFromStyle, styleFromForm, previewCss,
} from './subtitleStyle'

const HEX_RE = /^#[0-9a-fA-F]{6}$/

// JSON with sorted keys: the server stores the look in its own key order.
const canon = (value) => JSON.stringify(value, (key, val) => (
  val && typeof val === 'object' && !Array.isArray(val)
    ? Object.keys(val).sort().reduce((out, k) => { out[k] = val[k]; return out }, {})
    : val))

/** A colour that may be "the style's own" ('' ): the picker shows `fallback`
 * until a colour is chosen, and "Default" puts it back. */
function ColourField({ id, label, value, fallback, onChange, disabled, hint }) {
  return (
    <div className="form-group">
      <label className="form-label" htmlFor={id}>{label}</label>
      <span className="story-swatch-edit">
        <input
          id={id}
          type="color"
          value={HEX_RE.test(value) ? value : fallback}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
        <span className="story-swatch-hex">{value || 'style default'}</span>
        {value && !disabled && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange('')}>Default</button>
        )}
      </span>
      {hint && <p className="form-hint">{hint}</p>}
    </div>
  )
}

export default function SubtitleStyleEditor({
  storyId, story, styleLock, busy, onChange, showFont = true, actions = null,
}) {
  const stored = story && story.subtitle_style
  const storedKey = canon(stored || null)
  const [form, setForm] = useState(() => formFromStyle(stored))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  const [saved, setSaved] = useState(false)

  // The stored look changed (a save, a clear, another tab): show it.
  useEffect(() => { setForm(formFromStyle(stored)) }, [storedKey]) // eslint-disable-line react-hooks/exhaustive-deps

  const mode = (styleLock && styleLock.typography && styleLock.typography.subtitle_mode) || 'word_pop'
  const styleFont = styleLock && styleLock.typography ? styleLock.typography.font_family : ''
  const set = (patch) => { setForm((prev) => ({ ...prev, ...patch })); setSaved(false) }
  const payload = useMemo(() => styleFromForm(form, { withFont: showFont }), [form, showFont])
  // A page with no font control of its own (the style step) keeps the stored font as it is.
  const toSend = useMemo(() => {
    if (showFont || !stored || !stored.font_family) return payload
    return { ...(payload || {}), font_family: stored.font_family }
  }, [payload, showFont, stored])
  const dirty = canon(toSend) !== storedKey

  const save = async (body) => {
    setSaving(true)
    setError('')
    setErrors(null)
    try {
      await patchSubtitleStyle(storyId, body)
      setSaved(true)
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setSaving(false)
    }
  }

  const disabled = Boolean(busy) || saving
  const twoLine = mode === 'two_line'
  const preview = previewCss(form, mode)
  const defaults = RENDER_DEFAULTS[twoLine ? 'two_line' : 'word_pop']

  return (
    <div className="story-subtitle-style">
      <div className="story-subtitle-style-grid">
        <div className="story-subtitle-style-controls">
          {showFont && (
            <div className="form-group">
              <label className="form-label" htmlFor="subtitle-font-family">Font</label>
              <select
                id="subtitle-font-family"
                className="form-select"
                value={form.fontFamily}
                disabled={disabled}
                onChange={(e) => set({ fontFamily: e.target.value })}
              >
                <option value="">Style default{styleFont ? ` (${styleFont})` : ''}</option>
                {SUBTITLE_FONT_FAMILIES.map((family) => <option key={family} value={family}>{family}</option>)}
              </select>
            </div>
          )}

          <div className="form-group">
            <label className="form-label" htmlFor="subtitle-size">Size: {form.sizePct} %</label>
            <input
              id="subtitle-size"
              type="range"
              min={SIZE_PCT.min}
              max={SIZE_PCT.max}
              step="5"
              value={form.sizePct}
              disabled={disabled}
              onChange={(e) => set({ sizePct: Number(e.target.value) })}
            />
          </div>

          <div className="form-group">
            <label className="form-label" htmlFor="subtitle-position">Position (% of the frame's height)</label>
            <input
              id="subtitle-position"
              className="form-input"
              type="number"
              min={POSITION_PCT.min}
              max={POSITION_PCT.max}
              step="1"
              placeholder={String(defaults.position)}
              value={form.positionPct}
              disabled={disabled}
              onChange={(e) => set({ positionPct: e.target.value })}
            />
            <p className="form-hint">
              {twoLine ? 'Where the lines end: their bottom edge.' : "Where the word sits: its centre."}
              {' '}From the top; empty keeps {defaults.position} %.
            </p>
          </div>

          <ColourField
            id="subtitle-text-colour"
            label="Text colour"
            value={form.textColour}
            fallback="#ffffff"
            disabled={disabled}
            onChange={(value) => set({ textColour: value })}
            hint="Word pop only: two line keeps each speaker's own colour."
          />
          <ColourField
            id="subtitle-highlight-colour"
            label="Highlight colour"
            value={form.highlightColour}
            fallback={(styleLock && styleLock.typography && styleLock.typography.highlight_colour) || RENDER_DEFAULTS.highlight}
            disabled={disabled}
            onChange={(value) => set({ highlightColour: value })}
            hint="Two line: the word being spoken."
          />

          <div className="form-group">
            <label className="form-label" htmlFor="subtitle-outline-px">Outline width (px, 0–{OUTLINE_PX.max})</label>
            <input
              id="subtitle-outline-px"
              className="form-input"
              type="number"
              min={OUTLINE_PX.min}
              max={OUTLINE_PX.max}
              step="1"
              placeholder={String(defaults.outline)}
              value={form.outlinePx}
              disabled={disabled || form.boxOn}
              onChange={(e) => set({ outlinePx: e.target.value })}
            />
          </div>
          <ColourField
            id="subtitle-outline-colour"
            label="Outline colour"
            value={form.outlineColour}
            fallback="#000000"
            disabled={disabled || form.boxOn}
            onChange={(value) => set({ outlineColour: value })}
          />

          <div className="toggle-row">
            <div>
              <div className="toggle-label">Box behind the text</div>
              <div className="toggle-desc">Replaces the outline: the text sits on a box.</div>
            </div>
            <label className="toggle-switch">
              <input
                type="checkbox"
                aria-label="Box behind the text"
                checked={form.boxOn}
                disabled={disabled}
                onChange={(e) => set({ boxOn: e.target.checked })}
              />
              <span className="toggle-slider"></span>
            </label>
          </div>
          {form.boxOn && (
            <>
              <ColourField
                id="subtitle-box-colour"
                label="Box colour"
                value={form.boxColour}
                fallback="#000000"
                disabled={disabled}
                onChange={(value) => set({ boxColour: value || '#000000' })}
              />
              <div className="form-group">
                <label className="form-label" htmlFor="subtitle-box-opacity">Box opacity: {form.boxOpacity} %</label>
                <input
                  id="subtitle-box-opacity"
                  type="range"
                  min={BOX_OPACITY_PCT.min}
                  max={BOX_OPACITY_PCT.max}
                  step="5"
                  value={form.boxOpacity}
                  disabled={disabled}
                  onChange={(e) => set({ boxOpacity: Number(e.target.value) })}
                />
              </div>
            </>
          )}
        </div>

        <div className="story-subtitle-preview" aria-label="Approximate subtitle preview">
          <div className="story-subtitle-preview-frame">
            <span className="story-subtitle-preview-line" style={preview}>
              {twoLine
                ? <>Who left the <span style={{ color: form.highlightColour || RENDER_DEFAULTS.highlight }}>cake</span> here</>
                : 'CAKE'}
            </span>
          </div>
          <p className="form-hint">Approximate: the render's own font and outline differ a little.</p>
        </div>
      </div>

      <div className="story-step-actions">
        <button type="button" className="btn btn-secondary" onClick={() => save(toSend)} disabled={disabled || !dirty}>
          {saving ? 'Saving…' : 'Save subtitles'}
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => save(null)}
          disabled={disabled || !stored}
        >
          Clear
        </button>
        {saved && !dirty && <span className="chip chip-accent">Saved: the next render uses it</span>}
        {typeof actions === 'function' ? actions({ dirty }) : actions}
      </div>
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

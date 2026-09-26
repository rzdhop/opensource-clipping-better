import { useEffect, useRef, useState } from 'react'

/**
 * One step or card's own error slot: the message plus, when present, the
 * `ApiError.errors` list underneath it. Scrolls itself into view whenever a
 * new error appears -- without this, on a phone-width page, the control that
 * failed could be a full screen away from the only place the error was shown
 * (spec 10 finding 1).
 */
export function StepError({ message, errors, className }) {
  const ref = useRef(null)

  useEffect(() => {
    if (message && ref.current) ref.current.scrollIntoView({ block: 'nearest' })
  }, [message])

  if (!message) return null

  return (
    <div className={`story-error${className ? ` ${className}` : ''}`} ref={ref}>
      {message}
      {errors && errors.length > 0 && (
        <ul className="story-field-list">
          {errors.map((err, i) => <li key={i}>{err}</li>)}
        </ul>
      )}
    </div>
  )
}

/**
 * A read/edit toggle for one plain-text story field. `onSave(value)` is
 * called with the new text; it is expected to call `patchStory` and re-throw
 * on failure so the error can be shown next to the field it belongs to,
 * rather than only at the top of the page.
 */
export function EditableText({ label, value, placeholder, rows = 3, onSave, disabled }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  if (!editing) {
    return (
      <div className="story-field">
        {label && <div className="story-field-label">{label}</div>}
        <p className="story-field-value">{value ? value : <em>Not written yet.</em>}</p>
        {!disabled && (
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => { setDraft(value || ''); setError(''); setErrors(null); setEditing(true) }}
          >
            Edit
          </button>
        )}
      </div>
    )
  }

  const save = async () => {
    setSaving(true)
    setError('')
    setErrors(null)
    try {
      await onSave(draft.trim())
      setEditing(false)
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="story-field">
      {label && <div className="story-field-label">{label}</div>}
      <textarea
        className="form-input"
        rows={rows}
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
      />
      <StepError message={error} errors={errors} />
      <div className="story-field-actions">
        <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(false)} disabled={saving}>
          Cancel
        </button>
      </div>
    </div>
  )
}

/**
 * A read/edit toggle for a list of short strings, one per line. `onSave`
 * receives the trimmed, non-empty lines as an array.
 */
export function EditableList({ label, value, onSave, exactLines, disabled, hint }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState((value || []).join('\n'))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  const lines = value || []

  if (!editing) {
    return (
      <div className="story-field">
        {label && <div className="story-field-label">{label}</div>}
        {lines.length ? (
          <ul className="story-field-list">
            {lines.map((line, i) => <li key={i}>{line}</li>)}
          </ul>
        ) : (
          <p className="story-field-value"><em>Not written yet.</em></p>
        )}
        {!disabled && (
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => { setDraft(lines.join('\n')); setError(''); setErrors(null); setEditing(true) }}
          >
            Edit
          </button>
        )}
      </div>
    )
  }

  const save = async () => {
    const next = draft.split('\n').map((l) => l.trim()).filter(Boolean)
    setSaving(true)
    setError('')
    setErrors(null)
    try {
      await onSave(next)
      setEditing(false)
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="story-field">
      {label && <div className="story-field-label">{label}</div>}
      <textarea
        className="form-input"
        rows={Math.max(3, lines.length + 1)}
        value={draft}
        placeholder="One line each"
        onChange={(e) => setDraft(e.target.value)}
      />
      <p className="form-hint">
        {hint || 'One per line.'}{exactLines ? ` Needs exactly ${exactLines} lines.` : ''}
      </p>
      <StepError message={error} errors={errors} />
      <div className="story-field-actions">
        <button type="button" className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(false)} disabled={saving}>
          Cancel
        </button>
      </div>
    </div>
  )
}

/** A note input plus a "Regenerate" button, with its estimate chip. */
export function RegenerateControl({ onRegenerate, disabled, estimateChip }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const run = async () => {
    setBusy(true)
    setError('')
    setErrors(null)
    try {
      await onRegenerate(note.trim() || null)
      setNote('')
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="story-regenerate">
      <input
        className="form-input story-regenerate-note"
        type="text"
        placeholder="Optional note, e.g. 'make it darker'"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        disabled={disabled || busy}
      />
      <button type="button" className="btn btn-secondary btn-sm" onClick={run} disabled={disabled || busy}>
        {busy ? 'Regenerating…' : '↻ Regenerate'}
      </button>
      {estimateChip}
      <StepError message={error} errors={errors} />
    </div>
  )
}

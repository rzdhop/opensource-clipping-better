import { Children, cloneElement, isValidElement, useId } from 'react'

/**
 * A labelled form field around any input: the label points at the input
 * (`htmlFor`), and the hint and the error are tied to it with
 * `aria-describedby`; an error also sets `aria-invalid`. With no `htmlFor`
 * an id is generated and given to the single child input when it has none.
 */
export function Field({ label, htmlFor, hint, error, required = false, className = '', children }) {
  const generated = useId()
  const only = Children.count(children) === 1 && isValidElement(children) ? children : null
  const inputId = htmlFor || (only && only.props.id) || `field-${generated}`
  const hintId = hint ? `${inputId}-hint` : undefined
  const errorId = error ? `${inputId}-error` : undefined
  const describedBy = [hintId, errorId].filter(Boolean).join(' ') || undefined

  const control = only
    ? cloneElement(only, {
      id: only.props.id || inputId,
      'aria-describedby': [only.props['aria-describedby'], describedBy].filter(Boolean).join(' ') || undefined,
      'aria-invalid': error ? true : only.props['aria-invalid'],
      required: only.props.required ?? (required || undefined),
    })
    : children

  return (
    <div className={`ui-field${error ? ' ui-field-invalid' : ''} ${className}`.trim()}>
      {label && (
        <label className="ui-field-label" htmlFor={inputId}>
          {label}
          {required && <span className="ui-field-required" aria-hidden="true"> *</span>}
        </label>
      )}
      {control}
      {hint && <p className="ui-field-hint" id={hintId}>{hint}</p>}
      {error && <p className="ui-field-error" id={errorId} role="alert">{error}</p>}
    </div>
  )
}

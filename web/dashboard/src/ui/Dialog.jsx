import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Button } from './Button'
import { AlertTriangle, X } from './icons'

const FOCUSABLE = [
  'a[href]', 'area[href]', 'button:not([disabled])', 'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])', 'textarea:not([disabled])', '[tabindex]:not([tabindex="-1"])',
].join(', ')

function focusableIn(node) {
  return Array.from(node.querySelectorAll(FOCUSABLE))
    .filter((el) => el.getClientRects().length > 0 || el === document.activeElement)
}

/**
 * An accessible modal dialog: `role="dialog"`, `aria-modal`, labelled by its
 * title (and described by `description` when given). While open, focus is
 * trapped inside and returned to the element that had it on close; Escape
 * closes; a click on the backdrop closes unless the dialog is a `danger` one
 * (a destructive question must be answered with a button) or
 * `dismissible={false}`. Rendered into `document.body`.
 */
export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  tone = 'default',
  dismissible,
  initialFocusRef,
  hideClose = false,
  className = '',
}) {
  const titleId = useId()
  const descriptionId = useId()
  const panelRef = useRef(null)
  const onCloseRef = useRef(onClose)
  useEffect(() => { onCloseRef.current = onClose }, [onClose])
  const backdropCloses = dismissible ?? tone !== 'danger'

  useEffect(() => {
    if (!open) return undefined
    const panel = panelRef.current
    if (!panel) return undefined
    const previous = document.activeElement
    const target = (initialFocusRef && initialFocusRef.current)
      || panel.querySelector('[data-autofocus]')
      || focusableIn(panel)[0]
      || panel
    target.focus()

    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        if (onCloseRef.current) onCloseRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const items = focusableIn(panel)
      if (items.length === 0) {
        event.preventDefault()
        panel.focus()
        return
      }
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || active === panel || !panel.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (active === last || !panel.contains(active))) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown, true)
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown, true)
      document.body.style.overflow = overflow
      if (previous && typeof previous.focus === 'function' && document.contains(previous)) previous.focus()
    }
  }, [open, initialFocusRef])

  if (!open || typeof document === 'undefined') return null

  const onBackdropMouseDown = (event) => {
    // Only a press that starts on the backdrop itself: a text selection
    // dragged out of the panel must not close it. Focus stays in the panel.
    if (event.target !== event.currentTarget) return
    event.preventDefault()
    if (backdropCloses && onCloseRef.current) onCloseRef.current()
  }

  return createPortal(
    <div className="ui-dialog-backdrop" onMouseDown={onBackdropMouseDown}>
      <div
        ref={panelRef}
        className={`ui-dialog ui-dialog-${tone === 'danger' ? 'danger' : 'default'} ${className}`.trim()}
        role={tone === 'danger' ? 'alertdialog' : 'dialog'}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
      >
        <div className="ui-dialog-header">
          {tone === 'danger' && (
            <span className="ui-dialog-icon" aria-hidden="true"><AlertTriangle size={18} /></span>
          )}
          <h2 className="ui-dialog-title" id={titleId}>{title}</h2>
          {!hideClose && (
            <button type="button" className="ui-icon-btn ui-icon-btn-ghost ui-icon-btn-sm ui-dialog-close" aria-label="Close" onClick={() => onCloseRef.current && onCloseRef.current()}>
              <X size={14} aria-hidden="true" />
            </button>
          )}
        </div>
        {description && <p className="ui-dialog-description" id={descriptionId}>{description}</p>}
        {children && <div className="ui-dialog-body">{children}</div>}
        {footer && <div className="ui-dialog-footer">{footer}</div>}
      </div>
    </div>,
    document.body,
  )
}

// ------------------------------------------------------------------ confirm

function normalize(options) {
  const opts = typeof options === 'string' ? { message: options } : { ...(options || {}) }
  return {
    title: opts.title || 'Are you sure?',
    message: opts.message || '',
    confirmLabel: opts.confirmLabel || 'Confirm',
    cancelLabel: opts.cancelLabel || 'Cancel',
    tone: opts.tone === 'danger' ? 'danger' : 'default',
  }
}

/** The browser's own confirm, as the fallback when no DialogProvider is mounted. */
function windowConfirm(options) {
  const { title, message } = normalize(options)
  const text = [title, message].filter(Boolean).join('\n\n')
  return Promise.resolve(typeof window !== 'undefined' ? window.confirm(text) : false)
}

const ConfirmContext = createContext(null)

// The mounted provider's confirm, for call sites outside React components.
let activeConfirm = null
let nextRequestId = 0

function ConfirmDialog({ request, onSettle }) {
  const { title, message, confirmLabel, cancelLabel, tone } = request.options
  const cancelRef = useRef(null)
  const confirmRef = useRef(null)
  return (
    <Dialog
      open
      onClose={() => onSettle(false)}
      title={title}
      description={message}
      tone={tone}
      hideClose
      // A destructive question starts on Cancel; any other on its confirm button.
      initialFocusRef={tone === 'danger' ? cancelRef : confirmRef}
      footer={(
        <>
          <Button ref={cancelRef} variant="secondary" onClick={() => onSettle(false)}>{cancelLabel}</Button>
          <Button ref={confirmRef} variant={tone === 'danger' ? 'danger' : 'primary'} onClick={() => onSettle(true)}>
            {confirmLabel}
          </Button>
        </>
      )}
    />
  )
}

/**
 * Mount once near the root. It renders one confirm dialog at a time (later
 * requests wait their turn) and serves both `useConfirm()` and the
 * module-level `confirmDialog()`.
 */
export function DialogProvider({ children }) {
  const [queue, setQueue] = useState([])
  const queueRef = useRef(queue)
  useEffect(() => { queueRef.current = queue }, [queue])

  const confirm = useCallback((options) => new Promise((resolve) => {
    nextRequestId += 1
    const request = { id: nextRequestId, options: normalize(options), resolve, settled: false }
    setQueue((current) => [...current, request])
  }), [])

  useEffect(() => {
    activeConfirm = confirm
    return () => {
      if (activeConfirm === confirm) activeConfirm = null
    }
  }, [confirm])

  // A provider that goes away answers what it still holds with "no", so no
  // caller waits forever.
  useEffect(() => () => {
    queueRef.current.forEach((request) => {
      if (!request.settled) {
        request.settled = true
        request.resolve(false)
      }
    })
  }, [])

  const current = queue[0]
  const settle = (value) => {
    if (!current || current.settled) return
    current.settled = true
    current.resolve(value)
    setQueue((pending) => pending.filter((request) => request !== current))
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {current && <ConfirmDialog key={current.id} request={current} onSettle={settle} />}
    </ConfirmContext.Provider>
  )
}

/**
 * `const confirm = useConfirm()` then
 * `if (!(await confirm({ title, message, confirmLabel, cancelLabel, tone: 'danger' }))) return`.
 * Resolves true only on the confirm button. Outside a DialogProvider it falls
 * back to `window.confirm`, so a confirmation is never silently dropped.
 */
export function useConfirm() {
  return useContext(ConfirmContext) || windowConfirm
}

/** The same confirm for code that is not a component (or runs outside the provider). */
export function confirmDialog(options) {
  return activeConfirm ? activeConfirm(options) : windowConfirm(options)
}

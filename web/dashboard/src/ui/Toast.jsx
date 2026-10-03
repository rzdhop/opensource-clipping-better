import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, CircleCheck, Info, X } from './icons'

const ICONS = { success: CircleCheck, error: AlertTriangle, info: Info }
const MAX_VISIBLE = 4

const ToastContext = createContext(null)
let nextToastId = 0

// Without a provider a toast still reaches the console instead of vanishing.
const FALLBACK = {
  show: (tone, message) => { (tone === 'error' ? console.error : console.info)(message) },
  success: (message) => console.info(message),
  error: (message) => console.error(message),
  info: (message) => console.info(message),
  dismiss: () => {},
}

/**
 * Mount once near the root. Toasts stack bottom-right (full width on a
 * phone) in one `aria-live="polite"` region, so a screen reader announces
 * each without interrupting; they dismiss themselves (errors stay longer)
 * or on their close button.
 */
export function ToastProvider({ children, duration = 4500 }) {
  const [toasts, setToasts] = useState([])
  const timers = useRef(new Map())

  const dismiss = useCallback((id) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
    clearTimeout(timers.current.get(id))
    timers.current.delete(id)
  }, [])

  const show = useCallback((tone, message, options = {}) => {
    nextToastId += 1
    const id = nextToastId
    const kind = ICONS[tone] ? tone : 'info'
    setToasts((current) => [...current.slice(-(MAX_VISIBLE - 1)), { id, tone: kind, message, title: options.title }])
    const ms = options.duration ?? (kind === 'error' ? duration * 2 : duration)
    if (ms > 0) timers.current.set(id, setTimeout(() => dismiss(id), ms))
    return id
  }, [dismiss, duration])

  useEffect(() => {
    const pending = timers.current
    return () => {
      pending.forEach((timer) => clearTimeout(timer))
      pending.clear()
    }
  }, [])

  const api = useMemo(() => ({
    show,
    dismiss,
    success: (message, options) => show('success', message, options),
    error: (message, options) => show('error', message, options),
    info: (message, options) => show('info', message, options),
  }), [show, dismiss])

  const region = (
    <div className="ui-toast-region" role="region" aria-label="Notifications" aria-live="polite">
      {toasts.map((toast) => {
        const Icon = ICONS[toast.tone]
        return (
          <div key={toast.id} className={`ui-toast ui-toast-${toast.tone}`}>
            <span className="ui-toast-icon" aria-hidden="true"><Icon size={16} /></span>
            <div className="ui-toast-text">
              {toast.title && <strong className="ui-toast-title">{toast.title}</strong>}
              <span>{toast.message}</span>
            </div>
            <button
              type="button"
              className="ui-icon-btn ui-icon-btn-ghost ui-icon-btn-sm"
              aria-label="Dismiss notification"
              onClick={() => dismiss(toast.id)}
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        )
      })}
    </div>
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      {typeof document !== 'undefined' && createPortal(region, document.body)}
    </ToastContext.Provider>
  )
}

/** `const toast = useToast()` then `toast.success('Saved')`, `toast.error(err.message)`, `toast.info(...)`. */
export function useToast() {
  return useContext(ToastContext) || FALLBACK
}

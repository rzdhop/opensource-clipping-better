// One copy helper for every Copy button of the dashboard.
//
// `navigator.clipboard` only exists in a secure context (https or localhost).
// The app is also reached over plain http on a tailnet, mostly from a phone,
// where it is undefined. There a user-gesture `document.execCommand('copy')` on
// a temporary textarea still works, and when even that is refused the caller
// shows a visible, pre-selected textarea (`revealForManualCopy`) for a
// long-press copy.

/** The async Clipboard API; false when it is missing or refuses. Never throws. */
async function viaClipboardApi(text) {
  if (typeof navigator === 'undefined' || !navigator.clipboard || !window.isSecureContext) return false
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

/** Select `text` in an off-screen textarea and run `execCommand('copy')`. Never throws. */
function viaExecCommand(text) {
  if (typeof document === 'undefined' || !document.body) return false
  const previous = document.activeElement
  const area = document.createElement('textarea')
  try {
    area.value = text
    area.setAttribute('aria-hidden', 'true')
    area.setAttribute('tabindex', '-1')
    // Off-screen but laid out (select() needs layout); 16px stops the iOS zoom-on-focus.
    area.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;'
      + 'opacity:0;font-size:16px;pointer-events:none;'
    // read-only first: no on-screen keyboard on a phone; iOS sometimes needs it editable.
    area.readOnly = true
    document.body.appendChild(area)
    for (const readOnly of [true, false]) {
      area.readOnly = readOnly
      area.focus()
      area.select()
      area.setSelectionRange(0, text.length)
      if (document.execCommand('copy')) return true
    }
    return false
  } catch {
    return false
  } finally {
    if (area.parentNode) area.parentNode.removeChild(area)
    try {
      if (previous && typeof previous.focus === 'function') previous.focus()
    } catch {
      // The previous element is gone; nothing to restore.
    }
  }
}

/**
 * Copy `text` to the clipboard. Resolves true when it is copied, false when
 * every automatic route failed (then show the text for a manual copy).
 */
export async function copyText(text) {
  if (!text) return false
  if (await viaClipboardApi(text)) return true
  return viaExecCommand(text)
}

/**
 * The last resort: show `area` (a textarea) holding `text`, selected and
 * scrolled into view, so the user can long-press / Ctrl+C it.
 */
export function revealForManualCopy(area, text) {
  if (!area) return
  area.value = text
  area.hidden = false
  try {
    area.focus({ preventScroll: true })
    area.select()
    area.setSelectionRange(0, text.length)
    area.scrollIntoView({ block: 'center' })
  } catch {
    // Selection is a convenience; the text is visible either way.
  }
}

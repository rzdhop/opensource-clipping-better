// Shared display formatters for the dashboard.

/**
 * A dollar amount without its "$": "0.00" for nothing, otherwise three
 * decimals so a sub-cent price (an image, a TTS line) is not rounded away.
 * A missing or non-numeric value reads as nothing. The one copy of the
 * formatter that seven story files each carried a private, identical copy
 * of (DEC-253); its output is byte-for-byte what they printed.
 */
export function formatUsd(value) {
  const amount = Number(value) || 0
  return amount === 0 ? '0.00' : amount.toFixed(3)
}

/**
 * A dollar amount to the cent, with its "$": "$8.38". The daily cap is read
 * in whole cents (the budget rounds a needed extra up to the cent), so the
 * refusal panel, the chip and the Settings card all print this, not the
 * three-decimal estimate form of `formatUsd`.
 */
export function formatCents(value) {
  return `$${(Number(value) || 0).toFixed(2)}`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/**
 * The budget day a `YYYY-MM-DD` key names, as "4 Oct" (read off the key
 * itself, not through a Date, so no timezone can move it). Anything that is
 * not a day key comes back as it was.
 */
export function formatBudgetDay(day) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(day || ''))
  if (!match) return String(day || '')
  const month = MONTHS[Number(match[2]) - 1]
  return month ? `${Number(match[3])} ${month}` : String(day)
}

const MINUTE = 60 * 1000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/**
 * How long ago an ISO timestamp was, in the short form a card shows: "just
 * now", "5 min ago", "2 h ago", "3 d ago"; past four weeks (or more than a
 * minute in the future: clock skew), the date itself ("12 Sep 2026"). A
 * missing or unparsable value reads as "—". `now` is a parameter so a
 * caller can pin the clock.
 */
export function formatRelativeTime(value, now = Date.now()) {
  if (!value) return '—'
  const time = new Date(value).getTime()
  if (Number.isNaN(time)) return '—'
  const elapsed = now - time
  if (elapsed < -MINUTE || elapsed >= 28 * DAY) {
    return new Date(time).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
  }
  if (elapsed < MINUTE) return 'just now'
  if (elapsed < HOUR) return `${Math.floor(elapsed / MINUTE)} min ago`
  if (elapsed < DAY) return `${Math.floor(elapsed / HOUR)} h ago`
  return `${Math.floor(elapsed / DAY)} d ago`
}

/** The full local date and time of an ISO timestamp (a tooltip), or "" for none. */
export function formatDateTime(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

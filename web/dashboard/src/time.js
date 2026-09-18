import { useState, useEffect } from 'react'

/**
 * Parse a timestamp from the API as UTC.
 *
 * The backend is mostly timezone-aware, but `JobProgressEvent.timestamp`
 * defaults to `datetime.utcnow()`, which serializes without a zone. JS reads a
 * zoneless string as LOCAL time, which would report hours of "elapsed" on a
 * job that started seconds ago.
 */
export function parseTime(value) {
  if (!value) return null
  const zoned = /([Zz]|[+-]\d{2}:?\d{2})$/.test(value)
  const parsed = new Date(zoned ? value : `${value}Z`)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

/** "45s", "3m 12s", "1h 04m". Null for anything that is not a duration. */
export function formatDuration(ms) {
  if (ms == null || ms < 0) return null
  const total = Math.floor(ms / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h) return `${h}h ${String(m).padStart(2, '0')}m`
  if (m) return `${m}m ${String(s).padStart(2, '0')}s`
  return `${s}s`
}

/** Wall-clock time of day, for lining up the feed with the container logs. */
export function formatClock(value) {
  const parsed = parseTime(value)
  return parsed ? parsed.toLocaleTimeString([], { hour12: false }) : '--:--:--'
}

/** How long ago `value` was, as a duration. */
export function elapsedSince(value) {
  const parsed = parseTime(value)
  return parsed ? Date.now() - parsed.getTime() : null
}

/**
 * Re-render once a second so elapsed clocks actually tick.
 *
 * Without it they would only advance when new data arrives -- which, during
 * exactly the long silent step the clock exists to measure, is never.
 */
export function useSecondsTicker(active) {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [active])
}

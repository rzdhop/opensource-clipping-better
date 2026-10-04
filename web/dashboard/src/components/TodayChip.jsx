import { useEffect, useState } from 'react'
import { fetchBudgetToday } from '../api'
import { formatBudgetDay, formatCents } from '../lib/format'

// Today's paid spending against the daily cap (plan 23, Track A):
// "today $8.38 / $4.00", or with an extra allowed for today only
// "today $8.38 / $4.00 + $4.98". Warn styled once the spending is over what
// is allowed (the saved cap plus the extra), the state in which every paid
// call is refused.
//
// It is fed by the `today` block every `GET .../estimate/{step}` carries
// (`routes/budget.today_block`), so the chip beside an EstimateChip costs no
// request of its own; only a chip given no block asks `GET /api/budget/today`
// once, when it mounts.

/** The chip's text for a `today` block. */
export function todayLabel(today) {
  const extra = Number(today.extra_usd) || 0
  const label = `today ${formatCents(today.spent_usd)} / ${formatCents(today.daily_cap_usd)}`
  return extra > 0 ? `${label} + ${formatCents(extra)}` : label
}

/** True when today's paid spending is over the saved cap plus today's extra. */
export function isOverToday(today) {
  const effective = today.effective_cap_usd != null
    ? Number(today.effective_cap_usd)
    : (Number(today.daily_cap_usd) || 0) + (Number(today.extra_usd) || 0)
  return (Number(today.spent_usd) || 0) > effective
}

export default function TodayChip({ today }) {
  const [fetched, setFetched] = useState(null)

  useEffect(() => {
    if (today) return undefined
    let cancelled = false
    fetchBudgetToday().then((data) => { if (!cancelled) setFetched(data) }).catch(() => {})
    return () => { cancelled = true }
  }, [Boolean(today)]) // eslint-disable-line react-hooks/exhaustive-deps

  const block = today || fetched
  if (!block) return null
  const over = isOverToday(block)
  const zone = block.zone || 'UTC'
  const extra = Number(block.extra_usd) || 0
  const title = [
    `Paid spending today (day of ${formatBudgetDay(block.day)}, ${zone}) against the saved daily cap`,
    extra > 0 ? ` plus ${formatCents(extra)} allowed for today only` : '',
    over ? '. Over it: paid calls are refused until the day resets.' : '.',
  ].join('')
  return (
    <span className={`chip today-chip${over ? ' chip-warn' : ''}`} title={title}>
      {todayLabel(block)}
    </span>
  )
}

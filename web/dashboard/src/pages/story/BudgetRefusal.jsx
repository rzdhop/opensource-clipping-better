import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { allowTodayExtra } from '../../api'
import { Button, Dialog, Field, useToast } from '../../ui'
import { AlertTriangle } from '../../ui/icons'
import { formatBudgetDay, formatCents } from '../../lib/format'

/** The `code` of the API's 409 for a job the daily cap alone refuses (`_daily_cap_detail`). */
export const BUDGET_DAILY_CAP = 'budget_daily_cap'

// The most one day's extra can reach (budget.DAY_EXTRA_MAX_USD); the API
// answers 400 beyond it, this only keeps the dialog's field honest.
const EXTRA_MIN_USD = 0.01
const EXTRA_MAX_USD = 25

/** True when *detail* has the whole shape the panel reads (an older API's plain 409 does not). */
export function isBudgetRefusal(code, detail) {
  return code === BUDGET_DAILY_CAP
    && Boolean(detail && detail.today && detail.estimate && detail.cap)
}

/** What the refusal calls the job ("this cast"), from the sentence's "...: <noun> (est $0.60: ...)". */
export function jobNoun(detail, noun) {
  if (noun) return noun
  const match = /: (.+?) \(est \$/.exec((detail && detail.message) || '')
  return match ? match[1] : 'this step'
}

function upperFirst(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text
}

/** "Spent today: $8.38 · 2 stories (A $5.03, B $3.36) · day of 4 Oct (UTC)". */
export function spentLine(today) {
  const stories = [...(today.stories || [])].sort((a, b) => b.usd - a.usd).slice(0, 2)
  const count = Number(today.story_count ?? stories.length) || 0
  const out = [`Spent today: ${formatCents(today.spent_usd)}`]
  if (count > 0 && stories.length > 0) {
    let list = stories.map((story) => `${story.title || 'Untitled'} ${formatCents(story.usd)}`).join(', ')
    if (count > stories.length) list += `, +${count - stories.length} more`
    out.push(`${count} ${count === 1 ? 'story' : 'stories'} (${list})`)
  }
  out.push(`day of ${formatBudgetDay(today.day)} (${today.zone || 'UTC'})`)
  return out.join(' · ')
}

/** "This cast: est $0.60 · 5 portraits $0.20 + 10 sheet edits $0.40". */
export function estimateLine(estimate, noun) {
  const parts = (estimate.parts || []).map((part) => `${part.qty} ${part.what} ${formatCents(part.usd)}`)
  if (Number(estimate.llm_worst_usd) > 0) parts.push(`LLM up to ${formatCents(estimate.llm_worst_usd)}`)
  const head = `${upperFirst(noun)}: est ${formatCents(estimate.usd)}`
  return parts.length ? `${head} · ${parts.join(' + ')}` : head
}

/** "Daily cap: $4.00 (saved in Settings)", plus " + $4.98 allowed today" once an extra is granted. */
export function capLine(cap, today) {
  const extra = Number(today.extra_usd) || 0
  return `Daily cap: ${formatCents(cap.daily_usd)} (saved in Settings)`
    + (extra > 0 ? ` + ${formatCents(extra)} allowed today` : '')
}

/**
 * The panel a daily-cap refusal (409, `code: "budget_daily_cap"`) renders in
 * place of a bare sentence (plan 23, Track A): what was spent today, what this
 * job would cost, the cap, and what is needed -- with the one-click way out.
 *
 * "Allow $X more today" only GRANTS (`POST /api/budget/today/extra`, the
 * amount ending with the day); it never runs the step again. A person still
 * says go: the toast names the step's own button. "Other amount…" asks for
 * another figure in an in-app dialog, "Budget settings" opens the Settings
 * budget tab. When the job would still meet another cap (the episode's or the
 * story's), an extra for today would not lift it, so Allow is disabled and
 * that refusal is shown as a second line.
 */
export default function BudgetRefusal({ detail, storyId, noun, retryLabel, className, panelRef }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [granted, setGranted] = useState(null)
  const [grantError, setGrantError] = useState('')
  const [asking, setAsking] = useState(false)
  const [amount, setAmount] = useState('')
  const [amountError, setAmountError] = useState('')
  const amountRef = useRef(null)

  const { today, estimate, cap } = detail
  const job = jobNoun(detail, noun)
  const needed = Number(detail.needed_usd) || 0
  const zone = today.zone || 'UTC'
  const blocked = Boolean(detail.other_cap_refusal)
  const step = retryLabel ? `\u201c${retryLabel}\u201d` : 'the button'

  const grant = async (usd) => {
    setBusy(true)
    setGrantError('')
    try {
      await allowTodayExtra({
        usd,
        story_id: storyId,
        note: `${job} est ${formatCents(estimate.usd)}`,
        estimate_usd: estimate.usd,
      })
      setGranted(usd)
      toast.success(`Allowed ${formatCents(usd)} more for today. Press ${step} again.`)
      return true
    } catch (err) {
      setGrantError(err.message)
      return false
    } finally {
      setBusy(false)
    }
  }

  const openOther = () => {
    setAmount(needed > 0 ? Math.min(Math.max(needed, EXTRA_MIN_USD), EXTRA_MAX_USD).toFixed(2) : '')
    setAmountError('')
    setAsking(true)
  }

  const submitOther = async () => {
    const usd = Number(amount)
    if (!Number.isFinite(usd) || usd < EXTRA_MIN_USD || usd > EXTRA_MAX_USD) {
      setAmountError(`Enter an amount from ${formatCents(EXTRA_MIN_USD)} to ${formatCents(EXTRA_MAX_USD)}.`)
      return
    }
    if (await grant(Math.round(usd * 100) / 100)) setAsking(false)
  }

  return (
    <>
      <div className={`story-error story-alert budget-refusal${className ? ` ${className}` : ''}`} ref={panelRef} role="alert">
        <AlertTriangle size={15} aria-hidden="true" className="story-alert-icon" />
        <div className="story-alert-body">
          <strong className="budget-refusal-title">Over today's spending limit</strong>
          <ul className="budget-refusal-rows">
            <li>{spentLine(today)}</li>
            <li>{estimateLine(estimate, job)}</li>
            <li>{capLine(cap, today)}</li>
          </ul>
          {needed > 0 && <p className="budget-refusal-needs">Needs {formatCents(needed)} more today.</p>}
          {blocked && (
            <p className="budget-refusal-other">
              {detail.other_cap_refusal}
              {' '}An extra for today would not lift this limit.
            </p>
          )}
          {granted != null ? (
            <p className="budget-refusal-granted" role="status">
              Allowed {formatCents(granted)} more for today. Press {step} again.
            </p>
          ) : (
            <div className="budget-refusal-actions">
              <Button
                variant="primary"
                size="sm"
                loading={busy}
                disabled={blocked || !(needed > 0)}
                onClick={() => grant(needed)}
              >
                Allow {formatCents(needed)} more today
              </Button>
              <Button size="sm" disabled={busy} onClick={openOther}>Other amount…</Button>
              <Button as={Link} to="/settings#budget" variant="ghost" size="sm">Budget settings</Button>
            </div>
          )}
          {grantError && <p className="budget-refusal-error">{grantError}</p>}
          <p className="form-hint budget-refusal-hint">
            Only for today: the saved cap stays {formatCents(cap.daily_usd)} and this extra ends at 00:00 {zone}.
          </p>
        </div>
      </div>
      <Dialog
        open={asking}
        onClose={() => setAsking(false)}
        title="Allow another amount for today"
        description={`Only for today: the saved cap stays ${formatCents(cap.daily_usd)} and this extra ends at 00:00 ${zone}.`}
        initialFocusRef={amountRef}
        footer={(
          <>
            <Button variant="secondary" onClick={() => setAsking(false)}>Cancel</Button>
            <Button variant="primary" loading={busy} onClick={submitOther}>Allow for today</Button>
          </>
        )}
      >
        <Field label="Extra for today (USD)" error={amountError}>
          <input
            ref={amountRef}
            className="form-input"
            type="number"
            min={EXTRA_MIN_USD}
            max={EXTRA_MAX_USD}
            step="0.01"
            inputMode="decimal"
            value={amount}
            onChange={(e) => { setAmount(e.target.value); setAmountError('') }}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submitOther() } }}
          />
        </Field>
      </Dialog>
    </>
  )
}

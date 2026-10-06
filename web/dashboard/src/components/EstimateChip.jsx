import { formatUsd } from '../lib/format'
import TodayChip from './TodayChip'

// What a story step would cost, from GET /api/stories/{id}/estimate/{step}.
// An LLM step on a free first link answers est_usd 0.0, which reads as
// "$0.00" (the message says "up to $Z if the free links fail" when a billed
// link can be reached after it); a billed first link is priced at its worst
// call from the server's LLM price table (pricing.LLM_PRICES). A paid image
// step needs the extra digit, so a non-zero amount keeps three decimals
// instead of rounding a sub-cent price away (formatUsd, lib/format.js).

function plural(n, word) {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

/**
 * The phase-2 generation estimates (cast, places, an image or a voice
 * regenerate) count several units at once — `{llm_calls, images,
 * edit_images, tts_chars}` — and every non-zero one is shown:
 * "5 writing · 5 images · 10 edits · voice ~180 chars". Phase 6 stage 12: a
 * clip regenerate's estimate (`{clips, seconds}`,
 * `workflow.regenerate_clip_estimate`) joins the same units object, so its
 * two fields are shown here too: "1 clip · 4s video".
 */
function generationUnitsLabel(units) {
  const parts = []
  if (units.llm_calls) parts.push(`${units.llm_calls} writing`)
  if (units.images) parts.push(plural(units.images, 'image'))
  if (units.edit_images) parts.push(plural(units.edit_images, 'edit'))
  if (units.tts_chars) parts.push(`voice ~${plural(units.tts_chars, 'char')}`)
  if (units.clips) parts.push(plural(units.clips, 'clip'))
  if (units.seconds) parts.push(`${units.seconds}s video`)
  return parts.join(' · ') || 'nothing to make'
}

/**
 * "10 writing steps" / "1 writing step" / "3 images" / "1 image", from the estimate's
 * `units`; a generation estimate (more than one unit) lists each non-zero one.
 */
export function unitsLabel(units) {
  if (!units) return ''
  if (Object.keys(units).length > 1) return generationUnitsLabel(units)
  if (units.llm_calls != null) {
    const n = units.llm_calls
    return `${n} writing step${n === 1 ? '' : 's'}`
  }
  if (units.images != null) {
    const n = units.images
    return `${n} image${n === 1 ? '' : 's'}`
  }
  const [key, value] = Object.entries(units)[0] || []
  return key ? `${value} ${key}` : ''
}

/**
 * One compact chip: "est. $0.00 · 10 writing steps", or for a cast or places
 * step "est. $0.00 · 5 writing · 5 images · 10 edits · voice ~180 chars". Warn
 * styled when the step is not ready to run (the key gate refuses it,
 * `estimate.ready === false`);
 * `estimate.message` explains why, as the chip's title. Beside it, the
 * "today $8.38 / $4.00" chip (TodayChip) when the estimate carries its
 * `today` block. A neutral
 * "estimating…" placeholder covers the gap before the estimate arrives.
 */
export default function EstimateChip({ estimate, loading }) {
  if (loading || !estimate) {
    return <span className="chip" title="Fetching the estimate">estimating…</span>
  }
  const label = `est. $${formatUsd(estimate.est_usd)} · ${unitsLabel(estimate.units)}`
  // The day's spending against the daily cap sits beside the estimate (plan
  // 23, Track A): the estimate carries the block, so it costs no request.
  return (
    <>
      <span
        className={`chip${estimate.ready ? '' : ' chip-warn'}`}
        title={estimate.message || ''}
      >
        {label}
      </span>
      {estimate.today && <TodayChip today={estimate.today} />}
    </>
  )
}

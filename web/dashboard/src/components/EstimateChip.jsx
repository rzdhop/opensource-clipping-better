// What a story step would cost, from GET /api/stories/{id}/estimate/{step}.
// LLM steps always answer est_usd 0.0 (there is no LLM price table), which
// reads as "$0.00"; a paid image step needs the extra digit, so a non-zero
// amount keeps three decimals instead of rounding a sub-cent price away.
function formatUsd(value) {
  const amount = Number(value) || 0
  return amount === 0 ? '0.00' : amount.toFixed(3)
}

/** "5 LLM calls" / "1 LLM call" / "3 images" / "1 image", from the estimate's `units`. */
export function unitsLabel(units) {
  if (!units) return ''
  if (units.llm_calls != null) {
    const n = units.llm_calls
    return `${n} LLM call${n === 1 ? '' : 's'}`
  }
  if (units.images != null) {
    const n = units.images
    return `${n} image${n === 1 ? '' : 's'}`
  }
  const [key, value] = Object.entries(units)[0] || []
  return key ? `${value} ${key}` : ''
}

/**
 * One compact chip: "est. $0.00 · 5 LLM calls". Warn styled when the step is
 * not ready to run (the key gate refuses it, `estimate.ready === false`);
 * `estimate.message` explains why, as the chip's title. A neutral
 * "estimating…" placeholder covers the gap before the estimate arrives.
 */
export default function EstimateChip({ estimate, loading }) {
  if (loading || !estimate) {
    return <span className="chip" title="Fetching the estimate">estimating…</span>
  }
  const label = `est. $${formatUsd(estimate.est_usd)} · ${unitsLabel(estimate.units)}`
  return (
    <span
      className={`chip${estimate.ready ? '' : ' chip-warn'}`}
      title={estimate.message || ''}
    >
      {label}
    </span>
  )
}

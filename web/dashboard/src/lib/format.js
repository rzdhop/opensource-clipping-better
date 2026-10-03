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

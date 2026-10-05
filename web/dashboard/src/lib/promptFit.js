// The word count and the fit note of a prompt to copy (plan 26 stage 5).
//
// A prompt block of the handoff document (a shot's clip or keyframe, an
// entity image) and an image-brief entry carry, on a v2 story, `fit`:
// `{limit, words, full_words, dropped: [label]}` -- the prompt's size as sent
// for the link and what the fit to the link's limit dropped. A v1 story and a
// block that does not carry it yet have no `fit`: both helpers then add
// nothing.

/** "Copy prompt · 1793 words" -- the label alone without a fit. */
export function copyLabel(label, fit) {
  if (!fit || typeof fit.words !== 'number') return label
  return `${label} · ${fit.words} ${fit.words === 1 ? 'word' : 'words'}`
}

/**
 * "Fitted to 1800 words for this link: 2100 → 1793, dropped Series, Art
 * style"; null when the fit dropped nothing (or there is no fit).
 */
export function fitNote(fit) {
  if (!fit || !Array.isArray(fit.dropped) || fit.dropped.length === 0) return null
  return `Fitted to ${fit.limit} words for this link: ${fit.full_words} → ${fit.words}, dropped ${fit.dropped.join(', ')}`
}

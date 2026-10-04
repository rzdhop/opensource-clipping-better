// A story's own subtitle look (plan 23 stage B5): the form's values, the
// object PATCH /stories/{id}/subtitle-style takes, and the CSS of the
// approximate one-line preview. The numbers mirror
// clipping.aistory.schemas.SUBTITLE_STYLE_SCHEMA and clipping/aistory/render/
// subtitles.py (tests/test_dashboard_subtitle_style.py keeps them in step).

// assets/fonts/fonts_index.json's families, in its order
// (clipping.aistory.schemas.SUBTITLE_FONT_FAMILIES): the only ones a story may pick.
export const SUBTITLE_FONT_FAMILIES = ['Montserrat', 'Bangers', 'Luckiest Guy', 'Bebas Neue', 'Chewy', 'Patrick Hand']

// SUBTITLE_SIZE_PCT, SUBTITLE_POSITION_PCT, SUBTITLE_OUTLINE_PX, SUBTITLE_BOX_OPACITY_PCT
export const SIZE_PCT = { min: 60, max: 160 }
export const POSITION_PCT = { min: 15, max: 95 }
export const OUTLINE_PX = { min: 0, max: 8 }
export const BOX_OPACITY_PCT = { min: 0, max: 100 }

// What the render draws with no override (render/subtitles.py): word_pop is
// 62 px, centred at 77.5 % of the frame's height, white with a 3 px black
// outline; two_line is 56 px, its bottom edge at 100 - 18 = 82 %, a 2 px
// black outline (the speakers' own accents as text colours).
export const RENDER_DEFAULTS = {
  word_pop: { size: 62, position: 77.5, outline: 3, text: '#FFFFFF' },
  two_line: { size: 56, position: 82, outline: 2 },
  outlineColour: '#000000',
  highlight: '#FFD400',
}

export const BOX_DEFAULT = { colour: '#000000', opacity_pct: 60 }

// The editor's values from the story's `subtitle_style` (undefined/null: none).
// '' is "the style's own" for every optional value.
export function formFromStyle(style) {
  const s = style || {}
  return {
    fontFamily: s.font_family || '',
    sizePct: s.size_pct != null ? s.size_pct : 100,
    positionPct: s.position_pct != null ? String(s.position_pct) : '',
    textColour: s.text_colour || '',
    highlightColour: s.highlight_colour || '',
    outlinePx: s.outline_px != null ? String(s.outline_px) : '',
    outlineColour: s.outline_colour || '',
    boxOn: Boolean(s.box),
    boxColour: (s.box && s.box.colour) || BOX_DEFAULT.colour,
    boxOpacity: s.box ? s.box.opacity_pct : BOX_DEFAULT.opacity_pct,
  }
}

// The `subtitle_style` object of the form: only what differs from "the
// style's own", so an untouched form is `null` (clear the look).
export function styleFromForm(form, { withFont = true } = {}) {
  const style = {}
  if (withFont && form.fontFamily) style.font_family = form.fontFamily
  if (Number(form.sizePct) !== 100) style.size_pct = Number(form.sizePct)
  if (form.positionPct !== '') style.position_pct = Number(form.positionPct)
  if (form.textColour) style.text_colour = form.textColour
  if (form.highlightColour) style.highlight_colour = form.highlightColour
  if (form.outlinePx !== '') style.outline_px = Number(form.outlinePx)
  if (form.outlineColour) style.outline_colour = form.outlineColour
  if (form.boxOn) style.box = { colour: form.boxColour, opacity_pct: Number(form.boxOpacity) }
  return Object.keys(style).length ? style : null
}

function rgba(hex, opacityPct) {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${opacityPct / 100})`
}

// The CSS of one line over a 270 x 480 frame (a quarter of 1080 x 1920), as
// close as CSS gets: the font's size and place as the render sets them, the
// outline as a text stroke, the box as a background. word_pop is one
// uppercase word centred on `position`; two_line sits on its bottom edge.
export function previewCss(form, mode) {
  const scale = 0.25
  const twoLine = mode === 'two_line'
  const base = twoLine ? RENDER_DEFAULTS.two_line : RENDER_DEFAULTS.word_pop
  const position = form.positionPct !== '' ? Number(form.positionPct) : base.position
  const outline = form.outlinePx !== '' ? Number(form.outlinePx) : base.outline
  const colour = (!twoLine && form.textColour) || (twoLine ? '#FFFFFF' : RENDER_DEFAULTS.word_pop.text)
  const css = {
    position: 'absolute',
    left: 0,
    right: 0,
    textAlign: 'center',
    fontWeight: 700,
    fontStyle: twoLine ? 'normal' : 'italic',
    fontSize: `${(base.size * Number(form.sizePct) / 100) * scale}px`,
    lineHeight: 1.1,
    color: colour,
    fontFamily: form.fontFamily ? `'${form.fontFamily}', sans-serif` : 'sans-serif',
  }
  if (twoLine) css.bottom = `${100 - position}%`
  else { css.top = `${position}%`; css.transform = 'translateY(-50%)' }
  if (form.boxOn) {
    css.background = rgba(form.boxColour, Number(form.boxOpacity))
    css.padding = `${10 * scale}px`
    css.left = '10%'
    css.right = '10%'
  } else if (outline > 0) {
    css.WebkitTextStroke = `${outline * 2 * scale}px ${form.outlineColour || RENDER_DEFAULTS.outlineColour}`
    css.paintOrder = 'stroke fill'
  }
  return css
}

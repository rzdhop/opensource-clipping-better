const TONES = ['neutral', 'accent', 'success', 'warning', 'danger', 'info']

function toneOf(tone) {
  return TONES.includes(tone) ? tone : 'neutral'
}

/** A small status label: "Approved", "Draft", "Failed". `dot` adds a coloured dot before the text. */
export function Badge({ tone = 'neutral', dot = false, icon: Icon, className = '', children, ...rest }) {
  return (
    <span className={`ui-badge ui-badge-${toneOf(tone)} ${className}`.trim()} {...rest}>
      {dot && <span className="ui-badge-dot" aria-hidden="true" />}
      {Icon && <Icon size={12} aria-hidden="true" />}
      {children}
    </span>
  )
}

/**
 * A compact metadata chip. It renders the existing `.chip` styles (accent and
 * warning tones map onto `.chip-accent` / `.chip-warn`) with an optional icon;
 * given `onClick` it becomes a button, with the focus ring.
 */
export function Chip({ tone = 'neutral', icon: Icon, onClick, className = '', children, ...rest }) {
  const toneClass = tone === 'accent' ? 'chip-accent' : tone === 'warning' ? 'chip-warn' : ''
  const classes = ['chip', toneClass, 'ui-chip', onClick ? 'ui-chip-button' : '', className].filter(Boolean).join(' ')
  const content = (
    <>
      {Icon && <Icon size={12} aria-hidden="true" />}
      {children}
    </>
  )
  if (onClick) {
    return <button type="button" className={classes} onClick={onClick} {...rest}>{content}</button>
  }
  return <span className={classes} {...rest}>{content}</span>
}

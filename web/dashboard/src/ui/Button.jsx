import { Loader2 } from './icons'

const VARIANTS = ['primary', 'secondary', 'ghost', 'danger']

/** A spinning loader icon, sized to the text around it. */
export function Spinner({ size = 14, className = '', label }) {
  return (
    <Loader2
      size={size}
      className={`ui-spin ${className}`.trim()}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      role={label ? 'img' : undefined}
    />
  )
}

/**
 * The dashboard button. It renders the existing `.btn` classes, so a page can
 * swap a hand-written `<button className="btn btn-primary">` for it without a
 * visual change, and adds the focus ring, the loading spinner and an icon
 * slot. `as` renders another element with the same look (a router `Link`).
 */
export function Button({
  variant = 'secondary',
  size = 'md',
  loading = false,
  icon: Icon,
  iconRight: IconRight,
  as: Component = 'button',
  type,
  disabled,
  className = '',
  children,
  ...rest
}) {
  const tone = VARIANTS.includes(variant) ? variant : 'secondary'
  const classes = ['btn', `btn-${tone}`, size === 'sm' ? 'btn-sm' : '', 'ui-btn', className]
    .filter(Boolean).join(' ')
  const isButton = Component === 'button'
  const iconSize = size === 'sm' ? 14 : 16
  return (
    <Component
      className={classes}
      type={isButton ? (type || 'button') : type}
      disabled={isButton ? (disabled || loading) : undefined}
      aria-disabled={!isButton && (disabled || loading) ? true : undefined}
      aria-busy={loading ? true : undefined}
      {...rest}
    >
      {loading ? <Spinner size={iconSize} /> : Icon ? <Icon size={iconSize} aria-hidden="true" /> : null}
      {children}
      {IconRight && <IconRight size={iconSize} aria-hidden="true" />}
    </Component>
  )
}

/**
 * A square, icon-only button. `aria-label` is required: with no visible text
 * it is the button's only name for a screen reader (and its tooltip).
 */
export function IconButton({
  icon: Icon,
  'aria-label': ariaLabel,
  variant = 'ghost',
  size = 'md',
  loading = false,
  type = 'button',
  disabled,
  className = '',
  title,
  ...rest
}) {
  if (!ariaLabel && import.meta.env.DEV) {
    console.warn('IconButton needs an aria-label')
  }
  const tone = VARIANTS.includes(variant) ? variant : 'ghost'
  const iconSize = size === 'sm' ? 14 : 18
  return (
    <button
      type={type}
      className={`ui-icon-btn ui-icon-btn-${tone} ui-icon-btn-${size === 'sm' ? 'sm' : 'md'} ${className}`.trim()}
      aria-label={ariaLabel}
      title={title || ariaLabel}
      disabled={disabled || loading}
      aria-busy={loading ? true : undefined}
      {...rest}
    >
      {loading ? <Spinner size={iconSize} /> : Icon && <Icon size={iconSize} aria-hidden="true" />}
    </button>
  )
}

/**
 * A loading placeholder. `variant="lines"` (the default) draws `lines` text
 * bars, the last one shorter; `variant="card"` draws a card-sized block with
 * a title bar and two lines. Hidden from screen readers: pair it with a
 * visible or `aria-live` "Loading…" where the wait matters.
 */
export function Skeleton({ variant = 'lines', lines = 3, height, className = '' }) {
  if (variant === 'card') {
    return (
      <div className={`ui-skeleton-card ${className}`.trim()} aria-hidden="true" style={height ? { height } : undefined}>
        <span className="ui-skeleton ui-skeleton-title" />
        <span className="ui-skeleton ui-skeleton-line" />
        <span className="ui-skeleton ui-skeleton-line ui-skeleton-short" />
      </div>
    )
  }
  const count = Math.max(1, lines)
  return (
    <div className={`ui-skeleton-lines ${className}`.trim()} aria-hidden="true">
      {Array.from({ length: count }, (_, i) => (
        <span
          key={i}
          className={`ui-skeleton ui-skeleton-line${i === count - 1 && count > 1 ? ' ui-skeleton-short' : ''}`}
        />
      ))}
    </div>
  )
}

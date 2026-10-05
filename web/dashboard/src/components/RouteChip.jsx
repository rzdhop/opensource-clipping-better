import { Ban, CircleDollarSign, Gift, Monitor } from '../ui/icons'

// Where a story step's generation would run, from an estimate's
// `route_class` (GET /api/stories/{id}/estimate/{step}) or a story's `route`.
const ROUTE_LABELS = {
  local: 'local',
  free: 'free',
  paid: 'paid',
  // Plan 23 stage B8: a stock cutaway (a frame or a clip from a stock source, free).
  stock: 'stock',
  blocked: 'blocked',
}

const ROUTE_ICONS = {
  local: Monitor,
  free: Gift,
  paid: CircleDollarSign,
  stock: Gift,
  blocked: Ban,
}

/**
 * A compact chip naming the route class and, when known, the link that would
 * carry it -- reusing `.chip-sub` for the link so it ellipsizes on a narrow
 * screen exactly like the provider chip's model id already does. `blocked`
 * gets the same warn styling a refused link gets elsewhere in the dashboard.
 */
export default function RouteChip({ routeClass, link }) {
  const label = ROUTE_LABELS[routeClass] || routeClass || 'unknown'
  const Icon = ROUTE_ICONS[routeClass]
  return (
    <span
      className={`chip${routeClass === 'blocked' ? ' chip-warn' : ''}`}
      title={link || undefined}
    >
      {Icon && <Icon size={12} aria-hidden="true" />}
      {label}
      {link && <span className="chip-sub" title={link}>{link}</span>}
    </span>
  )
}

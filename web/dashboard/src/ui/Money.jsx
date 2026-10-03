import { formatUsd } from '../lib/format'

/**
 * A dollar amount as the story pages print it: "$0.00", "$0.042", or with
 * `estimate` "est. $0.042". Tabular figures, so columns of amounts line up.
 */
export function Money({ value, estimate = false, className = '', title }) {
  return (
    <span className={`ui-money ${className}`.trim()} title={title}>
      {estimate ? 'est. ' : ''}${formatUsd(value)}
    </span>
  )
}

/**
 * "More": the secondary actions of a step behind one disclosure (plan 28 stage S3, DEC-305
 * section 9: one button per step, the rest folded -- nothing removed). A step shows its one
 * primary action; what else it can do sits under this fold. `open` keeps it open (an error
 * or a running job belongs in view).
 */
export default function MoreFold({ children, label = 'More', open, className = '' }) {
  return (
    <details className={`story-profile story-more ${className}`.trim()} open={open || undefined}>
      <summary>{label}</summary>
      <div className="story-more-body">{children}</div>
    </details>
  )
}

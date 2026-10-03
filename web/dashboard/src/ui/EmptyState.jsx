/** What a page or panel shows when it has nothing yet: an icon, a title, a sentence and one action. */
export function EmptyState({ icon: Icon, title, text, action, className = '', children }) {
  return (
    <div className={`ui-empty ${className}`.trim()}>
      {Icon && <span className="ui-empty-icon" aria-hidden="true"><Icon size={28} /></span>}
      {title && <h3 className="ui-empty-title">{title}</h3>}
      {text && <p className="ui-empty-text">{text}</p>}
      {children}
      {action && <div className="ui-empty-action">{action}</div>}
    </div>
  )
}

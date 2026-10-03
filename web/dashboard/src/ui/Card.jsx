/**
 * A surface with optional header / body / footer slots:
 *
 *   <Card>
 *     <CardHeader title="Cast" subtitle="4 characters" actions={<Button>…</Button>} />
 *     <CardBody>…</CardBody>
 *     <CardFooter>…</CardFooter>
 *   </Card>
 *
 * The slots carry their own padding; a card used without slots passes
 * `padded` to pad its children instead. `interactive` adds the hover lift.
 */
export function Card({ as: Component = 'section', padded = false, interactive = false, className = '', children, ...rest }) {
  const classes = ['ui-card', padded ? 'ui-card-padded' : '', interactive ? 'ui-card-interactive' : '', className]
    .filter(Boolean).join(' ')
  return <Component className={classes} {...rest}>{children}</Component>
}

export function CardHeader({ title, subtitle, actions, icon: Icon, titleAs: Title = 'h3', id, children }) {
  return (
    <header className="ui-card-header">
      {Icon && <span className="ui-card-header-icon" aria-hidden="true"><Icon size={18} /></span>}
      <div className="ui-card-header-text">
        {title && <Title className="ui-card-title" id={id}>{title}</Title>}
        {subtitle && <p className="ui-card-subtitle">{subtitle}</p>}
        {children}
      </div>
      {actions && <div className="ui-card-actions">{actions}</div>}
    </header>
  )
}

export function CardBody({ className = '', children, ...rest }) {
  return <div className={`ui-card-body ${className}`.trim()} {...rest}>{children}</div>
}

export function CardFooter({ className = '', children, ...rest }) {
  return <footer className={`ui-card-footer ${className}`.trim()} {...rest}>{children}</footer>
}

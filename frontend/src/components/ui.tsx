import { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import clsx from 'clsx'

export function ShellNavItem({ to, label, icon }: { to: string; label: string; icon: ReactNode }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) => clsx('shell-nav__item', isActive && 'shell-nav__item--active')}
    >
      <span className="shell-nav__icon">{icon}</span>
      <span>{label}</span>
    </NavLink>
  )
}

export function Surface({
  title,
  subtitle,
  actions,
  children,
  className,
}: {
  title?: string
  subtitle?: string
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={clsx('surface', className)}>
      {(title || subtitle || actions) && (
        <header className="surface__header">
          <div>
            {title ? <h2 className="surface__title">{title}</h2> : null}
            {subtitle ? <p className="surface__subtitle">{subtitle}</p> : null}
          </div>
          {actions ? <div className="surface__actions">{actions}</div> : null}
        </header>
      )}
      {children}
    </section>
  )
}

export function MetricCard({
  label,
  value,
  note,
  tone = 'default',
  icon,
}: {
  label: string
  value: string | number
  note?: string
  tone?: 'default' | 'success' | 'warning' | 'danger' | 'brand' | 'info'
  icon?: ReactNode
}) {
  return (
    <div className={clsx('metric-card', `metric-card--${tone}`)}>
      <div className="metric-card__top">
        <span className="metric-card__label">{label}</span>
        {icon ? <span className="metric-card__icon">{icon}</span> : null}
      </div>
      <div className="metric-card__value">{value}</div>
      {note ? <div className="metric-card__note">{note}</div> : null}
    </div>
  )
}

export function Badge({
  label,
  tone = 'neutral',
}: {
  label: string
  tone?: 'neutral' | 'brand' | 'success' | 'warning' | 'danger' | 'info'
}) {
  return <span className={clsx('badge', `badge--${tone}`)}>{label}</span>
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="empty-state">
      <div className="empty-state__title">{title}</div>
      {detail ? <div className="empty-state__detail">{detail}</div> : null}
    </div>
  )
}

export function KeyValueList({ items }: { items: Array<{ label: string; value: ReactNode }> }) {
  return (
    <div className="kv-list">
      {items.map((item) => (
        <div key={item.label} className="kv-list__row">
          <span className="kv-list__label">{item.label}</span>
          <span className="kv-list__value">{item.value}</span>
        </div>
      ))}
    </div>
  )
}

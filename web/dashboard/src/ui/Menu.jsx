import { useEffect, useId, useRef, useState } from 'react'
import { IconButton } from './Button'
import { MoreHorizontal } from './icons'

/**
 * An overflow menu: an icon button that opens a small list of actions.
 *
 *   <Menu label="Job actions" items={[
 *     { label: 'Open', icon: ArrowRight, onSelect: open },
 *     { label: 'Delete…', icon: Trash2, tone: 'danger', onSelect: remove },
 *   ]} />
 *
 * The trigger carries `aria-haspopup="menu"` and `aria-expanded`; the list is
 * `role="menu"` with `role="menuitem"` buttons. Opening focuses the first
 * item; ArrowUp/ArrowDown/Home/End move between items; Escape, Tab or a
 * click outside closes it. Choosing an item closes the menu and puts focus
 * back on the trigger *before* `onSelect` runs, so a dialog the action opens
 * returns focus to the trigger when it closes.
 */
export function Menu({ label, items, icon = MoreHorizontal, size = 'sm', align = 'end', disabled, loading, className = '' }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const listRef = useRef(null)
  const menuId = useId()

  const itemsIn = () => Array.from((listRef.current && listRef.current.querySelectorAll('[role="menuitem"]:not(:disabled)')) || [])

  useEffect(() => {
    if (!open) return undefined
    const first = itemsIn()[0]
    if (first) first.focus()
    const onPointerDown = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  const close = (refocus = true) => {
    setOpen(false)
    if (refocus) {
      const trigger = rootRef.current && rootRef.current.querySelector('button[aria-haspopup="menu"]')
      if (trigger) trigger.focus()
    }
  }

  const onKeyDown = (event) => {
    const list = itemsIn()
    const index = list.indexOf(document.activeElement)
    if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      close()
    } else if (event.key === 'Tab') {
      close(false)
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!list.length) return
      const step = event.key === 'ArrowDown' ? 1 : -1
      list[(index + step + list.length) % list.length].focus()
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      if (list.length) list[event.key === 'Home' ? 0 : list.length - 1].focus()
    }
  }

  const choose = (item) => {
    close()
    if (item.onSelect) item.onSelect()
  }

  return (
    <div className={`ui-menu ${className}`.trim()} ref={rootRef}>
      <IconButton
        icon={icon}
        aria-label={label}
        size={size}
        disabled={disabled}
        loading={loading}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={(event) => {
          event.preventDefault()
          event.stopPropagation()
          setOpen((value) => !value)
        }}
      />
      {open && (
        <div
          id={menuId}
          ref={listRef}
          role="menu"
          aria-label={label}
          className={`ui-menu-list ui-menu-list-${align === 'start' ? 'start' : 'end'}`}
          onKeyDown={onKeyDown}
        >
          {items.map((item) => {
            const Icon = item.icon
            return (
              <button
                key={item.label}
                type="button"
                role="menuitem"
                tabIndex={-1}
                className={`ui-menu-item${item.tone === 'danger' ? ' ui-menu-item-danger' : ''}`}
                disabled={item.disabled}
                onClick={(event) => {
                  event.preventDefault()
                  event.stopPropagation()
                  choose(item)
                }}
              >
                {Icon && <Icon size={15} aria-hidden="true" />}
                <span>{item.label}</span>
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

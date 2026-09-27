import { useRef } from 'react'

let idSeq = 0

/**
 * A WAI-ARIA tabs widget (https://www.w3.org/WAI/ARIA/apg/patterns/tabs/):
 * `role="tablist"` holding `role="tab"` buttons, each owning a
 * `role="tabpanel"`. Only the active tab is in the tab order (roving
 * `tabIndex`); Left/Right move and activate the neighbour, Home/End jump to
 * the first/last -- the APG's "automatic activation" model, which is the
 * right fit here since switching panes is cheap (no data fetch on select).
 *
 * `tabs` is `[{id, label}]`; `children` is either a render function
 * `(id) => node` (called once, for the active tab only, so an inactive
 * pane's hooks and requests do not run) or an `{id: node}` map.
 */
export default function Tabs({ tabs, active, onChange, children }) {
  const idBase = useRef(`tabs-${++idSeq}`).current
  const tabRefs = useRef({})

  const activeIndex = Math.max(0, tabs.findIndex((tab) => tab.id === active))

  const activate = (index) => {
    const tab = tabs[(index + tabs.length) % tabs.length]
    if (!tab) return
    onChange(tab.id)
    const el = tabRefs.current[tab.id]
    if (el) el.focus()
  }

  const onKeyDown = (e) => {
    if (e.key === 'ArrowRight') { e.preventDefault(); activate(activeIndex + 1) }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); activate(activeIndex - 1) }
    else if (e.key === 'Home') { e.preventDefault(); activate(0) }
    else if (e.key === 'End') { e.preventDefault(); activate(tabs.length - 1) }
  }

  const panelFor = (id) => (typeof children === 'function' ? children(id) : children[id])

  return (
    <div className="tabs">
      <div className="tabs-list" role="tablist" onKeyDown={onKeyDown}>
        {tabs.map((tab) => {
          const selected = tab.id === active
          return (
            <button
              key={tab.id}
              ref={(el) => { tabRefs.current[tab.id] = el }}
              type="button"
              role="tab"
              id={`${idBase}-tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={`${idBase}-panel-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              className={`tabs-tab${selected ? ' active' : ''}`}
              onClick={() => onChange(tab.id)}
            >
              {tab.label}
            </button>
          )
        })}
      </div>
      {tabs.map((tab) => {
        const selected = tab.id === active
        return (
          <div
            key={tab.id}
            role="tabpanel"
            id={`${idBase}-panel-${tab.id}`}
            aria-labelledby={`${idBase}-tab-${tab.id}`}
            hidden={!selected}
            className="tabs-panel"
          >
            {selected && panelFor(tab.id)}
          </div>
        )
      })}
    </div>
  )
}

import { useEffect, useRef } from 'react'
import { NavLink } from 'react-router-dom'
import { Spinner } from '../../ui'
import { Check, Lock } from '../../ui/icons'

const STATUS_TEXT = { done: 'Done', active: 'To do', disabled: 'Locked' }

/**
 * The workspace's step rail: one link per step, with its icon, a status mark
 * (done, to do, locked -- a locked step's reason is its tooltip) and a
 * spinner on the step whose job runs. A 240 px column from 901 px up; under
 * that, a horizontal stepper above the content (CSS only), scrolled so the
 * open step is in view.
 *
 * `steps`: [{key, label, icon, status, tooltip}] in order.
 */
export default function StepRail({ storyId, steps, activeKey, runningKey }) {
  const listRef = useRef(null)

  useEffect(() => {
    const list = listRef.current
    if (!list || list.scrollWidth <= list.clientWidth) return
    const item = list.querySelector('[aria-current="page"]')
    if (item) list.scrollLeft = item.offsetLeft - (list.clientWidth - item.offsetWidth) / 2
  }, [activeKey])

  return (
    <nav className="story-rail" aria-label="Story steps">
      <ol className="story-rail-list" ref={listRef}>
        {steps.map((step, index) => {
          const Icon = step.icon
          const running = runningKey === step.key
          return (
            <li key={step.key} className="story-rail-item">
              <NavLink
                to={`/story/${storyId}/${step.key}`}
                className={`story-rail-link story-rail-${step.status}${running ? ' story-rail-running' : ''}`}
                title={step.tooltip || undefined}
              >
                <span className="story-rail-mark" aria-hidden="true">
                  {step.status === 'done'
                    ? <Check size={14} strokeWidth={3} />
                    : step.status === 'disabled' ? <Lock size={13} /> : index + 1}
                </span>
                <span className="story-rail-text">
                  <span className="story-rail-label">
                    {Icon && <Icon size={15} aria-hidden="true" className="story-rail-icon" />}
                    {step.label}
                  </span>
                  <span className="story-rail-status">
                    {running ? 'Running…' : STATUS_TEXT[step.status]}
                  </span>
                </span>
                {running && <Spinner size={14} className="story-rail-spinner" label="A job runs on this step" />}
              </NavLink>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

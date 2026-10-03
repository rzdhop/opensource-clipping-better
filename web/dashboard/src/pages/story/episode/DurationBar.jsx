// The episode's timing bar (spec 10): the template's window, its target and
// tighten marks, one segment per scene (width proportional to its duration),
// the running total and the timing state. Its flags are TimingWarnings below
// (dashboard overhaul stage 4, DEC-256: a collapsible warning panel at the
// top of the Script pane) -- each flag a real link to the scene or line it
// names, so a phone-width page can jump straight to it instead of hunting
// the scene list.

import { AlertTriangle, ChevronRight } from '../../../ui/icons'

const SCENE_FUNCTION_LABELS = {
  recap: 'Recap', hook: 'Hook', setup: 'Setup', rising: 'Rising',
  peak: 'Peak', turn: 'Turn', cliffhanger: 'Cliffhanger',
}

const TIMING_STATE_LABELS = { ok: 'on target', tightened: 'tightened', over: 'over the window', under: 'under the window' }

function targetOf(flag) {
  if (flag.scene_id) return `scene-${flag.scene_id}`
  if (flag.line_id) return `line-${flag.line_id}`
  return null
}

export default function DurationBar({ template, scenes, timing }) {
  if (!timing) {
    return <p className="form-hint">Not timed yet.</p>
  }

  const [windowLow, windowHigh] = template.window_s
  const scale = Math.max(timing.total_s, windowHigh, 1)
  const pct = (s) => Math.min(100, Math.max(0, (s / scale) * 100))

  const measuredLabel = timing.estimated_lines === 0 && timing.measured_lines > 0
    ? 'measured'
    : timing.measured_lines > 0 ? 'partly measured' : 'estimated'

  return (
    <div className="duration-bar">
      <div className="duration-bar-track">
        <div
          className="duration-bar-window"
          style={{ left: `${pct(windowLow)}%`, width: `${pct(windowHigh) - pct(windowLow)}%` }}
          title={`Window ${windowLow}–${windowHigh} s`}
        />
        <div className="duration-bar-scenes">
          {(scenes || []).map((scene) => {
            const sceneTiming = timing.scenes[scene.scene_id]
            if (!sceneTiming) return null
            return (
              <div
                key={scene.scene_id}
                className={`duration-bar-segment duration-bar-segment-${sceneTiming.state}`}
                style={{ width: `${pct(sceneTiming.duration_s)}%` }}
                title={`${scene.scene_id} · ${SCENE_FUNCTION_LABELS[scene.function] || scene.function} — ${sceneTiming.duration_s.toFixed(1)} s`}
              />
            )
          })}
        </div>
        <div className="duration-bar-mark duration-bar-mark-target" style={{ left: `${pct(template.target_s)}%` }}
             title={`Target ${template.target_s} s`} />
        <div className="duration-bar-mark duration-bar-mark-tighten" style={{ left: `${pct(template.tighten_above_s)}%` }}
             title={`Tighten above ${template.tighten_above_s} s`} />
      </div>

      <p className="duration-bar-total">
        {timing.total_s.toFixed(1)} s · {measuredLabel}
        {timing.state !== 'ok' && (
          <span className="chip chip-warn duration-bar-state">{TIMING_STATE_LABELS[timing.state] || timing.state}</span>
        )}
      </p>
    </div>
  )
}

/**
 * The timing flags as one collapsible warning panel: the count in its
 * summary (a native <details>, so it opens from the keyboard too), and the
 * same messages as before, each a link to the scene or line it names.
 * Nothing at all while the episode has no flag.
 */
export function TimingWarnings({ timing }) {
  const flags = (timing && timing.flags) || []
  if (flags.length === 0) return null
  return (
    <details className="story-script-warnings">
      <summary className="story-script-warnings-summary">
        <AlertTriangle size={15} aria-hidden="true" className="story-script-warnings-icon" />
        <span>{flags.length} timing warning{flags.length === 1 ? '' : 's'}</span>
        <ChevronRight size={15} aria-hidden="true" className="story-script-warnings-chevron" />
      </summary>
      <ul className="story-field-list duration-bar-flags">
        {flags.map((flag, i) => {
          const anchor = targetOf(flag)
          return (
            <li key={i}>
              {anchor ? <a href={`#${anchor}`}>{flag.message}</a> : flag.message}
            </li>
          )
        })}
      </ul>
    </details>
  )
}

// The episode's timing bar (spec 10): the template's window, its target and
// tighten marks, one segment per scene (width proportional to its duration),
// the running total and the timing state. Its flags are TimingWarnings below
// (dashboard overhaul stage 4, DEC-256: a collapsible warning panel at the
// top of the Script pane) -- each flag a real link to the scene or line it
// names, so a phone-width page can jump straight to it instead of hunting
// the scene list.

import { useState } from 'react'
import { regenerateStory } from '../../../api'
import { StepError } from '../fields'
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

// Plan 24 stage 6: a "Trim" action on a timing warning. It asks the writer
// to rewrite the flagged scene through the scene regenerate that already
// exists (POST /regenerate, target scene:<ep>:<sid>) with a note built here
// from the scene's slot and its line plan; no new route.

const SLOT_IN_MESSAGE = /its (\d+(?:\.\d+)?) s slot/

// The scene's upper slot bound: the stored slot_s when the scene carries one,
// else the figure a flag of the same scene names in its message ("Scene s02
// is 2.1 s over its 13 s slot." -- the scene_over flag; a trim_line flag's own
// message omits it), else null.
export function sceneSlotHi(flag, scene, flags = []) {
  if (scene && Array.isArray(scene.slot_s) && scene.slot_s.length === 2) return scene.slot_s[1]
  for (const f of [flag, ...flags]) {
    if (!f || f.scene_id !== flag.scene_id) continue
    const found = SLOT_IN_MESSAGE.exec(f.message || '')
    if (found) return Number(found[1])
  }
  return null
}

function plannedLine(plan, index) {
  const line = plan && Array.isArray(plan.lines) ? plan.lines[index] : null
  if (!line) return null
  return `line ${index + 1} (${line.speaker || 'narrator'}) at most ${line.max_words} words`
}

// "Trim to the slot: scene s02 lasts at most 13 s, line 1 (narrator) at most
// 14 words; keep the meaning and the speaker, cut words." The line part is
// the flagged line's cap, or every planned line's when the flag names none;
// a scene without a line plan gets the slot alone.
export function trimNote(flag, scene, flags = []) {
  if (flag.kind === 'episode_over') {
    // Plan 28 A3: a native episode over its window is trimmed at the scene the server names first; the server
    // plans it again inside the episode's fit before the writer rewrites it.
    return `Trim to fit the episode (${flag.message}): scene ${flag.scene_id} gets fewer or shorter clips; `
      + 'keep the meaning and the speaker, cut words.'
  }
  const hi = sceneSlotHi(flag, scene, flags)
  let note = `Trim to the slot: scene ${flag.scene_id} ` + (hi == null ? 'is over its slot' : `lasts at most ${hi} s`)
  const plan = scene && scene.line_plan
  if (plan && Array.isArray(plan.lines) && plan.lines.length > 0) {
    const lines = (scene.lines || [])
    const at = flag.line_id ? lines.findIndex((line) => line.line_id === flag.line_id) : -1
    const parts = at >= 0
      ? [plannedLine(plan, at)]
      : plan.lines.map((_, i) => plannedLine(plan, i))
    const named = parts.filter(Boolean)
    if (named.length > 0) note += `, ${named.join(', ')}`
  }
  return `${note}; keep the meaning and the speaker, cut words.`
}

function TrimButton({ storyId, ep, flag, flags, scene, disabled, onChange }) {
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const run = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      await regenerateStory(storyId, { target: `scene:${ep}:${flag.scene_id}`, note: trimNote(flag, scene, flags) })
      if (onChange) onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  return (
    <>
      {' '}
      <button type="button" className="btn btn-secondary btn-sm duration-bar-trim" onClick={run}
              disabled={disabled || running}>
        {running ? 'Trimming…' : 'Trim'}
      </button>
      <StepError message={error} errors={errors} className="story-step-error" />
    </>
  )
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
export function TimingWarnings({ timing, scenes, storyId, ep, busy, onChange }) {
  const flags = (timing && timing.flags) || []
  if (flags.length === 0) return null
  const sceneById = Object.fromEntries((scenes || []).map((scene) => [scene.scene_id, scene]))
  const trimmed = new Set(flags.filter((f) => f.kind === 'trim_line' && f.scene_id).map((f) => f.scene_id))
  const sceneFlagged = new Set(flags.filter((f) => f.kind !== 'episode_over' && f.scene_id).map((f) => f.scene_id))
  // A trim_line flag is trimmable; so is a scene_over flag whose scene has no
  // trim_line flag of its own (the same scene is not offered twice); and
  // (plan 28 A3, a native story) the episode_over flag naming the scene to
  // trim first, when no other flag offers that scene already.
  const trimmable = (flag) => Boolean(storyId && flag.scene_id && sceneById[flag.scene_id]) && (
    flag.kind === 'trim_line' || (flag.kind === 'scene_over' && !trimmed.has(flag.scene_id))
    || (flag.kind === 'episode_over' && !sceneFlagged.has(flag.scene_id)))
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
              {trimmable(flag) && (
                <TrimButton storyId={storyId} ep={ep} flag={flag} flags={flags} scene={sceneById[flag.scene_id]}
                            disabled={busy} onChange={onChange} />
              )}
            </li>
          )
        })}
      </ul>
    </details>
  )
}

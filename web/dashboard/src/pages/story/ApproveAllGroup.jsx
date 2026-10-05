import { useState } from 'react'
import { approveStoryGroup } from '../../api'
import { StepError } from './fields'

// What an entity still needs, in the words a person uses (the server's codes:
// clipping.aistory.workflow.MISSING_LABELS).
const NEEDS = {
  text: 'a description',
  portrait: 'a portrait',
  turnaround: 'a turnaround sheet',
  expressions: 'an expressions sheet',
  voice: 'a voice',
  sample: 'a voice sample',
  day: 'a daytime picture',
  image: 'a picture',
}

function joinWords(words) {
  if (words.length <= 1) return words.join('')
  return `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`
}

/** One plain line for an entity "Approve all" left alone: "Gaston still needs a portrait". */
export function skippedLine(item) {
  const codes = Array.isArray(item.missing) ? item.missing : []
  const words = codes.map((code) => NEEDS[code] || code)
  return words.length > 0 ? `${item.name} still needs ${joinWords(words)}.` : `${item.name} could not be approved yet.`
}

/**
 * The "Approve all" button of the Cast and Places steps: one call approves
 * every character (or every place and prop) that has everything, then the
 * page is refetched. What is left is named, one line each. `group` is `cast`
 * or `places`; `disabled` while a step of the story runs.
 */
export default function ApproveAllGroup({ storyId, group, disabled, onChange }) {
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  const [skipped, setSkipped] = useState([])
  const [approved, setApproved] = useState(null)

  const handleClick = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      const result = await approveStoryGroup(storyId, group)
      setSkipped(result.skipped || [])
      setApproved((result.approved || []).length)
      if (result.refused) setError(result.refused)
      onChange()
    } catch (err) {
      setSkipped([])
      setApproved(null)
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="story-step-actions">
      <button type="button" className="btn btn-primary" onClick={handleClick} disabled={disabled || running}>
        {running ? <><span className="spinner"></span> Approving…</> : 'Approve all'}
      </button>
      {approved !== null && skipped.length === 0 && !error && (
        <span className="form-hint">{approved === 0 ? 'Nothing new to approve.' : `${approved} approved.`}</span>
      )}
      {skipped.length > 0 && (
        <ul className="story-field-list">
          {approved ? <li>{approved} approved.</li> : null}
          {skipped.map((item) => <li key={`${item.kind}:${item.id}`}>{skippedLine(item)}</li>)}
        </ul>
      )}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

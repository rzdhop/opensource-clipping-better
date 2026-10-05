import { useState } from 'react'
import { approveStoryDoc } from '../../../api'
import { StepError } from '../fields'

/**
 * What is still to approve on an episode, in the order it is approved:
 * `[{label, target}]` -- the script, the storyboard, the keyframes (a v2
 * episode's), the assets. A document not made yet is not listed (there is
 * nothing to approve), nor is one already approved.
 */
export function pendingApprovals(episode, ep) {
  const pending = []
  if (episode.script && !episode.script.approved_at) pending.push({ label: 'script', target: `script:${ep}` })
  if (episode.storyboard && !episode.storyboard.approved_at) {
    pending.push({ label: 'storyboard', target: `storyboard:${ep}` })
  }
  const assets = episode.assets
  const made = Boolean(assets && assets.doc)
  const keyframes = episode.review && episode.review.approvals && episode.review.approvals.keyframes
  if (made && keyframes && keyframes.approval !== 'current') {
    pending.push({ label: 'keyframes', target: keyframes.target || `keyframes:${ep}` })
  }
  if (made && assets.fingerprint !== 'current') pending.push({ label: 'assets', target: `assets:${ep}` })
  return pending
}

/**
 * The episode studio's "Approve all": the script, the storyboard, the
 * keyframes and the assets, one after the other through the same approvals
 * their own buttons use. It stops at the first one the server refuses and
 * shows that sentence; it never approves anyway (the script's "Approve
 * anyway" stays a separate, explicit click; the keyframes have none since
 * plan 28 F1 -- a hard gate, regenerate or upload your own instead).
 */
export default function EpisodeApproveAll({ storyId, ep, episode, busy, onChange }) {
  const [approving, setApproving] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const pending = pendingApprovals(episode, ep)
  if (pending.length === 0) return null

  const handleClick = async () => {
    setApproving(true)
    setError('')
    setErrors(null)
    try {
      for (const item of pending) {
        try {
          await approveStoryDoc(storyId, item.target)
        } catch (err) {
          setError(`Stopped at the ${item.label}: ${err.message}`)
          setErrors(err.errors || null)
          break
        }
      }
    } finally {
      setApproving(false)
      onChange()
    }
  }

  return (
    <>
      <div className="story-step-actions episode-studio-approve-all">
        <button type="button" className="btn btn-primary" onClick={handleClick} disabled={busy || approving}>
          {approving ? 'Approving…' : 'Approve all'}
        </button>
        <span className="form-hint">
          {busy ? 'A step is running.' : `In order: ${pending.map((item) => item.label).join(', ')}.`}
        </span>
      </div>
      <StepError message={error} errors={errors} className="story-step-error" />
    </>
  )
}

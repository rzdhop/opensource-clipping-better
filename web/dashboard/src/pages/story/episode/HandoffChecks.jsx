// Plan 28 F7 (DEC-305 section 5): what a shot's clip row of the Handoff says
// about the checks around it -- the keyframe's check (J2), what the platform's
// image cap left out, and the first frame of the human's uploaded clip against
// its keyframe. Every line is the server's own sentence
// (clipping.aistory.steps.brief: keyframe_check, references_cut, first_frame).

const TONES = {
  passed: 'handoff-check-ok', failed: 'handoff-check-bad', unjudged: 'handoff-check-wait', stale: 'handoff-check-wait',
  none: 'handoff-check-wait',
}

/** The lines under a clip row's references; nothing when it has none. */
export default function HandoffChecks({ block }) {
  const check = block.keyframe_check
  const lines = []
  if (check && check.line) {
    lines.push(<p key="keyframe" className={`form-hint handoff-check ${TONES[check.state] || ''}`}>{check.line}</p>)
  }
  if (block.references_cut) lines.push(<p key="cut" className="form-hint handoff-check">{block.references_cut}</p>)
  if (block.first_frame) {
    lines.push(<p key="first" className="chip chip-warn chip-wrap handoff-check" role="status">{block.first_frame}</p>)
  }
  return lines.length ? <div className="handoff-checks">{lines}</div> : null
}

/** Why the clip cannot be uploaded yet (an app-made keyframe not current or not passed), or null. */
export function uploadRefusal(block) {
  return (block.keyframe_check && block.keyframe_check.upload_refusal) || null
}

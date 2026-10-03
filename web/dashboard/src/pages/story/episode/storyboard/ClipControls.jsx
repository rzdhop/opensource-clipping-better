// A shot's clip controls (phase 6; dashboard overhaul stage 4, DEC-256: split
// out of StoryboardPane.jsx, the code moved as it was): the clip preview,
// its state and route badges, Animate / Keep still, the native-audio toggle
// and the note-driven re-animate with its own estimate.

import { useEffect, useRef, useState } from 'react'
import { regenerateStory, fetchStoryEstimate, patchEpisodeAssets, fetchEpisodeClipUrl } from '../../../../api'
import EstimateChip from '../../../../components/EstimateChip'
import RouteChip from '../../../../components/RouteChip'
import { RegenerateControl, StepError } from '../../fields'
import { Badge } from '../../../../ui'

// ----------------------------------------------------------------- clip (phase 6)

// clipping.aistory.steps.clips.CLIP_DERIVED_STATES, plus the two states the
// page derives on top (clip.continue/clip.pending, workflow.episode_clips):
// a held request only Continue (the assets step) can collect reads as
// "generating", a queued regenerate not yet picked up as "pending".
const CLIP_STATE_LABELS = { none: 'no clip', current: 'current', stale: 'stale', failed: 'failed' }

function clipStatusLabel(clip) {
  if (clip.continue) return 'generating'
  if (clip.pending) return 'pending…'
  return CLIP_STATE_LABELS[clip.state] || clip.state
}

function ClipStateBadge({ clip }) {
  return <Badge tone={clipStateTone(clip)}>{clipStatusLabel(clip)}</Badge>
}

/** The kit tone a clip's state reads in (the filmstrip's dots use it too). */
function clipStateTone(clip) {
  if (clip.continue || clip.pending) return 'info'
  if (clip.state === 'current') return 'success'
  if (clip.state === 'failed') return 'danger'
  if (clip.state === 'stale') return 'warning'
  return 'neutral'
}

// Links known to carry no sound track of their own (checkpoint stage 10:
// seedance -- the cheapest default link -- and kling always fall back for a
// kept-native-audio shot); matched loosely against the recorded `provider/model`
// link string, since this is a UI hint, not the gate itself (the render step's
// own fallback is what actually decides, per shot, with its own printed note).
const NO_AUDIO_LINK_RE = /seedance|kling/i

/**
 * One shot's clip regenerate-with-note (`shot:<ep>:<shid>:video`), with its
 * own estimate fetched first (`GET /estimate/regenerate?target=`,
 * `workflow.regenerate_clip_estimate`) -- shown before anything is spent,
 * same reasoning as every other estimate-driven control in this file.
 *
 * While `clip.blocked` is set the target is still non-null (a "kept still"
 * or similar refusal still names its target; only a held `continue` request
 * clears `target`), so the estimate was still fetched and 409'd -- caught
 * and swallowed, leaving the chip stuck on "estimating…" forever (browser-
 * check finding F2). `clip.blocked` is already shown as the control's own
 * reason, so nothing is fetched or shown here while it is set.
 */
function ClipRegenerate({ storyId, ep, clip, disabled, onChange }) {
  const [estimate, setEstimate] = useState(null)

  useEffect(() => {
    setEstimate(null)
    if (!clip.target || clip.blocked) return undefined
    let cancelled = false
    fetchStoryEstimate(storyId, 'regenerate', { target: clip.target })
      .then((data) => { if (!cancelled) setEstimate(data) })
      .catch(() => { if (!cancelled) setEstimate(null) })
    return () => { cancelled = true }
  }, [storyId, clip.target, clip.blocked])

  const regenerate = async (note) => {
    await regenerateStory(storyId, { target: clip.target, note })
    onChange()
  }

  return (
    <RegenerateControl
      disabled={disabled || !clip.target}
      onRegenerate={regenerate}
      estimateChip={clip.blocked ? null : <EstimateChip estimate={estimate} />}
      actionLabel="Re-animate with note"
    />
  )
}

/**
 * One shot's clip (tier >= 2 only, never rendered at tier 1 -- RC: a tier-1
 * story's episode page keeps looking exactly as it did before phase 6): the
 * clip itself (fetched as a blob with the same helper the shot image uses,
 * DEC-113), a state badge, a route badge, "Animate"/"Keep still" pins
 * (`PATCH .../assets`'s per-shot flags) and the note-driven re-animate above,
 * each disabled with the server's own sentence shown as visible text (the F8
 * pattern -- a `title` alone is invisible on a phone) rather than only a
 * `title`. `keep_native_audio` only at tier 3.
 */
function ShotClipBlock({ storyId, ep, shotId, clip, tier, busy, onChange }) {
  const [url, setUrl] = useState(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [actionBusy, setActionBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const urlRef = useRef(null)

  useEffect(() => {
    setUrl(null)
    setLoadFailed(false)
    if (!clip.url) return undefined
    let cancelled = false
    fetchEpisodeClipUrl(storyId, ep, clip.name).then((fresh) => {
      if (cancelled) { URL.revokeObjectURL(fresh); return }
      urlRef.current = fresh
      setUrl(fresh)
    }).catch(() => { if (!cancelled) setLoadFailed(true) })
    return () => {
      cancelled = true
      if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = null }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId, ep, clip.url, clip.name])

  // Each pin clears the other, so the two read as one exclusive choice in
  // the UI even though they are two independent override fields in
  // assets.json (video_plan.effective_shot_flags resolves them). Each call
  // site sends its own literal shot item (rather than through one shared
  // helper taking a variable) so the payload-contract guard
  // (tests/test_story_payload_contract_episode.py) can read every key this
  // page sends as text, the same way every other patchEpisodeAssets call in
  // this file already does.
  //
  // Browser-check finding F3: each button shows and toggles the EFFECTIVE
  // flag (`clip.flags`, which already folds the storyboard's own keep_still
  // -- video_plan.effective_shot_flags); a second press of an already-
  // pressed button clears the override (null) instead of re-sending it.
  const toggleAnimate = async () => {
    setActionBusy(true)
    setActionError('')
    try {
      if (clip.flags.animate) {
        await patchEpisodeAssets(storyId, ep, [{ shot_id: shotId, animate: null }])
      } else {
        await patchEpisodeAssets(storyId, ep, [{ shot_id: shotId, animate: true, keep_still: null }])
      }
      onChange()
    } catch (err) {
      setActionError(err.message)
    } finally {
      setActionBusy(false)
    }
  }

  const toggleKeepStill = async () => {
    setActionBusy(true)
    setActionError('')
    try {
      if (clip.flags.keep_still) {
        await patchEpisodeAssets(storyId, ep, [{ shot_id: shotId, keep_still: null }])
      } else {
        await patchEpisodeAssets(storyId, ep, [{ shot_id: shotId, keep_still: true, animate: null }])
      }
      onChange()
    } catch (err) {
      setActionError(err.message)
    } finally {
      setActionBusy(false)
    }
  }

  const toggleNativeAudio = async (value) => {
    setActionBusy(true)
    setActionError('')
    try {
      await patchEpisodeAssets(storyId, ep, [{ shot_id: shotId, keep_native_audio: value }])
      onChange()
    } catch (err) {
      setActionError(err.message)
    } finally {
      setActionBusy(false)
    }
  }

  // Browser-check finding F1: the "still generating" wording also doubles as
  // the status line under the badges (so it is never shown only inside a
  // title); a failed or stale clip's own reason (the provider's failure
  // text, e.g. "HTTP 500 from fal…") was never rendered anywhere before this.
  const isGenerating = Boolean(clip.continue)
  const clipStatusReason = isGenerating
    ? 'Still generating — press Continue on the assets step.'
    : (clip.state === 'failed' || clip.state === 'stale') ? clip.reason : null
  // The F8 pattern (phase 5 stage 13b): the server's own regenerate-refusal
  // sentence, shown as text next to the disabled control, never only inside
  // its title. In the "generating" case this is identical to the status
  // line above (the backend's own ``blocked`` wraps the same held request),
  // so it is not repeated a second time.
  const regenerateReason = isGenerating ? clipStatusReason : clip.blocked

  return (
    <div className="story-shot-asset">
      <div className="story-shot-asset-media">
        {url ? (
          <video className="story-shot-asset-image" src={url} playsInline muted controls />
        ) : (
          <span className="story-shot-asset-placeholder" aria-hidden="true">
            {loadFailed ? 'Failed to load' : clip.url ? '' : 'No clip yet'}
          </span>
        )}
      </div>
      <div className="story-shot-asset-meta">
        <ClipStateBadge clip={clip} />
        {clip.route && <RouteChip routeClass={clip.route} link={clip.link} />}
      </div>
      {clipStatusReason && <p className="form-hint">{clipStatusReason}</p>}
      <div className="story-shot-asset-meta">
        <button
          type="button"
          className={`btn btn-sm ${clip.flags.animate ? 'btn-primary' : 'btn-secondary'}`}
          onClick={toggleAnimate}
          disabled={busy || actionBusy}
          aria-pressed={Boolean(clip.flags.animate)}
        >
          {clip.flags.animate ? '✓ Animate' : 'Animate'}
        </button>
        <button
          type="button"
          className={`btn btn-sm ${clip.flags.keep_still ? 'btn-primary' : 'btn-secondary'}`}
          onClick={toggleKeepStill}
          disabled={busy || actionBusy}
          aria-pressed={Boolean(clip.flags.keep_still)}
        >
          {clip.flags.keep_still ? '✓ Keep still' : 'Keep still'}
        </button>
      </div>
      {tier === 3 && (
        <label className="story-checkbox">
          <input
            type="checkbox"
            checked={Boolean(clip.flags.keep_native_audio)}
            onChange={(e) => toggleNativeAudio(e.target.checked)}
            disabled={busy || actionBusy}
          />
          Keep native audio
        </label>
      )}
      {tier === 3 && NO_AUDIO_LINK_RE.test(clip.link || '') && (
        <p className="form-hint">seedance and kling clips have no sound; the line is kept</p>
      )}
      <StepError message={actionError} />
      <ClipRegenerate storyId={storyId} ep={ep} clip={clip} disabled={busy || actionBusy || Boolean(regenerateReason)} onChange={onChange} />
      {!isGenerating && regenerateReason && <p className="form-hint">{regenerateReason}</p>}
    </div>
  )
}

export { clipStatusLabel, clipStateTone }
export default ShotClipBlock

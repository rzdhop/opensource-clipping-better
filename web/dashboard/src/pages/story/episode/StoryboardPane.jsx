// The storyboard pane (stage 11): before a storyboard, Fast/Plan actions; with
// one, shots grouped by scene (framing, motion, modifiers, subjects, action,
// prompt accordion, reference thumbnails, per-shot regenerate), transitions
// between scenes, and Approve storyboard. Mirrors ScriptPane.jsx's shape and
// conventions (spec 10).

import { useEffect, useRef, useState } from 'react'
import {
  runStoryStep, approveStoryDoc, regenerateStory, fetchStoryEstimate,
  patchEpisodeStoryboard, patchEpisodeAssets, patchEpisodeAssetsLinks,
  fetchStoryMediaUrl, fetchShotImageUrl, fetchEpisodeClipUrl,
} from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import RouteChip from '../../../components/RouteChip'
import { EditableText, RegenerateControl, StepError } from '../fields'

// GET /estimate/assets's est_usd/its `voices`/`images` sub-estimates and the
// fast-track split both carry sub-cent amounts; same formatting as
// ScriptPane.jsx's fmtUsd (duplicated so this file stays independently
// readable -- the two panes share no component module).
function fmtUsd(value) {
  const amount = Number(value) || 0
  return amount === 0 ? '0.00' : amount.toFixed(3)
}

// clipping.aistory.schemas closed lists, verbatim (tests/test_story_payload_contract_episode.py).
const FRAMINGS = [
  'wide_establishing', 'medium_single', 'medium_two_shot', 'close_up',
  'extreme_close_up', 'over_shoulder', 'low_angle', 'high_angle', 'insert_prop',
]
const CAMERA_MOTIONS = ['hold', 'push_in', 'pull_out', 'pan_lr', 'pan_rl', 'pan_ud', 'pan_du']
const MODIFIERS = ['handheld', 'jitter_stopmotion']
const TRANSITIONS = ['cut', 'dissolve', 'fadeblack', 'fadewhite', 'wipeleft', 'wiperight', 'slideup']

const SCENE_FUNCTION_LABELS = {
  recap: 'Recap', hook: 'Hook', setup: 'Setup', rising: 'Rising',
  peak: 'Peak', turn: 'Turn', cliffhanger: 'Cliffhanger',
}

// The tag grammar of clipping.aistory.schemas.SUBJECT_TAG_PATTERN, found
// inside free text (an action) rather than matched whole.
const TAG_IN_TEXT = /@char_[a-z0-9_]+|#place_[a-z0-9_]+:[a-z][a-z0-9_]*|%prop_[a-z0-9_]+/g

// -------------------------------------------------------------- tag <-> name

function buildEntityMaps(characters, places, props) {
  return {
    characters: new Map((characters || []).map((c) => [c.char_id, c])),
    places: new Map((places || []).map((p) => [p.place_id, p])),
    props: new Map((props || []).map((p) => [p.prop_id, p])),
  }
}

/** One tag ("@char_x" / "#place_y:variant" / "%prop_z") -> the entity's name,
 * or the tag itself when the entity is unknown. */
function tagLabel(tag, maps) {
  if (tag.startsWith('@')) {
    const doc = maps.characters.get(tag.slice(1))
    return doc ? doc.name : tag
  }
  if (tag.startsWith('#')) {
    const doc = maps.places.get(tag.slice(1).split(':')[0])
    return doc ? doc.name : tag
  }
  if (tag.startsWith('%')) {
    const doc = maps.props.get(tag.slice(1))
    return doc ? doc.name : tag
  }
  return tag
}

/** An action's tags replaced by their entity names, for reading. */
function actionWithNames(action, maps) {
  return action.replace(TAG_IN_TEXT, (tag) => tagLabel(tag, maps))
}

/** The tags a scene's shots may use (workflow._scene_tags, mirrored). */
function sceneTags(scene) {
  return [
    ...scene.characters.map((cid) => `@${cid}`),
    `#${scene.place_id}:${scene.time_variant}`,
    ...scene.props.map((pid) => `%${pid}`),
  ]
}

// --------------------------------------------------------------- references

/** One shot's reference_images entry ("characters/char_x/refs/portrait.jpg")
 * decoded into the entity_media route's own parts (shots.py's
 * _reference_images always writes exactly "<kind>/<eid>/refs/<name>"); null
 * for anything else, so the caller can fall back to the path as text. */
function parseReferencePath(path) {
  const parts = path.split('/')
  if (parts.length !== 4 || parts[2] !== 'refs') return null
  const [kind, eid, , name] = parts
  if (kind !== 'characters' && kind !== 'places' && kind !== 'props') return null
  return { kind, eid, name }
}

function ReferenceThumb({ storyId, path }) {
  const [url, setUrl] = useState(null)
  const [failed, setFailed] = useState(false)
  const urlRef = useRef(null)
  const parsed = parseReferencePath(path)

  useEffect(() => {
    if (!parsed) return undefined
    let cancelled = false
    fetchStoryMediaUrl(storyId, parsed.kind, parsed.eid, parsed.name).then((fresh) => {
      if (cancelled) { URL.revokeObjectURL(fresh); return }
      urlRef.current = fresh
      setUrl(fresh)
    }).catch(() => { if (!cancelled) setFailed(true) })
    return () => {
      cancelled = true
      if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = null }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId, path])

  if (!parsed || failed) {
    return <span className="story-shot-ref-path" title={path}>{path}</span>
  }
  if (!url) {
    return <span className="story-shot-ref-thumb story-shot-ref-loading" aria-hidden="true" />
  }
  return <img className="story-shot-ref-thumb" src={url} alt="" />
}

// ------------------------------------------------------------ header actions

function StoryboardHeader({ storyId, ep, episode, busy, onChange }) {
  const state = episode.state
  const script = episode.script
  const storyboard = episode.storyboard
  const [estimate, setEstimate] = useState(null)
  const [runningFast, setRunningFast] = useState(false)
  const [runningPlan, setRunningPlan] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  useEffect(() => {
    fetchStoryEstimate(storyId, 'storyboard', { ep }).then(setEstimate).catch(() => setEstimate(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId, ep, state.storyboard, (state.stale_scenes || []).join('|')])

  const scriptComplete = state.script === 'complete' || state.script === 'approved'
  const hasStoryboard = state.storyboard !== 'none'

  const reason = busy ? 'A step is running.'
    : !scriptComplete ? 'Finish the script first.'
    : null

  const plannedIds = new Set(storyboard ? Object.keys(storyboard.scenes) : [])
  const unplanned = script ? script.scenes.filter((scene) => !plannedIds.has(scene.scene_id))
    .map((scene) => scene.scene_id) : []
  const toRedo = [...new Set([...unplanned, ...(state.stale_scenes || [])])]

  const runFast = async () => {
    setRunningFast(true)
    setError('')
    setErrors(null)
    try {
      const fastParams = { fast: true }
      await runStoryStep(storyId, 'storyboard', { ep, params: fastParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunningFast(false)
    }
  }

  const runPlan = async () => {
    setRunningPlan(true)
    setError('')
    setErrors(null)
    try {
      const planParams = {}
      await runStoryStep(storyId, 'storyboard', { ep, params: planParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunningPlan(false)
    }
  }

  return (
    <div className="card episode-storyboard-header">
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-secondary"
          onClick={runFast}
          disabled={Boolean(reason) || runningFast || runningPlan}
          title={reason || undefined}
        >
          {runningFast ? <><span className="spinner"></span> Building…</> : hasStoryboard ? 'Rebuild fast' : 'Fast (no calls)'}
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={runPlan}
          disabled={Boolean(reason) || runningFast || runningPlan}
          title={reason || undefined}
        >
          {runningPlan ? <><span className="spinner"></span> Planning…</> : hasStoryboard ? 'Plan remaining with T1' : 'Plan shots'}
        </button>
        <EstimateChip estimate={estimate} />
        {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      </div>
      {reason && <p className="form-hint">{reason}</p>}
      {hasStoryboard && !reason && (
        <p className="form-hint">
          {toRedo.length === 0
            ? 'Every scene is planned and current.'
            : `Rebuild fast redoes every scene; Plan remaining with T1 redoes scene${toRedo.length === 1 ? '' : 's'} ${toRedo.join(', ')}.`}
        </p>
      )}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// --------------------------------------------------------------------- banners

function StoryboardBanners({ storyId, ep, episode, busy, onChange }) {
  const state = episode.state
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const staleScenes = state.stale_scenes || []
  if (staleScenes.length === 0 && !state.prompts_outdated) return null

  const refresh = async () => {
    setRefreshing(true)
    setError('')
    setErrors(null)
    try {
      await patchEpisodeStoryboard(storyId, ep, { refresh_prompts: true })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className="card story-storyboard-banners">
      {staleScenes.length > 0 && (
        <p className="chip chip-warn story-storyboard-banner">
          The script changed: re-plan scene{staleScenes.length === 1 ? '' : 's'} {staleScenes.join(', ')}.
        </p>
      )}
      {state.prompts_outdated && (
        <div className="story-storyboard-banner">
          <span className="chip chip-warn">Some prompts are outdated</span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={refresh} disabled={busy || refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh prompts'}
          </button>
        </div>
      )}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// ------------------------------------------------------------------- consistency

/** "base" (drawn without references, by design) is not worth a chip; a
 * degraded "prompt_only" shot gets the warn styling used everywhere else for
 * a compromise the user should notice (phase-2's ConsistencyChip, mirrored). */
function ConsistencyChip({ consistency }) {
  if (consistency !== 'prompt_only') return null
  return <span className="chip chip-warn">consistency: prompt-only</span>
}

// --------------------------------------------------------------- shot image

// clipping.aistory.steps.assets.shot_state, verbatim (tests/test_story_payload_contract_episode.py).
// "stale" reads as "needs a new image" (plan 11 stage 11): a framing, action
// or prompt edit is what moves a shot here (assets.outdated_images), and
// that is also the render refusal's own reason for the shot
// (render.require_renderable) -- the label says what to do about it, not
// just that it changed. A locked shot never reaches plain "stale" (assets.
// shot_state: locked and outdated is always "locked_stale"), so its label
// stays distinct -- no actionable "needs a new image" control shows there
// either (RegenerateControl is disabled with its own "Unlock first" hint).
const SHOT_STATE_LABELS = {
  none: 'no image', current: 'current', stale: 'needs a new image', locked_stale: 'locked · stale', failed: 'failed',
}

function ShotStateBadge({ state }) {
  const warn = state === 'stale' || state === 'locked_stale' || state === 'failed'
  return (
    <span className={`chip${warn ? ' chip-warn' : state === 'current' ? ' chip-accent' : ''}`}>
      {SHOT_STATE_LABELS[state] || state}
    </span>
  )
}

/**
 * One shot's generated image and its assets controls: the blob image (kept
 * only while this shot's `image_name` is unchanged -- revoked on unmount and
 * on a fresh generation), a state badge, the route it was made on, a
 * "prompt-only" consistency label, a lock toggle (PATCH; only an imaged shot
 * may be locked) and the image's own regenerate-with-note. `assetShot` is
 * one entry of `episode.assets.shots` (null before the episode has a
 * storyboard -- the caller only renders this once one exists).
 */
function ShotImageBlock({ storyId, ep, assetShot, planConsistency, assetsBlocked, busy, onChange }) {
  const [url, setUrl] = useState(null)
  const [loadFailed, setLoadFailed] = useState(false)
  const [lockSaving, setLockSaving] = useState(false)
  const [lockError, setLockError] = useState('')
  const urlRef = useRef(null)

  useEffect(() => {
    setUrl(null)
    setLoadFailed(false)
    if (!assetShot.image_name) return undefined
    let cancelled = false
    fetchShotImageUrl(storyId, ep, assetShot.image_name).then((fresh) => {
      if (cancelled) { URL.revokeObjectURL(fresh); return }
      urlRef.current = fresh
      setUrl(fresh)
    }).catch(() => { if (!cancelled) setLoadFailed(true) })
    return () => {
      cancelled = true
      if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = null }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId, ep, assetShot.image_name])

  const toggleLock = async () => {
    setLockSaving(true)
    setLockError('')
    try {
      await patchEpisodeAssets(storyId, ep, [{ shot_id: assetShot.shot_id, locked: !assetShot.locked }])
      onChange()
    } catch (err) {
      setLockError(err.message)
    } finally {
      setLockSaving(false)
    }
  }

  const regenerateImage = async (note) => {
    await regenerateStory(storyId, { target: `shot:${ep}:${assetShot.shot_id}`, note })
    onChange()
  }

  return (
    <div className="story-shot-asset">
      <div className="story-shot-asset-media">
        {url ? (
          <img className="story-shot-asset-image" src={url} alt="" />
        ) : (
          <span className="story-shot-asset-placeholder" aria-hidden="true">
            {loadFailed ? 'Failed to load' : assetShot.image_name ? '' : 'No image yet'}
          </span>
        )}
      </div>
      <div className="story-shot-asset-meta">
        <ShotStateBadge state={assetShot.state} />
        {assetShot.route && <RouteChip routeClass={assetShot.route} />}
        {/* The card header already labels the planned consistency; repeat it here
            only when the image was drawn under a different one. */}
        {assetShot.consistency !== planConsistency && <ConsistencyChip consistency={assetShot.consistency} />}
        {assetShot.pending && <span className="chip" title="A regenerate is queued for this shot">pending…</span>}
      </div>
      <label className="story-checkbox">
        <input
          type="checkbox"
          checked={assetShot.locked}
          onChange={toggleLock}
          disabled={busy || lockSaving || !assetShot.image_name}
        />
        Lock this image
      </label>
      <StepError message={lockError} />
      <RegenerateControl
        disabled={busy || Boolean(assetShot.locked) || Boolean(assetsBlocked)}
        onRegenerate={regenerateImage}
        empty={!assetShot.image_name}
        label="image"
      />
      {assetShot.locked && <p className="form-hint">Unlock first to regenerate this shot's image.</p>}
    </div>
  )
}

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
  const warn = clip.state === 'stale' || clip.state === 'failed'
  return (
    <span className={`chip${warn ? ' chip-warn' : clip.state === 'current' ? ' chip-accent' : ''}`}>
      {clipStatusLabel(clip)}
    </span>
  )
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

// ------------------------------------------------------------------------- shot

function ShotCard({ storyId, ep, shot, assetShot, tier, scene, maps, assetsBlocked, busy, onChange }) {
  const [fieldError, setFieldError] = useState('')
  const [fieldErrors, setFieldErrors] = useState(null)
  const [actionEditing, setActionEditing] = useState(false)
  const [actionDraft, setActionDraft] = useState(shot.action)
  const [actionSaving, setActionSaving] = useState(false)
  const [actionError, setActionError] = useState('')
  const [actionErrors, setActionErrors] = useState(null)

  const saveFraming = async (value) => {
    setFieldError('')
    setFieldErrors(null)
    try {
      await patchEpisodeStoryboard(storyId, ep, { shots: [{ shot_id: shot.shot_id, framing: value }] })
      onChange()
    } catch (err) {
      setFieldError(err.message)
      setFieldErrors(err.errors || null)
    }
  }

  const saveCameraMotion = async (value) => {
    setFieldError('')
    setFieldErrors(null)
    try {
      await patchEpisodeStoryboard(storyId, ep, { shots: [{ shot_id: shot.shot_id, camera_motion: value }] })
      onChange()
    } catch (err) {
      setFieldError(err.message)
      setFieldErrors(err.errors || null)
    }
  }

  const saveModifiers = async (value) => {
    setFieldError('')
    setFieldErrors(null)
    try {
      await patchEpisodeStoryboard(storyId, ep, { shots: [{ shot_id: shot.shot_id, modifiers: value }] })
      onChange()
    } catch (err) {
      setFieldError(err.message)
      setFieldErrors(err.errors || null)
    }
  }

  const saveKeepStill = async (value) => {
    setFieldError('')
    setFieldErrors(null)
    try {
      await patchEpisodeStoryboard(storyId, ep, { shots: [{ shot_id: shot.shot_id, keep_still: value }] })
      onChange()
    } catch (err) {
      setFieldError(err.message)
      setFieldErrors(err.errors || null)
    }
  }

  const saveAction = async () => {
    setActionSaving(true)
    setActionError('')
    setActionErrors(null)
    try {
      await patchEpisodeStoryboard(storyId, ep, { shots: [{ shot_id: shot.shot_id, action: actionDraft.trim() }] })
      setActionEditing(false)
      onChange()
    } catch (err) {
      setActionError(err.message)
      setActionErrors(err.errors || null)
    } finally {
      setActionSaving(false)
    }
  }

  const savePromptOverride = async (value) => {
    await patchEpisodeStoryboard(storyId, ep, { shots: [{ shot_id: shot.shot_id, prompt_override: value }] })
    onChange()
  }

  const toggleModifier = (mod) => {
    const next = shot.modifiers.includes(mod)
      ? shot.modifiers.filter((m) => m !== mod)
      : [...shot.modifiers, mod]
    saveModifiers(next)
  }

  const regenerate = async (note) => {
    await regenerateStory(storyId, { target: `shot:${ep}:${shot.shot_id}:plan`, note })
    onChange()
  }

  const tags = sceneTags(scene)
  const tagHint = tags.map((tag) => `${tag} (${tagLabel(tag, maps)})`).join(', ')

  return (
    <div className="card story-storyboard-shot" id={`shot-${shot.shot_id}`}>
      <div className="story-storyboard-shot-header">
        <span className="story-script-scene-id">{shot.shot_id}</span>
        <span className="chip">#{shot.order} · {shot.duration_s.toFixed(1)} s</span>
        <ConsistencyChip consistency={shot.consistency} />
      </div>

      {assetShot && (
        <ShotImageBlock storyId={storyId} ep={ep} assetShot={assetShot} planConsistency={shot.consistency}
          assetsBlocked={assetsBlocked} busy={busy} onChange={onChange} />
      )}

      {tier >= 2 && assetShot && assetShot.clip && (
        <ShotClipBlock storyId={storyId} ep={ep} shotId={shot.shot_id} clip={assetShot.clip} tier={tier}
          busy={busy} onChange={onChange} />
      )}

      <div className="story-shot-controls">
        <div className="form-group story-shot-control">
          <label className="form-label">Framing</label>
          <select
            className="form-select"
            value={shot.framing}
            onChange={(e) => saveFraming(e.target.value)}
            disabled={busy}
          >
            {FRAMINGS.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
        </div>
        <div className="form-group story-shot-control">
          <label className="form-label">Camera motion</label>
          <select
            className="form-select"
            value={shot.camera_motion}
            onChange={(e) => saveCameraMotion(e.target.value)}
            disabled={busy}
          >
            {CAMERA_MOTIONS.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
      </div>

      <div className="story-shot-modifiers">
        {MODIFIERS.map((mod) => (
          <label key={mod} className="story-checkbox">
            <input
              type="checkbox"
              checked={shot.modifiers.includes(mod)}
              onChange={() => toggleModifier(mod)}
              disabled={busy}
            />
            {mod}
          </label>
        ))}
        {tier < 2 && (
          <label className="story-checkbox">
            <input
              type="checkbox"
              checked={shot.keep_still}
              onChange={(e) => saveKeepStill(e.target.checked)}
              disabled={busy}
            />
            Keep still
          </label>
        )}
      </div>

      <StepError message={fieldError} errors={fieldErrors} />

      {shot.subject_tags.length > 0 && (
        <div className="story-shot-subjects">
          {shot.subject_tags.map((tag) => <span key={tag} className="chip" title={tag}>{tagLabel(tag, maps)}</span>)}
        </div>
      )}

      <div className="story-field">
        <div className="story-field-label">Action</div>
        {!actionEditing ? (
          <>
            <p className="story-field-value">{actionWithNames(shot.action, maps)}</p>
            {!busy && (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => { setActionDraft(shot.action); setActionError(''); setActionErrors(null); setActionEditing(true) }}
              >
                Edit
              </button>
            )}
          </>
        ) : (
          <>
            <textarea
              className="form-input"
              rows={2}
              value={actionDraft}
              onChange={(e) => setActionDraft(e.target.value)}
            />
            <p className="form-hint">Tags: {tagHint}</p>
            <StepError message={actionError} errors={actionErrors} />
            <div className="story-field-actions">
              <button type="button" className="btn btn-primary btn-sm" onClick={saveAction} disabled={actionSaving}>
                {actionSaving ? 'Saving…' : 'Save'}
              </button>
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                onClick={() => setActionEditing(false)}
                disabled={actionSaving}
              >
                Cancel
              </button>
            </div>
          </>
        )}
      </div>

      <details className="story-shot-prompt">
        <summary>Prompt</summary>
        <div className="story-field">
          <div className="story-field-label">Image prompt</div>
          <p className="story-field-value">{shot.image_prompt}</p>
        </div>
        <div className="story-field">
          <div className="story-field-label">Negative prompt</div>
          <p className="story-field-value">{shot.negative_prompt}</p>
        </div>
        {shot.prompt_layout && <LayeredPromptDetails shot={shot} />}
        <EditableText
          label="Prompt override"
          value={shot.prompt_override}
          onSave={savePromptOverride}
          disabled={busy}
          rows={2}
        />
      </details>

      {shot.reference_images.length > 0 && (
        <div className="story-shot-refs">
          {shot.reference_images.map((path, i) => (
            <ReferenceThumb key={`${path}-${i}`} storyId={storyId} path={path} />
          ))}
        </div>
      )}

      <RegenerateControl disabled={busy} onRegenerate={regenerate} />
    </div>
  )
}

// A v2 shot (phase 7 stage 3b: `prompt_layout` set) is resolved into a layered
// image prompt that opens on what each reference image is for, and carries its
// clip prompt in `video_prompt`. The word targets are clipping.aistory.prompting's
// KEYFRAME_V2_MAX_WORDS and CLIP_V2_MAX_WORDS.
const IMAGE_PROMPT_TARGET_WORDS = 220
const CLIP_PROMPT_TARGET_WORDS = 80
const ROLE_SENTENCE = /Image \d+ is [^.]*\./g

function wordCount(text) {
  return (text || '').split(/\s+/).filter(Boolean).length
}

function WordCount({ label, text, target }) {
  const count = wordCount(text)
  return (
    <span className={count > target ? 'chip chip-warn' : 'chip'}>
      {label}: {count} / {target} words
    </span>
  )
}

function LayeredPromptDetails({ shot }) {
  const roles = shot.image_prompt.match(ROLE_SENTENCE) || []
  return (
    <>
      <div className="story-field">
        <div className="story-field-label">Clip prompt</div>
        <p className="story-field-value">{shot.video_prompt || '—'}</p>
      </div>
      <div className="story-field">
        <div className="story-field-label">Reference roles</div>
        {roles.length > 0 ? (
          <ol className="story-field-value">
            {roles.map((role) => <li key={role}>{role}</li>)}
          </ol>
        ) : (
          <p className="story-field-value">No reference image is sent (prompt-only mode).</p>
        )}
      </div>
      <div className="story-field">
        <WordCount label="Image prompt" text={shot.image_prompt} target={IMAGE_PROMPT_TARGET_WORDS} />{' '}
        <WordCount label="Clip prompt" text={shot.video_prompt} target={CLIP_PROMPT_TARGET_WORDS} />
      </div>
    </>
  )
}

// -------------------------------------------------------------------- transition

function TransitionCut() {
  return <p className="story-storyboard-transition story-storyboard-transition-cut">cut</p>
}

function TransitionSelect({ storyId, ep, transition, busy, onChange }) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const save = async (value) => {
    setSaving(true)
    setError('')
    setErrors(null)
    try {
      await patchEpisodeStoryboard(storyId, ep, { transitions: [{ after: transition.after, type: value }] })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="story-storyboard-transition story-storyboard-transition-select">
      <select
        className="form-select"
        value={transition.type}
        onChange={(e) => save(e.target.value)}
        disabled={busy || saving}
      >
        {TRANSITIONS.map((t) => <option key={t} value={t}>{t}</option>)}
      </select>
      <StepError message={error} errors={errors} />
    </div>
  )
}

// ------------------------------------------------------------------------- scene

function SceneHeader({ scene, boardEntry, places }) {
  const place = places.find((p) => p.place_id === scene.place_id)
  return (
    <div className="story-storyboard-scene-header">
      <span className="story-script-scene-id">{scene.scene_id}</span>
      <span className="chip">{SCENE_FUNCTION_LABELS[scene.function] || scene.function}</span>
      <span className="chip">
        {place ? place.name : scene.place_id}{scene.time_variant ? ` · ${scene.time_variant}` : ''}
      </span>
      {boardEntry && (
        <span className="chip" title="How these shots were planned">{boardEntry.source === 't1' ? 'T1' : 'fast'}</span>
      )}
      {boardEntry && boardEntry.stale && <span className="chip chip-warn">stale</span>}
    </div>
  )
}

/** shots.js's board.shots, ordered and grouped by consecutive scene_id --
 * consecutive because a valid storyboard's order follows the script's scene
 * sequence (episode_common.covers); a shot never appears out of its scene's
 * run. */
function buildShotGroups(storyboard, scenesById) {
  const shotsSorted = [...storyboard.shots].sort((a, b) => a.order - b.order)
  const groups = []
  for (const shot of shotsSorted) {
    const last = groups[groups.length - 1]
    if (last && last.sceneId === shot.scene_id) {
      last.shots.push(shot)
    } else {
      groups.push({ sceneId: shot.scene_id, scene: scenesById[shot.scene_id] || null, shots: [shot] })
    }
  }
  return groups
}

// --------------------------------------------------------------------------- approve

function ApproveStoryboard({ storyId, ep, episode, busy, onChange }) {
  const [approving, setApproving] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const state = episode.state
  const storyboard = episode.storyboard
  const approved = Boolean(storyboard && storyboard.approved_at)

  const blockReason = () => {
    if (busy) return 'A step is running.'
    if (state.script !== 'approved') return 'Approve the script first.'
    if ((state.stale_scenes || []).length > 0) return 'Some scenes changed since they were planned: plan them again.'
    if (state.storyboard === 'none' || state.storyboard === 'partial') {
      return 'Not every scene has shots yet: plan the missing scenes.'
    }
    if (state.prompts_outdated) return 'Some prompts are outdated: refresh them.'
    return null
  }
  const reason = approved ? null : blockReason()

  const handleApprove = async () => {
    setApproving(true)
    setError('')
    setErrors(null)
    try {
      await approveStoryDoc(storyId, `storyboard:${ep}`)
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  return (
    <div className="card story-storyboard-approve">
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleApprove}
          disabled={approved || Boolean(reason) || approving}
          title={reason || undefined}
        >
          {approving ? 'Approving…' : approved ? 'Approved' : 'Approve storyboard'}
        </button>
        {reason && <span className="form-hint">{reason}</span>}
      </div>
      {approved && <p className="form-hint">Approved {new Date(storyboard.approved_at).toLocaleString()}.</p>}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// ----------------------------------------------------------------------- assets

/**
 * "Generate assets": the shot images and line voices the assets step would
 * still make, with its estimate and route (clipping.aistory.steps.assets.
 * asset_units, via GET /estimate/assets), and the "align words" opt-in
 * (DEC-165: forced alignment for a line whose voice timed no words). The
 * estimate's own shape -- `images`/`voices`/`alignment`/`est_usd` -- does not
 * fit EstimateChip's `units`, so this renders its own compact line (same
 * reasoning as ScriptPane.jsx's MeasureVoices).
 */
function AssetsHeader({ storyId, ep, episode, busy, onChange }) {
  const storyboard = episode.storyboard
  const storyboardApproved = Boolean(storyboard && storyboard.approved_at)
  const hasAssets = Boolean(episode.assets && episode.assets.doc)
  const tier = episode.assets ? episode.assets.tier : 1
  const [alignWords, setAlignWords] = useState(false)
  // Phase 6 stage 8's own default (assets.animate_param): on, so a tier >= 2
  // run makes the clips right after the images and voices unless turned off
  // here (stage 12's own toggle, test_story_defaults.py pins this default).
  const [animate, setAnimate] = useState(true)
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  // assets.require_approved needs the script AND the storyboard approved
  // and current (stage 7): a text-only edit clears only the script's
  // approval while the storyboard's own stays, so gating this fetch on
  // storyboardApproved alone still lets it 409 -- shown here and the button
  // disabled, rather than an estimate that stays null forever (stage-10
  // lesson, browser-check finding).
  const [estimateError, setEstimateError] = useState('')

  useEffect(() => {
    setEstimate(null)
    setEstimateError('')
    if (!storyboardApproved) return
    fetchStoryEstimate(storyId, 'assets', { ep, alignWords })
      .then((data) => { setEstimate(data); setEstimateError('') })
      .catch((err) => { setEstimate(null); setEstimateError(err.message) })
  }, [storyId, ep, storyboardApproved, alignWords])

  const reason = busy ? 'A step is running.' : !storyboardApproved ? 'Approve the storyboard first.' : null

  const handleRun = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      const assetsParams = { align_words: alignWords, animate }
      await runStoryStep(storyId, 'assets', { ep, params: assetsParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="card story-assets-header">
      <h4 className="card-title">Assets</h4>
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleRun}
          disabled={Boolean(reason) || Boolean(estimateError) || running}
          title={reason || estimateError || undefined}
        >
          {running ? <><span className="spinner"></span> Generating…</> : hasAssets ? 'Generate remaining assets' : 'Generate assets'}
        </button>
        {estimateError ? (
          <span className="chip chip-warn chip-wrap">{estimateError}</span>
        ) : estimate && (
          <>
            {/* Browser-check finding F4: the clip count used to be folded into this
                same "est. $x" chip, which read as if the clips were included in that
                total (``asset_units``'s own ``est_usd`` leaves them out whenever
                ``video.ready`` is false -- allow_paid off, over a cap, ...). The
                video part now gets its own chip with its own ready/cost reading. */}
            <span className="chip" title={estimate.message || ''}>
              est. ${fmtUsd(estimate.est_usd)} · {estimate.images.count} image{estimate.images.count === 1 ? '' : 's'}
              {' · '}{estimate.voices.lines} line{estimate.voices.lines === 1 ? '' : 's'}
            </span>
            {/* A RouteChip with no route_class (nothing left to route: every shot
                already has its image) used to render as a bare "unknown" chip. */}
            {estimate.images.route_class && (
              <RouteChip routeClass={estimate.images.route_class} link={estimate.images.link} />
            )}
            {estimate.video && estimate.video.count != null && (
              <span className={`chip${estimate.video.ready ? '' : ' chip-warn'}`} title={estimate.video.message || ''}>
                {estimate.video.count} clip{estimate.video.count === 1 ? '' : 's'}
                {' '}
                {estimate.video.ready
                  ? (estimate.video.route_class === 'local' ? '(local)' : `($${fmtUsd(estimate.video.est_usd)})`)
                  : `(est $${fmtUsd(estimate.video.est_usd)}, not now)`}
              </span>
            )}
          </>
        )}
      </div>
      <label className="story-checkbox">
        <input
          type="checkbox"
          checked={alignWords}
          onChange={(e) => setAlignWords(e.target.checked)}
          disabled={busy || running}
        />
        Align words (forced alignment for lines the voice timed no words for)
      </label>
      {estimate && !estimateError && alignWords && estimate.alignment.requests > 0 && (
        <p className="form-hint">
          {estimate.alignment.requests} line{estimate.alignment.requests === 1 ? '' : 's'} would be aligned.
        </p>
      )}
      {tier >= 2 && (
        <>
          <label className="story-checkbox">
            <input
              type="checkbox"
              checked={animate}
              onChange={(e) => setAnimate(e.target.checked)}
              disabled={busy || running}
            />
            Animate (make the clips right after the images and voices)
          </label>
          {!hasAssets && (
            <p className="form-hint">
              Make the keyframes first (animate off), then animate — the clip lengths follow the measured voices.
            </p>
          )}
        </>
      )}
      {reason && <p className="form-hint">{reason}</p>}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

/**
 * The sticky image-link offer (phase 6 stage 12 follow-up, A-087): from
 * `episode.assets.image_offer` -- already computed server-side
 * (`workflow.episode_clips`'s `image_offer`, merged into the page), so this
 * reads it directly rather than running its own estimate fetch. Unlike the
 * video offer (`VideoPhaseHeader`, tier >= 2 only), this applies at **any**
 * tier -- image generation exists from tier 1 -- so it is never gated on
 * tier, and never rendered inside a clip control's own gate. Same shape and
 * same confirmation pattern as the video offer: `offer.message` names what
 * a switch redoes and at what price, `offer.switch` is sent as is through
 * `patchEpisodeAssetsLinks`.
 */
function ImageOfferBanner({ storyId, ep, episode, busy, onChange }) {
  const [switching, setSwitching] = useState(false)
  const [switchError, setSwitchError] = useState('')
  const offer = episode.assets && episode.assets.image_offer
  if (!offer) return null

  const handleSwitch = async () => {
    if (!offer.switch) return
    if (!window.confirm(`${offer.message}\n\nSwitch now?`)) return
    setSwitching(true)
    setSwitchError('')
    try {
      await patchEpisodeAssetsLinks(storyId, ep, offer.switch.links)
      onChange()
    } catch (err) {
      setSwitchError(err.message)
    } finally {
      setSwitching(false)
    }
  }

  return (
    <div className="card story-assets-header">
      <h4 className="card-title">Image link</h4>
      <div className="story-storyboard-banner">
        <span className="chip chip-warn chip-wrap">{offer.message}</span>
        {offer.switch && (
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleSwitch}
            disabled={busy || switching}
          >
            {switching ? 'Switching…' : `Switch to ${offer.next_link}`}
          </button>
        )}
        <StepError message={switchError} />
      </div>
    </div>
  )
}

/**
 * The episode's shared video-phase status (phase 6 stage 11/12), from
 * `episode.assets.video` -- already computed server-side
 * (`workflow.episode_clips`, merged into the page), so this reads it
 * directly rather than running its own estimate fetch: the next assets run's
 * clip count/seconds and either the local ETA (`eta_s`/`eta_note`) or the
 * paid cost (`est_usd`), its route/link, and -- when the recorded video link
 * cannot serve -- the sticky offer (`offer`) to switch it, behind a
 * confirmation naming what the switch redoes and at what price
 * (`offer.message`), sending `offer.switch` as the assets PATCH. Null (and
 * nothing rendered) before tier 2 or before a script/storyboard exist.
 */
function VideoPhaseHeader({ storyId, ep, episode, busy, onChange }) {
  const [switching, setSwitching] = useState(false)
  const [switchError, setSwitchError] = useState('')
  const video = episode.assets && episode.assets.video
  if (!video) return null

  const handleSwitch = async () => {
    if (!video.offer || !video.offer.switch) return
    if (!window.confirm(`${video.offer.message}\n\nSwitch now?`)) return
    setSwitching(true)
    setSwitchError('')
    try {
      await patchEpisodeAssetsLinks(storyId, ep, video.offer.switch.links)
      onChange()
    } catch (err) {
      setSwitchError(err.message)
    } finally {
      setSwitching(false)
    }
  }

  return (
    <div className="card story-assets-header">
      <h4 className="card-title">Video</h4>
      <div className="story-step-actions">
        {video.route_class && <RouteChip routeClass={video.route_class} link={video.link} />}
        <span className="chip" title={video.message || ''}>
          {video.count != null ? `${video.count} clip${video.count === 1 ? '' : 's'}` : 'nothing to animate yet'}
          {video.seconds != null ? ` · ${video.seconds.toFixed(1)} s` : ''}
          {video.count != null ? (
            video.route_class === 'local' && video.eta_s != null
              ? ` · ~${Math.round(video.eta_s)} s local${video.eta_note ? ` (${video.eta_note})` : ''}`
              : ` · $${fmtUsd(video.est_usd)}`
          ) : ''}
        </span>
      </div>
      {!video.ready && video.message && <p className="form-hint">{video.message}</p>}
      {video.offer && (
        <div className="story-storyboard-banner">
          <span className="chip chip-warn chip-wrap">{video.offer.message}</span>
          {video.offer.switch && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleSwitch}
              disabled={busy || switching}
            >
              {switching ? 'Switching…' : `Switch to ${video.offer.next_link}`}
            </button>
          )}
          <StepError message={switchError} />
        </div>
      )}
    </div>
  )
}

/** The assets approve button: the fingerprint state (none | current | stale)
 * and, on a refusal, exactly what is still missing -- the server's own
 * sentence (`workflow.approve_assets`) names every shot or line and its
 * regenerate target. */
function ApproveAssets({ storyId, ep, episode, busy, onChange }) {
  const [approving, setApproving] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const assets = episode.assets
  if (!assets || !assets.doc) return null

  const fingerprint = assets.fingerprint
  const approved = fingerprint === 'current'
  const reason = busy ? 'A step is running.' : null

  const handleApprove = async () => {
    setApproving(true)
    setError('')
    setErrors(null)
    try {
      await approveStoryDoc(storyId, `assets:${ep}`)
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  return (
    <div className="card story-assets-approve">
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleApprove}
          disabled={approved || Boolean(reason) || approving}
          title={reason || undefined}
        >
          {approving ? 'Approving…' : approved ? 'Approved' : 'Approve assets'}
        </button>
        <span className={`chip${fingerprint === 'current' ? ' chip-accent' : fingerprint === 'stale' ? ' chip-warn' : ''}`}>
          fingerprint: {fingerprint}
        </span>
        {reason && <span className="form-hint">{reason}</span>}
      </div>
      {assets.approved_at && (
        <p className="form-hint">
          {approved ? 'Approved' : 'Approved previously (now stale)'} {new Date(assets.approved_at).toLocaleString()}.
        </p>
      )}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// --------------------------------------------------------------------------- page

export default function StoryboardPane({ episode, characters, places, props, storyId, ep, inFlightJob, onChange }) {
  const script = episode.script
  const storyboard = episode.storyboard
  const busy = Boolean(inFlightJob)
  const maps = buildEntityMaps(characters, places, props)
  // F8 (phase 5 stage 13b): assets.require_approved's own refusal sentence
  // right now, or null -- single-sourced (workflow.episode_view), so a
  // shot's image regenerate is disabled instead of round-tripping into a
  // 409 that spends nothing.
  const assetsBlocked = episode.state.assets_regenerate_blocked
  const scenesById = script ? Object.fromEntries(script.scenes.map((scene) => [scene.scene_id, scene])) : {}
  const assetsByShotId = episode.assets
    ? Object.fromEntries(episode.assets.shots.map((assetShot) => [assetShot.shot_id, assetShot]))
    : {}
  // 1 unless the page has merged workflow.episode_clips in (phase 6 stage
  // 11, once a script or storyboard exists) -- tier >= 2 gates every clip
  // control; a tier-1 story therefore renders none of them (RC).
  const tier = episode.assets ? episode.assets.tier : 1

  return (
    <div className="story-step-body">
      <StoryboardHeader storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />

      {storyboard && storyboard.shots.length > 0 && script && (
        <>
          <StoryboardBanners storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />

          {assetsBlocked && <p className="form-hint story-storyboard-assets-blocked">{assetsBlocked}</p>}

          <div className="story-storyboard-scenes">
            {buildShotGroups(storyboard, scenesById).map((group, i, groups) => {
              if (!group.scene) return null
              const boardEntry = storyboard.scenes[group.sceneId]
              const isLastGroup = i === groups.length - 1
              const lastShot = group.shots[group.shots.length - 1]
              const boundary = !isLastGroup
                ? storyboard.transitions.find((t) => t.after === lastShot.shot_id)
                : null
              return (
                <div key={group.sceneId} className="story-storyboard-scene" id={`storyboard-scene-${group.sceneId}`}>
                  <SceneHeader scene={group.scene} boardEntry={boardEntry} places={places} />
                  <div className="story-storyboard-shots">
                    {group.shots.map((shot, si) => (
                      <div key={shot.shot_id}>
                        <ShotCard
                          storyId={storyId}
                          ep={ep}
                          shot={shot}
                          assetShot={assetsByShotId[shot.shot_id]}
                          tier={tier}
                          scene={group.scene}
                          maps={maps}
                          assetsBlocked={assetsBlocked}
                          busy={busy}
                          onChange={onChange}
                        />
                        {si < group.shots.length - 1 && <TransitionCut />}
                      </div>
                    ))}
                  </div>
                  {boundary && (
                    <TransitionSelect storyId={storyId} ep={ep} transition={boundary} busy={busy} onChange={onChange} />
                  )}
                </div>
              )
            })}
          </div>

          <ApproveStoryboard storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />

          <AssetsHeader storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />
          <ImageOfferBanner storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />
          <VideoPhaseHeader storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />
          <ApproveAssets storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />
        </>
      )}
    </div>
  )
}

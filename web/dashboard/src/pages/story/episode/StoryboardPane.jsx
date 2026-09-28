// The storyboard pane (stage 11): before a storyboard, Fast/Plan actions; with
// one, shots grouped by scene (framing, motion, modifiers, subjects, action,
// prompt accordion, reference thumbnails, per-shot regenerate), transitions
// between scenes, and Approve storyboard. Mirrors ScriptPane.jsx's shape and
// conventions (spec 10).

import { useEffect, useRef, useState } from 'react'
import {
  runStoryStep, approveStoryDoc, regenerateStory, fetchStoryEstimate,
  patchEpisodeStoryboard, patchEpisodeAssets, fetchStoryMediaUrl, fetchShotImageUrl,
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
const SHOT_STATE_LABELS = {
  none: 'no image', current: 'current', stale: 'stale', locked_stale: 'locked · stale', failed: 'failed',
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
function ShotImageBlock({ storyId, ep, assetShot, busy, onChange }) {
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
        <ConsistencyChip consistency={assetShot.consistency} />
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
        disabled={busy || Boolean(assetShot.locked)}
        onRegenerate={regenerateImage}
        empty={!assetShot.image_name}
        label="image"
      />
      {assetShot.locked && <p className="form-hint">Unlock first to regenerate this shot's image.</p>}
    </div>
  )
}

// ------------------------------------------------------------------------- shot

function ShotCard({ storyId, ep, shot, assetShot, scene, maps, busy, onChange }) {
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
        <ShotImageBlock storyId={storyId} ep={ep} assetShot={assetShot} busy={busy} onChange={onChange} />
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
        <label className="story-checkbox">
          <input
            type="checkbox"
            checked={shot.keep_still}
            onChange={(e) => saveKeepStill(e.target.checked)}
            disabled={busy}
          />
          Keep still
        </label>
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
  const [alignWords, setAlignWords] = useState(false)
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  useEffect(() => {
    if (!storyboardApproved) { setEstimate(null); return }
    fetchStoryEstimate(storyId, 'assets', { ep, alignWords }).then(setEstimate).catch(() => setEstimate(null))
  }, [storyId, ep, storyboardApproved, alignWords])

  const reason = busy ? 'A step is running.' : !storyboardApproved ? 'Approve the storyboard first.' : null

  const handleRun = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      const assetsParams = { align_words: alignWords }
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
          disabled={Boolean(reason) || running}
          title={reason || undefined}
        >
          {running ? <><span className="spinner"></span> Generating…</> : hasAssets ? 'Generate remaining assets' : 'Generate assets'}
        </button>
        {estimate && (
          <span className="chip" title={estimate.message || ''}>
            est. ${fmtUsd(estimate.est_usd)} · {estimate.images.count} image{estimate.images.count === 1 ? '' : 's'}
            {' · '}{estimate.voices.lines} line{estimate.voices.lines === 1 ? '' : 's'}
          </span>
        )}
        {estimate && <RouteChip routeClass={estimate.images.route_class} link={estimate.images.link} />}
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
      {estimate && alignWords && estimate.alignment.requests > 0 && (
        <p className="form-hint">
          {estimate.alignment.requests} line{estimate.alignment.requests === 1 ? '' : 's'} would be aligned.
        </p>
      )}
      {reason && <p className="form-hint">{reason}</p>}
      <StepError message={error} errors={errors} className="story-step-error" />
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
  const scenesById = script ? Object.fromEntries(script.scenes.map((scene) => [scene.scene_id, scene])) : {}
  const assetsByShotId = episode.assets
    ? Object.fromEntries(episode.assets.shots.map((assetShot) => [assetShot.shot_id, assetShot]))
    : {}

  return (
    <div className="story-step-body">
      <StoryboardHeader storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />

      {storyboard && storyboard.shots.length > 0 && script && (
        <>
          <StoryboardBanners storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />

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
                          scene={group.scene}
                          maps={maps}
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
          <ApproveAssets storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />
        </>
      )}
    </div>
  )
}

// One shot of the storyboard (dashboard overhaul stage 4, DEC-256: split out
// of the 1,550-line StoryboardPane.jsx, the code moved as it was): the shot's
// card -- its keyframe and clip blocks, framing, camera motion, modifiers,
// subjects, action, prompt accordion, reference thumbnails and the plan's
// regenerate -- and the scene-boundary transition select. Opened from the
// filmstrip in StoryboardPane.jsx, one shot at a time.

import { useEffect, useRef, useState } from 'react'
import {
  regenerateStory, patchEpisodeStoryboard, patchEpisodeAssets, fetchStoryMediaUrl, fetchShotImageUrl,
} from '../../../../api'
import RouteChip from '../../../../components/RouteChip'
import { EditableText, RegenerateControl, StepError } from '../../fields'
import { Badge, Card, CardBody, CardHeader } from '../../../../ui'
import ShotClipBlock from './ClipControls'

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
  return <Badge tone={shotStateTone(state)}>{SHOT_STATE_LABELS[state] || state}</Badge>
}

/** The kit tone a shot image's state reads in (the filmstrip's dots use it too). */
function shotStateTone(state) {
  if (state === 'current') return 'success'
  if (state === 'failed') return 'danger'
  if (state === 'stale' || state === 'locked_stale') return 'warning'
  return 'neutral'
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
    <Card className="story-storyboard-shot" id={`shot-${shot.shot_id}`}>
      <CardHeader
        title={<span className="story-script-scene-id">{shot.shot_id}</span>}
        subtitle={`Shot ${shot.order} · ${shot.duration_s.toFixed(1)} s`}
        actions={<ConsistencyChip consistency={shot.consistency} />}
      />
      <CardBody className="story-storyboard-shot-body">
      {/* The keyframe and, at tier >= 2, its clip: side by side when there is room. */}
      <div className="story-shot-media-row">
      {assetShot && (
        <ShotImageBlock storyId={storyId} ep={ep} assetShot={assetShot} planConsistency={shot.consistency}
          assetsBlocked={assetsBlocked} busy={busy} onChange={onChange} />
      )}

      {tier >= 2 && assetShot && assetShot.clip && (
        <ShotClipBlock storyId={storyId} ep={ep} shotId={shot.shot_id} clip={assetShot.clip} tier={tier}
          busy={busy} onChange={onChange} />
      )}
      </div>

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
      </CardBody>
    </Card>
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

export {
  FRAMINGS, CAMERA_MOTIONS, MODIFIERS, TRANSITIONS, SCENE_FUNCTION_LABELS, SHOT_STATE_LABELS,
  buildEntityMaps, shotStateTone, TransitionSelect,
}
export default ShotCard

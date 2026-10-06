// The storyboard pane (stage 11; dashboard overhaul stage 4, DEC-256): before
// a storyboard, Fast/Plan actions; with one, a filmstrip of every shot --
// its keyframe, id, duration and state marks (image, keyframe check, clip),
// grouped by scene with the transitions between scenes -- and, under it, the
// one shot it selects opened as its full card (ShotCard.jsx), then Approve
// storyboard and the keyframe and asset cards (AssetsCards.jsx). Mirrors
// ScriptPane.jsx's shape and conventions (spec 10).

import { Fragment, useEffect, useRef, useState } from 'react'
import {
  runStoryStep, approveStoryDoc, fetchStoryEstimate, patchEpisodeStoryboard, fetchShotImageUrl,
} from '../../../../api'
import EstimateChip from '../../../../components/EstimateChip'
import RouteChip from '../../../../components/RouteChip'
import { StepError } from '../../fields'
import { Card, CardBody, CardHeader, IconButton } from '../../../../ui'
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Film, ImageIcon } from '../../../../ui/icons'
import ShotCard, {
  SCENE_FUNCTION_LABELS, SHOT_STATE_LABELS, TransitionSelect, buildEntityMaps, shotStateTone,
} from './ShotCard'
import { clipStatusLabel, clipStateTone } from './ClipControls'
import {
  AssetsHeader, ImageOfferBanner, VideoPhaseHeader, ApproveKeyframes, ApproveAssets,
} from './AssetsCards'

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
          {runningFast ? <><span className="spinner"></span> Building…</> : hasStoryboard ? 'Quick plan again' : 'Quick plan (free)'}
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={runPlan}
          disabled={Boolean(reason) || runningFast || runningPlan}
          title={reason || undefined}
        >
          {runningPlan ? <><span className="spinner"></span> Planning…</> : hasStoryboard ? 'Plan the rest' : 'Plan shots'}
        </button>
        <EstimateChip estimate={estimate} />
        {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      </div>
      {reason && <p className="form-hint">{reason}</p>}
      {hasStoryboard && !reason && (
        <p className="form-hint">
          {toRedo.length === 0
            ? 'Every scene is planned and current.'
            : `Quick plan again redoes every scene; Plan the rest redoes scene${toRedo.length === 1 ? '' : 's'} ${toRedo.join(', ')}.`}
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
        <span className="chip" title="How these shots were planned">{boardEntry.source === 't1' ? 'Planned by the app' : 'Quick plan'}</span>
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


// --------------------------------------------------------------------- filmstrip

/**
 * One shot image as a blob URL for a filmstrip thumbnail (the shot image
 * route needs the auth header, DEC-113), kept while the image name is
 * unchanged and revoked on a change or unmount -- ShotImageBlock's own
 * discipline.
 */
function useShotImageUrl(storyId, ep, imageName) {
  const [url, setUrl] = useState(null)
  useEffect(() => {
    setUrl(null)
    if (!imageName) return undefined
    let cancelled = false
    let current = null
    fetchShotImageUrl(storyId, ep, imageName).then((fresh) => {
      if (cancelled) { URL.revokeObjectURL(fresh); return }
      current = fresh
      setUrl(fresh)
    }).catch(() => {})
    return () => {
      cancelled = true
      if (current) URL.revokeObjectURL(current)
    }
  }, [storyId, ep, imageName])
  return url
}

// The review's per-shot keyframe verdict (workflow.REVIEW_VERDICT_STATES), as
// a filmstrip mark: ReviewPane.jsx's verdictChip says it in full.
const VERDICT_TEXT = {
  passed: 'passed', fixed: 'fixed', flagged: 'still flagged', not_current: 'not current', unchecked: 'not checked',
}

function verdictTone(state) {
  if (state === 'passed' || state === 'fixed') return 'success'
  if (state === 'flagged' || state === 'not_current') return 'warning'
  return 'neutral'
}

function FilmstripThumb({ storyId, ep, shot, assetShot, verdict, tier, selected, onSelect }) {
  const url = useShotImageUrl(storyId, ep, assetShot ? assetShot.image_name : null)
  const clip = tier >= 2 && assetShot ? assetShot.clip : null
  const marks = []
  if (assetShot) {
    marks.push({
      key: 'image', icon: ImageIcon, tone: shotStateTone(assetShot.state),
      text: `image ${SHOT_STATE_LABELS[assetShot.state] || assetShot.state}`,
    })
  }
  if (verdict) {
    const tone = verdictTone(verdict.state)
    marks.push({
      key: 'verdict', icon: tone === 'success' ? Check : AlertTriangle, tone,
      text: `keyframe check ${VERDICT_TEXT[verdict.state] || verdict.state}${verdict.issue ? ` (${verdict.issue})` : ''}`,
    })
  }
  if (clip) marks.push({ key: 'clip', icon: Film, tone: clipStateTone(clip), text: `clip ${clipStatusLabel(clip)}` })
  const label = [`Shot ${shot.shot_id}`, `${shot.duration_s.toFixed(1)} s`, ...marks.map((mark) => mark.text)].join(', ')

  return (
    <button
      type="button"
      className={`story-filmstrip-thumb${selected ? ' story-filmstrip-thumb-selected' : ''}`}
      onClick={() => onSelect(shot.shot_id)}
      aria-pressed={selected}
      aria-label={label}
      title={label}
      data-shot={shot.shot_id}
    >
      <span className="story-filmstrip-image">
        {url ? <img src={url} alt="" /> : (
          <span className="story-filmstrip-empty">{assetShot && assetShot.image_name ? '' : 'No image'}</span>
        )}
        <span className="story-filmstrip-marks" aria-hidden="true">
          {marks.map((mark) => {
            const Icon = mark.icon
            return (
              <span key={mark.key} className={`story-filmstrip-mark story-filmstrip-mark-${mark.tone}`}>
                <Icon size={11} />
              </span>
            )
          })}
        </span>
        <span className="story-filmstrip-duration">{shot.duration_s.toFixed(1)} s</span>
      </span>
      <span className="story-filmstrip-id">{shot.shot_id}</span>
    </button>
  )
}

/**
 * Every shot, in order, as a horizontal strip of thumbnails grouped by scene,
 * the transition between two scenes named where it falls. A click (or the
 * arrow keys, once a thumbnail has focus) selects a shot; the selected one
 * is kept in view inside the strip, which scrolls sideways in its own box.
 */
function Filmstrip({ storyId, ep, groups, transitions, assetsByShotId, verdictsByShotId, tier, selectedId, onSelect }) {
  const stripRef = useRef(null)
  const order = groups.flatMap((group) => group.shots.map((shot) => shot.shot_id))

  useEffect(() => {
    const strip = stripRef.current
    if (!strip || !selectedId) return
    const thumb = strip.querySelector(`[data-shot="${selectedId}"]`)
    if (!thumb) return
    const left = thumb.offsetLeft - strip.offsetLeft
    if (left < strip.scrollLeft || left + thumb.offsetWidth > strip.scrollLeft + strip.clientWidth) {
      strip.scrollLeft = left - (strip.clientWidth - thumb.offsetWidth) / 2
    }
  }, [selectedId])

  const onKeyDown = (event) => {
    if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
    const next = order[order.indexOf(selectedId) + (event.key === 'ArrowRight' ? 1 : -1)]
    if (!next) return
    event.preventDefault()
    onSelect(next)
    window.requestAnimationFrame(() => {
      const thumb = stripRef.current && stripRef.current.querySelector(`[data-shot="${next}"]`)
      if (thumb) thumb.focus()
    })
  }

  return (
    <div className="story-filmstrip" ref={stripRef} role="group" aria-label="Shots" onKeyDown={onKeyDown}>
      {groups.map((group, groupIndex) => {
        const lastShot = group.shots[group.shots.length - 1]
        const boundary = groupIndex < groups.length - 1
          ? transitions.find((transition) => transition.after === lastShot.shot_id)
          : null
        return (
          <Fragment key={group.sceneId}>
            <div className="story-filmstrip-scene">
              <span className="story-filmstrip-scene-label">
                {group.sceneId} · {SCENE_FUNCTION_LABELS[group.scene.function] || group.scene.function}
              </span>
              <div className="story-filmstrip-shots">
                {group.shots.map((shot) => (
                  <FilmstripThumb
                    key={shot.shot_id}
                    storyId={storyId}
                    ep={ep}
                    shot={shot}
                    assetShot={assetsByShotId[shot.shot_id]}
                    verdict={verdictsByShotId[shot.shot_id]}
                    tier={tier}
                    selected={shot.shot_id === selectedId}
                    onSelect={onSelect}
                  />
                ))}
              </div>
            </div>
            {boundary && (
              <span className="story-filmstrip-transition" title={`Transition to the next scene: ${boundary.type}`}>
                {boundary.type}
              </span>
            )}
          </Fragment>
        )
      })}
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
  // The review's per-shot keyframe check (stage C), for the filmstrip's marks;
  // none on a legacy episode.
  const verdictsByShotId = episode.review
    ? Object.fromEntries(episode.review.shots.map((reviewShot) => [reviewShot.shot_id, reviewShot.verdict]))
    : {}
  // 1 unless the page has merged workflow.episode_clips in (phase 6 stage
  // 11, once a script or storyboard exists) -- tier >= 2 gates every clip
  // control; a tier-1 story therefore renders none of them (RC).
  const tier = episode.assets ? episode.assets.tier : 1
  const [selectedId, setSelectedId] = useState(null)

  const groups = storyboard && script
    ? buildShotGroups(storyboard, scenesById).filter((group) => group.scene)
    : []
  const ordered = groups.flatMap((group) => group.shots)
  const selected = ordered.find((shot) => shot.shot_id === selectedId) || ordered[0] || null
  const selectedIndex = selected ? ordered.indexOf(selected) : -1
  const group = selected ? groups.find((entry) => entry.shots.includes(selected)) : null
  const groupIndex = group ? groups.indexOf(group) : -1
  const lastOfScene = Boolean(group && group.shots[group.shots.length - 1] === selected)
  const boundary = lastOfScene && groupIndex < groups.length - 1
    ? storyboard.transitions.find((transition) => transition.after === selected.shot_id)
    : null
  const step = (delta) => {
    const next = ordered[selectedIndex + delta]
    if (next) setSelectedId(next.shot_id)
  }

  return (
    <div className="story-step-body">
      <StoryboardHeader storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />

      {storyboard && storyboard.shots.length > 0 && script && (
        <>
          <StoryboardBanners storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />

          {assetsBlocked && <p className="form-hint story-storyboard-assets-blocked">{assetsBlocked}</p>}

          <Card className="story-storyboard-board">
            <CardHeader
              title="Shots"
              subtitle={`${ordered.length} shot${ordered.length === 1 ? '' : 's'} in ${groups.length} scene${groups.length === 1 ? '' : 's'} — pick one to open it below`}
              actions={(
                <>
                  <IconButton icon={ChevronLeft} size="sm" variant="secondary" aria-label="Previous shot"
                    onClick={() => step(-1)} disabled={selectedIndex <= 0} />
                  <IconButton icon={ChevronRight} size="sm" variant="secondary" aria-label="Next shot"
                    onClick={() => step(1)} disabled={selectedIndex < 0 || selectedIndex >= ordered.length - 1} />
                </>
              )}
            />
            <CardBody className="story-storyboard-board-body">
              <Filmstrip
                storyId={storyId}
                ep={ep}
                groups={groups}
                transitions={storyboard.transitions}
                assetsByShotId={assetsByShotId}
                verdictsByShotId={verdictsByShotId}
                tier={tier}
                selectedId={selected ? selected.shot_id : null}
                onSelect={setSelectedId}
              />
            </CardBody>
          </Card>

          {selected && group && (
            <div className="story-storyboard-detail" id={`storyboard-scene-${group.sceneId}`}>
              <SceneHeader scene={group.scene} boardEntry={storyboard.scenes[group.sceneId]} places={places} />
              <ShotCard
                key={selected.shot_id}
                storyId={storyId}
                ep={ep}
                shot={selected}
                assetShot={assetsByShotId[selected.shot_id]}
                tier={tier}
                scene={group.scene}
                maps={maps}
                assetsBlocked={assetsBlocked}
                busy={busy}
                onChange={onChange}
              />
              {boundary && (
                <div className="story-storyboard-boundary">
                  <span className="form-label">Transition to scene {groups[groupIndex + 1].sceneId}</span>
                  <TransitionSelect storyId={storyId} ep={ep} transition={boundary} busy={busy} onChange={onChange} />
                </div>
              )}
            </div>
          )}

          <ApproveStoryboard storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />

          {/* The stepper's Keyframes and Clips nodes open these two sections
              (#keyframes, #clips -- EpisodeStudio.jsx's STEP_TARGETS). */}
          <section id="episode-keyframes" className="story-storyboard-section">
            <AssetsHeader storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />
            <ImageOfferBanner storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />
            <ApproveKeyframes storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />
          </section>
          <section id="episode-clips" className="story-storyboard-section">
            <VideoPhaseHeader storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />
            <ApproveAssets storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />
          </section>
        </>
      )}
    </div>
  )
}

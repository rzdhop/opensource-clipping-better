// The review pane (phase 7 follow-up, stage C): the one screen "Generate
// episode" ends on. The page's `review` block (workflow.episode_review) is
// read as it comes -- the episode's status and headline, what the one click
// approved, what is still pending, the flagged keyframes, the spend by kind,
// the render's state -- above a grid of one tile per shot: the keyframe
// thumbnail, its verdict chip, the shot's line. A tile opens large, with the
// clip when one was made, and the existing regenerate controls (the image's
// `shot:<ep>:<shid>`, the clip's `shot:<ep>:<shid>:video`). One primary action
// approves whatever is pending with the existing approve routes, in order:
// the keyframes, then the assets (the render is the one click's own).
// Mirrors StoryboardPane.jsx's conventions (spec 10); phone-first at 375 px:
// three tiles a row, the large view a full-width card.

import { useEffect, useRef, useState } from 'react'
import {
  approveStoryDoc, regenerateStory, fetchStoryEstimate, fetchShotImageUrl, fetchEpisodeClipUrl,
} from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import { RegenerateControl, StepError } from '../fields'
import { formatUsd } from '../../../lib/format'

/**
 * A blob URL for one media file (the shot image route, the clip route:
 * fetched with the auth header, DEC-113), kept while `key` is unchanged and
 * revoked on unmount or change -- StoryboardPane.jsx's ShotImageBlock
 * discipline, as a hook so the tile and the large view share it.
 */
function useBlobUrl(load, key) {
  const [url, setUrl] = useState(null)
  const [failed, setFailed] = useState(false)
  const urlRef = useRef(null)

  useEffect(() => {
    setUrl(null)
    setFailed(false)
    if (!key) return undefined
    let cancelled = false
    load().then((fresh) => {
      if (cancelled) { URL.revokeObjectURL(fresh); return }
      urlRef.current = fresh
      setUrl(fresh)
    }).catch(() => { if (!cancelled) setFailed(true) })
    return () => {
      cancelled = true
      if (urlRef.current) { URL.revokeObjectURL(urlRef.current); urlRef.current = null }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  return { url, failed }
}

// clipping.aistory.workflow.REVIEW_VERDICT_STATES, verbatim
// (tests/test_dashboard_generate_episode.py): what each shot's keyframe chip
// says -- passed; fixed after N redraws (the auto-fix, phase 8 stage B);
// still flagged, with what J2 found; not current (the check judged other
// images: regenerate or run the assets step); not checked yet.
function verdictChip(verdict) {
  switch (verdict.state) {
    case 'passed':
      return { text: 'passed', className: 'chip chip-accent' }
    case 'fixed':
      return {
        text: `fixed after ${verdict.redraws} redraw${verdict.redraws === 1 ? '' : 's'}`,
        className: 'chip chip-accent',
      }
    case 'flagged':
      return {
        text: `still flagged${verdict.gave_up ? ` after ${plural(verdict.redraws, 'redraw')}` : ''}: ${verdict.issue}`,
        className: 'chip chip-warn chip-wrap',
      }
    case 'not_current':
      return { text: 'not current', className: 'chip chip-warn' }
    case 'unchecked':
    default:
      return { text: 'not checked', className: 'chip' }
  }
}

function speakerName(speaker, characters) {
  if (speaker === 'narrator') return 'Narrator'
  const doc = (characters || []).find((c) => c.char_id === speaker)
  return doc ? doc.name : speaker
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? '' : 's'}`
}

// -------------------------------------------------------------------- the tile

function ReviewTile({ storyId, ep, shot, characters, onOpen }) {
  const { url, failed } = useBlobUrl(() => fetchShotImageUrl(storyId, ep, shot.image_name), shot.image_name)
  const chip = verdictChip(shot.verdict)
  const first = shot.lines[0]
  const hasClip = Boolean(shot.clip && shot.clip.name)

  return (
    <button
      type="button"
      className="story-review-tile"
      onClick={() => onOpen(shot)}
      aria-label={`Shot ${shot.shot_id}: see it large`}
    >
      <span className="story-review-thumb">
        {url ? (
          <img className="story-shot-asset-image" src={url} alt="" />
        ) : (
          <span className="story-shot-asset-placeholder" aria-hidden="true">
            {failed ? 'Failed to load' : shot.image_name ? '' : 'No keyframe yet'}
          </span>
        )}
        {hasClip && (
          <span className="story-review-clip-mark" title={`clip ${shot.clip.state}`}>
            {shot.clip.current ? '▶' : '▶ stale'}
          </span>
        )}
      </span>
      <span className="story-review-tile-head">
        <span className="story-script-scene-id">{shot.shot_id}</span>
        {shot.locked && <span className="chip">locked</span>}
      </span>
      <span className={chip.className}>{chip.text}</span>
      {first && (
        <span className="story-review-line">{speakerName(first.speaker, characters)}: {first.text}</span>
      )}
    </button>
  )
}

// --------------------------------------------------------------- the large view

/**
 * The clip's re-animate (StoryboardPane.jsx's ClipRegenerate, duplicated for
 * because the panes share no component module): its own estimate first, never fetched while
 * `clip.blocked` is set (browser-check finding F2).
 */
function ClipRegenerate({ storyId, shot, disabled, onChange }) {
  const [estimate, setEstimate] = useState(null)
  const target = shot.clip ? shot.clip.target : null
  const blocked = shot.clip ? shot.clip.blocked : null

  useEffect(() => {
    setEstimate(null)
    if (!target || blocked) return undefined
    let cancelled = false
    fetchStoryEstimate(storyId, 'regenerate', { target: shot.clip.target })
      .then((data) => { if (!cancelled) setEstimate(data) })
      .catch(() => { if (!cancelled) setEstimate(null) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId, target, blocked])

  const regenerate = async (note) => {
    await regenerateStory(storyId, { target: shot.clip.target, note })
    onChange()
  }

  return (
    <RegenerateControl
      disabled={disabled || !target}
      onRegenerate={regenerate}
      estimateChip={blocked ? null : <EstimateChip estimate={estimate} />}
      actionLabel="Re-animate with note"
    />
  )
}

function ReviewDetail({ storyId, ep, shot, characters, assetsBlocked, busy, onClose, onChange }) {
  const image = useBlobUrl(() => fetchShotImageUrl(storyId, ep, shot.image_name), shot.image_name)
  const clipName = shot.clip ? shot.clip.name : null
  const clip = useBlobUrl(() => fetchEpisodeClipUrl(storyId, ep, shot.clip.name), clipName)
  const chip = verdictChip(shot.verdict)

  useEffect(() => {
    const onKey = (event) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const regenerateImage = async (note) => {
    await regenerateStory(storyId, { target: `shot:${ep}:${shot.shot_id}`, note })
    onChange()
  }

  // The F8 pattern: the server's own refusal as visible text, never only a title.
  const imageReason = shot.locked ? 'Unlock the shot on the storyboard first to regenerate its keyframe.' : assetsBlocked
  const clipReason = !shot.clip ? null
    : shot.clip.continue ? 'Still generating — press Continue on the assets step.'
    : shot.clip.blocked

  return (
    <div className="story-review-overlay" role="dialog" aria-modal="true" aria-label={`Shot ${shot.shot_id}`}>
      <div className="card story-review-detail">
        <div className="story-review-detail-head">
          <span className="story-script-scene-id">{shot.shot_id}</span>
          <span className={chip.className}>{chip.text}</span>
          <button type="button" className="btn btn-ghost btn-sm story-review-close" onClick={onClose}>Close</button>
        </div>
        <div className="story-review-media">
          <div className="story-review-large">
            {image.url ? (
              <img className="story-shot-asset-image" src={image.url} alt="" />
            ) : (
              <span className="story-shot-asset-placeholder" aria-hidden="true">
                {image.failed ? 'Failed to load' : shot.image_name ? '' : 'No keyframe yet'}
              </span>
            )}
          </div>
          {clipName && (
            <div className="story-review-large">
              {clip.url ? (
                <video className="story-shot-asset-image" src={clip.url} playsInline controls />
              ) : (
                <span className="story-shot-asset-placeholder" aria-hidden="true">
                  {clip.failed ? 'Failed to load' : ''}
                </span>
              )}
            </div>
          )}
        </div>
        {shot.clip && (
          <p className="form-hint">
            Clip: {clipName ? `${shot.clip.state}${shot.clip.current ? '' : ' (not this keyframe’s)'}` : 'none yet'}
          </p>
        )}
        {shot.verdict.issue && <p className="form-hint">Keyframe check (J2): {shot.verdict.issue}</p>}
        {shot.fix && (
          <p className="form-hint">
            Auto-fix: {plural(shot.fix.redraws, 'redraw')}, ${formatUsd(shot.fix.spent_usd)}
            {shot.fix.gave_up ? ' — gave up, still flagged' : ''}
          </p>
        )}
        {shot.lines.length > 0 && (
          <ul className="story-field-list story-review-lines">
            {shot.lines.map((line) => (
              <li key={line.line_id}><strong>{speakerName(line.speaker, characters)}:</strong> {line.text}</li>
            ))}
          </ul>
        )}
        <div className="story-field-label">Keyframe</div>
        <RegenerateControl
          disabled={busy || Boolean(imageReason)}
          onRegenerate={regenerateImage}
          empty={!shot.image_name}
          label="keyframe"
        />
        {imageReason && <p className="form-hint">{imageReason}</p>}
        {shot.clip && (
          <>
            <div className="story-field-label">Clip</div>
            <ClipRegenerate storyId={storyId} shot={shot} disabled={busy || Boolean(clipReason)} onChange={onChange} />
            {clipReason && <p className="form-hint">{clipReason}</p>}
          </>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- the action

/**
 * The one primary action: approves what `review.pending` lists, in its
 * order -- the keyframes (`keyframes:<ep>`), then the assets (`assets:<ep>`)
 * -- with the existing routes. A keyframe refusal (a failed or missing check)
 * shows the server's sentence and offers "Approve anyway", which goes over
 * it and then approves the assets too. Disabled once nothing is pending.
 */
function ApproveAll({ storyId, ep, review, busy, onChange }) {
  const [approving, setApproving] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  const [refusedKeyframes, setRefusedKeyframes] = useState(false)

  const pending = review.pending
  const label = pending.length === 2 ? 'Approve keyframes and assets'
    : pending[0] === 'keyframes' ? 'Approve keyframes'
    : pending[0] === 'assets' ? 'Approve assets'
    : 'Everything is approved'
  const reason = busy ? 'A step is running.' : null

  const approve = async (anyway) => {
    setApproving(true)
    setError('')
    setErrors(null)
    setRefusedKeyframes(false)
    try {
      if (pending.includes('keyframes')) {
        try {
          await approveStoryDoc(storyId, review.approvals.keyframes.target, anyway ? { approve_anyway: true } : undefined)
        } catch (err) {
          setRefusedKeyframes(true)
          throw err
        }
      }
      if (pending.includes('assets')) await approveStoryDoc(storyId, `assets:${ep}`)
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  return (
    <>
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => approve(false)}
          disabled={pending.length === 0 || Boolean(reason) || approving}
          title={reason || undefined}
        >
          {approving ? 'Approving…' : label}
        </button>
        {refusedKeyframes && error && (
          <button type="button" className="btn btn-secondary" onClick={() => approve(true)}
            disabled={Boolean(reason) || approving}>
            Approve anyway
          </button>
        )}
        {reason && <span className="form-hint">{reason}</span>}
      </div>
      <StepError message={error} errors={errors} className="story-step-error" />
    </>
  )
}

// --------------------------------------------------------------------- page

export default function ReviewPane({ episode, characters, storyId, ep, inFlightJob, onChange }) {
  const [open, setOpen] = useState(null) // the shot open large, by id
  const review = episode.review
  if (!review) return null

  const busy = Boolean(inFlightJob)
  const spend = review.spend
  const keyframes = review.approvals.keyframes
  const render = review.render
  const rendered = Boolean(render && render.state === 'completed' && !render.out_of_date)
  const shot = open ? review.shots.find((item) => item.shot_id === open) : null

  return (
    <div className="story-step-body">
      <div className="card story-review-header">
        <h3 className="card-title">Review</h3>
        <p className="story-review-headline">{review.headline}</p>
        <div className="story-step-actions">
          <span className={review.ready ? 'chip chip-accent' : 'chip chip-warn'}>{review.status.replace(/_/g, ' ')}</span>
          <span className="chip" title="What this episode spent so far, by kind">
            spent ${formatUsd(spend.total_usd)} · keyframes ${formatUsd(spend.images_usd)}
            {spend.fixes_usd > 0 ? ` · redraws $${formatUsd(spend.fixes_usd)}` : ''}
            {' · '}voices ${formatUsd(spend.voices_usd)} · clips ${formatUsd(spend.clips_usd)}
          </span>
          <span className={review.flagged.length ? 'chip chip-warn' : 'chip chip-accent'}>
            {plural(review.flagged.length, 'keyframe')} flagged
          </span>
          {render && (
            <span className={rendered ? 'chip chip-accent' : 'chip chip-warn'}>
              render: {render.state}{render.out_of_date ? ' (out of date)' : ''}
              {render.duration_s != null ? ` · ${render.duration_s.toFixed(1)} s` : ''}
            </span>
          )}
        </div>
        {review.auto_approved.length > 0 && (
          <p className="form-hint">
            Approved for you by Generate episode: {review.auto_approved.join(' and ')}
            {keyframes && keyframes.anyway && keyframes.flagged.length
              ? ` (the keyframes anyway — still flagged: ${keyframes.flagged.join(', ')})` : ''}.
          </p>
        )}
        {review.script_repairs && review.script_repairs.length > 0 && (
          <p className="form-hint">
            The script step repaired what the first-watch check found ({plural(review.script_repairs.length, 'pass')}).
          </p>
        )}
        {review.script_minor_issues && review.script_minor_issues.length > 0 && (
          <div className="form-hint">
            The first-watch check passed with {plural(review.script_minor_issues.length, 'minor issue')} kept for
            you to read (not blocking, not repaired):
            <ul>
              {review.script_minor_issues.map((issue, index) => (
                <li key={index}>{issue.scene_id || 'the episode'} ({issue.kind.replace(/_/g, ' ')}): {issue.fix}</li>
              ))}
            </ul>
          </div>
        )}
        <ApproveAll storyId={storyId} ep={ep} review={review} busy={busy} onChange={onChange} />
        {review.status === 'render_needed' && <p className="form-hint">Then render it on the Preview tab.</p>}
      </div>

      <div className="story-review-grid">
        {review.shots.map((item) => (
          <ReviewTile key={item.shot_id} storyId={storyId} ep={ep} shot={item} characters={characters}
            onOpen={(opened) => setOpen(opened.shot_id)} />
        ))}
      </div>

      {shot && (
        <ReviewDetail
          storyId={storyId}
          ep={ep}
          shot={shot}
          characters={characters}
          assetsBlocked={episode.state.assets_regenerate_blocked}
          busy={busy}
          onClose={() => setOpen(null)}
          onChange={onChange}
        />
      )}
    </div>
  )
}

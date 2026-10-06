// The review pane (phase 7 follow-up, stage C): the one screen "Generate
// episode" ends on. The page's `review` block (workflow.episode_review) is
// read as it comes -- since the dashboard overhaul's stage 4 (DEC-256) as a
// hero: the rendered episode (else the first keyframe) beside a checklist of
// what is approved and by whom, the flagged keyframes, the spend by kind and
// the one approve action -- above a grid of one tile per shot: the keyframe
// thumbnail, its verdict chip, the shot's line. A tile opens large, with the
// clip when one was made, and the existing regenerate controls (the image's
// `shot:<ep>:<shid>`, the clip's `shot:<ep>:<shid>:video`). One primary action
// approves whatever is pending with the existing approve routes, in order:
// the keyframes, then the assets (the render is the one click's own).
// Mirrors StoryboardPane.jsx's conventions (spec 10); phone-first at 375 px:
// three tiles a row, the large view a full-width card.

import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  approveStoryDoc, regenerateStory, fetchStoryEstimate, fetchShotImageUrl, fetchEpisodeClipUrl,
} from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import { RegenerateControl, StepError } from '../fields'
import { formatUsd } from '../../../lib/format'
import { Badge, Card, CardBody, CardHeader } from '../../../ui'
import { AlertTriangle, Circle, CircleCheck } from '../../../ui/icons'
import { handoffPath } from './HandoffPage'

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
        {shot.warning && <span className="chip chip-warn" title={shot.warning}>your own</span>}
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
  const closeRef = useRef(null)

  useEffect(() => {
    if (closeRef.current) closeRef.current.focus()
  }, [shot.shot_id])

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
          <button type="button" className="btn btn-ghost btn-sm story-review-close" onClick={onClose} ref={closeRef}>Close</button>
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
        {shot.warning ? (
          // Plan 28 F1: the human's own keyframe -- the check's issues are a warning, never a refusal.
          <p className="form-hint">Your own keyframe. {shot.warning}</p>
        ) : shot.verdict.issue && <p className="form-hint">Keyframe check: {shot.verdict.issue}</p>}
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

// ------------------------------------------------------------------ the hero

/**
 * The review's hero (dashboard overhaul stage 4, DEC-256): the rendered
 * episode when there is one -- `render.media.video_url` is already a signed
 * URL (DEC-163), used directly as PreviewPane.jsx's player does, with the
 * same one-time refetch when it has expired -- else the first keyframe.
 */
function ReviewHero({ storyId, ep, episode, review, onChange }) {
  const render = episode.render
  const playable = Boolean(render && render.output && render.media && render.media.video_url)
  const first = review.shots.find((item) => item.image_name) || null
  const still = useBlobUrl(
    () => fetchShotImageUrl(storyId, ep, first.image_name),
    playable || !first ? null : first.image_name,
  )
  const outputSha = playable ? render.output.sha256 : null
  const [recovered, setRecovered] = useState(false)
  useEffect(() => { setRecovered(false) }, [outputSha])

  const recoverMedia = () => {
    if (recovered) return
    setRecovered(true)
    onChange()
  }

  return (
    <div className="story-review-hero-media">
      {playable ? (
        <video
          key={outputSha}
          className="story-review-hero-video"
          src={render.media.video_url}
          controls
          playsInline
          preload="metadata"
          poster={render.media.cover_url || undefined}
          onError={recoverMedia}
        />
      ) : still.url ? (
        <img className="story-review-hero-still" src={still.url} alt={`Shot ${first.shot_id}, the first keyframe`} />
      ) : (
        <span className="story-shot-asset-placeholder story-review-hero-placeholder">
          {still.failed ? 'Failed to load' : first ? '' : 'Nothing rendered yet'}
        </span>
      )}
      <span className="story-review-hero-caption">
        {playable
          ? `The rendered episode${render.out_of_date ? ' (out of date)' : ''}`
          : first ? `First keyframe (${first.shot_id}) — not rendered yet` : 'No keyframe yet'}
      </span>
    </div>
  )
}

// ---------------------------------------------------------- the checklist

const BY_LABELS = { fast_track: 'by Generate episode', user: 'by you' }

function whenText(at) {
  return at ? new Date(at).toLocaleString() : ''
}

/**
 * What is approved, and by whom, as one checklist: the script, the
 * storyboard, the keyframes (the flagged ones named), the assets and the
 * render -- read from `review.approvals` and `review.render` as they come.
 */
function ApprovalsChecklist({ review }) {
  const { script, storyboard, keyframes, assets } = review.approvals
  const render = review.render
  const rendered = Boolean(render && render.state === 'completed' && !render.out_of_date)
  // Plan 19 stage 3: the blocking issues the one click approved the script over, named as the keyframes' are.
  const scriptIssues = script.issues && script.issues.length
    ? ` — still found: ${script.issues.map((issue) => `${issue.scene_id || 'the episode'} (${issue.kind})`).join(', ')}` : ''
  const rows = [
    {
      key: 'script', label: 'Script', done: script.approved, stale: false,
      detail: script.approved
        ? `Approved${script.anyway ? ' anyway' : ''} ${BY_LABELS[script.by] || ''} ${whenText(script.at)}${scriptIssues}`
        : 'Not approved',
    },
    {
      key: 'storyboard', label: 'Storyboard', done: storyboard.approved, stale: false,
      detail: storyboard.approved ? `Approved ${whenText(storyboard.at)}` : 'Not approved',
    },
  ]
  if (keyframes) {
    const flaggedNote = keyframes.flagged && keyframes.flagged.length
      ? ` — still flagged: ${keyframes.flagged.join(', ')}` : ''
    rows.push({
      key: 'keyframes', label: 'Keyframes', done: keyframes.approval === 'current', stale: keyframes.approval === 'stale',
      detail: keyframes.approval === 'current'
        ? `Approved${keyframes.anyway ? ' anyway' : ''} ${BY_LABELS[keyframes.by] || ''} ${whenText(keyframes.at)}${flaggedNote}`
        : keyframes.approval === 'stale' ? 'Approved before a keyframe changed: approve again' : 'Not approved',
    })
  }
  rows.push({
    key: 'assets', label: 'Assets', done: assets.approval === 'current', stale: assets.approval === 'stale',
    detail: assets.approval === 'current'
      ? `Approved ${BY_LABELS[assets.by] || ''} ${whenText(assets.at)}`
      : assets.approval === 'stale' ? 'Approved before an asset changed: approve again' : 'Not approved',
  })
  rows.push({
    key: 'render', label: 'Render', done: rendered, stale: Boolean(render && render.out_of_date),
    detail: !render ? 'Not rendered'
      : `${render.state}${render.out_of_date ? ' (out of date)' : ''}${render.duration_s != null ? ` · ${render.duration_s.toFixed(1)} s` : ''}`,
  })

  return (
    <ul className="story-review-checklist">
      {rows.map((row) => {
        const Icon = row.done ? CircleCheck : row.stale ? AlertTriangle : Circle
        const tone = row.done ? 'done' : row.stale ? 'stale' : 'pending'
        return (
          <li key={row.key} className={`story-review-check story-review-check-${tone}`}>
            <Icon size={16} aria-hidden="true" className="story-review-check-icon" />
            <span className="story-review-check-label">{row.label}</span>
            <span className="story-review-check-detail">
              <span className="sr-only">{row.done ? 'done: ' : row.stale ? 'stale: ' : 'to do: '}</span>
              {row.detail.replace(/\s+/g, ' ').trim()}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

// ---------------------------------------------------------------- the action

/**
 * The one primary action: approves what `review.pending` lists, in its
 * order -- the keyframes (`keyframes:<ep>`), then the assets (`assets:<ep>`)
 * -- with the existing routes. Plan 28 F1: the keyframe check (J2) is a hard
 * gate, the server goes over nothing -- a keyframe refusal shows the
 * server's sentence (each shot and what the judge saw) and offers the two
 * ways out instead: regenerate the shot (its tile, opened large) or upload
 * your own keyframe (the Handoff, the shot set to "My own"). Disabled once
 * nothing is pending.
 */
function ApproveAll({ storyId, ep, review, busy, onChange, onOpenShot }) {
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

  const approve = async () => {
    setApproving(true)
    setError('')
    setErrors(null)
    setRefusedKeyframes(false)
    try {
      if (pending.includes('keyframes')) {
        try {
          await approveStoryDoc(storyId, review.approvals.keyframes.target)
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
          onClick={approve}
          disabled={pending.length === 0 || Boolean(reason) || approving}
          title={reason || undefined}
        >
          {approving ? 'Approving…' : label}
        </button>
        {reason && <span className="form-hint">{reason}</span>}
      </div>
      <StepError message={error} errors={errors} className="story-step-error" />
      {refusedKeyframes && error && (
        <div className="story-step-actions">
          {review.flagged.map((shotId) => (
            <button key={shotId} type="button" className="btn btn-secondary btn-sm"
              onClick={() => onOpenShot(shotId)} disabled={Boolean(reason)}>
              Regenerate {shotId}
            </button>
          ))}
          <Link to={handoffPath(storyId, ep)} className="btn btn-ghost btn-sm">
            Upload your own (Handoff)
          </Link>
        </div>
      )}
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
  const scriptApproval = review.approvals.script
  const scriptIssues = scriptApproval.issues || []
  const shot = open ? review.shots.find((item) => item.shot_id === open) : null

  return (
    <div className="story-step-body">
      <Card className="story-review-header">
        <CardHeader
          title="Review"
          subtitle={review.headline}
          actions={(
            <Badge tone={review.ready ? 'success' : 'warning'} dot>{review.status.replace(/_/g, ' ')}</Badge>
          )}
        />
        <CardBody className="story-review-hero">
          <ReviewHero storyId={storyId} ep={ep} episode={episode} review={review} onChange={onChange} />
          <div className="story-review-hero-side">
            <ApprovalsChecklist review={review} />
            <div className="story-step-actions">
              <span className="chip chip-wrap" title="What this episode spent so far, by kind">
                spent ${formatUsd(spend.total_usd)} · keyframes ${formatUsd(spend.images_usd)}
                {spend.fixes_usd > 0 ? ` · redraws $${formatUsd(spend.fixes_usd)}` : ''}
                {' · '}voices ${formatUsd(spend.voices_usd)} · clips ${formatUsd(spend.clips_usd)}
              </span>
              <span className={review.flagged.length ? 'chip chip-warn' : 'chip chip-accent'}>
                {plural(review.flagged.length, 'keyframe')} flagged
              </span>
            </div>
            {review.auto_approved.length > 0 && (
              <p className="form-hint">
                Approved for you by Generate episode: {review.auto_approved.join(' and ')}
                {keyframes && keyframes.anyway && keyframes.flagged.length
                  ? ` (the keyframes anyway — still flagged: ${keyframes.flagged.join(', ')})` : ''}.
              </p>
            )}
            {scriptApproval.by === 'fast_track' && scriptApproval.anyway && scriptIssues.length > 0 && (
              <p className="form-hint">
                The script was approved for you anyway — its repair passes left:{' '}
                {scriptIssues.map((issue) => `${issue.scene_id || 'the episode'} (${issue.kind}): ${issue.fix}`).join('; ')}
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
            <ApproveAll storyId={storyId} ep={ep} review={review} busy={busy} onChange={onChange}
              onOpenShot={setOpen} />
            {review.status === 'render_needed' && <p className="form-hint">Then render it on the Preview tab.</p>}
          </div>
        </CardBody>
      </Card>

      <h4 className="story-review-strip-title">Keyframes · {plural(review.shots.length, 'shot')}</h4>
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

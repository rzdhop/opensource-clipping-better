// The storyboard's keyframe and asset cards (dashboard overhaul stage 4,
// DEC-256: split out of StoryboardPane.jsx, the code moved as it was, on the
// kit's Card and Badge): Generate assets, the image-link offer, the video
// phase, the keyframe approval and the assets approval. Their approvals are
// the routes they always were.

import { useEffect, useState } from 'react'
import { runStoryStep, approveStoryDoc, fetchStoryEstimate, patchEpisodeAssetsLinks } from '../../../../api'
import RouteChip from '../../../../components/RouteChip'
import { StepError } from '../../fields'
import { formatUsd } from '../../../../lib/format'
import { Badge, Card, CardBody, CardHeader, useConfirm } from '../../../../ui'

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
    <Card className="story-assets-header">
      <CardHeader title="Assets" subtitle="The shot keyframes and the line voices, then the clips" />
      <CardBody>
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
              est. ${formatUsd(estimate.est_usd)} · {estimate.images.count} image{estimate.images.count === 1 ? '' : 's'}
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
                  ? (estimate.video.route_class === 'local' ? '(local)' : `($${formatUsd(estimate.video.est_usd)})`)
                  : `(est $${formatUsd(estimate.video.est_usd)}, not now)`}
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
      </CardBody>
    </Card>
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
  const confirm = useConfirm()
  const [switching, setSwitching] = useState(false)
  const [switchError, setSwitchError] = useState('')
  const offer = episode.assets && episode.assets.image_offer
  if (!offer) return null

  const handleSwitch = async () => {
    if (!offer.switch) return
    if (!(await confirm({ title: 'Switch now?', message: offer.message, confirmLabel: `Switch to ${offer.next_link}` }))) return
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
    <Card className="story-assets-header">
      <CardHeader title="Image link" />
      <CardBody>
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
      </CardBody>
    </Card>
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
  const confirm = useConfirm()
  const [switching, setSwitching] = useState(false)
  const [switchError, setSwitchError] = useState('')
  const video = episode.assets && episode.assets.video
  if (!video) return null

  const handleSwitch = async () => {
    if (!video.offer || !video.offer.switch) return
    if (!(await confirm({ title: 'Switch now?', message: video.offer.message, confirmLabel: 'Switch' }))) return
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
    <Card className="story-assets-header">
      <CardHeader title="Video" subtitle="The clips the next assets run would make" />
      <CardBody>
      <div className="story-step-actions">
        {video.route_class && <RouteChip routeClass={video.route_class} link={video.link} />}
        <span className="chip" title={video.message || ''}>
          {video.count != null ? `${video.count} clip${video.count === 1 ? '' : 's'}` : 'nothing to animate yet'}
          {video.seconds != null ? ` · ${video.seconds.toFixed(1)} s` : ''}
          {video.count != null ? (
            video.route_class === 'local' && video.eta_s != null
              ? ` · ~${Math.round(video.eta_s)} s local${video.eta_note ? ` (${video.eta_note})` : ''}`
              : ` · $${formatUsd(video.est_usd)}`
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
      </CardBody>
    </Card>
  )
}

/**
 * A v2 episode's keyframe approval (phase 7 stage 6b, DEC-230): the approval
 * no clip is bought before (RC-Q3; `workflow.approve_keyframes`,
 * `episode.assets.keyframes`: approval none | current | stale), for a story
 * that stopped at the keyframes ("Stop at the keyframes for my review"). A
 * refusal shows the server's sentence and offers "Approve anyway", which goes
 * over a failed or missing check, never over a missing keyframe. Each shot's
 * check (J2) is read, large, on the Review tab (stage C: `episode.review`,
 * ReviewPane.jsx) -- this card only counts the flagged ones. Null on a legacy
 * episode (no `keyframes` in the payload).
 */
function ApproveKeyframes({ storyId, ep, episode, busy, onChange }) {
  const [approving, setApproving] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const assets = episode.assets
  if (!assets || !assets.doc || !assets.keyframes) return null

  const keyframes = assets.keyframes
  const approval = keyframes.approval
  const approved = approval === 'current'
  const review = episode.review
  const flagged = review ? review.flagged.length : 0
  const reason = busy ? 'A step is running.' : null

  const handleApprove = async (anyway) => {
    setApproving(true)
    setError('')
    setErrors(null)
    try {
      await approveStoryDoc(storyId, keyframes.target, anyway ? { approve_anyway: true } : undefined)
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  return (
    <Card className="story-keyframes-approve">
      <CardHeader
        title="Keyframes"
        subtitle="Approved before any clip is bought"
        actions={<Badge tone={approved ? 'success' : approval === 'stale' ? 'warning' : 'neutral'} dot>{approved ? 'Approved' : approval === 'stale' ? 'Stale' : 'Not approved'}</Badge>}
      />
      <CardBody>
      <p className="form-hint">
        No clip is bought until the keyframes are approved.{' '}
        {review
          ? `${flagged} keyframe${flagged === 1 ? '' : 's'} still flagged by the check (J2) — see each one, large, on the Review tab.`
          : 'Check each one shows its beat on the Review tab.'}
      </p>
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => handleApprove(false)}
          disabled={approved || Boolean(reason) || approving}
          title={reason || undefined}
        >
          {approving ? 'Approving…' : approved ? 'Keyframes approved' : 'Approve keyframes'}
        </button>
        {error && !approved && (
          <button type="button" className="btn btn-secondary" onClick={() => handleApprove(true)}
            disabled={Boolean(reason) || approving}>
            Approve anyway
          </button>
        )}
        <Badge tone={approved ? 'accent' : approval === 'stale' ? 'warning' : 'neutral'}>
          keyframes: {approval}{approved && keyframes.anyway ? ' (anyway)' : ''}
        </Badge>
        {reason && <span className="form-hint">{reason}</span>}
      </div>
      {keyframes.approved_at && (
        <p className="form-hint">
          {approved ? 'Approved' : 'Approved previously (a keyframe changed since)'}{' '}
          {new Date(keyframes.approved_at).toLocaleString()}.
        </p>
      )}
      <StepError message={error} errors={errors} className="story-step-error" />
      </CardBody>
    </Card>
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
    <Card className="story-assets-approve">
      <CardHeader
        title="Assets approval"
        subtitle="The keyframes, voices and clips the render uses"
        actions={<Badge tone={fingerprint === 'current' ? 'success' : fingerprint === 'stale' ? 'warning' : 'neutral'} dot>{fingerprint === 'current' ? 'Approved' : fingerprint === 'stale' ? 'Stale' : 'Not approved'}</Badge>}
      />
      <CardBody>
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
        <Badge tone={fingerprint === 'current' ? 'accent' : fingerprint === 'stale' ? 'warning' : 'neutral'}>
          fingerprint: {fingerprint}
        </Badge>
        {reason && <span className="form-hint">{reason}</span>}
      </div>
      {assets.approved_at && (
        <p className="form-hint">
          {approved ? 'Approved' : 'Approved previously (now stale)'} {new Date(assets.approved_at).toLocaleString()}.
        </p>
      )}
      <StepError message={error} errors={errors} className="story-step-error" />
      </CardBody>
    </Card>
  )
}

export { AssetsHeader, ImageOfferBanner, VideoPhaseHeader, ApproveKeyframes, ApproveAssets }

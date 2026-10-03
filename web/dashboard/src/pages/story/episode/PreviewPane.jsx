// The Preview pane (phase 4, stage 15): render the episode (a subtitles
// toggle, the estimate/route chip and the live feed), the video player and
// its manifest summary, "Write metadata" with per-platform cards (copy
// buttons, per-platform regenerate, a stale badge), the cover, the episode's
// cost ledger and a download link. Mirrors StoryboardPane.jsx's /
// ScriptPane.jsx's shape and conventions (spec 10; DEC-164: the subtitles
// toggle is a per-episode render parameter that re-runs only the final pass).

import { useEffect, useRef, useState } from 'react'
import { runStoryStep, regenerateStory, fetchStoryEstimate } from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import RouteChip from '../../../components/RouteChip'
import { RegenerateControl, StepError } from '../fields'
import { formatUsd } from '../../../lib/format'

// clipping.aistory.render.partial.RENDER_REUSE_REASONS / schemas.
// RENDER_REUSE_REASONS, verbatim: one line per shot in "Changes since last
// render" (plan 11 stage 11).
const REUSE_REASON_LABELS = {
  image: 'the image changed', motion: 'the camera motion changed', frames: 'the timing changed',
  modifiers: 'the modifiers changed', overlay: 'the style overlay changed',
  missing: 'missing from the cache', corrupt: 'the cached clip is corrupt', new: 'a new shot',
  settings: 'the render settings changed',
}

// clipping.aistory.steps.render.SUBTITLE_CHOICES, verbatim
// (tests/test_story_payload_contract_episode.py): "style" is the style
// lock's own mode (the render step's default); the rest are
// clipping.aistory.schemas.SUBTITLE_MODES.
const SUBTITLE_MODES = [
  { id: 'style', label: 'Style default' },
  { id: 'word_pop', label: 'Word pop' },
  { id: 'two_line', label: 'Two line' },
  { id: 'none', label: 'None' },
]

// clipping.aistory.schemas.PLATFORMS, verbatim; labels as
// clipping.aistory.prompts.PLATFORM_RULES names them (the M1 prompt).
const PLATFORMS = ['tiktok', 'shorts', 'reels']
const PLATFORM_LABELS = { tiktok: 'TikTok', shorts: 'YouTube Shorts', reels: 'Instagram Reels' }

// ------------------------------------------------------------------ copy

/**
 * A copy-to-clipboard button for one metadata field. `navigator.clipboard`
 * needs a secure context (https, or localhost); over plain http (the
 * keyless throwaway backend this stage is checked against, DEC-092) it is
 * undefined or its call rejects, so the fallback selects the text in a
 * hidden textarea for a manual Ctrl+C / Cmd+C instead of failing silently.
 */
function CopyButton({ text, label }) {
  const [status, setStatus] = useState('idle') // idle | copied | selected
  const areaRef = useRef(null)

  const copy = async () => {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text)
        setStatus('copied')
        setTimeout(() => setStatus('idle'), 1500)
        return
      } catch {
        // Denied or unavailable even in a secure context: fall through to
        // the manual-select fallback below rather than doing nothing.
      }
    }
    const area = areaRef.current
    if (!area) return
    area.value = text
    area.hidden = false
    area.focus()
    area.select()
    setStatus('selected')
  }

  return (
    <span className="story-copy">
      <button type="button" className="btn btn-ghost btn-sm" onClick={copy}>
        {status === 'copied' ? 'Copied ✓' : `Copy${label ? ` ${label}` : ''}`}
      </button>
      {status === 'selected' && (
        <span className="form-hint">Selected — press Ctrl+C / Cmd+C to copy.</span>
      )}
      <textarea ref={areaRef} className="story-copy-fallback" readOnly hidden aria-hidden="true" tabIndex={-1} />
    </span>
  )
}

// ---------------------------------------------------------------- render

function RenderHeader({ storyId, ep, episode, assetsApproved, busy, onChange }) {
  const initialSubtitles = (episode.render && episode.render.params && episode.render.params.subtitles) || 'style'
  const [subtitles, setSubtitles] = useState(initialSubtitles)
  // Phase 6 stage 9's own default (render.FILL_PARAM): off, so a failed,
  // stale or still-generating clip refuses the render (409) instead of
  // silently falling back to Tier-1 motion unless this is ticked.
  const [fillFailedWithMotion, setFillFailedWithMotion] = useState(false)
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  // The render's own precondition (an outdated shot image, an unvoiced
  // line, ...) answers the estimate with a 409 naming the shot to fix
  // (render.require_renderable) -- shown here and the button disabled,
  // rather than an estimate that silently never arrives (stage-10 lesson:
  // never an endless "estimating…" for a control that cannot run).
  const [estimateError, setEstimateError] = useState('')
  const [estimateErrors, setEstimateErrors] = useState(null)

  useEffect(() => {
    setEstimate(null)
    setEstimateError('')
    setEstimateErrors(null)
    if (!assetsApproved) return
    fetchStoryEstimate(storyId, 'render', { ep, subtitles, fillFailedWithMotion })
      .then((data) => { setEstimate(data); setEstimateError(''); setEstimateErrors(null) })
      .catch((err) => { setEstimate(null); setEstimateError(err.message); setEstimateErrors(err.errors || null) })
  }, [storyId, ep, assetsApproved, subtitles, fillFailedWithMotion])

  const reason = busy ? 'A step is running.'
    : !assetsApproved ? "Approve the episode's assets first (the Storyboard tab)."
    : null

  const hasRender = Boolean(episode.render)
  // The render's own clip-refusal pre-check (phase 6 stage 11/12), computed
  // server-side with fill_failed=False (workflow.episode_clips's
  // `video.render_blocked`) -- the sentence the render would 409 with right
  // now unless the box below is ticked. Null at tier 1 (episode.assets.video
  // is null there) and once nothing is blocking it.
  const renderBlocked = episode.assets && episode.assets.video && episode.assets.video.render_blocked

  const handleRun = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      const renderParams = { subtitles, fill_failed_with_motion: fillFailedWithMotion }
      await runStoryStep(storyId, 'render', { ep, params: renderParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="card story-render-header">
      <h4 className="card-title">Render</h4>
      <div className="form-group story-render-subtitles">
        <label className="form-label">Subtitles</label>
        <select
          className="form-select"
          value={subtitles}
          onChange={(e) => setSubtitles(e.target.value)}
          disabled={busy || running}
        >
          {SUBTITLE_MODES.map((mode) => <option key={mode.id} value={mode.id}>{mode.label}</option>)}
        </select>
      </div>
      {episode.assets && episode.assets.tier >= 2 && (
        <label className="story-checkbox">
          <input
            type="checkbox"
            checked={fillFailedWithMotion}
            onChange={(e) => setFillFailedWithMotion(e.target.checked)}
            disabled={busy || running}
          />
          Fill failed shots with motion
        </label>
      )}
      {!fillFailedWithMotion && renderBlocked && <p className="form-hint">{renderBlocked}</p>}
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleRun}
          disabled={Boolean(reason) || Boolean(estimateError) || running}
          title={reason || estimateError || undefined}
        >
          {running ? <><span className="spinner"></span> Rendering…</> : hasRender ? 'Render again' : 'Render'}
        </button>
        {estimate && !estimateError && (
          <span className="chip" title={estimate.message || ''}>
            est. ${formatUsd(estimate.est_usd)} · {estimate.units.shots} shot{estimate.units.shots === 1 ? '' : 's'}
            {' · ~'}{estimate.minutes} min
          </span>
        )}
        {estimate && !estimateError && <RouteChip routeClass={estimate.route_class} />}
      </div>
      {reason && <p className="form-hint">{reason}</p>}
      <StepError
        message={estimateError || error}
        errors={estimateError ? estimateErrors : errors}
        className="story-step-error"
      />
    </div>
  )
}

/** The video player, the "out of date" banner, the manifest summary and the
 * download link -- or an empty state while there is nothing to play yet.
 * `render.media.video_url`/`cover_url` are already signed URLs (DEC-163),
 * used directly as `src` (unlike the shot/voice blob routes, which need the
 * bearer header and so are fetched -- ShotImageBlock/LineRow in the other
 * panes), the same way a clip's `download_url` is used in JobDetail.jsx. */
function RenderMedia({ episode, assetsApproved, onChange }) {
  const render = episode.render
  const outputSha = render && render.output ? render.output.sha256 : null
  const [recovered, setRecovered] = useState(false)
  useEffect(() => { setRecovered(false) }, [outputSha])

  // An expiring signed URL can go stale in a tab left open a while; one
  // re-fetch of the episode page mints a fresh one (JobDetail.jsx's
  // recoverExpiredMedia, same reasoning). Guarded so a genuinely broken file
  // cannot become a reload loop.
  const recoverMedia = () => {
    if (recovered) return
    setRecovered(true)
    onChange()
  }

  if (!render || !render.output) {
    const message = !render
      ? (assetsApproved ? 'No render yet — click Render above.'
        : "The episode's assets are not approved yet: approve them (the Storyboard tab) before rendering.")
      : `The last render ended ${render.state}: see the activity log above, or click Render again.`
    return (
      <div className="card story-render-empty">
        <p className="form-hint">{message}</p>
      </div>
    )
  }

  // Signed when the server has a token (?exp=&sig=), the plain path when it has
  // none (DEC-173): the download flag needs & in the first case and ? in the
  // second -- a bare &download=1 on a plain path names a file that is not there.
  const downloadUrl = `${render.media.video_url}${render.media.video_url.includes('?') ? '&' : '?'}download=1`
  const loudness = render.loudness || {}
  const stages = render.stages || {}

  return (
    <div className="card story-render-media">
      {render.out_of_date && (
        <p className="chip chip-warn story-render-outdated">
          Out of date — the assets changed since this render; render again to pick them up.
        </p>
      )}
      <video
        key={render.output.sha256}
        className="story-render-video"
        src={render.media.video_url}
        controls
        playsInline
        preload="metadata"
        poster={render.media.cover_url || undefined}
        onError={recoverMedia}
      />
      <div className="story-render-summary">
        <span className="chip">{render.duration_s != null ? `${render.duration_s.toFixed(1)} s` : '—'}</span>
        <span className="chip">{render.width}×{render.height} · {render.fps} fps</span>
        <span className="chip" title="Integrated loudness / true peak / loudness range">
          I {loudness.i != null ? loudness.i.toFixed(1) : '—'} · TP {loudness.tp != null ? loudness.tp.toFixed(1) : '—'}
          {' '}· LRA {loudness.lra != null ? loudness.lra.toFixed(1) : '—'}
        </span>
        <span className="chip">{render.seconds != null ? `${render.seconds.toFixed(0)} s to render` : '—'}</span>
        {render.reuse ? (
          // The manifest's own reuse record (plan 11 stage 11, replacing the
          // "N/M shots cached" chip): "3 of 11 shots re-rendered", with the
          // whole-frames conversion note folded in already
          // (render.reuse_view/partial.summary) when a board converted.
          // Null for a render with no baseline to compare against (the
          // episode's very first render) -- the old cached-count chip still
          // answers that case below.
          <p className="chip chip-accent story-render-reuse-summary">
            {render.reuse.summary} · {render.reuse.shots_reused.length} reused
          </p>
        ) : (
          <span className="chip">{stages.shots_cached || 0}/{stages.shots || 0} shots cached</span>
        )}
        <span className="chip">{stages.ran || 0} ran · {stages.cached || 0} cached of {stages.total || 0} stages</span>
      </div>
      {render.warnings && render.warnings.length > 0 && (
        <ul className="story-field-list story-error">
          {render.warnings.map((warning, i) => <li key={i}>{warning}</li>)}
        </ul>
      )}
      <a className="btn btn-secondary btn-sm" href={downloadUrl} download>
        ⬇ Download video
      </a>
    </div>
  )
}

// ------------------------------------------------------- changes / re-render

/**
 * "Changes since last render" (plan 11 stage 9's dry-run block, exposed as
 * `episode.render.changes` -- no separate estimate fetch: the episode page
 * already carries it, cached against the last-good manifest and the cache
 * folder's own stats, so reading it here costs nothing new). A shot-by-shot
 * reason list and a Re-render button with its own count, or -- the episode
 * cannot be rendered right now (an outdated shot's image, the same sentence
 * `render.require_renderable` gives the route and the estimate) -- that
 * sentence instead of a button certain to 409 (stage-10 lesson). Renders
 * nothing before the episode has a finished render to compare against
 * (`changes` is null then: no "Changes since last render" before a first
 * render).
 */
function ChangesSinceRender({ storyId, ep, episode, busy, onChange }) {
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const changes = episode.render && episode.render.changes

  if (!changes) return null

  const handleRerender = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      await runStoryStep(storyId, 'rerender', { ep })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="card story-render-changes">
      <h4 className="card-title">Changes since last render</h4>
      {changes.blocked ? (
        <p className="form-hint">{changes.blocked}</p>
      ) : changes.current ? (
        <p className="form-hint">Nothing changed since the last render: re-rendering would repeat it.</p>
      ) : (
        <>
          <ul className="story-field-list">
            {changes.rebuild.map((shotId) => (
              <li key={shotId}>
                {shotId}: {REUSE_REASON_LABELS[changes.reasons[shotId]] || changes.reasons[shotId]}
              </li>
            ))}
          </ul>
          <div className="story-step-actions">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleRerender}
              disabled={busy || running}
            >
              {running ? <><span className="spinner"></span> Re-rendering…</> : 'Re-render'}
            </button>
            <p className="chip chip-accent story-render-reuse-summary">
              {changes.summary} · {changes.reuse.length} reused
            </p>
          </div>
        </>
      )}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// -------------------------------------------------------------- metadata

function MetadataHeader({ storyId, ep, episode, renderReady, busy, onChange }) {
  const hasMetadata = Boolean(episode.metadata && episode.metadata.pack)
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  // The metadata step's own precondition (an approved script, a finished
  // render: metadata.require_render) answers a 409 naming what to do first
  // once a render exists but the script's approval cleared under it (a
  // text-only edit, stage 7) -- renderReady stays true (the old render.output
  // is still there), so the fetch still runs and must not swallow the
  // refusal: EstimateChip renders "estimating…" forever for a null estimate,
  // whatever the reason it stayed null (browser-check finding, stage-10
  // lesson applied here too).
  const [estimateError, setEstimateError] = useState('')

  useEffect(() => {
    setEstimate(null)
    setEstimateError('')
    if (!renderReady) return
    fetchStoryEstimate(storyId, 'metadata', { ep })
      .then((data) => { setEstimate(data); setEstimateError('') })
      .catch((err) => { setEstimate(null); setEstimateError(err.message) })
  }, [storyId, ep, renderReady])

  const reason = busy ? 'A step is running.' : !renderReady ? 'Render the episode first.' : null

  const handleRun = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      const metadataParams = {}
      await runStoryStep(storyId, 'metadata', { ep, params: metadataParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="card story-metadata-header">
      <h4 className="card-title">Metadata</h4>
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleRun}
          disabled={Boolean(reason) || Boolean(estimateError) || running}
          title={reason || estimateError || undefined}
        >
          {running ? <><span className="spinner"></span> Writing…</> : hasMetadata ? 'Write remaining metadata' : 'Write metadata'}
        </button>
        {estimateError ? (
          <span className="chip chip-warn chip-wrap">{estimateError}</span>
        ) : (
          <EstimateChip estimate={estimate} />
        )}
      </div>
      {reason && <p className="form-hint">{reason}</p>}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

function PlatformCard({ storyId, ep, platform, entry, stale, metadataBlocked, busy, onChange }) {
  const hashtags = entry.hashtags || []
  const hashtagsEn = entry.hashtags_en || []

  const regenerate = async (note) => {
    await regenerateStory(storyId, { target: `metadata:${ep}:${platform}`, note })
    onChange()
  }

  return (
    <div className="card story-metadata-card">
      <div className="story-metadata-card-header">
        <span className="chip chip-accent">{PLATFORM_LABELS[platform] || platform}</span>
        {stale && <span className="chip chip-warn">stale</span>}
      </div>

      <div className="story-field">
        <div className="story-field-label">Title</div>
        <p className="story-field-value">{entry.title}</p>
        <CopyButton text={entry.title} label="title" />
      </div>

      {entry.title_en && (
        <div className="story-field">
          <div className="story-field-label">Title (EN)</div>
          <p className="story-field-value">{entry.title_en}</p>
          <CopyButton text={entry.title_en} label="EN title" />
        </div>
      )}

      <div className="story-field">
        <div className="story-field-label">Description</div>
        <p className="story-field-value">{entry.description}</p>
        <CopyButton text={entry.description} label="description" />
      </div>

      <div className="story-field">
        <div className="story-field-label">Hashtags</div>
        <div className="story-metadata-hashtags">
          {hashtags.map((tag) => <span key={tag} className="chip">{tag}</span>)}
        </div>
        <CopyButton text={hashtags.join(' ')} label="hashtags" />
      </div>

      {hashtagsEn.length > 0 && (
        <div className="story-field">
          <div className="story-field-label">Hashtags (EN)</div>
          <div className="story-metadata-hashtags">
            {hashtagsEn.map((tag) => <span key={tag} className="chip">{tag}</span>)}
          </div>
          <CopyButton text={hashtagsEn.join(' ')} label="EN hashtags" />
        </div>
      )}

      <div className="story-field">
        <div className="story-field-label">Hook text</div>
        <p className="story-field-value">{entry.hook_text}</p>
        <CopyButton text={entry.hook_text} label="hook text" />
      </div>

      <div className="story-field">
        <div className="story-field-label">Pinned comment</div>
        <p className="story-field-value">{entry.pinned_comment}</p>
        <CopyButton text={entry.pinned_comment} label="pinned comment" />
      </div>

      <RegenerateControl disabled={busy || Boolean(metadataBlocked)} onRegenerate={regenerate} />
    </div>
  )
}

function CoverImage({ episode }) {
  const render = episode.render
  if (!render || !render.media || !render.media.cover_url) return null
  return (
    <div className="card story-render-cover">
      <h4 className="card-title">Cover</h4>
      <img className="story-render-cover-image" src={render.media.cover_url} alt="Episode cover" />
    </div>
  )
}

// ------------------------------------------------------------------ ledger

function LedgerTable({ episode }) {
  const ledger = episode.ledger
  if (!ledger) return null
  return (
    <div className="card story-ledger">
      <h4 className="card-title">Cost ledger</h4>
      {ledger.entries.length === 0 ? (
        <p className="form-hint">No spending recorded for this episode yet.</p>
      ) : (
        <div className="story-ledger-rows">
          {ledger.entries.map((row, i) => (
            <div key={i} className="story-ledger-row">
              <span className="chip">{row.step}</span>
              <span className="story-ledger-cell">{row.provider}/{row.model}</span>
              <span className="story-ledger-cell">{row.qty} {row.unit}{row.qty === 1 ? '' : 's'}</span>
              <span className="story-ledger-cell">${formatUsd(row.est_usd)}</span>
              <span className={`chip${row.paid ? ' chip-warn' : ''}`}>{row.paid ? 'paid' : 'free'}</span>
              {row.note && <span className="form-hint">{row.note}</span>}
            </div>
          ))}
        </div>
      )}
      <div className="story-ledger-totals">
        <span className="chip">{ledger.totals.entries} row{ledger.totals.entries === 1 ? '' : 's'}</span>
        <span className="chip">est. ${formatUsd(ledger.totals.est_usd)}</span>
        <span className="chip">paid ${formatUsd(ledger.totals.paid_usd)}</span>
      </div>
    </div>
  )
}

// --------------------------------------------------------------------- page

export default function PreviewPane({ episode, storyId, ep, story, inFlightJob, onChange }) {
  const busy = Boolean(inFlightJob)
  // assets.fingerprint is only ever "current" once the assets are approved
  // with today's files (workflow.assets_approval_state: "none" covers "never
  // approved" too) -- the same reading StoryboardPane.jsx's ApproveAssets uses.
  const assetsApproved = Boolean(episode.assets && episode.assets.fingerprint === 'current')
  const renderReady = Boolean(episode.render && episode.render.output)
  const metadataPlatforms = episode.metadata && episode.metadata.pack ? episode.metadata.pack.platforms : null
  const frenchStory = Boolean(story && story.story && story.story.language === 'fr')
  // F8 (phase 5 stage 13b): metadata.require_render's own refusal sentence
  // right now, or null -- single-sourced (workflow.episode_view), so a
  // platform's metadata regenerate is disabled instead of round-tripping
  // into a 409 that spends nothing.
  const metadataBlocked = episode.state.metadata_regenerate_blocked

  return (
    <div className="story-step-body">
      <RenderHeader storyId={storyId} ep={ep} episode={episode} assetsApproved={assetsApproved} busy={busy} onChange={onChange} />
      <RenderMedia episode={episode} assetsApproved={assetsApproved} onChange={onChange} />
      <ChangesSinceRender storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />

      <MetadataHeader storyId={storyId} ep={ep} episode={episode} renderReady={renderReady} busy={busy} onChange={onChange} />
      {frenchStory && !metadataPlatforms && renderReady && (
        <p className="form-hint">A French story's cards also carry an English title and hashtags.</p>
      )}

      {metadataPlatforms ? (
        <>
          <CoverImage episode={episode} />
          {metadataBlocked && <p className="form-hint story-metadata-regenerate-blocked">{metadataBlocked}</p>}
          <div className="story-metadata-cards">
            {PLATFORMS.filter((platform) => metadataPlatforms[platform]).map((platform) => (
              <PlatformCard
                key={platform}
                storyId={storyId}
                ep={ep}
                platform={platform}
                entry={metadataPlatforms[platform]}
                stale={!episode.metadata.current}
                metadataBlocked={metadataBlocked}
                busy={busy}
                onChange={onChange}
              />
            ))}
          </div>
        </>
      ) : (
        <div className="card story-metadata-empty">
          <p className="form-hint">
            {renderReady ? 'No metadata written yet — click Write metadata above.'
              : 'Render the episode first, then write its metadata.'}
          </p>
        </div>
      )}

      <LedgerTable episode={episode} />
    </div>
  )
}

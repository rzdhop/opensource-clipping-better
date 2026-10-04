import { useEffect, useState } from 'react'
import { fetchStoryEstimate, patchStory, switchPipeline } from '../../api'
import RouteChip from '../../components/RouteChip'
import { StepError } from './fields'
import { formatUsd } from '../../lib/format'
import { useConfirm } from '../../ui'
import { stepLabel } from './storySteps'

// The story's "Visual tier" card (phase 6), moved out of the old wizard shell
// by the story workspace (dashboard overhaul stage 3, DEC-255): the header
// opens it as a popover. Same props, same calls, same text.

const ROUTES = ['auto', 'local', 'api']
// Plan 22: a native-speech story's speaking-clip models (generation_profile.speech_model).
const SPEECH_MODELS = { lite: 'Lite (Veo 3.1 lite)', fast: 'Fast (Veo 3.1 Fast)', premium: 'Premium (Veo 3.1)' }
// Plan 23 stage D4: how a character's sheets are drawn (generation_profile.sheet_mode; absent = three_sheet).
const SHEET_MODES = {
  three_sheet: 'Three sheets (portrait, turnaround, expressions)',
  two_view: 'One front + back sheet',
  two_view_expressions: 'Front + back sheet and expressions',
}
// Plan 23 stage D4: how the bodies are drawn (generation_profile.body_rule; absent = the style's own rules).
const BODY_RULES = { '': 'As the style draws them', all_matter: "All skin is the character's matter" }

/**
 * The episode the Visual tier card prices its video estimate for
 * (browser-check finding F5): the LATEST episode with an approved
 * storyboard -- the assets step can actually run on it -- else (no
 * episode has one yet) the next one to work on: the first with no timed
 * script yet (`total_s` is the script's own timing.total_s,
 * workflow.episode_summaries), else the one after the last created
 * episode, else episode 1. The estimate itself still answers with its own
 * refusal sentence when even that fallback episode is not ready for it.
 */
export function pricedEpisode(episodesList) {
  const approvedStoryboardEpisodes = episodesList.filter((entry) => entry.storyboard_state === 'approved')
  const latestApproved = approvedStoryboardEpisodes.length > 0
    ? approvedStoryboardEpisodes.reduce((latest, entry) => (entry.ep > latest.ep ? entry : latest))
    : null
  const unfinishedEpisode = episodesList.find((entry) => entry.total_s == null)
  return latestApproved ? latestApproved.ep
    : unfinishedEpisode ? unfinishedEpisode.ep
    : episodesList.length > 0 ? episodesList[episodesList.length - 1].ep + 1 : 1
}

// clipping.aistory.workflow.PIPELINE_SWITCH_HAS_SCRIPTS: PATCH's 409 when a
// pipeline switch meets episodes that already have a script (its detail names
// them: `episodes`). tests/test_dashboard_switch_pipeline.py checks the value.
const PIPELINE_SWITCH_HAS_SCRIPTS = 'pipeline_switch_has_scripts'

/** "episode 1", "episodes 1–3", "episodes 1, 3". */
function episodesLabel(episodes) {
  const eps = [...episodes].sort((a, b) => a - b)
  if (eps.length === 1) return `episode ${eps[0]}`
  const contiguous = eps.every((ep, i) => i === 0 || ep === eps[i - 1] + 1)
  return `episodes ${contiguous ? `${eps[0]}–${eps[eps.length - 1]}` : eps.join(', ')}`
}

function pipelineLabel(patch) {
  return patch.pipeline === 'v2' ? 'v2' : 'the legacy pipeline'
}

/** The confirm before `switchPipeline` archives *episodes*: what goes, what stays, what runs next. */
function regenerateConfirm(episodes, patch) {
  const what = episodesLabel(episodes)
  const next = patch.pipeline === 'v2'
    ? 'Next: the Cast step starts right away, writing each character\'s dossier and look and drawing again, '
      + 'from the look, the portrait and sheets drawn before it. Then Places & props (their looks; the plates '
      + 'and prop images drawn again), the knowledge base, then the episode -- each of those shows its '
      + 'estimate first, as usual.'
    : 'Next: write the episode again; each step shows its estimate first, as usual.'
  return [
    `Regenerate ${what} on ${pipelineLabel(patch)}?`,
    'Archived (moved to episodes/_discarded/ on the server, never deleted): '
      + `the script, storyboard, images, clips and render of ${what}, `
      + 'with its series memory, audience feedback and the proposals written from it. '
      + 'What it already cost stays in the story\'s total, not in the new episode\'s.',
    'Kept: the cast, places, props, season and music.',
    next,
  ].join('\n\n')
}

/** What `switchPipeline` did: the episodes archived, then the step it queued or why that step could not start. */
function switchedSummary(result) {
  const parts = []
  const archived = (result.discarded || []).map((report) => report.ep)
  if (archived.length > 0) parts.push(`Archived ${episodesLabel(archived)}.`)
  const next = result.next_step
  if (next) {
    const label = stepLabel(next.step)
    if (next.job) parts.push(`The ${label} step is queued.`)
    else if (next.refused) parts.push(`The ${label} step could not start: ${next.refused}`)
  }
  return parts.join(' ') || 'The story is on the new pipeline.'
}

/**
 * The story's generation profile's visual half: tier (1 stills + motion, 2
 * image-to-video, 3 + native audio) and route (auto/local/api), saved
 * through `PATCH /api/stories/{id}` (`generation_profile` is merged onto
 * the current values, so sending only the changed field leaves the rest
 * alone). Under it, *nextEp*'s assets run priced on each route in turn
 * (`GET /estimate/assets?route=`, phase 6 stage 11 -- a preview, it patches
 * nothing): clips/seconds and either the local ETA or the paid cost, the
 * link, or the refusal sentence when the episode is not ready for it yet
 * (no script/storyboard approved, no video link ready, ...). *nextEp* is
 * the latest episode with an approved storyboard, so this priced episode
 * usually has something to show rather than "no script yet" (browser-check
 * finding F5); see the caller for the fallback when none does.
 */
export default function GenerationProfileCard({ storyId, story, nextEp, onChange }) {
  const confirm = useConfirm()
  // What the server holds, as last fetched: the selects start from it, follow
  // it whenever the story is fetched again, and go back to it when a save is
  // refused (they used to keep showing values the server never saved).
  const profile = story.generation_profile
  const [tier, setTier] = useState(profile.tier)
  const [route, setRoute] = useState(profile.route)
  // Phase 7 stage 7 (browser-check finding F7): the budget profile -- what a
  // story may buy and how many shots it animates -- is chosen here too.
  const [budgetProfile, setBudgetProfile] = useState(profile.budget_profile)
  // Plan 22: the per-story switch of a native-speech story's speaking clips (absent: the profile's, fast).
  const [speechModel, setSpeechModel] = useState(profile.speech_model || 'fast')
  // Plan 23 stage D4: the characters' sheets and bodies (absent: three sheets, the style's own rules).
  const [sheetMode, setSheetMode] = useState(profile.sheet_mode || 'three_sheet')
  const [bodyRule, setBodyRule] = useState(profile.body_rule || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  // A pipeline switch refused over written episodes (PATCH's structured 409):
  // {episodes, patch}, what the "Regenerate on v2" button sends.
  const [switchOffer, setSwitchOffer] = useState(null)
  const [switched, setSwitched] = useState(null)
  const [routeEstimates, setRouteEstimates] = useState({})
  const [routeErrors, setRouteErrors] = useState({})

  useEffect(() => {
    setTier(profile.tier)
    setRoute(profile.route)
    setBudgetProfile(profile.budget_profile)
  }, [profile.tier, profile.route, profile.budget_profile])

  useEffect(() => { setSpeechModel(profile.speech_model || 'fast') }, [profile.speech_model])
  useEffect(() => { setSheetMode(profile.sheet_mode || 'three_sheet') }, [profile.sheet_mode])
  useEffect(() => { setBodyRule(profile.body_rule || '') }, [profile.body_rule])

  const save = async (patch) => {
    setSaving(true)
    setError('')
    setSwitchOffer(null)
    setSwitched(null)
    try {
      await patchStory(storyId, { generation_profile: patch })
      onChange()
    } catch (err) {
      // Nothing was saved: back to the server's values, and fetch the story again.
      setTier(profile.tier)
      setRoute(profile.route)
      setBudgetProfile(profile.budget_profile)
      setSpeechModel(profile.speech_model || 'fast')
      setSheetMode(profile.sheet_mode || 'three_sheet')
      setBodyRule(profile.body_rule || '')
      setError(err.message)
      if (err.status === 409 && err.code === PIPELINE_SWITCH_HAS_SCRIPTS && err.detail.episodes) {
        setSwitchOffer({ episodes: err.detail.episodes, patch })
      }
      onChange()
    } finally {
      setSaving(false)
    }
  }

  // "Regenerate episode N on v2": the same profile, the written episodes
  // archived first (POST /switch-pipeline); the server queues the step the
  // story needs next (the cast's dossiers, looks and redraws, ...).
  const regenerate = async () => {
    if (!switchOffer) return
    const confirmed = await confirm({
      title: `Regenerate ${episodesLabel(switchOffer.episodes)}`,
      message: regenerateConfirm(switchOffer.episodes, switchOffer.patch),
      confirmLabel: 'Regenerate',
    })
    if (!confirmed) return
    setSaving(true)
    setError('')
    try {
      const result = await switchPipeline(storyId, { generation_profile: switchOffer.patch, regenerate_episodes: true })
      setSwitchOffer(null)
      setSwitched(result)
      onChange()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const handleTier = (value) => { setTier(value); save({ tier: value }) }
  const handleRoute = (value) => { setRoute(value); save({ route: value }) }
  const handleBudgetProfile = (value) => {
    setBudgetProfile(value)
    // Plan 22: native speech is a v2 story at tier 3 (its clips speak the lines); stage 5: so is its
    // manual twin, every clip your own upload.
    if (value === 'native_speech' || value === 'native_speech_manual') {
      setTier(3)
      save({ budget_profile: value, tier: 3, route: 'api', ...(isV2 ? {} : { pipeline: 'v2', consistency_mode: 'references' }) })
    } else {
      save({ budget_profile: value })
    }
  }
  const handleSpeechModel = (value) => { setSpeechModel(value); save({ speech_model: value }) }
  // The default is no key at all (null clears it): a story that never chose keeps its documents as they were.
  const handleSheetMode = (value) => { setSheetMode(value); save({ sheet_mode: value === 'three_sheet' ? null : value }) }
  const handleBodyRule = (value) => { setBodyRule(value); save({ body_rule: value || null }) }

  // Every shot a clip: the quality budget profile (animate all_shots) at tier
  // >= 2 on the api route, on the v2 pipeline (the server sets its template and
  // narrator, and refuses the switch once an episode has a script:
  // workflow._follow_pipeline_switch -- its sentence shows as the card's error,
  // with the "Regenerate on v2" button that archives those episodes).
  const isV2 = story.generation_profile.pipeline === 'v2'
  const canSwitchToV2 = !isV2
  const hasCast = (story.cast_ids || []).length > 0
  const manualClips = story.generation_profile.budget_profile === 'native_speech_manual'
  const nativeSpeech = story.generation_profile.budget_profile === 'native_speech' || manualClips
  const imagesOwn = story.generation_profile.images === 'manual'
  // The style's rules are written into its lock once, when it is approved.
  const styleLocked = Boolean(story.approvals && story.approvals.style)
  const fullyAnimated = isV2 && (story.generation_profile.budget_profile === 'quality' || nativeSpeech) && tier >= 2
  const makeFullyAnimated = () => {
    const patch = { tier: Math.max(tier, 2), route: 'api', budget_profile: 'quality' }
    if (canSwitchToV2) Object.assign(patch, { pipeline: 'v2', consistency_mode: 'references' })
    setTier(patch.tier)
    setRoute(patch.route)
    setBudgetProfile(patch.budget_profile)
    save(patch)
  }

  useEffect(() => {
    if (tier < 2 || !nextEp) { setRouteEstimates({}); setRouteErrors({}); return undefined }
    let cancelled = false
    setRouteEstimates({})
    setRouteErrors({})
    ROUTES.forEach((r) => {
      fetchStoryEstimate(storyId, 'assets', { ep: nextEp, route: r })
        .then((data) => { if (!cancelled) setRouteEstimates((prev) => ({ ...prev, [r]: data })) })
        .catch((err) => { if (!cancelled) setRouteErrors((prev) => ({ ...prev, [r]: err.message })) })
    })
    return () => { cancelled = true }
  }, [storyId, tier, budgetProfile, nextEp])

  return (
    <div className="card story-generation-profile" style={{ marginBottom: '16px' }}>
      <h3 className="card-title">Visual tier</h3>
      <div className="form-group">
        {fullyAnimated ? (
          <span className="chip">
            {nativeSpeech ? 'Native speech: every character line is spoken by its own clip.'
              : 'Fully animated: every shot is a video clip.'}
          </span>
        ) : (
          <>
            <p className="form-hint">
              {canSwitchToV2
                ? 'This story is not fully animated yet. Switch it to the quality pipeline: 6–10 shots, each a '
                  + 'video clip, with quality images (billed).'
                  + (hasCast ? ' Then run the Cast step and Places & props again: they write each character\'s, '
                    + 'place\'s and prop\'s look and draw again, from it, the images drawn before it (each '
                    + 'estimate shows the cost).' : '')
                : 'Some shots of this story stay still. Animate every shot with the quality budget profile (billed).'}
            </p>
            <button type="button" className="btn btn-sm btn-primary" onClick={makeFullyAnimated} disabled={saving}>
              Animate every shot
            </button>
          </>
        )}
      </div>
      <div className="form-group">
        <label className="form-label" htmlFor="story-profile-tier">Tier</label>
        <select id="story-profile-tier" className="form-select" value={tier} onChange={(e) => handleTier(Number(e.target.value))} disabled={saving}>
          <option value={1}>1 — stills + motion</option>
          <option value={2}>2 — image-to-video</option>
          <option value={3}>3 — + native audio (experimental)</option>
        </select>
      </div>
      <div className="form-group">
        <label className="form-label" htmlFor="story-profile-route">Route</label>
        <select id="story-profile-route" className="form-select" value={route} onChange={(e) => handleRoute(e.target.value)} disabled={saving}>
          <option value="auto">Auto</option>
          <option value="local">Local</option>
          <option value="api">API</option>
        </select>
      </div>
      <div className="form-group">
        <label className="form-label" htmlFor="story-profile-budget">Budget profile</label>
        <select id="story-profile-budget" className="form-select" value={budgetProfile} onChange={(e) => handleBudgetProfile(e.target.value)}
          disabled={saving}>
          <option value="free">Free (no clip bought)</option>
          <option value="one_dollar">$1 / episode (key shots)</option>
          <option value="quality">Quality (billed APIs) — every shot animated</option>
          <option value="native_speech">Native speech (Veo) — characters speak in their clips</option>
          <option value="native_speech_manual">Native speech — your own clips (Flow / Higgsfield)</option>
        </select>
      </div>
      {manualClips && (
        <label className="story-checkbox">
          <input type="checkbox" checked={imagesOwn} disabled={saving}
            onChange={(e) => save({ images: e.target.checked ? 'manual' : null })} />
          My own images too (sheets, plates, props and keyframes: upload them on their tiles)
        </label>
      )}
      {isV2 && (
        <div className="form-group">
          <label className="form-label" htmlFor="story-profile-sheet-mode">Character sheets</label>
          <select id="story-profile-sheet-mode" className="form-select" value={sheetMode}
            onChange={(e) => handleSheetMode(e.target.value)} disabled={saving}>
            {Object.entries(SHEET_MODES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
          <p className="form-hint">
            Sheets drawn from now on; regenerate a character's images to draw it again in the new mode.
          </p>
        </div>
      )}
      <div className="form-group">
        <label className="form-label" htmlFor="story-profile-body-rule">Bodies</label>
        <select id="story-profile-body-rule" className="form-select" value={bodyRule}
          onChange={(e) => handleBodyRule(e.target.value)} disabled={saving || styleLocked}>
          {Object.entries(BODY_RULES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
        {styleLocked && <p className="form-hint">Written into the style when it was approved: it cannot change now.</p>}
      </div>
      {nativeSpeech && !manualClips && (
        <div className="form-group">
          <label className="form-label" htmlFor="story-profile-speech-model">Speaking clips</label>
          <select id="story-profile-speech-model" className="form-select" value={speechModel}
            onChange={(e) => handleSpeechModel(e.target.value)} disabled={saving}>
            {Object.entries(SPEECH_MODELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
        </div>
      )}
      <StepError message={error} />
      {switchOffer && (
        <div className="story-step-actions">
          <button type="button" className="btn btn-sm btn-primary" onClick={regenerate} disabled={saving}>
            {`Regenerate ${episodesLabel(switchOffer.episodes)} on ${pipelineLabel(switchOffer.patch)}`}
          </button>
        </div>
      )}
      {switched && <p className="form-hint">{switchedSummary(switched)}</p>}
      {tier >= 2 && (
        <div className="story-generation-profile-routes">
          <p className="form-hint">Episode {nextEp}'s video estimate, per route:</p>
          {ROUTES.map((r) => {
            const est = routeEstimates[r]
            const err = routeErrors[r]
            return (
              <div key={r} className="story-step-actions" style={{ marginBottom: '6px' }}>
                <span className="chip">{r}</span>
                {err ? (
                  <span className="chip chip-warn chip-wrap">{err}</span>
                ) : est && est.video ? (
                  <>
                    <span className="chip" title={est.video.message || ''}>
                      {est.video.count != null ? `${est.video.count} clip${est.video.count === 1 ? '' : 's'}` : 'nothing to animate yet'}
                      {est.video.seconds != null ? ` · ${est.video.seconds.toFixed(1)} s` : ''}
                      {est.video.count != null ? (
                        est.video.route_class === 'local' && est.video.eta_s != null
                          ? ` · ~${Math.round(est.video.eta_s)} s local${est.video.eta_note ? ` (${est.video.eta_note})` : ''}`
                          : ` · $${formatUsd(est.video.est_usd)}`
                      ) : ''}
                    </span>
                    {est.video.link && <RouteChip routeClass={est.video.route_class} link={est.video.link} />}
                    {/* F6: a plan of 0 clips says why (the free profile, every shot kept still,
                        no link...), not only in the chip's tooltip. */}
                    {(!est.video.ready || est.video.count === 0) && est.video.message
                      && <p className="form-hint">{est.video.message}</p>}
                  </>
                ) : (
                  <span className="chip">estimating…</span>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

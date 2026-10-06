import { useEffect, useState } from 'react'
import { fetchSettings, fetchStoryEstimate, fetchUniverses, patchStory, switchPipeline } from '../../api'
import RouteChip from '../../components/RouteChip'
import { StepError } from './fields'
import { formatUsd } from '../../lib/format'
import { useConfirm, useToast } from '../../ui'
import { stepLabel } from './storySteps'
import HowMadeControls from './HowMadeControls'

// The story's "Visual tier" card (phase 6), moved out of the old wizard shell
// by the story workspace (dashboard overhaul stage 3, DEC-255): the header
// opens it as a popover. Same props, same calls, same text.

const ROUTES = ['auto', 'local', 'api']
const ROUTE_NAMES = { auto: 'automatic', local: 'this computer', api: 'paid services' }
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
// Plan 23 stage A9: which provider the images try first (generation_profile.image_preference; absent = fal first).
const IMAGE_PREFERENCES = { '': 'fal first (default)', gemini_first: 'Gemini first' }

// Plan 23 stage B7: the story's frame (generation_profile.aspect; absent = 9:16), chosen when the story was
// made: shown here, never edited (PATCH answers 409).
const FRAMES = { '9:16': 'Vertical 9:16', '16:9': 'Landscape 16:9', '1:1': 'Square 1:1' }

// Plan 23 stage D6: how a clip's prompt is written (generation_profile.prompt_style; absent = studio).
const PROMPT_STYLES = {
  studio: 'studio (default)',
  action: 'one continuous action (Flow / Seedance style)',
}

// Plan 23 stage B8: stock cutaways (generation_profile.stock_cutaways; absent = off). Stock footage is
// live-action: only the photoreal style matches it, every other style gets the hint.
const STOCK_CUTAWAYS = { '': 'Off (default)', on: 'On: stock footage fills establishing shots' }
const STOCK_MATCHING_STYLES = ['cinematic_real']

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
  return patch.pipeline === 'v2' ? 'the animated format' : 'the older format'
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
  return parts.join(' ') || 'The story is now in the new format.'
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
  const toast = useToast()
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
  // Plan 23 stage A9: the image provider preference; Gemini first needs the paid key (GET /api/settings).
  const [imagePreference, setImagePreference] = useState(profile.image_preference || '')
  const [geminiKeySet, setGeminiKeySet] = useState(false)
  // Plan 23 stage D2: what the cast is made of -- the profile's universe, else the style's default
  // (shown, not edited here: it is chosen when the story is created and frozen with the style).
  const [universeCatalogue, setUniverseCatalogue] = useState(null)
  // Plan 23 stage D6: how a clip's prompt is written (absent: studio, today's prompts).
  const [promptStyle, setPromptStyle] = useState(profile.prompt_style || 'studio')
  // Plan 23 stage B8: fill establishing wide shots with stock footage (absent: off); patchable any time.
  const [stockCutaways, setStockCutaways] = useState(profile.stock_cutaways || '')
  // Plan 25 stage 5: the profile the card held before "My own" clips, what "Auto" goes back to.
  const [profileBeforeManual, setProfileBeforeManual] = useState('')
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

  useEffect(() => {
    let cancelled = false
    fetchUniverses().then((data) => { if (!cancelled) setUniverseCatalogue(data) }).catch(() => {})
    fetchSettings().then((data) => { if (!cancelled) setGeminiKeySet(Boolean(data.gemini_paid_api_key_set)) }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  useEffect(() => { setSpeechModel(profile.speech_model || 'fast') }, [profile.speech_model])
  useEffect(() => { setSheetMode(profile.sheet_mode || 'three_sheet') }, [profile.sheet_mode])
  useEffect(() => { setBodyRule(profile.body_rule || '') }, [profile.body_rule])
  useEffect(() => { setImagePreference(profile.image_preference || '') }, [profile.image_preference])
  useEffect(() => { setPromptStyle(profile.prompt_style || 'studio') }, [profile.prompt_style])
  useEffect(() => { setStockCutaways(profile.stock_cutaways || '') }, [profile.stock_cutaways])

  const save = async (patch) => {
    setSaving(true)
    setError('')
    setSwitchOffer(null)
    setSwitched(null)
    try {
      const saved = await patchStory(storyId, { generation_profile: patch })
      // Plan 23 stage D6: a clip-prompt style change answers with the current clips it makes stale.
      if (saved && saved.warning) toast.info(saved.warning, { duration: 12000 })
      onChange()
    } catch (err) {
      // Nothing was saved: back to the server's values, and fetch the story again.
      setTier(profile.tier)
      setRoute(profile.route)
      setBudgetProfile(profile.budget_profile)
      setSpeechModel(profile.speech_model || 'fast')
      setSheetMode(profile.sheet_mode || 'three_sheet')
      setBodyRule(profile.body_rule || '')
      setImagePreference(profile.image_preference || '')
      setPromptStyle(profile.prompt_style || 'studio')
      setStockCutaways(profile.stock_cutaways || '')
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
  // Plan 25 stage 5: "How clips are made" -- My own is the native_speech_manual profile (the same PATCH as the
  // select); Auto goes back to the profile the card held before, else native_speech. A running step answers 409
  // and save() puts every control back to the server's values.
  const handleClipsOwn = (own) => {
    if (own === (budgetProfile === 'native_speech_manual')) return
    if (own) {
      setProfileBeforeManual(budgetProfile)
      handleBudgetProfile('native_speech_manual')
    } else {
      handleBudgetProfile(profileBeforeManual || 'native_speech')
    }
  }
  // "How images are made": My own is images: 'manual' (a v2 story only), Auto clears it.
  const handleImagesOwn = (own) => { save(own ? { images: 'manual' } : { images: null }) }
  const handleSpeechModel = (value) => { setSpeechModel(value); save({ speech_model: value }) }
  // The default is no key at all (null clears it): a story that never chose keeps its documents as they were.
  const handleSheetMode = (value) => { setSheetMode(value); save({ sheet_mode: value === 'three_sheet' ? null : value }) }
  const handleBodyRule = (value) => { setBodyRule(value); save({ body_rule: value || null }) }
  const handleImagePreference = (value) => { setImagePreference(value); save({ image_preference: value || null }) }
  const handlePromptStyle = (value) => { setPromptStyle(value); save({ prompt_style: value === 'studio' ? null : value }) }
  const handleStockCutaways = (value) => { setStockCutaways(value); save({ stock_cutaways: value || null }) }

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
  const styleUniverses = universeCatalogue && universeCatalogue.by_style[story.style_template_id]
  const universeId = profile.universe || (styleUniverses ? styleUniverses.default : null)
  const universeEntry = universeId && universeCatalogue
    ? universeCatalogue.universes.find((universe) => universe.id === universeId) : null
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
      <h3 className="card-title">How this story is made</h3>
      <p className="form-hint">Who makes the clips and the images. The rest is under Advanced.</p>
      <HowMadeControls
        clipsOwn={budgetProfile === 'native_speech_manual'}
        imagesOwn={isV2 && imagesOwn}
        imagesDisabled={!isV2}
        imagesNote="Your own images need the animated format: switch this story to it first."
        disabled={saving}
        onClips={handleClipsOwn}
        onImages={handleImagesOwn}
      />
      <p className="form-hint">
        <strong>Clips: {manualClips ? 'my own' : 'auto'} · Images: {isV2 && imagesOwn ? 'my own' : 'auto'}</strong>
      </p>
      <div className="form-group">
        {fullyAnimated ? (
          <span className="chip">
            {nativeSpeech ? 'Every character line is spoken by its own clip.'
              : 'Fully animated: every shot is a video clip.'}
          </span>
        ) : (
          <>
            <p className="form-hint">
              {canSwitchToV2
                ? 'This story is not fully animated yet. Switch it to the animated format: 6–10 shots, each a '
                  + 'video clip, with quality images (billed).'
                  + (hasCast ? ' Then run the Cast step and Places & props again: they write each character\'s, '
                    + 'place\'s and prop\'s look and draw again, from it, the images drawn before it (each '
                    + 'estimate shows the cost).' : '')
                : 'Some shots of this story stay still. Animate every shot with the Quality spending plan (billed).'}
            </p>
            <button type="button" className="btn btn-sm btn-primary" onClick={makeFullyAnimated} disabled={saving}>
              Animate every shot
            </button>
          </>
        )}
      </div>
      <div className="form-group">
        <label className="form-label" htmlFor="story-profile-tier">Video level</label>
        <select id="story-profile-tier" className="form-select" value={tier} onChange={(e) => handleTier(Number(e.target.value))} disabled={saving}>
          <option value={1}>1 — pictures with motion</option>
          <option value={2}>2 — pictures turned into video clips</option>
          <option value={3}>3 — clips with their own sound (experimental)</option>
        </select>
      </div>
      <div className="form-group">
        <label className="form-label" htmlFor="story-profile-route">Where it is made</label>
        <select id="story-profile-route" className="form-select" value={route} onChange={(e) => handleRoute(e.target.value)} disabled={saving}>
          <option value="auto">Automatic</option>
          <option value="local">This computer</option>
          <option value="api">Paid services</option>
        </select>
      </div>
      <div className="form-group">
        <label className="form-label" htmlFor="story-profile-budget">Spending plan</label>
        <select id="story-profile-budget" className="form-select" value={budgetProfile} onChange={(e) => handleBudgetProfile(e.target.value)}
          disabled={saving}>
          <option value="free">Free (no clip bought)</option>
          <option value="one_dollar">About $1 per episode (key shots)</option>
          <option value="quality">Quality (paid) — every shot animated</option>
          <option value="native_speech">Characters speak in their clips (paid, Veo)</option>
          <option value="native_speech_manual">Characters speak in your own clips (Flow / Higgsfield)</option>
        </select>
      </div>
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
      {isV2 && !imagesOwn && (
        <div className="form-group">
          <label className="form-label" htmlFor="story-profile-image-preference">Image provider</label>
          <select id="story-profile-image-preference" className="form-select" value={imagePreference}
            onChange={(e) => handleImagePreference(e.target.value)}
            disabled={saving || (!geminiKeySet && imagePreference === '')}>
            {Object.entries(IMAGE_PREFERENCES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
          <p className="form-hint">
            {geminiKeySet
              ? 'Images made from now on try this provider first; an episode already on a provider keeps it.'
              : 'Add GEMINI_PAID_API_KEY in Settings to draw the images on Gemini first.'}
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
      {universeEntry && (
        <div className="form-group">
          <span className="form-label">Cast world</span>
          <p className="story-profile-universe">{universeEntry.label[story.language] || universeEntry.label.en}</p>
          {universeEntry.audience_note && (
            <p className="form-hint">{universeEntry.audience_note[story.language] || universeEntry.audience_note.en}</p>
          )}
        </div>
      )}
      <div className="form-group">
        <span className="form-label">Frame</span>
        <p className="story-profile-frame">{FRAMES[profile.aspect || '9:16']}</p>
        <p className="form-hint">Chosen when the story was made: its plates, keyframes and clips are made at it.</p>
      </div>
      {isV2 && (
        <div className="form-group">
          <label className="form-label" htmlFor="story-profile-prompt-style">Clip prompts</label>
          <select id="story-profile-prompt-style" className="form-select" value={promptStyle}
            onChange={(e) => handlePromptStyle(e.target.value)} disabled={saving}>
            {Object.entries(PROMPT_STYLES).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select>
          <p className="form-hint">
            Changing it rewrites every clip prompt: the clips already made (uploads included) go stale.
          </p>
        </div>
      )}
      <div className="form-group">
        <label className="form-label" htmlFor="story-profile-stock-cutaways">Stock cutaways</label>
        <select id="story-profile-stock-cutaways" className="form-select" value={stockCutaways}
          onChange={(e) => handleStockCutaways(e.target.value)} disabled={saving}>
          {Object.entries(STOCK_CUTAWAYS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}
        </select>
        <p className="form-hint">
          {STOCK_MATCHING_STYLES.includes(story.style_template_id)
            ? 'At the next assets run, wide establishing shots with no character are filled with free stock footage; an image or a clip already there is never replaced.'
            : 'Stock footage is live-action: it matches only the photoreal style, so on this one the cutaways will look out of place.'}
        </p>
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
          <p className="form-hint">Episode {nextEp}'s video estimate, by where it is made:</p>
          {ROUTES.map((r) => {
            const est = routeEstimates[r]
            const err = routeErrors[r]
            return (
              <div key={r} className="story-step-actions" style={{ marginBottom: '6px' }}>
                <span className="chip">{ROUTE_NAMES[r] || r}</span>
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

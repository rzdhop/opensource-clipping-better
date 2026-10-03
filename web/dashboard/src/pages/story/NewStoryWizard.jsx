import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  createStory, fetchNewStoryProfile, fetchStory, fetchStoryEstimate, fetchStyles, patchStory, styleNameOf,
  switchPipeline,
} from '../../api'
import { LiveActivity, useJobFeed } from '../../components/ActivityFeed'
import RouteChip from '../../components/RouteChip'
import { StepError } from './fields'
import ConceptsStep from './steps/ConceptsStep'
import BibleStep from './steps/BibleStep'
import StyleStep from './steps/StyleStep'
import CastStep from './steps/CastStep'
import PlacesStep from './steps/PlacesStep'
import SeasonStep from './steps/SeasonStep'
import KnowledgeStep, { knowledgeState } from './steps/KnowledgeStep'

// Same sub-cent formatting as the episode panes' fmtUsd (duplicated: this
// page shares no component module with them).
function fmtUsd(value) {
  const amount = Number(value) || 0
  return amount === 0 ? '0.00' : amount.toFixed(3)
}

const ROUTES = ['auto', 'local', 'api']

// The story defaults (spec 8, 8.1, 8.5): a story that does not name every
// one of these gets exactly these values. tests/test_story_defaults.py reads
// this literal as text and checks it against
// clipping.aistory.defaults.default_generation_profile().
const DEFAULT_GENERATION_PROFILE = { tier: 1, route: 'auto', consistency_mode: 'references', budget_profile: 'free' }

const STEPS = [
  { key: 'concepts', number: 2, label: 'Concepts' },
  { key: 'bible', number: 3, label: 'Bible' },
  { key: 'style', number: 4, label: 'Style' },
  { key: 'cast', number: 5, label: 'Cast' },
  { key: 'places', number: 6, label: 'Places & props' },
  { key: 'season', number: 7, label: 'Season' },
  // Phase 7 stage 5b (DEC-228): a v2 story only (stepsFor).
  { key: 'knowledge', number: 8, label: 'Knowledge base' },
]

// clipping.aistory.defaults.PIPELINE_V2: only a v2 story has a knowledge base.
function stepsFor(story) {
  const v2 = Boolean(story && story.generation_profile && story.generation_profile.pipeline === 'v2')
  return STEPS.filter((s) => s.key !== 'knowledge' || v2)
}

const IN_FLIGHT = ['queued', 'running']
const STORY_POLL_MS = 4000

function statusOf(key, story, data) {
  if (key === 'concepts') return story.approvals.concept ? 'done' : 'active'
  if (key === 'bible') {
    if (story.approvals.bible) return 'done'
    return story.approvals.concept ? 'active' : 'disabled'
  }
  if (key === 'style') {
    if (story.approvals.style) return 'done'
    return story.approvals.bible ? 'active' : 'disabled'
  }
  if (key === 'cast') {
    if (story.approvals.cast) return 'done'
    return story.approvals.style ? 'active' : 'disabled'
  }
  if (key === 'places') {
    if (story.approvals.places) return 'done'
    return story.approvals.cast ? 'active' : 'disabled'
  }
  if (key === 'season') {
    if (story.approvals.season) return 'done'
    return story.approvals.places ? 'active' : 'disabled'
  }
  if (key === 'knowledge') {
    // Done only while approved and current (approved_rev === rev): the
    // episode gate's own rule (episode_common.knowledge_state).
    if (knowledgeState(data && data.knowledge) === 'approved') return 'done'
    return story.approvals.season ? 'active' : 'disabled'
  }
  return 'disabled'
}

function disabledReason(key) {
  if (key === 'bible') return 'Choose a concept first.'
  if (key === 'style') return 'Approve the bible first.'
  if (key === 'cast') return 'Approve the style first.'
  if (key === 'places') return 'Approve the cast first.'
  if (key === 'season') return 'Approve every place and prop first.'
  if (key === 'knowledge') return 'Approve the season first.'
  return ''
}

function summaryFor(key, story, data) {
  if (key === 'concepts') return `Chosen: ${(story.concept && story.concept.title) || 'Untitled concept'}`
  if (key === 'bible') return story.logline || 'Bible written.'
  if (key === 'style') {
    const lock = data.style_lock
    if (!lock) return 'No style yet.'
    const name = lock.template_name && (lock.template_name.en || lock.template_name.fr)
    return `${name || lock.template_id} — locked`
  }
  if (key === 'cast') {
    const count = (data.characters || []).length
    return count ? `${count} character${count === 1 ? '' : 's'} in the cast.` : 'No cast yet.'
  }
  if (key === 'places') {
    const placeCount = (data.places || []).length
    const propCount = (data.props || []).length
    if (!placeCount && !propCount) return 'No places yet.'
    return `${placeCount} place${placeCount === 1 ? '' : 's'}, ${propCount} prop${propCount === 1 ? '' : 's'}.`
  }
  if (key === 'season') {
    const season = data.season
    return season && season.episodes_planned ? `${season.episodes_planned} episodes planned.` : 'No season yet.'
  }
  if (key === 'knowledge') {
    const timeline = (data.knowledge && data.knowledge.timeline) || []
    return `Approved: ${timeline.length} episode${timeline.length === 1 ? '' : 's'} of beats.`
  }
  return ''
}

// --------------------------------------------------------------- creation

function CreateStoryForm() {
  const navigate = useNavigate()
  // No default: a language a user forgot to pick must never silently become
  // one, matching StoryCreateRequest and the CLI's --lang.
  const [language, setLanguage] = useState(null)
  const [seedText, setSeedText] = useState('')
  const [styleTemplateId, setStyleTemplateId] = useState('')
  const [styles, setStyles] = useState([])
  const [showProfile, setShowProfile] = useState(false)
  const [tier, setTier] = useState(DEFAULT_GENERATION_PROFILE.tier)
  const [route, setRoute] = useState(DEFAULT_GENERATION_PROFILE.route)
  const [consistencyMode, setConsistencyMode] = useState(DEFAULT_GENERATION_PROFILE.consistency_mode)
  const [budgetProfile, setBudgetProfile] = useState(DEFAULT_GENERATION_PROFILE.budget_profile)
  const [pipeline, setPipeline] = useState('')
  // What the server gives a story created now (GET /api/stories/new-profile):
  // the quality preset -- v2, every shot animated -- when FAL_KEY is set.
  const [offer, setOffer] = useState(null)
  // False until the user changes a profile field: an untouched form sends no
  // profile, so the server's choice applies (it used to send a v1, free,
  // tier-1 profile over it, and a dashboard story could never animate).
  const [profileChosen, setProfileChosen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    fetchStyles().then((data) => { if (!cancelled) setStyles(data.styles || []) }).catch(() => {})
    fetchNewStoryProfile().then((data) => {
      if (cancelled) return
      setOffer(data)
      const profile = data.profile || {}
      setTier(profile.tier ?? DEFAULT_GENERATION_PROFILE.tier)
      setRoute(profile.route || DEFAULT_GENERATION_PROFILE.route)
      setConsistencyMode(profile.consistency_mode || DEFAULT_GENERATION_PROFILE.consistency_mode)
      setBudgetProfile(profile.budget_profile || DEFAULT_GENERATION_PROFILE.budget_profile)
      setPipeline(profile.pipeline || '')
    }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  // Every profile control goes through this, so a change marks the profile chosen.
  const choose = (setter) => (value) => { setProfileChosen(true); setter(value) }
  const handlePipeline = (value) => {
    setProfileChosen(true)
    setPipeline(value)
    // A v2 story's images are edits of its references (DEC-221).
    if (value === 'v2') setConsistencyMode('references')
  }
  const fullyAnimated = pipeline === 'v2' && budgetProfile === 'quality' && tier >= 2
  // What the quality preset costs (media_policy.preset_estimate, from the
  // server's price table; phase 7 stage 7): shown whether or not it is the
  // default yet, so a missing key is weighed against a price.
  const estimate = offer && offer.estimate

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!language) return
    setCreating(true)
    setError('')
    try {
      // The story's own request payload, one key per line: the payload
      // contract test reads this block as text and checks each key is a
      // field of StoryCreateRequest.
      const createFields = {
        language,
        seed_text: seedText.trim() ? seedText : null,
        style_template_id: styleTemplateId || null,
        // Untouched, null: the server picks (media_policy.new_story_profile).
        generation_profile: profileChosen ? {
          tier,
          route,
          consistency_mode: consistencyMode,
          budget_profile: budgetProfile,
          ...(pipeline ? { pipeline } : {}),
        } : null,
      }
      const story = await createStory(createFields)
      navigate(`/story/${story.story_id}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="fade-in">
      <div className="page-header">
        <div>
          <h2>New story</h2>
          <p>A persistent workspace: world, style lock and a season arc.</p>
        </div>
      </div>

      <form onSubmit={handleSubmit}>
        <div className="card" style={{ marginBottom: '16px' }}>
          <div className="form-group">
            <label className="form-label">Language</label>
            <div className="story-segmented">
              <button
                type="button"
                className={`btn btn-sm ${language === 'fr' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setLanguage('fr')}
              >
                Français
              </button>
              <button
                type="button"
                className={`btn btn-sm ${language === 'en' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setLanguage('en')}
              >
                English
              </button>
            </div>
            <p className="form-hint">Required — nothing is picked for you.</p>
          </div>

          <div className="form-group">
            <label className="form-label">Seed text (optional)</label>
            <textarea
              className="form-input"
              rows={4}
              maxLength={2000}
              placeholder="A rough idea, a scene, a vibe — anything to seed the concepts."
              value={seedText}
              onChange={(e) => setSeedText(e.target.value)}
            />
            <p className="form-hint">{seedText.length} / 2000</p>
          </div>

          <div className="form-group">
            <label className="form-label">Style (optional)</label>
            <div className="story-style-grid">
              <button
                type="button"
                className={`story-style-pick${styleTemplateId === '' ? ' active' : ''}`}
                onClick={() => setStyleTemplateId('')}
              >
                <span className="story-style-pick-name">Decide later</span>
              </button>
              {styles.map((style) => (
                <button
                  key={style.template_id}
                  type="button"
                  className={`story-style-pick${styleTemplateId === style.template_id ? ' active' : ''}`}
                  onClick={() => setStyleTemplateId(style.template_id)}
                >
                  <span className="story-swatch-row">
                    {style.palette.primary.slice(0, 3).map((hex, i) => (
                      <span key={i} className="story-swatch" style={{ background: hex }} />
                    ))}
                  </span>
                  <span className="story-style-pick-name">{style.name.en}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="form-group">
            {fullyAnimated ? (
              <p className="chip chip-wrap">
                Fully animated: every shot is a video clip, with quality images (Quality — billed APIs)
                {estimate ? `: ${estimate.summary}.` : '.'}
              </p>
            ) : (
              <p className="chip chip-warn chip-wrap">
                Not fully animated: {tier < 2 ? 'tier 1 is stills with motion' : budgetProfile !== 'quality'
                  ? `the ${budgetProfile} budget profile does not animate every shot`
                  : 'the legacy pipeline keeps the old shot layout and image links'}.
                {offer && !offer.quality && offer.missing_keys && offer.missing_keys.length > 0
                  ? ` Add ${offer.missing_keys.join(', ')} in Settings to start stories fully animated`
                    + (estimate ? `: ${estimate.summary}.` : '.') : ''}
              </p>
            )}
            {estimate && (fullyAnimated || (offer && !offer.quality)) && (
              <p className="form-hint">{estimate.assumptions}</p>
            )}
            {fullyAnimated && offer && !offer.allow_paid && (
              <p className="form-hint">
                Paid calls are off (Settings → allow paid): no image or clip is bought until you turn them on.
              </p>
            )}
          </div>

          <details className="story-profile" open={showProfile} onToggle={(e) => setShowProfile(e.target.open)}>
            <summary>Generation profile</summary>
            <div className="story-profile-grid">
              <div className="form-group">
                <label className="form-label">Pipeline</label>
                <select className="form-select" value={pipeline} onChange={(e) => handlePipeline(e.target.value)}>
                  <option value="v2">v2 — quality (6–10 shots, each a clip)</option>
                  <option value="">Legacy (v1)</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Tier</label>
                <select className="form-select" value={tier} onChange={(e) => choose(setTier)(Number(e.target.value))}>
                  <option value={1}>1 — stills + motion (slideshow)</option>
                  <option value={2}>2 — animated (image-to-video)</option>
                  <option value={3}>3 — animated + model sound</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Route</label>
                <select className="form-select" value={route} onChange={(e) => choose(setRoute)(e.target.value)}>
                  <option value="auto">Auto</option>
                  <option value="local">Local</option>
                  <option value="api">API</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Consistency mode</label>
                <select className="form-select" value={consistencyMode}
                  onChange={(e) => choose(setConsistencyMode)(e.target.value)}>
                  <option value="references">References</option>
                  <option value="prompt_only" disabled={pipeline === 'v2'}>Prompt only</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Budget profile</label>
                <select className="form-select" value={budgetProfile}
                  onChange={(e) => choose(setBudgetProfile)(e.target.value)}>
                  <option value="free">Free (no clip bought)</option>
                  <option value="one_dollar">$1 / episode (key shots)</option>
                  <option value="quality">Quality (billed APIs) — every shot animated</option>
                </select>
              </div>
            </div>
          </details>
        </div>

        {error && <p className="story-error">{error}</p>}

        <button type="submit" className="btn btn-primary" disabled={!language || creating}>
          {creating ? <><span className="spinner"></span> Creating…</> : 'Create story'}
        </button>
      </form>
    </div>
  )
}

// ------------------------------------------------------------- an existing story

function StoryJobActivity({ jobId }) {
  const { job, events, streamState } = useJobFeed(jobId)
  if (!job) return null
  return <LiveActivity job={job} events={events} streamState={streamState} />
}

function StoryJobList({ jobs }) {
  const [openId, setOpenId] = useState(null)
  if (!jobs || jobs.length === 0) return null

  return (
    <div className="card story-job-list-card">
      <h3 className="card-title">Step jobs</h3>
      <div className="story-job-list">
        {jobs.slice().reverse().map((job) => (
          <div key={job.id} className="story-job-row">
            <button
              type="button"
              className="story-job-row-header"
              onClick={() => setOpenId(openId === job.id ? null : job.id)}
            >
              <span className="story-job-row-step">
                {job.step}
                {job.params && job.params.target ? ` · ${job.params.target}` : ''}
              </span>
              <span className="chip">
                {job.status === 'queued' ? 'queued — waiting for the worker' : job.status}
              </span>
              <span className="story-job-row-time">{new Date(job.updated_at).toLocaleTimeString()}</span>
            </button>
            {openId === job.id && <StoryJobActivity jobId={job.id} />}
          </div>
        ))}
      </div>
    </div>
  )
}

// ----------------------------------------------------------- visual tier (phase 6)

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
    const label = (STEPS.find((s) => s.key === next.step) || { label: next.step }).label
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
function GenerationProfileCard({ storyId, story, nextEp, onChange }) {
  // What the server holds, as last fetched: the selects start from it, follow
  // it whenever the story is fetched again, and go back to it when a save is
  // refused (they used to keep showing values the server never saved).
  const profile = story.generation_profile
  const [tier, setTier] = useState(profile.tier)
  const [route, setRoute] = useState(profile.route)
  // Phase 7 stage 7 (browser-check finding F7): the budget profile -- what a
  // story may buy and how many shots it animates -- is chosen here too.
  const [budgetProfile, setBudgetProfile] = useState(profile.budget_profile)
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
    if (!switchOffer || !window.confirm(regenerateConfirm(switchOffer.episodes, switchOffer.patch))) return
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
  const handleBudgetProfile = (value) => { setBudgetProfile(value); save({ budget_profile: value }) }

  // Every shot a clip: the quality budget profile (animate all_shots) at tier
  // >= 2 on the api route, on the v2 pipeline (the server sets its template and
  // narrator, and refuses the switch once an episode has a script:
  // workflow._follow_pipeline_switch -- its sentence shows as the card's error,
  // with the "Regenerate on v2" button that archives those episodes).
  const isV2 = story.generation_profile.pipeline === 'v2'
  const canSwitchToV2 = !isV2
  const hasCast = (story.cast_ids || []).length > 0
  const fullyAnimated = isV2 && story.generation_profile.budget_profile === 'quality' && tier >= 2
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
          <span className="chip">Fully animated: every shot is a video clip.</span>
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
        <label className="form-label">Tier</label>
        <select className="form-select" value={tier} onChange={(e) => handleTier(Number(e.target.value))} disabled={saving}>
          <option value={1}>1 — stills + motion</option>
          <option value={2}>2 — image-to-video</option>
          <option value={3}>3 — + native audio (experimental)</option>
        </select>
      </div>
      <div className="form-group">
        <label className="form-label">Route</label>
        <select className="form-select" value={route} onChange={(e) => handleRoute(e.target.value)} disabled={saving}>
          <option value="auto">Auto</option>
          <option value="local">Local</option>
          <option value="api">API</option>
        </select>
      </div>
      <div className="form-group">
        <label className="form-label">Budget profile</label>
        <select className="form-select" value={budgetProfile} onChange={(e) => handleBudgetProfile(e.target.value)}
          disabled={saving}>
          <option value="free">Free (no clip bought)</option>
          <option value="one_dollar">$1 / episode (key shots)</option>
          <option value="quality">Quality (billed APIs) — every shot animated</option>
        </select>
      </div>
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
                          : ` · $${fmtUsd(est.video.est_usd)}`
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

function ExistingStory({ storyId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [manualStep, setManualStep] = useState(null)
  const [styles, setStyles] = useState([])
  const stepRefs = useRef({})
  const scrollPending = useRef(false)

  const refresh = useCallback(async () => {
    try {
      const fresh = await fetchStory(storyId)
      setData(fresh)
      setLoadError('')
    } catch (err) {
      setLoadError(err.message)
    } finally {
      setLoading(false)
    }
  }, [storyId])

  useEffect(() => {
    fetchStyles().then((d) => setStyles(d.styles || [])).catch(() => {})
  }, [])

  useEffect(() => {
    setLoading(true)
    setData(null)
    setManualStep(null)
    // Covers a create's redirect here too (finding 4): the page the browser
    // is scrolled to does not reset on a client-side route change, so the
    // freshly active step can otherwise land off-screen.
    scrollPending.current = true
    refresh()
  }, [storyId, refresh])

  // After an action advances the story (a concept chosen, a document
  // approved) or a fresh story page loads, scroll the newly active step's
  // header into view -- otherwise, on a phone, the step that just unlocked
  // can be a full screen away from wherever the page happened to be
  // scrolled (spec 10 finding 4).
  useEffect(() => {
    if (!scrollPending.current) return
    scrollPending.current = false
    if (!data) return
    const activeStep = stepsFor(data.story).find((s) => statusOf(s.key, data.story, data) === 'active')
    const key = manualStep || (activeStep ? activeStep.key : 'cast')
    const el = stepRefs.current[key]
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  // While the story has a step job queued or running, poll the story itself.
  // The step feeds can miss the moment a job finishes -- a refresh can land
  // between the step's last write and the job's flip to awaiting_approval,
  // and the feed has stopped by then -- which left a finished preview shown
  // as "Generating…" (Tier-2, 2026-09-26). This makes the page converge.
  const inFlightId = data
    ? ((data.jobs || []).find((j) => IN_FLIGHT.includes(j.status)) || {}).id || null
    : null
  useEffect(() => {
    if (!inFlightId) return
    const timer = setInterval(refresh, STORY_POLL_MS)
    return () => clearInterval(timer)
  }, [inFlightId, refresh])

  const afterAction = () => {
    setManualStep(null)
    refresh()
  }

  const afterAdvance = () => {
    scrollPending.current = true
    setManualStep(null)
    refresh()
  }

  // Coordinator fix attempt 2 (F1, phase 5 stage 13b, live walk): a series
  // panel action (memory, feedback, propose-next, a proposal decision, its
  // own "Approve proposals") -- and the Season step's own job feed -- must
  // never collapse the step. Every other step's afterAction closing on
  // success is fine (there is somewhere to advance to); once the season is
  // approved nothing is 'active' any more, so clearing manualStep here fell
  // back to 'cast' the instant a series action succeeded.
  const afterSeriesAction = () => {
    refresh()
  }

  if (loading) {
    return <div className="fade-in"><div className="empty-state"><span className="spinner"></span></div></div>
  }
  if (loadError) {
    return <div className="fade-in"><div className="card"><p className="story-error">{loadError}</p></div></div>
  }
  if (!data) return null

  const { story } = data
  const inFlightJob = data.jobs.find((j) => IN_FLIGHT.includes(j.status)) || null
  const defaultExpanded = stepsFor(story).find((s) => statusOf(s.key, story, data) === 'active')
  const expanded = manualStep || (defaultExpanded ? defaultExpanded.key : 'cast')
  // Keyed on the server's own derived status (store.derive_status), not
  // approvals.season alone: a voice regeneration can clear a character's
  // approval, which clears approvals.cast and drops the status back to
  // style_approved even though approvals.season was never touched -- the
  // card must not keep claiming the story is ready once that happens.
  const allDone = story.status === 'ready'
  // The episode the Visual tier card prices its video estimate for
  // (browser-check finding F5): the LATEST episode with an approved
  // storyboard -- the assets step can actually run on it -- else (no
  // episode has one yet) the next one to work on: the first with no timed
  // script yet (`total_s` is the script's own timing.total_s,
  // workflow.episode_summaries), else the one after the last created
  // episode, else episode 1. The estimate itself still answers with its own
  // refusal sentence when even that fallback episode is not ready for it.
  const episodesList = data.episodes || []
  const approvedStoryboardEpisodes = episodesList.filter((entry) => entry.storyboard_state === 'approved')
  const latestApproved = approvedStoryboardEpisodes.length > 0
    ? approvedStoryboardEpisodes.reduce((latest, entry) => (entry.ep > latest.ep ? entry : latest))
    : null
  const unfinishedEpisode = episodesList.find((entry) => entry.total_s == null)
  const nextEp = latestApproved ? latestApproved.ep
    : unfinishedEpisode ? unfinishedEpisode.ep
    : episodesList.length > 0 ? episodesList[episodesList.length - 1].ep + 1 : 1

  return (
    <div className="fade-in">
      <div className="page-header">
        <div>
          <h2>{story.title || 'Untitled story'}</h2>
          <p>{story.language === 'fr' ? 'Français' : 'English'}{story.style_template_id ? ` · ${styleNameOf(styles, story.style_template_id)}` : ''}</p>
        </div>
      </div>

      <GenerationProfileCard storyId={storyId} story={story} nextEp={nextEp} onChange={refresh} />

      <div className="stepper">
        <div className="stepper-step stepper-step-done">
          <div className="stepper-step-header">
            <span className="stepper-step-index">✓</span>
            <span className="stepper-step-label">New story</span>
          </div>
          <div className="stepper-summary">
            {story.language === 'fr' ? 'French' : 'English'}
            {story.seed_text ? ' · seed text set' : ''}
          </div>
        </div>

        {stepsFor(story).map((step) => {
          const status = statusOf(step.key, story, data)
          const reopenable = status === 'done' && step.key !== 'style'
          const isOpen = status !== 'disabled' && expanded === step.key
          const clickable = status === 'active' || reopenable

          return (
            <div
              key={step.key}
              ref={(el) => { stepRefs.current[step.key] = el }}
              className={`stepper-step stepper-step-${status}`}
            >
              <button
                type="button"
                className="stepper-step-header"
                disabled={!clickable}
                onClick={() => clickable && setManualStep(isOpen ? null : step.key)}
              >
                <span className="stepper-step-index">{status === 'done' ? '✓' : step.number}</span>
                <span className="stepper-step-label">{step.label}</span>
                {status === 'disabled' && <span className="stepper-step-reason">{disabledReason(step.key)}</span>}
                {reopenable && !isOpen && <span className="stepper-step-edit">Edit</span>}
              </button>

              {status === 'done' && !isOpen && (
                <div className="stepper-summary">{summaryFor(step.key, story, data)}</div>
              )}

              {isOpen && (
                <div className="stepper-step-body">
                  {step.key === 'concepts' && (
                    <ConceptsStep storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {step.key === 'bible' && (
                    <BibleStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {step.key === 'style' && (
                    <StyleStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {step.key === 'cast' && (
                    <CastStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {step.key === 'places' && (
                    <PlacesStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {step.key === 'season' && (
                    <SeasonStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onSeriesChange={afterSeriesAction} onAdvance={afterAdvance} />
                  )}
                  {step.key === 'knowledge' && (
                    <KnowledgeStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterSeriesAction} />
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {allDone && (
        <div className="card story-ready-card" style={{ marginBottom: '16px' }}>
          <h3 className="card-title">Ready</h3>
          <p>The story is ready: bible, style, cast, places and a planned season.</p>
          <Link to={`/story/${storyId}/episodes/1`} className="story-ready-open-episode">
            Open episode 1 →
          </Link>
          {data.episodes.length > 0 && (
            <ul className="story-field-list story-ready-episodes">
              {data.episodes.map((entry) => (
                <li key={entry.ep}>
                  <Link to={`/story/${storyId}/episodes/${entry.ep}`}>
                    Episode {entry.ep}
                  </Link>
                  {': '}{entry.script_state}
                  {entry.total_s != null ? ` · ${entry.total_s.toFixed(1)} s` : ''}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <StoryJobList jobs={data.jobs} />
    </div>
  )
}

// --------------------------------------------------------------------- page

export default function NewStoryWizard() {
  const { storyId } = useParams()
  if (!storyId) return <CreateStoryForm />
  return <ExistingStory storyId={storyId} />
}

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createStory, fetchNewStoryProfile, fetchStyles } from '../../api'
import { EPISODE_TEMPLATES, pipelineDefaultTemplate, styleSuggestedTemplate } from './episodeTemplates'

// The new-story form (`/story/new`). An existing story opens in the story
// workspace (StoryWorkspace.jsx, dashboard overhaul stage 3, DEC-255).

// The story defaults (spec 8, 8.1, 8.5): a story that does not name every
// one of these gets exactly these values. tests/test_story_defaults.py reads
// this literal as text and checks it against
// clipping.aistory.defaults.default_generation_profile().
const DEFAULT_GENERATION_PROFILE = { tier: 1, route: 'auto', consistency_mode: 'references', budget_profile: 'free' }

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
  // The episode format the user picked; '' until they pick one, so the
  // select follows the style's suggestion, else the pipeline's default.
  const [episodeTemplateChoice, setEpisodeTemplateChoice] = useState('')
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
  // Plan 20 stage 1: a style suggests an episode format
  // (episode_defaults.episode_template_id -- Fruit Drama the narrated drama)
  // when it fits the pipeline; the story keeps whichever is sent.
  const chosenStyle = styles.find((style) => style.template_id === styleTemplateId)
  const suggestedTemplate = styleSuggestedTemplate(chosenStyle, pipeline)
  const episodeTemplateId = episodeTemplateChoice || suggestedTemplate || pipelineDefaultTemplate(pipeline)
  const episodeFormat = EPISODE_TEMPLATES.find((tpl) => tpl.id === episodeTemplateId)

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
        // Picked or suggested by the style; else null: the server starts the
        // story on its pipeline's format (defaults.episode_template_for).
        episode_template_id: episodeTemplateChoice || suggestedTemplate || null,
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
            <label className="form-label" htmlFor="new-story-episode-format">Episode format</label>
            <select
              id="new-story-episode-format"
              className="form-select"
              value={episodeTemplateId}
              onChange={(e) => setEpisodeTemplateChoice(e.target.value)}
            >
              {EPISODE_TEMPLATES.map((tpl) => <option key={tpl.id} value={tpl.id}>{tpl.label}</option>)}
            </select>
            <p className="form-hint">
              {episodeFormat ? episodeFormat.help : ''}
              {!episodeTemplateChoice && suggestedTemplate ? ' Suggested by the style.' : ''}
            </p>
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

// --------------------------------------------------------------------- page

export default function NewStoryWizard() {
  return <CreateStoryForm />
}

import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createStory, fetchNewStoryProfile, fetchStyles } from '../../api'
import { Button } from '../../ui'
import { EPISODE_TEMPLATES, pipelineDefaultTemplate, styleSuggestedTemplate } from './episodeTemplates'

// Plan 21 decision 4: the Mode choice's own label, Studio first (the
// default).
const MODE_HELP = {
  studio: 'Approve each step yourself.',
  agent: ('One run from the idea to episode 1; the agent approves the style, the cast and the places as soon as '
    + 'they are complete -- no taste check; review them in Studio afterwards, every regenerate stays available.'),
}

// The new-story form (`/story/new`). An existing story opens in the story
// workspace (StoryWorkspace.jsx, dashboard overhaul stage 3, DEC-255).

// The story defaults (spec 8, 8.1, 8.5): a story that does not name every
// one of these gets exactly these values. tests/test_story_defaults.py reads
// this literal as text and checks it against
// clipping.aistory.defaults.default_generation_profile().
const DEFAULT_GENERATION_PROFILE = { tier: 1, route: 'auto', consistency_mode: 'references', budget_profile: 'free' }

// Plan 22: the native-speech profile's speaking-clip models (its
// speech_links), cheapest first; the profile's own default is fast.
const SPEECH_MODELS = [
  { id: 'lite', label: 'Lite (Veo 3.1 lite)' },
  { id: 'fast', label: 'Fast (Veo 3.1 Fast)' },
  { id: 'premium', label: 'Premium (Veo 3.1)' },
]

function CreateStoryForm() {
  const navigate = useNavigate()
  // No default: a language a user forgot to pick must never silently become
  // one, matching StoryCreateRequest and the CLI's --lang.
  const [language, setLanguage] = useState(null)
  const [mode, setMode] = useState('studio')
  const [seedText, setSeedText] = useState('')
  const [styleTemplateId, setStyleTemplateId] = useState('')
  const [styles, setStyles] = useState([])
  const [showProfile, setShowProfile] = useState(false)
  const [tier, setTier] = useState(DEFAULT_GENERATION_PROFILE.tier)
  const [route, setRoute] = useState(DEFAULT_GENERATION_PROFILE.route)
  const [consistencyMode, setConsistencyMode] = useState(DEFAULT_GENERATION_PROFILE.consistency_mode)
  const [budgetProfile, setBudgetProfile] = useState(DEFAULT_GENERATION_PROFILE.budget_profile)
  const [pipeline, setPipeline] = useState('')
  const [speechModel, setSpeechModel] = useState('fast')
  // Plan 22 stage 5: on "your own clips", the sheets, plates, props and keyframes may be yours too.
  const [imagesOwn, setImagesOwn] = useState(false)
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
  // Plan 22: the native-speech profile is a v2 story at tier 3 -- each character line spoken by its own clip.
  // Stage 5: its manual twin (every clip your own upload, from the shot brief) is the default with the keys set.
  const manualClips = budgetProfile === 'native_speech_manual'
  const nativeSpeech = budgetProfile === 'native_speech' || manualClips
  const handleBudgetProfile = (value) => {
    setProfileChosen(true)
    setBudgetProfile(value)
    if (value === 'native_speech' || value === 'native_speech_manual') {
      setPipeline('v2')
      setTier(3)
      setConsistencyMode('references')
    }
  }
  const fullyAnimated = pipeline === 'v2' && (budgetProfile === 'quality' || nativeSpeech) && tier >= 2
  // What the native-speech profile costs per speaking-clip model, and the keys it still needs.
  const speech = offer && (manualClips ? offer.native_speech_manual : offer.native_speech)
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
        // Plan 21 stage 3: the Mode choice below -- studio (every step waits
        // for your approval, the default) or agent (one story-fast-track job
        // approves by rule).
        mode,
        // Untouched, null: the server picks (media_policy.new_story_profile).
        generation_profile: profileChosen ? {
          tier,
          route,
          consistency_mode: consistencyMode,
          budget_profile: budgetProfile,
          ...(pipeline ? { pipeline } : {}),
          ...(nativeSpeech ? { speech_model: speechModel } : {}),
          ...(manualClips && imagesOwn ? { images: 'manual' } : {}),
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
            <label className="form-label">Mode</label>
            <div className="story-segmented" role="group" aria-label="Mode">
              <Button
                type="button"
                variant={mode === 'studio' ? 'primary' : 'secondary'}
                size="sm"
                onClick={() => setMode('studio')}
              >
                Studio
              </Button>
              <Button
                type="button"
                variant={mode === 'agent' ? 'primary' : 'secondary'}
                size="sm"
                onClick={() => setMode('agent')}
              >
                Agent
              </Button>
            </div>
            <p className="form-hint">{MODE_HELP[mode]}</p>
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
            {manualClips ? (
              <p className="chip chip-wrap">
                Native speech — your own clips: each character line is spoken on camera by its own clip, which you
                make on Google Flow or Higgsfield from the app's shot brief and upload; the app writes, draws the
                keyframes, times the subtitles and renders{speech ? `: ${speech.summary}.` : '.'}
              </p>
            ) : nativeSpeech ? (
              <p className="chip chip-wrap">
                Native speech (Veo): each character line is spoken on camera by its own clip, lips and voice one
                take; the narrator stays a voice-over{speech ? `: ${speech.summary}.` : '.'}
              </p>
            ) : fullyAnimated ? (
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
            {nativeSpeech && speech && (
              <>
                <p className="form-hint">{speech.assumptions}</p>
                {(speech.missing_keys.length > 0 || speech.stt_missing_keys.length > 0) && (
                  <p className="form-hint">
                    Missing in Settings: {[...speech.missing_keys,
                      ...(speech.stt_missing_keys.length > 0
                        ? [`${speech.stt_missing_keys.join(' or ')} (the speech check)`] : [])].join(', ')}.
                  </p>
                )}
              </>
            )}
            {!nativeSpeech && estimate && (fullyAnimated || (offer && !offer.quality)) && (
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
                  onChange={(e) => handleBudgetProfile(e.target.value)}>
                  <option value="free">Free (no clip bought)</option>
                  <option value="one_dollar">$1 / episode (key shots)</option>
                  <option value="quality">Quality (billed APIs) — every shot animated</option>
                  <option value="native_speech">Native speech (Veo) — characters speak in their clips</option>
                  <option value="native_speech_manual">Native speech — your own clips (Flow / Higgsfield)</option>
                </select>
              </div>
              {manualClips && (
                <label className="story-checkbox">
                  <input type="checkbox" checked={imagesOwn}
                    onChange={(e) => { setProfileChosen(true); setImagesOwn(e.target.checked) }} />
                  My own images too (sheets, plates, props and keyframes, from the image brief)
                </label>
              )}
              {nativeSpeech && !manualClips && (
                <div className="form-group">
                  <label className="form-label" htmlFor="new-story-speech-model">Speaking clips</label>
                  <select id="new-story-speech-model" className="form-select" value={speechModel}
                    onChange={(e) => choose(setSpeechModel)(e.target.value)}>
                    {SPEECH_MODELS.map((model) => {
                      const usd = speech && speech.by_model ? speech.by_model[model.id] : null
                      return (
                        <option key={model.id} value={model.id}>
                          {model.label}{usd != null ? ` — ≈ $${usd.toFixed(2)} an episode` : ''}
                        </option>
                      )
                    })}
                  </select>
                </div>
              )}
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

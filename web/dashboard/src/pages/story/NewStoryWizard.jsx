import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createStory, fetchNewStoryProfile, fetchSettings, fetchStyles, fetchUniverses } from '../../api'
import { Button } from '../../ui'
import HowMadeControls from './HowMadeControls'
import {
  EPISODE_TEMPLATES, pipelineDefaultTemplate, profileSuggestedTemplate, styleSuggestedTemplate,
} from './episodeTemplates'

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

// Plan 23 stage D4: how a character's reference sheets are drawn (generation_profile.sheet_mode),
// the story's default first; the price of each comes from the preset estimate
// (estimate.story.sheet_usd_by_mode).
const SHEET_MODES = [
  { id: 'three_sheet', label: 'Three sheets (portrait, turnaround, expressions)' },
  { id: 'two_view', label: 'One front + back sheet' },
  { id: 'two_view_expressions', label: 'Front + back sheet and expressions' },
]

// Plan 23 stage D4: how the characters' bodies are drawn (generation_profile.body_rule); '' sends nothing
// -- the style's own rules (today's).
const BODY_RULES = [
  { id: '', label: 'As the style draws them' },
  { id: 'all_matter', label: "All skin is the character's matter" },
]

// Plan 23 stage A9: which provider the images try first (generation_profile.image_preference); '' sends
// nothing -- the budget profile's order (fal first, today's). Gemini first needs GEMINI_PAID_API_KEY.
const IMAGE_PREFERENCES = [
  { id: '', label: 'fal first (default)' },
  { id: 'gemini_first', label: 'Gemini first' },
]

// Plan 23 stage D6: how a clip's prompt is written (generation_profile.prompt_style); the default (studio)
// sends nothing -- today's prompts.
const PROMPT_STYLES = [
  { id: 'studio', label: 'studio (default)' },
  { id: 'action', label: 'one continuous action (Flow / Seedance style)' },
]

// Plan 23 stage B7: the story's frame (generation_profile.aspect), chosen once, here; 9:16 sends nothing.
// The reasons a frame is disabled come from the server (offer.aspect_reasons, media_policy.aspect_reasons);
// what stays 9:16 in v1 is said under the select.
const FRAMES = [
  { id: '9:16', label: 'Vertical 9:16 (default)' },
  { id: '16:9', label: 'Landscape 16:9' },
  { id: '1:1', label: 'Square 1:1' },
]
const FRAME_HINT = ('The character sheets and the style preview stay 9:16 (references, not output). Landscape: a '
  + 'regular YouTube video, not Shorts. Square: Tier 1, or clips on Seedance or Kling (Veo, LTX, Flow and a local '
  + 'ComfyUI make no 1:1). The frame cannot change after the story is made.')

/** Why the frame `frame` cannot be picked with this profile, or '' (media_policy.aspect_refusal's rules). */
export function frameRefusal(frame, { pipeline, tier, route, budgetProfile, reasons }) {
  if (frame === '9:16') return ''
  const said = reasons || {}
  if (pipeline !== 'v2') return said.pipeline || 'a 16:9 or 1:1 frame is for a story on the v2 pipeline'
  if (tier < 2) return ''
  if (route === 'local') return said.local || 'local ComfyUI clips are 9:16 only (v1)'
  if (budgetProfile === 'free') return said.free || 'the free profile animates on a local ComfyUI only (9:16)'
  if (frame === '1:1' && budgetProfile === 'native_speech') return said.veo_square || 'Veo makes 9:16 and 16:9 only'
  if (frame === '1:1' && budgetProfile === 'native_speech_manual') {
    return said.manual_square || 'Google Flow makes 9:16 and 16:9 only'
  }
  return ''
}

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
  // Plan 22 stage 5: the sheets, plates, props and keyframes may be your own uploads (plan 25 stage 5: the
  // "How images are made" control; only a v2 story has manual images).
  const [imagesOwn, setImagesOwn] = useState(false)
  // Plan 25 stage 5: the profile the form had before "My own" clips, what "Auto" goes back to.
  const [profileBeforeManual, setProfileBeforeManual] = useState('')
  // Plan 23 stage D4: the characters' sheets and bodies; the defaults send nothing.
  const [sheetMode, setSheetMode] = useState('three_sheet')
  const [bodyRule, setBodyRule] = useState('')
  // Plan 23 stage A9: the image provider preference, and whether the paid Gemini key is set (GET /api/settings).
  const [imagePreference, setImagePreference] = useState('')
  const [geminiKeySet, setGeminiKeySet] = useState(false)
  // Plan 23 stage D2: what the cast is made of; '' sends nothing (the style's default universe).
  const [universeChoice, setUniverseChoice] = useState('')
  const [universeCatalogue, setUniverseCatalogue] = useState({ universes: [], by_style: {} })
  const [promptStyle, setPromptStyle] = useState('studio')
  const [frame, setFrame] = useState('9:16')
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
    fetchUniverses().then((data) => { if (!cancelled) setUniverseCatalogue(data) }).catch(() => {})
    fetchSettings().then((data) => { if (!cancelled) setGeminiKeySet(Boolean(data.gemini_paid_api_key_set)) }).catch(() => {})
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
  const imagesManual = pipeline === 'v2' && imagesOwn
  // Plan 25 stage 5: "How clips are made". My own is the native_speech_manual profile; Auto goes back to the
  // profile the form had, else native_speech (native speech is on), else the server's own default.
  const handleClipsOwn = (own) => {
    if (own === manualClips) return
    if (own) {
      setProfileBeforeManual(budgetProfile)
      handleBudgetProfile('native_speech_manual')
      return
    }
    const serverDefault = offer && offer.profile && offer.profile.budget_profile
    handleBudgetProfile(profileBeforeManual
      || (nativeSpeech ? 'native_speech' : (serverDefault && serverDefault !== 'native_speech_manual' ? serverDefault : 'free')))
  }
  const handleImagesOwn = (own) => { setProfileChosen(true); setImagesOwn(own) }
  const fullyAnimated = pipeline === 'v2' && (budgetProfile === 'quality' || nativeSpeech) && tier >= 2
  // What the native-speech profile costs per speaking-clip model, and the keys it still needs.
  const speech = offer && (manualClips ? offer.native_speech_manual : offer.native_speech)
  // What the quality preset costs (media_policy.preset_estimate, from the
  // server's price table; phase 7 stage 7): shown whether or not it is the
  // default yet, so a missing key is weighed against a price.
  const estimate = offer && offer.estimate
  // Plan 23 stage B7: a frame the profile cannot make is disabled with its reason; a pick that became
  // impossible falls back to 9:16 (what is sent is what the select shows).
  const frameReason = (id) => frameRefusal(id, {
    pipeline, tier, route, budgetProfile, reasons: offer && offer.aspect_reasons,
  })
  const shownFrame = frameReason(frame) ? '9:16' : frame
  // Plan 20 stage 1: a style suggests an episode format
  // (episode_defaults.episode_template_id -- Fruit Drama the narrated drama)
  // when it fits the pipeline; the story keeps whichever is sent. Plan 22
  // stage 3: a native-speech profile suggests the confrontation format first
  // (one shot per spoken line; defaults.episode_template_for), still a choice.
  const chosenStyle = styles.find((style) => style.template_id === styleTemplateId)
  const profileSuggestion = profileSuggestedTemplate(budgetProfile)
  const suggestedTemplate = profileSuggestion || styleSuggestedTemplate(chosenStyle, pipeline)
  const episodeTemplateId = episodeTemplateChoice || suggestedTemplate || pipelineDefaultTemplate(pipeline)
  const episodeFormat = EPISODE_TEMPLATES.find((tpl) => tpl.id === episodeTemplateId)
  // Plan 23 stage D2: the Universe select lists what the chosen style takes (hidden when it lists none);
  // a pick the style does not list is dropped, the style's default shows instead.
  const styleUniverses = universeCatalogue.by_style[styleTemplateId]
  const universeOptions = styleUniverses
    ? styleUniverses.universes
      .map((id) => universeCatalogue.universes.find((universe) => universe.id === id))
      .filter(Boolean)
    : []
  const universeId = universeOptions.some((universe) => universe.id === universeChoice) ? universeChoice : ''
  const shownUniverse = universeId || (styleUniverses ? styleUniverses.default : '') || ''
  const shownUniverseEntry = universeOptions.find((universe) => universe.id === shownUniverse)
  const universeLabel = (universe) => universe.label[language || 'en']
  // The select always shows a universe (the pick, else the style's default), so the story is created with
  // it: its profile is sent as the form shows it (the form starts from the server's own offer).
  useEffect(() => { if (shownUniverse) setProfileChosen(true) }, [shownUniverse])

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
          ...(imagesManual ? { images: 'manual' } : {}),
          ...(pipeline === 'v2' && sheetMode !== 'three_sheet' ? { sheet_mode: sheetMode } : {}),
          ...(bodyRule ? { body_rule: bodyRule } : {}),
          ...(pipeline === 'v2' && !imagesManual && imagePreference ? { image_preference: imagePreference } : {}),
          ...(shownUniverse ? { universe: shownUniverse } : {}),
          ...(pipeline === 'v2' && promptStyle !== 'studio' ? { prompt_style: promptStyle } : {}),
          ...(shownFrame !== '9:16' ? { aspect: shownFrame } : {}),
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

          <HowMadeControls
            clipsOwn={manualClips}
            imagesOwn={imagesManual}
            imagesDisabled={pipeline !== 'v2'}
            imagesNote="Your own images need the v2 pipeline (Generation profile → Pipeline)."
            onClips={handleClipsOwn}
            onImages={handleImagesOwn}
          />

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

          {universeOptions.length > 0 && (
            <div className="form-group">
              <label className="form-label" htmlFor="new-story-universe">Universe</label>
              <select id="new-story-universe" className="form-select" value={shownUniverse}
                onChange={(e) => choose(setUniverseChoice)(e.target.value)}>
                {universeOptions.map((universe) => (
                  <option key={universe.id} value={universe.id}>{universeLabel(universe)}</option>
                ))}
              </select>
              <p className="form-hint">What the characters are made of: each concept leads with another species of it.</p>
              {shownUniverseEntry && shownUniverseEntry.audience_note && (
                <p className="form-hint">{shownUniverseEntry.audience_note[language || 'en']}</p>
              )}
            </div>
          )}

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
              {!episodeTemplateChoice && suggestedTemplate
                ? (profileSuggestion ? ' Suggested for native speech: one shot per line.' : ' Suggested by the style.')
                : ''}
            </p>
          </div>

          <div className="form-group">
            <p className="form-hint">
              <strong>Clips: {manualClips ? 'my own' : 'auto'} · Images: {imagesManual ? 'my own' : 'auto'}</strong>
            </p>
            {manualClips ? (
              <p className="chip chip-wrap">
                Native speech — your own clips: each character line is spoken on camera by its own clip, which you
                make on Google Flow or Higgsfield from the app's shot brief and upload; the app writes, draws the
                keyframes, times the subtitles and renders{speech ? `: ${speech.summary}.` : '.'}
              </p>
            ) : nativeSpeech ? (
              <p className="chip chip-wrap">
                Native speech (Veo): each character line is spoken on camera by its own clip, lips and voice one
                take; no narrator and no generated voice{speech ? `: ${speech.summary}.` : '.'}
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
              {pipeline === 'v2' && (
                <div className="form-group">
                  <label className="form-label" htmlFor="new-story-sheet-mode">Character sheets</label>
                  <select id="new-story-sheet-mode" className="form-select" value={sheetMode}
                    onChange={(e) => choose(setSheetMode)(e.target.value)}>
                    {SHEET_MODES.map((mode) => {
                      const usd = estimate && estimate.story && estimate.story.sheet_usd_by_mode
                        ? estimate.story.sheet_usd_by_mode[mode.id] : null
                      return (
                        <option key={mode.id} value={mode.id}>
                          {mode.label}{usd != null ? ` — ≈ $${usd.toFixed(2)} a character` : ''}
                        </option>
                      )
                    })}
                  </select>
                  {sheetMode !== 'three_sheet' && (
                    <p className="form-hint">
                      One 9:16 image per character: the front on the left half, the back on the right.
                    </p>
                  )}
                </div>
              )}
              {pipeline === 'v2' && !imagesManual && (
                <div className="form-group">
                  <label className="form-label" htmlFor="new-story-image-preference">Image provider</label>
                  <select id="new-story-image-preference" className="form-select" value={imagePreference}
                    disabled={!geminiKeySet && imagePreference === ''}
                    onChange={(e) => choose(setImagePreference)(e.target.value)}>
                    {IMAGE_PREFERENCES.map((pref) => <option key={pref.id} value={pref.id}>{pref.label}</option>)}
                  </select>
                  {!geminiKeySet && (
                    <p className="form-hint">Add GEMINI_PAID_API_KEY in Settings to draw the images on Gemini first.</p>
                  )}
                </div>
              )}
              <div className="form-group">
                <label className="form-label" htmlFor="new-story-body-rule">Bodies</label>
                <select id="new-story-body-rule" className="form-select" value={bodyRule}
                  onChange={(e) => choose(setBodyRule)(e.target.value)}>
                  {BODY_RULES.map((rule) => <option key={rule.id} value={rule.id}>{rule.label}</option>)}
                </select>
                {bodyRule === 'all_matter' && (
                  <p className="form-hint">
                    The whole body, hands and legs included, is drawn in the character's own matter. Only a style
                    that defines it (Fruit Drama) can be approved with it.
                  </p>
                )}
              </div>
              {pipeline === 'v2' && (
                <div className="form-group">
                  <label className="form-label" htmlFor="new-story-prompt-style">Clip prompts</label>
                  <select id="new-story-prompt-style" className="form-select" value={promptStyle}
                    onChange={(e) => choose(setPromptStyle)(e.target.value)}>
                    {PROMPT_STYLES.map((style) => <option key={style.id} value={style.id}>{style.label}</option>)}
                  </select>
                  {promptStyle === 'action' && (
                    <p className="form-hint">
                      Each clip prompt is one continuous action in the present tense, every character named by the
                      same colour and species phrase, with the sounds and a single camera move.
                    </p>
                  )}
                </div>
              )}
              <div className="form-group">
                <label className="form-label" htmlFor="new-story-frame">Frame</label>
                <select id="new-story-frame" className="form-select" value={shownFrame}
                  onChange={(e) => choose(setFrame)(e.target.value)}>
                  {FRAMES.map((option) => {
                    const reason = frameReason(option.id)
                    return (
                      <option key={option.id} value={option.id} disabled={Boolean(reason)} title={reason}>
                        {option.label}{reason ? ` — unavailable: ${reason}` : ''}
                      </option>
                    )
                  })}
                </select>
                <p className="form-hint">{FRAME_HINT}</p>
              </div>
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

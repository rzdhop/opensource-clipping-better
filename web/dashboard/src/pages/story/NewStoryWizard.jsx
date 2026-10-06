import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createStory, fetchNewStoryProfile, fetchSettings, fetchStyles, fetchUniverses } from '../../api'
import { Button } from '../../ui'
import {
  EPISODE_TEMPLATES, pipelineDefaultTemplate, profileSuggestedTemplate, styleSuggestedTemplate,
} from './episodeTemplates'

// Plan 21 decision 4: the Mode choice's own label, Studio first (the
// default). Plan 28 stage S1: under Advanced, as "How the story runs".
const MODE_HELP = {
  studio: 'Approve each step yourself.',
  agent: ('One run from the idea to episode 1; the agent approves the style, the cast and the places as soon as '
    + 'they are complete -- no taste check; review them in Studio afterwards, every regenerate stays available.'),
}

// The new-story form (`/story/new`). An existing story opens in the story
// workspace (StoryWorkspace.jsx, dashboard overhaul stage 3, DEC-255).
//
// Plan 28 stage S1 (DEC-305 §9, the human: "the UI became too complicated,
// too much term I do not understand"): the form asks four things -- the
// idea, the language, the look and who makes the clips -- and the server
// decides the rest (media_policy.new_story_profile, format_fit.choose_format:
// a format that always fits). Everything else sits under one collapsed
// "Advanced" fold; untouched, nothing of it is sent.

// The story defaults (spec 8, 8.1, 8.5): a story that does not name every
// one of these gets exactly these values. tests/test_story_defaults.py reads
// this literal as text and checks it against
// clipping.aistory.defaults.default_generation_profile().
const DEFAULT_GENERATION_PROFILE = { tier: 1, route: 'auto', consistency_mode: 'references', budget_profile: 'free' }

// Plan 28 stage S1: what the form shows before the server's offer arrives -- "Me" picked
// (defaults.manual_speech_generation_profile); the offer's own profile replaces it.
const ME_SETUP = { tier: 3, route: 'api', consistency_mode: 'references', budget_profile: 'native_speech_manual', pipeline: 'v2' }

// Plan 22: the native-speech profile's speaking-clip models (its
// speech_links), cheapest first; "The app" starts on lite (plan 28 stage A4).
const SPEECH_MODELS = [
  { id: 'lite', label: 'Cheapest (Veo 3.1 lite)' },
  { id: 'fast', label: 'Better (Veo 3.1 Fast)' },
  { id: 'premium', label: 'Best (Veo 3.1)' },
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
  + 'regular YouTube video, not Shorts. Square: pictures with motion, or clips on Seedance or Kling (Veo, LTX, Flow and a local '
  + 'ComfyUI make no 1:1). The frame cannot change after the story is made.')

/** Why the frame `frame` cannot be picked with this profile, or '' (media_policy.aspect_refusal's rules). */
export function frameRefusal(frame, { pipeline, tier, route, budgetProfile, reasons }) {
  if (frame === '9:16') return ''
  const said = reasons || {}
  if (pipeline !== 'v2') return said.pipeline || 'a 16:9 or 1:1 frame is for an animated story (the current format)'
  if (tier < 2) return ''
  if (route === 'local') return said.local || 'clips made on this computer are 9:16 only'
  if (budgetProfile === 'free') return said.free || 'the free spending plan animates on this computer only (9:16)'
  if (frame === '1:1' && budgetProfile === 'native_speech') return said.veo_square || 'Veo makes 9:16 and 16:9 only'
  if (frame === '1:1' && budgetProfile === 'native_speech_manual') {
    return said.manual_square || 'Google Flow makes 9:16 and 16:9 only'
  }
  return ''
}

// Plan 28 stage S2: a key the offer names (its environment name) as the Settings page says it.
const KEY_NAMES = {
  GEMINI_PAID_API_KEY: 'the paid Gemini key', GOOGLE_API_KEY: 'the Google key', GROQ_API_KEY: 'the Groq key',
  FAL_KEY: 'the fal.ai key', ELEVENLABS_API_KEY: 'the ElevenLabs key', OPENAI_API_KEY: 'the OpenAI key',
  MISTRAL_API_KEY: 'the Mistral key', RUNPOD_API_KEY: 'the RunPod key', RUNPOD_COMFY_ENDPOINT_ID: 'the RunPod endpoint id',
}
const keyName = (env) => KEY_NAMES[env] || env

/** A price in whole dollars, rounded up (the writing's few cents included): 0.48 -> "$1", 4.08 -> "$5". */
export function roughUsd(usd) {
  return `$${Math.max(1, Math.ceil(usd))}`
}

// Plan 28 stage S1: "Who makes the clips", in the human's words; `id` is StoryCreateRequest.clips
// (defaults.CLIP_MAKERS). The price is the offer's (offer.clip_makers[id].episode_usd).
const CLIP_CHOICES = [
  { id: 'me', title: 'Me, on Flow or Higgsfield', text: 'the app writes the prompts and checks the clips.',
    price: (usd) => `About ${roughUsd(usd)} of app cost per episode.`, unpriced: '' },
  { id: 'app', title: 'The app', text: '',
    price: (usd) => `about ${roughUsd(usd)} per episode`, unpriced: 'it makes them and pays for them' },
]

// The budget profile each answer is (plan 25's "How clips are made": My own = native_speech_manual, Auto =
// native_speech), and back.
const CLIP_MAKER_SETUPS = { me: 'native_speech_manual', app: 'native_speech' }
const clipMakerOf = (budgetProfile) => Object.keys(CLIP_MAKER_SETUPS)
  .find((id) => CLIP_MAKER_SETUPS[id] === budgetProfile) || null

function CreateStoryForm() {
  const navigate = useNavigate()
  // No default: a language a user forgot to pick must never silently become
  // one, matching StoryCreateRequest and the CLI's --lang.
  const [language, setLanguage] = useState(null)
  const [mode, setMode] = useState('studio')
  const [seedText, setSeedText] = useState('')
  const [styleTemplateId, setStyleTemplateId] = useState('')
  const [styles, setStyles] = useState([])
  // Plan 28 stage S1: one fold for every other choice, closed until the human opens it.
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [tier, setTier] = useState(ME_SETUP.tier)
  const [route, setRoute] = useState(ME_SETUP.route)
  const [consistencyMode, setConsistencyMode] = useState(ME_SETUP.consistency_mode)
  const [budgetProfile, setBudgetProfile] = useState(ME_SETUP.budget_profile)
  const [pipeline, setPipeline] = useState(ME_SETUP.pipeline)
  const [speechModel, setSpeechModel] = useState('fast')
  // Plan 22 stage 5: the sheets, plates, props and keyframes may be your own uploads (plan 25 stage 5;
  // only a v2 story has manual images). Plan 28 stage S1: under Advanced, "Images".
  const [imagesOwn, setImagesOwn] = useState(false)
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
  // The episode format the user picked under Advanced; '' -- the app chooses one that fits.
  const [episodeTemplateChoice, setEpisodeTemplateChoice] = useState('')
  // What the server gives a story created now (GET /api/stories/new-profile): each answer to "Who makes the
  // clips" with its profile and price, and the formats each one fits (plan 28 stage A4).
  const [offer, setOffer] = useState(null)
  // False until the user changes a choice under Advanced: an untouched form sends no
  // profile, so the server's choice applies (it used to send a v1, free,
  // tier-1 profile over it, and a dashboard story could never animate).
  const [profileChosen, setProfileChosen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')

  // The form's state follows a profile the server made (the offer's, for an answer to "Who makes the clips").
  const applySetup = (profile) => {
    setTier(profile.tier ?? DEFAULT_GENERATION_PROFILE.tier)
    setRoute(profile.route || DEFAULT_GENERATION_PROFILE.route)
    setConsistencyMode(profile.consistency_mode || DEFAULT_GENERATION_PROFILE.consistency_mode)
    setBudgetProfile(profile.budget_profile || DEFAULT_GENERATION_PROFILE.budget_profile)
    setPipeline(profile.pipeline || '')
    setSpeechModel(profile.speech_model || 'fast')
  }

  useEffect(() => {
    let cancelled = false
    fetchStyles().then((data) => { if (!cancelled) setStyles(data.styles || []) }).catch(() => {})
    fetchUniverses().then((data) => { if (!cancelled) setUniverseCatalogue(data) }).catch(() => {})
    fetchSettings().then((data) => { if (!cancelled) setGeminiKeySet(Boolean(data.gemini_paid_api_key_set)) }).catch(() => {})
    fetchNewStoryProfile().then((data) => {
      if (cancelled) return
      setOffer(data)
      const makers = data.clip_makers || {}
      applySetup((makers.me && makers.me.profile) || data.profile || ME_SETUP)
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
  // Stage 5: its manual twin (every clip your own upload, from the shot brief) is "Me" (plan 28 stage S1).
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
  // Plan 28 stage S1: "Who makes the clips" -- the answer the form shows is read back from the budget profile
  // (null when Advanced set another one); a card sets the server's profile for it, not a choice of the human's.
  const clipMaker = clipMakerOf(budgetProfile)
  const handleClipMaker = (id) => {
    if (id === clipMaker) return
    const made = offer && offer.clip_makers && offer.clip_makers[id]
    if (made && made.profile) {
      applySetup(made.profile)
      return
    }
    applySetup({ ...ME_SETUP, budget_profile: CLIP_MAKER_SETUPS[id], ...(id === 'app' ? { speech_model: 'lite' } : {}) })
  }
  const makerOffer = (id) => (offer && offer.clip_makers ? offer.clip_makers[id] : null)
  const chosenMaker = clipMaker ? makerOffer(clipMaker) : null
  const handleImagesOwn = (own) => { setProfileChosen(true); setImagesOwn(own) }
  const imagesManual = pipeline === 'v2' && imagesOwn
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
  // Plan 28 stage A4: the Advanced format select lists only the formats the server's oracle says this story's
  // clips fit in its language (offer.formats_that_fit; no language yet: those that fit both), each other one
  // named with its reason (offer.formats_hidden). A story whose lines are voiced keeps every format. A pick
  // that stopped fitting (another language, other clips) goes back to the app's choice.
  const fitLists = nativeSpeech && clipMaker && offer && offer.formats_that_fit ? offer.formats_that_fit[clipMaker] : null
  const hiddenLists = nativeSpeech && clipMaker && offer && offer.formats_hidden ? offer.formats_hidden[clipMaker] : null
  const fitIds = fitLists
    ? (language ? fitLists[language] : fitLists.fr.filter((id) => fitLists.en.includes(id)))
    : EPISODE_TEMPLATES.filter((tpl) => !nativeSpeech || tpl.pipeline === 'v2').map((tpl) => tpl.id)
  const hiddenReasons = hiddenLists ? (language ? hiddenLists[language] : { ...hiddenLists.en, ...hiddenLists.fr }) : {}
  const formatOptions = EPISODE_TEMPLATES.filter((tpl) => fitIds.includes(tpl.id))
  const formatChoice = fitIds.includes(episodeTemplateChoice) ? episodeTemplateChoice : ''
  // Plan 20 stage 1: on a story whose lines are voiced, a style suggests an episode format
  // (episode_defaults.episode_template_id -- Fruit Drama the narrated drama) when it fits the pipeline. A story
  // whose characters speak in their own clips sends none: the server picks one that fits (the confrontation,
  // defaults.episode_template_for; plan 28 stage A4).
  const chosenStyle = styles.find((style) => style.template_id === styleTemplateId)
  const suggestedTemplate = nativeSpeech ? null : styleSuggestedTemplate(chosenStyle, pipeline)
  const appFormat = EPISODE_TEMPLATES.find((tpl) => tpl.id === (
    profileSuggestedTemplate(budgetProfile) || suggestedTemplate || pipelineDefaultTemplate(pipeline)))
  const episodeFormat = EPISODE_TEMPLATES.find((tpl) => tpl.id === formatChoice) || appFormat
  // Plan 23 stage D2: the Universe select lists what the chosen style takes (hidden when it lists none);
  // a pick the style does not list is dropped, the style's default shows instead. Plan 28 stage S1: the
  // universe is part of the look -- the style's default, said under the style cards; the select is under
  // Advanced. A profile the form sends names the universe it shows; untouched, the server names the look's
  // default itself (media_policy.new_story_profile): the concepts read only an explicit one.
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
  const ready = Boolean(language) && seedText.trim() !== ''

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!ready) return
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
        // Picked under Advanced, or suggested by the style on a voiced story; else null: the server picks one
        // that fits (format_fit.choose_format).
        episode_template_id: formatChoice || suggestedTemplate || null,
        // Plan 21 stage 3: "How the story runs" under Advanced -- studio (every
        // step waits for your approval, the default) or agent (one
        // story-fast-track job approves by rule).
        mode,
        // Plan 28 stage S1: "Who makes the clips" -- the server makes the profile from it
        // (media_policy.new_story_profile) unless Advanced sent one.
        clips: clipMaker,
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
          <p>Four questions; the app decides the rest.</p>
        </div>
      </div>

      <form onSubmit={handleSubmit}>
        <div className="card" style={{ marginBottom: '16px' }}>
          <div className="form-group">
            <label className="form-label" htmlFor="new-story-idea">Your idea</label>
            <textarea
              id="new-story-idea"
              className="form-input"
              rows={4}
              maxLength={2000}
              required
              placeholder="Two lemons share a stall at the market; one of them is lying about the price."
              value={seedText}
              onChange={(e) => setSeedText(e.target.value)}
            />
            <p className="form-hint">
              A sentence or two: who, where, and what goes wrong. The app writes the rest. {seedText.length} / 2000
            </p>
          </div>

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
            <p className="form-hint">The language the characters speak. Nothing is picked for you.</p>
          </div>

          <div className="form-group">
            <label className="form-label">The look</label>
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
            {shownUniverseEntry && (
              <p className="form-hint">
                The characters are {universeLabel(shownUniverseEntry).toLowerCase()}. Pick others under Advanced.
              </p>
            )}
          </div>

          <div className="form-group">
            <label className="form-label">Who makes the clips</label>
            <div className="story-style-grid" role="group" aria-label="Who makes the clips">
              {CLIP_CHOICES.map((choice) => {
                const made = makerOffer(choice.id)
                const words = [choice.text, made ? choice.price(made.episode_usd) : choice.unpriced]
                  .filter(Boolean).join(' ')
                return (
                  <button
                    key={choice.id}
                    type="button"
                    className={`story-style-pick${clipMaker === choice.id ? ' active' : ''}`}
                    aria-pressed={clipMaker === choice.id}
                    data-choice={`clips-${choice.id}`}
                    onClick={() => handleClipMaker(choice.id)}
                  >
                    <span className="story-style-pick-name">{choice.title} — {words}</span>
                  </button>
                )
              })}
            </div>
            {!clipMaker && <p className="form-hint">Your choices under Advanced decide how the clips are made.</p>}
            {chosenMaker && chosenMaker.missing_keys.length > 0 && (
              <p className="form-hint">Add {chosenMaker.missing_keys.map(keyName).join(' and ')} in Settings first.</p>
            )}
            {chosenMaker && chosenMaker.stt_missing_keys.length > 0 && (
              <p className="form-hint">To check the clips, add {chosenMaker.stt_missing_keys.map(keyName).join(' or ')} in Settings.</p>
            )}
            {clipMaker === 'app' && offer && !offer.allow_paid && (
              <p className="form-hint">Paid services are off in Settings: nothing is bought until you turn them on.</p>
            )}
          </div>

          {/* advanced */}
          <details className="story-profile" open={showAdvanced} onToggle={(e) => setShowAdvanced(e.target.open)}>
            <summary>Advanced</summary>
            <div className="story-profile-grid">
              <div className="form-group">
                <label className="form-label" htmlFor="new-story-episode-format">Episode format</label>
                <select
                  id="new-story-episode-format"
                  className="form-select"
                  value={formatChoice}
                  onChange={(e) => setEpisodeTemplateChoice(e.target.value)}
                >
                  <option value="">Let the app choose (recommended)</option>
                  {formatOptions.map((tpl) => <option key={tpl.id} value={tpl.id}>{tpl.label}</option>)}
                </select>
                <p className="form-hint">
                  {formatChoice ? '' : `The app will use: ${appFormat ? appFormat.label : ''}. `}
                  {episodeFormat ? episodeFormat.help : ''}
                </p>
                {EPISODE_TEMPLATES.filter((tpl) => hiddenReasons[tpl.id]).map((tpl) => (
                  <p key={tpl.id} className="form-hint">Not offered: {tpl.label}, {hiddenReasons[tpl.id]}</p>
                ))}
              </div>

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

              {universeOptions.length > 0 && (
                <div className="form-group">
                  <label className="form-label" htmlFor="new-story-universe">Characters made of</label>
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
                <label className="form-label">Images</label>
                <div className="story-segmented" role="group" aria-label="Images">
                  <Button type="button" size="sm" variant={imagesManual ? 'secondary' : 'primary'}
                    aria-pressed={!imagesManual} onClick={() => handleImagesOwn(false)} data-choice="images-auto">
                    Made by the app
                  </Button>
                  <Button type="button" size="sm" variant={imagesManual ? 'primary' : 'secondary'}
                    disabled={pipeline !== 'v2'} aria-pressed={imagesManual} onClick={() => handleImagesOwn(true)}
                    data-choice="images-own">
                    My own uploads
                  </Button>
                </div>
                <p className="form-hint">
                  {imagesManual
                    ? 'You paste the prompts into your provider and upload the images; nothing is billed.'
                    : 'The app draws the characters, places and keyframes on its paid image services.'}
                </p>
              </div>

              {budgetProfile === 'native_speech' && (
                <div className="form-group">
                  <label className="form-label" htmlFor="new-story-speech-model">Clip quality</label>
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

              {pipeline === 'v2' && (
                <div className="form-group">
                  <label className="form-label" htmlFor="new-story-sheet-mode">Character sheets</label>
                  <select id="new-story-sheet-mode" className="form-select" value={sheetMode}
                    onChange={(e) => choose(setSheetMode)(e.target.value)}>
                    {SHEET_MODES.map((option) => {
                      const usd = estimate && estimate.story && estimate.story.sheet_usd_by_mode
                        ? estimate.story.sheet_usd_by_mode[option.id] : null
                      return (
                        <option key={option.id} value={option.id}>
                          {option.label}{usd != null ? ` — ≈ $${usd.toFixed(2)} a character` : ''}
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
                <label className="form-label">How the story runs</label>
                <div className="story-segmented" role="group" aria-label="How the story runs">
                  <Button
                    type="button"
                    variant={mode === 'studio' ? 'primary' : 'secondary'}
                    size="sm"
                    onClick={() => setMode('studio')}
                  >
                    Step by step
                  </Button>
                  <Button
                    type="button"
                    variant={mode === 'agent' ? 'primary' : 'secondary'}
                    size="sm"
                    onClick={() => setMode('agent')}
                  >
                    In one run
                  </Button>
                </div>
                <p className="form-hint">{MODE_HELP[mode]}</p>
              </div>

              <div className="form-group">
                <label className="form-label">Narrator</label>
                <p className="form-hint">
                  Off. When the characters speak in their own clips there is no narrator and no generated voice; a
                  story whose lines are voiced can turn its narrator on later, in the story's settings.
                </p>
              </div>
            </div>

            <h4 className="form-label" style={{ marginTop: '14px' }}>The app's setup</h4>
            <div className="story-profile-grid">
              <div className="form-group">
                <label className="form-label">Story engine</label>
                <select className="form-select" value={pipeline} onChange={(e) => handlePipeline(e.target.value)}>
                  <option value="v2">Current — 6–10 shots, each a clip</option>
                  <option value="">Old — stills and short scenes</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Movement</label>
                <select className="form-select" value={tier} onChange={(e) => choose(setTier)(Number(e.target.value))}>
                  <option value={1}>Still pictures that pan and zoom</option>
                  <option value={2}>Animated</option>
                  <option value={3}>Animated, with the clips' own sound</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Where the app makes things</label>
                <select className="form-select" value={route} onChange={(e) => choose(setRoute)(e.target.value)}>
                  <option value="auto">Let the app decide</option>
                  <option value="local">On this computer</option>
                  <option value="api">On online services</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Keeping characters the same</label>
                <select className="form-select" value={consistencyMode}
                  onChange={(e) => choose(setConsistencyMode)(e.target.value)}>
                  <option value="references">From their reference pictures</option>
                  <option value="prompt_only" disabled={pipeline === 'v2'}>From the words only</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Spending plan</label>
                <select className="form-select" value={budgetProfile}
                  onChange={(e) => handleBudgetProfile(e.target.value)}>
                  <option value="free">Free (no clip bought)</option>
                  <option value="one_dollar">$1 an episode (key shots)</option>
                  <option value="quality">Paid services — every shot animated, voices added</option>
                  <option value="native_speech">The app makes clips that speak (Veo)</option>
                  <option value="native_speech_manual">You make the clips that speak (Flow / Higgsfield)</option>
                  <option value="own_gpu">Your own GPU (RunPod) — every shot animated</option>
                </select>
              </div>
            </div>
            <div className="form-group">
              {manualClips ? (
                <p className="chip chip-wrap">
                  You make each clip on Google Flow or Higgsfield from the app's prompt and upload it; the app writes,
                  draws the keyframes, checks the clips, times the subtitles and renders{speech ? `: ${speech.summary}.` : '.'}
                </p>
              ) : nativeSpeech ? (
                <p className="chip chip-wrap">
                  The app makes each clip on Veo, the character speaking its line in it; no narrator and no generated
                  voice{speech ? `: ${speech.summary}.` : '.'}
                </p>
              ) : fullyAnimated ? (
                <p className="chip chip-wrap">
                  Every shot is a clip, with paid images and voices{estimate ? `: ${estimate.summary}.` : '.'}
                </p>
              ) : (
                <p className="chip chip-warn chip-wrap">
                  Not every shot moves with this setup.
                  {offer && !offer.quality && offer.missing_keys && offer.missing_keys.length > 0
                    ? ` Add ${offer.missing_keys.join(', ')} in Settings to animate every shot`
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
          </details>
          {/* /advanced */}
        </div>

        {error && <p className="story-error">{error}</p>}

        <button type="submit" className="btn btn-primary" disabled={!ready || creating}>
          {creating ? <><span className="spinner"></span> Creating…</> : 'Create the story'}
        </button>
        {!ready && <p className="form-hint">Write your idea and pick a language first.</p>}
      </form>
    </div>
  )
}

// --------------------------------------------------------------------- page

export default function NewStoryWizard() {
  return <CreateStoryForm />
}

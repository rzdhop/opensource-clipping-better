import { useState, useEffect, useRef } from 'react'
import { checkVideoKeys, fetchHardware, fetchSettings, testChain, testGenerationChain, updateSettings } from '../api'
import { Badge, Button, Card, CardBody, CardHeader, Field } from '../ui'
import {
  Brain, CircleCheck, CircleDollarSign, Cpu, Eye, EyeOff, Film, Gauge, ImageIcon, KeyRound, LinkIcon, Mic, Monitor,
  Palette, RefreshCw, Save, Server, Wallet, Wand2,
} from '../ui/icons'

/**
 * A write-only secret: the server reports whether one is set, never its
 * value. The kit's Field gives it its id and description, which it forwards
 * to the input; the eye button shows or hides what is being typed.
 */
const PasswordInput = ({ value, onChange, placeholder, isSet, id, ...rest }) => {
  const [show, setShow] = useState(false)
  return (
    <div className="settings-secret">
      <input
        {...rest}
        id={id}
        className="form-input"
        type={show ? 'text' : 'password'}
        placeholder={isSet ? '••••••••••••••••' : placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete="off"
        spellCheck={false}
      />
      <button
        type="button"
        className="settings-secret-toggle"
        onClick={() => setShow(!show)}
        aria-label={show ? 'Hide the key' : 'Show the key'}
        aria-pressed={show}
        aria-controls={id}
        title={show ? 'Hide' : 'Show'}
      >
        {show ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
      </button>
    </div>
  )
}

/**
 * A provider key's state: "Tested" once a test on this page got an answer
 * from one of its links, "Set" when the server holds one, else "Missing".
 */
const KeyBadge = ({ set, tested }) => {
  if (tested) return <Badge tone="success" icon={CircleCheck}>Tested</Badge>
  if (set) return <Badge tone="accent" dot>Set</Badge>
  return <Badge tone="neutral">Missing</Badge>
}

/** One provider key: a labelled secret input (Field), its note, its badge and its hint. */
const KeyField = ({ id, label, note, isSet, tested, value, onChange, placeholder, hint }) => (
  <Field
    className="settings-key"
    label={<>{label}{note && <span className="settings-key-note"> — {note}</span>}</>}
    aside={<KeyBadge set={isSet} tested={tested} />}
    hint={hint}
    htmlFor={id}
  >
    <PasswordInput id={id} value={value} onChange={onChange} placeholder={placeholder} isSet={isSet} />
  </Field>
)

// Which tested links speak for which key: a chain test's row label is
// "<provider>/<model>". Veo and the nano-banana images run on the paid
// Gemini key (DEC-222), every other gemini/ link on the free one.
const KEY_PROVIDERS = {
  groq_api_key: 'groq',
  nvidia_api_key: 'nvidia',
  google_api_key: 'gemini',
  openrouter_api_key: 'openrouter',
  mistral_api_key: 'mistral',
  openai_compat_api_key: 'custom',
  fal_key: 'fal',
  openai_api_key: 'openai',
  cloudflare_api_token: 'cloudflare',
  cloudflare_account_id: 'cloudflare',
  pollinations_api_key: 'pollinations',
  gemini_paid_api_key: 'gemini_paid',
}

function providerOfLabel(label) {
  const text = String(label || '')
  if (/^gemini\/(veo|nano-banana)/.test(text)) return 'gemini_paid'
  // The premium writing chain's own provider (plan 22 stage 1): the same
  // paid, billing-enabled Google project as Veo and nano-banana above.
  if (/^gemini-paid\//.test(text)) return 'gemini_paid'
  return text.split('/')[0]
}

/** The providers a test on this page got a real answer from (status "ok"). */
function testedProviders(testResult, genResults) {
  const rows = [
    ...((testResult && testResult.results) || []),
    ...Object.values(genResults || {}).flatMap((result) => (result && result.results) || []),
  ]
  return new Set(rows.filter((row) => row.status === 'ok').map((row) => providerOfLabel(row.label)))
}

const linkStyle = { color: 'var(--accent-hover)' }

/**
 * Base URLs for services that speak the OpenAI chat API.
 *
 * Conveniences, not endorsements: the project recommends Groq and Gemini
 * because both still hand out a free key with no card. This list is for people
 * who already have a key somewhere else.
 */
const ENDPOINT_PRESETS = [
  { label: 'OpenRouter', url: 'https://openrouter.ai/api/v1' },
  { label: 'Groq', url: 'https://api.groq.com/openai/v1' },
  { label: 'Mistral', url: 'https://api.mistral.ai/v1' },
  { label: 'xAI (Grok)', url: 'https://api.x.ai/v1' },
  { label: 'Ollama (local)', url: 'http://localhost:11434/v1' },
]

// The four tabs of spec 8.6. The last one opened is remembered per browser.
const SETTINGS_TABS = [
  { id: 'providers', icon: KeyRound, label: 'Providers' },
  { id: 'generation', icon: Palette, label: 'Generation' },
  { id: 'hardware', icon: Cpu, label: 'Local hardware' },
  { id: 'budget', icon: Wallet, label: 'Budget' },
]
const TAB_KEY = 'rzc_settings_tab'

function readTab() {
  try {
    const stored = localStorage.getItem(TAB_KEY)
    return SETTINGS_TABS.some(t => t.id === stored) ? stored : 'providers'
  } catch {
    return 'providers'
  }
}

/**
 * The tab strip: a WAI-ARIA tablist (arrow keys, Home and End move between
 * tabs; only the selected one is in the tab order). It scrolls sideways on a
 * phone instead of wrapping.
 */
function SettingsTabs({ tab, onSelect }) {
  const refs = useRef({})
  // On a phone the strip scrolls: keep the open tab in view (the remembered
  // one may be the last).
  useEffect(() => {
    const el = refs.current[tab]
    const strip = el && el.parentElement
    if (strip && strip.scrollWidth > strip.clientWidth) {
      const offset = el.getBoundingClientRect().left - strip.getBoundingClientRect().left
      strip.scrollLeft += offset - (strip.clientWidth - el.offsetWidth) / 2
    }
  }, [tab])
  const onKeyDown = (event) => {
    const index = SETTINGS_TABS.findIndex(t => t.id === tab)
    let next = null
    if (event.key === 'ArrowRight') next = (index + 1) % SETTINGS_TABS.length
    else if (event.key === 'ArrowLeft') next = (index - 1 + SETTINGS_TABS.length) % SETTINGS_TABS.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = SETTINGS_TABS.length - 1
    if (next == null) return
    event.preventDefault()
    const id = SETTINGS_TABS[next].id
    onSelect(id)
    if (refs.current[id]) refs.current[id].focus()
  }
  return (
    <div className="settings-tabs" role="tablist" aria-label="Settings sections" onKeyDown={onKeyDown}>
      {SETTINGS_TABS.map(t => {
        const Icon = t.icon
        return (
          <button
            key={t.id}
            ref={(el) => { refs.current[t.id] = el }}
            type="button"
            role="tab"
            id={`settings-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`settings-panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            className={`settings-tab${tab === t.id ? ' active' : ''}`}
            onClick={() => onSelect(t.id)}
          >
            <Icon size={16} aria-hidden="true" />
            {t.label}
          </button>
        )
      })}
    </div>
  )
}

function Settings() {
  const [settings, setSettings] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [tab, setTab] = useState(readTab)
  const switchTab = (id) => {
    setTab(id)
    try { localStorage.setItem(TAB_KEY, id) } catch { /* private mode: the tab still switches */ }
  }
  // Generation chain tests (DEC-103): one at a time, the last result kept per kind.
  const [genTesting, setGenTesting] = useState(null)
  const [genResults, setGenResults] = useState({})
  const [genError, setGenError] = useState('')
  // Local hardware (GET /api/hardware), probed when its tab opens.
  const [hardware, setHardware] = useState(null)
  const [hwLoading, setHwLoading] = useState(false)
  const [hwError, setHwError] = useState('')
  const [localComfyuiUrl, setLocalComfyuiUrl] = useState('')
  const [localOllamaUrl, setLocalOllamaUrl] = useState('')

  // API keys are write-only: the backend reports whether each is set, never its
  // value, so these stay empty unless a new one is being entered.
  const [googleKey, setGoogleKey] = useState('')
  const [pexelsKey, setPexelsKey] = useState('')
  const [hfToken, setHfToken] = useState('')
  const [nvidiaKey, setNvidiaKey] = useState('')
  const [groqKey, setGroqKey] = useState('')
  const [openrouterKey, setOpenrouterKey] = useState('')
  const [mistralKey, setMistralKey] = useState('')
  const [compatKey, setCompatKey] = useState('')
  // Generation providers (AI Story, spec 8.6)
  const [falKey, setFalKey] = useState('')
  const [openaiKey, setOpenaiKey] = useState('')
  const [cloudflareToken, setCloudflareToken] = useState('')
  const [cloudflareAccountId, setCloudflareAccountId] = useState('')
  const [pollinationsKey, setPollinationsKey] = useState('')
  // Veo and nano-banana: a separate, billing-enabled Google project (phase 6 stage 12, RC-V4; DEC-222).
  const [geminiPaidKey, setGeminiPaidKey] = useState('')

  // The endpoint URL and model are not secrets, so they are prefilled.
  const [compatUrl, setCompatUrl] = useState('')
  const [compatModel, setCompatModel] = useState('')
  // AI Story's premium writing chain (plan 22 stage 1, DEC-273): not a
  // secret, like compatUrl/compatModel above -- prefilled, sent only when it
  // changed, "" falls through to STORY_LLM_CHAIN, then LLM_CHAIN, then the
  // shipped default (the paid Gemini writer first).
  const [storyLlmPremiumChain, setStoryLlmPremiumChain] = useState('')

  // Run a job even when only the slow floor (NVIDIA) has a key. Prefilled.
  const [allowSlowChain, setAllowSlowChain] = useState(false)
  // Budget (AI Story, DEC-097)
  const [allowPaid, setAllowPaid] = useState(false)
  const [perEpisodeCap, setPerEpisodeCap] = useState('')
  const [dailyCap, setDailyCap] = useState('')
  const [perStoryCap, setPerStoryCap] = useState('')
  const [budgetProfile, setBudgetProfile] = useState('')

  // The chain test. It can take a couple of minutes, so it counts seconds
  // while it runs: a bare spinner reads as "hung" long before NVIDIA answers.
  const [testing, setTesting] = useState(false)
  const [testStarted, setTestStarted] = useState(0)
  const [testElapsed, setTestElapsed] = useState(0)
  const [testResult, setTestResult] = useState(null)
  const [testError, setTestError] = useState('')

  useEffect(() => {
    if (!testing) return undefined
    const id = setInterval(() => setTestElapsed(Math.round((Date.now() - testStarted) / 1000)), 1000)
    return () => clearInterval(id)
  }, [testing, testStarted])

  const handleTestChain = async () => {
    setTesting(true)
    setTestStarted(Date.now())
    setTestElapsed(0)
    setTestResult(null)
    setTestError('')
    try {
      setTestResult(await testChain())
    } catch (err) {
      setTestError(err.message)
    } finally {
      setTesting(false)
    }
  }

  const handleTestGeneration = async (kind, link) => {
    setGenTesting(link ? `${kind}:${link}` : kind)
    setGenError('')
    try {
      const result = await testGenerationChain({ kind, link: link || '' })
      setGenResults(prev => ({ ...prev, [kind]: result }))
      // A paid test changes today's spend and the rows' "allowed": refresh.
      if (link) setSettings(await fetchSettings())
    } catch (err) {
      setGenError(err.message)
    } finally {
      setGenTesting(null)
    }
  }

  const loadHardware = async (refresh = false) => {
    setHwLoading(true)
    setHwError('')
    try {
      setHardware(await fetchHardware(refresh))
    } catch (err) {
      setHwError(err.message)
    } finally {
      setHwLoading(false)
    }
  }

  useEffect(() => {
    if (tab === 'hardware' && !hardware && !hwLoading) loadHardware()
  }, [tab]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    fetchSettings()
      .then(data => {
        setSettings(data)
        setCompatUrl(data.openai_compat_base_url || '')
        setCompatModel(data.openai_compat_model || '')
        setStoryLlmPremiumChain(data.story_llm_premium_chain || '')
        setAllowSlowChain(Boolean(data.allow_slow_chain))
        setAllowPaid(Boolean(data.allow_paid))
        setPerEpisodeCap(String(data.per_episode_cap_usd ?? ''))
        setDailyCap(String(data.daily_cap_usd ?? ''))
        setPerStoryCap(String(data.per_story_cap_usd ?? ''))
        setBudgetProfile(data.budget_profile || '')
        setLocalComfyuiUrl(data.local_comfyui_url || '')
        setLocalOllamaUrl(data.local_ollama_url || '')
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [])

  const handleSave = async (e) => {
    e.preventDefault()
    setSaving(true)
    setMsg('')
    try {
      const payload = {}
      if (googleKey) payload.google_api_key = googleKey
      if (pexelsKey) payload.pexels_api_key = pexelsKey
      if (hfToken) payload.hf_token = hfToken
      if (nvidiaKey) payload.nvidia_api_key = nvidiaKey
      if (groqKey) payload.groq_api_key = groqKey
      if (openrouterKey) payload.openrouter_api_key = openrouterKey
      if (mistralKey) payload.mistral_api_key = mistralKey
      if (compatKey) payload.openai_compat_api_key = compatKey
      if (falKey) payload.fal_key = falKey
      if (openaiKey) payload.openai_api_key = openaiKey
      if (cloudflareToken) payload.cloudflare_api_token = cloudflareToken
      if (cloudflareAccountId) payload.cloudflare_account_id = cloudflareAccountId
      if (pollinationsKey) payload.pollinations_api_key = pollinationsKey
      if (geminiPaidKey) payload.gemini_paid_api_key = geminiPaidKey

      // Sent whenever they differ from what the server holds, including when
      // cleared: an empty value removes the override and falls back to .env,
      // which is the only way to undo one from here.
      if (compatUrl !== (settings?.openai_compat_base_url || '')) {
        payload.openai_compat_base_url = compatUrl.trim()
      }
      if (compatModel !== (settings?.openai_compat_model || '')) {
        payload.openai_compat_model = compatModel.trim()
      }
      if (storyLlmPremiumChain.trim() !== (settings?.story_llm_premium_chain || '')) {
        payload.story_llm_premium_chain = storyLlmPremiumChain.trim()
      }
      // Same rule: turning it OFF must be sent too, or it could never be undone.
      if (allowSlowChain !== Boolean(settings?.allow_slow_chain)) {
        payload.allow_slow_chain = allowSlowChain
      }
      // Budget (DEC-097): the switch follows the same rule; an amount is sent
      // when it changed and is a positive number; the profile when it changed.
      if (allowPaid !== Boolean(settings?.allow_paid)) {
        payload.allow_paid = allowPaid
      }
      if (perEpisodeCap !== String(settings?.per_episode_cap_usd ?? '') && Number(perEpisodeCap) > 0) {
        payload.per_episode_cap_usd = Number(perEpisodeCap)
      }
      if (dailyCap !== String(settings?.daily_cap_usd ?? '') && Number(dailyCap) > 0) {
        payload.daily_cap_usd = Number(dailyCap)
      }
      if (perStoryCap !== String(settings?.per_story_cap_usd ?? '') && Number(perStoryCap) > 0) {
        payload.per_story_cap_usd = Number(perStoryCap)
      }
      if (budgetProfile !== (settings?.budget_profile || '')) {
        payload.budget_profile = budgetProfile
      }
      // Local servers (spec 8.1): sent when changed; "" clears back to the default.
      if (localComfyuiUrl.trim() !== (settings?.local_comfyui_url || '')) {
        payload.local_comfyui_url = localComfyuiUrl.trim()
      }
      if (localOllamaUrl.trim() !== (settings?.local_ollama_url || '')) {
        payload.local_ollama_url = localOllamaUrl.trim()
      }

      if (Object.keys(payload).length === 0) {
        setMsg('No changes to save')
        setSaving(false)
        return
      }

      const updated = await updateSettings(payload)
      setSettings(updated)
      setCompatUrl(updated.openai_compat_base_url || '')
      setCompatModel(updated.openai_compat_model || '')
      setStoryLlmPremiumChain(updated.story_llm_premium_chain || '')
      setAllowSlowChain(Boolean(updated.allow_slow_chain))
      setAllowPaid(Boolean(updated.allow_paid))
      setPerEpisodeCap(String(updated.per_episode_cap_usd ?? ''))
      setDailyCap(String(updated.daily_cap_usd ?? ''))
      setPerStoryCap(String(updated.per_story_cap_usd ?? ''))
      setBudgetProfile(updated.budget_profile || '')
      setLocalComfyuiUrl(updated.local_comfyui_url || '')
      setLocalOllamaUrl(updated.local_ollama_url || '')
      setGoogleKey('')
      setPexelsKey('')
      setHfToken('')
      setNvidiaKey('')
      setGroqKey('')
      setOpenrouterKey('')
      setMistralKey('')
      setCompatKey('')
      setFalKey('')
      setOpenaiKey('')
      setCloudflareToken('')
      setCloudflareAccountId('')
      setPollinationsKey('')
      setMsg('✅ Saved on the server. These now survive a restart.')
    } catch (err) {
      setMsg('❌ Failed to save: ' + err.message)
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="empty-state"><div className="spinner"></div></div>

  const endpointReady = Boolean(
    settings?.openai_compat_api_key_set && compatUrl && compatModel
  )
  const tested = testedProviders(testResult, genResults)
  const isTested = (key) => tested.has(KEY_PROVIDERS[key])
  const spentToday = Number(settings?.spend_today_usd || 0)
  const dailyCapNow = Number(settings?.daily_cap_usd || 0)
  const dailyShare = dailyCapNow > 0 ? Math.min(100, Math.round((spentToday / dailyCapNow) * 100)) : 0

  return (
    <div className="fade-in settings-page">
      <div className="page-header">
        <div>
          <h2>Settings</h2>
          <p>Configure API keys and default settings</p>
        </div>
      </div>

      <form onSubmit={handleSave}>
        <SettingsTabs tab={tab} onSelect={switchTab} />

        {tab === 'providers' && (
          <div className="settings-grid" role="tabpanel" id="settings-panel-providers" aria-labelledby="settings-tab-providers">
          {/* API keys */}
          <Card className="settings-card">
            <CardHeader icon={KeyRound} title="API keys" subtitle="The analysis chain, voice-over, B-roll and diarization." />
            <CardBody>
            <KeyField
              id="settings-groq-key"
              label="Groq API Key"
              note="first link in the chain"
              isSet={settings?.groq_api_key_set}
              tested={isTested('groq_api_key')}
              value={groqKey}
              onChange={setGroqKey}
              placeholder="Paste your Groq API key"
              hint={<>
                Free, and by far the fastest tier — analysis finishes in seconds
                rather than minutes.{' '}
                <a href="https://console.groq.com/keys" target="_blank" rel="noopener" style={linkStyle}>Get a key →</a>
              </>}
            />

            <KeyField
              id="settings-nvidia-key"
              label="NVIDIA API Key"
              note="last link, the floor"
              isSet={settings?.nvidia_api_key_set}
              tested={isTested('nvidia_api_key')}
              value={nvidiaKey}
              onChange={setNvidiaKey}
              placeholder="Paste your NVIDIA NIM API key"
              hint={<>
                Free, no credit card, but slow: ~12 tokens/s behind a queue that
                can hold a request for a minute. On its own it cannot carry the
                analysis, so a job with only this key is refused unless the
                switch below is on.{' '}
                <a href="https://build.nvidia.com/" target="_blank" rel="noopener" style={linkStyle}>Get a key →</a>
              </>}
            />
            <label className="settings-check" htmlFor="settings-allow-slow-chain">
              <input
                id="settings-allow-slow-chain"
                type="checkbox"
                checked={allowSlowChain}
                onChange={(e) => setAllowSlowChain(e.target.checked)}
                aria-describedby="settings-allow-slow-chain-hint"
              />
              <span>
                Run on the slow chain anyway
                <span className="form-hint" id="settings-allow-slow-chain-hint">
                  Lets a job start when NVIDIA is the only keyed link. Expect
                  the analysis to take tens of minutes, and windows to be
                  skipped when the time budget runs out.
                </span>
              </span>
            </label>

            <KeyField
              id="settings-google-key"
              label="Google Gemini API Key"
              isSet={settings?.google_api_key_set}
              tested={isTested('google_api_key')}
              value={googleKey}
              onChange={setGoogleKey}
              placeholder="Paste your Gemini API key"
              hint={<>
                Free, no credit card. Also the key voice-over uses.{' '}
                <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener" style={linkStyle}>Get a key →</a>
              </>}
            />

            <KeyField
              id="settings-pexels-key"
              label="Pexels API Key"
              isSet={settings?.pexels_api_key_set}
              value={pexelsKey}
              onChange={setPexelsKey}
              placeholder="For B-roll footage (optional)"
              hint="Without it, B-roll is skipped silently and the clips still render."
            />

            <KeyField
              id="settings-openrouter-key"
              label="OpenRouter API Key"
              note="optional chain link"
              isSet={settings?.openrouter_api_key_set}
              tested={isTested('openrouter_api_key')}
              value={openrouterKey}
              onChange={setOpenrouterKey}
              placeholder="Only needed if your chain names openrouter/..."
              hint="Not in the default chain. Add it with LLM_CHAIN or --llm-chain."
            />

            <KeyField
              id="settings-mistral-key"
              label="Mistral API Key"
              note="optional chain link"
              isSet={settings?.mistral_api_key_set}
              tested={isTested('mistral_api_key')}
              value={mistralKey}
              onChange={setMistralKey}
              placeholder="Only needed if your chain names mistral/..."
              hint="Also used by the hosted transcription chain (mistral/voxtral-mini-latest)."
            />

            <KeyField
              id="settings-hf-token"
              label="HuggingFace Token"
              isSet={settings?.hf_token_set}
              value={hfToken}
              onChange={setHfToken}
              placeholder="For split-screen mode (optional)"
              hint="Only for speaker diarization. Split-screen can key off face detection instead, which needs no token."
            />
            </CardBody>
          </Card>
          {/* Chain test */}
          <Card className="settings-card">
            <CardHeader icon={Brain} title="Provider chain" subtitle="Would a job on these keys work?" />
            <CardBody>
            <p className="form-hint settings-card-lead">
              Sends every keyed link one small real analysis request (a short
              test transcript with one clip in it), all providers at once,
              using the keys saved on the server — save new keys first. A job
              needs at least one fast link (Groq, Gemini, OpenRouter or
              Mistral) that completes it.
            </p>
            <button type="button" className="btn btn-secondary" onClick={handleTestChain} disabled={testing}>
              {testing
                ? <><span className="spinner"></span> Testing… {testElapsed}s</>
                : 'Test provider chain'}
            </button>
            {testing && testElapsed >= 20 && (
              <p className="form-hint" style={{ marginTop: '8px' }}>
                Still waiting. Each link may take as long as a job would wait
                for it (up to 180s on the fast tiers, 280s on NVIDIA, whose
                free tier queues). Most answer in seconds.
              </p>
            )}
            {testError && (
              <p className="settings-error" role="alert">
                {testError}
              </p>
            )}
            {testResult && <ChainTestResult result={testResult} />}
            </CardBody>
          </Card>
          {/* AI Story's premium writing chain (plan 22 stage 1, DEC-273) */}
          <Card className="settings-card">
            <CardHeader icon={Brain} title="Story premium writing chain"
              subtitle="Concepts, the bible, the episode script and the first-watch judge." />
            <CardBody>
            <p className="form-hint settings-card-lead">
              The rest of AI Story's writing stays on the provider chain
              above. Empty uses the paid Gemini project (the key on the
              Generation tab) first, then today's chain, free links included.
            </p>
            <Field
              label="Premium chain"
              aside={<KeyBadge set={Boolean(storyLlmPremiumChain || settings?.story_llm_premium_chain)}
                               tested={false} />}
              hint="e.g. gemini-paid/gemini-3.8-flash,gemini/gemini-3.5-flash-lite — a provider/model list, same grammar as LLM_CHAIN."
              htmlFor="settings-story-premium-chain"
            >
              <input
                id="settings-story-premium-chain"
                className="form-input"
                type="text"
                value={storyLlmPremiumChain}
                onChange={(e) => setStoryLlmPremiumChain(e.target.value)}
                placeholder="gemini-paid/gemini-3.8-flash,nvidia/nvidia/nemotron-3-ultra-550b-a55b,gemini/gemini-3.5-flash-lite"
                spellCheck={false}
              />
            </Field>
            <p className="form-hint">
              Not a secret, and never spent by "Test provider chain" above:
              that button only probes LLM_CHAIN, never this one.
            </p>
            </CardBody>
          </Card>
          {/* Custom OpenAI-compatible endpoint */}
          <Card className="settings-card">
            <CardHeader
              icon={LinkIcon}
              title="Custom endpoint (optional)"
              actions={endpointReady
                ? <Badge tone="success" icon={CircleCheck}>Ready</Badge>
                : <Badge tone="neutral">Not set up</Badge>}
            />
            <CardBody>
            <p className="form-hint settings-card-lead">
              Point the analysis step at anything that speaks the OpenAI chat API.
              Only worth setting if you already have a key elsewhere — Groq and
              Gemini above are both free.
            </p>

            <Field label="Preset" htmlFor="settings-compat-preset">
              <select
                id="settings-compat-preset"
                className="form-select"
                value={ENDPOINT_PRESETS.find(p => p.url === compatUrl)?.url || ''}
                onChange={(e) => { if (e.target.value) setCompatUrl(e.target.value) }}
              >
                <option value="">Choose a preset, or type a URL below…</option>
                {ENDPOINT_PRESETS.map(p => (
                  <option key={p.url} value={p.url}>{p.label}</option>
                ))}
              </select>
            </Field>

            <Field
              label="Base URL"
              htmlFor="settings-compat-url"
              hint="Include the version path. Clearing the field falls back to whatever your .env holds."
            >
              <input
                id="settings-compat-url"
                className="form-input"
                type="text"
                placeholder="https://openrouter.ai/api/v1"
                value={compatUrl}
                onChange={(e) => setCompatUrl(e.target.value)}
              />
            </Field>

            <KeyField
              id="settings-compat-key"
              label="API Key"
              isSet={settings?.openai_compat_api_key_set}
              tested={isTested('openai_compat_api_key')}
              value={compatKey}
              onChange={setCompatKey}
              placeholder="Paste the endpoint's API key"
              hint={<>
                A local Ollama ignores this, but one is still required — type any
                value, such as <code>ollama</code>.
              </>}
            />

            <Field
              label="Model"
              htmlFor="settings-compat-model"
              hint="The exact model id the endpoint expects. It has to take a long transcript and answer in JSON."
            >
              <input
                id="settings-compat-model"
                className="form-input"
                type="text"
                placeholder="meta-llama/llama-3.3-70b-instruct"
                value={compatModel}
                onChange={(e) => setCompatModel(e.target.value)}
              />
            </Field>

            <p className={`settings-status${endpointReady ? ' settings-status-ok' : ''}`}>
              {endpointReady
                ? 'Ready. Pick "Custom endpoint" as the provider on a new job.'
                : 'Needs a base URL, a key and a model before it can be used.'}
            </p>
            </CardBody>
          </Card>
          </div>
        )}

        {tab === 'generation' && (
          <div className="settings-grid" role="tabpanel" id="settings-panel-generation" aria-labelledby="settings-tab-generation">
          {/* Generation providers (AI Story, spec 8.6) */}
          <Card className="settings-card">
            <CardHeader icon={Palette} title="Generation providers" subtitle="Images, video and voices of the AI Story mode." />
            <CardBody>
            <p className="form-hint settings-card-lead">
              Gemini reuses the Google key of the Providers tab; OpenRouter its own.
              Paid links never run until the Budget tab allows them.
            </p>
            <KeyField
              id="settings-fal-key"
              label="fal.ai key"
              note="paid: images and video"
              isSet={settings?.fal_key_set}
              tested={isTested('fal_key')}
              value={falKey}
              onChange={setFalKey}
              placeholder="Paste your fal.ai key"
            />
            <KeyField
              id="settings-openai-key"
              label="OpenAI API key"
              note="paid: gpt-image-2"
              isSet={settings?.openai_api_key_set}
              tested={isTested('openai_api_key')}
              value={openaiKey}
              onChange={setOpenaiKey}
              placeholder="Paste your OpenAI API key"
            />
            <KeyField
              id="settings-cloudflare-token"
              label="Cloudflare Workers AI token"
              note="free allowance, ~170 images a day"
              isSet={settings?.cloudflare_api_token_set}
              tested={isTested('cloudflare_api_token')}
              value={cloudflareToken}
              onChange={setCloudflareToken}
              placeholder="Paste your Cloudflare API token"
            />
            <KeyField
              id="settings-cloudflare-account"
              label="Cloudflare account id"
              isSet={settings?.cloudflare_account_id_set}
              tested={isTested('cloudflare_account_id')}
              value={cloudflareAccountId}
              onChange={setCloudflareAccountId}
              placeholder="The account id the token belongs to"
            />
            <KeyField
              id="settings-pollinations-key"
              label="Pollinations key"
              note="optional, keyless works slowly"
              isSet={settings?.pollinations_api_key_set}
              tested={isTested('pollinations_api_key')}
              value={pollinationsKey}
              onChange={setPollinationsKey}
              placeholder="Paste your Pollinations key (optional)"
            />
            <KeyField
              id="settings-gemini-paid-key"
              label="Gemini paid key"
              note="Veo and the nano-banana images — a separate billing-enabled Google project"
              isSet={settings?.gemini_paid_api_key_set}
              tested={isTested('gemini_paid_api_key')}
              value={geminiPaidKey}
              onChange={setGeminiPaidKey}
              placeholder="Paste the billing-enabled project's key"
            />
            </CardBody>
          </Card>
          <ChainLinksPanel
            chains={settings?.generation_chains}
            usage={settings?.usage_today}
            results={genResults}
            testing={genTesting}
            error={genError}
            onTest={handleTestGeneration}
          />
          </div>
        )}

        {tab === 'hardware' && (
          <div className="settings-grid" role="tabpanel" id="settings-panel-hardware" aria-labelledby="settings-tab-hardware">
          <HardwarePanel hardware={hardware} loading={hwLoading} error={hwError} onRefresh={loadHardware} />

          <Card className="settings-card">
            <CardHeader icon={Server} title="Local servers" subtitle="Where ComfyUI and Ollama answer." />
            <CardBody>
            <p className="form-hint settings-card-lead">
              Inside Docker the default is host.docker.internal (the host's
              services); on a host it is 127.0.0.1. Empty = the default.
            </p>
            <Field label="ComfyUI URL" htmlFor="settings-comfyui-url" hint={hardware?.comfyui?.note || undefined}>
              <input id="settings-comfyui-url" className="form-input" type="url" value={localComfyuiUrl}
                onChange={e => setLocalComfyuiUrl(e.target.value)} placeholder="http://127.0.0.1:8188" />
            </Field>
            <Field label="Ollama URL" htmlFor="settings-ollama-url" hint={hardware?.ollama?.note || undefined}>
              <input id="settings-ollama-url" className="form-input" type="url" value={localOllamaUrl}
                onChange={e => setLocalOllamaUrl(e.target.value)} placeholder="http://127.0.0.1:11434" />
            </Field>
            </CardBody>
          </Card>
          <Card className="settings-card">
            <CardHeader icon={Monitor} title="System info" />
            <CardBody>
            <dl className="settings-facts">
              <Row label="GPU" value={settings?.gpu_available
                ? <Badge tone="success" icon={CircleCheck}>Available</Badge>
                : <Badge tone="neutral">Not available</Badge>} />
              <Row label="Default Whisper" value={settings?.default_whisper_model} />
              <Row label="Whisper device" value={settings?.default_whisper_device} />
              <Row label="Default AI" value={settings?.default_ai_provider} />
              <Row label="Custom endpoint" value={endpointReady
                ? <Badge tone="success" icon={CircleCheck}>Configured</Badge>
                : <Badge tone="neutral">Not configured</Badge>} />
              <Row label="Default Ratio" value={settings?.default_ratio} />
            </dl>
            </CardBody>
          </Card>
          </div>
        )}

        {tab === 'budget' && (
          <div className="settings-grid" role="tabpanel" id="settings-panel-budget" aria-labelledby="settings-tab-budget">
          {/* Budget (AI Story, DEC-097) */}
          <Card className="settings-card settings-card-wide">
            <CardHeader
              icon={Wallet}
              title="Budget"
              subtitle="Paid generation providers are never called unless allowed here, and never past these caps."
              actions={allowPaid
                ? <Badge tone="warning" icon={CircleDollarSign}>Paid allowed</Badge>
                : <Badge tone="success">Free only</Badge>}
            />
            <CardBody>
            <p className="form-hint settings-card-lead">Every estimate is shown before it is spent.</p>
            <label className="settings-check" htmlFor="settings-allow-paid">
              <input
                id="settings-allow-paid"
                type="checkbox"
                checked={allowPaid}
                onChange={e => setAllowPaid(e.target.checked)}
              />
              <span>Allow paid providers (within the caps)</span>
            </label>
            <div className="settings-table-wrap">
              <table className="settings-caps">
                <caption className="sr-only">Spending caps, in US dollars, and the spend so far</caption>
                <thead>
                  <tr>
                    <th scope="col">Cap</th>
                    <th scope="col">Limit (USD)</th>
                    <th scope="col">Spent so far</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <th scope="row"><label htmlFor="settings-cap-episode">Per episode</label></th>
                    <td>
                      <input id="settings-cap-episode" className="form-input" type="number" min="0.01" step="0.01" inputMode="decimal"
                        value={perEpisodeCap} onChange={e => setPerEpisodeCap(e.target.value)} />
                    </td>
                    <td className="settings-caps-spend">On each episode's page</td>
                  </tr>
                  <tr>
                    <th scope="row"><label htmlFor="settings-cap-daily">Daily</label></th>
                    <td>
                      <input id="settings-cap-daily" className="form-input" type="number" min="0.01" step="0.01" inputMode="decimal"
                        value={dailyCap} onChange={e => setDailyCap(e.target.value)} />
                    </td>
                    <td className="settings-caps-spend">
                      <span className="settings-caps-amount">${spentToday.toFixed(2)} of ${dailyCapNow.toFixed(2)} today</span>
                      <span
                        className={`settings-caps-meter${dailyShare >= 90 ? ' settings-caps-meter-high' : ''}`}
                        role="img"
                        aria-label={`${dailyShare}% of today's cap spent`}
                      >
                        <span style={{ width: `${dailyShare}%` }}></span>
                      </span>
                    </td>
                  </tr>
                  <tr>
                    <th scope="row"><label htmlFor="settings-cap-story">Per story</label></th>
                    <td>
                      <input id="settings-cap-story" className="form-input" type="number" min="0.01" step="0.01" inputMode="decimal"
                        value={perStoryCap} onChange={e => setPerStoryCap(e.target.value)} />
                    </td>
                    <td className="settings-caps-spend">On each story's episodes</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <Field
              label="Budget profile"
              htmlFor="settings-budget-profile"
              hint={<>In force now: <strong>{settings?.effective_budget_profile || 'free'}</strong></>}
            >
              <select id="settings-budget-profile" className="form-input" value={budgetProfile} onChange={e => setBudgetProfile(e.target.value)}>
                <option value="">auto — free until paid is allowed, then one_dollar</option>
                <option value="free">free — $0.00: free chains or local, stills + motion</option>
                <option value="one_dollar">one_dollar — ≤ $1 per episode: reference images + key shots animated</option>
                <option value="quality">quality — Quality (billed APIs): ≤ $4 per episode, quality image links, every shot animated with its own ambience</option>
              </select>
            </Field>
            </CardBody>
          </Card>
          </div>
        )}

        <div className="settings-savebar">
          <Button type="submit" variant="primary" icon={Save} loading={saving}>
            {saving ? 'Saving…' : 'Save settings'}
          </Button>
          {msg && (
            <p className={`settings-msg${msg.startsWith('✅') ? ' settings-msg-ok' : msg.startsWith('❌') ? ' settings-msg-error' : ''}`} role="status">
              {msg}
            </p>
          )}
        </div>
      </form>
    </div>
  )
}

// One per ChainLinkResult.status in web/api/models.py (a test keeps them in step).
const STATUS_GLYPH = { ok: '✅', alive: '⚠️', failed: '✖', no_key: '⏭', unused: '·' }

// The verdict's colour and headline. The rule itself is the server's (DEC-073,
// DEC-090); this only says it.
const VERDICT_STYLE = {
  ready: { color: 'var(--success)' },
  floor_only: { color: 'var(--warning)' },
  blocked: { color: 'var(--error)' },
  dead: { color: 'var(--error)' },
}

function rowFinding(row) {
  if (row.status !== 'ok' || row.candidates == null) return null
  if (row.found_moment) return `found the test clip (${row.candidates} moment${row.candidates === 1 ? '' : 's'})`
  return `${row.candidates} moment${row.candidates === 1 ? '' : 's'}, not the test clip`
}

/** One row per link, then the verdict: would a job on this chain work? */
function ChainTestResult({ result }) {
  const verdict = result.verdict || (result.ready ? 'ready' : 'dead')
  return (
    <div style={{ marginTop: '12px', fontSize: '13px' }}>
      {result.results.map((row, index) => {
        const finding = rowFinding(row)
        const swapped = row.used_model && row.used_model !== row.model
        return (
          <div
            key={index}
            title={row.work_timeout_seconds
              ? `Allowed ${row.work_timeout_seconds}s for the real request`
              : `Probe timeout: ${row.probe_timeout_seconds}s`}
            style={{ padding: '6px 0', borderBottom: '1px solid var(--border-color)' }}
          >
            <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline', flexWrap: 'wrap' }}>
              <span>{STATUS_GLYPH[row.status] || '·'}</span>
              <code style={{ wordBreak: 'break-all' }}>{row.label}</code>
              {row.latency_seconds != null && (
                <span style={{ color: 'var(--text-tertiary)' }}>{row.latency_seconds.toFixed(1)}s</span>
              )}
              {!row.primary && (
                <span style={{ color: 'var(--text-tertiary)' }}>floor</span>
              )}
              {row.status === 'unused' && (
                <span style={{ color: 'var(--text-tertiary)' }}>not in chain</span>
              )}
            </div>
            {swapped && (
              <div className="form-hint" style={{ marginLeft: '24px' }}>
                ↪ answered as <code>{row.used_model}</code>
              </div>
            )}
            {finding && (
              <div className="form-hint" style={{ marginLeft: '24px' }}>
                {finding}{row.level ? ` · ${row.level}` : ''}
              </div>
            )}
            {row.status === 'no_key' && (
              <div className="form-hint" style={{ marginLeft: '24px' }}>
                No {row.env_key}.{' '}
                {row.signup_url && (
                  <a href={row.signup_url} target="_blank" rel="noopener" style={{ color: 'var(--accent)' }}>Get a key →</a>
                )}
              </div>
            )}
            {(row.status === 'failed' || row.status === 'alive') && row.reason && (
              <div className="form-hint" style={{
                marginLeft: '24px',
                color: row.status === 'failed' ? 'var(--error)' : 'var(--warning)',
                wordBreak: 'break-word',
              }}>
                {row.reason}
              </div>
            )}
            {row.note && (
              <div className="form-hint" style={{ marginLeft: '24px', wordBreak: 'break-word' }}>
                {row.note}
              </div>
            )}
          </div>
        )
      })}
      <div style={{
        marginTop: '10px',
        ...(VERDICT_STYLE[verdict] || VERDICT_STYLE.dead),
        whiteSpace: 'pre-wrap',
        lineHeight: 1.45,
      }}>
        {verdict === 'ready'
          ? `✅ Jobs can start. ${result.live_link} completed the real analysis request first (${result.elapsed_seconds.toFixed(0)}s for the whole test).`
          : `${verdict === 'floor_only' ? '⚠️' : '✖'} ${result.message}`}
      </div>
    </div>
  )
}

// One per GenerationLinkResult.status in web/api/models.py (tests/test_settings_tabs.py keeps them in step).
const GEN_STATUS_GLYPH = { ok: '✅', failed: '✖', no_key: '⏭', no_adapter: '·', unreachable: '🔌', refused: '🔒', skipped: '💸' }

// One per GenerationChainTestResponse.verdict.
const GEN_VERDICT_STYLE = {
  ready: { color: 'var(--success)' },
  paid_only: { color: 'var(--warning)' },
  blocked: { color: 'var(--error)' },
  no_adapter: { color: 'var(--text-tertiary)' },
}

const KIND_LABELS = {
  image: 'Images (text → image)',
  image_edit: 'Image edit (with references)',
  video: 'Video (image → video)',
  tts: 'Voices (TTS)',
  vision: 'Vision (describe frames)',
}

function linkGlyph(row) {
  if (!row.adapter) return '·'
  if (!row.keyed) return '⏭'
  if (row.paid) return row.allowed ? '💸' : '🔒'
  return '✅'
}

/** The rows of one generation chain test, then its verdict. */
function GenerationChainResult({ result }) {
  return (
    <div style={{ marginTop: '12px', fontSize: '13px' }}>
      {result.results.map((row, index) => (
        <div key={index} style={{ padding: '6px 0', borderBottom: '1px solid var(--border-color)' }}>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline', flexWrap: 'wrap' }}>
            <span>{GEN_STATUS_GLYPH[row.status] || '·'}</span>
            <code style={{ wordBreak: 'break-all' }}>{row.label}</code>
            {row.latency_seconds != null && (
              <span style={{ color: 'var(--text-tertiary)' }}>{row.latency_seconds.toFixed(1)}s</span>
            )}
            {row.paid && (
              <span style={{ color: 'var(--text-tertiary)' }}>paid · ${Number(row.est_usd).toFixed(3)}</span>
            )}
          </div>
          {row.reason && (
            <div className="form-hint" style={{
              marginLeft: '24px',
              color: row.status === 'failed' || row.status === 'refused' ? 'var(--error)' : 'var(--text-tertiary)',
              wordBreak: 'break-word',
            }}>
              {row.reason}
            </div>
          )}
          {row.note && (
            <div className="form-hint" style={{ marginLeft: '24px', wordBreak: 'break-word' }}>{row.note}</div>
          )}
          {row.artifact_url && row.artifact_kind === 'image' && (
            <img
              src={row.artifact_url}
              alt={`sample from ${row.label}`}
              style={{ display: 'block', marginLeft: '24px', marginTop: '6px', maxWidth: '160px', borderRadius: '8px', border: '1px solid var(--border-color)' }}
            />
          )}
          {row.artifact_url && row.artifact_kind === 'audio' && (
            <audio controls src={row.artifact_url} style={{ display: 'block', marginLeft: '24px', marginTop: '6px', maxWidth: 'calc(100% - 24px)' }} />
          )}
        </div>
      ))}
      <div style={{
        marginTop: '10px',
        ...(GEN_VERDICT_STYLE[result.verdict] || GEN_VERDICT_STYLE.blocked),
        whiteSpace: 'pre-wrap',
        lineHeight: 1.45,
      }}>
        {result.verdict === 'ready'
          ? `✅ ${result.message} (${result.elapsed_seconds.toFixed(0)}s)`
          : `${result.verdict === 'paid_only' ? '💸' : result.verdict === 'no_adapter' ? '·' : '✖'} ${result.message}`}
      </div>
    </div>
  )
}

const KEY_CHECK_GLYPH = { ok: '✅', bad_key: '✖', no_model: '⚠️', no_key: '⏭', unreachable: '✖', failed: '✖', skipped: '·' }

/**
 * Video links are never test-generated, so a wrong key used to show only on
 * the first clip bought. This asks each keyed hosted link's provider (fal's
 * pricing, Gemini's models.get) whether the key is accepted and the model
 * live -- free, nothing generated (POST /api/settings/check-video-keys).
 * For a fal link it also reads the endpoint's public schema for the prompt
 * limit it publishes (or "not published"); the server keeps what it read and
 * refuses a longer prompt before sending it. Each row's text carries it.
 */
function VideoKeyCheck() {
  const [checking, setChecking] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  const run = async () => {
    setChecking(true)
    setError('')
    try {
      setResult(await checkVideoKeys())
    } catch (err) {
      setError(err.message)
    } finally {
      setChecking(false)
    }
  }

  return (
    <div style={{ marginTop: '10px' }}>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
        <button type="button" className="btn btn-secondary" disabled={checking} onClick={run}>
          {checking ? <><span className="spinner"></span> Asking…</> : 'Ask the providers (free)'}
        </button>
        <span className="form-hint" style={{ margin: 0 }}>
          Checks each video key and model with the provider itself, and reads fal's published prompt limit;
          nothing is generated or billed.
        </span>
      </div>
      {error && <p style={{ marginTop: '8px', fontSize: '13px', color: 'var(--error)' }}>{error}</p>}
      {result && (
        <div style={{ marginTop: '10px', fontSize: '13px' }}>
          {result.results.map((row) => (
            <div key={row.label} style={{ padding: '6px 0', borderBottom: '1px solid var(--border-color)' }}>
              <span>{KEY_CHECK_GLYPH[row.status] || '·'}</span>{' '}
              <code style={{ wordBreak: 'break-all' }}>{row.label}</code>
              {row.text && (
                <div className="form-hint" style={{
                  marginLeft: '24px', wordBreak: 'break-word',
                  color: row.status === 'ok' ? undefined : row.status === 'skipped' ? 'var(--text-tertiary)' : 'var(--error)',
                }}>
                  {row.text}
                </div>
              )}
            </div>
          ))}
          <div style={{ marginTop: '10px', ...(GEN_VERDICT_STYLE[result.verdict] || GEN_VERDICT_STYLE.blocked) }}>
            {result.verdict === 'ready' ? '✅' : '✖'} {result.message}
          </div>
        </div>
      )}
    </div>
  )
}

const KIND_ICONS = { image: ImageIcon, image_edit: Wand2, video: Film, tts: Mic, vision: Eye }

/** One card per generation chain: its links as the runner sees them, a chain test, a test per paid link. */
function ChainLinksPanel({ chains, usage, results, testing, error, onTest }) {
  const entries = Object.entries(chains || {})
  const usageRows = Object.entries(usage || {}).filter(([name]) => name !== 'day')
  return (
    <>
      {entries.map(([kind, chain]) => (
        <Card className="settings-card" key={kind}>
          <CardHeader
            icon={KIND_ICONS[kind] || Palette}
            title={KIND_LABELS[kind] || kind}
            actions={<code className="settings-env">{chain.env}</code>}
          />
          <CardBody>
          <p className="form-hint settings-card-lead" style={{ wordBreak: 'break-all' }}>
            {chain.source === 'env' ? 'From the environment' : 'Shipped default'} · <code>{chain.chain}</code>
          </p>
          {chain.error && <p className="settings-error" role="alert">{chain.error}</p>}
          <div style={{ fontSize: '13px' }}>
            {(chain.links || []).map(row => (
              <div key={row.label} style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', padding: '6px 0', borderBottom: '1px solid var(--border-color)' }}>
                <span aria-hidden="true">{linkGlyph(row)}</span>
                <code style={{ wordBreak: 'break-all' }}>{row.label}</code>
                <span className="link-chip">{row.paid ? `paid · est $${Number(row.est_usd).toFixed(3)}` : 'free'}</span>
                {!row.adapter && <span className="link-chip">no adapter yet (phase 6)</span>}
                {row.adapter && !row.keyed && (
                  <span className="link-chip">
                    no key: {row.missing_keys.join(', ')}
                    {row.signup_url && <> · <a href={row.signup_url} target="_blank" rel="noopener" style={linkStyle}>get one →</a></>}
                  </span>
                )}
                {row.paid && row.keyed && row.adapter && (row.allowed
                  ? (
                    <button type="button" className="btn btn-secondary btn-sm" disabled={testing !== null} onClick={() => onTest(kind, row.label)}>
                      {testing === `${kind}:${row.label}` ? <><span className="spinner"></span> Testing…</> : `Test (est $${Number(row.est_usd).toFixed(3)})`}
                    </button>
                  )
                  : <span className="link-chip link-chip-warn">{row.reason}</span>)}
              </div>
            ))}
          </div>
          <div style={{ marginTop: '10px', display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            <button type="button" className="btn btn-secondary" disabled={testing !== null} onClick={() => onTest(kind)}>
              {testing === kind ? <><span className="spinner"></span> Testing…</> : 'Test chain'}
            </button>
            <span className="form-hint" style={{ margin: 0 }}>
              Runs the free and local links; a paid link is only reported here — test it from its row, once.
            </span>
          </div>
          {error && testing === null && <p className="settings-error" role="alert">{error}</p>}
          {results[kind] && <GenerationChainResult result={results[kind]} />}
          {kind === 'video' && <VideoKeyCheck />}
          </CardBody>
        </Card>
      ))}
      <Card className="settings-card">
        <CardHeader icon={Gauge} title="Free allowance today" />
        <CardBody>
        <p className="form-hint settings-card-lead">
          Calls made today on each free tier, against its published daily limit (UTC day{usage?.day ? ` ${usage.day}` : ''}).
        </p>
        <dl className="settings-facts">
          {usageRows.map(([name, row]) => (
            <Row
              key={name}
              label={name}
              value={<>{row.calls} / {row.rpd} <span style={{ color: 'var(--text-tertiary)' }}>({row.left} left)</span></>}
            />
          ))}
        </dl>
        {usageRows.length === 0 && <span className="form-hint">No daily limit to show.</span>}
        </CardBody>
      </Card>
    </>
  )
}

/** One fact of a <dl className="settings-facts">: a term and its value on one row. */
const Row = ({ label, value }) => (
  <div className="settings-fact">
    <dt>{label}</dt>
    <dd>{value}</dd>
  </div>
)

/**
 * What this machine can generate locally (GET /api/hardware, spec 8.2). On a
 * host that cannot run a good image or video model (no GPU, or under 8 GB:
 * hardware.BILLED_PRESET_PROFILES) the first recommendation is the billed
 * Quality preset (phase 7 stage 7, A18): its row carries the preset's
 * estimate (media_policy.preset_estimate, priced from pricing.py) and the
 * keys it needs, and is shown first, apart from the local models.
 */
function HardwarePanel({ hardware, loading, error, onRefresh }) {
  const recommendations = hardware?.recommendations || []
  const advice = recommendations.filter((r) => r.estimate)
  const rows = recommendations.filter((r) => !r.estimate)
  return (
    <Card className="settings-card">
      <CardHeader
        icon={Cpu}
        title="Local hardware"
        actions={(
          <Button size="sm" icon={RefreshCw} disabled={loading} onClick={() => onRefresh(true)}>
            Probe again
          </Button>
        )}
      />
      <CardBody>
      {loading && <p className="form-hint" role="status"><span className="spinner"></span> Probing this machine…</p>}
      {error && <p className="settings-error" role="alert">{error}</p>}
      {hardware && (
        <>
          <dl className="settings-facts">
            <Row label="Profile" value={<strong>{hardware.profile}</strong>} />
            <Row label="GPU" value={hardware.gpu_name ? `${hardware.gpu_name}${hardware.vram_gb != null ? ` · ${hardware.vram_gb} GB` : ''}` : 'none found'} />
            <Row label="Backend" value={hardware.backend} />
            <Row label="RAM" value={hardware.ram_gb != null ? `${hardware.ram_gb} GB` : '?'} />
            <Row label="Free disk" value={hardware.disk_free_gb != null ? `${hardware.disk_free_gb} GB` : '?'} />
            <Row label="Container" value={hardware.in_container ? 'yes (Docker)' : 'no'} />
            <Row label="ComfyUI" value={hardware.comfyui?.note} />
            <Row label="Ollama" value={hardware.ollama?.reachable
              ? `${hardware.ollama.note}${hardware.ollama.models?.length ? ` — ${hardware.ollama.models.join(', ')}` : ''}`
              : hardware.ollama?.note} />
          </dl>
          {hardware.errors?.length > 0 && (
            <p className="form-hint" style={{ color: 'var(--warning)', wordBreak: 'break-word' }}>
              Probe errors: {hardware.errors.join(' · ')}
            </p>
          )}
          {advice.map((r, index) => (
            <div key={`advice-${index}`} className="settings-advice">
              <div><strong>Recommended: {r.model}</strong></div>
              <p style={{ margin: '6px 0', wordBreak: 'break-word' }}>{r.install_hint}</p>
              <p className="form-hint" style={{ wordBreak: 'break-word' }}>{r.estimate.assumptions}</p>
              {r.keys?.length > 0 && (
                <p className="form-hint" style={{ wordBreak: 'break-word' }}>
                  Keys: {r.keys.join(', ')} (Generation tab). Paid calls also need “Allow paid providers” (Budget tab).
                </p>
              )}
            </div>
          ))}
          <h4 className="settings-subheading">Recommended locally</h4>
          <div style={{ fontSize: '13px' }}>
            {rows.map((r, index) => (
              <div key={index} style={{ padding: '6px 0', borderBottom: '1px solid var(--border-color)' }}>
                <div><strong>{r.task}</strong> · {r.model}{r.workflow && <span className="link-chip" style={{ marginLeft: '6px' }}>workflow {r.workflow}</span>}</div>
                <div className="form-hint" style={{ wordBreak: 'break-word' }}>{r.install_hint}</div>
              </div>
            ))}
          </div>
        </>
      )}
      </CardBody>
    </Card>
  )
}

export default Settings

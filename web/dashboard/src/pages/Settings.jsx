import { useState, useEffect } from 'react'
import { fetchHardware, fetchSettings, testChain, testGenerationChain, updateSettings } from '../api'

const PasswordInput = ({ value, onChange, placeholder, isSet }) => {
  const [show, setShow] = useState(false)
  return (
    <div style={{ position: 'relative' }}>
      <input
        className="form-input"
        type={show ? "text" : "password"}
        placeholder={isSet ? '••••••••••••••••' : placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{ paddingRight: '40px' }}
      />
      <button
        type="button"
        onClick={() => setShow(!show)}
        style={{
          position: 'absolute',
          right: '12px',
          top: '50%',
          transform: 'translateY(-50%)',
          background: 'none',
          border: 'none',
          color: 'var(--text-secondary)',
          cursor: 'pointer',
          fontSize: '14px',
          padding: '4px'
        }}
        title={show ? "Hide" : "Show"}
      >
        {show ? '👀' : '👁️'}
      </button>
    </div>
  )
}

const SetBadge = ({ on }) => on
  ? <span style={{ color: 'var(--success)', marginLeft: '8px' }}>✅ Set</span>
  : null

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
  { id: 'providers', icon: '🔑', label: 'Providers' },
  { id: 'generation', icon: '🎨', label: 'Generation' },
  { id: 'hardware', icon: '💻', label: 'Local hardware' },
  { id: 'budget', icon: '💰', label: 'Budget' },
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

  // The endpoint URL and model are not secrets, so they are prefilled.
  const [compatUrl, setCompatUrl] = useState('')
  const [compatModel, setCompatModel] = useState('')

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

      // Sent whenever they differ from what the server holds, including when
      // cleared: an empty value removes the override and falls back to .env,
      // which is the only way to undo one from here.
      if (compatUrl !== (settings?.openai_compat_base_url || '')) {
        payload.openai_compat_base_url = compatUrl.trim()
      }
      if (compatModel !== (settings?.openai_compat_model || '')) {
        payload.openai_compat_model = compatModel.trim()
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

  return (
    <div className="fade-in">
      <div className="page-header">
        <div>
          <h2>Settings</h2>
          <p>Configure API keys and default settings</p>
        </div>
      </div>

      <form onSubmit={handleSave}>
        <div className="settings-tabs" role="tablist">
          {SETTINGS_TABS.map(t => (
            <button
              key={t.id}
              type="button"
              role="tab"
              aria-selected={tab === t.id}
              className={`settings-tab${tab === t.id ? ' active' : ''}`}
              onClick={() => switchTab(t.id)}
            >
              {t.icon} {t.label}
            </button>
          ))}
        </div>

        {tab === 'providers' && (
          <div className="settings-grid">
          {/* API keys */}
          <div className="settings-section">
            <h3>🔑 API Keys</h3>

            <div className="form-group">
              <label className="form-label">
                Groq API Key
                <span style={{ color: 'var(--text-tertiary)', marginLeft: '6px', fontWeight: 400 }}>
                  — first link in the chain
                </span>
                <SetBadge on={settings?.groq_api_key_set} />
              </label>
              <PasswordInput
                value={groqKey}
                onChange={setGroqKey}
                placeholder="Paste your Groq API key"
                isSet={settings?.groq_api_key_set}
              />
              <p className="form-hint">
                Free, and by far the fastest tier — analysis finishes in seconds
                rather than minutes.{' '}
                <a href="https://console.groq.com/keys" target="_blank" rel="noopener" style={{ color: 'var(--accent)' }}>Get a key →</a>
              </p>
            </div>

            <div className="form-group">
              <label className="form-label">
                NVIDIA API Key
                <span style={{ color: 'var(--text-tertiary)', marginLeft: '6px', fontWeight: 400 }}>
                  — last link, the floor
                </span>
                <SetBadge on={settings?.nvidia_api_key_set} />
              </label>
              <PasswordInput
                value={nvidiaKey}
                onChange={setNvidiaKey}
                placeholder="Paste your NVIDIA NIM API key"
                isSet={settings?.nvidia_api_key_set}
              />
              <p className="form-hint">
                Free, no credit card, but slow: ~12 tokens/s behind a queue that
                can hold a request for a minute. On its own it cannot carry the
                analysis, so a job with only this key is refused unless the
                switch below is on.{' '}
                <a href="https://build.nvidia.com/" target="_blank" rel="noopener" style={{ color: 'var(--accent)' }}>Get a key →</a>
              </p>
              <label style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', marginTop: '8px', fontSize: '13px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={allowSlowChain}
                  onChange={(e) => setAllowSlowChain(e.target.checked)}
                  style={{ marginTop: '2px' }}
                />
                <span>
                  Run on the slow chain anyway
                  <span className="form-hint" style={{ display: 'block', marginTop: '2px' }}>
                    Lets a job start when NVIDIA is the only keyed link. Expect
                    the analysis to take tens of minutes, and windows to be
                    skipped when the time budget runs out.
                  </span>
                </span>
              </label>
            </div>

            <div className="form-group">
              <label className="form-label">
                Google Gemini API Key
                <SetBadge on={settings?.google_api_key_set} />
              </label>
              <PasswordInput
                value={googleKey}
                onChange={setGoogleKey}
                placeholder="Paste your Gemini API key"
                isSet={settings?.google_api_key_set}
              />
              <p className="form-hint">
                Free, no credit card. Also the key voice-over uses.{' '}
                <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener" style={{ color: 'var(--accent)' }}>Get a key →</a>
              </p>
            </div>

            <div className="form-group">
              <label className="form-label">
                Pexels API Key
                <SetBadge on={settings?.pexels_api_key_set} />
              </label>
              <PasswordInput
                value={pexelsKey}
                onChange={setPexelsKey}
                placeholder="For B-roll footage (optional)"
                isSet={settings?.pexels_api_key_set}
              />
              <p className="form-hint">
                Without it, B-roll is skipped silently and the clips still render.
              </p>
            </div>

            <div className="form-group">
              <label className="form-label">
                OpenRouter API Key
                <span style={{ color: 'var(--text-tertiary)', marginLeft: '6px', fontWeight: 400 }}>
                  — optional chain link
                </span>
                <SetBadge on={settings?.openrouter_api_key_set} />
              </label>
              <PasswordInput
                value={openrouterKey}
                onChange={setOpenrouterKey}
                placeholder="Only needed if your chain names openrouter/..."
                isSet={settings?.openrouter_api_key_set}
              />
              <p className="form-hint">
                Not in the default chain. Add it with LLM_CHAIN or --llm-chain.
              </p>
            </div>

            <div className="form-group">
              <label className="form-label">
                Mistral API Key
                <span style={{ color: 'var(--text-tertiary)', marginLeft: '6px', fontWeight: 400 }}>
                  — optional chain link
                </span>
                <SetBadge on={settings?.mistral_api_key_set} />
              </label>
              <PasswordInput
                value={mistralKey}
                onChange={setMistralKey}
                placeholder="Only needed if your chain names mistral/..."
                isSet={settings?.mistral_api_key_set}
              />
              <p className="form-hint">
                Also used by the hosted transcription chain
                (mistral/voxtral-mini-latest).
              </p>
            </div>

            <div className="form-group">
              <label className="form-label">
                HuggingFace Token
                <SetBadge on={settings?.hf_token_set} />
              </label>
              <PasswordInput
                value={hfToken}
                onChange={setHfToken}
                placeholder="For split-screen mode (optional)"
                isSet={settings?.hf_token_set}
              />
              <p className="form-hint">
                Only for speaker diarization. Split-screen can key off face
                detection instead, which needs no token.
              </p>
            </div>
          </div>
          {/* Chain test */}
          <div className="settings-section">
            <h3>🩺 Provider chain</h3>
            <p className="form-hint" style={{ marginTop: '-6px', marginBottom: '14px' }}>
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
              <p style={{ marginTop: '10px', fontSize: '13px', color: 'var(--error)', whiteSpace: 'pre-wrap' }}>
                {testError}
              </p>
            )}
            {testResult && <ChainTestResult result={testResult} />}
          </div>
          {/* Custom OpenAI-compatible endpoint */}
          <div className="settings-section">
            <h3>🔌 Custom endpoint (optional)</h3>
            <p className="form-hint" style={{ marginTop: '-6px', marginBottom: '14px' }}>
              Point the analysis step at anything that speaks the OpenAI chat API.
              Only worth setting if you already have a key elsewhere — Groq and
              Gemini above are both free.
            </p>

            <div className="form-group">
              <label className="form-label">Preset</label>
              <select
                className="form-select"
                value={ENDPOINT_PRESETS.find(p => p.url === compatUrl)?.url || ''}
                onChange={(e) => { if (e.target.value) setCompatUrl(e.target.value) }}
              >
                <option value="">Choose a preset, or type a URL below…</option>
                {ENDPOINT_PRESETS.map(p => (
                  <option key={p.url} value={p.url}>{p.label}</option>
                ))}
              </select>
            </div>

            <div className="form-group">
              <label className="form-label">Base URL</label>
              <input
                className="form-input"
                type="text"
                placeholder="https://openrouter.ai/api/v1"
                value={compatUrl}
                onChange={(e) => setCompatUrl(e.target.value)}
              />
              <p className="form-hint">
                Include the version path. Clearing the field falls back to whatever
                your .env holds.
              </p>
            </div>

            <div className="form-group">
              <label className="form-label">
                API Key
                <SetBadge on={settings?.openai_compat_api_key_set} />
              </label>
              <PasswordInput
                value={compatKey}
                onChange={setCompatKey}
                placeholder="Paste the endpoint's API key"
                isSet={settings?.openai_compat_api_key_set}
              />
              <p className="form-hint">
                A local Ollama ignores this, but one is still required — type any
                value, such as <code>ollama</code>.
              </p>
            </div>

            <div className="form-group">
              <label className="form-label">Model</label>
              <input
                className="form-input"
                type="text"
                placeholder="meta-llama/llama-3.3-70b-instruct"
                value={compatModel}
                onChange={(e) => setCompatModel(e.target.value)}
              />
              <p className="form-hint">
                The exact model id the endpoint expects. It has to take a long
                transcript and answer in JSON.
              </p>
            </div>

            <div style={{
              fontSize: '13px',
              color: endpointReady ? 'var(--success)' : 'var(--text-tertiary)',
            }}>
              {endpointReady
                ? '✅ Ready. Pick "Custom endpoint" as the provider on a new job.'
                : '⚪ Needs a base URL, a key and a model before it can be used.'}
            </div>
          </div>
          </div>
        )}

        {tab === 'generation' && (
          <div className="settings-grid">
          {/* Generation providers (AI Story, spec 8.6) */}
          <div className="settings-section">
            <h3>🎨 Generation providers</h3>
            <p className="form-hint" style={{ marginTop: '-6px', marginBottom: '14px' }}>
              Image, video and voice providers of the AI Story mode. Gemini reuses
              the Google key above; OpenRouter its own. Paid links never run until
              the Budget below allows them.
            </p>
            <div className="form-group">
              <label className="form-label">
                fal.ai key
                <span style={{ color: 'var(--text-tertiary)', marginLeft: '6px', fontWeight: 400 }}>— paid: images and video</span>
                <SetBadge on={settings?.fal_key_set} />
              </label>
              <PasswordInput value={falKey} onChange={setFalKey} placeholder="Paste your fal.ai key" isSet={settings?.fal_key_set} />
            </div>
            <div className="form-group">
              <label className="form-label">
                OpenAI API key
                <span style={{ color: 'var(--text-tertiary)', marginLeft: '6px', fontWeight: 400 }}>— paid: gpt-image-2</span>
                <SetBadge on={settings?.openai_api_key_set} />
              </label>
              <PasswordInput value={openaiKey} onChange={setOpenaiKey} placeholder="Paste your OpenAI API key" isSet={settings?.openai_api_key_set} />
            </div>
            <div className="form-group">
              <label className="form-label">
                Cloudflare Workers AI token
                <span style={{ color: 'var(--text-tertiary)', marginLeft: '6px', fontWeight: 400 }}>— free allowance, ~170 images a day</span>
                <SetBadge on={settings?.cloudflare_api_token_set} />
              </label>
              <PasswordInput value={cloudflareToken} onChange={setCloudflareToken} placeholder="Paste your Cloudflare API token" isSet={settings?.cloudflare_api_token_set} />
            </div>
            <div className="form-group">
              <label className="form-label">
                Cloudflare account id
                <SetBadge on={settings?.cloudflare_account_id_set} />
              </label>
              <PasswordInput value={cloudflareAccountId} onChange={setCloudflareAccountId} placeholder="The account id the token belongs to" isSet={settings?.cloudflare_account_id_set} />
            </div>
            <div className="form-group">
              <label className="form-label">
                Pollinations key
                <span style={{ color: 'var(--text-tertiary)', marginLeft: '6px', fontWeight: 400 }}>— optional, keyless works slowly</span>
                <SetBadge on={settings?.pollinations_api_key_set} />
              </label>
              <PasswordInput value={pollinationsKey} onChange={setPollinationsKey} placeholder="Paste your Pollinations key (optional)" isSet={settings?.pollinations_api_key_set} />
            </div>
          </div>
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
          <div className="settings-grid">
          <HardwarePanel hardware={hardware} loading={hwLoading} error={hwError} onRefresh={loadHardware} />

          <div className="settings-section">
            <h3>🔗 Local servers</h3>
            <p className="form-hint" style={{ marginTop: '-6px', marginBottom: '14px' }}>
              Where ComfyUI and Ollama answer. Inside Docker the default is
              host.docker.internal (the host's services); on a host it is
              127.0.0.1. Empty = the default.
            </p>
            <div className="form-group">
              <label className="form-label">ComfyUI URL</label>
              <input className="form-input" type="url" value={localComfyuiUrl}
                onChange={e => setLocalComfyuiUrl(e.target.value)} placeholder="http://127.0.0.1:8188" />
              {hardware?.comfyui?.note && <p className="form-hint" style={{ wordBreak: 'break-word' }}>{hardware.comfyui.note}</p>}
            </div>
            <div className="form-group">
              <label className="form-label">Ollama URL</label>
              <input className="form-input" type="url" value={localOllamaUrl}
                onChange={e => setLocalOllamaUrl(e.target.value)} placeholder="http://127.0.0.1:11434" />
              {hardware?.ollama?.note && <p className="form-hint" style={{ wordBreak: 'break-word' }}>{hardware.ollama.note}</p>}
            </div>
          </div>
          <div className="settings-section">
            <h3>💻 System Info</h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', fontSize: '13px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-secondary)' }}>GPU</span>
                <span style={{ color: settings?.gpu_available ? 'var(--success)' : 'var(--text-tertiary)' }}>
                  {settings?.gpu_available ? '✅ Available' : '⚪ Not available'}
                </span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-secondary)' }}>Default Whisper</span>
                <span>{settings?.default_whisper_model}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-secondary)' }}>Whisper device</span>
                <span>{settings?.default_whisper_device}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-secondary)' }}>Default AI</span>
                <span>{settings?.default_ai_provider}</span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-secondary)' }}>Custom endpoint</span>
                <span style={{ color: endpointReady ? 'var(--success)' : 'var(--text-tertiary)' }}>
                  {endpointReady ? '✅ Configured' : '⚪ Not configured'}
                </span>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ color: 'var(--text-secondary)' }}>Default Ratio</span>
                <span>{settings?.default_ratio}</span>
              </div>
            </div>
          </div>
          </div>
        )}

        {tab === 'budget' && (
          <div className="settings-grid">
          {/* Budget (AI Story, DEC-097) */}
          <div className="settings-section">
            <h3>💰 Budget</h3>
            <p className="form-hint" style={{ marginTop: '-6px', marginBottom: '14px' }}>
              Paid generation providers are never called unless allowed here, and
              never past these caps. Every estimate is shown before it is spent.
            </p>
            <label className="form-label" style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={allowPaid}
                onChange={e => setAllowPaid(e.target.checked)}
              />
              Allow paid providers (within the caps)
            </label>
            <div className="settings-grid" style={{ marginTop: '12px' }}>
              <div className="form-group">
                <label className="form-label">Per episode cap (USD)</label>
                <input className="form-input" type="number" min="0.01" step="0.01" inputMode="decimal"
                  value={perEpisodeCap} onChange={e => setPerEpisodeCap(e.target.value)} />
              </div>
              <div className="form-group">
                <label className="form-label">Daily cap (USD)</label>
                <input className="form-input" type="number" min="0.01" step="0.01" inputMode="decimal"
                  value={dailyCap} onChange={e => setDailyCap(e.target.value)} />
              </div>
              <div className="form-group">
                <label className="form-label">Per story cap (USD)</label>
                <input className="form-input" type="number" min="0.01" step="0.01" inputMode="decimal"
                  value={perStoryCap} onChange={e => setPerStoryCap(e.target.value)} />
              </div>
              <div className="form-group">
                <label className="form-label">Budget profile</label>
                <select className="form-input" value={budgetProfile} onChange={e => setBudgetProfile(e.target.value)}>
                  <option value="">auto — free until paid is allowed, then one_dollar</option>
                  <option value="free">free — $0.00: free chains or local, stills + motion</option>
                  <option value="one_dollar">one_dollar — ≤ $1 per episode: reference images + key shots animated</option>
                  <option value="quality">quality — your cap: animate every shot</option>
                </select>
                <p className="form-hint">
                  In force now: <strong>{settings?.effective_budget_profile || 'free'}</strong>
                  {' · '}spent today ${Number(settings?.spend_today_usd || 0).toFixed(2)} of ${Number(settings?.daily_cap_usd || 0).toFixed(2)}
                </p>
              </div>
            </div>
          </div>
          </div>
        )}

        {msg && (
          <div style={{ marginTop: '16px', fontSize: '13px', color: msg.startsWith('✅') ? 'var(--success)' : 'var(--error)' }}>
            {msg}
          </div>
        )}

        <button type="submit" className="btn btn-primary" disabled={saving} style={{ marginTop: '20px' }}>
          {saving ? <><span className="spinner"></span> Saving...</> : '💾 Save Settings'}
        </button>
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

/** One card per generation chain: its links as the runner sees them, a chain test, a test per paid link. */
function ChainLinksPanel({ chains, usage, results, testing, error, onTest }) {
  const entries = Object.entries(chains || {})
  const usageRows = Object.entries(usage || {}).filter(([name]) => name !== 'day')
  return (
    <>
      {entries.map(([kind, chain]) => (
        <div className="settings-section" key={kind}>
          <h3>
            {KIND_LABELS[kind] || kind}
            <code style={{ fontSize: '11px', fontWeight: 400, color: 'var(--text-tertiary)' }}>{chain.env}</code>
          </h3>
          <p className="form-hint" style={{ marginTop: '-6px', marginBottom: '10px', wordBreak: 'break-all' }}>
            {chain.source === 'env' ? 'From the environment' : 'Shipped default'} · <code>{chain.chain}</code>
          </p>
          {chain.error && <p style={{ color: 'var(--error)', fontSize: '13px' }}>{chain.error}</p>}
          <div style={{ fontSize: '13px' }}>
            {(chain.links || []).map(row => (
              <div key={row.label} style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', padding: '6px 0', borderBottom: '1px solid var(--border-color)' }}>
                <span>{linkGlyph(row)}</span>
                <code style={{ wordBreak: 'break-all' }}>{row.label}</code>
                <span className="link-chip">{row.paid ? `paid · est $${Number(row.est_usd).toFixed(3)}` : 'free'}</span>
                {!row.adapter && <span className="link-chip">no adapter yet (phase 6)</span>}
                {row.adapter && !row.keyed && (
                  <span className="link-chip">
                    no key: {row.missing_keys.join(', ')}
                    {row.signup_url && <> · <a href={row.signup_url} target="_blank" rel="noopener" style={{ color: 'var(--accent)' }}>get one →</a></>}
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
          {error && testing === null && <p style={{ marginTop: '8px', fontSize: '13px', color: 'var(--error)', whiteSpace: 'pre-wrap' }}>{error}</p>}
          {results[kind] && <GenerationChainResult result={results[kind]} />}
        </div>
      ))}
      <div className="settings-section">
        <h3>📊 Free allowance today</h3>
        <p className="form-hint" style={{ marginTop: '-6px', marginBottom: '10px' }}>
          Calls made today on each free tier, against its published daily limit (UTC day{usage?.day ? ` ${usage.day}` : ''}).
        </p>
        <div style={{ fontSize: '13px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {usageRows.map(([name, row]) => (
            <div key={name} style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
              <span style={{ color: 'var(--text-secondary)' }}>{name}</span>
              <span>{row.calls} / {row.rpd} <span style={{ color: 'var(--text-tertiary)' }}>({row.left} left)</span></span>
            </div>
          ))}
          {usageRows.length === 0 && <span className="form-hint">No daily limit to show.</span>}
        </div>
      </div>
    </>
  )
}

const Row = ({ label, value }) => (
  <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px' }}>
    <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
    <span style={{ textAlign: 'right', wordBreak: 'break-word' }}>{value}</span>
  </div>
)

/** What this machine can generate locally (GET /api/hardware, spec 8.2). */
function HardwarePanel({ hardware, loading, error, onRefresh }) {
  const rows = hardware?.recommendations || []
  return (
    <div className="settings-section">
      <h3>🖥️ Local hardware</h3>
      {loading && <p className="form-hint"><span className="spinner"></span> Probing this machine…</p>}
      {error && <p style={{ color: 'var(--error)', fontSize: '13px' }}>{error}</p>}
      {hardware && (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', fontSize: '13px' }}>
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
          </div>
          {hardware.errors?.length > 0 && (
            <p className="form-hint" style={{ color: 'var(--warning)', wordBreak: 'break-word' }}>
              Probe errors: {hardware.errors.join(' · ')}
            </p>
          )}
          <h4 style={{ margin: '14px 0 6px', fontSize: '13px' }}>Recommended locally</h4>
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
      <button type="button" className="btn btn-secondary btn-sm" style={{ marginTop: '10px' }} disabled={loading} onClick={() => onRefresh(true)}>
        Probe again
      </button>
    </div>
  )
}

export default Settings

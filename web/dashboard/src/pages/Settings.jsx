import { useState, useEffect } from 'react'
import { fetchSettings, testChain, updateSettings } from '../api'

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

function Settings() {
  const [settings, setSettings] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')

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

  // The endpoint URL and model are not secrets, so they are prefilled.
  const [compatUrl, setCompatUrl] = useState('')
  const [compatModel, setCompatModel] = useState('')

  // Run a job even when only the slow floor (NVIDIA) has a key. Prefilled.
  const [allowSlowChain, setAllowSlowChain] = useState(false)

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

  useEffect(() => {
    fetchSettings()
      .then(data => {
        setSettings(data)
        setCompatUrl(data.openai_compat_base_url || '')
        setCompatModel(data.openai_compat_model || '')
        setAllowSlowChain(Boolean(data.allow_slow_chain))
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
      setGoogleKey('')
      setPexelsKey('')
      setHfToken('')
      setNvidiaKey('')
      setGroqKey('')
      setOpenrouterKey('')
      setMistralKey('')
      setCompatKey('')
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
              Asks every link of the chain for one word, with the keys saved on
              the server — save new keys first. A job needs at least one fast
              link (Groq, Gemini, OpenRouter or Mistral) to have a key.
            </p>
            <button type="button" className="btn btn-secondary" onClick={handleTestChain} disabled={testing}>
              {testing
                ? <><span className="spinner"></span> Testing… {testElapsed}s</>
                : 'Test provider chain'}
            </button>
            {testing && testElapsed >= 20 && (
              <p className="form-hint" style={{ marginTop: '8px' }}>
                Still waiting. NVIDIA's free tier queues requests and is allowed
                up to 120s to answer.
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

          {/* System info */}
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

const STATUS_GLYPH = { ok: '✅', no_key: '⏭', failed: '✖' }

/** One row per link, then the verdict: would a job on this chain start? */
function ChainTestResult({ result }) {
  return (
    <div style={{ marginTop: '12px', fontSize: '13px' }}>
      {result.results.map((row) => (
        <div
          key={row.label}
          title={`Probe timeout: ${row.probe_timeout_seconds}s`}
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
          </div>
          {row.status === 'no_key' && (
            <div className="form-hint" style={{ marginLeft: '24px' }}>
              No {row.env_key}.{' '}
              {row.signup_url && (
                <a href={row.signup_url} target="_blank" rel="noopener" style={{ color: 'var(--accent)' }}>Get a free key →</a>
              )}
            </div>
          )}
          {row.status === 'failed' && (
            <div className="form-hint" style={{ marginLeft: '24px', color: 'var(--error)', wordBreak: 'break-word' }}>
              {row.reason}
            </div>
          )}
        </div>
      ))}
      <div style={{
        marginTop: '10px',
        color: result.ready ? 'var(--success)' : 'var(--error)',
        whiteSpace: 'pre-wrap',
        lineHeight: 1.45,
      }}>
        {result.ready
          ? `✅ Jobs can start. ${result.live_link} answered first (${result.elapsed_seconds.toFixed(0)}s for the whole test).`
          : result.message}
      </div>
    </div>
  )
}

export default Settings

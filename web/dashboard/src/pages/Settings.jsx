import { useState, useEffect } from 'react'
import { fetchBrollStatus, fetchSettings, testChain, updateSettings } from '../api'
import { Badge, Button, Card, CardBody, CardHeader, Field } from '../ui'
import { Brain, CircleCheck, Eye, EyeOff, Film, KeyRound, LinkIcon, Monitor, Save } from '../ui/icons'

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
// "<provider>/<model>".
const KEY_PROVIDERS = {
  groq_api_key: 'groq',
  nvidia_api_key: 'nvidia',
  google_api_key: 'gemini',
  openrouter_api_key: 'openrouter',
  mistral_api_key: 'mistral',
  openai_compat_api_key: 'custom',
  gemini_paid_api_key: 'gemini_paid',
  anthropic_api_key: 'anthropic',
}

function providerOfLabel(label) {
  const text = String(label || '')
  // gemini-paid/ links run on the paid, billing-enabled Google project's key.
  if (/^gemini-paid\//.test(text)) return 'gemini_paid'
  return text.split('/')[0]
}

/** The providers the chain test on this page got a real answer from (status "ok"). */
function testedProviders(testResult) {
  const rows = (testResult && testResult.results) || []
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

// The three B-roll sources (plan 23 stage B2), the same names the server accepts.
const BROLL_SOURCE_NAMES = ['local', 'pexels', 'pixabay']

/** The names in a typed source order that are not a source; [] when it is valid or empty. */
const unknownBrollSources = (text) =>
  text.split(',').map(s => s.trim().toLowerCase()).filter(s => s && !BROLL_SOURCE_NAMES.includes(s))

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
  // Paid analysis links: gemini-paid/ (a separate, billing-enabled Google
  // project) and anthropic/ (Claude on the Anthropic API).
  const [geminiPaidKey, setGeminiPaidKey] = useState('')
  const [anthropicKey, setAnthropicKey] = useState('')
  // B-roll sources (plan 23 stage B2): the Pixabay key is a secret; the order and the folder are prefilled.
  const [pixabayKey, setPixabayKey] = useState('')
  const [brollSources, setBrollSources] = useState('')
  const [brollLocalDir, setBrollLocalDir] = useState('')
  const [brollStatus, setBrollStatus] = useState(null)

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

  const loadBrollStatus = () => {
    fetchBrollStatus().then(setBrollStatus).catch(() => setBrollStatus(null))
  }

  useEffect(() => {
    loadBrollStatus()
    fetchSettings()
      .then(data => {
        setSettings(data)
        setBrollSources(data.broll_sources || '')
        setBrollLocalDir(data.broll_local_dir || '')
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
      if (geminiPaidKey) payload.gemini_paid_api_key = geminiPaidKey
      if (anthropicKey) payload.anthropic_api_key = anthropicKey
      if (pixabayKey) payload.pixabay_api_key = pixabayKey

      // B-roll order and folder: sent when changed; "" restores the default order / clears the folder.
      const badSources = unknownBrollSources(brollSources)
      if (badSources.length) {
        setMsg(`❌ Unknown B-roll source: ${badSources.join(', ')}. Use ${BROLL_SOURCE_NAMES.join(', ')}.`)
        setSaving(false)
        return
      }
      if (brollSources.trim() !== (settings?.broll_sources || '')) {
        payload.broll_sources = brollSources.trim()
      }
      if (brollLocalDir.trim() !== (settings?.broll_local_dir || '')) {
        payload.broll_local_dir = brollLocalDir.trim()
      }

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
      setGeminiPaidKey('')
      setAnthropicKey('')
      setPixabayKey('')
      setBrollSources(updated.broll_sources || '')
      setBrollLocalDir(updated.broll_local_dir || '')
      loadBrollStatus()
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
  const tested = testedProviders(testResult)
  const isTested = (key) => tested.has(KEY_PROVIDERS[key])

  return (
    <div className="fade-in settings-page">
      <div className="page-header">
        <div>
          <h2>Settings</h2>
          <p>Your keys and where the app finds extra footage</p>
        </div>
      </div>

      <form onSubmit={handleSave}>
        <div className="settings-grid">
          {/* API keys */}
          <Card className="settings-card">
            <CardHeader icon={KeyRound} title="API keys" subtitle="The keys that let the app analyse your videos, speak and find extra footage." />
            <CardBody>
            <KeyField
              id="settings-groq-key"
              label="Groq API Key"
              note="tried first"
              isSet={settings?.groq_api_key_set}
              tested={isTested('groq_api_key')}
              value={groqKey}
              onChange={setGroqKey}
              placeholder="Paste your Groq API key"
              hint={<>
                Free, and by far the fastest option — analysis finishes in seconds
                rather than minutes.{' '}
                <a href="https://console.groq.com/keys" target="_blank" rel="noopener" style={linkStyle}>Get a key →</a>
              </>}
            />

            <KeyField
              id="settings-nvidia-key"
              label="NVIDIA API Key"
              note="tried last, slow"
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
                Run on the slow service anyway
                <span className="form-hint" id="settings-allow-slow-chain-hint">
                  Lets a job start when NVIDIA is the only key you added. Expect
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
              hint="With no B-roll source at all, B-roll is skipped silently and the clips still render."
            />

            <KeyField
              id="settings-openrouter-key"
              label="OpenRouter API Key"
              note="optional"
              isSet={settings?.openrouter_api_key_set}
              tested={isTested('openrouter_api_key')}
              value={openrouterKey}
              onChange={setOpenrouterKey}
              placeholder="Only needed if you use OpenRouter"
              hint="Not used unless you add it to the list of analysis services."
            />

            <KeyField
              id="settings-mistral-key"
              label="Mistral API Key"
              note="optional"
              isSet={settings?.mistral_api_key_set}
              tested={isTested('mistral_api_key')}
              value={mistralKey}
              onChange={setMistralKey}
              placeholder="Only needed if you use Mistral"
              hint="Also used to turn speech into text, when you choose it."
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

            <KeyField
              id="settings-gemini-paid-key"
              label="Gemini paid key"
              note="optional, paid"
              isSet={settings?.gemini_paid_api_key_set}
              tested={isTested('gemini_paid_api_key')}
              value={geminiPaidKey}
              onChange={setGeminiPaidKey}
              placeholder="Paste the billing-enabled project's key"
              hint="A separate Google project with billing on. Only used by gemini-paid/ links of the list of analysis services."
            />

            <KeyField
              id="settings-anthropic-key"
              label="Anthropic API key"
              note="optional, paid"
              isSet={settings?.anthropic_api_key_set}
              tested={isTested('anthropic_api_key')}
              value={anthropicKey}
              onChange={setAnthropicKey}
              placeholder="sk-ant-…"
              hint="To let Claude analyse your videos, add an anthropic/ link to the list of analysis services. Every request is billed."
            />
            </CardBody>
          </Card>
          {/* Chain test */}
          <Card className="settings-card">
            <CardHeader icon={Brain} title="Check my keys" subtitle="Tests that the keys above can really analyse a video." />
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
                : 'Test my keys'}
            </button>
            {testing && testElapsed >= 20 && (
              <p className="form-hint" style={{ marginTop: '8px' }}>
                Still waiting. Each link may take as long as a job would wait
                for it (up to 3 minutes on the fast services, 5 on NVIDIA, whose
                free service queues). Most answer in seconds.
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
          {/* Custom OpenAI-compatible endpoint */}
          <Card className="settings-card">
            <CardHeader
              icon={LinkIcon}
              title="Another AI service (optional)"
              subtitle="Use any other AI service that works like OpenAI's."
              actions={endpointReady
                ? <Badge tone="success" icon={CircleCheck}>Ready</Badge>
                : <Badge tone="neutral">Not set up</Badge>}
            />
            <CardBody>
            <p className="form-hint settings-card-lead">
              Point the analysis step at any service that works like OpenAI's.
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
              hint="The exact model name the service expects. It has to take a long transcript and answer in a fixed format."
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
          {/* B-roll sources for Clips mode (plan 23 stage B2) */}
          <Card className="settings-card">
            <CardHeader
              icon={Film}
              title="Extra footage"
              subtitle="Where the app finds extra footage to cut into your clips."
              actions={settings?.broll_available
                ? <Badge tone="success" icon={CircleCheck}>Ready</Badge>
                : <Badge tone="neutral">No source</Badge>}
            />
            <CardBody>
            <p className="form-hint settings-card-lead">
              Clips mode tries the sources in order and credits the footage in the
              render manifest. A source with no key or folder is skipped; the
              Pexels key above is the third.
            </p>

            <KeyField
              id="settings-pixabay-key"
              label="Pixabay API Key"
              isSet={settings?.pixabay_api_key_set}
              value={pixabayKey}
              onChange={setPixabayKey}
              placeholder="For Pixabay footage (optional)"
              hint={<>
                Free.{' '}
                <a href="https://pixabay.com/api/docs/" target="_blank" rel="noopener" style={linkStyle}>Get a key →</a>
              </>}
            />

            <Field
              label="Source order"
              htmlFor="settings-broll-sources"
              hint="Any of local, pexels, pixabay, comma separated. Empty means local,pexels,pixabay."
              error={unknownBrollSources(brollSources).length ? `Unknown source: ${unknownBrollSources(brollSources).join(', ')}` : undefined}
            >
              <input
                id="settings-broll-sources"
                className="form-input"
                type="text"
                placeholder="local,pexels,pixabay"
                value={brollSources}
                onChange={(e) => setBrollSources(e.target.value)}
              />
            </Field>

            <Field
              label="Local folder"
              htmlFor="settings-broll-dir"
              hint={<>
                Your own clips (mp4, mov, webm). It has to be{' '}
                <code>{brollStatus?.root || '/app/broll'}</code> or a folder inside it.
              </>}
            >
              <input
                id="settings-broll-dir"
                className="form-input"
                type="text"
                placeholder={brollStatus?.root || '/app/broll'}
                value={brollLocalDir}
                onChange={(e) => setBrollLocalDir(e.target.value)}
              />
            </Field>
            <p className={`settings-status${brollStatus?.local_clips ? ' settings-status-ok' : ''}`}>
              {!brollStatus
                ? 'Folder status unavailable.'
                : !brollStatus.local_dir
                  ? 'No local folder set.'
                  : `Found ${brollStatus.local_clips} clip${brollStatus.local_clips === 1 ? '' : 's'} in ${brollStatus.local_dir}.`}
            </p>
            </CardBody>
          </Card>
          <Card className="settings-card">
            <CardHeader icon={Monitor} title="System info" subtitle="What this computer and the app are set up with." />
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
const STATUS_GLYPH = { ok: '✅', alive: '⚠️', failed: '✖', no_key: '⏭', unused: '·', listed: '🔑' }

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
                <span style={{ color: 'var(--text-tertiary)' }}>not used</span>
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

/** One fact of a <dl className="settings-facts">: a term and its value on one row. */
const Row = ({ label, value }) => (
  <div className="settings-fact">
    <dt>{label}</dt>
    <dd>{value}</dd>
  </div>
)

export default Settings

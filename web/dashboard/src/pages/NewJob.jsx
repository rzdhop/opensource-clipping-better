import { useState, useRef, useEffect } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { createJob, uploadVideo, fetchSettings } from '../api'

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}

function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return null
  if (seconds < 60) return `${Math.ceil(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  if (mins < 60) return `${mins}m ${secs}s`
  return `${Math.floor(mins / 60)}h ${mins % 60}m`
}

/**
 * Quality presets.
 *
 * These drive resolution and the two rate-control values, and deliberately
 * leave video_preset and video_bitrate on "auto". The encoder is detected at
 * run time (NVENC, AMF, VAAPI or libx264) and the same preset string is handed
 * to whichever one wins -- but their vocabularies do not overlap ("veryfast"
 * is meaningless to NVENC, "p1" to libx264), so pinning one would break the
 * other. Bitrate already scales itself from the render height.
 *
 * Balanced is byte-for-byte the backend's own defaults, so leaving the preset
 * alone changes nothing about how clips are rendered today.
 */
const QUALITY_PRESETS = {
  fast: {
    label: 'Fast',
    desc: 'Draft — 720p, quickest render',
    render_height: '720',
    video_cq: 30,
    video_crf: 26,
    video_scale_algo: 'bilinear',
  },
  balanced: {
    label: 'Balanced',
    desc: 'Default — 1080p',
    render_height: '1080',
    video_cq: 23,
    video_crf: 20,
    video_scale_algo: 'lanczos',
  },
  best: {
    label: 'Best',
    desc: 'Highest quality — slowest',
    render_height: '1080',
    video_cq: 19,
    video_crf: 17,
    video_scale_algo: 'lanczos',
  },
}

function matchPreset({ renderHeight, videoCq, videoCrf, videoScaleAlgo }) {
  const found = Object.entries(QUALITY_PRESETS).find(([, p]) =>
    p.render_height === String(renderHeight) &&
    p.video_cq === Number(videoCq) &&
    p.video_crf === Number(videoCrf) &&
    p.video_scale_algo === videoScaleAlgo
  )
  return found ? found[0] : 'custom'
}

/** A one-line notice under the control it concerns. */
function Notice({ kind = 'info', children }) {
  const color = { warn: 'var(--warning)', error: 'var(--error)' }[kind] || 'var(--info)'
  const bg = { warn: 'var(--warning-dim)', error: 'var(--error-dim)' }[kind] || 'var(--info-dim)'
  return (
    <div style={{
      background: bg,
      borderRadius: 'var(--radius-sm)',
      padding: '8px 10px',
      marginTop: '8px',
      fontSize: '12.5px',
      color,
      lineHeight: 1.45,
    }}>
      {children}
    </div>
  )
}

/**
 * Byte counter, percentage and bar for an in-flight upload.
 *
 * A 2GB video over a slow link takes long enough that a bare spinner reads
 * as "hung". `startedAt` drives the rate and the estimate; both are dropped
 * once the bytes are away and the backend is still writing to disk, since
 * the remaining wait is not a function of transfer rate.
 */
function UploadProgress({ progress, startedAt }) {
  if (!progress) return null

  const { loaded, total, percent, done } = progress
  const elapsed = startedAt ? (Date.now() - startedAt) / 1000 : 0
  const rate = elapsed > 0.5 ? loaded / elapsed : 0
  const eta = rate > 0 && total ? formatDuration((total - loaded) / rate) : null

  return (
    <div style={{ marginTop: '12px' }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between',
        fontSize: '13px', marginBottom: '6px',
      }}>
        <span style={{ color: 'var(--text-secondary)' }}>
          {done
            ? 'Saving on the server…'
            : `${formatBytes(loaded)}${total ? ` / ${formatBytes(total)}` : ''}`}
        </span>
        <span style={{ color: 'var(--accent-hover)' }}>
          {percent === null ? 'Uploading…' : `${Math.floor(percent)}%`}
        </span>
      </div>

      <div className="progress-bar-bg">
        <div
          className="progress-bar-fill"
          style={{ width: percent === null ? '100%' : `${percent}%` }}
        ></div>
      </div>

      {!done && rate > 0 && (
        <p className="form-hint" style={{ marginTop: '6px' }}>
          {formatBytes(rate)}/s{eta ? ` — about ${eta} remaining` : ''}
        </p>
      )}
    </div>
  )
}

// ASS inline colours are &HBBGGRR& -- BGR, and the reverse of the #RRGGBB an
// <input type="color"> speaks. Converting in both directions here keeps that
// detail out of the form itself.
function assToHex(ass) {
  const m = /^&H([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})&$/.exec(ass || '')
  if (!m) return '#00ffff'
  const [, bb, gg, rr] = m
  return `#${rr}${gg}${bb}`.toLowerCase()
}

function hexToAss(hex) {
  const m = /^#([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})$/.exec(hex || '')
  if (!m) return '&H00FFFF&'
  const [, rr, gg, bb] = m
  return `&H${bb}${gg}${rr}&`.toUpperCase()
}

function NewJob() {
  const navigate = useNavigate()
  const location = useLocation()
  const fileRef = useRef(null)
  const transcriptRef = useRef(null)
  const uploadStartRef = useRef(null)
  const transcriptStartRef = useRef(null)

  // Local-first: the backend never downloads, so a video must be uploaded or
  // reused from an earlier job.
  const [mode, setMode] = useState('upload') // 'upload' or 'reuse'
  const [uploadFilename, setUploadFilename] = useState('')
  const [transcriptFilename, setTranscriptFilename] = useState('')
  const [transcriptOffset, setTranscriptOffset] = useState(0)
  const [sourceUrl, setSourceUrl] = useState('')
  const [uploadingTranscript, setUploadingTranscript] = useState(false)
  const [uploading, setUploading] = useState(false)
  // { loaded, total, percent, done } while an upload is in flight, else null.
  const [uploadProgress, setUploadProgress] = useState(null)
  const [transcriptProgress, setTranscriptProgress] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [reuseJobId, setReuseJobId] = useState('')

  // Which keys the backend holds. Used only to warn; a failure to load leaves
  // it null and the form still works.
  const [settings, setSettings] = useState(null)
  const [showAdvanced, setShowAdvanced] = useState(false)

  // Config
  const [clips, setClips] = useState(7)
  // Clip length. The API has accepted `platform` since the preset snapper
  // landed and it drives clipping/analysis/snap.py, but the page never sent
  // it -- so every job created here was 'auto' (20-75s) whatever you wanted.
  const [platform, setPlatform] = useState('auto')
  // One line of context handed to every scan window. Optional, and free --
  // no request is made to work it out.
  const [topic, setTopic] = useState('')
  const [ratio, setRatio] = useState('9:16')
  const [fontStyle, setFontStyle] = useState('HORMOZI')
  const [whisperModel, setWhisperModel] = useState('large-v3')
  // 'auto' matches the backend default: the resolver picks CUDA only when
  // CTranslate2 can genuinely use it, and CPU otherwise. Defaulting the UI to
  // 'cuda' made every dashboard job ask for a GPU that may not exist.
  const [whisperDevice, setWhisperDevice] = useState('auto')
  // 'chain' is the pipeline's default (clipping/config.py AI_PROVIDER) and the
  // only path that runs the three-pass analyzer. This page used to default to
  // 'nvidia', so every job created from the dashboard silently took the legacy
  // single-request path instead.
  const [aiProvider, setAiProvider] = useState('chain')
  const [openaiCompatModel, setOpenaiCompatModel] = useState('')

  // Quality
  const [quality, setQuality] = useState('balanced')
  const [renderHeight, setRenderHeight] = useState(QUALITY_PRESETS.balanced.render_height)
  const [videoCq, setVideoCq] = useState(QUALITY_PRESETS.balanced.video_cq)
  const [videoCrf, setVideoCrf] = useState(QUALITY_PRESETS.balanced.video_crf)
  const [videoScaleAlgo, setVideoScaleAlgo] = useState(QUALITY_PRESETS.balanced.video_scale_algo)

  // Advanced
  const [wordsPerSub, setWordsPerSub] = useState(5)
  const [hookDuration, setHookDuration] = useState(3)
  const [faceDetector, setFaceDetector] = useState('mediapipe')
  const [yoloSize, setYoloSize] = useState('8m')
  const [useSplitScreen, setUseSplitScreen] = useState(false)
  // 'face' rather than the backend's 'diarization' default: face detection
  // needs no HuggingFace token and no pyannote install, so the option works
  // out of the box. The page always sends this explicitly.
  const [splitTrigger, setSplitTrigger] = useState('face')

  // Toggles
  const [useBroll, setUseBroll] = useState(true)
  // Off by default (clipping/config.USE_HOOK_GLITCH): the transition is a
  // one-second full-frame effect on every clip, which is an opt-in.
  const [useHookGlitch, setUseHookGlitch] = useState(false)
  const [useBgm, setUseBgm] = useState(true)
  const [useKaraoke, setUseKaraoke] = useState(true)
  // ASS colour, BGR: &HBBGGRR&. The input below is an RGB picker.
  const [karaokeColor, setKaraokeColor] = useState('&H00FFFF&')
  const [noSubs, setNoSubs] = useState(false)
  const [hookV2, setHookV2] = useState(false)
  const [silenceTrim, setSilenceTrim] = useState(false)
  const [loadGeminiJson, setLoadGeminiJson] = useState(false)

  useEffect(() => {
    fetchSettings().then(setSettings).catch(() => setSettings(null))
  }, [])

  // Load from location state if user clicked "Clone / Rerun"
  useEffect(() => {
    const reuseJob = location.state?.reuseJob
    if (reuseJob) {
      setReuseJobId(reuseJob.id)
      setUploadFilename(reuseJob.upload_filename || '')
      setTranscriptFilename(reuseJob.transcript_filename || '')
      setSourceUrl(reuseJob.source_url || '')
      setMode('reuse')

      // Every field this page can send is restored, so a clone reproduces the
      // original job rather than silently resetting half of it to defaults.
      const config = reuseJob.config || {}
      if (config.clips !== undefined) setClips(config.clips)
      if (config.ratio !== undefined) setRatio(config.ratio)
      if (config.platform !== undefined) setPlatform(config.platform)
      if (config.topic !== undefined) setTopic(config.topic)
      if (config.render_height !== undefined) setRenderHeight(config.render_height)
      if (config.words_per_sub !== undefined) setWordsPerSub(config.words_per_sub)
      if (config.hook_duration !== undefined) setHookDuration(config.hook_duration)
      if (config.font_style !== undefined) setFontStyle(config.font_style)
      if (config.whisper_model !== undefined) setWhisperModel(config.whisper_model)
      if (config.whisper_device !== undefined) setWhisperDevice(config.whisper_device)
      if (config.ai_provider !== undefined) setAiProvider(config.ai_provider)
      if (config.openai_compat_model !== undefined) setOpenaiCompatModel(config.openai_compat_model)
      if (config.face_detector !== undefined) setFaceDetector(config.face_detector)
      if (config.yolo_size !== undefined) setYoloSize(config.yolo_size)
      if (config.use_split_screen !== undefined) setUseSplitScreen(config.use_split_screen)
      if (config.split_trigger !== undefined) setSplitTrigger(config.split_trigger)
      if (config.video_cq !== undefined) setVideoCq(config.video_cq)
      if (config.video_crf !== undefined) setVideoCrf(config.video_crf)
      if (config.video_scale_algo !== undefined) setVideoScaleAlgo(config.video_scale_algo)
      if (config.use_broll !== undefined) setUseBroll(config.use_broll)
      if (config.use_hook_glitch !== undefined) setUseHookGlitch(config.use_hook_glitch)
      if (config.use_auto_bgm !== undefined) setUseBgm(config.use_auto_bgm)
      if (config.use_karaoke_effect !== undefined) setUseKaraoke(config.use_karaoke_effect)
      if (config.karaoke_color !== undefined) setKaraokeColor(config.karaoke_color)
      if (config.hook_v2 !== undefined) setHookV2(config.hook_v2)
      if (config.silence_trim !== undefined) setSilenceTrim(config.silence_trim)
      if (config.no_subs !== undefined) setNoSubs(config.no_subs)

      // Default to true when cloning to save AI tokens, user can untoggle
      setLoadGeminiJson(true)
    }
  }, [location.state])

  // Keep the preset chips honest when Advanced changes the values behind them.
  useEffect(() => {
    setQuality(matchPreset({ renderHeight, videoCq, videoCrf, videoScaleAlgo }))
  }, [renderHeight, videoCq, videoCrf, videoScaleAlgo])

  const applyPreset = (name) => {
    const preset = QUALITY_PRESETS[name]
    if (!preset) return
    setRenderHeight(preset.render_height)
    setVideoCq(preset.video_cq)
    setVideoCrf(preset.video_crf)
    setVideoScaleAlgo(preset.video_scale_algo)
  }

  const handleFileUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return

    setUploading(true)
    setError('')
    uploadStartRef.current = Date.now()
    setUploadProgress({ loaded: 0, total: file.size, percent: 0, done: false })
    try {
      const result = await uploadVideo(file, setUploadProgress)
      setUploadFilename(result.filename)
      setMode('upload')
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
      setUploadProgress(null)
    }
  }

  const handleTranscriptUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return

    setUploadingTranscript(true)
    setError('')
    transcriptStartRef.current = Date.now()
    setTranscriptProgress({ loaded: 0, total: file.size, percent: 0, done: false })
    try {
      const result = await uploadVideo(file, setTranscriptProgress)
      setTranscriptFilename(result.filename)
    } catch (err) {
      setError(err.message)
    } finally {
      setUploadingTranscript(false)
      setTranscriptProgress(null)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')

    if (mode === 'upload' && !uploadFilename) {
      setError('Please upload a video first')
      return
    }
    if (mode === 'reuse' && !reuseJobId.trim()) {
      setError('Job ID cannot be empty')
      return
    }

    setSubmitting(true)
    try {
      // Every job field this page sends, one key per line. The shape is
      // load-bearing: tests/test_dashboard_payload_contract.py reads this block
      // as text to check each key is declared on JobCreateRequest and is
      // restored by Clone & Rerun.
      const jobFields = {
        clips,
        ratio,
        platform,
        topic,
        render_height: renderHeight,
        words_per_sub: Number(wordsPerSub),
        hook_duration: Number(hookDuration),
        font_style: fontStyle,
        whisper_model: whisperModel,
        whisper_device: whisperDevice,
        ai_provider: aiProvider,
        openai_compat_model: openaiCompatModel,
        face_detector: faceDetector,
        yolo_size: yoloSize,
        use_split_screen: useSplitScreen,
        split_trigger: splitTrigger,
        video_cq: Number(videoCq),
        video_crf: Number(videoCrf),
        video_scale_algo: videoScaleAlgo,
        use_broll: useBroll,
        use_hook_glitch: useHookGlitch,
        use_auto_bgm: useBgm,
        use_karaoke_effect: useKaraoke,
        karaoke_color: karaokeColor,
        no_subs: noSubs,
        hook_v2: hookV2,
        silence_trim: silenceTrim,
        load_gemini_json: loadGeminiJson,
      }

      const payload = {
        ...jobFields,
        ...(uploadFilename ? { upload_filename: uploadFilename } : {}),
        ...(transcriptFilename ? { transcript_filename: transcriptFilename } : {}),
        ...(transcriptOffset ? { transcript_offset: Number(transcriptOffset) } : {}),
        ...(sourceUrl.trim() ? { source_url: sourceUrl.trim() } : {}),
        ...(reuseJobId.trim() ? { reuse_job_id: reuseJobId.trim() } : {}),
      }

      const job = await createJob(payload)
      navigate(`/job/${job.id}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  // --- warnings -----------------------------------------------------------
  // A render-only rerun reuses the cached analysis and calls no provider, so
  // it genuinely needs no key. This mirrors the same skip in web/api/worker.py.
  const renderOnly = loadGeminiJson && Boolean(reuseJobId.trim())
  const missingEndpointParts = [
    settings && !settings.openai_compat_base_url ? 'base URL' : null,
    settings && !settings.openai_compat_api_key_set ? 'API key' : null,
    settings && !settings.openai_compat_model && !openaiCompatModel ? 'model' : null,
  ].filter(Boolean)

  // A chain link with no key is skipped with a printed reason and the next link
  // answers, so the only thing worth warning about is a chain with no key at all.
  const anyChainKeySet = Boolean(
    settings && (
      settings.groq_api_key_set ||
      settings.google_api_key_set ||
      settings.nvidia_api_key_set ||
      settings.openrouter_api_key_set ||
      settings.mistral_api_key_set
    )
  )

  // The server's own verdict (chain_readiness), not a copy of the rule: a
  // chain job whose only keyed link is the slow floor is refused by
  // POST /api/jobs, and saying so before the click is the whole point.
  const chainBlocked = Boolean(
    settings && !renderOnly && aiProvider === 'chain' && settings.chain_blocked_reason
  )

  const providerWarning = (() => {
    if (!settings || renderOnly) return null
    if (aiProvider === 'chain' && !anyChainKeySet) {
      return <>No provider key at all, so every link in the chain will be skipped
        and this job will stop at the analysis step. Add one under
        <strong> Settings</strong> — Groq and Gemini are both free, and both
        are far faster than NVIDIA.</>
    }
    if (aiProvider === 'openai_compat' && missingEndpointParts.length) {
      return <>The custom endpoint is missing its {missingEndpointParts.join(', ')}.
        Fill it in under <strong>Settings</strong>, or pick NVIDIA or Gemini instead.</>
    }
    return null
  })()

  const transcriptAttached = Boolean(transcriptFilename)
  const verticalRatio = ['9:16', '1:1', '3:4', '4:5'].includes(ratio)

  return (
    <div className="fade-in">
      <div className="page-header">
        <div>
          <h2>New Clipping Job</h2>
          <p>Generate viral short clips from long-form video</p>
        </div>
      </div>

      {/* Which provider to use, and how to make this fast */}
      <div className="card" style={{ marginBottom: '16px', background: 'var(--info-dim)' }}>
        <div style={{ fontSize: '13px', lineHeight: 1.6 }}>
          <strong>Set an AI key before you start.</strong> Picking the moments needs
          one, and the two the chain tries first are free with no credit card:{' '}
          <a href="https://console.groq.com/keys" target="_blank" rel="noopener" style={{ color: 'var(--accent-hover)' }}>Groq</a>
          {' '}(fastest) or{' '}
          <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener" style={{ color: 'var(--accent-hover)' }}>Google Gemini</a>.
          NVIDIA NIM is the chain's slow last resort: it can back them up, but
          cannot carry a job on its own. Already have a key from xAI or a local
          Ollama? Choose <strong>Custom endpoint</strong> below and set it up in
          Settings.
          <br />
          <span style={{ color: 'var(--text-secondary)' }}>
            Fastest run: upload a transcript alongside the video to skip
            transcription entirely, and use the <strong>Fast</strong> quality preset
            while you are still deciding what to keep.
          </span>
        </div>
      </div>

      <form onSubmit={handleSubmit}>
        {/* Source Selection */}
        <div className="card" style={{ marginBottom: '16px' }}>
          <h3 className="card-title" style={{ marginBottom: '16px' }}>📥 Video Source</h3>

          {/* Mode toggle */}
          <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
            <button
              type="button"
              className={`btn ${mode === 'upload' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
              onClick={() => setMode('upload')}
            >
              📁 Upload File
            </button>
            <button
              type="button"
              className={`btn ${mode === 'reuse' ? 'btn-primary' : 'btn-secondary'} btn-sm`}
              onClick={() => setMode('reuse')}
            >
              🔁 Reuse Job
            </button>
          </div>

          {mode === 'upload' && (
            <div className="form-group">
              <label className="form-label">Upload Video</label>
              {uploadFilename ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <span style={{ color: 'var(--success)' }}>✅ {uploadFilename}</span>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setUploadFilename(''); fileRef.current?.click() }}>
                    Change
                  </button>
                </div>
              ) : (
                <div>
                  <input
                    ref={fileRef}
                    type="file"
                    accept="video/*"
                    onChange={handleFileUpload}
                    style={{ display: 'none' }}
                  />
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => fileRef.current?.click()}
                    disabled={uploading}
                  >
                    {uploading ? <><span className="spinner"></span> Uploading...</> : '📁 Select Video File'}
                  </button>
                  <UploadProgress progress={uploadProgress} startedAt={uploadStartRef.current} />
                  <p className="form-hint">MP4, MKV, AVI, MOV, WebM (max 2GB)</p>
                </div>
              )}
            </div>
          )}

          {mode === 'upload' && (
            <div className="form-group">
              <label className="form-label">Transcript (optional)</label>
              {transcriptFilename ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <span style={{ color: 'var(--success)' }}>✅ {transcriptFilename}</span>
                  <button type="button" className="btn btn-ghost btn-sm" onClick={() => setTranscriptFilename('')}>
                    Remove
                  </button>
                </div>
              ) : (
                <div>
                  <input
                    ref={transcriptRef}
                    type="file"
                    accept=".vtt,.srt,.json3"
                    onChange={handleTranscriptUpload}
                    style={{ display: 'none' }}
                  />
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => transcriptRef.current?.click()}
                    disabled={uploadingTranscript}
                  >
                    {uploadingTranscript ? <><span className="spinner"></span> Uploading...</> : '📝 Select Transcript File'}
                  </button>
                  <UploadProgress progress={transcriptProgress} startedAt={transcriptStartRef.current} />
                  <p className="form-hint">
                    VTT, SRT, JSON3 — skips Whisper entirely (much faster).
                    Leave empty to transcribe with Whisper.
                  </p>
                </div>
              )}
            </div>
          )}

          {mode === 'upload' && transcriptFilename && (
            <div className="form-group" style={{ maxWidth: '200px' }}>
              <label className="form-label">Transcript Offset (seconds)</label>
              <input
                className="form-input"
                type="number"
                step="0.1"
                value={transcriptOffset}
                onChange={(e) => setTranscriptOffset(e.target.value)}
              />
              <p className="form-hint">Shift the timestamp if the video has already been trimmed.</p>
            </div>
          )}

          <div className="form-group">
            <label className="form-label">Source Attribution (optional)</label>
            <input
              className="form-input"
              type="text"
              placeholder="https://youtube.com/watch?v=... or 'Podcast XYZ ep.42'"
              value={sourceUrl}
              onChange={(e) => setSourceUrl(e.target.value)}
            />
            <p className="form-hint">Only used for credit in the description &amp; manifest. Never downloaded.</p>
          </div>

          {mode === 'reuse' && (
            <div className="form-group">
              <label className="form-label">Reuse Job ID</label>
              <input
                className="form-input"
                type="text"
                placeholder="Example: d20b47341e08"
                value={reuseJobId}
                onChange={(e) => setReuseJobId(e.target.value)}
              />
              <p className="form-hint" style={{ marginTop: '4px' }}>Reuse an earlier job's video and analysis. (If using Clone &amp; Rerun, leave this field filled in.)</p>
            </div>
          )}
        </div>

        {/* Quality preset */}
        <div className="card" style={{ marginBottom: '16px' }}>
          <h3 className="card-title" style={{ marginBottom: '6px' }}>🎚️ Quality</h3>
          <p className="form-hint" style={{ marginBottom: '12px' }}>
            Trades render time against output quality. Balanced is what every job
            used before this control existed.
          </p>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            {Object.entries(QUALITY_PRESETS).map(([name, preset]) => (
              <button
                key={name}
                type="button"
                className={`btn ${quality === name ? 'btn-primary' : 'btn-secondary'} btn-sm`}
                onClick={() => applyPreset(name)}
                title={preset.desc}
              >
                {preset.label}
              </button>
            ))}
            {quality === 'custom' && (
              <span style={{
                alignSelf: 'center',
                fontSize: '12.5px',
                color: 'var(--text-tertiary)',
              }}>
                Custom — set in Advanced below
              </span>
            )}
          </div>
          <p className="form-hint" style={{ marginTop: '10px' }}>
            {quality === 'custom'
              ? `${renderHeight === 'source' ? 'Source' : `${renderHeight}p`} · CQ ${videoCq} / CRF ${videoCrf} · ${videoScaleAlgo}`
              : QUALITY_PRESETS[quality].desc}
          </p>
        </div>

        {/* Main Config */}
        <div className="config-grid">
          {/* Basic Settings */}
          <div className="config-section">
            <h4>🎯 Basic Settings</h4>
            <div className="form-group">
              <label className="form-label">Number of Clips</label>
              <input className="form-input" type="number" min="1" max="30" value={clips} onChange={(e) => setClips(parseInt(e.target.value) || 7)} />
            </div>
            <div className="form-group">
              <label className="form-label">Clip Length</label>
              <select className="form-select" value={platform} onChange={(e) => setPlatform(e.target.value)}>
                <option value="auto">Auto — 20–75s, aims for 40s (posts anywhere)</option>
                <option value="tiktok">TikTok — 15–90s, aims for 34s</option>
                <option value="reels">Reels — 15–90s, aims for 30s</option>
                <option value="shorts">Shorts — 15–59s, aims for 45s</option>
                <option value="long">Long — 60–179s, aims for 90s</option>
              </select>
              <p className="form-hint">
                The window each clip is snapped into. A moment shorter than the
                minimum is grown into neighbouring sentences, or dropped if it
                cannot reach it.
              </p>
            </div>
            <div className="form-group">
              <label className="form-label">What is this video about? (optional)</label>
              <input
                className="form-input"
                type="text"
                maxLength={120}
                placeholder="e.g. home espresso gear review"
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
              />
              <p className="form-hint">
                One line, handed to the analysis so it knows what it is reading.
                It judges each moment on whether it stands on its own to someone
                who has not seen the rest, and that is easier when it knows the
                subject. Costs nothing and asks no model.
              </p>
            </div>
            <div className="form-group">
              <label className="form-label">Aspect Ratio</label>
              <select className="form-select" value={ratio} onChange={(e) => setRatio(e.target.value)}>
                <option value="9:16">9:16 (Vertical — TikTok/Reels)</option>
                <option value="16:9">16:9 (Horizontal)</option>
                <option value="1:1">1:1 (Square)</option>
                <option value="3:4">3:4</option>
                <option value="4:5">4:5</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Font Style</label>
              <select className="form-select" value={fontStyle} onChange={(e) => setFontStyle(e.target.value)}>
                <option value="HORMOZI">Hormozi (Bold)</option>
                <option value="DEFAULT">Default (Montserrat)</option>
                <option value="STORYTELLER">Storyteller (Inter)</option>
                <option value="CINEMATIC">Cinematic (Bebas Neue)</option>
              </select>
            </div>
          </div>

          {/* AI Settings */}
          <div className="config-section">
            <h4>🤖 AI &amp; Whisper</h4>
            <div className="form-group">
              <label className="form-label">AI Provider</label>
              <select className="form-select" value={aiProvider} onChange={(e) => setAiProvider(e.target.value)}>
                <option value="chain">Provider chain (recommended)</option>
                <option value="openai_compat">Custom endpoint (OpenAI-compatible)</option>
              </select>
              {chainBlocked ? (
                <Notice kind="error">
                  <div>
                    ⛔ Only the slow floor of the chain has a key, so this job
                    cannot start. Add a free Groq or Gemini key under
                    <strong> Settings</strong>, or turn on “Run on the slow
                    chain anyway” there.
                  </div>
                  <div style={{ marginTop: '6px' }}>
                    <a href="https://console.groq.com/keys" target="_blank" rel="noopener" style={{ color: 'var(--accent)' }}>Groq key →</a>
                    {'  ·  '}
                    <a href="https://aistudio.google.com/apikey" target="_blank" rel="noopener" style={{ color: 'var(--accent)' }}>Gemini key →</a>
                  </div>
                  {/* The server's own words: which links, which env vars. Laid
                      out for a terminal, so it is kept out of the narrow column
                      until asked for. */}
                  <details style={{ marginTop: '6px' }}>
                    <summary style={{ cursor: 'pointer' }}>Why?</summary>
                    <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginTop: '4px' }}>
                      {settings.chain_blocked_reason}
                    </div>
                  </details>
                </Notice>
              ) : providerWarning && <Notice kind="warn">⚠️ {providerWarning}</Notice>}
            </div>

            {aiProvider === 'openai_compat' && (
              <div className="form-group">
                <label className="form-label">Model for this job (optional)</label>
                <input
                  className="form-input"
                  type="text"
                  placeholder={settings?.openai_compat_model || 'Leave empty to use the one in Settings'}
                  value={openaiCompatModel}
                  onChange={(e) => setOpenaiCompatModel(e.target.value)}
                />
                <p className="form-hint">
                  Overrides the saved model for this job only.
                </p>
              </div>
            )}

            <div className="form-group">
              <label className="form-label">Whisper Model</label>
              <select
                className="form-select"
                value={whisperModel}
                onChange={(e) => setWhisperModel(e.target.value)}
                disabled={transcriptAttached}
                style={{ opacity: transcriptAttached ? 0.5 : 1 }}
              >
                <option value="large-v3">large-v3 (Best quality)</option>
                <option value="medium">medium (Balanced)</option>
                <option value="small">small (Fast)</option>
                <option value="base">base (Fastest)</option>
              </select>
            </div>
            <div className="form-group">
              <label className="form-label">Device</label>
              <select
                className="form-select"
                value={whisperDevice}
                onChange={(e) => setWhisperDevice(e.target.value)}
                disabled={transcriptAttached}
                style={{ opacity: transcriptAttached ? 0.5 : 1 }}
              >
                <option value="auto">Auto (detect GPU, else CPU)</option>
                <option value="cuda">CUDA (GPU)</option>
                <option value="cpu">CPU</option>
              </select>
              {transcriptAttached && (
                <p className="form-hint">
                  A transcript is attached, so Whisper is skipped and these two
                  settings are unused.
                </p>
              )}
            </div>
          </div>

          {/* Feature Toggles */}
          <div className="config-section">
            <h4>✨ Features</h4>
            <ToggleRow label="B-Roll Footage" desc="Insert stock footage" checked={useBroll} onChange={setUseBroll} />
            {useBroll && settings && !settings.pexels_api_key_set && (
              <Notice>
                ℹ️ No Pexels key, so B-roll will be skipped and the clips render
                without it. Add one in Settings, or turn this off.
              </Notice>
            )}
            <ToggleRow label="Hook Glitch" desc="Glitch transition intro" checked={useHookGlitch} onChange={setUseHookGlitch} />
            <ToggleRow label="Background Music" desc="Auto BGM matching" checked={useBgm} onChange={setUseBgm} />
            <ToggleRow label="Karaoke Effect" desc="Word-by-word highlight" checked={useKaraoke} onChange={setUseKaraoke} />
            {useKaraoke && (
              <div className="form-group">
                <label className="form-label">Highlight Colour</label>
                <input
                  className="form-input"
                  type="color"
                  value={assToHex(karaokeColor)}
                  onChange={(e) => setKaraokeColor(hexToAss(e.target.value))}
                />
                <p className="form-hint">
                  The colour of the word currently being spoken. Default is
                  yellow.
                </p>
              </div>
            )}
            <ToggleRow label="Hook V2" desc="Multi-hook intro clips" checked={hookV2} onChange={setHookV2} />
            <ToggleRow label="Silence Trim" desc="Remove dead air" checked={silenceTrim} onChange={setSilenceTrim} />
            <ToggleRow label="No Subtitles" desc="Render without text" checked={noSubs} onChange={setNoSubs} />
            {noSubs && useKaraoke && (
              <Notice>
                ℹ️ "No Subtitles" removes all on-screen text, so the karaoke effect
                above will have nothing to animate.
              </Notice>
            )}
            <ToggleRow label="Bypass AI" desc="Reuse a previous analysis if one exists" checked={loadGeminiJson} onChange={setLoadGeminiJson} />
          </div>
        </div>

        {/* Advanced */}
        <div className="card" style={{ marginBottom: '16px' }}>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={() => setShowAdvanced(!showAdvanced)}
          >
            {showAdvanced ? '▾' : '▸'} Advanced options
          </button>

          {showAdvanced && (
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
              gap: '16px',
              marginTop: '16px',
            }}>
              <div className="config-section">
                <h4>🖥️ Output</h4>
                <div className="form-group">
                  <label className="form-label">Resolution</label>
                  <select className="form-select" value={renderHeight} onChange={(e) => setRenderHeight(e.target.value)}>
                    <option value="720">720p (fastest)</option>
                    <option value="1080">1080p</option>
                    <option value="1440">1440p</option>
                    <option value="source">Match the source</option>
                  </select>
                  <p className="form-hint">Short side of the frame. 9:16 at 1080 is 1080×1920.</p>
                </div>
                <div className="form-group">
                  <label className="form-label">GPU quality (CQ)</label>
                  <input className="form-input" type="number" min="1" max="51" value={videoCq} onChange={(e) => setVideoCq(e.target.value)} />
                  <p className="form-hint">Used on NVENC/AMF/VAAPI. Lower is better and bigger.</p>
                </div>
                <div className="form-group">
                  <label className="form-label">CPU quality (CRF)</label>
                  <input className="form-input" type="number" min="1" max="51" value={videoCrf} onChange={(e) => setVideoCrf(e.target.value)} />
                  <p className="form-hint">Used on libx264, when no hardware encoder is found.</p>
                </div>
                <div className="form-group">
                  <label className="form-label">Scaling</label>
                  <select className="form-select" value={videoScaleAlgo} onChange={(e) => setVideoScaleAlgo(e.target.value)}>
                    <option value="lanczos">Lanczos (sharpest)</option>
                    <option value="bicubic">Bicubic</option>
                    <option value="bilinear">Bilinear (fastest)</option>
                    <option value="area">Area</option>
                  </select>
                </div>
              </div>

              <div className="config-section">
                <h4>💬 Subtitles &amp; hook</h4>
                <div className="form-group">
                  <label className="form-label">Words per subtitle</label>
                  <input className="form-input" type="number" min="1" max="15" value={wordsPerSub} onChange={(e) => setWordsPerSub(e.target.value)} />
                  <p className="form-hint">Fewer words reads faster on a phone.</p>
                </div>
                <div className="form-group">
                  <label className="form-label">Hook duration (seconds)</label>
                  <input className="form-input" type="number" min="1" max="10" value={hookDuration} onChange={(e) => setHookDuration(e.target.value)} />
                </div>
              </div>

              <div className="config-section">
                <h4>🙂 Face tracking</h4>
                <div className="form-group">
                  <label className="form-label">Detector</label>
                  <select className="form-select" value={faceDetector} onChange={(e) => setFaceDetector(e.target.value)}>
                    <option value="mediapipe">MediaPipe (light, CPU)</option>
                    <option value="yolo">YOLO (better, heavier)</option>
                  </select>
                </div>
                {faceDetector === 'yolo' && (
                  <div className="form-group">
                    <label className="form-label">YOLO model</label>
                    <select className="form-select" value={yoloSize} onChange={(e) => setYoloSize(e.target.value)}>
                      <option value="8n">8n (fastest)</option>
                      <option value="8s">8s</option>
                      <option value="8m">8m (default)</option>
                      <option value="8n_v2">8n_v2</option>
                      <option value="9c">9c (most accurate)</option>
                    </select>
                    <p className="form-hint">Downloaded once on first use.</p>
                  </div>
                )}
              </div>

              <div className="config-section">
                <h4>👥 Split screen</h4>
                <ToggleRow
                  label="Split Screen"
                  desc="Two-speaker podcast layout"
                  checked={useSplitScreen}
                  onChange={setUseSplitScreen}
                />
                {useSplitScreen && (
                  <div className="form-group" style={{ marginTop: '12px' }}>
                    <label className="form-label">Speaker detection</label>
                    <select className="form-select" value={splitTrigger} onChange={(e) => setSplitTrigger(e.target.value)}>
                      <option value="face">Face detection (no token needed)</option>
                      <option value="diarization">Audio diarization (needs HF token)</option>
                    </select>
                  </div>
                )}
                {useSplitScreen && splitTrigger === 'diarization' && settings && !settings.hf_token_set && (
                  <Notice kind="warn">
                    ⚠️ Diarization needs a HuggingFace token and the pyannote model
                    licence accepted. Switch to face detection to avoid both.
                  </Notice>
                )}
                {useSplitScreen && !verticalRatio && (
                  <Notice>
                    ℹ️ Split screen only applies to vertical ratios, so it will be
                    ignored at {ratio}.
                  </Notice>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Error */}
        {error && (
          <div style={{ background: 'var(--error-dim)', border: '1px solid rgba(239,68,68,0.2)', borderRadius: 'var(--radius-md)', padding: '12px 16px', marginBottom: '16px', color: 'var(--error)', fontSize: '13px', whiteSpace: 'pre-wrap' }}>
            ⚠️ {error}
          </div>
        )}

        {/* Submit */}
        <button
          type="submit"
          className="btn btn-primary"
          disabled={submitting || chainBlocked}
          title={chainBlocked ? 'Add a Groq or Gemini key in Settings first' : undefined}
          style={{ fontSize: '14px', padding: '12px 28px' }}
        >
          {submitting ? <><span className="spinner"></span> Processing...</> : '🚀 Start Clipping'}
        </button>
      </form>
    </div>
  )
}

function ToggleRow({ label, desc, checked, onChange }) {
  return (
    <div className="toggle-row">
      <div>
        <div className="toggle-label">{label}</div>
        {desc && <div className="toggle-desc">{desc}</div>}
      </div>
      <label className="toggle-switch">
        <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
        <span className="toggle-slider"></span>
      </label>
    </div>
  )
}

export default NewJob

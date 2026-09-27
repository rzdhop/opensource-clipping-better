import { useEffect, useRef, useState } from 'react'
import {
  patchStory, runStoryStep, approveStoryDoc, regenerateStory, fetchStoryEstimate,
  patchEpisodeScript, fetchEpisodeVoiceUrl,
} from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import RouteChip from '../../../components/RouteChip'
import { EditableText, RegenerateControl, StepError } from '../fields'
import DurationBar from './DurationBar'

// The two episode lengths shipped (spec 6.2): FR/EN-agnostic English labels,
// since the story's language is the *cast's* language, not the workspace
// UI's. Ids mirror clipping.aistory.defaults.EPISODE_TEMPLATE_IDS exactly
// (tests/test_story_payload_contract_episode.py).
const EPISODE_TEMPLATES = [
  { id: 'serial_60s_v1', label: '60 s (55–80)' },
  { id: 'serial_90s_v1', label: '90 s (75–100)' },
]

// clipping.aistory.schemas.EMOTIONS, verbatim.
const EMOTIONS = [
  'neutral', 'happy', 'angry', 'shocked', 'sad', 'scheming',
  'tension', 'tender', 'fear', 'triumph',
]

const SCENE_FUNCTION_LABELS = {
  recap: 'Recap', hook: 'Hook', setup: 'Setup', rising: 'Rising',
  peak: 'Peak', turn: 'Turn', cliffhanger: 'Cliffhanger',
}

function fmtUsd(value) {
  const amount = Number(value) || 0
  return amount === 0 ? '0.00' : amount.toFixed(3)
}

// ------------------------------------------------------------ length + write

function ScriptHeader({ storyId, ep, episode, storyDoc, episodes, busy, onChange }) {
  const state = episode.state
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  const [templateError, setTemplateError] = useState('')
  const [templateSaving, setTemplateSaving] = useState(false)

  useEffect(() => {
    fetchStoryEstimate(storyId, 'script', { ep }).then(setEstimate).catch(() => setEstimate(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storyId, ep, state.missing.join('|'), state.report])

  // The episode length is fixed once ANY episode of the story has a script
  // (workflow.check_episode_template): each episode keeps the template it
  // was written against.
  const anyScriptWritten = (episodes || []).some((e) => e.script_state && e.script_state !== 'none')

  const handleTemplateChange = async (e) => {
    const value = e.target.value
    setTemplateSaving(true)
    setTemplateError('')
    try {
      await patchStory(storyId, { episode_template_id: value })
      onChange()
    } catch (err) {
      setTemplateError(err.message)
    } finally {
      setTemplateSaving(false)
    }
  }

  const needsWrite = state.missing.length > 0 || state.report === 'stale'
  const actionLabel = state.script === 'none' ? `Write episode ${ep}`
    : state.script === 'writing' ? 'Continue writing'
    : 'Check again'

  const handleRun = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      // Writing, continuing and re-checking all send no parameters --
      // MeasureVoices below is the only script-step call that does.
      const scriptParams = {}
      await runStoryStep(storyId, 'script', { ep, params: scriptParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="card episode-script-header">
      <div className="form-group">
        <label className="form-label">Episode length</label>
        <select
          className="form-select"
          value={storyDoc.episode_template_id}
          onChange={handleTemplateChange}
          disabled={busy || templateSaving || anyScriptWritten}
        >
          {EPISODE_TEMPLATES.map((tpl) => <option key={tpl.id} value={tpl.id}>{tpl.label}</option>)}
        </select>
        {anyScriptWritten && (
          <p className="form-hint">The episode length is fixed once an episode has a script.</p>
        )}
        <StepError message={templateError} />
      </div>

      {needsWrite && (
        <div className="story-step-actions">
          <button type="button" className="btn btn-primary" onClick={handleRun} disabled={busy || running}>
            {running ? <><span className="spinner"></span> {actionLabel}…</> : actionLabel}
          </button>
          <EstimateChip estimate={estimate} />
          {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
        </div>
      )}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// ------------------------------------------------------------------ consistency

function ConsistencyPanel({ report, state }) {
  if (state === 'none') {
    return <div className="card story-script-consistency"><p className="form-hint">Not checked yet.</p></div>
  }
  return (
    <div className="card story-script-consistency">
      <h4 className="card-title">Consistency</h4>
      {state === 'stale' && <p className="form-hint">The script changed since this was checked — Check again above.</p>}
      {state === 'passed' && <p className="chip chip-accent">Passed ✓</p>}
      {state === 'issues' && report && (
        <>
          <p className="chip chip-warn">{report.issues.length} issue{report.issues.length === 1 ? '' : 's'}</p>
          <ul className="story-field-list">
            {report.issues.map((issue, i) => (
              <li key={i}>
                {issue.scene_id ? <a href={`#scene-${issue.scene_id}`}>{issue.scene_id}</a> : 'Episode'}: {issue.fix}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

// ------------------------------------------------------------------------ lines

function LineRow({ storyId, ep, line, sceneCharacters, narratorEnabled, busy, onChange }) {
  const [playUrl, setPlayUrl] = useState(null)
  const [selectError, setSelectError] = useState('')
  const [selectErrors, setSelectErrors] = useState(null)
  const urlRef = useRef(null)

  useEffect(() => () => { if (urlRef.current) URL.revokeObjectURL(urlRef.current) }, [])

  const saveText = async (value) => {
    await patchEpisodeScript(storyId, ep, { lines: [{ line_id: line.line_id, text: value }] })
    onChange()
  }
  const saveDelivery = async (value) => {
    await patchEpisodeScript(storyId, ep, { lines: [{ line_id: line.line_id, delivery: value }] })
    onChange()
  }
  const saveSpeaker = async (value) => {
    setSelectError('')
    setSelectErrors(null)
    try {
      await patchEpisodeScript(storyId, ep, { lines: [{ line_id: line.line_id, speaker: value }] })
      onChange()
    } catch (err) {
      setSelectError(err.message)
      setSelectErrors(err.errors || null)
    }
  }
  const saveEmotion = async (value) => {
    setSelectError('')
    setSelectErrors(null)
    try {
      await patchEpisodeScript(storyId, ep, { lines: [{ line_id: line.line_id, emotion: value }] })
      onChange()
    } catch (err) {
      setSelectError(err.message)
      setSelectErrors(err.errors || null)
    }
  }

  const play = async () => {
    try {
      const fresh = await fetchEpisodeVoiceUrl(storyId, ep, line.timing.audio)
      if (urlRef.current) URL.revokeObjectURL(urlRef.current)
      urlRef.current = fresh
      setPlayUrl(fresh)
    } catch (err) {
      setSelectError(err.message)
    }
  }

  const measured = line.timing.source !== 'estimated'

  return (
    <div className="story-script-line" id={`line-${line.line_id}`}>
      <div className="story-script-line-row">
        <select
          className="form-select story-script-line-speaker"
          value={line.speaker}
          onChange={(e) => saveSpeaker(e.target.value)}
          disabled={busy}
        >
          {sceneCharacters.map((c) => <option key={c.char_id} value={c.char_id}>{c.name}</option>)}
          {narratorEnabled && <option value="narrator">Narrator</option>}
        </select>
        <select
          className="form-select story-script-line-emotion"
          value={line.emotion}
          onChange={(e) => saveEmotion(e.target.value)}
          disabled={busy}
        >
          {EMOTIONS.map((emotion) => <option key={emotion} value={emotion}>{emotion}</option>)}
        </select>
        <span className="chip" title={line.timing.source}>
          {line.timing.duration_s.toFixed(1)} s · {measured ? 'measured' : 'estimated'}
        </span>
        {line.timing.audio && (
          <button type="button" className="btn btn-ghost btn-sm" onClick={play} aria-label={`Play line ${line.line_id}`}>
            ▶
          </button>
        )}
      </div>
      <EditableText value={line.text} onSave={saveText} disabled={busy} rows={2} />
      <EditableText label="Delivery" value={line.delivery} onSave={saveDelivery} disabled={busy} rows={1} />
      {playUrl && <audio controls autoPlay src={playUrl} />}
      <StepError message={selectError} errors={selectErrors} />
    </div>
  )
}

// ------------------------------------------------------------------------ scenes

function SceneCard({ storyId, ep, scene, issues, characters, places, narratorEnabled, busy, onChange }) {
  const place = places.find((p) => p.place_id === scene.place_id)
  const sceneCharacters = scene.characters
    .map((cid) => characters.find((c) => c.char_id === cid))
    .filter(Boolean)

  const saveSummary = async (value) => {
    await patchEpisodeScript(storyId, ep, { scenes: [{ scene_id: scene.scene_id, summary: value }] })
    onChange()
  }
  const saveOnScreenText = async (value) => {
    await patchEpisodeScript(storyId, ep, { scenes: [{ scene_id: scene.scene_id, on_screen_text: value || null }] })
    onChange()
  }
  const regenerate = async (note) => {
    await regenerateStory(storyId, { target: `scene:${ep}:${scene.scene_id}`, note })
    onChange()
  }

  return (
    <div className="card story-script-scene" id={`scene-${scene.scene_id}`}>
      <div className="story-script-scene-header">
        <span className="story-script-scene-id">{scene.scene_id}</span>
        <span className="chip">{SCENE_FUNCTION_LABELS[scene.function] || scene.function}</span>
        <span className="chip">
          {place ? place.name : scene.place_id}{scene.time_variant ? ` · ${scene.time_variant}` : ''}
        </span>
        <span className="chip">{scene.emotion}</span>
      </div>
      {sceneCharacters.length > 0 && (
        <p className="story-script-scene-characters">{sceneCharacters.map((c) => c.name).join(', ')}</p>
      )}

      {issues.length > 0 && (
        <ul className="story-field-list story-error">
          {issues.map((issue, i) => <li key={i}>{issue.kind}: {issue.fix}</li>)}
        </ul>
      )}

      <div className="story-field">
        <div className="story-field-label">Summary</div>
        <EditableText value={scene.summary} onSave={saveSummary} disabled={busy} rows={2} />
      </div>

      <div className="story-script-lines">
        {scene.lines.map((line) => (
          <LineRow
            key={line.line_id}
            storyId={storyId}
            ep={ep}
            line={line}
            sceneCharacters={sceneCharacters}
            narratorEnabled={narratorEnabled}
            busy={busy}
            onChange={onChange}
          />
        ))}
      </div>

      {scene.sfx_cues.length > 0 && (
        <div className="story-script-sfx">
          {scene.sfx_cues.map((cue, i) => (
            <span key={i} className="chip" title={`at ${cue.at}`}>🔊 {cue.cue}</span>
          ))}
        </div>
      )}

      <div className="story-field">
        <div className="story-field-label">On-screen text</div>
        <EditableText value={scene.on_screen_text} onSave={saveOnScreenText} disabled={busy} rows={1} />
      </div>

      <RegenerateControl disabled={busy} onRegenerate={regenerate} />
    </div>
  )
}

// --------------------------------------------------------------- hook/cliffhanger/teaser

function FramingFields({ storyId, ep, script, busy, onChange }) {
  const saveHookText = async (value) => {
    await patchEpisodeScript(storyId, ep, { hook_on_screen_text: value || null })
    onChange()
  }
  const saveCliffhangerReveal = async (value) => {
    await patchEpisodeScript(storyId, ep, { cliffhanger_reveal: value })
    onChange()
  }
  const saveTeaser = async (value) => {
    await patchEpisodeScript(storyId, ep, { next_episode_teaser: value })
    onChange()
  }
  const regenerateHook = async (note) => {
    await regenerateStory(storyId, { target: `hook:${ep}`, note })
    onChange()
  }
  const regenerateCliffhanger = async (note) => {
    await regenerateStory(storyId, { target: `cliffhanger:${ep}`, note })
    onChange()
  }
  const regenerateTeaser = async (note) => {
    await regenerateStory(storyId, { target: `teaser:${ep}`, note })
    onChange()
  }

  return (
    <div className="card story-script-framing">
      <div className="story-field">
        <div className="story-field-label">Hook on-screen text</div>
        <EditableText value={script.hook.on_screen_text} onSave={saveHookText} disabled={busy} rows={1} />
        <RegenerateControl disabled={busy} onRegenerate={regenerateHook} />
      </div>
      <div className="story-field">
        <div className="story-field-label">Cliffhanger reveal</div>
        <EditableText value={script.cliffhanger.reveal} onSave={saveCliffhangerReveal} disabled={busy} rows={2} />
        <RegenerateControl disabled={busy} onRegenerate={regenerateCliffhanger} />
      </div>
      <div className="story-field">
        <div className="story-field-label">Next-episode teaser</div>
        <EditableText value={script.next_episode_teaser} onSave={saveTeaser} disabled={busy} rows={2} />
        <RegenerateControl disabled={busy} onRegenerate={regenerateTeaser} />
      </div>
    </div>
  )
}

// -------------------------------------------------------------------- real voices

function MeasureVoices({ storyId, ep, complete, busy, onChange }) {
  const [estimate, setEstimate] = useState(null)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  useEffect(() => {
    if (!complete) { setEstimate(null); return }
    fetchStoryEstimate(storyId, 'script', { ep, measure: true }).then(setEstimate).catch(() => setEstimate(null))
  }, [storyId, ep, complete])

  const handleMeasure = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      const measureParams = { measure_voices: true }
      await runStoryStep(storyId, 'script', { ep, params: measureParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  const block = estimate && estimate.measure

  return (
    <div className="card story-script-measure">
      <h4 className="card-title">Real voices</h4>
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-secondary"
          onClick={handleMeasure}
          disabled={!complete || busy || running}
        >
          {running ? <><span className="spinner"></span> Measuring…</> : 'Measure with real voices'}
        </button>
        {block && (
          <span className="chip" title={block.ready ? '' : 'Some lines have no pinned voice'}>
            {block.lines} line{block.lines === 1 ? '' : 's'} · {block.chars} char{block.chars === 1 ? '' : 's'} · est. ${fmtUsd(block.est_usd)}
          </span>
        )}
      </div>
      {!complete && <p className="form-hint">Finish the script first.</p>}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// ----------------------------------------------------------------------- approve

function ApproveScript({ storyId, ep, episode, busy, onChange }) {
  const [approving, setApproving] = useState(false)
  const [approveAnyway, setApproveAnyway] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const state = episode.state
  const script = episode.script
  const complete = state.missing.length === 0
  const approved = Boolean(script && script.approved_at)

  const blockReason = () => {
    if (busy) return 'A step is running.'
    if (!complete) return 'The script is not complete yet.'
    if (state.report === 'stale' || state.report === 'none') return 'Check the consistency first.'
    if (state.report === 'issues' && !approveAnyway) {
      return 'The consistency check found issues: tick "approve anyway", or fix them and check again.'
    }
    return null
  }
  const reason = approved ? null : blockReason()

  const handleApprove = async () => {
    setApproving(true)
    setError('')
    setErrors(null)
    try {
      await approveStoryDoc(storyId, `script:${ep}`, { approve_anyway: approveAnyway })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  return (
    <div className="card story-script-approve">
      {state.report === 'issues' && !approved && (
        <label className="story-checkbox">
          <input
            type="checkbox"
            checked={approveAnyway}
            onChange={(e) => setApproveAnyway(e.target.checked)}
          />
          Approve anyway (the consistency check found issues)
        </label>
      )}
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleApprove}
          disabled={approved || Boolean(reason) || approving}
          title={reason || undefined}
        >
          {approving ? 'Approving…' : approved ? 'Approved' : 'Approve script'}
        </button>
        {reason && <span className="form-hint">{reason}</span>}
      </div>
      {approved && <p className="form-hint">Approved {new Date(script.approved_at).toLocaleString()}.</p>}
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// --------------------------------------------------------------------------- page

export default function ScriptPane({ episode, storyDoc, characters, places, episodes, storyId, ep, inFlightJob, onChange }) {
  const script = episode.script
  const busy = Boolean(inFlightJob)
  const narratorEnabled = Boolean(storyDoc.narrator && storyDoc.narrator.enabled)

  const issuesByScene = {};
  if (script && script.consistency_report) {
    for (const issue of script.consistency_report.issues) {
      if (!issue.scene_id) continue;
      (issuesByScene[issue.scene_id] = issuesByScene[issue.scene_id] || []).push(issue);
    }
  }

  return (
    <div className="story-step-body">
      <ScriptHeader
        storyId={storyId}
        ep={ep}
        episode={episode}
        storyDoc={storyDoc}
        episodes={episodes}
        busy={busy}
        onChange={onChange}
      />

      {script && script.scenes.length > 0 && (
        <>
          <div className="card">
            <DurationBar template={episode.template} scenes={script.scenes} timing={script.timing} />
          </div>

          <ConsistencyPanel report={script.consistency_report} state={episode.state.report} />

          <div className="story-script-scenes">
            {script.scenes.map((scene) => (
              <SceneCard
                key={scene.scene_id}
                storyId={storyId}
                ep={ep}
                scene={scene}
                issues={issuesByScene[scene.scene_id] || []}
                characters={characters}
                places={places}
                narratorEnabled={narratorEnabled}
                busy={busy}
                onChange={onChange}
              />
            ))}
          </div>

          <FramingFields storyId={storyId} ep={ep} script={script} busy={busy} onChange={onChange} />

          <MeasureVoices
            storyId={storyId}
            ep={ep}
            complete={episode.state.missing.length === 0}
            busy={busy}
            onChange={onChange}
          />

          <ApproveScript storyId={storyId} ep={ep} episode={episode} busy={busy} onChange={onChange} />
        </>
      )}
    </div>
  )
}

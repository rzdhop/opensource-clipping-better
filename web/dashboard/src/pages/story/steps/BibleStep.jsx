import { useEffect, useState } from 'react'
import { patchStory, runStoryStep, approveStoryDoc, regenerateStory, fetchStoryEstimate } from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import RouteChip from '../../../components/RouteChip'
import { LiveActivity, useJobFeed } from '../../../components/ActivityFeed'
import { EditableText, EditableList, RegenerateControl, StepError } from '../fields'

// The regenerate targets this step uses (spec 9.2 grammar `bible:<field>`).
// tests/test_story_payload_contract.py checks this list against
// clipping.aistory.prompts.REGENERATE_TARGETS.
const BIBLE_REGENERATE_FIELDS = ['logline', 'premise', 'tone', 'world', 'themes']

const PLATFORMS = ['tiktok', 'shorts', 'reels']

const EMPTY_WORLD = { setting_summary: '', rules: [], time_period: '', recurring_motifs: [] }
const EMPTY_AUDIENCE = { age: '', platforms: [] }

export default function BibleStep({ data, storyId, inFlightJob, onChange, onAdvance }) {
  const { story } = data
  const [writeError, setWriteError] = useState('')
  const [writeErrors, setWriteErrors] = useState(null)
  const [approveError, setApproveError] = useState('')
  const [approveErrors, setApproveErrors] = useState(null)
  const [approving, setApproving] = useState(false)
  const [estimate, setEstimate] = useState(null)

  const loadEstimate = () => {
    fetchStoryEstimate(storyId, 'bible').then(setEstimate).catch(() => setEstimate(null))
  }
  useEffect(() => { loadEstimate() }, [storyId]) // eslint-disable-line react-hooks/exhaustive-deps

  const myJob = inFlightJob && (
    inFlightJob.step === 'bible'
    || (inFlightJob.step === 'regenerate' && inFlightJob.params
        && typeof inFlightJob.params.target === 'string'
        && inFlightJob.params.target.startsWith('bible:'))
  ) ? inFlightJob : null

  const { job: liveJob, events, streamState } = useJobFeed(myJob ? myJob.id : null, {
    onJob: (job) => {
      if (job && job.status !== 'queued' && job.status !== 'running') {
        onChange()
        loadEstimate()
      }
    },
  })

  const busy = Boolean(inFlightJob)

  const handleWrite = async () => {
    setWriteError('')
    setWriteErrors(null)
    try {
      await runStoryStep(storyId, 'bible')
      onChange()
    } catch (err) {
      setWriteError(err.message)
      setWriteErrors(err.errors || null)
    }
  }

  // Each call site below sends one literal top-level key of StoryPatchRequest;
  // tests/test_story_payload_contract.py checks every patchStory(storyId, {
  // <key>: ... }) call site in this file against that model's fields.
  const saveLogline = async (value) => { await patchStory(storyId, { logline: value }); onChange() }
  const savePremise = async (value) => { await patchStory(storyId, { premise: value }); onChange() }
  const saveTone = async (value) => { await patchStory(storyId, { tone: value }); onChange() }
  const saveGenreTags = async (value) => { await patchStory(storyId, { genre_tags: value }); onChange() }
  const saveThemesAndValues = async (value) => { await patchStory(storyId, { themes_and_values: value }); onChange() }
  const saveWhyComeBack = async (value) => { await patchStory(storyId, { why_come_back: value }); onChange() }

  const patchWorldField = async (patch) => {
    await patchStory(storyId, { world: { ...EMPTY_WORLD, ...(story.world || {}), ...patch } })
    onChange()
  }

  const patchAudienceField = async (patch) => {
    await patchStory(storyId, { audience: { ...EMPTY_AUDIENCE, ...(story.audience || {}), ...patch } })
    onChange()
  }

  const regenerate = async (field, note) => {
    await regenerateStory(storyId, { target: `bible:${field}`, note })
    onChange()
  }

  const handleApprove = async () => {
    setApproving(true)
    setApproveError('')
    setApproveErrors(null)
    try {
      await approveStoryDoc(storyId, 'bible')
      onAdvance()
    } catch (err) {
      setApproveError(err.message)
      setApproveErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  const world = story.world || EMPTY_WORLD
  const audience = story.audience || EMPTY_AUDIENCE
  const cardBusy = busy || approving

  return (
    <div className="story-step-body">
      <div className="story-step-actions">
        <button type="button" className="btn btn-secondary" onClick={handleWrite} disabled={busy}>
          {myJob && myJob.step === 'bible' ? <><span className="spinner"></span> Writing…</> : 'Write the bible'}
        </button>
        <EstimateChip estimate={estimate} />
        {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      </div>
      <StepError message={writeError} errors={writeErrors} className="story-step-error" />

      {myJob && liveJob && (
        liveJob.status === 'queued'
          ? <p className="form-hint">Waiting to start…</p>
          : <LiveActivity job={liveJob} events={events} streamState={streamState} />
      )}

      <div className="card story-bible-card">
        <h4 className="card-title">Logline</h4>
        <EditableText
          value={story.logline}
          onSave={saveLogline}
          disabled={cardBusy}
          rows={2}
        />
        <RegenerateControl
          disabled={cardBusy}
          onRegenerate={(note) => regenerate('logline', note)}
        />
      </div>

      <div className="card story-bible-card">
        <h4 className="card-title">Premise</h4>
        <EditableText
          value={story.premise}
          onSave={savePremise}
          disabled={cardBusy}
          rows={4}
        />
        <RegenerateControl
          disabled={cardBusy}
          onRegenerate={(note) => regenerate('premise', note)}
        />
      </div>

      <div className="card story-bible-card">
        <h4 className="card-title">Tone &amp; genre tags</h4>
        <EditableText
          label="Tone"
          value={story.tone}
          onSave={saveTone}
          disabled={cardBusy}
          rows={2}
        />
        <EditableList
          label="Genre tags"
          value={story.genre_tags}
          onSave={saveGenreTags}
          disabled={cardBusy}
        />
        <RegenerateControl
          disabled={cardBusy}
          onRegenerate={(note) => regenerate('tone', note)}
        />
      </div>

      <div className="card story-bible-card">
        <h4 className="card-title">World</h4>
        <EditableText
          label="Setting"
          value={world.setting_summary}
          onSave={(value) => patchWorldField({ setting_summary: value })}
          disabled={cardBusy}
          rows={3}
        />
        <EditableList
          label="Rules"
          value={world.rules}
          onSave={(value) => patchWorldField({ rules: value })}
          disabled={cardBusy}
        />
        <EditableText
          label="Time period"
          value={world.time_period}
          onSave={(value) => patchWorldField({ time_period: value })}
          disabled={cardBusy}
          rows={1}
        />
        <EditableList
          label="Recurring motifs"
          value={world.recurring_motifs}
          onSave={(value) => patchWorldField({ recurring_motifs: value })}
          disabled={cardBusy}
        />
        <RegenerateControl
          disabled={cardBusy}
          onRegenerate={(note) => regenerate('world', note)}
        />
      </div>

      <div className="card story-bible-card">
        <h4 className="card-title">Themes &amp; values</h4>
        <EditableList
          label="Themes &amp; values"
          value={story.themes_and_values}
          onSave={saveThemesAndValues}
          disabled={cardBusy}
        />

        <div className="story-field">
          <div className="story-field-label">Audience</div>
          <EditableText
            label="Age"
            value={audience.age}
            onSave={(value) => patchAudienceField({ age: value })}
            disabled={cardBusy}
            rows={1}
          />
          <div className="story-field-label">Platforms</div>
          <div className="toggle-row-group">
            {PLATFORMS.map((platform) => (
              <label key={platform} className="story-checkbox">
                <input
                  type="checkbox"
                  checked={(audience.platforms || []).includes(platform)}
                  disabled={cardBusy}
                  onChange={(e) => {
                    const next = e.target.checked
                      ? [...(audience.platforms || []), platform]
                      : (audience.platforms || []).filter((p) => p !== platform)
                    patchAudienceField({ platforms: next })
                  }}
                />
                {platform}
              </label>
            ))}
          </div>
        </div>

        <EditableList
          label="Why people come back"
          value={story.why_come_back}
          onSave={saveWhyComeBack}
          disabled={cardBusy}
          exactLines={3}
        />

        <RegenerateControl
          disabled={cardBusy}
          onRegenerate={(note) => regenerate('themes', note)}
        />
      </div>

      <div className="story-step-actions">
        <button type="button" className="btn btn-primary" onClick={handleApprove} disabled={cardBusy}>
          {approving ? 'Approving…' : 'Approve bible'}
        </button>
      </div>
      <StepError message={approveError} errors={approveErrors} className="story-step-error" />
    </div>
  )
}

// Exported only so the payload-contract test can read it without re-deriving
// the field list from the JSX above.
export { BIBLE_REGENERATE_FIELDS }

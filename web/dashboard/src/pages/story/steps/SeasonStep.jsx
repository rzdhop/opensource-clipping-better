import { useEffect, useState } from 'react'
import {
  runStoryStep, approveStoryDoc, regenerateStory, fetchStoryEstimate,
  postEpisodeFeedback, decideProposal,
} from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import RouteChip from '../../../components/RouteChip'
import { LiveActivity, useJobFeed } from '../../../components/ActivityFeed'
import { RegenerateControl, StepError } from '../fields'
import { useConfirm } from '../../../ui'

// The season arc's function badges (spec 2.6): the closed list
// clipping.aistory.schemas.ARC_FUNCTIONS also uses, and their human labels.
const ARC_FUNCTION_LABELS = {
  setup: 'Setup',
  escalation: 'Escalation',
  complication: 'Complication',
  midpoint_twist: 'Midpoint twist',
  crisis: 'Crisis',
  climax_and_reset: 'Climax & reset',
}

// clipping.aistory.schemas.EPISODES_PLANNED_MIN/MAX and season.DEFAULT_EPISODES.
const MIN_EPISODES = 3
const MAX_EPISODES = 12
const DEFAULT_EPISODES = 8

// Phase 5, stage 10 (SeriesMemoryPanel): clipping.aistory.schemas.
// FEEDBACK_TEXT_MAX_LENGTH / FEEDBACK_STATS_MAX_LENGTH -- the API refuses a
// paste over this whole, never trimming it (422); the feedback box checks
// the same cap client-side so the count and the refusal match the server's.
const FEEDBACK_TEXT_MAX_LENGTH = 6000
const FEEDBACK_STATS_MAX_LENGTH = 6000

// clipping.aistory.schemas.CHARACTER_ROLES (CastStep.jsx keeps its own copy
// too, for the same reason: a tiny closed list, not worth importing).
const CHARACTER_ROLES = ['lead', 'support', 'recurring', 'guest']

// Shown when a proposed character's role is set to lead or support (DEC-123,
// unchanged by phase 5): once the cast step writes them, the cast approval
// is cleared until they are approved. Shown before the decision is sent, and
// repeated in the confirm dialog -- decisions here are final.
const ROLE_FOLD_WARNING = 'approving this character re-opens the cast approval'

function clampEpisodes(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return MIN_EPISODES
  return Math.min(MAX_EPISODES, Math.max(MIN_EPISODES, Math.round(n)))
}

// Coordinator fix attempt 2 (F2, phase 5 stage 13b): the server counts a
// paste in Unicode code points (clipping.aistory.workflow._pasted, Python
// len()), not UTF-16 code units -- a JS string's own .length over-counts an
// emoji or other astral character by one (live: 295 vs the server's 294).
// Spreading a string iterates by code point, so this keeps the client's
// count, cap check and message in agreement with the server's either way.
function codePointLength(value) {
  return [...value].length
}

// ------------------------------------------------------------- no arc yet

function NoSeasonYet({ storyId, onChange }) {
  const [episodes, setEpisodes] = useState(DEFAULT_EPISODES)
  const [estimate, setEstimate] = useState(null)
  const [planning, setPlanning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  useEffect(() => {
    fetchStoryEstimate(storyId, 'season', { episodes }).then(setEstimate).catch(() => setEstimate(null))
  }, [storyId, episodes])

  const handlePlan = async () => {
    setPlanning(true)
    setError('')
    setErrors(null)
    try {
      // The season step's own request payload: the payload contract test
      // reads this literal and checks its keys against
      // clipping.aistory.workflow.SEASON_PARAMS.
      const seasonParams = { episodes }
      await runStoryStep(storyId, 'season', { params: seasonParams })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setPlanning(false)
    }
  }

  return (
    <div className="story-step-body">
      <div className="form-group">
        <label className="form-label">Episodes</label>
        <input
          type="number"
          className="form-input story-season-episodes-input"
          min={MIN_EPISODES}
          max={MAX_EPISODES}
          value={episodes}
          onChange={(e) => setEpisodes(clampEpisodes(e.target.value))}
        />
        <p className="form-hint">{MIN_EPISODES} to {MAX_EPISODES}; {DEFAULT_EPISODES} by default.</p>
      </div>
      <div className="story-step-actions">
        <button type="button" className="btn btn-primary" onClick={handlePlan} disabled={planning}>
          {planning ? <><span className="spinner"></span> Planning…</> : 'Plan the season'}
        </button>
        <EstimateChip estimate={estimate} />
        {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      </div>
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// ------------------------------------------------------------ one arc entry

function ArcEntry({ storyId, entry, characters, disabled, onChange }) {
  const nameOf = (id) => {
    const doc = characters.find((c) => c.char_id === id)
    return doc ? doc.name : id
  }

  const regenerate = async (note) => {
    await regenerateStory(storyId, { target: `season:${entry.ep}`, note })
    onChange()
  }

  return (
    <div className="story-season-entry">
      <div className="story-season-entry-header">
        <span className="story-season-entry-number">Ep {entry.ep}</span>
        <span className="chip">{ARC_FUNCTION_LABELS[entry.function] || entry.function}</span>
      </div>
      <p className="story-field-value">{entry.summary}</p>
      {entry.open_hooks_in.length > 0 && (
        <div className="story-field">
          <div className="story-field-label">Hooks in</div>
          <ul className="story-field-list">
            {entry.open_hooks_in.map((hook, i) => <li key={i}>{hook}</li>)}
          </ul>
        </div>
      )}
      {entry.open_hooks_out.length > 0 && (
        <div className="story-field">
          <div className="story-field-label">Hooks out</div>
          <ul className="story-field-list">
            {entry.open_hooks_out.map((hook, i) => <li key={i}>{hook}</li>)}
          </ul>
        </div>
      )}
      {entry.characters.length > 0 && (
        <p className="story-season-entry-characters">{entry.characters.map(nameOf).join(', ')}</p>
      )}
      <RegenerateControl disabled={disabled} onRegenerate={regenerate} />
    </div>
  )
}

// ---------------------------------------------------------------- memory

/**
 * `entry.relationship_deltas` is `{"<char_a>|<char_b>": "<delta text>", ...}`
 * (`clipping.aistory.series_memory.pair_key`, the two ids sorted). Shown by
 * name, never by id -- the same `characters` lookup `ArcEntry` above uses.
 */
function relationshipLines(entry, characters) {
  if (!entry) return []
  const nameOf = (id) => {
    const doc = characters.find((c) => c.char_id === id)
    return doc ? doc.name : id
  }
  return Object.entries(entry.relationship_deltas || {}).map(([key, delta]) => {
    const [a, b] = key.split('|')
    return `${nameOf(a)} & ${nameOf(b)}: ${delta}`
  })
}

/**
 * One episode's series memory (`series.memory`: `{state, entry}`,
 * `clipping.aistory.workflow.series_page`): the recap, the hooks it opened
 * and closed, its relationship deltas by character name, a stale banner, and
 * "Update memory" / "Approve memory" (draft only -- a stale entry cannot be
 * approved: run memory again first). `gate` is this episode's
 * `next_episode_gate` -- why the episode after it is still locked, or null.
 *
 * `scriptApproved` (coordinator fix attempt 1, F1) is episode `ep`'s script
 * state from `data.episodes`: `clipping.aistory.steps.memory.
 * require_approved_script` 409s the estimate and the run alike without one,
 * so the estimate is fetched -- and "Update memory" enabled -- only once the
 * script is approved, instead of firing into a guaranteed refusal on every
 * episode that has not been written yet.
 */
function MemoryCard({ storyId, ep, memory, gate, scriptApproved, characters, disabled, onChange }) {
  const [estimate, setEstimate] = useState(null)
  const [estimateError, setEstimateError] = useState('')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  const [approving, setApproving] = useState(false)
  const [approveError, setApproveError] = useState('')

  const blockReason = disabled ? 'A step is already running.'
    : !scriptApproved ? `Approve episode ${ep}'s script first.`
      : ''

  useEffect(() => {
    if (blockReason) {
      setEstimate(null)
      setEstimateError('')
      return undefined
    }
    let cancelled = false
    fetchStoryEstimate(storyId, 'memory', { ep })
      .then((value) => { if (!cancelled) { setEstimate(value); setEstimateError('') } })
      .catch((err) => { if (!cancelled) setEstimateError(err.message) })
    return () => { cancelled = true }
  }, [storyId, ep, blockReason])

  const handleUpdate = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      await runStoryStep(storyId, 'memory', { ep })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  const handleApprove = async () => {
    setApproving(true)
    setApproveError('')
    try {
      await approveStoryDoc(storyId, `memory:${ep}`)
      onChange()
    } catch (err) {
      setApproveError(err.message)
    } finally {
      setApproving(false)
    }
  }

  const entry = memory.entry
  const relationships = relationshipLines(entry, characters)

  return (
    <div className="story-field story-season-memory-entry">
      <div className="story-field-label">Series memory</div>

      {memory.state === 'stale' && (
        <p className="chip chip-warn story-season-memory-banner">
          Episode {ep}&apos;s series memory is out of date: the script changed since it was written. Update
          memory, then approve it again.
        </p>
      )}

      {!entry && <p className="story-field-value"><em>No memory written yet for episode {ep}.</em></p>}

      {entry && (
        <>
          <p className="story-field-value story-season-preserve-lines">{entry.recap}</p>
          {entry.hooks_opened.length > 0 && (
            <div className="story-field">
              <div className="story-field-label">Hooks opened</div>
              <ul className="story-field-list">
                {entry.hooks_opened.map((hook, i) => <li key={i}>{hook}</li>)}
              </ul>
            </div>
          )}
          {entry.hooks_closed.length > 0 && (
            <div className="story-field">
              <div className="story-field-label">Hooks closed</div>
              <ul className="story-field-list">
                {entry.hooks_closed.map((hook, i) => <li key={i}>{hook}</li>)}
              </ul>
            </div>
          )}
          {relationships.length > 0 && (
            <div className="story-field">
              <div className="story-field-label">Relationships</div>
              <ul className="story-field-list">
                {relationships.map((line, i) => <li key={i}>{line}</li>)}
              </ul>
            </div>
          )}
          <p className="form-hint">
            {memory.state === 'approved' ? 'Approved.'
              : memory.state === 'stale' ? 'Written, but out of date.'
                : 'Written, not approved yet.'}
          </p>
        </>
      )}

      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={handleUpdate}
          disabled={disabled || running || Boolean(blockReason)}
        >
          {running ? <><span className="spinner"></span> Updating…</> : 'Update memory'}
        </button>
        {memory.state === 'draft' && (
          <button type="button" className="btn btn-primary btn-sm" onClick={handleApprove} disabled={disabled || approving}>
            {approving ? 'Approving…' : 'Approve memory'}
          </button>
        )}
        {blockReason ? (
          <p className="form-hint story-season-blocked-reason">{blockReason}</p>
        ) : estimateError ? (
          <p className="chip chip-warn story-season-blocked-reason">{estimateError}</p>
        ) : (
          <EstimateChip estimate={estimate} />
        )}
      </div>
      <StepError message={error} errors={errors} className="story-step-error" />
      <StepError message={approveError} className="story-step-error" />

      {gate && <p className="form-hint story-season-memory-gate">{gate}</p>}
    </div>
  )
}

// -------------------------------------------------------------- feedback

/**
 * One episode's audience feedback (`series.feedback`: null, `{ep, pasted_at,
 * text, stats?}`, then `+ {digest, directions}` once F1 has digested it,
 * then `+ {chosen_direction}` once approved): paste -> digest and three
 * directions -> pick one or "none". A new paste always replaces the item
 * (`workflow.store_feedback`), so "Paste new feedback" stays available at
 * every stage.
 */
function FeedbackBox({ storyId, ep, feedback, disabled, onChange }) {
  const [editing, setEditing] = useState(!feedback)
  const [text, setText] = useState((feedback && feedback.text) || '')
  const [stats, setStats] = useState((feedback && feedback.stats) || '')
  const [estimate, setEstimate] = useState(null)
  const [estimateError, setEstimateError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  const [direction, setDirection] = useState(feedback && 'chosen_direction' in feedback ? feedback.chosen_direction : null)
  const [choosing, setChoosing] = useState(false)
  const [chooseError, setChooseError] = useState('')

  const hasFeedbackItem = Boolean(feedback)

  // clipping.aistory.steps.feedback.require_feedback: the estimate 409s
  // without a pasted item already, so there is no estimate to show for the
  // very first paste of an episode (the endpoint needs the item to exist) --
  // fetched only once one does, and skipped while a step is already running.
  useEffect(() => {
    if (!hasFeedbackItem || disabled) {
      setEstimate(null)
      setEstimateError('')
      return undefined
    }
    let cancelled = false
    fetchStoryEstimate(storyId, 'feedback', { ep })
      .then((value) => { if (!cancelled) { setEstimate(value); setEstimateError('') } })
      .catch((err) => { if (!cancelled) setEstimateError(err.message) })
    return () => { cancelled = true }
  }, [storyId, ep, hasFeedbackItem, disabled])

  const digested = Boolean(feedback && 'directions' in feedback)
  const chosen = Boolean(feedback && 'chosen_direction' in feedback)
  const overText = codePointLength(text) > FEEDBACK_TEXT_MAX_LENGTH
  const overStats = codePointLength(stats) > FEEDBACK_STATS_MAX_LENGTH

  // Same wording as the API's own refusal (clipping.aistory.workflow._pasted):
  // refused whole, never trimmed -- shown client-side before the cap is hit
  // on the server, so the count and the message agree either way. Counted in
  // code points (F2), like the server, not UTF-16 units.
  const capMessage = (what, value, limit) =>
    `The ${what} is ${codePointLength(value)} characters: at most ${limit} are taken, and it is never shortened -- paste ` +
    'less (the part that matters most).'

  const startEditing = () => {
    setText((feedback && feedback.text) || '')
    setStats((feedback && feedback.stats) || '')
    setError('')
    setErrors(null)
    setEditing(true)
  }

  const handleSubmit = async () => {
    if (overText || overStats || !text.trim()) return
    setSubmitting(true)
    setError('')
    setErrors(null)
    try {
      // The feedback paste's own request payload: the payload contract test
      // reads this literal and checks its keys against
      // web.api.models.StoryEpisodeFeedbackRequest.
      const feedbackPayload = stats.trim() ? { text: text.trim(), stats: stats.trim() } : { text: text.trim() }
      await postEpisodeFeedback(storyId, ep, feedbackPayload)
      setEditing(false)
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setSubmitting(false)
    }
  }

  const handleChoose = async () => {
    setChoosing(true)
    setChooseError('')
    try {
      await approveStoryDoc(storyId, `feedback:${ep}`, { direction })
      onChange()
    } catch (err) {
      setChooseError(err.message)
    } finally {
      setChoosing(false)
    }
  }

  return (
    <div className="story-field story-season-feedback">
      <div className="story-field-label">Audience feedback</div>

      {!editing && feedback && (
        <div className="story-field-value story-season-feedback-current">
          <p className="story-season-preserve-lines">{feedback.text}</p>
          {feedback.stats && (
            <p className="form-hint story-season-preserve-lines">Stats: {feedback.stats}</p>
          )}
        </div>
      )}

      {editing && (
        <>
          <textarea
            aria-label="Audience comments"
            className="form-input story-season-feedback-textarea"
            rows={4}
            placeholder="Paste the audience comments"
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={disabled || submitting}
          />
          <p className="form-hint">{codePointLength(text)} / {FEEDBACK_TEXT_MAX_LENGTH}</p>
          {overText && <p className="story-error">{capMessage('feedback text', text, FEEDBACK_TEXT_MAX_LENGTH)}</p>}
          <textarea
            aria-label="Audience stats (optional)"
            className="form-input story-season-feedback-textarea"
            rows={2}
            placeholder="Optional stats (views, likes, comments...)"
            value={stats}
            onChange={(e) => setStats(e.target.value)}
            disabled={disabled || submitting}
          />
          <p className="form-hint">{codePointLength(stats)} / {FEEDBACK_STATS_MAX_LENGTH}</p>
          {overStats && <p className="story-error">{capMessage('stats text', stats, FEEDBACK_STATS_MAX_LENGTH)}</p>}
          <div className="story-step-actions">
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleSubmit}
              disabled={disabled || submitting || overText || overStats || !text.trim()}
            >
              {submitting ? 'Submitting…' : 'Submit feedback'}
            </button>
            {feedback && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setEditing(false)} disabled={submitting}>
                Cancel
              </button>
            )}
            {/* clipping.aistory.steps.feedback.require_feedback: no item to
               estimate against yet on a first paste -- shown only once a
               feedback item already exists (a re-paste, i.e. hasFeedbackItem). */}
            {hasFeedbackItem && (
              estimateError ? (
                <span className="chip chip-warn story-season-blocked-reason">{estimateError}</span>
              ) : (
                <EstimateChip estimate={estimate} />
              )
            )}
          </div>
          <StepError message={error} errors={errors} className="story-step-error" />
        </>
      )}

      {!editing && (
        <div className="story-step-actions">
          <button type="button" className="btn btn-secondary btn-sm" onClick={startEditing} disabled={disabled}>
            {feedback ? 'Paste new feedback' : 'Paste feedback'}
          </button>
          {hasFeedbackItem && (
            estimateError ? (
              <span className="chip chip-warn story-season-blocked-reason">{estimateError}</span>
            ) : (
              <EstimateChip estimate={estimate} />
            )
          )}
        </div>
      )}

      {feedback && !digested && <p className="form-hint">Queued — waiting for the digest.</p>}

      {feedback && digested && (
        <div className="story-field story-season-feedback-digest">
          <div className="story-field-label">Digest</div>
          <p className="story-field-value story-season-preserve-lines">{feedback.digest}</p>
          {!chosen ? (
            <>
              <div className="story-field-label">Pick a direction</div>
              <ul className="story-field-list story-season-feedback-directions">
                {feedback.directions.map((line, i) => (
                  <li key={i}>
                    <label>
                      <input
                        type="radio"
                        name={`direction-${ep}`}
                        checked={direction === i}
                        onChange={() => setDirection(i)}
                      />
                      {' '}{line}
                    </label>
                  </li>
                ))}
                <li>
                  <label>
                    <input
                      type="radio"
                      name={`direction-${ep}`}
                      checked={direction === null}
                      onChange={() => setDirection(null)}
                    />
                    {' '}None of these
                  </label>
                </li>
              </ul>
              <div className="story-step-actions">
                <button type="button" className="btn btn-primary btn-sm" onClick={handleChoose} disabled={disabled || choosing}>
                  {choosing ? 'Choosing…' : 'Choose direction'}
                </button>
              </div>
              <StepError message={chooseError} className="story-step-error" />
            </>
          ) : (
            <p className="form-hint">
              {feedback.chosen_direction === null
                ? 'No direction chosen.'
                : `Direction chosen: ${feedback.directions[feedback.chosen_direction]}`}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------- propose next

/**
 * "Propose next episode": queues `propose-next` for episode `ep`, whose
 * approved and fresh memory it reads to write episode `ep + 1`'s proposals
 * (`ProposalsCard` on that episode's own card shows them). Hidden on the
 * season's last episode -- there is no episode after it to propose for.
 *
 * `memoryApproved` (coordinator fix attempt 1, F1/F3) is `series[ep-1].
 * memory.state === 'approved'`: `clipping.aistory.steps.propose_next.
 * require_fresh_memory` 409s the estimate and the run alike without a fresh
 * approved memory (a draft counts as not approved here -- exactly why
 * episode 1's control must stay disabled while its memory is still a
 * draft), so both are gated on it instead of firing into a guaranteed
 * refusal.
 */
function ProposeNextControl({ storyId, ep, memoryApproved, disabled, onChange }) {
  const [estimate, setEstimate] = useState(null)
  const [estimateError, setEstimateError] = useState('')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  const blockReason = disabled ? 'A step is already running.'
    : !memoryApproved ? `Approve episode ${ep}'s memory first.`
      : ''

  useEffect(() => {
    if (blockReason) {
      setEstimate(null)
      setEstimateError('')
      return undefined
    }
    let cancelled = false
    fetchStoryEstimate(storyId, 'propose-next', { ep })
      .then((value) => { if (!cancelled) { setEstimate(value); setEstimateError('') } })
      .catch((err) => { if (!cancelled) setEstimateError(err.message) })
    return () => { cancelled = true }
  }, [storyId, ep, blockReason])

  const handleRun = async () => {
    setRunning(true)
    setError('')
    setErrors(null)
    try {
      await runStoryStep(storyId, 'propose-next', { ep })
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="story-field story-season-propose-next">
      <div className="story-field-label">Propose next episode</div>
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={handleRun}
          disabled={disabled || running || Boolean(blockReason)}
        >
          {running ? <><span className="spinner"></span> Proposing…</> : 'Propose next episode'}
        </button>
        {blockReason ? (
          <p className="form-hint story-season-blocked-reason">{blockReason}</p>
        ) : estimateError ? (
          <p className="chip chip-warn story-season-blocked-reason">{estimateError}</p>
        ) : (
          <EstimateChip estimate={estimate} />
        )}
      </div>
      <StepError message={error} errors={errors} className="story-step-error" />
    </div>
  )
}

// ------------------------------------------------------------- proposals

/**
 * One proposed character or twist (`series.proposals.characters` /
 * `.twists`, `clipping.aistory.steps.propose_next.proposals_doc`): accept or
 * reject, confirmed first -- a decision is final (`workflow.decide_proposal`
 * refuses a second one, 409). A character's role picker defaults to the
 * proposal's own (recurring or guest); choosing lead or support shows the
 * fold warning before the confirm, and again inside it. Accepting a
 * character answers `{..., job}`: the queued cast job, shown once it
 * arrives.
 */
function ProposalItem({ storyId, ep, item, kind, decision, disabled, onChange }) {
  const confirm = useConfirm()
  const [role, setRole] = useState(kind === 'character' ? item.role : null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)
  const [castJob, setCastJob] = useState(null)

  const foldsCast = kind === 'character' && (role === 'lead' || role === 'support')

  const decide = async (accept) => {
    const subject = kind === 'character' ? item.name : `the twist for episode ${item.target_ep}`
    const question = `${accept ? 'Accept' : 'Reject'} ${subject}?`
    let message = 'This decision is final.'
    if (accept && foldsCast) message += ` Heads up: ${ROLE_FOLD_WARNING}.`
    const confirmed = await confirm({
      title: question,
      message,
      confirmLabel: accept ? 'Accept' : 'Reject',
    })
    if (!confirmed) return
    setBusy(true)
    setError('')
    setErrors(null)
    try {
      // The proposal decision's own request payload: the payload contract
      // test reads this literal and checks its keys against
      // web.api.models.StoryProposalDecisionRequest. A role is sent only
      // when accepting a character -- sending one on a rejection is invalid
      // (workflow.proposal_request).
      const decisionPayload = accept && kind === 'character' ? { accept, role } : { accept }
      const result = await decideProposal(storyId, ep, item.item_id, decisionPayload)
      if (result.job) setCastJob(result.job)
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="story-season-proposal-item">
      {kind === 'character' ? (
        <>
          <p className="story-field-value story-season-preserve-lines"><strong>{item.name}</strong> — {item.one_line}</p>
          {item.archetype && <p className="form-hint">{item.archetype}</p>}
          <p className="form-hint story-season-preserve-lines">{item.why}</p>
        </>
      ) : (
        <>
          <p className="story-field-value story-season-preserve-lines">
            Twist for episode {item.target_ep}: {item.summary}
          </p>
          {item.open_hooks_out.length > 0 && (
            <ul className="story-field-list">
              {item.open_hooks_out.map((hook, i) => <li key={i}>{hook}</li>)}
            </ul>
          )}
          <p className="form-hint story-season-preserve-lines">{item.why}</p>
        </>
      )}

      {!decision && (
        <>
          {kind === 'character' && (
            <div className="story-season-proposal-role">
              <label className="form-label" htmlFor={`role-${item.item_id}`}>Role</label>
              <select
                id={`role-${item.item_id}`}
                className="form-select"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                disabled={disabled || busy}
              >
                {CHARACTER_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
              </select>
              {foldsCast && (
                <p className="chip chip-warn story-season-proposal-warning">
                  Choosing lead or support here means {ROLE_FOLD_WARNING}.
                </p>
              )}
            </div>
          )}
          <div className="story-step-actions">
            <button type="button" className="btn btn-primary btn-sm" onClick={() => decide(true)} disabled={disabled || busy}>
              {busy ? 'Working…' : 'Accept'}
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => decide(false)} disabled={disabled || busy}>
              Reject
            </button>
          </div>
          <StepError message={error} errors={errors} className="story-step-error" />
        </>
      )}

      {decision && <p className="form-hint">{decision === 'accepted' ? 'Accepted.' : 'Rejected.'}</p>}
      {castJob && <p className="form-hint">Cast job queued: {castJob.id} ({castJob.status}).</p>}
    </li>
  )
}

/** The proposals made *for* this episode (N1 of the episode before, `series.
 * proposals`): a card per item, and "Approve proposals" once every one is
 * decided (`workflow.approve_proposals`: 409 naming the undecided ones
 * otherwise -- disabled here so that refusal is never hit in the ordinary
 * path).
 *
 * `approved` (coordinator fix attempt 2, F5) is `series[ep].
 * proposals_approved`: `approve_proposals` writes nothing of its own ("the
 * decisions are the record"), so once it has run the button must not stay
 * live forever -- it reads "Approved" and disables, the same pattern
 * SeasonActions' own `season.approved_at` already uses below. */
function ProposalsCard({ storyId, ep, proposals, approved, disabled, onChange }) {
  const [approving, setApproving] = useState(false)
  const [approveError, setApproveError] = useState('')

  if (!proposals) return null

  const items = [
    ...proposals.characters.map((item) => ({ item, kind: 'character' })),
    ...proposals.twists.map((item) => ({ item, kind: 'twist' })),
  ]
  const allDecided = items.every(({ item }) => Boolean(proposals.decisions[item.item_id]))

  const handleApprove = async () => {
    setApproving(true)
    setApproveError('')
    try {
      await approveStoryDoc(storyId, `proposals:${ep}`)
      onChange()
    } catch (err) {
      setApproveError(err.message)
    } finally {
      setApproving(false)
    }
  }

  return (
    <div className="story-field story-season-proposals">
      <div className="story-field-label">Proposed for episode {ep}</div>
      <ul className="story-field-list story-season-proposal-list">
        {items.map(({ item, kind }) => (
          <ProposalItem
            key={item.item_id}
            storyId={storyId}
            ep={ep}
            item={item}
            kind={kind}
            decision={proposals.decisions[item.item_id]}
            disabled={disabled}
            onChange={onChange}
          />
        ))}
      </ul>
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={handleApprove}
          disabled={disabled || approving || !allDecided || approved}
        >
          {approving ? 'Approving…' : approved ? 'Approved' : 'Approve proposals'}
        </button>
      </div>
      <StepError message={approveError} className="story-step-error" />
    </div>
  )
}

// ------------------------------------------------------------ the panel

/** Episode `ep`'s script state (`data.episodes`, `workflow.episode_summaries`
 * -- only episodes with a folder appear there at all, so a not-yet-started
 * one is correctly `'none'`), or `'none'` when it has no folder yet. */
function scriptStateOf(episodes, ep) {
  const found = (episodes || []).find((entry) => entry.ep === ep)
  return found ? found.script_state : 'none'
}

/**
 * Coordinator fix attempt 1 (F2): a full card is worth rendering only for an
 * episode that has *something* -- a script, a memory entry, a feedback item,
 * or proposals made for it (N1 of the episode before writes episode ep + 1's
 * proposals before it has any script at all, so `proposals` must count on
 * its own, not only alongside a script).
 */
function hasContent(page, scriptState) {
  return Boolean(
    (scriptState && scriptState !== 'none')
    || (page.memory && page.memory.entry)
    || page.feedback
    || page.proposals,
  )
}

/**
 * The series panel (phase 5, plan 11 stage 10): one card per episode that
 * has something yet (script, memory, feedback or proposals -- `hasContent`);
 * a run of episodes with nothing yet collapses into one quiet line instead
 * of a full, mostly-empty card each (fix attempt 1, F2). Each full card
 * carries its series memory, its audience feedback box, "Propose next
 * episode" (unless it is the season's last), and the proposals made for it.
 */
function SeriesMemoryPanel({ storyId, series, episodes, characters, totalEpisodes, disabled, onChange }) {
  if (!series || series.length === 0) {
    return (
      <div className="card story-season-memory">
        <h4 className="card-title">Series memory</h4>
        <p className="form-hint">Filled by the memory step once an episode's script is approved.</p>
      </div>
    )
  }

  // One row per episode: a full card, or -- runs of nothing-yet episodes
  // merged together -- one collapsed line.
  const rows = []
  let emptyRun = null
  series.forEach((page) => {
    const scriptState = scriptStateOf(episodes, page.ep)
    if (hasContent(page, scriptState)) {
      if (emptyRun) {
        rows.push(emptyRun)
        emptyRun = null
      }
      rows.push({ kind: 'episode', page, scriptState })
    } else if (emptyRun) {
      emptyRun.to = page.ep
    } else {
      emptyRun = { kind: 'empty', from: page.ep, to: page.ep }
    }
  })
  if (emptyRun) rows.push(emptyRun)

  return (
    <div className="card story-season-memory">
      <h4 className="card-title">Series memory</h4>
      {rows.map((row) => (row.kind === 'empty' ? (
        <p key={`empty-${row.from}`} className="form-hint story-season-memory-empty-range">
          {row.from === row.to ? `Episode ${row.from}` : `Episodes ${row.from}–${row.to}`}: nothing yet — each
          fills in once its script is approved.
        </p>
      ) : (
        <div key={row.page.ep} className="story-season-memory-episode">
          <div className="story-season-entry-header">
            <span className="story-season-entry-number">Ep {row.page.ep}</span>
          </div>
          <MemoryCard
            storyId={storyId}
            ep={row.page.ep}
            memory={row.page.memory}
            gate={row.page.next_episode_gate}
            scriptApproved={row.scriptState === 'approved'}
            characters={characters}
            disabled={disabled}
            onChange={onChange}
          />
          <FeedbackBox storyId={storyId} ep={row.page.ep} feedback={row.page.feedback} disabled={disabled} onChange={onChange} />
          {row.page.ep < totalEpisodes && (
            <ProposeNextControl
              storyId={storyId}
              ep={row.page.ep}
              memoryApproved={row.page.memory.state === 'approved'}
              disabled={disabled}
              onChange={onChange}
            />
          )}
          <ProposalsCard
            storyId={storyId}
            ep={row.page.ep}
            proposals={row.page.proposals}
            approved={row.page.proposals_approved}
            disabled={disabled}
            onChange={onChange}
          />
        </div>
      )))}
    </div>
  )
}

// -------------------------------------------------------------- approve/re-plan

function SeasonActions({ storyId, season, disabled, onChange }) {
  const confirm = useConfirm()
  const [approving, setApproving] = useState(false)
  const [approveError, setApproveError] = useState('')
  const [approveErrors, setApproveErrors] = useState(null)
  const [replanning, setReplanning] = useState(false)
  const [replanError, setReplanError] = useState('')
  const [replanErrors, setReplanErrors] = useState(null)

  const handleApprove = async () => {
    setApproving(true)
    setApproveError('')
    setApproveErrors(null)
    try {
      await approveStoryDoc(storyId, 'season')
      onChange()
    } catch (err) {
      setApproveError(err.message)
      setApproveErrors(err.errors || null)
    } finally {
      setApproving(false)
    }
  }

  const handleReplan = async () => {
    if (!(await confirm({
      title: 'Re-plan the season?',
      message: 'This replaces the current arc entirely.',
      confirmLabel: 'Re-plan',
      tone: 'danger',
    }))) return
    setReplanning(true)
    setReplanError('')
    setReplanErrors(null)
    try {
      const replanParams = { episodes: season.episodes_planned }
      await runStoryStep(storyId, 'season', { params: replanParams })
      onChange()
    } catch (err) {
      setReplanError(err.message)
      setReplanErrors(err.errors || null)
    } finally {
      setReplanning(false)
    }
  }

  return (
    <div className="story-season-actions">
      <div className="story-step-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleApprove}
          disabled={disabled || approving || Boolean(season.approved_at)}
        >
          {approving ? 'Approving…' : season.approved_at ? 'Approved' : 'Approve season'}
        </button>
      </div>
      <StepError message={approveError} errors={approveErrors} className="story-step-error" />

      <div className="story-season-replan">
        <button type="button" className="btn btn-secondary" onClick={handleReplan} disabled={disabled || replanning}>
          {replanning ? <><span className="spinner"></span> Re-planning…</> : 'Re-plan'}
        </button>
      </div>
      <StepError message={replanError} errors={replanErrors} className="story-step-error" />
    </div>
  )
}

// --------------------------------------------------------------------- page

export default function SeasonStep({ data, storyId, inFlightJob, onChange, onSeriesChange }) {
  const { season, characters, series, episodes } = data

  const myJob = inFlightJob && (
    inFlightJob.step === 'season'
    || (inFlightJob.step === 'regenerate' && inFlightJob.params
        && typeof inFlightJob.params.target === 'string'
        && inFlightJob.params.target.startsWith('season:'))
  ) ? inFlightJob : null

  // Coordinator fix attempt 2 (F1): this job feed watches the season step's
  // own jobs (plan/re-plan, an arc entry's regenerate) -- its completion
  // must not collapse the step either, the same reason SeriesMemoryPanel
  // below takes onSeriesChange instead of onChange.
  const { job: liveJob, events, streamState } = useJobFeed(myJob ? myJob.id : null, {
    onJob: (job) => {
      if (job && job.status !== 'queued' && job.status !== 'running') onSeriesChange()
    },
  })

  const busy = Boolean(inFlightJob)

  if (!season || !season.arc || season.arc.length === 0) {
    return (
      <div className="story-step-body">
        <NoSeasonYet storyId={storyId} onChange={onChange} />
        {myJob && liveJob && (
          liveJob.status === 'queued'
            ? <p className="form-hint">Waiting to start…</p>
            : <LiveActivity job={liveJob} events={events} streamState={streamState} />
        )}
      </div>
    )
  }

  return (
    <div className="story-step-body">
      {myJob && liveJob && (
        liveJob.status === 'queued'
          ? <p className="form-hint">Waiting to start…</p>
          : <LiveActivity job={liveJob} events={events} streamState={streamState} />
      )}

      <div className="story-season-timeline">
        {season.arc.map((entry) => (
          <ArcEntry
            key={entry.ep}
            storyId={storyId}
            entry={entry}
            characters={characters}
            disabled={busy}
            onChange={onChange}
          />
        ))}
      </div>

      <SeriesMemoryPanel
        storyId={storyId}
        series={series}
        episodes={episodes}
        characters={characters}
        totalEpisodes={season.episodes_planned}
        disabled={busy}
        onChange={onSeriesChange}
      />

      <SeasonActions storyId={storyId} season={season} disabled={busy} onChange={onChange} />
    </div>
  )
}

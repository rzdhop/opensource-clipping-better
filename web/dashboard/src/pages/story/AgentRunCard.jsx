import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchStoryEstimate, runStoryStep } from '../../api'
import { formatUsd } from '../../lib/format'
import { Button, Card, CardBody, CardHeader, useConfirm } from '../../ui'
import { Bot } from '../../ui/icons'
import { agentPartOf, IN_FLIGHT } from './storySteps'

const AGENT_STEP = 'story-fast-track'
const EPISODE = 1
const AGENT_LABEL = ('One run from the idea to episode 1; the agent approves the style, the cast and the places '
  + 'as soon as they are complete -- no taste check; review them in Studio afterwards, every regenerate stays '
  + 'available.')

/** The latest `story-fast-track` job of the story, or null (`data.jobs` is
 * oldest first: the last match is the newest). */
function latestAgentJob(jobs) {
  const matches = (jobs || []).filter((job) => job.step === AGENT_STEP)
  return matches.length ? matches[matches.length - 1] : null
}

/**
 * The agent run's own card (plan 21 stage 3, DEC-270): shown only on an
 * agent-mode story (StoryWorkspace.jsx checks `story.generation_profile.mode`
 * -- a Studio story renders nothing here). "Run the agent" shows the summed
 * estimate (`GET /estimate/story-fast-track`'s own message, its paid total
 * and the caps line) in the kit's confirm dialog -- Cancel focused first, as
 * every story confirm -- then runs `POST /steps/story-fast-track`. While the
 * job runs the card names the part its `sub_step` is on ("Agent 4/9: cast",
 * `agentPartOf`); the rail marks the same step running (`stepOfJob` reads the
 * same map). Once the job stops, the card shows the runner's last line
 * (`job.error`, the sentence `story_fast_track.stop_message` ends the job
 * with) and the button reads "Continue the agent run" -- the same job, run
 * again, repeats nothing already done. Episode 1 rendered: a link to its
 * Review tab, no button. Plan 22 stage 5: a run paused for the user's own
 * clips (status `awaiting_uploads`) says what it waits for and -- plan 25
 * stage 3 -- opens the episode's Handoff (prompts, uploads, the brief's
 * export); the upload that leaves nothing missing starts it again by itself.
 */
export default function AgentRunCard({ storyId, data, onChange }) {
  const confirm = useConfirm()
  const [estimate, setEstimate] = useState(null)
  const [estimateError, setEstimateError] = useState('')
  const [running, setRunning] = useState(false)
  const [error, setError] = useState('')

  const job = latestAgentJob(data.jobs)
  const jobRunning = Boolean(job) && IN_FLIGHT.includes(job.status)
  const paused = Boolean(job) && job.status === 'awaiting_uploads'
  const episode1 = (data.episodes || []).find((entry) => entry.ep === EPISODE)
  const stopped = Boolean(job) && !jobRunning && !paused && !episode1

  useEffect(() => {
    if (jobRunning || episode1) return undefined
    let cancelled = false
    setEstimate(null)
    setEstimateError('')
    fetchStoryEstimate(storyId, AGENT_STEP)
      .then((body) => { if (!cancelled) { setEstimate(body); setEstimateError('') } })
      .catch((err) => { if (!cancelled) { setEstimate(null); setEstimateError(err.message) } })
    return () => { cancelled = true }
  }, [storyId, jobRunning, episode1, job && job.id, job && job.status])

  const handleRun = async () => {
    if (!estimate) return
    const paidTotal = `Total: est $${formatUsd(estimate.est_usd)}${estimate.paid.length ? ' (paid)' : ''}`
    const confirmed = await confirm({
      title: 'Run the agent',
      message: [estimate.message, paidTotal, estimate.caps_line].filter(Boolean).join('\n'),
      confirmLabel: 'Run the agent',
    })
    if (!confirmed) return
    setRunning(true)
    setError('')
    try {
      await runStoryStep(storyId, AGENT_STEP, {})
      onChange()
    } catch (err) {
      setError(err.message)
    } finally {
      setRunning(false)
    }
  }

  if (paused) {
    const uploads = job.uploads || {}
    return (
      <Card className="story-agent-run-card">
        <CardHeader icon={Bot} title="Agent run" subtitle={`Paused at episode ${EPISODE}: ${uploads.message || 'waiting for your clips'}.`} />
        <CardBody>
          <p className="form-hint">
            Make each clip on your own subscription from the Handoff's prompts, then upload it on its shot: the run goes on
            by itself once every clip is there, repeating nothing already done.
          </p>
          <div className="story-step-actions">
            {/* Plan 25 stage 3: the Handoff holds the prompts, the uploads and the brief's export. */}
            <Button as={Link} variant="primary" to={`/story/${storyId}/episodes/${EPISODE}/handoff`}>
              Open the Handoff →
            </Button>
          </div>
        </CardBody>
      </Card>
    )
  }

  if (episode1) {
    return (
      <Card className="story-agent-run-card">
        <CardHeader icon={Bot} title="Agent run" subtitle={`Episode ${EPISODE} is rendered.`} />
        <CardBody>
          <Link to={`/story/${storyId}/episodes/${EPISODE}#review`} className="story-ready-open-episode">
            {`Open episode ${EPISODE}'s review →`}
          </Link>
        </CardBody>
      </Card>
    )
  }

  const part = jobRunning ? agentPartOf(job.sub_step) : null
  const buttonLabel = stopped ? 'Continue the agent run' : 'Run the agent'

  return (
    <Card className="story-agent-run-card">
      <CardHeader icon={Bot} title="Agent run" subtitle={AGENT_LABEL} />
      <CardBody>
        <div className="story-step-actions">
          <Button
            variant="primary"
            onClick={handleRun}
            loading={running}
            disabled={running || jobRunning || !estimate || Boolean(estimateError)}
          >
            {jobRunning ? `Agent ${part ? `${part.number}/${part.total}: ${part.label}` : '…'}` : buttonLabel}
          </Button>
          {estimateError ? (
            <span className="chip chip-warn chip-wrap">{estimateError}</span>
          ) : estimate && !jobRunning && (
            <span className="chip" title={estimate.message || ''}>est. ${formatUsd(estimate.est_usd)} total</span>
          )}
        </div>
        {stopped && job.error && <p className="story-error">{job.error}</p>}
        {error && <p className="story-error">{error}</p>}
      </CardBody>
    </Card>
  )
}

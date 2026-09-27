// PLACEHOLDER (stage 10): the real Storyboard pane -- shots grouped by scene,
// framing/motion/duration, the prompt accordion, reference thumbnails,
// transition chips, re-plan-one-shot and approve -- arrives in stage 11 and
// replaces this file. It only reads the episode's storyboard state today, so
// the pane says something true (planned or not) rather than nothing.

export default function StoryboardPane({ data }) {
  const state = data.state.storyboard

  return (
    <div className="story-step-body">
      <div className="card">
        <h3 className="card-title">Storyboard</h3>
        <p>
          {state === 'none'
            ? 'No shots planned yet.'
            : `Shots: ${state}.`}
        </p>
        <p className="form-hint">The storyboard pane (shot grid, prompts, references, approve) arrives in stage 11.</p>
      </div>
    </div>
  )
}

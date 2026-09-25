// Placeholder for the AI Story mode. The pages of the spec's section 10
// (stories list, wizard, cast, places, season, episode studio) arrive with
// phase 1; phase 0 ships the foundation they stand on.
export default function StoryHome() {
  return (
    <div className="fade-in">
      <div className="page-header">
        <div>
          <h2>✨ AI Story</h2>
          <p>Coming in phase 1</p>
        </div>
      </div>

      <div className="card">
        <p style={{ marginBottom: '10px' }}>
          AI Story turns a concept into a persistent story workspace — world, style lock,
          characters, places, props and a season arc — and produces ~60-second serialized
          episodes with consistent AI characters, per-character voices, music, subtitles
          and a platform metadata pack. Every step is gated: nothing is generated until
          you approve the previous step.
        </p>
        <p style={{ color: 'var(--text-secondary)' }}>
          What exists today is the foundation: generation provider chains (image, image
          edit, video, TTS, vision), a hardware profile for local generation, and the
          budget with its caps. They live under <strong>Settings</strong>. The story
          workspace itself is the next phase.
        </p>
      </div>
    </div>
  )
}

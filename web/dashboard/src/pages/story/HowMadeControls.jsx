import { Button } from '../../ui'

// Plan 25 stage 5 (D-4): the two plain choices that decide whether the app bills anything, shared by the
// new-story form and the profile card. They carry no state of their own: the caller maps "My own" clips to
// the native_speech_manual budget profile and "My own" images to generation_profile.images = 'manual', and
// reads both back from the profile, so the Budget profile select and these controls always agree. They set
// the step default only; a single shot's own mode is chosen in the Handoff view.
const CLIPS_HELP = {
  auto: 'The app generates them on the API links; the caps and the daily cap apply.',
  own: 'You paste the prompts into your provider and upload the clips; nothing is billed.',
}
// The images' own wording (the keyframes, sheets, plates and props).
const IMAGES_HELP = {
  auto: CLIPS_HELP.auto,
  own: 'You paste the prompts into your provider and upload the images; nothing is billed.',
}

function Choice({ label, name, own, disabled, onChange, help, ownDisabled }) {
  return (
    <div className="form-group">
      <label className="form-label">{label}</label>
      <div className="story-segmented" role="group" aria-label={label}>
        <Button type="button" size="sm" variant={own ? 'secondary' : 'primary'} disabled={disabled}
          aria-pressed={!own} onClick={() => onChange(false)} data-choice={`${name}-auto`}>
          Auto (API links)
        </Button>
        <Button type="button" size="sm" variant={own ? 'primary' : 'secondary'} disabled={disabled || ownDisabled}
          aria-pressed={own} onClick={() => onChange(true)} data-choice={`${name}-own`}>
          My own (Flow, Higgsfield…)
        </Button>
      </div>
      <p className="form-hint">{help}</p>
    </div>
  )
}

/**
 * "How clips are made" and "How images are made", each Auto (API links) or My own (Flow, Higgsfield…).
 * `imagesDisabled` greys the images' My own (manual images exist on the v2 pipeline only); `imagesNote` says why.
 */
export default function HowMadeControls({
  clipsOwn, imagesOwn, imagesDisabled = false, imagesNote = '', disabled = false, onClips, onImages,
}) {
  return (
    <>
      <Choice label="How clips are made" name="clips" own={clipsOwn} disabled={disabled} onChange={onClips}
        help={clipsOwn ? CLIPS_HELP.own : CLIPS_HELP.auto} />
      <Choice label="How images are made" name="images" own={imagesOwn} disabled={disabled}
        ownDisabled={imagesDisabled} onChange={onImages}
        help={imagesDisabled && imagesNote ? imagesNote : (imagesOwn ? IMAGES_HELP.own : IMAGES_HELP.auto)} />
    </>
  )
}

import { useEffect, useRef, useState } from 'react'
import {
  regenerateStory, fetchVoiceReference, uploadVoiceReference, deleteVoiceReference, fetchStoryMediaUrl,
} from '../../../api'
import { StepError } from '../fields'

// Plan 23 stage B4 (DEC-281): a character's own voice recording, cloned
// locally by chatterbox. The same limits as clipping.aistory.voice_reference.
const ACCEPTED_AUDIO_TYPES = 'audio/*,.wav,.mp3,.m4a,.ogg,.flac'
const REFERENCE_VOICE = { provider: 'chatterbox', voice_id: 'reference' }
const MEDIA_NAME = 'voice_reference.wav'

/**
 * The cast step's voice-recording slot of one character: an upload behind a
 * consent checkbox, a player, and "Use as voice (chatterbox)" -- disabled with
 * the engine probe's own reason while chatterbox is not installed.
 */
export default function VoiceReferenceSlot({ storyId, character, disabled, onChange }) {
  const reference = character.voice_reference || null
  const pinned = Boolean(character.voice
    && character.voice.provider === REFERENCE_VOICE.provider
    && character.voice.voice_id === REFERENCE_VOICE.voice_id)
  const [state, setState] = useState(null)
  const [consent, setConsent] = useState(false)
  const [busy, setBusy] = useState(false)
  const [playerUrl, setPlayerUrl] = useState(null)
  const playerRef = useRef(null)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetchVoiceReference(storyId, character.char_id)
      .then((fresh) => { if (!cancelled) setState(fresh) })
      .catch(() => { if (!cancelled) setState(null) })
    return () => { cancelled = true }
  }, [storyId, character.char_id, reference && reference.sha256, pinned])

  useEffect(() => {
    let cancelled = false
    if (playerRef.current) { URL.revokeObjectURL(playerRef.current); playerRef.current = null }
    setPlayerUrl(null)
    if (reference) {
      fetchStoryMediaUrl(storyId, 'characters', character.char_id, MEDIA_NAME).then((fresh) => {
        if (cancelled) { URL.revokeObjectURL(fresh); return }
        playerRef.current = fresh
        setPlayerUrl(fresh)
      }).catch(() => {})
    }
    return () => {
      cancelled = true
      if (playerRef.current) { URL.revokeObjectURL(playerRef.current); playerRef.current = null }
    }
  }, [storyId, character.char_id, reference && reference.sha256])

  const run = async (action) => {
    setBusy(true)
    setError('')
    setErrors(null)
    try {
      await action()
      onChange()
    } catch (err) {
      setError(err.message)
      setErrors(err.errors || null)
    } finally {
      setBusy(false)
    }
  }

  const handleFile = (e) => {
    const file = e.target.files && e.target.files[0]
    e.target.value = ''
    if (!file) return
    run(async () => {
      await uploadVoiceReference(storyId, character.char_id, file, consent)
      setConsent(false)
    })
  }

  const useAsVoice = () => run(() => regenerateStory(storyId, {
    target: `character:${character.char_id}:voice`,
    voice: REFERENCE_VOICE,
  }))

  const remove = () => run(() => deleteVoiceReference(storyId, character.char_id))

  const engine = state && state.engine
  const engineReady = Boolean(engine && engine.ready)
  const engineReason = engine && !engine.ready ? engine.reason : ''

  return (
    <div className="story-cast-voice-reference">
      <div className="story-field-label">Voice recording</div>
      <p className="form-hint">
        5 to 30 seconds of one voice speaking clearly. It is cloned on this server by chatterbox (local, free,
        slower than real time on a small CPU); nothing is sent to a provider.
      </p>
      {reference && (
        <div className="story-cast-voice-current">
          <span>{Number(reference.duration_s).toFixed(1)} s</span>
          {playerUrl && <audio controls src={playerUrl} aria-label={`${character.name} voice recording`} />}
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={remove}
            disabled={disabled || busy || pinned}
            title={pinned ? 'This recording is the voice: pick another voice first.' : 'Remove the recording'}
          >
            Remove
          </button>
        </div>
      )}
      <label className="form-hint">
        <input
          type="checkbox"
          checked={consent}
          onChange={(e) => setConsent(e.target.checked)}
          disabled={disabled || busy}
        />
        {' '}This is my voice, or I have the speaker's permission to use it.
      </label>
      <input
        type="file"
        aria-label={reference ? 'Replace the voice recording' : 'Add a voice recording'}
        accept={ACCEPTED_AUDIO_TYPES}
        onChange={handleFile}
        disabled={disabled || busy || !consent}
      />
      {reference && (
        <div className="story-step-actions">
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={useAsVoice}
            disabled={disabled || busy || pinned || !engineReady}
            title={engineReason || (pinned ? 'Already this character\'s voice.' : undefined)}
          >
            Use as voice (chatterbox)
          </button>
          {pinned && <span className="form-hint">This recording is {character.name}'s voice.</span>}
          {!pinned && engineReason && <span className="form-hint">{engineReason}</span>}
        </div>
      )}
      <StepError message={error} errors={errors} />
    </div>
  )
}

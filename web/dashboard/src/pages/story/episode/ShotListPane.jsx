import { useCallback, useEffect, useState } from 'react'
import { downloadShotBriefZip, fetchShotBrief, fetchShotImageUrl } from '../../../api'
import { Badge, Button, Card, CardBody, CardHeader, Spinner, useToast } from '../../../ui'
import { ArrowDownToLine, Clapperboard, Copy } from '../../../ui/icons'
import { useStoryMediaUrl } from '../EntityGallery'
import ManualUploadSlot from '../ManualUploadSlot'

// The episode's Shot list (plan 22 stage 5, the manual link): on a story
// whose clips are the user's own (`manual/upload`), per shot what it must
// show, the prompt to paste (with a copy button), the reference images (a
// thumbnail and a download each), the line and its voice, the length to
// pick, the checks, an upload slot and the shot's state -- read from the
// shot brief (GET /stories/{id}/episodes/{ep}/brief?platform=...). The
// platform select re-renders the prompts for Flow or Higgsfield; "Download
// brief (zip)" saves the .md, the .json and the references. An upload that
// leaves nothing missing starts the paused run again (the route does it).

export const PLATFORMS = [
  { id: 'flow', label: 'Google Flow (Veo 3.1)' },
  { id: 'higgsfield', label: 'Higgsfield / Freepik' },
]

// The shot's state as a badge: a clip to make, one uploaded, its take.
export const SHOT_STATES = {
  missing: { tone: 'warning', label: 'Missing' },
  uploaded: { tone: 'info', label: 'Uploaded' },
  take_ok: { tone: 'success', label: 'Take ok' },
  mismatch: { tone: 'danger', label: 'Take mismatch' },
  approximate: { tone: 'neutral', label: 'Approximate' },
}

const PLATFORM_KEY = 'aistory.shotList.platform'

function readPlatform() {
  try {
    const value = window.localStorage.getItem(PLATFORM_KEY)
    return PLATFORMS.some((item) => item.id === value) ? value : 'flow'
  } catch {
    return 'flow'
  }
}

/** "7 of 12 clips uploaded". */
export function progressLine(counts) {
  if (!counts) return ''
  return `${counts.uploaded} of ${counts.total} clip${counts.total === 1 ? '' : 's'} uploaded`
}

function RefThumb({ storyId, ep, reference }) {
  const parts = (reference.path || '').split('/')
  const entity = parts[0] !== 'episodes'
  const mediaUrl = useStoryMediaUrl(storyId, entity ? parts[0] : null, entity ? parts[1] : null,
    entity ? reference.name : null, { thumb: true })
  const [shotUrl, setShotUrl] = useState(null)
  useEffect(() => {
    if (entity) return undefined
    let cancelled = false
    let current = null
    fetchShotImageUrl(storyId, ep, reference.name).then((fresh) => {
      if (cancelled) { URL.revokeObjectURL(fresh); return }
      current = fresh
      setShotUrl(fresh)
    }).catch(() => {})
    return () => {
      cancelled = true
      if (current) URL.revokeObjectURL(current)
    }
  }, [storyId, ep, reference.name, entity])
  const url = entity ? mediaUrl : shotUrl
  return (
    <figure className="shot-list-ref">
      {url ? <img src={url} alt={reference.label} loading="lazy" /> : <span className="shot-list-ref-empty" />}
      <figcaption>
        {reference.number}. {reference.label}
        {url && <a href={url} download={reference.file} className="shot-list-ref-download">download</a>}
      </figcaption>
    </figure>
  )
}

function ShotRow({ storyId, ep, entry, disabled, keyframes, onUploaded }) {
  const toast = useToast()
  const state = SHOT_STATES[entry.state] || SHOT_STATES.missing
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(entry.prompt)
      toast.success(`Shot ${entry.shot_id}'s prompt copied.`)
    } catch {
      toast.error('The prompt could not be copied: select it and copy it by hand.')
    }
  }
  return (
    <li className="shot-list-row" id={`shot-list-${entry.shot_id}`}>
      <div className="shot-list-row-head">
        <strong>{entry.order}. {entry.shot_id}</strong>
        <Badge tone={entry.speaks ? 'accent' : 'neutral'}>{entry.speaks ? 'Speaks' : 'Silent'}</Badge>
        <Badge tone={state.tone} dot>{state.label}</Badge>
        <span className="form-hint">Pick {entry.length_s} s (planned {entry.clip_s} s) · {entry.aspect} · {entry.model_label}</span>
      </div>
      <p className="shot-list-purpose">{entry.purpose}</p>
      {entry.line && (
        <p className="shot-list-line">
          <strong>{entry.speaker}:</strong> “{entry.line}” <span className="form-hint">— {entry.voice_line}</span>
        </p>
      )}
      <div className="shot-list-prompt">
        <pre>{entry.prompt}</pre>
        <Button size="sm" icon={Copy} onClick={copy}>Copy prompt</Button>
      </div>
      {entry.negative_prompt && (
        <details className="shot-list-negative">
          <summary>Negative prompt</summary>
          <pre>{entry.negative_prompt}</pre>
        </details>
      )}
      <p className="form-hint">{entry.mode}</p>
      {entry.references.length > 0 && (
        <div className="shot-list-refs">
          {entry.references.map((reference) => (
            <RefThumb key={reference.file} storyId={storyId} ep={ep} reference={reference} />
          ))}
        </div>
      )}
      <ul className="shot-list-checks">
        {entry.checks.map((check) => <li key={check}>{check}</li>)}
      </ul>
      {entry.take && entry.take.heard && (
        <p className="form-hint">Heard: “{entry.take.heard}”{entry.take.matched != null
          ? ` (${Math.round(entry.take.matched * 100)} % of the line)` : ''}</p>
      )}
      <div className="shot-list-uploads">
        {keyframes && (
          <ManualUploadSlot
            slot={`/api/stories/${storyId}/episodes/${ep}/shots/${entry.shot_id}/keyframe`}
            label={entry.references.some((ref) => ref.kind === 'keyframe') ? 'Replace keyframe' : 'Upload keyframe'}
            accept="image/png,image/jpeg,image/webp"
            disabled={disabled}
            onDone={onUploaded}
          />
        )}
        <ManualUploadSlot
          slot={entry.upload_slot}
          label={entry.state === 'missing' ? 'Upload clip' : 'Replace clip'}
          disabled={disabled}
          onDone={onUploaded}
        />
      </div>
    </li>
  )
}

/**
 * The Shot list pane. `pausedJob` is the episode's job paused awaiting the
 * clips (status `awaiting_uploads`), if any: its sentence heads the pane.
 */
export default function ShotListPane({ storyId, ep, inFlightJob, pausedJob, keyframes = false, onChange }) {
  const toast = useToast()
  const [platform, setPlatform] = useState(readPlatform)
  const [brief, setBrief] = useState(null)
  const [error, setError] = useState('')
  const [downloading, setDownloading] = useState(false)

  const load = useCallback(() => {
    fetchShotBrief(storyId, ep, platform)
      .then((body) => { setBrief(body); setError('') })
      .catch((err) => { setBrief(null); setError(err.message) })
  }, [storyId, ep, platform])

  useEffect(() => { load() }, [load])

  const choose = (value) => {
    setPlatform(value)
    try {
      window.localStorage.setItem(PLATFORM_KEY, value)
    } catch {
      // A private window: the choice lasts this visit only.
    }
  }

  const download = async () => {
    setDownloading(true)
    try {
      await downloadShotBriefZip(storyId, ep, platform)
    } catch (err) {
      toast.error(err.message)
    } finally {
      setDownloading(false)
    }
  }

  const onUploaded = (result) => {
    if (result && result.resumed && result.resumed.job_id) {
      toast.success('Every clip is uploaded: the paused run goes on.')
    }
    load()
    onChange()
  }

  const busy = Boolean(inFlightJob)
  return (
    <Card id="episode-shot-list" className="shot-list">
      <CardHeader
        icon={Clapperboard}
        title="Shot list"
        subtitle={brief ? `${progressLine(brief.counts)} · ${brief.credits}` : 'Your own clips, shot by shot'}
        actions={(
          <>
            <label className="shot-list-platform">
              <span className="form-hint">Platform</span>
              <select value={platform} onChange={(event) => choose(event.target.value)}>
                {PLATFORMS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </select>
            </label>
            <Button size="sm" icon={ArrowDownToLine} loading={downloading} onClick={download}>
              Download brief (zip)
            </Button>
          </>
        )}
      />
      <CardBody>
        {pausedJob && pausedJob.uploads && (
          <p className="chip chip-warn chip-wrap">{pausedJob.uploads.message} — the run goes on by itself once every
            clip is uploaded.</p>
        )}
        {brief && brief.waiting && !pausedJob && <p className="form-hint">{brief.waiting}.</p>}
        {brief && <p className="form-hint">{brief.platform.where_to_paste}</p>}
        {error && <p className="story-error">{error}</p>}
        {!brief && !error && <Spinner label="Loading the shot list" />}
        {brief && (
          <ol className="shot-list-rows">
            {brief.shots.map((entry) => (
              <ShotRow key={entry.shot_id} storyId={storyId} ep={ep} entry={entry} disabled={busy}
                keyframes={keyframes} onUploaded={onUploaded} />
            ))}
          </ol>
        )}
      </CardBody>
    </Card>
  )
}

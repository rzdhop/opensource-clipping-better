import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fetchEpisode, fetchStory } from '../../api'
import { LiveActivity, useJobFeed } from '../../components/ActivityFeed'
import Tabs from '../../components/Tabs'
import ScriptPane from './episode/ScriptPane'
import StoryboardPane from './episode/StoryboardPane'

const TABS = [
  { id: 'script', label: 'Script' },
  { id: 'storyboard', label: 'Storyboard' },
  { id: 'preview', label: 'Preview' },
]

const IN_FLIGHT = ['queued', 'running']
const EPISODE_POLL_MS = 4000
const WIDE_BREAKPOINT = 1100

function tabFromHash() {
  if (typeof window === 'undefined') return 'script'
  const id = window.location.hash.slice(1)
  return TABS.some((tab) => tab.id === id) ? id : 'script'
}

/** Three panes side by side at >=1100px; Tabs below that -- tracked with
 * matchMedia (not CSS alone) so only one layout is ever mounted: a pane
 * hidden by CSS would still hold a live job feed and poll the episode. */
function useIsWide(breakpoint) {
  const [wide, setWide] = useState(() => (
    typeof window !== 'undefined' ? window.innerWidth >= breakpoint : true
  ))
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mq = window.matchMedia(`(min-width: ${breakpoint}px)`)
    const onChange = () => setWide(mq.matches)
    onChange()
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [breakpoint])
  return wide
}

function PreviewPane() {
  return (
    <div className="story-step-body">
      <div className="card">
        <h3 className="card-title">Preview</h3>
        <p>Rendering arrives in phase 4.</p>
      </div>
    </div>
  )
}

export default function EpisodeStudio() {
  const { storyId, ep } = useParams()
  const epNumber = Number(ep)
  const [story, setStory] = useState(null)
  const [episode, setEpisode] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [tab, setTab] = useState(tabFromHash)
  const wide = useIsWide(WIDE_BREAKPOINT)

  const refresh = useCallback(async () => {
    try {
      const [storyData, episodeData] = await Promise.all([fetchStory(storyId), fetchEpisode(storyId, ep)])
      setStory(storyData)
      setEpisode(episodeData)
      setLoadError('')
    } catch (err) {
      setLoadError(err.message)
    } finally {
      setLoading(false)
    }
  }, [storyId, ep])

  useEffect(() => {
    setLoading(true)
    setEpisode(null)
    refresh()
  }, [storyId, ep, refresh])

  const onTabChange = (id) => {
    setTab(id)
    if (typeof window !== 'undefined') window.location.hash = id
  }

  // While a job of this episode's documents is queued or running, poll the
  // episode page -- same reasoning as the wizard (ActivityFeed's stream can
  // miss the moment a step finishes).
  const inFlightJob = episode ? (episode.jobs || []).find((j) => IN_FLIGHT.includes(j.status)) || null : null
  useEffect(() => {
    if (!inFlightJob) return
    const timer = setInterval(refresh, EPISODE_POLL_MS)
    return () => clearInterval(timer)
  }, [inFlightJob, refresh])

  const { job: liveJob, events, streamState } = useJobFeed(inFlightJob ? inFlightJob.id : null, {
    onJob: (job) => {
      if (job && job.status !== 'queued' && job.status !== 'running') refresh()
    },
  })

  if (loading) {
    return <div className="fade-in"><div className="empty-state"><span className="spinner"></span></div></div>
  }
  if (loadError) {
    return <div className="fade-in"><div className="card"><p className="story-error">{loadError}</p></div></div>
  }
  if (!story || !episode) return null

  const arcEntry = ((story.season && story.season.arc) || []).find((entry) => entry.ep === epNumber)

  const panes = {
    script: (
      <ScriptPane
        episode={episode}
        storyDoc={story.story}
        characters={story.characters}
        places={story.places}
        episodes={story.episodes}
        storyId={storyId}
        ep={epNumber}
        inFlightJob={inFlightJob}
        onChange={refresh}
      />
    ),
    storyboard: (
      <StoryboardPane
        episode={episode}
        characters={story.characters}
        places={story.places}
        props={story.props}
        storyId={storyId}
        ep={epNumber}
        inFlightJob={inFlightJob}
        onChange={refresh}
      />
    ),
    preview: <PreviewPane />,
  }

  return (
    <div className="fade-in episode-studio">
      <div className="page-header episode-studio-header">
        <div>
          <Link to={`/story/${storyId}`} className="episode-studio-back">← {story.story.title || 'Untitled story'}</Link>
          <h2>Episode {ep}</h2>
          {arcEntry && <p>{arcEntry.summary}</p>}
        </div>
      </div>

      {inFlightJob && liveJob && (
        liveJob.status === 'queued'
          ? <p className="form-hint">queued — waiting for the worker</p>
          : <LiveActivity job={liveJob} events={events} streamState={streamState} />
      )}

      {wide ? (
        <div className="episode-studio-panes">
          <div className="episode-studio-pane">{panes.script}</div>
          <div className="episode-studio-pane">{panes.storyboard}</div>
          <div className="episode-studio-pane">{panes.preview}</div>
        </div>
      ) : (
        <div className="episode-studio-tabs">
          <Tabs tabs={TABS} active={tab} onChange={onTabChange}>
            {(id) => panes[id]}
          </Tabs>
        </div>
      )}
    </div>
  )
}

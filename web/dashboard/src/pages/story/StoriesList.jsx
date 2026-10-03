import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { fetchStories, deleteStory, fetchStyles, styleNameOf, fetchStoryCoverUrl } from '../../api'
import { Button, Chip, EmptyState, Menu, Skeleton, useConfirm } from '../../ui'
import { ArrowRight, BookOpen, Clapperboard, Sparkles, Trash2 } from '../../ui/icons'
import { formatDateTime, formatRelativeTime } from '../../lib/format'

// The story steps' names, by the step ids the list's `progress.next` carries
// (NewStoryWizard's STEPS keys).
const STEP_LABELS = {
  concepts: 'Concepts',
  bible: 'Bible',
  style: 'Style',
  cast: 'Cast',
  places: 'Places & props',
  season: 'Season',
  knowledge: 'Knowledge base',
}

// An episode's furthest point (`episodes.latest.state`, workflow.LIST_EPISODE_STATES).
const EPISODE_STATE_LABELS = {
  draft: 'Draft',
  written: 'Script approved',
  planned: 'Storyboard approved',
  assets: 'Assets approved',
  rendered: 'Rendered',
}

const SKELETON_CARDS = 6

function countLabel(n) {
  return `${n} ${n === 1 ? 'story' : 'stories'}`
}

/** The style's palette as a gradient, for a story with no portrait yet. */
function swatchBackground(styles, templateId) {
  const style = (styles || []).find((s) => s.template_id === templateId)
  const colours = (style && style.palette && style.palette.primary) || []
  if (colours.length >= 2) return `linear-gradient(135deg, ${colours.join(', ')})`
  if (colours.length === 1) return colours[0]
  return 'linear-gradient(135deg, var(--accent-dim), var(--bg-input))'
}

/**
 * The card's cover: the first lead's portrait (fetched with the auth header,
 * as a blob URL revoked on unmount), else the style's palette with the
 * title's initial.
 */
function StoryCover({ story, styles }) {
  const [url, setUrl] = useState(null)

  useEffect(() => {
    if (!story.cover) return undefined
    let cancelled = false
    let current = null
    fetchStoryCoverUrl(story.cover).then((fresh) => {
      if (cancelled) { URL.revokeObjectURL(fresh); return }
      current = fresh
      setUrl(fresh)
    }).catch(() => {})
    return () => {
      cancelled = true
      if (current) URL.revokeObjectURL(current)
      setUrl(null)
    }
  }, [story.cover])

  const initial = (story.title || 'Untitled story').trim().charAt(0).toUpperCase()
  return (
    <div className="story-list-cover" style={url ? undefined : { background: swatchBackground(styles, story.style_template_id) }}>
      {url ? (
        <img src={url} alt="" className="story-list-cover-img" />
      ) : (
        <span className="story-list-cover-initial" aria-hidden="true">{initial}</span>
      )}
    </div>
  )
}

/** Where the story stands: its steps done and the next one, or "Ready" and its latest episode. */
function ProgressRibbon({ progress, episodes }) {
  const total = (progress && progress.steps_total) || 0
  if (!total) {
    return <div className="story-list-progress story-list-progress-unknown">Progress unavailable</div>
  }
  const done = Math.min(progress.steps_done || 0, total)
  const ready = done >= total
  const latest = episodes && episodes.latest
  const count = (episodes && episodes.count) || 0
  let text
  if (!ready) {
    text = `${done} of ${total} steps · Next: ${STEP_LABELS[progress.next] || progress.next}`
  } else if (latest) {
    text = `Episode ${latest.ep} · ${EPISODE_STATE_LABELS[latest.state] || latest.state}`
  } else {
    text = 'No episode yet'
  }
  return (
    <div className={`story-list-progress${ready ? ' story-list-progress-ready' : ''}`}>
      <div className="story-list-progress-row">
        <span className="story-list-progress-label">
          {ready && <span className="story-list-progress-ready-word">Ready</span>}
          <span>{text}</span>
        </span>
        {count > 0 && <span className="story-list-progress-count">{count} ep.</span>}
      </div>
      <div
        className="story-list-progress-track"
        role="progressbar"
        aria-label="Story steps done"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        aria-valuetext={`${done} of ${total} steps done`}
      >
        <span className="story-list-progress-fill" style={{ width: `${(done / total) * 100}%` }} />
      </div>
    </div>
  )
}

function StoryCardSkeleton() {
  return (
    <div className="story-list-card story-list-card-skeleton" aria-hidden="true">
      <span className="ui-skeleton story-list-cover" />
      <div className="story-list-body">
        <Skeleton lines={3} />
      </div>
    </div>
  )
}

export default function StoriesList() {
  const [stories, setStories] = useState(null)
  const [styles, setStyles] = useState([])
  const [error, setError] = useState('')
  const [deletingId, setDeletingId] = useState(null)
  const [deleteErrors, setDeleteErrors] = useState({})
  const confirm = useConfirm()
  const navigate = useNavigate()

  const load = () => {
    fetchStories().then((data) => setStories(data.stories || [])).catch((err) => setError(err.message))
  }

  useEffect(() => {
    load()
    fetchStyles().then((data) => setStyles(data.styles || [])).catch(() => {})
  }, [])

  const handleDelete = async (storyId, title) => {
    const confirmed = await confirm({
      title: `Delete "${title || 'Untitled story'}"?`,
      message: 'This cannot be undone.',
      confirmLabel: 'Delete',
      tone: 'danger',
    })
    if (!confirmed) return
    setDeletingId(storyId)
    setDeleteErrors((prev) => ({ ...prev, [storyId]: '' }))
    try {
      await deleteStory(storyId)
      load()
    } catch (err) {
      setDeleteErrors((prev) => ({ ...prev, [storyId]: err.message }))
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="fade-in">
      <div className="page-header">
        <div>
          <h2>AI Story</h2>
          <p>
            {stories && stories.length > 0 && <span className="story-list-count">{countLabel(stories.length)} · </span>}
            Persistent story workspaces — a world, a style lock and a season arc.
          </p>
        </div>
        <Button as={Link} to="/story/new" variant="primary" icon={Sparkles}>New story</Button>
      </div>

      {error && <p className="story-error">{error}</p>}

      {stories === null ? (
        !error && (
          <div className="story-list-grid" aria-busy="true" aria-label="Loading stories">
            {Array.from({ length: SKELETON_CARDS }, (_, i) => <StoryCardSkeleton key={i} />)}
          </div>
        )
      ) : stories.length === 0 ? (
        <EmptyState
          icon={BookOpen}
          title="No stories yet"
          text={
            'Start a persistent story workspace — a world, a cast and a style lock that ' +
            'stays consistent across every episode.'
          }
          action={<Button as={Link} to="/story/new" variant="primary" icon={Sparkles}>New story</Button>}
        />
      ) : (
        <div className="story-list-grid">
          {stories.map((story) => {
            const title = story.title || 'Untitled story'
            const href = `/story/${story.story_id}`
            const styleLabel = story.style_label || styleNameOf(styles, story.style_template_id)
            const deleting = deletingId === story.story_id
            return (
              <article
                key={story.story_id}
                className={`story-list-card${deleting ? ' story-list-card-busy' : ''}`}
                aria-busy={deleting || undefined}
              >
                <StoryCover story={story} styles={styles} />
                <div className="story-list-body">
                  <div className="story-list-title-row">
                    <h3 className="story-list-title">
                      {/* The title's link covers the whole card (::after). */}
                      <Link to={href} className="story-list-link">{title}</Link>
                    </h3>
                    <Menu
                      label={`Actions for ${title}`}
                      className="story-list-menu"
                      loading={deleting}
                      items={[
                        { label: 'Open', icon: ArrowRight, onSelect: () => navigate(href) },
                        {
                          label: 'Delete…',
                          icon: Trash2,
                          tone: 'danger',
                          onSelect: () => handleDelete(story.story_id, story.title),
                        },
                      ]}
                    />
                  </div>
                  <div className="story-list-chips">
                    <Chip>{story.language === 'fr' ? 'FR' : 'EN'}</Chip>
                    {styleLabel && <Chip>{styleLabel}</Chip>}
                    {story.pipeline === 'v2' && <Chip tone="accent" icon={Clapperboard}>Animated</Chip>}
                  </div>
                  <ProgressRibbon progress={story.progress} episodes={story.episodes} />
                  <div className="story-list-updated" title={formatDateTime(story.updated_at)}>
                    Updated {formatRelativeTime(story.updated_at)}
                  </div>
                  {deleteErrors[story.story_id] && <p className="story-error">{deleteErrors[story.story_id]}</p>}
                </div>
              </article>
            )
          })}
        </div>
      )}
    </div>
  )
}

import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchStories, deleteStory, fetchStyles, styleNameOf } from '../../api'

const STATUS_LABELS = {
  draft: 'Draft',
  concept_chosen: 'Concept chosen',
  bible_approved: 'Bible approved',
  style_approved: 'Style approved',
  cast_approved: 'Cast approved',
  places_approved: 'Places approved',
  ready: 'Ready',
}

function formatUpdated(value) {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

export default function StoriesList() {
  const [stories, setStories] = useState(null)
  const [styles, setStyles] = useState([])
  const [error, setError] = useState('')
  const [deletingId, setDeletingId] = useState(null)
  const [deleteErrors, setDeleteErrors] = useState({})

  const load = () => {
    fetchStories().then((data) => setStories(data.stories || [])).catch((err) => setError(err.message))
  }

  useEffect(() => {
    load()
    fetchStyles().then((data) => setStyles(data.styles || [])).catch(() => {})
  }, [])

  const handleDelete = async (storyId, title) => {
    if (!window.confirm(`Delete "${title || 'Untitled story'}"?\n\nThis cannot be undone.`)) return
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
          <p>Persistent story workspaces — a world, a style lock and a season arc.</p>
        </div>
        <Link to="/story/new" className="btn btn-primary">✨ New story</Link>
      </div>

      {error && <p className="story-error">{error}</p>}

      {stories === null ? (
        <div className="empty-state"><span className="spinner"></span></div>
      ) : stories.length === 0 ? (
        <div className="empty-state">
          <div className="icon">📖</div>
          <h3>No stories yet</h3>
          <p>
            Start a persistent story workspace — a world, a cast and a style lock that
            stays consistent across every episode.
          </p>
          <Link to="/story/new" className="btn btn-primary">✨ New story</Link>
        </div>
      ) : (
        <div className="story-grid">
          {stories.map((story) => (
            <div key={story.story_id} className="card story-card">
              <Link to={`/story/${story.story_id}`} className="story-card-link">
                <div className="story-card-header">
                  <h3>{story.title || 'Untitled story'}</h3>
                </div>
                <div className="story-card-meta">
                  <span className="chip">{STATUS_LABELS[story.status] || story.status}</span>
                  {story.style_template_id && <span className="chip">{styleNameOf(styles, story.style_template_id)}</span>}
                  <span className="chip">{story.language === 'fr' ? 'FR' : 'EN'}</span>
                </div>
                <div className="story-card-updated">Updated {formatUpdated(story.updated_at)}</div>
              </Link>
              {deleteErrors[story.story_id] && <p className="story-error">{deleteErrors[story.story_id]}</p>}
              <div className="story-card-actions">
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  disabled={deletingId === story.story_id}
                  onClick={() => handleDelete(story.story_id, story.title)}
                >
                  {deletingId === story.story_id ? 'Deleting…' : 'Delete'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

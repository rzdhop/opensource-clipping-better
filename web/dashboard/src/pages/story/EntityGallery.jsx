import { Fragment, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { fetchStoryMediaUrl } from '../../api'
import { IconButton } from '../../ui'
import { ImageIcon, X } from '../../ui/icons'

// The Cast and Places steps' card grids (dashboard overhaul stage 3,
// DEC-255): one tile per character, place or prop; a click opens that
// entity's full editor -- the step's own card, unchanged -- in place, under
// the tile's row, one at a time. The open entity is the URL's #hash
// (`/story/:id/cast#char_x`), so it can be linked to and survives a reload.

/**
 * One entity image as a blob URL (the media route is token-gated, so it is
 * fetched with the auth header, DEC-113), revoked when the name changes or
 * the tile unmounts. Null while loading, with no name, or on a failure.
 * `thumb` asks for the route's cached 160 px thumbnail (DEC-257).
 */
export function useStoryMediaUrl(storyId, kind, eid, name, { thumb = false } = {}) {
  const [url, setUrl] = useState(null)
  useEffect(() => {
    if (!name) return undefined
    let cancelled = false
    let current = null
    fetchStoryMediaUrl(storyId, kind, eid, name, { thumb }).then((fresh) => {
      if (cancelled) { URL.revokeObjectURL(fresh); return }
      current = fresh
      setUrl(fresh)
    }).catch(() => {})
    return () => {
      cancelled = true
      if (current) URL.revokeObjectURL(current)
      setUrl(null)
    }
  }, [storyId, kind, eid, name, thumb])
  return url
}

/**
 * The entity the URL's hash names, when it is one of *ids*, and a toggle
 * that opens one (closing any other) or closes it. The hash is replaced,
 * not pushed: opening cards does not fill the back button's history.
 */
export function useHashAccordion(ids) {
  const location = useLocation()
  const navigate = useNavigate()
  const hashId = decodeURIComponent((location.hash || '').replace(/^#/, ''))
  const openId = ids.includes(hashId) ? hashId : null
  const toggle = (id) => {
    navigate({ pathname: location.pathname, search: location.search, hash: openId === id ? '' : id },
      { replace: true })
  }
  return [openId, toggle]
}

/** The number of columns a CSS grid lays out right now, followed through resizes. */
function useGridColumns(ref) {
  const [columns, setColumns] = useState(1)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const measure = () => {
      const tracks = window.getComputedStyle(el).gridTemplateColumns.split(' ').filter(Boolean).length
      setColumns(Math.max(1, tracks))
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(measure)
    observer.observe(el)
    return () => observer.disconnect()
  }, [ref])
  return columns
}

function Tile({ storyId, item, open, editorId, tileId, onToggle }) {
  const thumb = item.thumb || {}
  const url = useStoryMediaUrl(storyId, thumb.kind, thumb.eid, thumb.name, { thumb: true })
  return (
    <button
      type="button"
      id={tileId}
      className={`story-entity-tile${open ? ' story-entity-tile-open' : ''}`}
      aria-expanded={open}
      aria-controls={editorId}
      onClick={() => onToggle(item.id)}
    >
      <span className={`story-entity-thumb story-entity-thumb-${item.shape || 'portrait'}`}>
        {url
          ? <img src={url} alt="" />
          : <span className="story-entity-thumb-empty"><ImageIcon size={22} aria-hidden="true" />{item.thumbEmpty}</span>}
      </span>
      <span className="story-entity-tile-body">
        <span className="story-entity-tile-name">{item.name}</span>
        {item.meta && <span className="story-entity-tile-meta">{item.meta}</span>}
      </span>
    </button>
  )
}

/**
 * *items*: [{id, name, thumb: {kind, eid, name} | null, thumbEmpty, shape
 * ('portrait' 4:5, 'wide' 16:9, 'square'), meta (badges)}]. *openId* and
 * *onToggle* come from useHashAccordion; *renderEditor(item)* is the
 * entity's full editor. The editor is placed after the last tile of the open
 * tile's row, so the grid keeps its rows and the reading order is the
 * visual order.
 */
export default function EntityGallery({ storyId, items, openId, onToggle, renderEditor, label, size = 'md' }) {
  const gridRef = useRef(null)
  const editorRef = useRef(null)
  const columns = useGridColumns(gridRef)
  const openIndex = items.findIndex((item) => item.id === openId)
  const rowEnd = openIndex < 0 ? -1 : Math.min(items.length - 1, Math.floor(openIndex / columns) * columns + columns - 1)

  // A #id deep link brings its tile (and the editor under it) into view once;
  // a click opens the editor under a tile that is already on screen.
  const deepLinked = useRef(false)
  useEffect(() => {
    if (deepLinked.current) return
    deepLinked.current = true
    if (openIndex < 0) return
    const tile = document.getElementById(`entity-tile-${openId}`)
    const target = tile || editorRef.current
    if (target) target.scrollIntoView({ block: 'start' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const openItem = openIndex >= 0 ? items[openIndex] : null

  return (
    <div className={`story-entity-grid story-entity-grid-${size}`} ref={gridRef} role="group" aria-label={label}>
      {items.map((item, index) => {
        const open = item.id === openId
        return (
          <Fragment key={item.id}>
            <div className="story-entity-cell">
              <Tile
                storyId={storyId}
                item={item}
                open={open}
                tileId={`entity-tile-${item.id}`}
                editorId={`entity-editor-${item.id}`}
                onToggle={onToggle}
              />
            </div>
            {openItem && index === rowEnd && (
              <div
                role="region"
                id={`entity-editor-${openItem.id}`}
                aria-labelledby={`entity-tile-${openItem.id}`}
                className="story-entity-editor"
                ref={editorRef}
              >
                <IconButton
                  icon={X}
                  size="sm"
                  aria-label={`Close ${openItem.name}`}
                  className="story-entity-editor-close"
                  onClick={() => onToggle(openItem.id)}
                />
                {renderEditor(openItem)}
              </div>
            )}
          </Fragment>
        )
      })}
    </div>
  )
}

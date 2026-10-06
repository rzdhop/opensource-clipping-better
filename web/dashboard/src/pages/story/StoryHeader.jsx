import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchStoryCoverUrl, styleNameOf } from '../../api'
import { Button, Chip, Spinner } from '../../ui'
import { ArrowRight, ChevronDown, Film, SlidersHorizontal } from '../../ui/icons'
import GenerationProfileCard, { pricedEpisode } from './GenerationProfileCard'
import { jobLabel, stepLabel, stepOfJob, stepsFor } from './storySteps'

/**
 * Plan 28 stage S2 (DEC-305 section 9): who makes the clips, in words -- the summary the header shows
 * instead of the tier, the route and the budget profile.
 */
export function howMadeSummary(profile) {
  if (profile.budget_profile === 'native_speech_manual') return 'Your own clips'
  const animated = profile.tier >= 2
  if (animated && profile.budget_profile === 'one_dollar') return 'Key shots animated'
  if (animated && (profile.budget_profile === 'quality' || profile.budget_profile === 'native_speech')) return 'App-made clips'
  return 'Pictures with motion'
}

/** The style's palette as a gradient, for a story with no portrait yet (as the stories list does). */
function swatchBackground(styles, templateId) {
  const style = (styles || []).find((s) => s.template_id === templateId)
  const colours = (style && style.palette && style.palette.primary) || []
  if (colours.length >= 2) return `linear-gradient(135deg, ${colours.join(', ')})`
  if (colours.length === 1) return colours[0]
  return 'linear-gradient(135deg, var(--accent-dim), var(--bg-input))'
}

/**
 * The cover thumbnail: the first character's portrait in cast order (the
 * payload lists them so; the stories list's cover rule, workflow.list_cover)
 * fetched as a blob through `fetchStoryCoverUrl`, else the style's palette
 * with the title's initial.
 */
function HeaderCover({ storyId, story, characters, styles }) {
  const lead = (characters || []).find((c) => c.refs && c.refs.portrait)
  const cover = lead ? `/stories/${storyId}/media/characters/${lead.char_id}/${encodeURIComponent(lead.refs.portrait.name)}` : null
  const [url, setUrl] = useState(null)

  useEffect(() => {
    if (!cover) return undefined
    let cancelled = false
    let current = null
    fetchStoryCoverUrl(cover).then((fresh) => {
      if (cancelled) { URL.revokeObjectURL(fresh); return }
      current = fresh
      setUrl(fresh)
    }).catch(() => {})
    return () => {
      cancelled = true
      if (current) URL.revokeObjectURL(current)
      setUrl(null)
    }
  }, [cover])

  const initial = (story.title || 'Untitled story').trim().charAt(0).toUpperCase()
  return (
    <div className="story-header-cover" style={url ? undefined : { background: swatchBackground(styles, story.style_template_id) }}>
      {url ? <img src={url} alt="" /> : <span aria-hidden="true">{initial}</span>}
    </div>
  )
}

/**
 * The "How it's made" card behind a header button: a non-modal popover. It stays
 * mounted while closed, so a refused switch's "Regenerate"
 * offer and the per-route estimates survive closing it. Escape or a click
 * outside closes it; focus goes back to the button.
 */
function VisualTierPopover({ storyId, story, nextEp, onChange }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef(null)
  const buttonRef = useRef(null)
  const panelRef = useRef(null)
  const panelId = useId()
  const profile = story.generation_profile

  useEffect(() => {
    if (!open) return undefined
    if (panelRef.current) panelRef.current.focus()
    const onPointerDown = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  const onKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      setOpen(false)
      if (buttonRef.current) buttonRef.current.focus()
    }
  }

  const summary = howMadeSummary(profile)

  return (
    <div className="story-tier" ref={rootRef} onKeyDown={onKeyDown}>
      <Button
        ref={buttonRef}
        size="sm"
        icon={SlidersHorizontal}
        iconRight={ChevronDown}
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
        title="How this story is made: who makes the clips and the images"
      >
        <span className="story-tier-label">How it's made</span>
        <span className="story-tier-summary">{summary}</span>
      </Button>
      <div
        id={panelId}
        ref={panelRef}
        className="story-tier-panel"
        role="dialog"
        aria-label="How this story is made"
        tabIndex={-1}
        hidden={!open}
      >
        <GenerationProfileCard storyId={storyId} story={story} nextEp={nextEp} onChange={onChange} />
      </div>
    </div>
  )
}

/**
 * The header's height, followed through resizes, as `--story-header-h` on
 * the root: the step rail sticks under it and a deep-linked tile scrolls to
 * below it. A long title or wrapped actions make the header two lines tall
 * between ~900 and 1100 px, where a fixed offset let the rail touch it.
 */
function useHeaderHeightVar(ref) {
  useEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const root = document.documentElement
    const write = () => root.style.setProperty('--story-header-h', `${Math.ceil(el.getBoundingClientRect().height)}px`)
    write()
    if (typeof ResizeObserver === 'undefined') {
      return () => root.style.removeProperty('--story-header-h')
    }
    const observer = new ResizeObserver(write)
    observer.observe(el)
    return () => {
      observer.disconnect()
      root.style.removeProperty('--story-header-h')
    }
  }, [ref])
}

/**
 * The workspace's sticky header: the cover, the title, the story's chips,
 * the "How it's made" popover and the one primary action -- the episode page once
 * the story is ready, else the step to do next.
 */
export default function StoryHeader({ storyId, data, styles, allDone, currentKey, activeKey, inFlightJob, onChange }) {
  const { story } = data
  const episodes = data.episodes || []
  const isV2 = story.generation_profile.pipeline === 'v2'
  const latest = episodes.length > 0 ? episodes[episodes.length - 1] : null
  const runningStep = stepOfJob(inFlightJob)
  const headerRef = useRef(null)
  useHeaderHeightVar(headerRef)

  let action = null
  if (allDone) {
    action = latest ? (
      <Button as={Link} to={`/story/${storyId}/episodes/${latest.ep}`} variant="primary" size="sm" icon={Film}>
        Open episode {latest.ep}
      </Button>
    ) : (
      <Button as={Link} to={`/story/${storyId}/episodes/1`} variant="primary" size="sm" icon={Film}>
        Generate episode
      </Button>
    )
  } else if (currentKey !== activeKey) {
    action = (
      <Button as={Link} to={`/story/${storyId}/${currentKey}`} variant="primary" size="sm" iconRight={ArrowRight}>
        {stepLabel(currentKey)}
      </Button>
    )
  } else {
    // Already on the step to do: where it stands instead of a link to this page.
    const steps = stepsFor(story)
    const index = steps.findIndex((s) => s.key === currentKey)
    action = <Chip tone="accent">{`Step ${index + 1} of ${steps.length} · ${stepLabel(currentKey)}`}</Chip>
  }

  return (
    <header className="story-header" ref={headerRef}>
      <HeaderCover storyId={storyId} story={story} characters={data.characters} styles={styles} />
      <div className="story-header-text">
        <h2 className="story-header-title">{story.title || 'Untitled story'}</h2>
        <div className="story-header-chips">
          <Chip>{story.language === 'fr' ? 'Français' : 'English'}</Chip>
          {story.style_template_id && <Chip>{styleNameOf(styles, story.style_template_id)}</Chip>}
          {isV2 && <Chip tone="accent">Animated</Chip>}
          <Chip>{howMadeSummary(story.generation_profile)}</Chip>
          {inFlightJob && !runningStep && (
            <Chip tone="warning">
              <Spinner size={12} />
              {jobLabel(inFlightJob)} {inFlightJob.status === 'queued' ? 'waiting' : 'running'}
            </Chip>
          )}
        </div>
      </div>
      <div className="story-header-actions">
        <VisualTierPopover storyId={storyId} story={story} nextEp={pricedEpisode(episodes)} onChange={onChange} />
        {action}
      </div>
    </header>
  )
}

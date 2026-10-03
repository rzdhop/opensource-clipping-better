import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchStoryCoverUrl, styleNameOf } from '../../api'
import { Button, Chip, Spinner } from '../../ui'
import { ArrowRight, ChevronDown, Film, SlidersHorizontal } from '../../ui/icons'
import GenerationProfileCard, { pricedEpisode } from './GenerationProfileCard'
import { stepLabel, stepOfJob, stepsFor } from './storySteps'

const BUDGET_LABELS = { free: 'Free', one_dollar: '$1 / ep.', quality: 'Quality' }

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
 * The Visual tier card behind a header button: a non-modal popover. It stays
 * mounted while closed, so a refused pipeline switch's "Regenerate on v2"
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

  const summary = `Tier ${profile.tier} · ${profile.route} · ${BUDGET_LABELS[profile.budget_profile] || profile.budget_profile}`

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
        title="Visual tier: tier, route and budget profile"
      >
        <span className="story-tier-label">Visual tier</span>
        <span className="story-tier-summary">{summary}</span>
      </Button>
      <div
        id={panelId}
        ref={panelRef}
        className="story-tier-panel"
        role="dialog"
        aria-label="Visual tier"
        tabIndex={-1}
        hidden={!open}
      >
        <GenerationProfileCard storyId={storyId} story={story} nextEp={nextEp} onChange={onChange} />
      </div>
    </div>
  )
}

/**
 * The workspace's sticky header: the cover, the title, the story's chips,
 * the Visual tier popover and the one primary action -- the episode page once
 * the story is ready, else the step to do next.
 */
export default function StoryHeader({ storyId, data, styles, allDone, currentKey, activeKey, inFlightJob, onChange }) {
  const { story } = data
  const episodes = data.episodes || []
  const isV2 = story.generation_profile.pipeline === 'v2'
  const latest = episodes.length > 0 ? episodes[episodes.length - 1] : null
  const runningStep = stepOfJob(inFlightJob)

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
    <header className="story-header">
      <HeaderCover storyId={storyId} story={story} characters={data.characters} styles={styles} />
      <div className="story-header-text">
        <h2 className="story-header-title">{story.title || 'Untitled story'}</h2>
        <div className="story-header-chips">
          <Chip>{story.language === 'fr' ? 'Français' : 'English'}</Chip>
          {story.style_template_id && <Chip>{styleNameOf(styles, story.style_template_id)}</Chip>}
          {isV2 && <Chip tone="accent">Animated</Chip>}
          <Chip>Tier {story.generation_profile.tier}</Chip>
          {inFlightJob && !runningStep && (
            <Chip tone="warning">
              <Spinner size={12} />
              {inFlightJob.step} {inFlightJob.status === 'queued' ? 'queued' : 'running'}
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

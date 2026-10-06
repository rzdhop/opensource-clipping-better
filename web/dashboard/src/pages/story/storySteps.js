// The story workspace's steps (dashboard overhaul stage 3, DEC-255): their
// order, labels, URL ids (`/story/:storyId/:step`), status and the reason a
// locked step gives. The ids are the step ids the stories list's
// `progress.next` carries too (workflow.list_progress).
import { BookOpen, Brain, Calendar, Lightbulb, MapPin, Palette, Users } from '../../ui/icons'
import { knowledgeState } from './steps/KnowledgeStep'

export const STEPS = [
  { key: 'concepts', number: 1, label: 'Concepts', icon: Lightbulb,
    help: 'Pick the concept the story grows from, or generate more.' },
  { key: 'bible', number: 2, label: 'Bible', icon: BookOpen,
    help: 'The world, the tone and the audience. Edit any field, then approve.' },
  { key: 'style', number: 3, label: 'Style', icon: Palette,
    help: 'Palette, typography and consistency, locked once approved.' },
  { key: 'cast', number: 4, label: 'Cast', icon: Users,
    help: 'Each character\'s look, personality and voice. Approve every one.' },
  { key: 'places', number: 5, label: 'Places & props', icon: MapPin,
    help: 'The places and props the episodes reuse. Approve every one.' },
  { key: 'season', number: 6, label: 'Season', icon: Calendar,
    help: 'The season\'s arc, episode by episode, and the series memory.' },
  // Phase 7 stage 5b (DEC-228): a v2 story only (stepsFor).
  { key: 'knowledge', number: 7, label: 'Knowledge base', icon: Brain,
    help: 'What the writers keep for the whole season.' },
]

export const IN_FLIGHT = ['queued', 'running']

// clipping.aistory.defaults.PIPELINE_V2: only a v2 story has a knowledge base.
export function stepsFor(story) {
  const v2 = Boolean(story && story.generation_profile && story.generation_profile.pipeline === 'v2')
  return STEPS.filter((s) => s.key !== 'knowledge' || v2)
}

// Plan 28 stage S2: what a story job is called on screen (its step id and target stay in the API).
const JOB_LABELS = {
  concepts: 'Concepts', bible: 'Bible', style_preview: 'Style preview', cast: 'Cast',
  places_proposal: 'Places proposal', places: 'Places & props', season: 'Season', knowledge: 'Knowledge base',
  script: 'Script', storyboard: 'Storyboard', assets: 'Pictures and clips', render: 'Render', rerender: 'Render',
  metadata: 'Title and description', regenerate: 'Make again', 'fast-track': 'Make the episode',
  'story-fast-track': 'Agent run',
}

/** "Cast", "Make again": a job's step as a person reads it. */
export function jobLabel(job) {
  if (!job) return ''
  return JOB_LABELS[job.step] || String(job.step || 'Step').replace(/[_-]+/g, ' ')
}

export function stepLabel(key) {
  return (STEPS.find((s) => s.key === key) || { label: key }).label
}

// Plan 21 stage 3: the agent run's parts, in order (``story_fast_track.
// PARTS``, read as text by tests/test_dashboard_agent_run.py so this list
// never drifts from the runner's own), each the rail key its sub_step drives
// (``places_proposal`` -> ``places``; ``episode`` has no rail step -- episode
// 1 itself) and the label the feed names it ("Agent n/9: <label>",
// ``story_fast_track.LABELS``).
export const AGENT_PARTS = [
  { subStep: 'concepts', railKey: 'concepts', label: 'concept' },
  { subStep: 'bible', railKey: 'bible', label: 'bible' },
  { subStep: 'style', railKey: 'style', label: 'style' },
  { subStep: 'cast', railKey: 'cast', label: 'cast' },
  { subStep: 'places_proposal', railKey: 'places', label: 'places proposal' },
  { subStep: 'places', railKey: 'places', label: 'places' },
  { subStep: 'season', railKey: 'season', label: 'season' },
  { subStep: 'knowledge', railKey: 'knowledge', label: 'knowledge' },
  { subStep: 'episode', railKey: null, label: 'episode 1' },
]

/** The agent run's part a job's `sub_step` names -- `{subStep, railKey,
 * label, number, total}` -- or null (no sub_step yet, or one this map does
 * not carry). */
export function agentPartOf(subStep) {
  const index = AGENT_PARTS.findIndex((part) => part.subStep === subStep)
  if (index < 0) return null
  return { ...AGENT_PARTS[index], number: index + 1, total: AGENT_PARTS.length }
}

export function statusOf(key, story, data) {
  if (key === 'concepts') return story.approvals.concept ? 'done' : 'active'
  if (key === 'bible') {
    if (story.approvals.bible) return 'done'
    return story.approvals.concept ? 'active' : 'disabled'
  }
  if (key === 'style') {
    if (story.approvals.style) return 'done'
    return story.approvals.bible ? 'active' : 'disabled'
  }
  if (key === 'cast') {
    if (story.approvals.cast) return 'done'
    return story.approvals.style ? 'active' : 'disabled'
  }
  if (key === 'places') {
    if (story.approvals.places) return 'done'
    return story.approvals.cast ? 'active' : 'disabled'
  }
  if (key === 'season') {
    if (story.approvals.season) return 'done'
    return story.approvals.places ? 'active' : 'disabled'
  }
  if (key === 'knowledge') {
    // Done only while approved and current (approved_rev === rev): the
    // episode gate's own rule (episode_common.knowledge_state).
    if (knowledgeState(data && data.knowledge) === 'approved') return 'done'
    return story.approvals.season ? 'active' : 'disabled'
  }
  return 'disabled'
}

export function disabledReason(key) {
  if (key === 'bible') return 'Choose a concept first.'
  if (key === 'style') return 'Approve the bible first.'
  if (key === 'cast') return 'Approve the style first.'
  if (key === 'places') return 'Approve the cast first.'
  if (key === 'season') return 'Approve every place and prop first.'
  if (key === 'knowledge') return 'Approve the season first.'
  return ''
}

/** One line for a done step (the rail's tooltip). */
export function summaryFor(key, story, data) {
  if (key === 'concepts') return `Chosen: ${(story.concept && story.concept.title) || 'Untitled concept'}`
  if (key === 'bible') return story.logline || 'Bible written.'
  if (key === 'style') {
    const lock = data.style_lock
    if (!lock) return 'No style yet.'
    const name = lock.template_name && (lock.template_name.en || lock.template_name.fr)
    return `${name || lock.template_id} — locked`
  }
  if (key === 'cast') {
    const count = (data.characters || []).length
    return count ? `${count} character${count === 1 ? '' : 's'} in the cast.` : 'No cast yet.'
  }
  if (key === 'places') {
    const placeCount = (data.places || []).length
    const propCount = (data.props || []).length
    if (!placeCount && !propCount) return 'No places yet.'
    return `${placeCount} place${placeCount === 1 ? '' : 's'}, ${propCount} prop${propCount === 1 ? '' : 's'}.`
  }
  if (key === 'season') {
    const season = data.season
    return season && season.episodes_planned ? `${season.episodes_planned} episodes planned.` : 'No season yet.'
  }
  if (key === 'knowledge') {
    const timeline = (data.knowledge && data.knowledge.timeline) || []
    return `Approved: ${timeline.length} episode${timeline.length === 1 ? '' : 's'} of beats.`
  }
  return ''
}

/** Where the workspace opens: the first step not done, else the last one. */
export function currentStepKey(story, data) {
  const steps = stepsFor(story)
  const pending = steps.find((s) => statusOf(s.key, story, data) !== 'done')
  return (pending || steps[steps.length - 1]).key
}

/**
 * The step a story job belongs to (the same job each step's own `myJob`
 * test picks), or null for a job no step owns (fast-track, ...).
 */
export function stepOfJob(job) {
  if (!job) return null
  const target = job.params && typeof job.params.target === 'string' ? job.params.target : ''
  switch (job.step) {
    // Plan 21 stage 3: the agent run names its own rail step by its
    // sub_step (AGENT_PARTS), not by its own job step name.
    case 'story-fast-track': {
      const part = agentPartOf(job.sub_step)
      return part ? part.railKey : null
    }
    case 'concepts': return 'concepts'
    case 'bible': return 'bible'
    case 'style_preview': return 'style'
    case 'cast': return 'cast'
    case 'places_proposal':
    case 'places': return 'places'
    case 'season': return 'season'
    case 'knowledge': return 'knowledge'
    case 'regenerate':
      if (target === 'concepts') return 'concepts'
      if (target.startsWith('bible:')) return 'bible'
      if (target.startsWith('character:')) return 'cast'
      if (target.startsWith('place:') || target.startsWith('prop:')) return 'places'
      if (target.startsWith('season:')) return 'season'
      return null
    default: return null
  }
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { createStory, fetchStory, fetchStyles, styleNameOf } from '../../api'
import { LiveActivity, useJobFeed } from '../../components/ActivityFeed'
import ConceptsStep from './steps/ConceptsStep'
import BibleStep from './steps/BibleStep'
import StyleStep from './steps/StyleStep'
import CastStep from './steps/CastStep'
import PlacesStep from './steps/PlacesStep'
import SeasonStep from './steps/SeasonStep'

// The story defaults (spec 8, 8.1, 8.5): a story that does not name every
// one of these gets exactly these values. tests/test_story_defaults.py reads
// this literal as text and checks it against
// clipping.aistory.defaults.default_generation_profile().
const DEFAULT_GENERATION_PROFILE = { tier: 1, route: 'auto', consistency_mode: 'references', budget_profile: 'free' }

const STEPS = [
  { key: 'concepts', number: 2, label: 'Concepts' },
  { key: 'bible', number: 3, label: 'Bible' },
  { key: 'style', number: 4, label: 'Style' },
  { key: 'cast', number: 5, label: 'Cast' },
  { key: 'places', number: 6, label: 'Places & props' },
  { key: 'season', number: 7, label: 'Season' },
]

const IN_FLIGHT = ['queued', 'running']
const STORY_POLL_MS = 4000

function statusOf(key, story) {
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
  return 'disabled'
}

function disabledReason(key) {
  if (key === 'bible') return 'Choose a concept first.'
  if (key === 'style') return 'Approve the bible first.'
  if (key === 'cast') return 'Approve the style first.'
  if (key === 'places') return 'Approve the cast first.'
  if (key === 'season') return 'Approve every place and prop first.'
  return ''
}

function summaryFor(key, story, data) {
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
  return ''
}

// --------------------------------------------------------------- creation

function CreateStoryForm() {
  const navigate = useNavigate()
  // No default: a language a user forgot to pick must never silently become
  // one, matching StoryCreateRequest and the CLI's --lang.
  const [language, setLanguage] = useState(null)
  const [seedText, setSeedText] = useState('')
  const [styleTemplateId, setStyleTemplateId] = useState('')
  const [styles, setStyles] = useState([])
  const [showProfile, setShowProfile] = useState(false)
  const [tier, setTier] = useState(DEFAULT_GENERATION_PROFILE.tier)
  const [route, setRoute] = useState(DEFAULT_GENERATION_PROFILE.route)
  const [consistencyMode, setConsistencyMode] = useState(DEFAULT_GENERATION_PROFILE.consistency_mode)
  const [budgetProfile, setBudgetProfile] = useState(DEFAULT_GENERATION_PROFILE.budget_profile)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    fetchStyles().then((data) => { if (!cancelled) setStyles(data.styles || []) }).catch(() => {})
    return () => { cancelled = true }
  }, [])

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!language) return
    setCreating(true)
    setError('')
    try {
      // The story's own request payload, one key per line: the payload
      // contract test reads this block as text and checks each key is a
      // field of StoryCreateRequest.
      const createFields = {
        language,
        seed_text: seedText.trim() ? seedText : null,
        style_template_id: styleTemplateId || null,
        generation_profile: {
          tier,
          route,
          consistency_mode: consistencyMode,
          budget_profile: budgetProfile,
        },
      }
      const story = await createStory(createFields)
      navigate(`/story/${story.story_id}`)
    } catch (err) {
      setError(err.message)
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="fade-in">
      <div className="page-header">
        <div>
          <h2>New story</h2>
          <p>A persistent workspace: world, style lock and a season arc.</p>
        </div>
      </div>

      <form onSubmit={handleSubmit}>
        <div className="card" style={{ marginBottom: '16px' }}>
          <div className="form-group">
            <label className="form-label">Language</label>
            <div className="story-segmented">
              <button
                type="button"
                className={`btn btn-sm ${language === 'fr' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setLanguage('fr')}
              >
                Français
              </button>
              <button
                type="button"
                className={`btn btn-sm ${language === 'en' ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => setLanguage('en')}
              >
                English
              </button>
            </div>
            <p className="form-hint">Required — nothing is picked for you.</p>
          </div>

          <div className="form-group">
            <label className="form-label">Seed text (optional)</label>
            <textarea
              className="form-input"
              rows={4}
              maxLength={2000}
              placeholder="A rough idea, a scene, a vibe — anything to seed the concepts."
              value={seedText}
              onChange={(e) => setSeedText(e.target.value)}
            />
            <p className="form-hint">{seedText.length} / 2000</p>
          </div>

          <div className="form-group">
            <label className="form-label">Style (optional)</label>
            <div className="story-style-grid">
              <button
                type="button"
                className={`story-style-pick${styleTemplateId === '' ? ' active' : ''}`}
                onClick={() => setStyleTemplateId('')}
              >
                <span className="story-style-pick-name">Decide later</span>
              </button>
              {styles.map((style) => (
                <button
                  key={style.template_id}
                  type="button"
                  className={`story-style-pick${styleTemplateId === style.template_id ? ' active' : ''}`}
                  onClick={() => setStyleTemplateId(style.template_id)}
                >
                  <span className="story-swatch-row">
                    {style.palette.primary.slice(0, 3).map((hex, i) => (
                      <span key={i} className="story-swatch" style={{ background: hex }} />
                    ))}
                  </span>
                  <span className="story-style-pick-name">{style.name.en}</span>
                </button>
              ))}
            </div>
          </div>

          <details className="story-profile" open={showProfile} onToggle={(e) => setShowProfile(e.target.open)}>
            <summary>Generation profile</summary>
            <div className="story-profile-grid">
              <div className="form-group">
                <label className="form-label">Tier</label>
                <select className="form-select" value={tier} onChange={(e) => setTier(Number(e.target.value))}>
                  <option value={1}>1 — stills</option>
                  <option value={2}>2 — animated key shots</option>
                  <option value={3}>3 — fully animated</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Route</label>
                <select className="form-select" value={route} onChange={(e) => setRoute(e.target.value)}>
                  <option value="auto">Auto</option>
                  <option value="local">Local</option>
                  <option value="api">API</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Consistency mode</label>
                <select className="form-select" value={consistencyMode} onChange={(e) => setConsistencyMode(e.target.value)}>
                  <option value="references">References</option>
                  <option value="prompt_only">Prompt only</option>
                </select>
              </div>
              <div className="form-group">
                <label className="form-label">Budget profile</label>
                <select className="form-select" value={budgetProfile} onChange={(e) => setBudgetProfile(e.target.value)}>
                  <option value="free">Free</option>
                  <option value="one_dollar">$1 / episode</option>
                  <option value="quality">Quality</option>
                </select>
              </div>
            </div>
          </details>
        </div>

        {error && <p className="story-error">{error}</p>}

        <button type="submit" className="btn btn-primary" disabled={!language || creating}>
          {creating ? <><span className="spinner"></span> Creating…</> : 'Create story'}
        </button>
      </form>
    </div>
  )
}

// ------------------------------------------------------------- an existing story

function StoryJobActivity({ jobId }) {
  const { job, events, streamState } = useJobFeed(jobId)
  if (!job) return null
  return <LiveActivity job={job} events={events} streamState={streamState} />
}

function StoryJobList({ jobs }) {
  const [openId, setOpenId] = useState(null)
  if (!jobs || jobs.length === 0) return null

  return (
    <div className="card story-job-list-card">
      <h3 className="card-title">Step jobs</h3>
      <div className="story-job-list">
        {jobs.slice().reverse().map((job) => (
          <div key={job.id} className="story-job-row">
            <button
              type="button"
              className="story-job-row-header"
              onClick={() => setOpenId(openId === job.id ? null : job.id)}
            >
              <span className="story-job-row-step">
                {job.step}
                {job.params && job.params.target ? ` · ${job.params.target}` : ''}
              </span>
              <span className="chip">
                {job.status === 'queued' ? 'queued — waiting for the worker' : job.status}
              </span>
              <span className="story-job-row-time">{new Date(job.updated_at).toLocaleTimeString()}</span>
            </button>
            {openId === job.id && <StoryJobActivity jobId={job.id} />}
          </div>
        ))}
      </div>
    </div>
  )
}

function ExistingStory({ storyId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [manualStep, setManualStep] = useState(null)
  const [styles, setStyles] = useState([])
  const stepRefs = useRef({})
  const scrollPending = useRef(false)

  const refresh = useCallback(async () => {
    try {
      const fresh = await fetchStory(storyId)
      setData(fresh)
      setLoadError('')
    } catch (err) {
      setLoadError(err.message)
    } finally {
      setLoading(false)
    }
  }, [storyId])

  useEffect(() => {
    fetchStyles().then((d) => setStyles(d.styles || [])).catch(() => {})
  }, [])

  useEffect(() => {
    setLoading(true)
    setData(null)
    setManualStep(null)
    // Covers a create's redirect here too (finding 4): the page the browser
    // is scrolled to does not reset on a client-side route change, so the
    // freshly active step can otherwise land off-screen.
    scrollPending.current = true
    refresh()
  }, [storyId, refresh])

  // After an action advances the story (a concept chosen, a document
  // approved) or a fresh story page loads, scroll the newly active step's
  // header into view -- otherwise, on a phone, the step that just unlocked
  // can be a full screen away from wherever the page happened to be
  // scrolled (spec 10 finding 4).
  useEffect(() => {
    if (!scrollPending.current) return
    scrollPending.current = false
    if (!data) return
    const activeStep = STEPS.find((s) => statusOf(s.key, data.story) === 'active')
    const key = manualStep || (activeStep ? activeStep.key : 'cast')
    const el = stepRefs.current[key]
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data])

  // While the story has a step job queued or running, poll the story itself.
  // The step feeds can miss the moment a job finishes -- a refresh can land
  // between the step's last write and the job's flip to awaiting_approval,
  // and the feed has stopped by then -- which left a finished preview shown
  // as "Generating…" (Tier-2, 2026-09-26). This makes the page converge.
  const inFlightId = data
    ? ((data.jobs || []).find((j) => IN_FLIGHT.includes(j.status)) || {}).id || null
    : null
  useEffect(() => {
    if (!inFlightId) return
    const timer = setInterval(refresh, STORY_POLL_MS)
    return () => clearInterval(timer)
  }, [inFlightId, refresh])

  const afterAction = () => {
    setManualStep(null)
    refresh()
  }

  const afterAdvance = () => {
    scrollPending.current = true
    setManualStep(null)
    refresh()
  }

  // Coordinator fix attempt 2 (F1, phase 5 stage 13b, live walk): a series
  // panel action (memory, feedback, propose-next, a proposal decision, its
  // own "Approve proposals") -- and the Season step's own job feed -- must
  // never collapse the step. Every other step's afterAction closing on
  // success is fine (there is somewhere to advance to); once the season is
  // approved nothing is 'active' any more, so clearing manualStep here fell
  // back to 'cast' the instant a series action succeeded.
  const afterSeriesAction = () => {
    refresh()
  }

  if (loading) {
    return <div className="fade-in"><div className="empty-state"><span className="spinner"></span></div></div>
  }
  if (loadError) {
    return <div className="fade-in"><div className="card"><p className="story-error">{loadError}</p></div></div>
  }
  if (!data) return null

  const { story } = data
  const inFlightJob = data.jobs.find((j) => IN_FLIGHT.includes(j.status)) || null
  const defaultExpanded = STEPS.find((s) => statusOf(s.key, story) === 'active')
  const expanded = manualStep || (defaultExpanded ? defaultExpanded.key : 'cast')
  // Keyed on the server's own derived status (store.derive_status), not
  // approvals.season alone: a voice regeneration can clear a character's
  // approval, which clears approvals.cast and drops the status back to
  // style_approved even though approvals.season was never touched -- the
  // card must not keep claiming the story is ready once that happens.
  const allDone = story.status === 'ready'

  return (
    <div className="fade-in">
      <div className="page-header">
        <div>
          <h2>{story.title || 'Untitled story'}</h2>
          <p>{story.language === 'fr' ? 'Français' : 'English'}{story.style_template_id ? ` · ${styleNameOf(styles, story.style_template_id)}` : ''}</p>
        </div>
      </div>

      <div className="stepper">
        <div className="stepper-step stepper-step-done">
          <div className="stepper-step-header">
            <span className="stepper-step-index">✓</span>
            <span className="stepper-step-label">New story</span>
          </div>
          <div className="stepper-summary">
            {story.language === 'fr' ? 'French' : 'English'}
            {story.seed_text ? ' · seed text set' : ''}
          </div>
        </div>

        {STEPS.map((step) => {
          const status = statusOf(step.key, story)
          const reopenable = status === 'done' && step.key !== 'style'
          const isOpen = status !== 'disabled' && expanded === step.key
          const clickable = status === 'active' || reopenable

          return (
            <div
              key={step.key}
              ref={(el) => { stepRefs.current[step.key] = el }}
              className={`stepper-step stepper-step-${status}`}
            >
              <button
                type="button"
                className="stepper-step-header"
                disabled={!clickable}
                onClick={() => clickable && setManualStep(isOpen ? null : step.key)}
              >
                <span className="stepper-step-index">{status === 'done' ? '✓' : step.number}</span>
                <span className="stepper-step-label">{step.label}</span>
                {status === 'disabled' && <span className="stepper-step-reason">{disabledReason(step.key)}</span>}
                {reopenable && !isOpen && <span className="stepper-step-edit">Edit</span>}
              </button>

              {status === 'done' && !isOpen && (
                <div className="stepper-summary">{summaryFor(step.key, story, data)}</div>
              )}

              {isOpen && (
                <div className="stepper-step-body">
                  {step.key === 'concepts' && (
                    <ConceptsStep storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {step.key === 'bible' && (
                    <BibleStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {step.key === 'style' && (
                    <StyleStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {step.key === 'cast' && (
                    <CastStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {step.key === 'places' && (
                    <PlacesStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onAdvance={afterAdvance} />
                  )}
                  {step.key === 'season' && (
                    <SeasonStep data={data} storyId={storyId} inFlightJob={inFlightJob} onChange={afterAction} onSeriesChange={afterSeriesAction} onAdvance={afterAdvance} />
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {allDone && (
        <div className="card story-ready-card" style={{ marginBottom: '16px' }}>
          <h3 className="card-title">Ready</h3>
          <p>The story is ready: bible, style, cast, places and a planned season.</p>
          <Link to={`/story/${storyId}/episodes/1`} className="story-ready-open-episode">
            Open episode 1 →
          </Link>
          {data.episodes.length > 0 && (
            <ul className="story-field-list story-ready-episodes">
              {data.episodes.map((entry) => (
                <li key={entry.ep}>
                  <Link to={`/story/${storyId}/episodes/${entry.ep}`}>
                    Episode {entry.ep}
                  </Link>
                  {': '}{entry.script_state}
                  {entry.total_s != null ? ` · ${entry.total_s.toFixed(1)} s` : ''}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <StoryJobList jobs={data.jobs} />
    </div>
  )
}

// --------------------------------------------------------------------- page

export default function NewStoryWizard() {
  const { storyId } = useParams()
  if (!storyId) return <CreateStoryForm />
  return <ExistingStory storyId={storyId} />
}

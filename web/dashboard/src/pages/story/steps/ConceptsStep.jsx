import { useEffect, useState } from 'react'
import { fetchConcepts, generateConcepts, chooseConcept, fetchStoryEstimate, fetchStyles, styleNameOf } from '../../../api'
import EstimateChip from '../../../components/EstimateChip'
import RouteChip from '../../../components/RouteChip'
import { LiveActivity, useJobFeed } from '../../../components/ActivityFeed'
import { StepError } from '../fields'

/** A library card's `style_fit` is `{default, alternatives}`; a generated
 * card's is the bare template id. Both are shown and filtered the same way. */
function styleFitOf(card) {
  const fit = card.style_fit
  return typeof fit === 'string' ? fit : fit && fit.default
}

/**
 * Compact by default -- title, logline, style, value and "Pick this concept"
 * -- so ten of these do not each run a full phone screen. "Details" reveals
 * the rest (world, cast, hook formula, retention mechanics); the pick button
 * never moves, so choosing a concept never requires expanding it first.
 */
function ConceptCard({ card, styleName, onChoose, choosing, disabled, error }) {
  const [detailsOpen, setDetailsOpen] = useState(false)
  const isChoosing = choosing === card.concept_id

  return (
    <div className="card story-concept-card">
      <h4>{card.title}</h4>
      <p className="story-concept-logline">{card.logline}</p>
      <p className="story-concept-value"><em>Value:</em> {card.value}</p>

      <button
        type="button"
        className="btn btn-ghost btn-sm"
        onClick={() => setDetailsOpen((open) => !open)}
      >
        {detailsOpen ? 'Hide details' : 'Details'}
      </button>

      {detailsOpen && (
        <div className="story-concept-details">
          <p className="story-concept-world">{card.world}</p>
          {Array.isArray(card.cast_sketch) && card.cast_sketch.length > 0 && (
            <ul className="story-field-list">
              {card.cast_sketch.map((member, i) => (
                <li key={i}><strong>{member.name}</strong> ({member.role}) — {member.one_line}</li>
              ))}
            </ul>
          )}
          <p><em>Hook:</em> {card.hook_formula}</p>
          <p><em>Keeps people watching:</em> {card.retention_mechanics}</p>
        </div>
      )}

      <div className="story-concept-footer">
        <span className="chip">{styleName}</span>
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => onChoose(card.concept_id)}
          disabled={disabled}
        >
          {isChoosing ? 'Choosing…' : 'Pick this concept'}
        </button>
      </div>
      <StepError message={error && error.message} errors={error && error.errors} className="story-step-error" />
    </div>
  )
}

export default function ConceptsStep({ storyId, inFlightJob, onChange, onAdvance }) {
  const [concepts, setConcepts] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [styles, setStyles] = useState([])
  const [styleFilter, setStyleFilter] = useState('')
  const [choosing, setChoosing] = useState(null)
  const [chooseError, setChooseError] = useState(null) // { conceptId, message, errors }
  const [generateError, setGenerateError] = useState(null) // { message, errors }
  const [estimate, setEstimate] = useState(null)

  const loadConcepts = () => {
    fetchConcepts(storyId).then(setConcepts).catch((err) => setLoadError(err.message))
  }
  const loadEstimate = () => {
    fetchStoryEstimate(storyId, 'concepts').then(setEstimate).catch(() => setEstimate(null))
  }

  useEffect(() => {
    loadConcepts()
    loadEstimate()
    fetchStyles().then((data) => setStyles(data.styles || [])).catch(() => {})
  }, [storyId]) // eslint-disable-line react-hooks/exhaustive-deps

  const myJob = inFlightJob && (
    inFlightJob.step === 'concepts'
    || (inFlightJob.step === 'regenerate' && inFlightJob.params && inFlightJob.params.target === 'concepts')
  ) ? inFlightJob : null

  const { job: liveJob, events, streamState } = useJobFeed(myJob ? myJob.id : null, {
    onJob: (job) => {
      if (job && job.status !== 'queued' && job.status !== 'running') {
        loadConcepts()
        loadEstimate()
      }
    },
  })

  const busy = Boolean(inFlightJob)

  const handleGenerate = async () => {
    setGenerateError(null)
    try {
      await generateConcepts(storyId)
      onChange()
    } catch (err) {
      setGenerateError({ message: err.message, errors: err.errors || null })
    }
  }

  const handleChoose = async (conceptId) => {
    setChoosing(conceptId)
    setChooseError(null)
    try {
      await chooseConcept(storyId, { concept_id: conceptId })
      onAdvance()
    } catch (err) {
      setChooseError({ conceptId, message: err.message, errors: err.errors || null })
      setChoosing(null)
    }
  }

  if (loadError) return <p className="story-error">{loadError}</p>
  if (!concepts) return <div className="story-loading"><span className="spinner"></span></div>

  const styleIds = [...new Set(concepts.library.map(styleFitOf).filter(Boolean))].sort()
  const filteredLibrary = styleFilter
    ? concepts.library.filter((card) => {
      const fit = card.style_fit
      if (typeof fit === 'string') return fit === styleFilter
      return fit && (fit.default === styleFilter || (fit.alternatives || []).includes(styleFilter))
    })
    : concepts.library

  return (
    <div className="story-step-body">
      <div className="story-step-actions">
        <button type="button" className="btn btn-secondary" onClick={handleGenerate} disabled={busy}>
          {myJob ? <><span className="spinner"></span> Generating…</> : 'Generate 10 more'}
        </button>
        <EstimateChip estimate={estimate} />
        {estimate && <RouteChip routeClass={estimate.route_class} link={estimate.link} />}
      </div>
      <StepError message={generateError && generateError.message} errors={generateError && generateError.errors} className="story-step-error" />

      {myJob && liveJob && (
        liveJob.status === 'queued'
          ? <p className="form-hint">queued — waiting for the worker</p>
          : <LiveActivity job={liveJob} events={events} streamState={streamState} />
      )}

      {styleIds.length > 0 && (
        <div className="story-filter-chips">
          <button
            type="button"
            className={`chip${styleFilter === '' ? ' chip-accent' : ''}`}
            onClick={() => setStyleFilter('')}
          >
            All styles
          </button>
          {styleIds.map((id) => (
            <button
              key={id}
              type="button"
              className={`chip${styleFilter === id ? ' chip-accent' : ''}`}
              onClick={() => setStyleFilter(id)}
            >
              {styleNameOf(styles, id)}
            </button>
          ))}
        </div>
      )}

      <h4 className="story-section-title">Library</h4>
      <div className="story-concept-grid">
        {filteredLibrary.map((card) => (
          <ConceptCard
            key={card.concept_id}
            card={card}
            styleName={styleNameOf(styles, styleFitOf(card))}
            onChoose={handleChoose}
            choosing={choosing}
            disabled={busy || choosing !== null}
            error={chooseError && chooseError.conceptId === card.concept_id ? chooseError : null}
          />
        ))}
      </div>

      {concepts.generated.length > 0 && (
        <>
          <h4 className="story-section-title">Generated</h4>
          <div className="story-concept-grid">
            {concepts.generated.map((card) => (
              <ConceptCard
                key={card.concept_id}
                card={card}
                styleName={styleNameOf(styles, styleFitOf(card))}
                onChoose={handleChoose}
                choosing={choosing}
                disabled={busy || choosing !== null}
                error={chooseError && chooseError.conceptId === card.concept_id ? chooseError : null}
              />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

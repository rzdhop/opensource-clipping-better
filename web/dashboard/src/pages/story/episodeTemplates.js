// The episode formats shipped (spec 6.2; serial_60s_v2 since phase 7,
// DEC-227: 6-10 beat shots of 5-12 s, every shot animated; serial_90s_v2 and
// narrated_drama_60s_v2 since the fruit-drama pack, plan 20 stage 1): one
// list for the new-story form's "Episode format" and the episode page's
// "Episode length". FR/EN-agnostic English labels, since the story's
// language is the *cast's* language, not the workspace UI's (the template
// files carry the fr/en labels). Ids mirror
// clipping.aistory.defaults.EPISODE_TEMPLATE_IDS exactly
// (tests/test_story_payload_contract_episode.py); `pipeline` is the one each
// format is shaped for ('v2' or '' for legacy).
export const EPISODE_TEMPLATES = [
  { id: 'serial_60s_v1', label: '60 s (55–80)', pipeline: '',
    help: 'Classic: 8–12 short scenes of stills or clips.' },
  { id: 'serial_90s_v1', label: '90 s (75–100)', pipeline: '',
    help: 'Classic, longer: 8–12 scenes over 75–100 s.' },
  { id: 'serial_60s_v2', label: '60 s beat shots (55–75)', pipeline: 'v2',
    help: '6–10 beat shots of 5–12 s, each one a clip.' },
  { id: 'serial_90s_v2', label: '90 s beat shots (80–100)', pipeline: 'v2',
    help: '8–12 scenes of beat shots over 80–100 s, each shot a clip.' },
  { id: 'narrated_drama_60s_v2', label: 'Narrated drama 60 s (58–78)', pipeline: 'v2',
    help: 'Narrated drama: one dramatic narrator, 2–4 character lines.' },
]

// clipping.aistory.defaults.episode_template_for: the template a story of
// `pipeline` starts on when it names none.
export function pipelineDefaultTemplate(pipeline) {
  return pipeline === 'v2' ? 'serial_60s_v2' : 'serial_60s_v1'
}

// The format a style suggests (GET /api/stories/styles:
// episode_defaults.episode_template_id) when it fits the story's pipeline;
// null when there is no style, or its suggestion is shaped for the other
// pipeline (every style but Fruit Drama still names serial_60s_v1).
export function styleSuggestedTemplate(style, pipeline) {
  const suggested = style && style.episode_defaults && style.episode_defaults.episode_template_id
  const format = EPISODE_TEMPLATES.find((tpl) => tpl.id === suggested)
  return format && format.pipeline === (pipeline || '') ? format.id : null
}

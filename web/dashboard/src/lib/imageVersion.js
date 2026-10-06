// A picture's version: what changes when its file is rewritten under the same
// name (a regenerated plate keeps `variant_day.jpg`, a regenerated keyframe
// keeps `shot_03.png`). Tiles put it in their load effect's dependency list and
// in the fetched URL (`?v=`), so a new file is fetched again and no cache layer
// can answer with the old bytes. Empty when the ref carries nothing to tell.

/** The version of an image ref (or a shot's assets entry), '' when it has none. */
export function imageVersion(ref) {
  if (!ref) return ''
  return String(ref.created_at || ref.generated_at || ref.sha256 || ref.image_hash || ref.seed || '')
}

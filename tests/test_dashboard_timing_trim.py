"""The Trim action on a timing warning (plan 24, stage 6): text contracts over
the JSX (CI has no node).

A ``trim_line`` flag (and a ``scene_over`` flag whose scene has none) gets a
"Trim" button in the Script step's warning panel. It reuses the scene
regenerate that already exists -- ``regenerateStory`` with the target
``scene:<ep>:<scene_id>`` -- and a note built client-side from the scene's slot
and line plan; no new route, no new API call shape. The button is disabled
exactly while the scene's own "Regenerate" is (a job in flight).
"""

from __future__ import annotations

from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "web" / "dashboard" / "src"
EPISODE = SRC / "pages" / "story" / "episode"
BAR = EPISODE / "DurationBar.jsx"
SCRIPT = EPISODE / "ScriptPane.jsx"
API = SRC / "api.js"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_a_timing_warning_trims_its_scene_through_the_existing_regenerate():
    """Fail-first: DurationBar had no action. The button calls the one
    ``regenerateStory`` the scene card calls, with the scene target and the
    trim note, and adds no API function."""
    bar = _read(BAR)
    assert "import { regenerateStory } from '../../../api'" in bar
    assert "regenerateStory(storyId, { target: `scene:${ep}:${flag.scene_id}`, note: trimNote(flag, scene, flags) })" in bar
    assert "Trim to the slot: scene " in bar
    assert "keep the meaning and the speaker, cut words." in bar
    # the line plan's caps and the slot's upper bound feed the note
    assert "scene.line_plan" in bar and "max_words" in bar and "slot_s" in bar
    # a trim_line flag is trimmable; a scene_over flag only when its scene has no trim_line flag
    assert "f.kind === 'trim_line'" in bar and "flag.kind === 'scene_over' && !trimmed.has(flag.scene_id)" in bar
    # the scene card's own regenerate is the same call shape
    assert "regenerateStory(storyId, { target: `scene:${ep}:${scene.scene_id}`, note })" in _read(SCRIPT)
    assert "regenerateStory" in _read(API)


def test_the_trim_button_is_disabled_while_a_job_runs_and_shows_its_error():
    """The pane passes the same ``busy`` the scene's Regenerate gets; the button
    keeps a running state and the shared step error."""
    script = _read(SCRIPT)
    assert "<TimingWarnings timing={script.timing} scenes={script.scenes} storyId={storyId} ep={ep} busy={busy}" in script
    bar = _read(BAR)
    assert "disabled={disabled || running}" in bar
    assert "<StepError message={error} errors={errors}" in bar
    assert "'Trimming…'" in bar

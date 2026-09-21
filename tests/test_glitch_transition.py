"""The glitch transition must not be a black frame.

A user reported a "blackscreen like bug" about one second into every clip. It was
the Hook Glitch transition. The lavfi fallback -- which is the ONLY path now,
since the pinned source video went private -- built its noise on a pure black
base:

    color=c=black -> noise=alls=100:allf=t+u -> rgbashift

`noise` adds a SIGNED offset, so on black every negative value clamps to 0 and
only the positive half survives. Measured mean luma: 19/255. That is a black
frame with faint speckle. On a mid-grey base the identical recipe measures
126/255 and reads as static.

`blackdetect` never fired, because the frame is not quite black -- which is
exactly why nothing caught it.

The render itself needs ffmpeg, so the brightness assertion is skipped when
ffmpeg is absent (CI installs pytest and nothing else). The source-level
assertions always run.
"""

import pathlib
import shutil
import subprocess

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
EFFECTS = PROJECT_ROOT / "clipping" / "studio" / "effects.py"


def effects_source():
    return EFFECTS.read_text(encoding="utf-8")


# ------------------------------------------------------------ the recipe itself

def test_the_glitch_is_not_built_on_a_black_base():
    """The one-line cause. On black, noise can only brighten, and barely."""
    source = effects_source()
    assert "color=c=black:s={out_w}x{out_h}" not in source
    assert "color=c=gray:s={out_w}x{out_h}" in source


def test_the_noise_filter_is_still_there():
    """Guard against 'fixing' the black frame by removing the effect."""
    source = effects_source()
    assert "noise=alls=" in source
    assert "rgbashift" in source


def test_an_unset_source_url_still_generates_locally():
    """URL_GLITCH_VIDEO is empty since the pinned video went private, so the
    lavfi path is the only one that runs. If this branch were removed the
    transition would silently vanish rather than be fixed."""
    source = effects_source()
    assert 'url_glitch = getattr(cfg, "url_glitch_video", None)' in source
    assert "generating glitch locally" in source


# --------------------------------------------------------- what it actually looks like

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_the_generated_frame_is_visibly_not_black(tmp_path):
    """Render the real recipe and measure it. 19/255 was the bug; grey gives ~126.

    The threshold is deliberately far from both: anything under 60 is a frame a
    viewer would call black, and anything over 200 is a white flash.
    """
    pytest.importorskip("PIL")
    from PIL import Image, ImageStat

    # The exact filter chain from effects.py, read out of the source so this
    # cannot pass against a recipe the pipeline no longer uses.
    source = effects_source()
    assert 'color=c=gray:s={out_w}x{out_h}:d={duration}:r=30' in source
    vf_line = next(
        line for line in source.splitlines()
        if '"-vf", "noise=alls=' in line
    )
    vf = vf_line.split('"-vf", "')[1].rsplit('"', 1)[0]

    out = tmp_path / "glitch.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=gray:s=160x288:d=1:r=30",
         "-vf", vf, "-frames:v", "1", str(out)],
        check=True, capture_output=True,
    )

    stat = ImageStat.Stat(Image.open(out).convert("L"))
    assert 60 < stat.mean[0] < 200, f"mean luma {stat.mean[0]:.1f}/255 reads as a flat frame"
    # Static, not a flat grey card: the noise has to actually vary.
    assert stat.stddev[0] > 10, f"stddev {stat.stddev[0]:.1f} -- no visible noise"


# --------------------------------------------------------------- the default

def test_the_transition_is_opt_in_everywhere():
    """One default in five places. A mismatch means the dashboard and the CLI
    disagree about what a job with no explicit setting does.

    JobCreateRequest is read as TEXT rather than imported. Importing it pulls in
    pydantic, which CI does not install (DEC-012), so an importorskip here would
    mean this guard never runs in the one place that checks every push -- and a
    drifted default is exactly the kind of thing that survives on a dev box.
    """
    from clipping import config

    assert config.USE_HOOK_GLITCH is False

    models = (PROJECT_ROOT / "web" / "api" / "models.py").read_text(encoding="utf-8")
    assert "use_hook_glitch: bool = False" in models

    adapter = (PROJECT_ROOT / "web" / "api" / "config_adapter.py").read_text(encoding="utf-8")
    assert 'payload.get("use_hook_glitch", False)' in adapter

    jsx = (
        PROJECT_ROOT / "web" / "dashboard" / "src" / "pages" / "NewJob.jsx"
    ).read_text(encoding="utf-8")
    assert "const [useHookGlitch, setUseHookGlitch] = useState(false)" in jsx


def test_the_cli_can_still_turn_it_on_and_off(tmp_path):
    """--hook-glitch enables it now that the default is off; --no-hook still
    wins, so a script that disables it explicitly does not silently start
    enabling it."""
    from clipping.config import build_config

    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 2048)
    base = ["--video", str(video)]

    assert build_config(base).use_hook_glitch is False
    assert build_config(base + ["--hook-glitch"]).use_hook_glitch is True
    assert build_config(base + ["--no-hook"]).use_hook_glitch is False
    assert build_config(base + ["--hook-glitch", "--no-hook"]).use_hook_glitch is False


def test_the_cache_key_carries_the_recipe_version():
    """The cached .ts is keyed by filename and returned unconditionally, so a
    machine that already has one never regenerates it. Without a version in the
    name, every machine that had rendered once would keep the black frame
    forever -- the same way a stale custom_fonts/Montserrat-Regular.ttf survived
    the font fix (DEC-049).
    """
    source = effects_source()
    assert "GLITCH_RECIPE_VERSION" in source
    assert 'f"glitch_ready_{out_w}x{out_h}_v{GLITCH_RECIPE_VERSION}.ts"' in source


def test_the_version_was_bumped_past_the_black_recipe():
    # effects.py imports cv2, so read the constant out of the source rather than
    # importing the module -- the CI environment has pytest and nothing else.
    source = effects_source()
    version = int(source.split("GLITCH_RECIPE_VERSION = ")[1].split("\n")[0])
    assert version >= 2, "v1 is the black-frame recipe"

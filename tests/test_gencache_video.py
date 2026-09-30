"""A video clip joins the generation cache (AI Story phase 6): the request
key now covers what a clip is bought for -- its keyframe's bytes, its
length, its frame rate and whether it carries native audio -- so a paid
clip is kept and never bought twice (DEC-151..154), while the image and
voice keys stay exactly what they were (RC-V2). The runner side is
``test_gencache_runner.py``'s, unchanged. Stdlib + pytest only (DEC-012).
"""

import pytest

from clipping.providers import gencache
from clipping.providers.generation import GenRequest
from clipping.providers.registry import Link

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
SEEDANCE = Link("fal", "seedance-1-pro-fast")


@pytest.fixture
def keyframe(tmp_path):
    path = tmp_path / "shot_01.png"
    path.write_bytes(PNG)
    return path


def clip(keyframe, **kw):
    kw.setdefault("prompt", "the kiwi turns to the camera, slow push in")
    kw.setdefault("negative", "morphing, flicker")
    kw.setdefault("seed", 11)
    kw.setdefault("duration_s", 5)
    kw.setdefault("fps", 24)
    kw.setdefault("native_audio", False)
    return GenRequest(kind="video", width=720, height=1280, references=(str(keyframe),), **kw)


def test_a_clip_has_a_key_that_moves_with_what_is_bought(keyframe, tmp_path):
    key = gencache.request_key("video", SEEDANCE, clip(keyframe))
    assert key is not None and len(key) == 64

    # The same clip, its keyframe elsewhere and its answer written elsewhere: the same key.
    same = tmp_path / "copy.png"
    same.write_bytes(PNG)
    assert gencache.request_key("video", SEEDANCE, clip(same, out_dir="/tmp/x", extra={"name": "shot_01"})) == key
    assert gencache.request_key("video", SEEDANCE, clip(keyframe, duration_s=5.0)) == key

    for change in ({"duration_s": 6}, {"prompt": "the kiwi waves"}, {"native_audio": True}, {"fps": 16},
                   {"seed": 12}, {"negative": ""}):
        assert gencache.request_key("video", SEEDANCE, clip(keyframe, **change)) != key, change
    assert gencache.request_key("video", Link("gemini", "veo-3.1-lite"), clip(keyframe)) != key
    keyframe.write_bytes(PNG + b"retouched")
    assert gencache.request_key("video", SEEDANCE, clip(keyframe)) != key


# Computed on the code before video joined the key (7fd67ac) and pinned: a
# stored image or voice line must keep its key, or it is generated -- and,
# on a paid link, bought -- again.
PINNED = {
    "image": "3414b3760782bcd9b4c8a4715971970ab46c2685220ea58181669175030acccb",
    "image_edit": "4f67caa0448283d30d18deec7cb0aba00a39884763d60405d5e395d14faada6c",
    "tts": "3b527f7b5e5d09d9d632f73f4c0f169a0e9eb4aab5fefcf981025557bec0a0e2",
}


def test_the_image_and_voice_keys_are_the_ones_computed_before_video_joined(keyframe):
    image = GenRequest(kind="image", prompt="an anthropomorphic kiwi in a linen shirt", negative="text, watermark",
                       seed=7, width=720, height=1280, out_dir="/tmp/a",
                       extra={"template": "t2i_flux2_klein", "name": "shot_01"})
    edit = GenRequest(kind="image_edit", prompt="the kiwi, turning", negative="blurry", seed=3,
                      references=(str(keyframe),), out_dir="/tmp/b", extra={"name": "shot_02"})
    line = GenRequest(kind="tts", text="Bonjour.", voice="fr-FR-HenriNeural", out_dir="/tmp/c",
                      extra={"rate": "+5%", "pitch": "+2Hz", "take": 2, "name": "line_01"})
    assert {
        "image": gencache.request_key("image", Link("fal", "flux-schnell"), image),
        "image_edit": gencache.request_key("image_edit", Link("fal", "seedream-4-edit"), edit),
        "tts": gencache.request_key("tts", Link("edge", "fr-FR-HenriNeural"), line),
    } == PINNED

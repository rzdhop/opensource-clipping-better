"""The clip's size in its cache key (AI Story phase 7 stage 4, DEC-227).

A v2 story may ask seedance for 1080p clips (``generation_profile.
video_resolution``, the per-story switch of the human's answer 3): a 1080p
clip is another purchase than a 720p one, so its key must differ -- while
every 720p clip, the only size bought before this stage, keeps the very key
it was journaled under (DEC-207: a stored clip is never bought twice).
Stdlib + pytest only (DEC-012).
"""

from clipping.providers import gencache
from clipping.providers.generation import GenRequest
from clipping.providers.registry import Link

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
SEEDANCE = Link("fal", "seedance-1-pro-fast")

# tests/test_gencache_video.py's clip, keyed by the code of HEAD 9032d05 (before
# the resolution existed): the key every stored 720p clip of that shape carries.
PINNED_720P_KEY = "796eb1d037ecb183216d6bf77c120b544da4e5e09b1b02d1d87534a7ba9dc512"


def _clip(keyframe, **extra):
    return GenRequest(kind="video", width=720, height=1280, references=(str(keyframe),),
                      prompt="the kiwi turns to the camera, slow push in", negative="morphing, flicker", seed=11,
                      duration_s=5, fps=24, native_audio=False, extra=extra or None)


def test_720p_key_unchanged_1080p_differs(tmp_path):
    keyframe = tmp_path / "shot_01.png"
    keyframe.write_bytes(PNG)

    assert gencache.request_key("video", SEEDANCE, _clip(keyframe)) == PINNED_720P_KEY
    assert gencache.request_key("video", SEEDANCE, _clip(keyframe, resolution="720p")) == PINNED_720P_KEY
    assert "resolution" not in gencache.key_payload("video", SEEDANCE, _clip(keyframe, resolution="720p"))

    hd = gencache.request_key("video", SEEDANCE, _clip(keyframe, resolution="1080p"))
    assert hd is not None and hd != PINNED_720P_KEY
    assert gencache.key_payload("video", SEEDANCE, _clip(keyframe, resolution="1080p"))["resolution"] == "1080p"
    # An image request's key never reads it (RC-V2).
    image = GenRequest(kind="image", prompt="a kiwi", width=720, height=1280, seed=3)
    assert gencache.request_key("image", Link("fal", "flux-schnell"), image) == gencache.request_key(
        "image", Link("fal", "flux-schnell"), GenRequest(kind="image", prompt="a kiwi", width=720, height=1280,
                                                         seed=3, extra={"resolution": "1080p"}))


def test_a_resolution_switch_makes_the_clip_request_hash_move():
    """Phase 7 stage 4 (DEC-227): a clip bought at 720p is not current for a
    story switched to 1080p. The hash of a 720p request is today's hash, so
    every stored clip stays current."""
    from clipping.aistory.steps import clips

    old = clips.clip_prompt_hash("a prompt", "a negative", native_audio=False)
    assert clips.clip_prompt_hash("a prompt", "a negative", native_audio=False, resolution="720p") == old
    assert clips.clip_prompt_hash("a prompt", "a negative", native_audio=False, resolution="1080p") != old

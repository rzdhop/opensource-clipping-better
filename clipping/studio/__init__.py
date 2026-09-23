"""
clipping.studio — Video Rendering Engine

The package's public API, re-exported from its modules.

This used to be clipping/studio.py, sitting beside this directory and
shadowing it: `import clipping.studio.core` failed with "not a package", so
every module here loaded its siblings by file path instead -- a dozen private
copies of each, none shared. The names below are unchanged.
"""

FIREFOX_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0"
)

from . import helpers as _helpers  # noqa: E402  (FIREFOX_UA is defined first, as before)
from . import ffmpeg_utils as _ffmpeg_utils  # noqa: E402
from . import core as _core  # noqa: E402

format_seconds = _helpers.format_seconds
escape_ffmpeg_filter_value = _helpers.escape_ffmpeg_filter_value
detect_video_encoder = _ffmpeg_utils.detect_video_encoder
get_ts_encode_args = _ffmpeg_utils.get_ts_encode_args
get_mp4_encode_args = _ffmpeg_utils.get_mp4_encode_args
open_ffmpeg_video_writer = _ffmpeg_utils.open_ffmpeg_video_writer
build_ffmpeg_progress_cmd = _ffmpeg_utils.build_ffmpeg_progress_cmd
run_ffmpeg_with_progress = _ffmpeg_utils.run_ffmpeg_with_progress

get_face_detector = _core.get_face_detector
estimate_speaker_count_from_video = _core.estimate_speaker_count_from_video
download_google_font = _core.download_google_font
register_fonts_for_libass = _core.register_fonts_for_libass
siapkan_font_tipografi = _core.siapkan_font_tipografi
get_local_bgm_file = _core.get_local_bgm_file
build_bgm_filter = _core.build_bgm_filter
download_pexels_broll = _core.download_pexels_broll
crop_center_broll = _core.crop_center_broll
buat_video_hybrid = _core.buat_video_hybrid
buat_file_ass = _core.buat_file_ass
siapkan_glitch_video = _core.siapkan_glitch_video
download_transition_raw = _core.download_transition_raw
download_all_transitions = _core.download_all_transitions
get_random_transition = _core.get_random_transition
prepare_transition_clip = _core.prepare_transition_clip
TMP_TRANSITION_POOL = _core.TMP_TRANSITION_POOL
buat_thumbnail = _core.buat_thumbnail
buat_video_split_screen = _core.buat_video_split_screen
buat_video_camera_switch = _core.buat_video_camera_switch
_get_render_dims = _core._get_render_dims
_is_vertical_ratio = _core._is_vertical_ratio
proses_klip = _core.proses_klip

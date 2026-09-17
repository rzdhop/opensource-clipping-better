"""
General helper utilities for Studio rendering workflow.
"""


def format_seconds(seconds):
    """
    Format a duration in seconds into HH:MM:SS.

    Args:
        seconds: Numeric duration in seconds.

    Returns:
        Duration string in `HH:MM:SS` format, clamped to non-negative.
    """
    seconds = max(0, int(seconds))
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def escape_ffmpeg_filter_value(value: str) -> str:
    """
    Escape a filesystem path so it is safe inside an FFmpeg filter expression.

    Every caller passes ``os.path.abspath(...)``, so this is a path escaper.

    Two things matter here, and both were established by testing forms against
    ffmpeg directly rather than by reasoning about the docs:

    1. Backslashes are converted to forward slashes rather than escaped. FFmpeg
       accepts forward slashes on Windows, and escaping the separators instead
       leaves libass with a mangled path.
    2. The colon is escaped with *two* backslashes, not one. A filter option
       value is unescaped twice on its way in -- once when the filtergraph is
       split on ``:``, once by the filter itself -- so a single backslash is
       consumed by the first pass and the drive letter still terminates the
       option. ``C\\\\:/Users/...`` survives both passes.

    On POSIX this is a no-op: those paths contain neither backslashes nor
    colons, so the output is byte-identical to the previous behaviour.

    Args:
        value: Raw path to place inside an FFmpeg filter string.

    Returns:
        Escaped value string for FFmpeg filter usage.
    """
    return (
        str(value)
        .replace("\\", "/")
        .replace(":", "\\\\:")
        .replace("'", r"\'")
    )


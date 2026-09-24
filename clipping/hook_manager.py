import os
import shutil
import time

# A hook is a few seconds of video. Anything this large is not one, and an
# unbounded download from an arbitrary URL can fill the disk.
MAX_HOOK_BYTES = 500 * 1024 * 1024
# requests' timeout bounds each socket operation, not the download: a server
# that drips a byte every 59s never trips (10, 60). The deadline bounds the whole.
HOOK_TIMEOUT = (10, 60)
HOOK_DEADLINE_SECONDS = 300


def _download_direct(source: str, local_path: str) -> bool:
    """Stream *source* to *local_path*. Returns False (and leaves no file) when
    the server refuses, the file is too large, or the deadline passes."""
    import requests

    deadline = time.monotonic() + HOOK_DEADLINE_SECONDS
    written = 0
    reason = None
    with requests.get(source, stream=True, timeout=HOOK_TIMEOUT) as r:
        if r.status_code != 200:
            print(f"⚠️ Custom hook download failed: HTTP {r.status_code} from {source}")
            return False
        with open(local_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_HOOK_BYTES:
                    reason = f"it is larger than {MAX_HOOK_BYTES // (1024 * 1024)} MB"
                    break
                if time.monotonic() > deadline:
                    reason = f"it took longer than {HOOK_DEADLINE_SECONDS}s"
                    break
                f.write(chunk)
    if reason:
        os.remove(local_path)
        print(f"⚠️ Custom hook refused: {reason} ({source})")
        return False
    return written > 0


def download_custom_hook(cfg) -> str | None:
    """
    Download or copy a single custom hook file from the --hook-source argument.
    Returns: absolute path to the local file, or None if it failed/doesn't exist.
    """
    source = cfg.hook_source
    if not source:
        return None

    cache_dir = os.path.join(cfg.outputs_dir, "hooks_cache")
    os.makedirs(cache_dir, exist_ok=True)
    local_path = os.path.join(cache_dir, "custom_hook_override.mp4")

    # 1. Download if URL
    if source.startswith("http"):
        print(f"📥 Downloading single custom hook from: {source}")
        # The file name is fixed, so a previous run's hook is sitting here. If
        # this download fails, the existence checks below must find nothing
        # rather than hand back the old clip as this run's hook.
        if os.path.exists(local_path):
            os.remove(local_path)

        try:
            import gdown
            # Gdown download for a single file
            file_id = source.split('/d/')[1].split('/')[0] if '/d/' in source else source
            download_url = f'https://drive.google.com/uc?id={file_id}'
            gdown.download(download_url, local_path, quiet=False)
        except Exception as e:
            # Not a Drive file (or gdown is missing): a direct link still gets
            # its chance below instead of the whole download being abandoned.
            print(f"   ℹ️ Not a Google Drive download ({type(e).__name__}: {e}); trying a direct link.")
            if os.path.exists(local_path):
                os.remove(local_path)  # whatever gdown left behind is partial

        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            print(f"   ✅ Custom hook successfully downloaded to {local_path}")
            return local_path

        # Fallback, maybe it's not a google drive ID but a direct .mp4 link
        try:
            if _download_direct(source, local_path):
                print(f"   ✅ Custom hook successfully downloaded to {local_path}")
                return local_path
        except Exception as e:
            print(f"⚠️ Failed to download custom hook: {type(e).__name__}: {e}")
            if os.path.exists(local_path):
                os.remove(local_path)
        return None
    else:
        # It's a local path
        if not os.path.exists(source):
            print(f"⚠️ Local hook source not found: {source}")
            return None

        print(f"📥 Using local hook file: {source}")
        try:
            shutil.copy(source, local_path)
            return local_path
        except shutil.SameFileError:
            return source
        except Exception as e:
            print(f"⚠️ Failed to copy local hook file: {e}")
            return source

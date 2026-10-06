"""The build-time patch that makes the worker-comfyui handler return audio.

docker/worker-comfyui-tts/patch_handler.py rewrites runpod-workers/worker-comfyui's
/handler.py so the `audio` key of a node output (core SaveAudio) is collected like
`images` and returned under its own key. The image build is the human's (CI), so
this test is what proves, offline, that the patch still applies to the handler it
targets and does what it says.

The fixture below is the output-collection loop and the final_result assembly of
handler(job) at tag 5.10.0, verbatim, wrapped in a minimal function (the websocket
and queueing code between them is not needed). The execution test execs the
patched fixture with a fake get_image_data and no BUCKET_ENDPOINT_URL (the
base64 branch).
"""

import base64
import importlib.util
import os
import tempfile
from pathlib import Path

import pytest

_PATCH = (
    Path(__file__).resolve().parent.parent
    / "docker"
    / "worker-comfyui-tts"
    / "patch_handler.py"
)
_spec = importlib.util.spec_from_file_location("patch_handler", _PATCH)
patch_handler = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(patch_handler)

_HEADER = '''\
def handler(job_id, outputs):
    output_data = []
    errors = []
    try:
'''

_LOOP = r"""        print(f"worker-comfyui - Processing {len(outputs)} output nodes...")
        for node_id, node_output in outputs.items():
            if "images" in node_output:
                print(
                    f"worker-comfyui - Node {node_id} contains {len(node_output['images'])} image(s)"
                )
                for image_info in node_output["images"]:
                    filename = image_info.get("filename")
                    subfolder = image_info.get("subfolder", "")
                    img_type = image_info.get("type")

                    # skip temp images
                    if img_type == "temp":
                        print(
                            f"worker-comfyui - Skipping image {filename} because type is 'temp'"
                        )
                        continue

                    if not filename:
                        warn_msg = f"Skipping image in node {node_id} due to missing filename: {image_info}"
                        print(f"worker-comfyui - {warn_msg}")
                        errors.append(warn_msg)
                        continue

                    image_bytes = get_image_data(filename, subfolder, img_type)

                    if image_bytes:
                        file_extension = os.path.splitext(filename)[1] or ".png"

                        if os.environ.get("BUCKET_ENDPOINT_URL"):
                            try:
                                with tempfile.NamedTemporaryFile(
                                    suffix=file_extension, delete=False
                                ) as temp_file:
                                    temp_file.write(image_bytes)
                                    temp_file_path = temp_file.name
                                print(
                                    f"worker-comfyui - Wrote image bytes to temporary file: {temp_file_path}"
                                )

                                print(f"worker-comfyui - Uploading {filename} to S3...")
                                s3_url = rp_upload.upload_image(job_id, temp_file_path)
                                os.remove(temp_file_path)  # Clean up temp file
                                print(
                                    f"worker-comfyui - Uploaded {filename} to S3: {s3_url}"
                                )
                                # Append dictionary with filename and URL
                                output_data.append(
                                    {
                                        "filename": filename,
                                        "type": "s3_url",
                                        "data": s3_url,
                                    }
                                )
                            except Exception as e:
                                error_msg = f"Error uploading {filename} to S3: {e}"
                                print(f"worker-comfyui - {error_msg}")
                                errors.append(error_msg)
                                if "temp_file_path" in locals() and os.path.exists(
                                    temp_file_path
                                ):
                                    try:
                                        os.remove(temp_file_path)
                                    except OSError as rm_err:
                                        print(
                                            f"worker-comfyui - Error removing temp file {temp_file_path}: {rm_err}"
                                        )
                        else:
                            # Return as base64 string
                            try:
                                base64_image = base64.b64encode(image_bytes).decode(
                                    "utf-8"
                                )
                                # Append dictionary with filename and base64 data
                                output_data.append(
                                    {
                                        "filename": filename,
                                        "type": "base64",
                                        "data": base64_image,
                                    }
                                )
                                print(f"worker-comfyui - Encoded {filename} as base64")
                            except Exception as e:
                                error_msg = f"Error encoding {filename} to base64: {e}"
                                print(f"worker-comfyui - {error_msg}")
                                errors.append(error_msg)
                    else:
                        error_msg = f"Failed to fetch image data for {filename} from /view endpoint."
                        errors.append(error_msg)

            # Check for other output types
            other_keys = [k for k in node_output.keys() if k != "images"]
            if other_keys:
                warn_msg = (
                    f"Node {node_id} produced unhandled output keys: {other_keys}."
                )
                print(f"worker-comfyui - WARNING: {warn_msg}")
                print(
                    f"worker-comfyui - --> If this output is useful, please consider opening an issue on GitHub to discuss adding support."
                )
"""

_MIDDLE = '''\
    except Exception as e:
        return {"error": f"An unexpected error occurred: {e}"}

'''

_FINAL = r"""    final_result = {}

    if output_data:
        final_result["images"] = output_data

    if errors:
        final_result["errors"] = errors
        print(f"worker-comfyui - Job completed with errors/warnings: {errors}")

    if not output_data and errors:
        print(f"worker-comfyui - Job failed with no output images.")
        return {
            "error": "Job processing failed",
            "details": errors,
        }
    elif not output_data and not errors:
        print(
            f"worker-comfyui - Job completed successfully, but the workflow produced no images."
        )
        final_result["status"] = "success_no_images"
        final_result["images"] = []

    print(f"worker-comfyui - Job completed. Returning {len(output_data)} image(s).")
    return final_result"""

HANDLER_5_10_0_EXCERPT = _HEADER + _LOOP + "\n" + _MIDDLE + _FINAL + "\n"


def _run(source, outputs, fetched=None):
    """Exec a (patched) fixture and call it; returns (result, fetched filenames)."""
    fetched = [] if fetched is None else fetched

    def get_image_data(filename, subfolder, image_type):
        fetched.append((filename, subfolder, image_type))
        return b"bytes:" + filename.encode()

    ns = {
        "os": os,
        "base64": base64,
        "tempfile": tempfile,
        "get_image_data": get_image_data,
        "rp_upload": None,
    }
    exec(compile(source, "<handler-fixture>", "exec"), ns)
    return ns["handler"]("job-1", outputs), fetched


def test_patch_compiles_and_names_both_keys():
    patched = patch_handler.patch_source(HANDLER_5_10_0_EXCERPT)
    compile(patched, "<patched>", "exec")
    assert patch_handler.MARKER in patched
    assert 'final_result["images"] = output_data' in patched
    assert 'final_result["audio"] = audio_data' in patched


def test_patch_is_idempotent():
    once = patch_handler.patch_source(HANDLER_5_10_0_EXCERPT)
    assert patch_handler.patch_source(once) == once


def test_unexpected_handler_fails_loudly_naming_the_anchor():
    with pytest.raises(SystemExit) as exc:
        patch_handler.patch_source("def handler(job):\n    return {}\n")
    assert "output loop header" in str(exc.value)
    assert "for node_id, node_output in outputs.items()" in str(exc.value)


def test_patched_handler_returns_images_and_audio_like_images():
    patched = patch_handler.patch_source(HANDLER_5_10_0_EXCERPT)
    outputs = {
        "9": {"images": [{"filename": "a.png", "subfolder": "", "type": "output"}]},
        "12": {
            "audio": [
                {"filename": "line.flac", "subfolder": "audio", "type": "output"},
                {"filename": "preview.flac", "subfolder": "", "type": "temp"},
            ]
        },
    }
    result, fetched = _run(patched, outputs)
    assert [i["filename"] for i in result["images"]] == ["a.png"]
    assert result["images"][0]["type"] == "base64"
    assert len(result["audio"]) == 1
    audio = result["audio"][0]
    assert audio["filename"].endswith(".flac")
    assert audio["type"] == "base64"
    assert base64.b64decode(audio["data"]) == b"bytes:line.flac"
    # the temp-typed audio entry is skipped, the others go through /view
    assert ("preview.flac", "", "temp") not in fetched
    assert ("line.flac", "audio", "output") in fetched
    assert "status" not in result


def test_audio_only_job_is_not_the_empty_case_but_nothing_at_all_is():
    patched = patch_handler.patch_source(HANDLER_5_10_0_EXCERPT)
    audio_only = {
        "12": {"audio": [{"filename": "line.flac", "subfolder": "", "type": "output"}]}
    }
    result, _ = _run(patched, audio_only)
    assert "status" not in result
    assert len(result["audio"]) == 1 and "images" not in result

    # an output with neither kind still reports the stock empty case
    result, _ = _run(patched, {"3": {"text": ["nothing to collect"]}})
    assert result["status"] == "success_no_images"
    assert result["images"] == []
    assert "audio" not in result


def test_stock_handler_drops_audio_which_is_why_the_patch_exists():
    """Guards the fixture: unpatched, the same outputs lose the audio."""
    result, _ = _run(
        HANDLER_5_10_0_EXCERPT,
        {"12": {"audio": [{"filename": "line.flac", "subfolder": "", "type": "output"}]}},
    )
    assert "audio" not in result
    assert result["status"] == "success_no_images"

# VISION — rzdhop's clips (a fork of OpenSource Clipping)

## Purpose
Convert long-form video (podcasts, talks, streams) into vertical short-form
clips with burned-in karaoke subtitles, face-tracked framing, B-roll, BGM and
platform-ready metadata.

## Business context
The project is published open-source and is used primarily through Colab/Kaggle
notebooks and a small web Studio UI. Its users do not control the machine it
runs on, and frequently have no working CUDA stack.

## The shift this work serves
The pipeline was built as a monolith that acquires its own media at run time and
transcribes in-process. Both assumptions fail in the environments users actually
have:

- `yt-dlp` ingestion is broken by YouTube anti-bot telemetry, datacenter-IP
  bans and JS PoW challenges.
- `faster-whisper` / CTranslate2 breaks on CUDA mismatch and silently falls back
  to CPU, where a 20-minute video takes ~12 hours.

The engine is therefore being decoupled into a **local-first** tool: external
tools acquire the `.mp4` and `.vtt`; the engine ingests local paths, skips
Whisper entirely when a transcript is supplied, analyses with a chain of free
hosted LLM providers (Groq, Gemini, NVIDIA), and renders through the
FFmpeg/OpenCV layer.

## Where it stands (2026-09-23)
A comparison with the upstream project found the fork well ahead on analysis
and the Studio, behind on onboarding, and never having touched two inherited
areas. Those were then fixed: the notebooks and docs run and describe this
fork; a job can be cancelled and deleted for real; the render layer is a real
package with its per-frame and per-clip waste removed, and loudness levelling
is available. The render layer is no longer off-limits, but every change to it
must prove frame parity on a real render.

Next, in order: CI coverage of the web layer; tests for the render layer's pure
logic; download-all and per-clip re-edit in the dashboard; multi-platform
ingest through the PC helper; a decision on upload guardrails; a dependency
pass (extras, lockfile, audit).

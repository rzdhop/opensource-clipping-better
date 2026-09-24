# VISION — OpenSource Clipping

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
Whisper entirely when a transcript is supplied, uses an ordered chain of hosted
LLM providers for semantic analysis (Gemini, OpenRouter, ... with NVIDIA NIM as
the floor), and renders through the existing FFmpeg/OpenCV layer unchanged.

The render layer (Layer 4) is explicitly **not** part of this change.

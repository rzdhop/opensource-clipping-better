# 🚀 Getting Started

This guide walks you through setting up rzdhop AI on your local machine.

---

## 📋 Prerequisites

| Requirement | Details |
|---|---|
| **Python** | 3.10 or higher |
| **FFmpeg** | Must be installed and available in PATH |
| **GPU (CUDA)** | Recommended for Whisper transcription (CPU fallback available) |
| **Groq or Gemini API Key** | **At least one is required.** Both are free with no card: [Groq](https://console.groq.com/keys) (fastest, first in the chain) or [Gemini](https://aistudio.google.com/apikey) |
| **NVIDIA NIM API Key** | Optional backup — [Get one here](https://build.nvidia.com/). Too slow to carry a job alone, so a job with only this key is refused unless you pass `--allow-slow-chain` |
| **Pexels API Key** | Optional, for B-roll footage — [Get one here](https://www.pexels.com/api/) |
| **HuggingFace Token** | Optional, for split-screen / camera-switch — [Get one here](https://huggingface.co/settings/tokens) |

> **Note:** If you plan to use split-screen or camera-switch podcast modes, you must also accept the [Pyannote model agreement](https://huggingface.co/pyannote/speaker-diarization-3.1) on HuggingFace.

---

## 📥 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/rzdhop/opensource-clipping-better.git rzdhop-ai
cd rzdhop-ai
```

### 2. Install Dependencies

Choose one of the following methods:

```bash
# Using pip (standard)
pip install -r requirements.txt

# Using uv (faster alternative)
uv sync
```

### 3. Set Up API Keys

```bash
# Copy the template
cp .env.example .env
```

Then edit the `.env` file. The analysis runs on a chain of free providers, and
it needs **at least one of Groq or Gemini** -- a job whose only key is NVIDIA's
is refused before it starts, because NVIDIA's free tier is too slow to carry
one on its own. All three are free with no card:

```env
GROQ_API_KEY=your-groq-key-here              # https://console.groq.com/keys
GOOGLE_API_KEY=your-gemini-key-here          # https://aistudio.google.com/apikey
NVIDIA_API_KEY=your-nvidia-key-here          # Optional backup: https://build.nvidia.com/
PEXELS_API_KEY=your-pexels-key-here          # Optional (B-roll)
HF_TOKEN=your-huggingface-token-here         # Optional (podcast modes)
```

`.env.example` documents every other setting (the chain order, the API token,
server-side downloads).

### 4. Run Your First Clip

```bash
python main.py --video talk.mp4 --transcript talk.vtt
```

That's it! The pipeline will:
1. Download the video
2. Transcribe it with Whisper
3. Analyze it with Gemini AI
4. Generate highlight clips with subtitles, thumbnails, and metadata

---

## 📁 Output Structure

All generated files are saved in the `outputs/` directory:

```text
outputs/
└── <video_hash>/
    ├── highlight_rank_1_ready.mp4    # Final rendered clip (Rank 1)
    ├── highlight_rank_2_ready.mp4    # Final rendered clip (Rank 2)
    ├── thumbnail_rank_1.jpg          # Auto-generated thumbnail
    ├── thumbnail_rank_2.jpg
    ├── render_manifest.json          # Manifest with metadata for all clips
    ├── metadata_preview.json         # Gemini-generated metadata
    ├── gemini_response.json          # Raw AI response (for debugging)
    └── video_asli.mp4                # Downloaded source video
```

---

## 🎯 Quick Examples

### Standard Clipping (7 clips, vertical)
```bash
python main.py --video talk.mp4 --transcript talk.vtt --clips 7 --ratio "9:16"
```

### Landscape Output (YouTube format)
```bash
python main.py --video talk.mp4 --transcript talk.vtt --ratio "16:9" --clips 5
```

### Podcast with Split-Screen
```bash
python main.py --video podcast.mp4 --transcript podcast.vtt --split-screen --dynamic-split --split-trigger face
```

### No Subtitles, No BGM (Clean output)
```bash
python main.py --video talk.mp4 --transcript talk.vtt --no-subs --no-bgm --no-broll
```

---

## 🌐 Supported Video Sources

| Platform | Flag | Example |
|---|---|---|

---

## ⬆️ Next Steps

- 📖 Read the full **[CLI Reference](CLI-Reference)** for all available options
- 🎙️ Learn about **[Podcast Modes](Podcast-Modes)** for multi-speaker content
- ☁️ No GPU? Check the **[Google Colab Guide](Google-Colab-Guide)**

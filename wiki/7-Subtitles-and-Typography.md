# 📝 Subtitles & Typography

rzdhop's clips generates word-by-word karaoke-style subtitles using the `.ASS` subtitle format, with support for kinetic typography and multiple font presets.

---

## Subtitle System Overview

The subtitle pipeline works as follows:

1. **Transcription** — Faster-Whisper generates word-level timestamps
2. **Grouping** — Words are grouped into subtitle chunks (default: 5 words per group)
3. **ASS Generation** — `.ASS` subtitle file is created with karaoke timing
4. **Rendering** — Subtitles are burned into the video via FFmpeg

---

## Font Styles

Four preset font styles are available:

| Style | Main Font | Emphasis Font | Best For |
|---|---|---|---|
| `HORMOZI` (default) | Montserrat | Anton | Business / motivational content |
| `STORYTELLER` | Inter | Lora | Narrative / storytelling |
| `CINEMATIC` | Roboto | Bebas Neue | Film / dramatic content |
| `DEFAULT` | Montserrat Black | Montserrat Medium | General purpose |

```bash
# Use Cinematic style
python main.py --video talk.mp4 --transcript talk.vtt --font-style CINEMATIC

# Use Storyteller style
python main.py --video talk.mp4 --transcript talk.vtt --font-style STORYTELLER
```

> **Note:** All fonts are auto-downloaded on first run. No manual font installation is needed.

---

## Karaoke Effect

By default, subtitles use a **karaoke highlight effect** where each word lights up (changes color) as it's spoken — similar to the style popularized by Alex Hormozi and Veed.io.

```bash
# Default: karaoke highlight enabled
python main.py --video talk.mp4 --transcript talk.vtt

# Disable karaoke (use clean text instead)
python main.py --video talk.mp4 --transcript talk.vtt --no-karaoke

# Disable all subtitles
python main.py --video talk.mp4 --transcript talk.vtt --no-subs
```

---

## Words Per Subtitle

Control how many words appear on screen at once:

```bash
# Default: 5 words per subtitle group
python main.py --video talk.mp4 --transcript talk.vtt --words-per-sub 5

# Fewer words (faster reading, more subtitle changes)
python main.py --video talk.mp4 --transcript talk.vtt --words-per-sub 3

# More words (slower reading, fewer changes)
python main.py --video talk.mp4 --transcript talk.vtt --words-per-sub 7
```

---

## Kinetic Typography

Advanced text animation with bounce/stagger effects and word scaling:

```bash
# Enable kinetic typography on main clip
python main.py --video talk.mp4 --transcript talk.vtt --advanced-text

# Enable kinetic typography on hook teaser only
python main.py --video talk.mp4 --transcript talk.vtt --advanced-text-hook

# Enable on both
python main.py --video talk.mp4 --transcript talk.vtt --advanced-text --advanced-text-hook
```

### What Kinetic Typography Does

- **Word Scaling** — Emphasis words appear larger with a bounce animation
- **Dual-Font System** — Important words use the emphasis font, regular words use the main font
- **Stagger Animation** — Words appear sequentially with slight delays

---

## Subtitle Positioning

Subtitle position is automatically adjusted based on the output aspect ratio:

| Ratio | Alignment | Margin | Font Size |
|---|---|---|---|
| `9:16` (Vertical) | Bottom-center | 450px from bottom | 90pt |
| `16:9` (Landscape) | Bottom-center | 70px from bottom | 80pt |
| Split-Screen | Centered vertically | Auto-adjusted | Scaled |

---

## Transcription Options

### Whisper Settings

```bash
# Use a smaller/faster model
python main.py --video talk.mp4 --transcript talk.vtt --whisper-model medium

# Force CPU (if no CUDA GPU)
python main.py --video talk.mp4 --transcript talk.vtt --whisper-device cpu

# Use int8 for lower VRAM usage
python main.py --video talk.mp4 --transcript talk.vtt --whisper-compute-type int8

# Use float32 for Kaggle compatibility
python main.py --video talk.mp4 --transcript talk.vtt --whisper-compute-type float32
```

### YouTube Built-in Subtitles

Skip Whisper entirely by using YouTube's own subtitles:

```bash
python main.py --video talk.mp4 --transcript talk.vtt --transcript talk.vtt
```

This can significantly speed up processing. If YouTube subtitles are not available, the system automatically falls back to Whisper.

> **Note:** `--transcript` accepts `.vtt`, `.srt` and `.json3`. YouTube auto-caption VTTs carry per-word timing tags, which give the most accurate karaoke; other transcripts fall back to dividing each cue evenly across its words.

---

## Common Combinations

```bash
# Clean video without subtitles
python main.py --video talk.mp4 --transcript talk.vtt --no-subs

# Clean text (no karaoke highlight)
python main.py --video talk.mp4 --transcript talk.vtt --no-karaoke

# Maximum subtitle quality
python main.py --video talk.mp4 --transcript talk.vtt --font-style HORMOZI --words-per-sub 4 --advanced-text

# Fast processing (skip Whisper)
python main.py --video talk.mp4 --transcript talk.vtt --transcript talk.vtt --no-karaoke
```

---

## See Also

- [CLI Reference](CLI-Reference) — All subtitle-related flags
- [Video Quality & Rendering](Video-Quality-and-Rendering) — Output quality settings

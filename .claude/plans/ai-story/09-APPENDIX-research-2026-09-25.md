# Appendix — provider pricing and prior art (researched 2026-09-25)

Facts below were read from the linked pages on 2026-09-25. Prices change monthly; the
code keeps them in `clipping/providers/pricing.py` with `PRICES_AS_OF` and this file is
the source to update from. "Free" means a recurring allowance, not a one-off trial.

## A. Image generation

| Provider / model | Price ≈1024 px | Free | Multi-reference editing | Source |
|---|---|---|---|---|
| Cloudflare Workers AI `flux-1-schnell` | ≈ $0.0006 / image | **10,000 neurons/day ≈ 170 images/day** | no (t2i) | https://developers.cloudflare.com/workers-ai/platform/pricing/ |
| Pollinations.ai (flux, seedream, gemini image…) | "pollen" credits, $1 ≈ 1 pollen; legacy key 1 pollen/IP/hour | quests / small | some models | https://raw.githubusercontent.com/pollinations/pollinations/master/APIDOCS.md |
| Hugging Face Inference Providers | pass-through | $0.10 / month | via fal/Replicate models | https://huggingface.co/docs/inference-providers/pricing |
| Google Nano Banana 2 Lite (`gemini-3.1-flash-lite-image`) | **$0.0336** (batch $0.0168) | **none** | up to 14 reference images | https://ai.google.dev/gemini-api/docs/pricing |
| Google Nano Banana 2 (`gemini-3.1-flash-image`) | $0.067 (1K) | none | 10 objects + 4 characters | same |
| Google Nano Banana legacy (`gemini-2.5-flash-image`) | $0.039 | none | multi-image edit | same |
| fal `bytedance/seedream/v4/edit` | **$0.03** | — | yes (4+ refs) | https://fal.ai/models/fal-ai/bytedance/seedream/v4/edit |
| fal `flux-pro/kontext` | $0.04 | — | single ref | https://fal.ai/models/fal-ai/flux-pro/kontext |
| fal `flux/schnell` | $0.003 / MP (≈ $0.006 for 1080×1920) | — | no | https://fal.ai/models/fal-ai/flux/schnell |
| Replicate flux-schnell | $0.003 | — | no | https://replicate.com/pricing |
| OpenAI gpt-image-2 (low) | ≈ $0.005 (1024×1536) | none | multi-image edit | https://developers.openai.com/api/docs/pricing |
| Recraft V4.1 Flash via OpenRouter | $0.007 | — | style refs | https://openrouter.ai/recraft/recraft-v4.1-flash |

Key fact: **no Gemini image model is on the free tier**, and no free hosted
multi-reference editor exists → consistency on the free route = local ComfyUI
(FLUX.2 klein 4B, Qwen-Image-Edit) or labelled prompt-only mode.

## B. Video generation (image-to-video), cheapest per 5-second ≈720p clip

| Model | Per 5 s | Native audio | Source |
|---|---|---|---|
| Wan 2.2 Ultra Fast (WaveSpeed) | $0.05 | no | https://wavespeed.ai/pricing |
| **Seedance 1.0 Pro Fast (fal)** | ≈ $0.11 (token-billed) | no | https://fal.ai/models/fal-ai/bytedance/seedance/v1/pro/fast/image-to-video |
| Hailuo H3 Max Turbo (fal) | $0.20 (768p), ≈1.6 s generation | no | https://fal.ai/learn/tools/fastest-ai-video-generation-models |
| **LTX-2 Fast (fal)** | $0.20 (1080p, with audio) — endpoint deprecating to LTX-2.3 | yes | https://fal.ai/models/fal-ai/ltxv-2/image-to-video/fast |
| PixVerse V6 | $0.20 | optional | https://docs.platform.pixverse.ai/pricing-796039m0 |
| Kling 2.5 Turbo Std (fal) | $0.21 | no | https://fal.ai/models/fal-ai/kling-video/v2.5-turbo/standard/image-to-video |
| Runway gen4_turbo | $0.25 | no | https://docs.dev.runwayml.com/guides/pricing/ |
| Veo 3.1 Lite (Gemini API) | $0.25 (720p) | (Veo family: yes) | https://ai.google.dev/gemini-api/docs/pricing |
| Kling 3.0 Std | $0.42–0.50 | yes | https://piapi.ai/kling-api |
| Veo 3.1 Fast | $0.50–0.75 | yes | https://fal.ai/models/fal-ai/veo3.1/fast/image-to-video |

**No recurring free video API exists.** Self-hosting (Wan 2.2 5B/14B, LTX-2, LTX-Video 2B)
on the user's GPU is the only free path.

## C. TTS (multiple distinct voices, FR + EN)

| Service | Price | Free | Notes | Source |
|---|---|---|---|---|
| **Edge TTS** (`edge-tts`) | $0 | unlimited, unofficial | many fr-FR / fr-CA / en voices; one voice per request | https://github.com/rany2/edge-tts |
| **Gemini Flash-Lite TTS** | $0.0015 / 10 s | free tier | 2 speakers per request, 30 voices | https://ai.google.dev/gemini-api/docs/speech-generation |
| Google Cloud TTS | Neural2 $16 / M chars | 1 M chars / month (Neural2) | hundreds of voices | https://cloud.google.com/text-to-speech/pricing |
| Azure Speech | ≈ $7.50 / M chars | 0.5 M chars / month | 400+ voices | https://azure.microsoft.com/en-us/pricing/details/cognitive-services/speech-services/ |
| OpenAI gpt-4o-mini-tts | ≈ $0.015 / min | none | 11 voices, instructable | https://developers.openai.com/api/docs/models/gpt-4o-mini-tts |
| ElevenLabs Flash | $0.05 / 1k chars | 10k credits / month (non-commercial) | best voices | https://elevenlabs.io/pricing/api |
| **Piper** (local) | $0 | — | **6 fr_FR voices**, real-time CPU, MIT | https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR |
| **Kokoro-82M** (local) | $0 | — | 54 voices, **1 French voice**, CPU real-time, Apache-2.0 | https://huggingface.co/hexgrad/Kokoro-82M |
| **Chatterbox Multilingual** (local) | $0 | — | zero-shot voices → unlimited distinct characters, 23 langs incl. FR, MIT | https://github.com/resemble-ai/chatterbox |
| XTTS-v2 | — | — | **do not ship** (non-commercial licence) | https://huggingface.co/coqui/XTTS-v2 |

Free SFX/BGM: Freesound API (60 req/min, 2000/day; per-sound CC licences — filter CC0)
https://freesound.org/docs/api/overview.html. Pixabay's API documents images/videos only.

## D. LLM and vision (free tiers)

| Provider | Free | Source |
|---|---|---|
| Gemini Flash / Flash-Lite | free tier (RPD shown in AI Studio; ≈ 250 / 1000 per third-party snapshots) — also best free **vision** | https://ai.google.dev/gemini-api/docs/pricing |
| Groq (gpt-oss-120b, qwen3.8-27b) | 30 RPM / 1k RPD / 200k TPD | https://console.groq.com/docs/rate-limits |
| OpenRouter `:free` models (12 with vision) | 20 RPM; 50 RPD, or 1000 RPD after ≥ $10 credits ever bought | https://openrouter.ai/docs/api-reference/limits |
| Cerebras | 5 RPM / 1 M TPD | https://inference-docs.cerebras.ai/support/rate-limits |
| Mistral | free tier, limits in console | https://docs.mistral.ai/deployment/laplateforme/tier/ |
| Ollama local | qwen3:4b (2.5 GB) on 8 GB RAM, qwen3:8b on 16 GB; `format=<json schema>` | https://ollama.com/blog/structured-outputs |

## E. Cost of one 60-second episode (cheapest paid path, 2026-09-25)

- Tier 1 (10 images with references + ≈ 60 s of dialogue TTS): 10 × $0.03 (Seedream 4
  edit) + ≈ $0.01 TTS ≈ **$0.31**; **$0.00** on the free route (Cloudflare/Edge/local).
- Tier 2 (10 × 5 s clips): Seedance 1.0 Pro Fast ≈ **$1.10**; LTX-2 Fast with audio
  $2.00; Kling 2.5 Std $2.10; Veo 3.1 Lite $2.50. Tier 1 + Tier 2 ≈ $1.40–2.30.
- **The author's $1 ceiling** (`one_dollar` profile): 20 reference-consistent images on
  Seedream 4 edit ≈ $0.60 + 3 key shots animated on Seedance Fast ≈ $0.33 + free TTS ≈
  **$0.93**; with a local image editor (ComfyUI) the same $1 animates 8–9 shots instead.

## F. Prior art worth studying (patterns, not code)

| Project | Why it matters | Licence |
|---|---|---|
| MoneyPrinterTurbo — https://github.com/harry0703/MoneyPrinterTurbo | audio-driven clip timing, FFmpeg concat without re-encode, many TTS backends | MIT |
| story-flicks — https://github.com/alecm20/story-flicks | closest stack (FastAPI + React), shows the failure mode of independent per-scene prompts (no consistency) | unclear |
| ViMax (HKUDS) — https://github.com/HKUDS/ViMax | character reference bank, first-frame anchoring, explicit consistency-check agent | MIT |
| Open-AI-Micro-Drama-Generator — https://github.com/Anil-matcha/Open-AI-Micro-Drama-Generator | portrait once → Kontext edit for every frame → Kling I2V | MIT |
| LocalMiniDrama — https://github.com/xuanyustudio/LocalMiniDrama | 8-stage pipeline with per-project material library | MIT |
| Pixelle-Video — https://github.com/ATH-MaaS/Pixelle-Video | ComfyUI API-format workflow templates as data | check |
| huobao-drama — https://github.com/chatfire-AI/huobao-drama | `@character` tags resolved to reference images (pattern only) | CC BY-NC-SA — **do not copy code** |
| BigBanana-AI-Director — https://github.com/shuyu-labs/BigBanana-AI-Director | character sheets with wardrobe variants, keyframe pairs (pattern only) | CC BY-NC-SA — **do not copy code** |
| ComfyUI_StoryDiffusion — https://github.com/smthemex/ComfyUI_StoryDiffusion | `[A]/[B]` role tags; SDXL-era, fragile | — |
| glide-ffmpeg — https://github.com/jham2081-blip/glide-ffmpeg | sub-pixel Ken Burns when torch is present | MIT |
| DepthFlow — https://github.com/BrokenSource/DepthFlow | depth parallax (AGPL — optional external tool only) | AGPL-3.0 |

Consistency techniques: multi-reference editors (Nano Banana 2, Seedream 4/4.5, FLUX
Kontext, FLUX.2 klein 4B local Apache-2.0, Qwen-Image-Edit-2509 Apache-2.0); **face-identity
adapters (PuLID, InstantID, InfiniteYou) fail on non-human faces** — not used; per-character
LoRA (fal ≈ $2 / run, FLUX.2 klein trains on 12–16 GB) is a future extension for a series'
main cast.

Genre notes (Fruit Drama / AI persona soaps, 2026): daily sub-60 s episodes; ≈ 6 beats
(recap → 2 rising → 2 peak → cliffhanger); 3–5 s per shot; 80–90 characters of dialogue
per scene; medium two-shot for dialogue, close-up for emotion, push-in on reveals;
cut-to-black at the reveal; "Part N" numbering and reply-to-comments serialization; top
plots: infidelity, inheritance, revenge, secret identity. Sources:
https://www.nbcnews.com/pop-culture/viral/ai-fruit-love-island-tiktok-slop-popularity-rcna264946 ,
https://www.flashloop.app/blog/how-to-make-ai-fruit-drama-videos ,
https://software.informer.com/Stories/the-slop-opera-why-ai-fruit-dramas-took-over-tiktok.html

"""
clipping.voiceover — AI Commentary & TTS Generation Module

Handles generation of commentary scripts using Gemini AI and converts them
to speech using edge-tts. Supports word-level subtitle generation.
"""

import os
import json
import asyncio
# Both dependencies belong to an optional feature (--voiceover), so neither is
# allowed to break `import clipping.voiceover` for everyone else. The functions
# that need them raise a clear error instead.
try:
    from google import genai
    from google.genai import types
except ImportError:  # pragma: no cover - depends on the install
    genai = None
    types = None

try:
    import edge_tts
except ImportError:
    edge_tts = None


# ==============================================================================
# AVAILABLE EDGE-TTS VOICES (REFERENCE)
# ==============================================================================
# List of available edge-tts voices for reference.
# Use the value (voice name string) as the `voice` argument in synthesize_voice().
# Run `edge-tts --list-voices` for the full list.
# GitHub: https://github.com/rany2/edge-tts
# Preview: https://geeksta.net/tools/tts-samples/
# Sample in git: https://github.com/yaph/tts-samples/blob/main/mp3/English/en-US-GuyNeural.mp3

AVAILABLE_VOICES = {
    "id": {
        "male": [
            "id-ID-ArdiNeural",
            "id-ID-GadisNeural",       # Note: despite name, check output
            "id-ID-ArdiNeural",         # Primary Indonesian male
            # Edge-TTS only provides 2 Indonesian voice IDs (Ardi & Gadis).
            # Malay (ms-MY) alternatives can be used for variety:
            "ms-MY-OsmanNeural",        # Malay male (similar to ID)
            "ms-MY-YasminNeural",       # Malay female (similar to ID)
            "jv-ID-DimasNeural",        # Javanese male
        ],
        "female": [
            "id-ID-GadisNeural",        # Primary Indonesian female
            "jv-ID-SitiNeural",         # Javanese female
            "su-ID-TutiNeural",         # Sundanese female
            "ms-MY-YasminNeural",       # Malay female (similar to ID)
            "su-ID-JajangNeural",       # Sundanese (check gender)
        ],
    },
    "en": {
        "male": [
            "en-US-GuyNeural",          # US English male (natural)
            "en-US-ChristopherNeural",  # US English male (formal)
            "en-US-EricNeural",         # US English male (warm)
            "en-GB-RyanNeural",         # British English male
            "en-AU-WilliamNeural",      # Australian English male
        ],
        "female": [
            "en-US-JennyNeural",        # US English female (natural)
            "en-US-AriaNeural",         # US English female (expressive)
            "en-US-MichelleNeural",     # US English female (warm)
            "en-GB-SoniaNeural",        # British English female
            "en-AU-NatashaNeural",      # Australian English female
        ],
    },
}


# ==============================================================================
# TTS SYNTHESIS (EDGE-TTS)
# ==============================================================================

def _word_boundary_kwargs() -> dict:
    """``{"boundary": "WordBoundary"}`` when this edge-tts takes the argument.

    edge-tts 7.2 changed the default to SentenceBoundary, after which the
    stream below saw no WordBoundary chunk at all and every voice-over lost
    its word timings (found on 2026-09-25 by the AI Story TTS chain test).
    Older versions have no such argument and always sent word boundaries.
    """
    import inspect

    try:
        parameters = inspect.signature(edge_tts.Communicate.__init__).parameters
    except (TypeError, ValueError):
        return {}
    return {"boundary": "WordBoundary"} if "boundary" in parameters else {}


async def _synthesize_async(text: str, voice: str, output_audio_path: str, output_subs_path: str = None):
    """Async core for TTS generation."""
    if edge_tts is None:
        raise ImportError("edge-tts is not installed. Run: pip install edge-tts")

    communicate = edge_tts.Communicate(text, voice, **_word_boundary_kwargs())
    submaker = edge_tts.SubMaker()

    with open(output_audio_path, "wb") as file:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                file.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                submaker.feed(chunk)

    if output_subs_path:
        # Save SRT for reference (edge-tts v7+ uses get_srt instead of generate_subs)
        with open(output_subs_path, "w", encoding="utf-8") as file:
            file.write(submaker.get_srt())
            
        # Extract timings directly from submaker.cues instead of parsing text files
        segments = []
        for cue in submaker.cues:
            start_s = cue.start.total_seconds()
            end_s = cue.end.total_seconds()
            word = cue.content
            segments.append({
                "start": start_s,
                "end": end_s,
                "text": word,
                "words": [{
                    "word": word,
                    "start": start_s,
                    "end": end_s,
                    "probability": 1.0
                }]
            })
        return segments
    return []

def synthesize_voice(text: str, voice: str, output_dir: str, clip_id: str) -> tuple[str, list[dict]]:
    """
    Synthesize text to speech using edge-tts.
    
    Returns:
        tuple[str, list[dict]]: (path_to_audio_mp3, list_of_subtitle_segments)
    """
    os.makedirs(output_dir, exist_ok=True)
    audio_path = os.path.join(output_dir, f"vo_clip_{clip_id}.mp3")
    subs_path = os.path.join(output_dir, f"vo_clip_{clip_id}.vtt")
    
    print(f"   🎙️ Synthesizing voice-over ({voice}) for clip {clip_id}...")
    segments = asyncio.run(_synthesize_async(text, voice, audio_path, subs_path))
    
    # We consolidate the word-level segments into 3-word chunks for better subtitle rendering
    consolidated = _consolidate_segments(segments, words_per_seg=3)
    
    return audio_path, consolidated

def _consolidate_segments(raw_segments: list[dict], words_per_seg: int = 3) -> list[dict]:
    """Group word-level VTT segments into chunked segments for better readability."""
    if not raw_segments:
        return []
        
    consolidated = []
    current_chunk = {"start": 0.0, "end": 0.0, "text": "", "words": []}
    word_count = 0
    
    for i, seg in enumerate(raw_segments):
        if not seg["words"]:
            continue
            
        word_data = seg["words"][0]
        
        if word_count == 0:
            current_chunk["start"] = word_data["start"]
            
        current_chunk["words"].append(word_data)
        current_chunk["end"] = word_data["end"]
        word_count += 1
        
        if word_count >= words_per_seg or i == len(raw_segments) - 1:
            current_chunk["text"] = " ".join(w["word"] for w in current_chunk["words"])
            consolidated.append(current_chunk)
            current_chunk = {"start": 0.0, "end": 0.0, "text": "", "words": []}
            word_count = 0
            
    return consolidated


# ==============================================================================
# AI COMMENTARY SCRIPT GENERATION
# ==============================================================================

def generate_commentary_script(transcript_snippet: str, cfg, style="analysis", language="id", length="short") -> str:
    """Generate commentary script using Gemini AI."""
    if genai is None:
        raise RuntimeError(
            "--voiceover requires google-genai. Install it with "
            "`pip install google-genai`, or run without --voiceover."
        )

    print(f"   🧠 Generating {style} commentary script via Gemini ({language}, {length})...")

    api_key = cfg.api_key_gemini
    if not api_key:
        raise ValueError("GOOGLE_API_KEY not found in environment or config.")

    client = genai.Client(api_key=api_key)
    # Imported here, not at module scope: prompts.py is stdlib-only and this
    # module is not, and the one-way dependency is what lets the prompt be
    # tested in an environment where google-genai is absent.
    from clipping.analysis import prompts

    prompt = prompts.commentary_prompt(
        transcript_snippet, style=style, language=language, length=length
    )
    
    gemini_config = types.GenerateContentConfig(
        temperature=0.7,
        top_p=0.9,
    )
    
    model = getattr(cfg, "gemini_model", "gemini-3-flash-preview")
    fallback = getattr(cfg, "gemini_fallback_model", "gemini-2.5-flash")
    
    try:
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=gemini_config
        )
        if response.text:
            script = response.text.strip().strip('"').strip()
            print(f"   ✅ Script generated ({len(script)} chars)")
            return script
    except Exception as e:
        print(f"   ⚠️ Gemini main model failed: {e}. Trying fallback...")
        try:
            response = client.models.generate_content(
                model=fallback,
                contents=prompt,
                config=gemini_config
            )
            if response.text:
                script = response.text.strip().strip('"').strip()
                print(f"   ✅ Script generated via fallback ({len(script)} chars)")
                return script
        except Exception as e2:
            print(f"   ❌ Gemini fallback failed: {e2}")
            
    return ""

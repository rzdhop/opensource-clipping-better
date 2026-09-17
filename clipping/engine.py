"""
clipping.engine — Transcription & AI Analysis

The download layer that used to live here is gone: this pipeline is local-first
and acquires nothing. Media and transcripts arrive as local paths via --video
and --transcript; see clipping.transcript for parsing.
"""

import json
import os
import re
import time

# Transcript parsing lives in clipping.transcript (stdlib-only, so a --transcript
# run never touches the ML stack). Re-exported here because callers reach for
# engine.load_transcript alongside engine.transcribe_video.
from .transcript import (  # noqa: F401
    TranscriptParseError,
    load_transcript,
    parse_vtt_subs,
    parse_youtube_json3_subs,
)

# NOTE: faster_whisper is imported lazily inside transcribe_video(). Importing it
# here would drag CTranslate2/cuDNN into every `import clipping.engine`, which
# defeats the --transcript bypass and makes the test suite require a GPU stack.


# ==============================================================================
# STAGE 2: WHISPER TRANSCRIPTION & JSON3 FALLBACK
# ==============================================================================

def load_whisper_model(
    model_size: str = "large-v3",
    device: str = "auto",
    compute_type: str = "auto",
):
    """Build a Faster-Whisper model.

    Split out so callers that transcribe several files (story mode) can build the
    model once. large-v3 costs ~30s and several GB to load, and it was previously
    rebuilt on every transcribe_video call.

    Device and compute type are resolved here rather than at each call site: the
    CLI, story mode and the web worker all funnel through this function, and
    resolving in one place is what stops the three of them drifting apart.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "faster-whisper is not installed, so in-process transcription cannot "
            "run. Install it with `pip install faster-whisper`, or run "
            "with --transcript <file.vtt> to skip Whisper entirely."
        ) from exc

    from clipping.device import resolve_whisper_runtime

    device, compute_type = resolve_whisper_runtime(device, compute_type)

    print(
        f"      ⏳ Loading Whisper model '{model_size}' ({device}/{compute_type})"
        " — the first download may take a while...",
        flush=True,
    )
    try:
        return WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception as exc:
        # CTranslate2 raises a bare ValueError naming neither the flag to change
        # nor the alternative, which is how this cost the user two failed runs.
        raise RuntimeError(
            f"Whisper could not start on {device}/{compute_type}: {exc}\n"
            "  If this mentions CUDA, the installed CTranslate2 is a CPU-only "
            "build. Run with --whisper-device cpu --whisper-compute-type int8, "
            "or skip Whisper entirely with --transcript <file.vtt>."
        ) from exc


def transcribe_video(
    video_path: str,
    max_words_per_subtitle: int = 5,
    model_size: str = "large-v3",
    device: str = "auto",
    compute_type: str = "auto",
    model=None,
) -> tuple[str, list[dict]]:
    """
    Transcribe *video_path* using Faster-Whisper.

    Returns
    -------
    transkrip_lengkap : str
        Human-readable transcript with timestamps.
    data_segmen : list[dict]
        Word-level segments grouped by *max_words_per_subtitle*.

    Notes
    -----
    Pass *model* to reuse an already-loaded WhisperModel across several files;
    otherwise one is built from *model_size*/*device*/*compute_type*.
    """
    print("[2/3] Starting transcription with Faster-Whisper (Word-Level)...")

    # Faster-whisper produces no output until the first segment, so each phase is
    # announced -- otherwise a first CPU run (model download + full audio decode)
    # looks like a hang.
    if model is None:
        model = load_whisper_model(model_size, device, compute_type)

    print("      ⏳ Decoding audio & extracting features (no output yet)...", flush=True)
    segments, info = model.transcribe(video_path, beam_size=5, word_timestamps=True)

    transkrip_lengkap = ""
    data_segmen: list[dict] = []

    # Progress bar based on audio timestamp. faster-whisper streams segments
    # lazily, so the bar advances to each segment's end time as it arrives.
    from tqdm import tqdm

    total_dur = round(info.duration, 2)
    progress = tqdm(
        total=total_dur,
        unit="s",
        desc="      Transcribing",
        bar_format="{desc}: {percentage:3.0f}%|{bar}| {n:.0f}/{total:.0f}s [{elapsed}<{remaining}]",
    )

    for segment in segments:
        # Clamp so floating-point drift past the duration doesn't overshoot.
        progress.update(min(segment.end, total_dur) - progress.n)
        transkrip_lengkap += f"[{segment.start:.1f} - {segment.end:.1f}] {segment.text}\n"

        if segment.words:
            chunk_words: list[dict] = []
            chunk_start = 0.0

            for i, w in enumerate(segment.words):
                if len(chunk_words) == 0:
                    chunk_start = w.start

                chunk_words.append({
                    "word": w.word.strip(),
                    "start": w.start,
                    "end": w.end,
                })

                if len(chunk_words) == max_words_per_subtitle or i == len(segment.words) - 1:
                    data_segmen.append({
                        "start": chunk_start,
                        "end": w.end,
                        "words": chunk_words,
                    })
                    chunk_words = []

    progress.update(total_dur - progress.n)  # snap to 100% when done
    progress.close()
    return transkrip_lengkap, data_segmen


# ==============================================================================
# STAGE 3: GEMINI AI ANALYSIS
# ==============================================================================

TARGET_ACCOUNTS = {
    "Business": {
        "akun_tujuan": "Business.Mereska",
        "angle_desc": "Kalau angle-nya bisnis, brand, omzet, jualan, founder, marketing, atau UMKM.",
        "bio": "Insight bisnis, founder story & brand lokal. Business | Founder | Finance | Beauty | Marketing"
    },
    "Life": {
        "akun_tujuan": "Life.Mereska",
        "angle_desc": "Kalau angle-nya personal life, lifestyle, skincare, career, mindset, relationship, personal finance, atau self growth.",
        "bio": "Klip insight buat upgrade hidup & mindset. Podcast | Career | Finance | Beauty | Self Growth"
    },
    "Creator": {
        "akun_tujuan": "Creator.Mereska",
        "angle_desc": "Kalau angle-nya konten digital, AI, affiliate, tools, clipping, monetisasi, atau cara menghasilkan uang dari konten.",
        "bio": "Ngulik konten digital biar bisa jadi uang. AI | Affiliate | Clips | Tools | Monetize"
    },
    "Muslim": {
        "akun_tujuan": "Muslim.Mereska",
        "angle_desc": "Kalau angle-nya religi, rezeki, doa, ibadah, kerja karena Allah, keluarga Islami, atau bisnis dengan nilai Islam.",
        "bio": "Reminder kerja, rezeki & hidup bernilai Islam. Islamic | Rezeki | Family | Work | Business"
    }
}

def _build_account_classification_prompt() -> str:
    lines = []
    for tipe, data in TARGET_ACCOUNTS.items():
        lines.append(f"- {tipe}: {data['angle_desc']}")
        lines.append(f"  (akun_tujuan: \"{data['akun_tujuan']}\", bio: \"{data['bio']}\")")
    return "\n".join(lines)


# ---- Retry Config ----
# --- NVIDIA NIM retry policy -------------------------------------------------
# Short backoff on purpose. Gemini's 60s+ linear ladder is tuned for quota
# exhaustion; NVIDIA failures on this path are overwhelmingly malformed JSON,
# and the only remedy for a bad sample is another sample.
NVIDIA_MAX_ATTEMPTS = 3
NVIDIA_BACKOFF_SECONDS = (5, 15)

# Classified by exception class NAME so that `openai` is never imported at module
# scope. An SDK rename would make an unknown error non-retryable, i.e. it fails
# closed, which is the safe direction.
_NVIDIA_RETRYABLE_EXC = {
    "APIConnectionError",
    "APITimeoutError",
    "RateLimitError",
    "InternalServerError",
    "APIError",
}
_NVIDIA_FATAL_EXC = {
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "BadRequestError",
    "UnprocessableEntityError",
}


def _nvidia_is_retryable(exc: Exception) -> bool:
    """Whether *exc* is worth another sample."""
    name = type(exc).__name__

    if name in _NVIDIA_FATAL_EXC:
        # A bad key or a malformed guided_json will never succeed on retry.
        return False

    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status in (408, 409, 429) or status >= 500:
            return True
        if 400 <= status < 500:
            return False

    if isinstance(exc, json.JSONDecodeError):
        return True
    if name in _NVIDIA_RETRYABLE_EXC:
        return True
    # Our own shape validation below raises ValueError; another sample may fix it.
    return isinstance(exc, ValueError)


def _extract_clip_list(content: str) -> list[dict]:
    """Turn a raw NIM response into the clip list, or raise.

    Every failure here is a ValueError or JSONDecodeError, i.e. retryable, so
    the caller's loop can simply resample.
    """
    if not content or not content.strip():
        raise ValueError("NVIDIA returned empty content.")

    content = content.strip()
    if "```" in content:
        content = re.sub(r"```(json)?", "", content).strip()
        content = content.split("```")[0].strip()

    hasil = json.loads(content)

    # guided_json should return the array directly, but non-conforming models
    # wrap it. Keep the unwrapper.
    if isinstance(hasil, dict):
        for key in ("clips", "data", "highlights"):
            if isinstance(hasil.get(key), list):
                hasil = hasil[key]
                break
        else:
            hasil = [hasil]

    if not isinstance(hasil, list):
        raise ValueError(
            f"NVIDIA provider returned a non-list/dict format: {type(hasil)}"
        )
    if not hasil:
        # A schema-conformant empty array used to sail through here and detonate
        # much later inside metadata.normalize_and_validate.
        raise ValueError("NVIDIA returned an empty clip array.")

    required = ("start_time", "end_time")
    for idx, item in enumerate(hasil):
        if not isinstance(item, dict):
            raise ValueError(f"Clip #{idx} is not an object: {type(item)}")
        missing = [k for k in required if k not in item]
        if missing:
            raise ValueError(f"Clip #{idx} is missing required field(s): {missing}")

    return hasil


def _make_nvidia_client(cfg):
    """Build the NIM client. Split out so tests can substitute a fake."""
    from openai import OpenAI

    return OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=cfg.api_key_nvidia,
    )


MAX_ATTEMPTS = 10
INITIAL_WAIT_SECONDS = 60
WAIT_INCREMENT_SECONDS = 30
REQUEST_TIMEOUT_MS = 15 * 60 * 1000  # 15 minutes
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def _extract_status_code(exc: Exception):
    for attr in ("status_code", "code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)

    match = re.search(r"\b(408|429|500|502|503|504)\b", str(exc))
    return int(match.group(1)) if match else None


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, json.JSONDecodeError):
        return True

    code = _extract_status_code(exc)
    if code in RETRYABLE_STATUS_CODES:
        return True

    msg = str(exc).lower()
    keywords = (
        "timeout", "temporarily unavailable", "deadline",
        "connection reset", "connection aborted", "service unavailable",
    )
    return any(k in msg for k in keywords)


def _generate_json_with_retry(client, model, fallback_model, contents, config):
    last_exc = None
    status_code = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"[Gemini] Attempt {attempt}/{MAX_ATTEMPTS}...")

            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )

            text = getattr(response, "text", None)
            if not text or not text.strip():
                raise ValueError("Gemini returned an empty response.text.")

            return json.loads(text)

        except Exception as exc:
            last_exc = exc
            status_code = _extract_status_code(exc)
            retryable = _is_retryable(exc)

            print(
                f"[Gemini] Attempt {attempt}/{MAX_ATTEMPTS} failed | "
                f"status={status_code} | error={exc}"
            )

            if (not retryable) or attempt == MAX_ATTEMPTS:
                break

            wait_seconds = INITIAL_WAIT_SECONDS + ((attempt - 1) * WAIT_INCREMENT_SECONDS)
            print(f"[Gemini] Retrying again in {wait_seconds} seconds...")
            time.sleep(wait_seconds)

    print(f"[Gemini] Attempts with the main model ({model}) failed.")
    if fallback_model:
        print(f"[Gemini] Trying one more time with the fallback model ({fallback_model})...")
        try:
            response = client.models.generate_content(
                model=fallback_model,
                contents=contents,
                config=config,
            )
            text = getattr(response, "text", None)
            if not text or not text.strip():
                raise ValueError("Gemini fallback returned an empty response.text.")

            return json.loads(text)
        except Exception as exc_fallback:
            print(f"[Gemini] Fallback model failed | error={exc_fallback}")
            raise RuntimeError(
                f"Failed to call Gemini main & fallback. "
                f"Main report status={status_code}, error={last_exc} | "
                f"Fallback report error={exc_fallback}"
            ) from exc_fallback

    raise RuntimeError(
        f"Failed to call Gemini after {MAX_ATTEMPTS} attempts. Last error: {last_exc}"
    ) from last_exc


# ==== CLIP DURATION CONFIGURATION ====
# Change the values below to adjust the clip duration limits (in seconds)
MIN_CLIP_DURATION = 20
MAX_CLIP_DURATION = 179

def get_analysis_prompt(transkrip_lengkap: str, jumlah_clip: int, durasi_hook: int, cfg=None) -> str:
    """Centralized prompt for both Gemini and NVIDIA providers."""
    # Build optional Hook V2 prompt section
    _hook_v2_prompt = ""
    if cfg and getattr(cfg, "hook_v2", False):
        _hook_v2_items = getattr(cfg, "hook_v2_items", 3)
        _hook_v2_style = getattr(cfg, "hook_v2_style", "controversial_fast_glitch")
        _hook_v2_prompt = f"""

HOOK V2 (MULTI-HOOK INTRO — WAJIB):
- Selain hook standar, buat juga "hook_v2" berisi {_hook_v2_items} potongan pendek (0.5-2 detik) yang diambil dari momen paling mencolok/controversial/emosional di dalam klip.
- Gaya: {_hook_v2_style}
- Setiap item harus berisi: start_time, end_time, dan text (teks on-screen singkat 2-5 kata).
- Item harus diurutkan dari paling kuat ke paling lemah.
- Transisi antar item akan ditambahkan otomatis (white flash / glitch) oleh sistem.
- Isi field "hook_v2" sebagai objek dengan:
  - "enabled": true
  - "items": array dari objek (start_time, end_time, text)
  - "transition": objek dengan "type" ("white_flash" atau "glitch")
"""

    # Build optional Segment Trimming prompt section
    _segment_prompt = ""
    if cfg and not getattr(cfg, "no_segment_trim", False):
        _silence_hint = ""
        if cfg and getattr(cfg, "silence_trim", False):
            _silence_hint = "\n- AGRESIF buang bagian diam/silence/dead air. Jangan sertakan jeda lebih dari 0.5 detik."
        _segment_prompt = f"""

SEGMENT-BASED TRIMMING (KEEP SEGMENTS — WAJIB):
- Untuk setiap klip, analisis apakah ada bagian yang kurang menarik, terlalu diam, bertele-tele, atau filler di tengah.
- Jika ada, pecah klip menjadi beberapa "keep_segments" — hanya potongan terbaik yang dipertahankan.
- Setiap segment berisi: start_time dan end_time.
- Segment harus berurutan secara kronologis dan tidak boleh overlap.
- Jika seluruh durasi klip sudah padat dan menarik, cukup buat 1 segment yang mencakup seluruh durasi.{_silence_hint}
- Isi field "keep_segments" sebagai array dari objek (start_time, end_time).
"""
    return f"""
Kamu adalah Art Director, Editor Video, dan Strategist Metadata Short-Form Content untuk TikTok, Reels, dan YouTube Shorts.

Baca transkrip video berikut. Format transkrip:
[detik_mulai - detik_selesai] teks

TUGAS UTAMA:
- Carikan {jumlah_clip} momen paling menarik, paling kuat, paling shareable, dan paling berpotensi viral untuk dijadikan klip pendek.
- Urutkan klip berdasarkan viral_score tertinggi (paling berpotensi viral) ke terendah. Peringkat ("rank") hanya sebagai nomor urut (1, 2, 3...).
- Untuk setiap klip, hasilkan timing klip, hook, typography plan, b-roll plan, alasan pemilihan, metadata lintas platform, dan klasifikasi akun tujuan.
- Semua output harus sangat relevan dengan isi klip, bukan isi video penuh secara umum.

ATURAN PEMILIHAN KLIP & VIRAL-BILITY:
- Durasi klip harus {MIN_CLIP_DURATION}-{MAX_CLIP_DURATION} detik.
- Pilih bagian yang punya emosi, konflik, kejutan, insight, opini kuat, pelajaran praktis, atau punchline jelas.
- Evaluasi kekuatan viral (viral-bility) dan berikan "viral_score" (1-100) yang merepresentasikan seberapa viral suatu klip.
  - 90-100: Sangat berpotensi fyp/viral, emosi/konflik kuat, hook sangat nendang.
  - 80-89: Menarik, berpotensi performa baik.
  - 70-79: Standar, informatif tapi mungkin kurang greget.
- Utamakan bagian yang tetap menarik walau ditonton tanpa konteks video penuh.
- Hindari klip yang isinya terlalu mirip satu sama lain.
- Jangan pilih klip yang terasa datar, bertele-tele, atau tidak punya payoff yang jelas.

ATURAN RETENTION & STRUKTUR KLIP:
- Pastikan 3 detik pertama klip punya daya tarik kuat: hook, konflik, rasa penasaran, statement tajam, emosi, atau pertanyaan implisit.
- Klip ideal memiliki struktur:
  hook -> context singkat -> tension/insight -> payoff.
- Jangan memilih klip yang baru menarik setelah terlalu lama berjalan.
- Jika bagian awal segmen terlalu lambat, geser start_time ke kalimat yang lebih kuat.
- Jika payoff sudah selesai, jangan memperpanjang klip tanpa alasan.
- Jangan memasukkan intro, basa-basi, jeda panjang, atau transisi yang tidak menambah daya tarik.
- Utamakan klip yang membuat penonton ingin:
  1. berhenti scroll,
  2. menonton sampai akhir,
  3. komentar,
  4. share,
  5. save,
  6. atau merasa "ini gue banget".

ATURAN PEMOTONGAN TIMING:
- start_time harus dimulai sedekat mungkin dengan momen kuat pertama, bukan sekadar awal topik.
- end_time harus berhenti setelah payoff, kesimpulan, punchline, atau emotional beat utama selesai.
- Jangan potong terlalu awal jika kalimat masih menggantung.
- Jangan lanjutkan klip terlalu lama setelah inti pesan selesai.
- Klip harus tetap bisa dipahami tanpa harus menonton bagian sebelum atau sesudahnya.
- Jika ada dua momen kuat yang terlalu berdekatan dan saling mendukung, boleh digabung selama durasi tetap {MIN_CLIP_DURATION}-{MAX_CLIP_DURATION} detik.
- Jika ada dua momen kuat tetapi angle-nya berbeda, pisahkan sebagai kandidat klip berbeda.

PENILAIAN INTERNAL VIRAL_SCORE:
Nilai viral_score 1-100 berdasarkan komponen berikut. Ini hanya untuk penilaian internal, JANGAN menambahkan field baru ke JSON.
- Hook strength: 1-20
- Emotional intensity: 1-20
- Shareability/comment potential: 1-20
- Standalone clarity: 1-20
- Payoff/retention: 1-20

Panduan penilaian:
- Hook strength: seberapa kuat 3 detik pertama membuat orang berhenti scroll.
- Emotional intensity: seberapa kuat emosi, konflik, keresahan, lucu, haru, marah, kagum, atau relatable-nya.
- Shareability/comment potential: seberapa besar peluang orang komentar, debat, tag teman, share, atau save.
- Standalone clarity: seberapa mudah klip dipahami tanpa konteks video penuh.
- Payoff/retention: seberapa jelas reward menonton sampai akhir, seperti punchline, insight, twist, kesimpulan, atau pelajaran praktis.
- Jangan pilih klip dengan viral_score di bawah 70 kecuali jumlah momen bagus di transkrip sangat terbatas.

KLASIFIKASI AKUN TUJUAN (UNTUK SETIAP KLIP):
Tentukan akun tujuan berdasarkan ANGLE video dari klip tersebut. Jangan menilai hanya dari topik (misal: beauty tidak otomatis masuk Life). Nilai berdasarkan angle:
{_build_account_classification_prompt()}

ATURAN KHUSUS KLASIFIKASI:
1. Beauty tidak otomatis masuk Life. (Bahas omzet/brand -> Business. Review/lifestyle -> Life. Affiliate/konten -> Creator).
2. Finance tidak otomatis masuk Business. (Bahas omzet/bisnis -> Business. Personal finance/nabung -> Life. Cara bikin konten finance -> Creator).
3. Owner story dibagi berdasarkan angle. (Perjuangan brand -> Business. Kehidupan pribadi/keluarga -> Life. Rezeki/ibadah -> Muslim).

HOOK (WAJIB):
- Ambil 1 kalimat paling punchy yang ADA DI DALAM klip.
- Hook harus terasa kuat dan menarik perhatian dalam ~{durasi_hook} detik pertama.
- Simpan sebagai hook_start_time dan hook_end_time.
- Hook harus membuat orang ingin lanjut menonton, tapi jangan clickbait palsu.
- Pastikan hook masih natural dan benar-benar diucapkan dalam transkrip.
- Jika hook terbaik tidak berada tepat di awal kandidat klip, sesuaikan start_time agar hook muncul sedini mungkin.
- Hook harus cocok sebagai teks pembuka on-screen untuk menahan penonton dalam 3 detik pertama.

TYPOGRAPHY PLAN (KINETIC TYPOGRAPHY):
- Pilih 3-6 kata TUNGGAL paling berbobot, emosional, atau paling layak ditekankan dari setiap klip.
- Untuk setiap kata, tentukan:
  1. 'kata_utama': kata spesifik tersebut, harus sama persis ejaannya dengan transkrip.
  2. 'scale_level': pilih 1, 2, atau 3.
     - 1 = normal/kecil
     - 2 = besar/penekanan
     - 3 = raksasa/sangat krusial
  3. 'style': pilih "utama" atau "khusus".
  4. 'animasi': pilih "bounce_pop" atau "stagger_up".
- Jangan pilih frasa panjang. Hanya kata tunggal.
- Prioritaskan kata yang paling kuat secara emosi, makna, atau retensi visual.

B-ROLL (WAJIB JIKA RELEVAN):
- Carikan maksimal 1-3 momen dalam klip yang sangat cocok disisipi video B-roll / stock footage.
- Setiap B-roll berdurasi 3-7 detik.
- Berikan:
  - start_time
  - end_time
  - search_query
- search_query harus singkat, jelas, dan dalam Bahasa Inggris.
- Jangan taruh B-roll tepat di detik yang sama dengan hook.
- Hanya tambahkan B-roll jika benar-benar membantu visualisasi isi ucapan.
- Jika tidak ada momen yang cocok, isi broll_list dengan array kosong [].

VISUAL B-ROLL HOOK (0-3 DETIK PERTAMA):
- Berikan 2-5 ide B-Roll pembuka yang kontras, lucu, dramatis, atau memancing rasa penasaran sebelum video asli masuk.
- Sertakan keyword pencarian YouTube/TikTok untuk editor.
- Jika ada gestur yang bisa dipakai sebagai hook visual, berikan juga referensinya.
- Ini disimpan dalam objek 'recommended_visual_broll_hook' dan hanya berlaku sebagai referensi jika editor ingin mencari footage manual untuk 3 detik pertama.

BGM MOOD (BACKGROUND MUSIC):
- Analisis emosi dan topik dari klip ini.
- Pilih SATU mood musik latar yang paling cocok dari daftar baku ini: [chill, epic, sad, upbeat, suspense].
- Pastikan mood selaras dengan cerita. (Contoh: cerita perjuangan berat = sad/epic, cerita lucu/santai = chill/upbeat).

SLOW CLOSING:
- end_time HARUS ditambah padding +0.10 sampai +0.85 detik setelah kata terakhir agar ending terasa lega dan tidak kepotong kasar.

ALASAN PEMILIHAN:
- Isi field 'alasan' dengan penjelasan singkat mengapa klip ini layak dipilih.
- Fokus pada nilai emosi, kekuatan hook, potensi retention, shareability, dan payoff.
- Jelaskan trigger viral utama dari klip ini.
- Jelaskan kenapa orang kemungkinan akan menonton sampai akhir.
- Jelaskan kenapa klip ini tetap menarik walau ditonton tanpa konteks video penuh.

ATURAN BAHASA METADATA:
- title_indonesia tetap wajib diisi untuk kompatibilitas internal / fallback.
- title_indonesia HARUS dalam Bahasa Indonesia natural dan maksimal 100 karakter.
- Semua metadata lintas platform utama harus berbahasa Inggris natural.
- Ini berlaku untuk:
  - title_inggris
  - hastag
  - description_hook
  - description_context
  - keyword_tags
  - tiktok_caption
- Khusus kebutuhan TikTok versi Indonesia, juga buat:
  - tiktok_title_id
  - tiktok_caption_id
- tiktok_title_id dan tiktok_caption_id HARUS dalam Bahasa Indonesia natural.
- tiktok_title_id harus lebih deskriptif daripada title_indonesia, boleh lebih panjang dari 100 karakter jika perlu, dan harus menjelaskan isi klip/video dengan jelas.
- tiktok_caption_id harus natural, informatif, cocok untuk audiens Indonesia, dan boleh sedikit lebih panjang jika itu membantu menjelaskan isi klip.
- Jangan mencampur Bahasa Indonesia dan Bahasa Inggris di dalam field yang sama.
- Gunakan English yang natural, ringkas, enak dibaca, dan cocok untuk short-form content.
- Hindari terjemahan literal yang kaku.

METADATA LINTAS PLATFORM:
Untuk setiap klip, hasilkan metadata berikut:

1. title_indonesia
- Bahasa Indonesia natural, singkat, dan relevan.
- Ini hanya untuk kompatibilitas internal / fallback.
- Maksimal 100 karakter.

2. title_inggris
- Bahasa Inggris natural, kuat, tajam, dan enak dibaca.
- Ini adalah judul utama untuk metadata platform.
- Maksimal 100 karakter.
- Fokus pada 1 ide utama.
- Relevan dengan isi klip, bukan isi video penuh secara umum.
- Jangan clickbait murahan.
- Jangan pakai huruf kapital berlebihan.
- Hindari tanda baca berlebihan seperti !!! ??? ...
- Jangan terlalu generik.

3. hastag
- Isi dengan 2 sampai 3 hashtag saja dalam satu string.
- Semua hashtag HARUS dalam Bahasa Inggris.
- Pisahkan dengan spasi.
- Harus relevan langsung dengan topik klip.
- Jangan duplikat.
- Hindari hashtag terlalu generik seperti #fyp #viral #trending kecuali memang sangat relevan.
- Gunakan format seperti: #mindset #career #productivity

4. description_hook
- Tepat 1 kalimat.
- HARUS dalam Bahasa Inggris.
- Ini adalah kalimat pembuka metadata.
- Harus singkat, kuat, dan memancing rasa ingin tahu.
- Jangan clickbait palsu.

5. description_context
- Tepat 1 kalimat.
- HARUS dalam Bahasa Inggris.
- Menjelaskan konteks utama isi klip secara ringkas.
- Harus relevan dengan pembicaraan di klip.

6. keyword_tags
- Berisi 5 sampai 8 keyword pendek.
- HARUS dalam Bahasa Inggris.
- Bukan hashtag.
- Harus berupa daftar frasa singkat yang relevan dengan isi klip.
- Hindari keyword spam.
- Utamakan keyword yang mungkin benar-benar dicari orang.
- Field ini terutama untuk kebutuhan metadata YouTube.

7. tiktok_title_id
- Bahasa Indonesia natural.
- Lebih panjang dan lebih menjelaskan isi video daripada title_indonesia.
- Tidak perlu dibatasi 100 karakter, tapi tetap harus ringkas, jelas, dan enak dibaca.
- Harus relevan dengan isi klip, bukan isi video panjang secara umum.
- Jangan clickbait murahan.

8. tiktok_caption_id
- 1 sampai 2 kalimat.
- HARUS dalam Bahasa Indonesia.
- Boleh sedikit lebih panjang daripada caption English jika membantu menjelaskan isi klip.
- Gaya natural, ringan, dan enak dibaca.
- Tetap sesuai isi klip.
- Jangan sekadar copy-paste title.
- Jangan terlalu formal.

9. tiktok_caption
- 1 sampai 2 kalimat singkat.
- HARUS dalam Bahasa Inggris.
- Gaya lebih natural, ringan, dan conversational.
- Tetap sesuai isi klip.
- Jangan sekadar copy-paste title.
- Jangan terlalu formal.
- Usahakan tidak lebih dari 140 karakter.

ATURAN KUALITAS METADATA:
- Semua metadata harus sesuai isi klip, bukan isi video panjang secara umum.
- Jangan membuat janji yang tidak dibahas di klip.
- Jangan pakai hiperbola palsu seperti "100% berhasil", "pasti kaya", dll kecuali memang sangat jelas disebutkan.
- Jika ada angka, frasa kuat, atau statement tajam dari ucapan asli, prioritaskan itu sebagai inspirasi judul/caption.
- Title, descriptions, dan caption harus saling melengkapi, bukan mengulang kalimat yang sama.
- Semua field metadata yang dipakai untuk platform harus berbahasa Inggris natural, bukan terjemahan literal yang kaku.
- Khusus tiktok_title_id dan tiktok_caption_id, gunakan Bahasa Indonesia yang natural, jelas, dan lebih menjelaskan isi klip untuk audiens Indonesia.

ATURAN OUTPUT:
- Output HARUS berupa JSON array valid.
- Jangan beri penjelasan apa pun di luar JSON.
- Semua field wajib terisi.
- Jika ragu, prioritaskan akurasi isi klip daripada kreativitas berlebihan.
{_hook_v2_prompt}{_segment_prompt}

STRUKTUR JSON WAJIB (Ikuti nama field ini secara kaku):
[
  {{
    "rank": 1,
    "viral_score": 95,
    "start_time": 30.5,
    "end_time": 90.0,
    "hook_start_time": 30.5,
    "hook_end_time": 35.0,
    "bgm_mood": "mood_here",
    "typography_plan": [{{ "kata_utama": "...", "scale_level": 2, "style": "utama", "animasi": "bounce_pop" }}],
    "broll_list": [{{ "start_time": 40.0, "end_time": 45.0, "search_query": "..." }}],
    "recommended_visual_broll_hook": [
      {{ "broll_idea": "...", "search_keyword": "...", "why_it_works": "..." }}
    ],
    "hook_v2": {{
      "enabled": true,
      "items": [{{ "start_time": 31.0, "end_time": 32.5, "text": "KATA KUNCI" }}],
      "transition": {{ "type": "white_flash" }}
    }},
    "keep_segments": [
      {{ "start_time": 30.5, "end_time": 55.0 }},
      {{ "start_time": 58.0, "end_time": 90.0 }}
    ],
    "title_indonesia": "...",
    "title_inggris": "...",
    "hastag": "#hastag1 #hastag2",
    "description_hook": "...",
    "description_context": "...",
    "keyword_tags": ["tag1", "tag2"],
    "tiktok_title_id": "...",
    "tiktok_caption_id": "...",
    "tiktok_caption": "...",
    "alasan": "...",
    "klasifikasi_akun": {{
      "tipe_akun": "Creator",
      "akun_tujuan": "Creator.Mereska",
      "confidence": 87,
      "angle_utama": "Monetisasi konten digital dari niche beauty",
      "alasan": "...",
      "kata_kunci_pendukung": ["affiliate", "monetisasi"],
      "bio_akun": "...",
      "alternatif_akun": {{
        "tipe_akun": "Life",
        "akun_tujuan": "Life.Mereska",
        "alasan": "..."
      }}
    }}
  }}
]

Transkrip:
{transkrip_lengkap}
"""


def analyze_with_nvidia(transkrip_lengkap: str, cfg) -> list[dict]:
    """Analyze transcript using NVIDIA NIM API (OpenAI compatible)."""
    print(f"[3/3] Analyzing Top {cfg.jumlah_clip} moments using NVIDIA ({cfg.nvidia_model})...")

    if not cfg.api_key_nvidia:
        raise ValueError("NVIDIA_API_KEY not found in environment.")

    client = _make_nvidia_client(cfg)
    
    prompt = get_analysis_prompt(transkrip_lengkap, cfg.jumlah_clip, cfg.durasi_hook, cfg=cfg)
    
    # Define the strict schema for Guided JSON (NVIDIA NIM specific)
    clips_schema = {
        "type": "array",
        "items": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "rank": {"type": "integer"},
                "viral_score": {"type": "integer"},
                "start_time": {"type": "number"},
                "end_time": {"type": "number"},
                "hook_start_time": {"type": "number"},
                "hook_end_time": {"type": "number"},
                "bgm_mood": {
                    "type": "string",
                    "enum": ["chill", "epic", "sad", "upbeat", "suspense"]
                },
                "typography_plan": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "kata_utama": {"type": "string"},
                            "scale_level": {"type": "integer", "enum": [1, 2, 3]},
                            "style": {"type": "string", "enum": ["utama", "khusus"]},
                            "animasi": {"type": "string", "enum": ["bounce_pop", "stagger_up"]}
                        },
                        "required": ["kata_utama", "scale_level", "style", "animasi"]
                    }
                },
                "broll_list": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "start_time": {"type": "number"},
                            "end_time": {"type": "number"},
                            "search_query": {"type": "string"}
                        },
                        "required": ["start_time", "end_time", "search_query"]
                    }
                },
                "recommended_visual_broll_hook": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "broll_idea": {"type": "string"},
                            "search_keyword": {"type": "string"},
                            "why_it_works": {"type": "string"}
                        },
                        "required": ["broll_idea", "search_keyword", "why_it_works"]
                    }
                },
                "title_indonesia": {"type": "string"},
                "title_inggris": {"type": "string"},
                "hastag": {"type": "string"},
                "description_hook": {"type": "string"},
                "description_context": {"type": "string"},
                "keyword_tags": {
                    "type": "array",
                    "items": {"type": "string"}
                },
                "tiktok_title_id": {"type": "string"},
                "tiktok_caption_id": {"type": "string"},
                "tiktok_caption": {"type": "string"},
                "alasan": {"type": "string"},
                "klasifikasi_akun": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "tipe_akun": {"type": "string", "enum": list(TARGET_ACCOUNTS.keys())},
                        "akun_tujuan": {"type": "string"},
                        "confidence": {"type": "integer"},
                        "angle_utama": {"type": "string"},
                        "alasan": {"type": "string"},
                        "kata_kunci_pendukung": {
                            "type": "array",
                            "items": {"type": "string"}
                        },
                        "bio_akun": {"type": "string"},
                        "alternatif_akun": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "tipe_akun": {"type": "string", "enum": list(TARGET_ACCOUNTS.keys())},
                                "akun_tujuan": {"type": "string"},
                                "alasan": {"type": "string"}
                            },
                            "required": ["tipe_akun", "akun_tujuan", "alasan"]
                        }
                    },
                    "required": ["tipe_akun", "akun_tujuan", "confidence", "angle_utama", "alasan", "kata_kunci_pendukung", "bio_akun", "alternatif_akun"]
                },
                "hook_v2": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "start_time": {"type": "number"},
                                    "end_time": {"type": "number"},
                                    "text": {"type": "string"},
                                },
                                "required": ["start_time", "end_time", "text"],
                            },
                        },
                        "transition": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "type": {"type": "string", "enum": ["white_flash", "glitch"]},
                            },
                            "required": ["type"],
                        },
                    },
                    "required": ["enabled", "items", "transition"],
                },
                "keep_segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "start_time": {"type": "number"},
                            "end_time": {"type": "number"},
                        },
                        "required": ["start_time", "end_time"],
                    },
                },
            },
            "required": [
                "rank", "viral_score", "start_time", "end_time", "hook_start_time", "hook_end_time",
                "bgm_mood", "typography_plan", "broll_list", "recommended_visual_broll_hook", "title_indonesia",
                "title_inggris", "hastag", "description_hook", "description_context",
                "keyword_tags", "tiktok_title_id", "tiktok_caption_id", "tiktok_caption",
                "alasan", "klasifikasi_akun", "hook_v2", "keep_segments"
            ]
        }
    }

    # The HTTP call and the parse share one retry loop deliberately: with
    # guided_json in play, a malformed response is a *sampling* failure, and the
    # only meaningful remedy is another sample.
    failures: list[str] = []

    for attempt in range(1, NVIDIA_MAX_ATTEMPTS + 1):
        try:
            print(f"   🔁 NVIDIA attempt {attempt}/{NVIDIA_MAX_ATTEMPTS}...")
            completion = client.chat.completions.create(
                model=cfg.nvidia_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a professional video editor and strategist. "
                            "Return JSON only. Follow the provided JSON schema exactly."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                # Nudge toward determinism on each retry: if the last sample was
                # malformed, a cooler one is likelier to conform.
                temperature=max(0.0, 0.5 - 0.15 * (attempt - 1)),
                top_p=1,
                max_tokens=16384,
                extra_body={
                    "chat_template_kwargs": {"thinking": False},
                    "nvext": {"guided_json": clips_schema},
                },
            )
            content = completion.choices[0].message.content
            return _extract_clip_list(content)

        except Exception as exc:
            failures.append(f"attempt {attempt}: {type(exc).__name__}: {exc}")
            print(f"   ⚠️ NVIDIA attempt {attempt} failed | {type(exc).__name__}: {exc}")

            if not _nvidia_is_retryable(exc) or attempt == NVIDIA_MAX_ATTEMPTS:
                detail = "\n  ".join(failures)
                raise RuntimeError(
                    f"NVIDIA analysis failed after {attempt} attempt(s):\n  {detail}"
                ) from exc

            time.sleep(NVIDIA_BACKOFF_SECONDS[attempt - 1])

    # Unreachable: the loop either returns or raises.
    raise RuntimeError("NVIDIA analysis failed (loop ended with no result).")


def analyze_with_ai(transkrip_lengkap: str, cfg) -> list[dict]:
    """Dispatch transcript analysis to the configured provider.

    There is deliberately no cross-provider fallback. The previous behaviour --
    catch bare Exception and silently retry on Gemini -- was actively harmful:
    a user who chose NVIDIA got billed on Gemini instead, with the real error
    reduced to a single warning line. It is also how `openai` stayed an
    undeclared dependency for so long, since ModuleNotFoundError was swallowed
    along with everything else.
    """
    provider = getattr(cfg, "ai_provider", "nvidia")

    if provider == "nvidia":
        if not getattr(cfg, "api_key_nvidia", ""):
            raise RuntimeError(
                "NVIDIA_API_KEY not found. Set it in .env, or run "
                "with --ai-provider gemini."
            )
        return analyze_with_nvidia(transkrip_lengkap, cfg)

    if provider == "gemini":
        if not getattr(cfg, "api_key_gemini", ""):
            raise RuntimeError(
                "GOOGLE_API_KEY not found. Set it in .env, or run "
                "with --ai-provider nvidia."
            )
        return analyze_with_gemini(transkrip_lengkap, cfg)

    raise ValueError(
        f"Unknown AI provider: {provider!r} (choices: nvidia, gemini)"
    )


def analyze_with_gemini(
    transkrip_lengkap: str,
    cfg,
) -> list[dict]:
    """Analyse transcript with Gemini AI."""
    import google.genai as genai
    from google.genai import types

    print(f"[3/3] Analyzing Top {cfg.jumlah_clip} best moments using Gemini...")

    prompt = get_analysis_prompt(transkrip_lengkap, cfg.jumlah_clip, cfg.durasi_hook, cfg=cfg)

    # JSON Schema definitions (same as before)
    schema_broll = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "start_time": {"type": "NUMBER"},
                "end_time": {"type": "NUMBER"},
                "search_query": {"type": "STRING"},
            },
            "required": ["start_time", "end_time", "search_query"],
        },
    }

    schema_visual_broll_hook = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "broll_idea": {"type": "STRING"},
                "search_keyword": {"type": "STRING"},
                "why_it_works": {"type": "STRING"},
            },
            "required": ["broll_idea", "search_keyword", "why_it_works"],
        },
    }

    schema_typography = {
        "type": "ARRAY",
        "items": {
            "type": "OBJECT",
            "properties": {
                "kata_utama": {"type": "STRING"},
                "scale_level": {"type": "INTEGER"},
                "style": {"type": "STRING"},
                "animasi": {"type": "STRING"},
            },
            "required": ["kata_utama", "scale_level", "style", "animasi"],
        },
    }

    schema_klasifikasi = {
        "type": "OBJECT",
        "properties": {
            "tipe_akun": {"type": "STRING"},
            "akun_tujuan": {"type": "STRING"},
            "confidence": {"type": "INTEGER"},
            "angle_utama": {"type": "STRING"},
            "alasan": {"type": "STRING"},
            "kata_kunci_pendukung": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
            },
            "bio_akun": {"type": "STRING"},
            "alternatif_akun": {
                "type": "OBJECT",
                "properties": {
                    "tipe_akun": {"type": "STRING"},
                    "akun_tujuan": {"type": "STRING"},
                    "alasan": {"type": "STRING"},
                },
                "required": ["tipe_akun", "akun_tujuan", "alasan"],
            },
        },
        "required": ["tipe_akun", "akun_tujuan", "confidence", "angle_utama", "alasan", "kata_kunci_pendukung", "bio_akun", "alternatif_akun"],
    }

    client = genai.Client(
        api_key=cfg.api_key_gemini,
        http_options=types.HttpOptions(
            timeout=REQUEST_TIMEOUT_MS,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )

    gemini_config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema={
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "rank": {"type": "INTEGER"},
                    "viral_score": {"type": "INTEGER"},
                    "hook_start_time": {"type": "NUMBER"},
                    "hook_end_time": {"type": "NUMBER"},
                    "start_time": {"type": "NUMBER"},
                    "end_time": {"type": "NUMBER"},
                    "typography_plan": schema_typography,
                    "broll_list": schema_broll,
                    "recommended_visual_broll_hook": schema_visual_broll_hook,
                    "alasan": {"type": "STRING"},
                    "bgm_mood": {"type": "STRING"},
                    "title_indonesia": {"type": "STRING"},
                    "title_inggris": {"type": "STRING"},
                    "hastag": {"type": "STRING"},
                    "description_hook": {"type": "STRING"},
                    "description_context": {"type": "STRING"},
                    "keyword_tags": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                    "tiktok_title_id": {"type": "STRING"},
                    "tiktok_caption_id": {"type": "STRING"},
                    "tiktok_caption": {"type": "STRING"},
                    "klasifikasi_akun": schema_klasifikasi,
                    "hook_v2": {
                        "type": "OBJECT",
                        "properties": {
                            "enabled": {"type": "BOOLEAN"},
                            "items": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "start_time": {"type": "NUMBER"},
                                        "end_time": {"type": "NUMBER"},
                                        "text": {"type": "STRING"},
                                    },
                                    "required": ["start_time", "end_time", "text"],
                                },
                            },
                            "transition": {
                                "type": "OBJECT",
                                "properties": {
                                    "type": {"type": "STRING"},
                                },
                                "required": ["type"],
                            },
                        },
                        "required": ["enabled", "items", "transition"],
                    },
                    "keep_segments": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "start_time": {"type": "NUMBER"},
                                "end_time": {"type": "NUMBER"},
                            },
                            "required": ["start_time", "end_time"],
                        },
                    },
                },
                "required": [
                    "rank", "viral_score", "hook_start_time", "hook_end_time",
                    "start_time", "end_time", "typography_plan",
                    "broll_list", "recommended_visual_broll_hook", "alasan", "bgm_mood",
                    "title_indonesia", "title_inggris", "hastag",
                    "description_hook", "description_context",
                    "keyword_tags", "tiktok_title_id",
                    "tiktok_caption_id", "tiktok_caption",
                    "klasifikasi_akun", "hook_v2", "keep_segments",
                ],
            },
        },
    )

    return _generate_json_with_retry(
        client=client,
        model=cfg.gemini_model,
        fallback_model=getattr(cfg, "gemini_fallback_model", None),
        contents=prompt,
        config=gemini_config,
    )
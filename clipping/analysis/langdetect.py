"""Which language is being spoken, from stopword frequency alone.

The pipeline needs this to decide what language to write titles and captions in.
Nothing heavier is justified: the input is hundreds of words of transcript, not
a three-word query, and at that length stopword ratios separate these languages
cleanly. A model or a C extension for the same answer would be a dependency on
every install for a result that is superseded whenever the transcription
provider reports the language itself (which Groq and Voxtral both do).

Also replaces ``metadata._looks_indonesian``, which was a hardcoded Indonesian
stopword list serving the same purpose for one language.

Stdlib only.
"""

from __future__ import annotations

import re

# ~30 of the commonest function words per language. Function words are used
# because they are frequent, short, and mostly disjoint across languages — and
# because they survive a transcript that is otherwise full of proper nouns,
# brand names and English loanwords, which content words do not.
STOPWORDS = {
    "en": {"the", "and", "to", "of", "a", "in", "is", "it", "you", "that", "was",
           "for", "on", "are", "with", "as", "be", "this", "have", "from", "or",
           "one", "had", "by", "but", "not", "what", "all", "were", "we", "when",
           "your", "can", "said", "there", "if", "so", "just", "like", "dont"},
    "fr": {"le", "de", "un", "une", "et", "en", "que", "qui", "dans", "pour",
           "pas", "sur", "ce", "il", "elle", "les", "des", "du", "est", "au",
           "aux", "avec", "mais", "ou", "donc", "nous", "vous", "ils", "je",
           "tu", "on", "cest", "plus", "tout", "comme", "sont", "fait", "être",
           "avoir", "faire", "très", "bien", "moi", "ça", "ne", "se", "la"},
    "id": {"yang", "dan", "di", "itu", "dengan", "untuk", "tidak", "ini", "dari",
           "dalam", "akan", "pada", "juga", "ke", "karena", "ada", "kita", "saya",
           "kamu", "bisa", "sudah", "atau", "seperti", "jadi", "lebih", "saja",
           "harus", "kalau", "mereka", "orang", "banyak", "gak", "nggak", "aja",
           "sih", "kan", "nya", "bang", "biar", "emang",
           # The tail of metadata._looks_indonesian's original list. Kept so
           # that delegating to this module cannot silently stop a warning from
           # firing: every indicator the old detector matched is still matched.
           "adalah", "agar", "saat", "tentang", "bikin", "banget"},
    "es": {"el", "la", "de", "que", "y", "en", "un", "una", "los", "las", "del",
           "por", "con", "no", "para", "es", "se", "lo", "como", "más", "pero",
           "sus", "le", "ya", "muy", "porque", "cuando", "todo", "esta", "son",
           "está", "hay", "bien", "así", "eso", "tiene", "nos", "yo"},
    "de": {"der", "die", "das", "und", "ist", "von", "den", "nicht", "mit",
           "sich", "des", "auf", "für", "dem", "ein", "eine", "als", "auch",
           "es", "an", "werden", "aus", "er", "hat", "dass", "sie", "nach",
           "wird", "bei", "einen", "aber", "wenn", "nur", "wir", "was", "so",
           "ich", "hier", "noch", "mal"},
    "pt": {"de", "que", "não", "para", "com", "uma", "os", "no", "se", "na",
           "por", "mais", "as", "dos", "como", "mas", "foi", "ao", "ele", "das",
           "tem", "muito", "isso", "você", "já", "está", "eu", "também", "só",
           "pelo", "até", "isso", "então", "porque", "aqui", "gente", "vai"},
    "it": {"di", "che", "non", "per", "una", "con", "sono", "come", "più",
           "anche", "ma", "se", "da", "il", "lo", "la", "le", "gli", "nel",
           "alla", "questo", "quando", "perché", "cosa", "molto", "essere",
           "fare", "però", "poi", "adesso", "cioè", "quindi"},
    "nl": {"de", "het", "een", "en", "van", "ik", "te", "dat", "die", "in",
           "niet", "je", "is", "zijn", "op", "aan", "met", "als", "voor", "maar",
           "om", "hij", "ook", "ze", "was", "wel", "heeft", "dan", "nog", "naar",
           "hier", "gaan", "want"},
    "tr": {"bir", "ve", "bu", "için", "ile", "çok", "daha", "ama", "olarak",
           "gibi", "var", "ben", "sen", "biz", "şey", "kadar", "sonra", "her",
           "yok", "ne", "de", "da", "diye", "böyle", "şimdi", "zaman", "tamam"},
    "ar": {"في", "من", "على", "الى", "عن", "مع", "هذا", "هذه", "التي", "الذي",
           "كان", "كل", "لا", "ما", "أن", "إن", "هو", "هي", "نحن", "انت",
           "يكون", "بعد", "قبل", "حتى", "لكن", "أو", "ثم", "عند"},
}

LANG_NAMES = {
    "en": "English", "fr": "French", "id": "Indonesian", "es": "Spanish",
    "de": "German", "pt": "Portuguese", "it": "Italian", "nl": "Dutch",
    "tr": "Turkish", "ar": "Arabic",
}

SUPPORTED = tuple(STOPWORDS)

# How far ahead the winner must be before the answer is trusted. Several of
# these languages share function words ("de", "la", "que", "no"), so a narrow
# win is genuinely ambiguous rather than a close call.
MIN_MARGIN = 1.5
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _tokens(text):
    return [match.group(0).lower() for match in _WORD_RE.finditer(str(text or ""))]


def scores(text):
    """``{lang: hit_ratio}`` — the share of tokens that are that language's stopwords."""
    tokens = _tokens(text)
    if not tokens:
        return {lang: 0.0 for lang in STOPWORDS}
    total = len(tokens)
    return {
        lang: sum(1 for tok in tokens if tok in words) / total
        for lang, words in STOPWORDS.items()
    }


def detect(text, *, default="en"):
    """``(iso639_1, confidence)`` for *text*.

    ``confidence`` is the winner's ratio, and the *default* is returned with the
    winner's score when the margin over the runner-up is too small to call. A
    confident wrong answer here means a whole clip set captioned in the wrong
    language, so an unsure detector says so rather than guessing.
    """
    ranked = sorted(scores(text).items(), key=lambda kv: kv[1], reverse=True)
    if not ranked or ranked[0][1] <= 0:
        return default, 0.0

    best_lang, best = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

    if runner_up > 0 and best < runner_up * MIN_MARGIN:
        return default, best
    return best_lang, best


def looks_like(text, lang):
    """Whether *text* reads as *lang*. The replacement for ``_looks_indonesian``.

    Deliberately lenient, because it drives warnings rather than rejections: a
    single stopword hit is enough. The original checked the same way, and
    tightening it would turn advisory warnings into noise on short fields like a
    three-word title.
    """
    words = STOPWORDS.get(lang)
    if not words:
        return False
    return any(tok in words for tok in _tokens(text))


def language_name(code, *, default="English"):
    """``"fr"`` -> ``"French"``, for dropping into a prompt."""
    return LANG_NAMES.get(str(code or "").lower(), default)

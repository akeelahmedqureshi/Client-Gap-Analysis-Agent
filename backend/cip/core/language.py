"""Language detection for crawled pages (multi-language analysis, BRS Phase 3).

The declared ``<html lang>`` wins; otherwise a stop-word vote over the page text picks among the languages
the deterministic keyword matching supports (``SUPPORTED``). Detection is only used to label pages, to add the
matching keyword packs (core/taxonomy_i18n.yaml) and to warn when a site's language is not covered.
"""

from __future__ import annotations

import re
from collections import Counter

SUPPORTED = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese", "it": "Italian",
             "nl": "Dutch"}
NAMES = {**SUPPORTED, "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "ru": "Russian", "ar": "Arabic",
         "pl": "Polish", "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish", "tr": "Turkish"}
STOPWORDS = {
    "en": {"the", "and", "with", "for", "your", "you", "our", "are", "that", "this", "from", "more", "can"},
    "es": {"el", "la", "los", "las", "y", "con", "para", "su", "sus", "nuestro", "que", "una", "más", "del"},
    "fr": {"le", "la", "les", "et", "avec", "pour", "votre", "vos", "nous", "des", "une", "est", "plus", "du"},
    "de": {"der", "die", "das", "und", "mit", "für", "ihre", "ihr", "wir", "ist", "eine", "mehr", "auf", "den"},
    "pt": {"o", "os", "as", "e", "com", "para", "seu", "sua", "nosso", "que", "uma", "mais", "do", "da"},
    "it": {"il", "lo", "gli", "le", "e", "con", "per", "tuo", "tua", "nostro", "che", "una", "più", "del"},
    "nl": {"de", "het", "en", "met", "voor", "uw", "jouw", "onze", "is", "een", "meer", "van", "op", "wij"},
}
WORD = re.compile(r"[a-zà-ÿ]+")
CJK = re.compile(r"[぀-ヿ一-鿿가-힯]")


def normalize(tag: str | None) -> str | None:
    if not tag:
        return None
    code = tag.strip().lower().replace("_", "-").split("-")[0]
    return code if re.fullmatch(r"[a-z]{2,3}", code) else None


def detect(text: str, declared: str | None = None) -> str | None:
    code = normalize(declared)
    if code:
        return code
    if not text:
        return None
    sample = text[:20_000]
    if len(CJK.findall(sample)) > 50:
        return "zh" if not re.search(r"[぀-ヿ]", sample) else "ja"
    words = Counter(WORD.findall(sample.lower()))
    scores = {lang: sum(words[w] for w in stops) for lang, stops in STOPWORDS.items()}
    lang, best = max(scores.items(), key=lambda x: x[1])
    return lang if best >= 3 else None


def name(code: str | None) -> str:
    return NAMES.get(code or "", code or "unknown")


def summarize(langs: list[str | None]) -> dict:
    """{"primary": "es", "pages": {"es": 7, "en": 1}, "supported": True}"""
    counts = Counter(lang for lang in langs if lang)
    primary = counts.most_common(1)[0][0] if counts else None
    return {"primary": primary, "primary_name": name(primary), "pages": dict(counts),
            "supported": primary is None or primary in SUPPORTED}

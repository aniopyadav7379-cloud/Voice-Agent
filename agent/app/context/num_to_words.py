"""
Deterministic number → words for the voice reply, 0–9999, in Hindi / Marathi /
English.

WHY THIS EXISTS
───────────────
Sarvam TTS frequently mis-speaks or SKIPS bare digits — especially Devanagari
numerals ("६५"). The system prompt asks the model to spell numbers out, but the
model is not perfectly consistent, so a stray "६५" / "65" reaches TTS and the
price gets dropped from the spoken reply. This module is the deterministic
safety net applied just before TTS (see SarvamTTSService._normalize_pronunciation
→ _spell_out_numbers): any digit run in the text is converted to spoken words in
the reply's language, so the number is always heard.

Scope: 0–9999 covers all realistic telecom figures (prices, GB, validity days).
Indian number words are irregular in 0–99, so those are explicit tables; 100–9999
compose them (hundreds/thousands). Hindi and Marathi share Devanagari but differ
in many words (65 = Hindi पैंसठ, Marathi पासष्ट), so each has its own table.
"""
from __future__ import annotations

# Map Devanagari digits → ASCII so one code path handles both scripts.
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


# ── English ────────────────────────────────────────────────────────────────
_EN_ONES = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen",
]
_EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
            "eighty", "ninety"]

# ── Hindi 0–99 (irregular — explicit) ────────────────────────────────────────
_HI_0_99 = [
    "शून्य", "एक", "दो", "तीन", "चार", "पाँच", "छह", "सात", "आठ", "नौ",
    "दस", "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस",
    "बीस", "इक्कीस", "बाईस", "तेईस", "चौबीस", "पच्चीस", "छब्बीस", "सत्ताईस", "अट्ठाईस", "उनतीस",
    "तीस", "इकतीस", "बत्तीस", "तैंतीस", "चौंतीस", "पैंतीस", "छत्तीस", "सैंतीस", "अड़तीस", "उनतालीस",
    "चालीस", "इकतालीस", "बयालीस", "तैंतालीस", "चवालीस", "पैंतालीस", "छियालीस", "सैंतालीस", "अड़तालीस", "उनचास",
    "पचास", "इक्यावन", "बावन", "तिरेपन", "चौवन", "पचपन", "छप्पन", "सत्तावन", "अट्ठावन", "उनसठ",
    "साठ", "इकसठ", "बासठ", "तिरेसठ", "चौंसठ", "पैंसठ", "छियासठ", "सड़सठ", "अड़सठ", "उनहत्तर",
    "सत्तर", "इकहत्तर", "बहत्तर", "तिहत्तर", "चौहत्तर", "पचहत्तर", "छिहत्तर", "सतहत्तर", "अठहत्तर", "उनासी",
    "अस्सी", "इक्यासी", "बयासी", "तिरासी", "चौरासी", "पचासी", "छियासी", "सत्तासी", "अट्ठासी", "नवासी",
    "नब्बे", "इक्यानवे", "बानवे", "तिरानवे", "चौरानवे", "पचानवे", "छियानवे", "सत्तानवे", "अट्ठानवे", "निन्यानवे",
]

# ── Marathi 0–99 (irregular — explicit) ──────────────────────────────────────
_MR_0_99 = [
    "शून्य", "एक", "दोन", "तीन", "चार", "पाच", "सहा", "सात", "आठ", "नऊ",
    "दहा", "अकरा", "बारा", "तेरा", "चौदा", "पंधरा", "सोळा", "सतरा", "अठरा", "एकोणीस",
    "वीस", "एकवीस", "बावीस", "तेवीस", "चोवीस", "पंचवीस", "सव्वीस", "सत्तावीस", "अठ्ठावीस", "एकोणतीस",
    "तीस", "एकतीस", "बत्तीस", "तेहेतीस", "चौतीस", "पस्तीस", "छत्तीस", "सदतीस", "अडतीस", "एकोणचाळीस",
    "चाळीस", "एक्केचाळीस", "बेचाळीस", "त्रेचाळीस", "चव्वेचाळीस", "पंचेचाळीस", "सेहेचाळीस", "सत्तेचाळीस", "अठ्ठेचाळीस", "एकोणपन्नास",
    "पन्नास", "एक्कावन्न", "बावन्न", "त्रेपन्न", "चोपन्न", "पंचावन्न", "छप्पन्न", "सत्तावन्न", "अठ्ठावन्न", "एकोणसाठ",
    "साठ", "एकसष्ट", "बासष्ट", "त्रेसष्ट", "चौसष्ट", "पासष्ट", "सहासष्ट", "सदुसष्ट", "अडुसष्ट", "एकोणसत्तर",
    "सत्तर", "एक्काहत्तर", "बाहत्तर", "त्र्याहत्तर", "चौर्‍याहत्तर", "पंच्याहत्तर", "शहात्तर", "सत्याहत्तर", "अठ्ठ्याहत्तर", "एकोणऐंशी",
    "ऐंशी", "एक्क्याऐंशी", "ब्याऐंशी", "त्र्याऐंशी", "चौऱ्याऐंशी", "पंच्याऐंशी", "शहाऐंशी", "सत्त्याऐंशी", "अठ्ठ्याऐंशी", "एकोणनव्वद",
    "नव्वद", "एक्क्याण्णव", "ब्याण्णव", "त्र्याण्णव", "चौऱ्याण्णव", "पंच्याण्णव", "शहाण्णव", "सत्त्याण्णव", "अठ्ठ्याण्णव", "नव्व्याण्णव",
]

# Per-language "hundred" and "thousand" words.
_HUNDRED = {"hi": "सौ", "mr": "शे", "en": "hundred"}
_THOUSAND = {"hi": "हज़ार", "mr": "हजार", "en": "thousand"}


def _en_0_99(n: int) -> str:
    if n < 20:
        return _EN_ONES[n]
    tens, ones = divmod(n, 10)
    return _EN_TENS[tens] + (" " + _EN_ONES[ones] if ones else "")


def _words_0_99(n: int, lang: str) -> str:
    if lang == "hi":
        return _HI_0_99[n]
    if lang == "mr":
        return _MR_0_99[n]
    return _en_0_99(n)


def number_to_words(n: int, lang: str) -> str:
    """
    Spell an integer 0–9999 in `lang` ('hi' | 'mr' | 'en').
    Out-of-range values are returned as their digit string unchanged (caller
    should not pass them, but we never crash on the hot path).
    """
    if n < 0 or n > 9999:
        return str(n)
    if n < 100:
        return _words_0_99(n, lang)

    # Marathi joins the hundred word to the digit (दोनशे, नऊशे); Hindi and English
    # space it (एक सौ, "two hundred"). Only Marathi hundreds join.
    join = "" if lang == "mr" else " "

    parts: list[str] = []
    thousands, rest = divmod(n, 1000)
    if thousands:
        # "हज़ार"/"हजार" reads better spaced even in Indic; only hundreds join.
        parts.append(f"{_words_0_99(thousands, lang)} {_THOUSAND[lang]}")
    hundreds, rest2 = divmod(rest, 100)
    if hundreds:
        parts.append(f"{_words_0_99(hundreds, lang)}{join}{_HUNDRED[lang]}")
    if rest2:
        parts.append(_words_0_99(rest2, lang))
    return " ".join(parts)


# BCP-47 → the 3 supported word tables. Everything else falls back to English
# words (still far better than dropped digits).
def _lang_key(bcp47: str) -> str:
    if bcp47.startswith("hi"):
        return "hi"
    if bcp47.startswith("mr"):
        return "mr"
    return "en"


def spell_digits(text: str, language: str) -> str:
    """
    Replace every run of digits (Latin 0-9 or Devanagari ०-९) in `text` with its
    spoken words in `language`. Groups of ≥5 digits (phone numbers, order refs)
    are left as-is — spelling a 10-digit number as one integer would be wrong and
    those are rare on this sales path. Applied just before TTS.
    """
    lang = _lang_key(language)
    ascii_text = text.translate(_DEVANAGARI_DIGITS)

    out: list[str] = []
    i = 0
    n = len(ascii_text)
    while i < n:
        ch = ascii_text[i]
        if ch.isdigit():
            j = i
            while j < n and ascii_text[j].isdigit():
                j += 1
            run = ascii_text[i:j]
            if len(run) <= 4:                       # 0–9999 → spell it
                out.append(number_to_words(int(run), lang))
            else:                                   # long run → speak per digit
                out.append(" ".join(_words_0_99(int(d), lang) for d in run))
            i = j
        else:
            out.append(ch)
            i += 1
    return "".join(out)

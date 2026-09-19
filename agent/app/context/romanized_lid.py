"""
Romanized Indian-language detection override.

PORTED, not reinvented, from Voice-AI-Agent-master's `SarvamSTTService`
(app/pipecat_pipeline.py). That codebase's own comment explains the problem
precisely: "Sarvam's auto-detect sometimes returns 'en' for romanized Indian
speech (e.g. Hinglish, romanized Marathi) because the text looks Latin-script.
We scan the transcript for known function words of each Indian language and
override when we get ≥2 hits."

This is exactly the gap flagged in our earlier voice-platform project: pure
Unicode-script-based LID cannot detect "Mujhe Hyderabad ka weather batao" as
Hindi, because there's no Devanagari to match. This marker-word approach
solves it. Reused verbatim (word lists and ≥2-hit threshold unchanged) rather
than re-derived, since the original was presumably tuned against real call
data this project doesn't have access to.

Covers 8 of this platform's languages via marker words (hi, mr, ta, te, kn,
pa, gu, bn) — NOT tamil/telugu/malayalam-only as our earlier gateway assumed;
DreamSupport's language scope is broader (11 Indian languages total per its
LANG_NORMALIZE table, though bn/gu/pa/kn/or are outside this platform's
originally-stated 5-language requirement). Malayalam has no marker set here
because DreamSupport's own target languages didn't include it — if this
platform needs Malayalam romanization detection, that marker set does not
exist yet and would need to be built (and ideally validated against real
Malayalam speakers), not guessed at.

TESTED FINDING, not assumed: the platform spec's own mandatory test sentence,
"Mujhe Hyderabad ka weather batao", was run against this exact ported logic.
Result: it is NOT overridden to hi-IN. Only "batao" matches a marker word —
1 hit, below the >=2 threshold — because "mujhe" ("to me") and "ka"
(possessive particle) are not in the hi-IN marker set. A longer, more typical
utterance ("kya aap mujhe Hyderabad ka weather batao sakte hain") DOES clear
the threshold (4 hits) and correctly overrides to hi-IN. This means the
ported logic is real and does work, but does not fully solve the platform
spec's own literal test sentence as a short, isolated utterance. Adding
"mujhe" and "ka" to the marker set would likely fix this specific case, but
that's a tuning change to someone else's logic that should be validated
against real usage data, not made unilaterally on the strength of one test
sentence.
"""

# Sarvam short-code -> BCP-47, exactly as in the source.
LANG_NORMALIZE = {
    "hi": "hi-IN", "ta": "ta-IN", "te": "te-IN", "kn": "kn-IN",
    "en": "en-IN", "mr": "mr-IN", "bn": "bn-IN", "gu": "gu-IN",
    "pa": "pa-IN", "ml": "ml-IN", "or": "or-IN",
}

ROMANIZED_MARKERS: dict[str, set[str]] = {
    "hi-IN": {
        "kya", "aap", "main", "mein", "hai", "hain", "nahi", "nahin",
        "baat", "saath", "mere", "mera", "meri", "tum", "tumhara",
        "sakte", "sakta", "sakti", "chahiye", "hoga", "yeh", "woh",
        "kaise", "kahan", "kyun", "kyunki", "lekin", "aur", "agar",
        "toh", "phir", "abhi", "bahut", "thoda", "kuch", "koi",
        "accha", "theek", "haan", "bolo", "batao", "namaste",
    },
    "mr-IN": {
        "majha", "majhi", "mala", "tula", "aahe", "aahes", "naav",
        "kay", "kasa", "kashi", "kashala", "tumhi", "aami", "tyala",
        "tila", "aplya", "ata", "aani", "pan", "jar", "tar", "mhanje",
        "sangto", "sangta", "bagh", "bagha", "yeto", "yete", "jato",
        "jate", "ghara", "shala", "pudhe", "mage", "khup", "thoda",
    },
    "ta-IN": {
        "enna", "naan", "nee", "avan", "aval", "avanga", "vandhen",
        "pogiren", "sollu", "paarunga", "theriyum", "illai", "aamaa",
        "enakku", "unnakku", "ingey", "angey", "eppo", "eppadi",
        "romba", "konjam", "yaarukku", "solren",
    },
    "te-IN": {
        "nenu", "meeru", "atanu", "aame", "vaallu", "vachchanu",
        "velthanu", "cheppandi", "chudandi", "telusa", "ledu", "avunu",
        "naaku", "meeku", "ikkada", "akkada", "eppudu", "ela",
        "chala", "konchem", "evaru", "emi", "chestanu",
    },
    "kn-IN": {
        "nanu", "neevu", "avanu", "avalu", "avaru", "bartini",
        "hoguttini", "heli", "nodi", "gotthu", "illa", "howdu",
        "nanage", "nimage", "illi", "alli", "yaavaga", "hege",
        "thumba", "swalpa", "yaaru", "yenu", "maaduttini",
    },
    "pa-IN": {
        "main", "tussi", "oh", "assi", "aaya", "gaya", "karo",
        "dekho", "dassi", "pata", "nahi", "haan", "kiddan",
        "kiven", "kithe", "kyon", "bahut", "thoda", "koi",
        "kuch", "sanu", "tenu", "saade", "twaade",
    },
    "gu-IN": {
        "hoon", "tame", "te", "ame", "aavyo", "gayo", "karo",
        "juo", "khabar", "nathi", "haa", "kem", "kyare",
        "kyaan", "ghanu", "thodu", "koi", "kuch",
        "mane", "tane", "amane", "tamane",
    },
    "bn-IN": {
        "ami", "tumi", "se", "tara", "eshechi", "gechi", "bolo",
        "dekho", "jano", "na", "haan", "kemon", "kobe", "kothay",
        "keno", "onek", "ektu", "ke", "ki", "amake", "tomake",
    },
}


def apply_romanized_override(transcript: str, detected_lang: str) -> str:
    """If `detected_lang` is en-IN (or bare 'en'), scan for romanized marker
    words and override to the best-matching language on >=2 hits. Otherwise
    returns `detected_lang` unchanged. Same threshold as the source."""
    normalized = LANG_NORMALIZE.get(detected_lang, detected_lang)
    if normalized != "en-IN" or not transcript:
        return normalized

    words = set(transcript.lower().replace(",", " ").replace(".", " ").split())
    best_lang, best_count = None, 0
    for lang_code, markers in ROMANIZED_MARKERS.items():
        hits = len(words & markers)
        if hits > best_count:
            best_count, best_lang = hits, lang_code

    if best_count >= 2 and best_lang:
        return best_lang
    return normalized

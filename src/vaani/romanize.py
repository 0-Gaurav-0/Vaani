"""Devanagari → rough Latin Hinglish (spoken paste, not scholarly IAST)."""
from __future__ import annotations

import re

# Independent vowels
_VOWELS = {
    "अ": "a",
    "आ": "aa",
    "इ": "i",
    "ई": "ee",
    "उ": "u",
    "ऊ": "oo",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "ऋ": "ri",
}

# Consonants (inherent 'a' unless matra / virama follows)
_CONS = {
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "ng",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "झ": "jh",
    "ञ": "ny",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ल": "l",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
    "क्ष": "ksh",
    "त्र": "tr",
    "ज्ञ": "gy",
    "ड़": "d",
    "ढ़": "dh",
    "फ़": "f",
    "ज़": "z",
    "ख़": "kh",
    "ग़": "g",
}

_MATRA = {
    "ा": "aa",
    "ि": "i",
    "ी": "ee",
    "ु": "u",
    "ू": "oo",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    "ृ": "ri",
    "ं": "n",
    "ँ": "n",
    "ः": "h",
}

_VIRAMA = "्"
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]+")


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI_RE.search(text or ""))


# Everyday spellings people actually type in Hinglish (beats mechanical output).
_HINDI_WORDS = {
    "में": "mein", "मैं": "main", "हैं": "hain", "है": "hai", "हूँ": "hoon", "हूं": "hoon",
    "नहीं": "nahi", "नही": "nahi", "क्या": "kya", "क्यों": "kyun", "कैसे": "kaise",
    "कैसा": "kaisa", "कहाँ": "kahan", "कहां": "kahan", "कब": "kab", "कौन": "kaun",
    "यह": "yeh", "ये": "ye", "वह": "woh", "वो": "wo", "और": "aur", "या": "ya",
    "कि": "ki", "की": "ki", "के": "ke", "का": "ka", "को": "ko", "से": "se", "पर": "par",
    "भी": "bhi", "तो": "toh", "ही": "hi", "था": "tha", "थी": "thi", "थे": "the",
    "कर": "kar", "करो": "karo", "करना": "karna", "करके": "karke", "दो": "do", "दे": "de",
    "हो": "ho", "आप": "aap", "तुम": "tum", "हम": "hum", "मुझे": "mujhe", "मेरा": "mera",
    "मेरी": "meri", "मेरे": "mere", "तेरा": "tera", "तेरी": "teri", "अपना": "apna",
    "कल": "kal", "आज": "aaj", "अब": "ab", "बहुत": "bahut", "अच्छा": "accha",
    "ठीक": "theek", "चलो": "chalo", "बताओ": "batao", "बता": "bata", "गाना": "gaana",
    "गाने": "gaane", "प्यार": "pyaar", "यार": "yaar", "सब": "sab", "कुछ": "kuch",
    "एक": "ek", "बस": "bas", "फिर": "phir", "लेकिन": "lekin", "मतलब": "matlab",
    "चाहिए": "chahiye", "सकते": "sakte", "सकता": "sakta", "रहा": "raha", "रही": "rahi",
    "रहे": "rahe", "गया": "gaya", "गयी": "gayi", "गई": "gayi", "वाला": "wala",
    "वाली": "wali", "सिंह": "singh", "जी": "ji", "हाँ": "haan", "हां": "haan",
    "ना": "na", "न": "na", "लगाओ": "lagao", "चलाओ": "chalao", "बजाओ": "bajao",
    "खोलो": "kholo", "दिखाओ": "dikhao", "सुनाओ": "sunao", "सी": "si", "सबसे": "sabse",
}

# English words Whisper-hi writes in Devanagari.
_ENGLISH_LOANWORDS = {
    "प्ले": "play", "प्लेय": "play", "यूट्यूब": "YouTube", "यूटूब": "YouTube",
    "गूगल": "Google", "क्रोम": "Chrome", "जीमेल": "Gmail", "ईमेल": "email", "मेल": "mail",
    "मीटिंग": "meeting", "कॉल": "call", "ओपन": "open", "क्लोज": "close", "फाइल": "file",
    "फ़ाइल": "file", "फोल्डर": "folder", "कोड": "code", "टेस्ट": "test", "बग": "bug",
    "फिक्स": "fix", "रिपो": "repo", "सर्वर": "server", "डेटा": "data", "डाटा": "data",
    "एरर": "error", "इशू": "issue", "इश्यू": "issue", "टास्क": "task", "प्रोजेक्ट": "project",
    "टीम": "team", "क्लाइंट": "client", "कस्टमर": "customer", "अकाउंट": "account",
    "डोमेन": "domain", "मेलबॉक्स": "mailbox", "डैशबोर्ड": "dashboard", "रिपोर्ट": "report",
    "अपडेट": "update", "चेक": "check", "सेंड": "send", "मैसेज": "message", "ऐप": "app",
    "एप": "app", "वेबसाइट": "website", "लिंक": "link", "पेज": "page", "स्क्रीन": "screen",
    "म्यूजिक": "music", "म्यूज़िक": "music", "सॉन्ग": "song", "वीडियो": "video",
    "ट्रेलर": "trailer", "नेटफ्लिक्स": "Netflix", "स्पॉटिफाई": "Spotify", "स्लैक": "Slack",
    "टर्मिनल": "terminal", "ब्राउज़र": "browser", "ब्राउजर": "browser", "विंडो": "window",
    "टैब": "tab", "वॉल्यूम": "volume", "म्यूट": "mute", "पॉज": "pause", "पॉज़": "pause",
    "नेक्स्ट": "next", "स्टॉप": "stop", "स्टार्ट": "start", "ओके": "okay", "सॉरी": "sorry",
    "थैंक": "thank", "यू": "you", "प्लीज": "please", "प्लीज़": "please", "हेलो": "hello",
    "हाय": "hi", "बाय": "bye", "टाइम": "time", "डेट": "date", "वीक": "week", "मंडे": "Monday",
    "लैपटॉप": "laptop", "फोन": "phone", "मोबाइल": "mobile", "इंटरनेट": "internet",
    "सेटिंग": "setting", "सेटिंग्स": "settings", "पासवर्ड": "password", "लॉगिन": "login",
    "वर्क": "work", "वर्कफ्लो": "workflow", "स्टेप": "step", "नेक्स्ट स्टेप": "next step",
    "आइडिया": "idea", "फीचर": "feature", "प्रॉब्लम": "problem", "सॉल्यूशन": "solution",
    "एक्चुअली": "actually", "बेसिकली": "basically", "सो": "so", "बट": "but",
}

_FINAL_LONG = (("aa", "a"), ("ee", "i"), ("oo", "u"))


def _romanize_word(word: str) -> str:
    """Mechanical romanization of one Devanagari word, Hinglish-style."""
    out: list[str] = []
    inherent_last = False
    aksharas = 0
    i = 0
    n = len(word)
    while i < n:
        dig = word[i : i + 2]
        if dig in _CONS:
            cons = _CONS[dig]
            i += 2
        elif word[i] in _VOWELS:
            out.append(_VOWELS[word[i]])
            inherent_last = False
            aksharas += 1
            i += 1
            continue
        elif word[i] in _CONS:
            cons = _CONS[word[i]]
            i += 1
        elif word[i] in _MATRA:
            out.append(_MATRA[word[i]])
            inherent_last = False
            i += 1
            continue
        else:
            i += 1
            continue
        aksharas += 1
        if i < n and word[i] == _VIRAMA:
            out.append(cons)
            inherent_last = False
            aksharas -= 1
            i += 1
            continue
        if i < n and word[i] in _MATRA:
            mat = word[i]
            i += 1
            if mat in {"ं", "ँ", "ः"}:
                out.append(cons + "a" + _MATRA[mat])
            else:
                out.append(cons + _MATRA[mat])
            if i < n and word[i] in {"ं", "ँ"}:
                out.append(_MATRA[word[i]])
                i += 1
            inherent_last = False
            continue
        out.append(cons + "a")
        inherent_last = True
    text = "".join(out)
    # Anusvara before labials is spoken "m": लंबी → lambi.
    text = re.sub(r"n(?=[bpm])", "m", text)
    # Hindi drops the word-final schwa: तुम → tum, not tuma.
    if inherent_last and aksharas > 1 and text.endswith("a"):
        text = text[:-1]
    # Casual spelling shortens final long vowels: gaanaa → gaana, kee → ki.
    if aksharas > 1:
        for long, short in _FINAL_LONG:
            if text.endswith(long):
                text = text[: -len(long)] + short
                break
    return text


def romanize_devanagari(text: str) -> str:
    """Romanize Devanagari to everyday Latin Hinglish; leave Latin unchanged.

    Word lexicons first (common Hindi spellings, English loanwords Whisper-hi
    writes in Devanagari), then mechanical transliteration.
    """
    if not text:
        return ""
    if not has_devanagari(text):
        return text

    def _word(m: re.Match) -> str:
        word = m.group(0)
        bare = word.replace("।", "").replace("॥", "")
        if bare in _ENGLISH_LOANWORDS:
            return _ENGLISH_LOANWORDS[bare] + ("." if "।" in word else "")
        if bare in _HINDI_WORDS:
            return _HINDI_WORDS[bare] + ("." if "।" in word else "")
        out = _romanize_word(bare)
        return out + ("." if "।" in word else "")

    return _DEVANAGARI_RE.sub(_word, text)

"""
Reusable TTML parsing helpers for the podcast-json skill.
Import these functions in your generator scripts.

Key feature: parse_ttml() always returns one segment per sentence,
using word-level span timestamps to assign accurate begin/end times.

Each segment also carries the word-level times of its words ("wordTimings",
[[begin_sec, end_sec, text], ...]) when the TTML has them (Apple Podcasts
transcripts and TTML made by podcast-transcribe). Write them into the JSON:
the listening app uses them for word highlighting and for cutting each
sentence exactly; apps that do not know the key ignore it.
"""
import json
import re
import xml.etree.ElementTree as ET

NS_TT  = "http://www.w3.org/ns/ttml"
NS_POD = "http://podcasts.apple.com/transcript-ttml-internal"

# Words that end with a period but do NOT end a sentence.
# Lowercase comparisons only.
ABBREVS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "vs.",
    "etc.", "e.g.", "i.e.", "approx.", "dept.", "est.",
    # Broadcaster names that appear at the end of BBC transcripts
    "bbc.", "u.s.", "u.k.", "u.n.",
}

# Chinese, Japanese and Korean characters: written without spaces between words
CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿가-힯\U00020000-\U0002fa1f。，、！？；：「」『』（）《》〈〉【】…—]")


def _is_sentence_end(word: str) -> bool:
    """Return True if this word token ends a sentence."""
    w = word.lower()
    # Ellipsis — sentence continues
    if w.endswith("...") or w in ("…", "..."):
        return False
    # Known abbreviations
    if w in ABBREVS:
        return False
    # Single digit or low number followed by period at START of word could be
    # a list item in written text, but in audio transcripts it almost never is.
    # We intentionally do NOT exclude digits here so that "Radio 4.", "BBC 1.",
    # "2026." etc. are correctly recognised as sentence endings.
    # Single letter + period: name initial ("J.", "A.")
    if re.match(r"^[a-zA-Z]\.$", w):
        return False
    # Ends with sentence-terminating punctuation (allowing trailing quote/paren),
    # including the full-width ones used in Chinese and Japanese
    return bool(re.search(r"([.!?][\"'\)\]»]?|[。！？][」』”’）]?)$", word))


def join_words(words):
    """Join word tokens into text: spaces between Latin words, none between CJK words."""
    out = ""
    for w in words:
        if out and not (CJK.search(out[-1]) or CJK.search(w[0]) or re.match(r"^[,.!?;:%)\]}'’”]", w)):
            out += " "
        out += w
    return out


def to_seconds(t) -> float:
    """TTML / JSON time ("HH:MM:SS.mmm", "MM:SS.ms", "12.5s", 12.5) → seconds."""
    if isinstance(t, (int, float)):
        return float(t)
    t = str(t).strip().replace(",", ".")
    m = re.fullmatch(r"([\d.]+)(h|m|s|ms)", t)
    if m:
        return float(m.group(1)) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[m.group(2)]
    secs = 0.0
    for part in t.split(":"):
        secs = secs * 60 + float(part)
    return secs


def norm_time(t) -> str:
    """Normalize any TTML timestamp to HH:MM:SS.mmm.

    Handles: plain seconds (float or int), MM:SS(.ms), HH:MM:SS(.ms),
    and a trailing 's' suffix (e.g. "3661.5s").
    """
    ms = int(round(to_seconds(t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _extract_words_with_timing(p_el):
    """Return list of dicts {word, begin, end, timed} for every spoken word in <p>.

    Uses word-level span begin/end when available; falls back to the
    parent <p> timestamps (begin for every word, end only on the last).
    "timed" is True only when the span has its own times.
    """
    p_begin = p_el.get("begin", "")
    p_end   = p_el.get("end",   "")

    tokens = []
    for span in p_el.iter(f"{{{NS_TT}}}span"):
        if span.get(f"{{{NS_POD}}}unit") == "word":
            w = (span.text or "").strip()
            if not w:
                continue
            sb = span.get("begin", "")
            se = span.get("end",   "")
            tokens.append({"word": w, "begin": sb or p_begin, "end": se or p_end, "timed": bool(sb and se)})

    # Fallback: no word-unit spans found — try plain itertext
    if not tokens:
        raw = " ".join(p_el.itertext()).strip()
        if raw:
            tokens = [{"word": w, "begin": p_begin, "end": p_end, "timed": False}
                      for w in raw.split()]

    # Fill missing word-level timestamps with parent <p> values
    for tok in tokens:
        if not tok["begin"]:
            tok["begin"] = p_begin
        if not tok["end"]:
            tok["end"] = p_end

    return tokens


def _split_into_sentences(tokens):
    """Split a flat list of word tokens into sentence groups.

    Each group is a list of tokens. Split points are words whose text
    looks like a sentence-ending token (ends with . ! ? 。！？), subject to
    the exclusions in _is_sentence_end().

    Returns a list of token-lists, each representing one sentence.
    """
    sentences = []
    current   = []
    for tok in tokens:
        current.append(tok)
        if _is_sentence_end(tok["word"]):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return sentences


def ttml_timing(path: str) -> str:
    """"word" when the TTML has a time for each word (Apple, podcast-transcribe),
    "line" when each <p> is one span with the line's times (older Whisper TTML)."""
    root = ET.parse(path).getroot()
    ps = [p for p in root.iter(f"{{{NS_TT}}}p") if p.get("begin")]
    words = [s for s in root.iter(f"{{{NS_TT}}}span")
             if s.get(f"{{{NS_POD}}}unit") == "word" and s.get("begin")]
    return "word" if ps and len(words) >= 2 * len(ps) else "line"


def parse_ttml(path: str):
    """Parse a TTML file and return (lang, segments).

    Each <p> element is split into individual sentences.
    Word-level span timestamps are used to assign accurate begin/end
    to every sentence.

    Returns:
        lang (str): BCP-47 language tag from xml:lang on the root element.
        segments (list[dict]): Each dict has keys:
            id (int), begin (str), end (str), original (str),
            wordTimings (list, only when the TTML has word-level times):
                [[begin_sec, end_sec, word], ...]
    """
    tree = ET.parse(path)
    root = tree.getroot()
    lang = root.get("{http://www.w3.org/XML/1998/namespace}lang", "en")
    body = root.find(f".//{{{NS_TT}}}body")

    word_level = ttml_timing(path) == "word"
    segs = []
    sid  = 0

    for p in body.iter(f"{{{NS_TT}}}p"):
        if not p.get("begin"):
            continue

        tokens = _extract_words_with_timing(p)
        if not tokens:
            continue

        # Split the <p>'s tokens into individual sentences
        sentences = _split_into_sentences(tokens)

        for sent_tokens in sentences:
            text = join_words([t["word"] for t in sent_tokens]).strip()
            if not text:
                continue
            sid += 1
            seg = {
                "id":       sid,
                "begin":    norm_time(sent_tokens[0]["begin"]),
                "end":      norm_time(sent_tokens[-1]["end"]),
                "original": text,
            }
            if word_level and all(t["timed"] for t in sent_tokens):
                seg["wordTimings"] = [[round(to_seconds(t["begin"]), 3), round(to_seconds(t["end"]), 3), t["word"]]
                                      for t in sent_tokens]
            segs.append(seg)

    return lang, segs


def ttml_words(path: str):
    """All timed words in the TTML, in order: [(begin_sec, end_sec, text)]."""
    root = ET.parse(path).getroot()
    out = []
    for span in root.iter(f"{{{NS_TT}}}span"):
        if span.get(f"{{{NS_POD}}}unit") == "word" and span.get("begin") and span.get("end"):
            text = (span.text or "").strip()
            if text:
                out.append((to_seconds(span.get("begin")), to_seconds(span.get("end")), text))
    out.sort(key=lambda w: (w[0], w[1]))
    return out


def write_episode_json(output: dict, path: str):
    """Write the episode JSON (indent=2, UTF-8) with each segment's wordTimings on one line."""
    marks = {}
    for i, seg in enumerate(output.get("segments", [])):
        if isinstance(seg.get("wordTimings"), list):
            key = f"@@WT{i}@@"
            marks[key] = json.dumps(seg["wordTimings"], ensure_ascii=False, separators=(",", ":"))
    def swap(o):
        return [dict(s, wordTimings=f"@@WT{i}@@") if f"@@WT{i}@@" in marks else s
                for i, s in enumerate(o["segments"])]
    text = json.dumps(dict(output, segments=swap(output)) if marks else output, ensure_ascii=False, indent=2)
    for key, compact in marks.items():
        text = text.replace(f'"{key}"', compact, 1)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python parse_ttml.py <file.ttml>")
        sys.exit(1)
    lang, segs = parse_ttml(sys.argv[1])
    timed = sum(1 for s in segs if "wordTimings" in s)
    print(f"lang={lang}, {len(segs)} segments, timing={ttml_timing(sys.argv[1])}, "
          f"word timings on {timed}/{len(segs)} segments")
    for s in segs[:10]:
        s = dict(s)
        if "wordTimings" in s:
            s["wordTimings"] = f"<{len(s['wordTimings'])} words>"
        print(json.dumps(s, ensure_ascii=False))
    if len(segs) > 10:
        print(f"  ... ({len(segs)-10} more)")

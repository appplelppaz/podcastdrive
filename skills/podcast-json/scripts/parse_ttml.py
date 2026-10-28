"""
Reusable TTML parsing helpers for the podcast-json skill.
Import these functions in your generator scripts.

Key feature: parse_ttml() always returns one segment per sentence,
using word-level span timestamps to assign accurate begin/end times.
"""
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
    # Ends with sentence-terminating punctuation (allowing trailing quote/paren)
    return bool(re.search(r"[.!?][\"'\)\]»]?$", word))


def norm_time(t: str) -> str:
    """Normalize any TTML timestamp to HH:MM:SS.mmm.

    Handles: plain seconds (float or int), MM:SS(.ms), HH:MM:SS(.ms),
    and a trailing 's' suffix (e.g. "3661.5s").
    """
    t = t.rstrip("s")
    parts = t.split(":")
    if len(parts) == 1:
        secs = float(parts[0])
    elif len(parts) == 2:
        secs = int(parts[0]) * 60 + float(parts[1])
    else:
        secs = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s = secs % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _extract_words_with_timing(p_el):
    """Return list of dicts {word, begin, end} for every spoken word in <p>.

    Uses word-level span begin/end when available; falls back to the
    parent <p> timestamps (begin for every word, end only on the last).
    """
    p_begin = p_el.get("begin", "")
    p_end   = p_el.get("end",   "")

    tokens = []
    for span in p_el.iter(f"{{{NS_TT}}}span"):
        if span.get(f"{{{NS_POD}}}unit") == "word":
            w = (span.text or "").strip()
            if not w:
                continue
            sb = span.get("begin", "") or p_begin
            se = span.get("end",   "") or p_end
            tokens.append({"word": w, "begin": sb, "end": se})

    # Fallback: no word-unit spans found — try plain itertext
    if not tokens:
        raw = " ".join(p_el.itertext()).strip()
        if raw:
            tokens = [{"word": w, "begin": p_begin, "end": p_end}
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
    looks like a sentence-ending token (ends with . ! ?), subject to
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


def parse_ttml(path: str):
    """Parse a TTML file and return (lang, segments).

    Each <p> element is split into individual sentences.
    Word-level span timestamps are used to assign accurate begin/end
    to every sentence.

    Returns:
        lang (str): BCP-47 language tag from xml:lang on the root element.
        segments (list[dict]): Each dict has keys:
            id (int), begin (str), end (str), original (str)
    """
    tree = ET.parse(path)
    root = tree.getroot()
    lang = root.get("{http://www.w3.org/XML/1998/namespace}lang", "en")
    body = root.find(f".//{{{NS_TT}}}body")

    segs = []
    sid  = 0

    for div in body.findall(f"{{{NS_TT}}}div"):
        for p in div.findall(f"{{{NS_TT}}}p"):
            if not p.get("begin"):
                continue

            tokens = _extract_words_with_timing(p)
            if not tokens:
                continue

            # Split the <p>'s tokens into individual sentences
            sentences = _split_into_sentences(tokens)

            for sent_tokens in sentences:
                text = " ".join(t["word"] for t in sent_tokens).strip()
                if not text:
                    continue
                sid += 1
                segs.append({
                    "id":       sid,
                    "begin":    norm_time(sent_tokens[0]["begin"]),
                    "end":      norm_time(sent_tokens[-1]["end"]),
                    "original": text,
                })

    return lang, segs


if __name__ == "__main__":
    import sys, json
    if len(sys.argv) < 2:
        print("Usage: python parse_ttml.py <file.ttml>")
        sys.exit(1)
    lang, segs = parse_ttml(sys.argv[1])
    print(f"lang={lang}, {len(segs)} segments")
    for s in segs[:10]:
        print(json.dumps(s, ensure_ascii=False))
    if len(segs) > 10:
        print(f"  ... ({len(segs)-10} more)")

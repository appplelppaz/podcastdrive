"""既存の教材JSONとApple Podcasts / Whisper形式TTMLを読み込む。

時刻は必ず元データ（JSONの begin/end、TTMLの単語時刻）から取り、推測で作らない。
"""

import json
import re
import xml.etree.ElementTree as ET

TTML_NS = "http://www.w3.org/ns/ttml"
PODCASTS_NS = "http://podcasts.apple.com/transcript-ttml-internal"
UNIT_ATTR = "{%s}unit" % PODCASTS_NS
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def parse_time(value):
    """"00:00:07.980" / "07:59.1" / "7.98" / "7,98" / "7.98s" / "980ms" / 数値 → 秒。"""
    if value is None:
        raise ValueError("time is missing")
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", ".")
    m = re.fullmatch(r"([\d.]+)(h|m|s|ms)", s)
    if m:
        n, unit = float(m.group(1)), m.group(2)
        return n * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    total = 0.0
    for part in s.split(":"):
        if part == "":
            raise ValueError("bad time: %r" % value)
        total = total * 60 + float(part)
    return total


def format_time(sec):
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d.%03d" % (h, m, s, ms)


# ---------------------------------------------------------------- JSON


def load_episode_json(path):
    """既存JSONを読み、形式の揺れを吸収した dict を返す。

    返り値: {"metadata": {...}, "segments": [{id, begin, end, original, translation,
              words:[{word, meaning, pos, reading?}], grammar, pinyin?}], "warnings": [...]}
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    warnings = []
    if isinstance(raw, list):
        meta, segs = {}, raw
        warnings.append("トップレベルが配列のため metadata なしとして読み込みました")
    else:
        meta = raw.get("metadata") or {}
        segs = raw.get("segments")
        if segs is None:
            for key in ("sentences", "items", "cues"):
                if isinstance(raw.get(key), list):
                    segs = raw[key]
                    warnings.append("segments の代わりに %s を使用しました" % key)
                    break
        if segs is None:
            raise ValueError("segments が見つかりません: %s" % path)

    out = []
    for i, s in enumerate(segs):
        try:
            begin = parse_time(s.get("begin", s.get("start")))
            end = parse_time(s.get("end", s.get("stop")))
        except (ValueError, TypeError) as e:
            warnings.append("区間 %s の時刻を読めないため除外: %s" % (s.get("id", i + 1), e))
            continue
        if end <= begin:
            warnings.append("区間 %s は end<=begin のため除外" % s.get("id", i + 1))
            continue
        words = []
        for w in s.get("words") or []:
            if isinstance(w, str):
                words.append({"word": w, "meaning": "", "pos": ""})
                continue
            entry = {
                "word": str(w.get("word") or w.get("term") or "").strip(),
                "meaning": str(w.get("meaning") or w.get("ja") or "").strip(),
                "pos": str(w.get("pos") or "").strip(),
            }
            if w.get("reading"):
                entry["reading"] = str(w["reading"]).strip()
            if entry["word"]:
                words.append(entry)
        grammar = s.get("grammar") or ""
        if isinstance(grammar, list):
            grammar = "\n".join(str(g) for g in grammar if g)
        seg = {
            "id": s.get("id", i + 1),
            "begin": begin,
            "end": end,
            "original": str(s.get("original") or s.get("text") or "").strip(),
            "translation": str(s.get("translation") or "").strip(),
            "words": words,
            "grammar": str(grammar).strip(),
        }
        if s.get("pinyin"):
            seg["pinyin"] = str(s["pinyin"]).strip()
        if isinstance(s.get("translationParts"), list):
            seg["translationParts"] = [str(t).strip() for t in s["translationParts"]]
        out.append(seg)
    out.sort(key=lambda x: x["begin"])
    return {"metadata": meta, "segments": out, "warnings": warnings}


# ---------------------------------------------------------------- TTML


def load_ttml(path):
    """TTMLから言語・単語時刻・文（または行）時刻を取り出す。

    返り値: {"lang", "duration", "words": [(b, e, text)], "sentences": [(b, e, text)]}
    Apple形式は podcasts:unit="sentence"/"word" の span、Whisper形式は <p> を文として扱う。
    """
    root = ET.parse(path).getroot()
    lang = root.get(XML_LANG) or ""
    body = root.find("{%s}body" % TTML_NS)
    duration = None
    if body is not None and body.get("dur"):
        duration = parse_time(body.get("dur"))
    words, sentences = [], []
    for el in root.iter():
        unit = el.get(UNIT_ATTR)
        if unit == "word" and el.get("begin") and el.get("end"):
            words.append((parse_time(el.get("begin")), parse_time(el.get("end")), (el.text or "").strip()))
        elif unit == "sentence" and el.get("begin") and el.get("end"):
            text = " ".join((w.text or "").strip() for w in el if w.get(UNIT_ATTR) == "word")
            if not text:
                text = "".join(el.itertext()).strip()
            sentences.append((parse_time(el.get("begin")), parse_time(el.get("end")), text))
    if not sentences:
        for p in root.iter("{%s}p" % TTML_NS):
            if p.get("begin") and p.get("end"):
                sentences.append((parse_time(p.get("begin")), parse_time(p.get("end")),
                                  "".join(p.itertext()).strip()))
    words.sort()
    sentences.sort()
    return {"lang": lang, "duration": duration, "words": words, "sentences": sentences}


def _norm(text):
    return re.sub(r"[\W_]+", "", text.lower())


def align_segment(seg, ttml, snap_tolerance=0.06):
    """区間の時刻をTTMLの単語時刻と照合する。

    単語境界が tolerance 以内にあるときだけ、その単語時刻に合わせる（根拠のある調整のみ）。
    返り値: (begin, end, alignment dict)
    """
    info = {"method": "json"}
    begin, end = seg["begin"], seg["end"]
    if not ttml:
        return begin, end, info
    words = ttml["words"]
    if words:
        wb = min(words, key=lambda w: abs(w[0] - begin))
        we = min(words, key=lambda w: abs(w[1] - end))
        if abs(wb[0] - begin) <= snap_tolerance and abs(we[1] - end) <= snap_tolerance and wb[0] < we[1]:
            begin, end = wb[0], we[1]
            info["method"] = "ttml-words"
            inside = [w for w in words if w[0] >= begin - 1e-3 and w[1] <= end + 1e-3]
            info["wordCount"] = len(inside)
            info["textMatches"] = _norm(" ".join(w[2] for w in inside)) == _norm(seg["original"])
    starts = [s for s in ttml["sentences"] if begin - 1e-3 <= s[0] < end - 1e-3]
    if starts:
        info["ttmlSentences"] = len(starts)
    return begin, end, info


def split_by_ttml_sentences(seg, ttml):
    """1区間に複数の文が入っていて、文ごとの訳（translationParts）があるときだけ、TTMLの文の時刻で分ける。

    TTMLの文の数と訳の数が一致し、文の単語列が原文と一致する場合に限る。
    そうでなければ None（既存の区間を維持）。
    """
    parts = seg.get("translationParts")
    if not parts or not ttml:
        return None
    sents = [s for s in ttml["sentences"]
             if s[0] >= seg["begin"] - 1e-3 and s[1] <= seg["end"] + 1e-3]
    if len(sents) != len(parts) or len(sents) < 2:
        return None
    if _norm(" ".join(s[2] for s in sents)) != _norm(seg["original"]):
        return None
    out = []
    for k, ((b, e, text), tr) in enumerate(zip(sents, parts), 1):
        words = [w for w in seg["words"] if _norm(w["word"]) and _norm(w["word"]) in _norm(text)]
        out.append(dict(seg, begin=b, end=e, original=text, translation=tr, words=words,
                        grammar=seg["grammar"] if k == 1 else "", part=k))
        out[-1].pop("translationParts", None)
    return out


SENTENCE_END = re.compile(r"[.!?。！？…‼⁇][\"'”’»」』）)\]]*$")


def is_sentence_end(text):
    return bool(SENTENCE_END.search(text.strip()))

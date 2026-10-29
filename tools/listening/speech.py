"""画面を見ずに聞くための読み上げテキストと、読み上げ順のパーツ列を作る。

読み上げテキストは教材準備時に一度だけ作って .listening.json に保存する。
再生時にAIや変換処理は呼ばない。

外国語部分の印: 読み上げテキスト中の [[...]] は教材の言語（sourceLanguage）の音声で読む。
"""

import hashlib
import re

CUE_TEXTS = {
    "ja": {"translation": "訳", "vocab": "表現", "grammar": "解説", "again": "もう一度"},
}

# 品詞ラベルなどの揺れ。熟語・句は単語より優先して選ぶ。
PHRASE_POS = ("熟語", "句", "慣用", "表現", "idiom", "phrase", "expression", "collocation")
SKIP_POS = ("固有名詞", "proper", "人名", "地名")

LATIN_LANGS = {"en", "es", "fr", "de", "it", "pt", "nl", "id", "vi", "tr", "pl", "sv", "ca", "ro", "cs", "sk", "hu",
               "fi", "da", "no", "nb", "hr", "sl", "et", "lv", "lt", "ms", "tl", "sw", "ga", "cy", "eu", "gl", "af", "la"}
# 日本語の説明の中の教材言語の語句を見つける文字（app/material.js と同じ）。ラテン文字は ł ş ư などの拡張も含める
LATIN = "A-Za-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F\u1E00-\u1EFF"
SCRIPTS = {
    "ko": "\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F",
    "ru": "\u0400-\u04FF", "uk": "\u0400-\u04FF", "bg": "\u0400-\u04FF", "sr": "\u0400-\u04FF",
    "el": "\u0370-\u03FF", "ar": "\u0600-\u06FF", "fa": "\u0600-\u06FF", "he": "\u0590-\u05FF",
    "th": "\u0E00-\u0E7F", "hi": "\u0900-\u097F",
}
HAN_LANGS = {"zh", "yue", "cmn"}
HAN = "\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF"
KANA = re.compile("[\u3040-\u30FF\u30FC]")
PARTICLES = "はがをのもとにでやへ"

# 1秒あたりの目安（通常速度）。解説の長さの見積もりに使う。
JA_CHARS_PER_SEC = 7.5
WORDS_PER_SEC = 2.6


def clip_id(lang, text):
    return hashlib.sha1(("%s\n%s" % (lang, text)).encode("utf-8")).hexdigest()[:16]


def lang_base(lang):
    return (lang or "").split("-")[0].split("_")[0].lower()


# ---------------------------------------------------------------- 文字の整形


def clean_ja(text):
    """記号・括弧・スラッシュなど、機械的に読まれると不自然なものを話し言葉に直す。"""
    t = text
    t = re.sub(r"【訂正】[^\n]*", "", t)  # 音声認識の訂正メモは読み上げない
    t = re.sub(r"[「」『』\"“”]", "", t)
    t = re.sub(r"[（(]([^（）()]*)[）)]", r"、\1、", t)
    t = re.sub(r"\s*[／/]\s*", "、", t)
    t = re.sub(r"[〜～]", "", t)
    t = re.sub(r"\s*(?:…+|\.{2,})\s*", "、", t)
    t = re.sub(r"\s*[—–―]+\s*", "、", t)
    t = re.sub(r"\s*[＝=→⇒]\s*", "、", t)
    t = re.sub(r"\s*[＋+]\s*", "と", t)
    t = re.sub(r"[*＊#＃_＿|｜<>＜＞\[\]［］{}｛｝]", "", t)
    t = re.sub(r"\s*[:：;；]\s*", "、", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(、\s*)+", "、", t)
    t = re.sub(r"、([。？！])", r"\1", t)
    return t.strip(" 、")


def clean_term(term):
    """外国語の見出し語から読み上げに不要な記号を除く。"""
    t = term
    t = re.sub(r"\bsb\b", "somebody", t)
    t = re.sub(r"\bsth\b", "something", t)
    t = re.sub(r"\bone's\b", "one's", t)
    t = re.sub(r"[（(]([^（）()]*)[）)]", r"\1", t)
    t = re.sub(r"\s*[／/]\s*", ", ", t)
    t = re.sub(r"\s*(?:…+|\.{2,}|[〜～])\s*", " ", t)
    t = re.sub(r"[\[\]{}<>「」『』\"“”*]", "", t)
    return re.sub(r"\s+", " ", t).strip(" ,")


def split_runs(text, source_lang, target_lang="ja", auto_detect=True, original=""):
    """日本語の文を [(lang, text)] の並びに分ける。[[...]] は教材言語。

    auto_detect のとき、教材言語の文字の並びも教材言語として扱う（ラテン文字は2文字以上、
    ハングル・キリル文字などは1文字から、中国語は原文 original にある漢字の並びで前後が日本語の語でないもの）。
    """
    runs = []
    pos = 0
    for m in re.finditer(r"\[\[(.+?)\]\]", text):
        if m.start() > pos:
            runs.extend(_split_plain(text[pos:m.start()], source_lang, target_lang, auto_detect, original))
        runs.append((source_lang, m.group(1).strip()))
        pos = m.end()
    if pos < len(text):
        runs.extend(_split_plain(text[pos:], source_lang, target_lang, auto_detect, original))
    merged = []
    for lang, t in runs:
        t = t.strip()
        if not t or re.fullmatch(r"[\s、。,.!?！？…‥・\-‐–—]+", t):
            if merged and t.strip():
                merged[-1] = (merged[-1][0], merged[-1][1] + t.strip())
            continue
        if merged and merged[-1][0] == lang:
            sep = " " if lang != target_lang and lang_base(lang) not in HAN_LANGS else ""
            merged[-1] = (lang, merged[-1][1] + sep + t)
        else:
            merged.append((lang, t))
    return merged


def _script_runs(text, chars, min_len):
    pattern = f"[{chars}][{chars}'’\\-]*(?:[ ,]+[{chars}][{chars}'’\\-]*)*"
    return [(m.start(), m.end()) for m in re.finditer(pattern, text)
            if len(re.sub(r"[ ,]", "", m.group(0))) >= min_len]


def _han_runs(text, original):
    # 漢字は日本語にもあるので、原文にある漢字の並びで、前が仮名でなく、後ろが仮名なら助詞のものだけ
    if not original:
        return []
    found = []
    for m in re.finditer(f"[{HAN}]+", text):
        w, b, e = m.group(0), m.start(), m.end()
        if w not in original:
            continue
        before = text[b - 1] if b > 0 else ""
        after = text[e] if e < len(text) else ""
        if before and KANA.match(before):
            continue
        if after and KANA.match(after) and after not in PARTICLES:
            continue
        found.append((b, e))
    return found


def _split_plain(text, source_lang, target_lang, auto_detect, original=""):
    base = lang_base(source_lang)
    if not auto_detect:
        return [(target_lang, text)]
    if base in LATIN_LANGS:
        found = _script_runs(text, LATIN, 2)
    elif base in SCRIPTS:
        found = _script_runs(text, SCRIPTS[base], 1)
    elif base in HAN_LANGS:
        found = _han_runs(text, original)
    else:
        return [(target_lang, text)]
    # 教材言語の語句を囲む「」は、語句と一緒に外す（読み上げで区切りになるだけなので）
    out = []
    pos = 0
    for b, e in found:
        if b > pos:
            out.append((target_lang, re.sub(r"[「『]$", "", text[pos:b])))
        out.append((source_lang, text[b:e].strip(" ,")))
        pos = e + 1 if text[e:e + 1] in ("」", "』") else e
    if pos < len(text):
        out.append((target_lang, text[pos:]))
    return out


# ---------------------------------------------------------------- 自動生成（規則ベース）


def pick_meaning(meaning, translation):
    """複数の意味のうち、訳文に現れるもの（＝この文での意味）を選ぶ。なければ最初の意味。"""
    cands = [c.strip() for c in re.split(r"[、，,；;／/]", meaning) if c.strip()]
    if not cands:
        return ""
    for c in cands:
        core = re.sub(r"[（(][^（）()]*[）)]|[〜～]", "", c)
        stem = re.sub(r"(している|する|した|される|な|の|い|だ)$", "", core)
        if (core and core in translation) or (len(stem) >= 2 and stem in translation):
            return c
    return cands[0]


def _rank_words(words):
    def score(i_w):
        i, w = i_w
        pos = w.get("pos", "").lower()
        term = w["word"]
        s = 0.0
        if any(p in pos for p in PHRASE_POS) or " " in term.strip() or "-" in term:
            s += 10
        s += min(len(term), 14) / 2
        return (-s, i)

    usable = [(i, w) for i, w in enumerate(words)
              if w.get("meaning") and not any(p in w.get("pos", "").lower() for p in SKIP_POS)]
    return [w for _, w in sorted(usable, key=score)]


def auto_listening(seg, source_lang, max_vocab=2):
    """表示用の語彙・文法から、耳で聞くための短い解説を規則で作る（作者による解説がないときの代替）。"""
    vocab = []
    for w in _rank_words(seg.get("words") or [])[:max_vocab]:
        meaning = clean_ja(pick_meaning(w["meaning"], seg.get("translation", "")))
        term = clean_term(w["word"])
        if term and meaning:
            vocab.append({"term": term, "meaning": meaning, "note": ""})
    grammar = auto_grammar(seg.get("grammar", ""), source_lang)
    return {"source": "auto", "vocab": vocab, "grammar": grammar}


def auto_grammar(text, source_lang):
    """文法メモを1文の話し言葉にする。式の部分（「…—」の前）は読まない。"""
    t = re.sub(r"【訂正】[^\n]*", "", text or "").strip()
    if not t:
        return ""
    t = t.split("\n")[0]
    if re.search(r"\s[—–―]\s", t):
        head, t = re.split(r"\s[—–―]\s", t, maxsplit=1)
    sentences = [s for s in re.split(r"(?<=[。！？])", t) if s.strip()]
    if not sentences:
        return ""
    s = sentences[0].strip()
    # 先頭が名詞句だけ（例:「間接疑問文。」）なら次の文をつなげて意味が伝わるようにする
    if len(sentences) > 1 and len(s) <= 12:
        s = s.rstrip("。") + "で、" + sentences[1].strip()
    s = clean_ja(s)
    if s and not s.endswith(("。", "？", "！")):
        s += "。"
    return s


# ---------------------------------------------------------------- 見積もり・パーツ


def estimate_seconds(runs, target_lang="ja"):
    total = 0.0
    for lang, text in runs:
        if lang == target_lang:
            total += len(re.sub(r"\s", "", text)) / JA_CHARS_PER_SEC
        else:
            total += max(1, len(text.split())) / WORDS_PER_SEC
    return total


# 語彙の意味から「ここでは」「〜という意味です」のような決まり文句を外す（app/material.js の plainMeaning と同じ）
MEANING_HEAD = re.compile(r"^(?:ここでは|ここでの意味は|この文では|この文での意味は|この文脈では|文脈上は?)[、,，\s]*")
MEANING_TAIL = re.compile(r"[、,，\s]*(?:(?:という|との)意味(?:です|だ|になります|で使われています)?|[、,，]\s*の?意味です|の意味(?:です|になります))[。.]*$")


def plain_meaning(text):
    t = MEANING_TAIL.sub("", MEANING_HEAD.sub("", text.strip()))
    t = re.sub(r"^[、,，\s]+|[、,，\s]+$", "", t)
    t = re.sub(r"^「([^「」]*)」$", r"\1", t)
    return t or text.strip()


def vocab_runs(item, source_lang, target_lang="ja", original=""):
    runs = [(source_lang, item["term"])]
    runs.append((target_lang, plain_meaning(item["meaning"].rstrip("。")).rstrip("。") + "。"))
    if item.get("note"):
        runs.extend(split_runs(item["note"], source_lang, target_lang, original=original))
    return runs


def explanation_seconds(listening, source_lang, target_lang="ja", cue_texts=None):
    cues = cue_texts or CUE_TEXTS["ja"]
    runs = []
    if listening.get("vocab"):
        runs.append((target_lang, cues["vocab"]))
        for item in listening["vocab"]:
            runs.extend(vocab_runs(item, source_lang, target_lang))
    if listening.get("grammar"):
        runs.append((target_lang, cues["grammar"]))
        runs.extend(split_runs(listening["grammar"], source_lang, target_lang))
    return estimate_seconds(runs, target_lang)


def fit_budget(listening, source_lang, target_lang="ja", max_seconds=20.0):
    """解説が長すぎるとき、補足 → 2つ目の語彙の順に削る。削った内容は警告で返す。"""
    notes = []
    if explanation_seconds(listening, source_lang, target_lang) <= max_seconds:
        return listening, notes
    for item in listening.get("vocab", []):
        if item.get("note"):
            item["note"] = ""
            notes.append("補足を省略")
    if explanation_seconds(listening, source_lang, target_lang) > max_seconds and len(listening.get("vocab", [])) > 1:
        dropped = listening["vocab"].pop()
        notes.append("語彙 %s を省略" % dropped["term"])
    return listening, notes


def build_parts(translation_text, listening, source_lang, target_lang="ja", original=""):
    """読み上げ順のパーツ列。回数・オンオフは再生側で適用する（ここでは全部を並べる）。"""
    parts = [{"type": "original", "role": "first"}]
    if translation_text:
        parts.append({"type": "cue", "cue": "translation", "group": "translation"})
        t = clean_ja(translation_text)
        parts.append(_speech("translation", target_lang, t))
    if listening.get("vocab"):
        parts.append({"type": "cue", "cue": "vocab", "group": "vocab"})
        for item in listening["vocab"]:
            for lang, text in vocab_runs(item, source_lang, target_lang, original):
                parts.append(_speech("vocab", lang, text))
    if listening.get("grammar"):
        parts.append({"type": "cue", "cue": "grammar", "group": "grammar"})
        for lang, text in split_runs(listening["grammar"], source_lang, target_lang, original=original):
            parts.append(_speech("grammar", lang, text))
    parts.append({"type": "cue", "cue": "again", "group": "again"})
    parts.append({"type": "original", "role": "second"})
    return parts


def _speech(group, lang, text):
    return {"type": "speech", "group": group, "lang": lang, "text": text, "clip": clip_id(lang, text)}


def build_cues(target_lang="ja"):
    texts = CUE_TEXTS.get(lang_base(target_lang), CUE_TEXTS["ja"])
    return {k: {"lang": target_lang, "text": v, "clip": clip_id(target_lang, v)} for k, v in texts.items()}

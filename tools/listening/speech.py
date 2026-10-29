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

LATIN_LANGS = {"en", "es", "fr", "de", "it", "pt", "nl", "id", "vi", "tr", "pl", "sv"}
# 日本語の説明の中の教材言語（英語・スペイン語・フランス語・中国語）の語句を見つける（app/material.js と同じ）。
# ラテン文字はアクセント付きの文字と œ も含める
LATIN = "A-Za-z\u00C0-\u00D6\u00D8-\u00F6\u00F8-\u024F\u1E00-\u1EFF"
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

    auto_detect のとき、教材言語の文字の並びも教材言語として扱う（ラテン文字は2文字以上か原文にある1文字の語、
    中国語は原文 original にある漢字の並びで日本語の語に見えないもの）。
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
        if not t or re.fullmatch(r"[\s、。,.!?！？，；：…‥・\-‐–—]+", t):
            if merged and t.strip():
                merged[-1] = (merged[-1][0], merged[-1][1] + t.strip())
            continue
        if merged and merged[-1][0] == lang:
            sep = " " if lang != target_lang and lang_base(lang) not in HAN_LANGS else ""
            merged[-1] = (lang, merged[-1][1] + sep + t)
        else:
            merged.append((lang, t))
    return merged


def _script_runs(text, chars, min_len, original=""):
    # AI・EU・GDP のような略語は、原文に出てこなければ日本語の声で読む（日本語の説明の中の略語なので）
    found = []
    for m in re.finditer(f"[{chars}][{chars}'’\\-]*(?:[ ,]+[{chars}][{chars}'’\\-]*)*", text):
        w = m.group(0)
        # 1文字の語（スペイン語の a・y、フランス語の à・y）は、原文に同じ語があるときだけ
        if len(re.sub(r"[ ,]", "", w)) < min_len and not (
                original and re.fullmatch(r"[a-zà-ÿ]", w) and re.search(f"(^|[^{chars}]){w}([^{chars}]|$)", original)):
            continue
        if re.fullmatch(r"[A-Z]{2,5}", w) and w not in original:
            continue
        found.append((m.start(), m.end()))
    return found


def _han_runs(text, original):
    # 中国語（「」の外）: 原文にある漢字の並びで、日本語の語に見えないものだけ。1字なら前が仮名でなく
    # 後ろが仮名でない（か助詞）とき、2字以上なら前後が両方とも仮名でないとき（「この方法を」は日本語のまま）
    if not original:
        return []
    found = []
    for m in re.finditer(f"[{HAN}]+", text):
        w, b, e = m.group(0), m.start(), m.end()
        if w not in original:
            continue
        before = bool(KANA.match(text[b - 1])) if b > 0 else False
        after_ch = text[e] if e < len(text) else ""
        after = bool(KANA.match(after_ch)) if after_ch else False
        if (before or (after and after_ch not in PARTICLES)) if len(w) == 1 else (before and after):
            continue
        found.append((b, e))
    return found


def _split_plain(text, source_lang, target_lang, auto_detect, original=""):
    base = lang_base(source_lang)
    if not auto_detect:
        return [(target_lang, text)]
    if base in LATIN_LANGS:
        found = _script_runs(text, LATIN, 2, original)
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
    grammar = auto_grammar(seg.get("grammar", ""), source_lang, seg.get("original", ""))
    return {"source": "auto", "vocab": vocab, "grammar": grammar}


# 中国語: 文法メモで中国語の語句を書くところ（仮名を含まない「」の中と、「正在＋動詞：…」の「：」より前）の漢字の並びは中国語。
# ただし「動詞」「時間」のような型の説明の語（原文にないもの）は日本語。「」は読み上げ用に整えるときに消えるので、その前に印を付ける
MARK_OPEN, MARK_CLOSE = "\uE000", "\uE001"
PLACEHOLDERS = {"動詞", "名詞", "形容詞", "副詞", "助詞", "助動詞", "代名詞", "数詞", "量詞", "介詞", "主語", "述語",
                "目的語", "補語", "修飾語", "動作", "時間", "場所", "手段", "方法", "人", "物", "事", "数", "数量", "数字", "文", "節", "句",
                "疑問文", "疑問詞", "名詞句", "動詞句", "主題", "対象", "内容", "結果", "理由", "原因", "条件", "程度", "方向", "状態", "人名",
                "地名", "否定", "肯定", "単語", "語", "相手", "自分", "誰", "何"}


def _mark_han(part, original):
    return re.sub(f"[{HAN}]+", lambda m: m.group(0) if m.group(0) in PLACEHOLDERS and m.group(0) not in (original or "")
                  else MARK_OPEN + m.group(0) + MARK_CLOSE, part)


def _mark_quoted_source(text, source_lang, original):
    if lang_base(source_lang) not in HAN_LANGS:
        return text
    t = re.sub(r"[「『]([^「」『』]*)[」』]",
               lambda m: m.group(0) if KANA.search(m.group(1)) else m.group(0)[0] + _mark_han(m.group(1), original) + m.group(0)[-1],
               text)
    head = re.match(r"([^：:＝=。、]+)[：:＝=]", t)
    if head and not KANA.search(head.group(1)) and MARK_OPEN not in head.group(1) and re.search(r"[＋+…]", head.group(1)):
        t = _mark_han(head.group(1), original) + t[len(head.group(1)):]
    return t


def auto_grammar(text, source_lang, original=""):
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
    s = clean_ja(_mark_quoted_source(s, source_lang, original)).replace(MARK_OPEN, "[[").replace(MARK_CLOSE, "]]")
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
    meaning = plain_meaning(item["meaning"].rstrip("。")).rstrip("。") + "。"
    # 意味の中の原語（社会党、Parti Socialiste など）はラテン文字の言語だけ見分ける（漢字は日本語の意味と区別できないので）
    if lang_base(source_lang) in LATIN_LANGS:
        runs.extend(split_runs(meaning, source_lang, target_lang, original=original))
    else:
        runs.append((target_lang, meaning))
    if item.get("note"):
        runs.extend(split_runs(item["note"], source_lang, target_lang, original=original))
    return runs


def speak_listening(sp, source_lang, target_lang="ja"):
    """読み上げ用の指定（speak）から聞き流しの語彙・文法を作る。言語は指定のまま（日本語の文だけ記号を整える）。"""
    vocab = [{"term": term, "meaning": plain_meaning(clean_ja(meaning)), "note": ""} for term, meaning in sp["vocab"]]
    runs = []
    for lang, text in sp["grammar"]:
        is_target = lang_base(lang) == lang_base(target_lang)
        t = clean_ja(text) if is_target else re.sub(r"[「」『』]", "", text).strip(" ")
        if t:
            runs.append([target_lang if is_target else lang, t])
    if runs and runs[-1][0] == target_lang and not runs[-1][1].endswith(("。", "？", "！")):
        runs[-1][1] += "。"
    return {"source": "speak", "vocab": vocab, "grammar": " ".join(r[1] for r in runs), "grammarRuns": runs}


def _item_runs(listening, item, source_lang, target_lang, original=""):
    # speak で指定された語彙は、指定どおりの言語で読む（推測しない）
    if listening.get("source") == "speak":
        return [(source_lang, item["term"]), (target_lang, item["meaning"].rstrip("。") + "。")]
    return vocab_runs(item, source_lang, target_lang, original)


def _grammar_runs(listening, source_lang, target_lang, original=""):
    if listening.get("grammarRuns") is not None:
        return [tuple(r) for r in listening["grammarRuns"]]
    return split_runs(listening["grammar"], source_lang, target_lang, original=original)


def explanation_seconds(listening, source_lang, target_lang="ja", cue_texts=None):
    cues = cue_texts or CUE_TEXTS["ja"]
    runs = []
    if listening.get("vocab"):
        runs.append((target_lang, cues["vocab"]))
        for item in listening["vocab"]:
            runs.extend(_item_runs(listening, item, source_lang, target_lang))
    if listening.get("grammar"):
        runs.append((target_lang, cues["grammar"]))
        runs.extend(_grammar_runs(listening, source_lang, target_lang))
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
            for lang, text in _item_runs(listening, item, source_lang, target_lang, original):
                parts.append(_speech("vocab", lang, text))
    if listening.get("grammar"):
        parts.append({"type": "cue", "cue": "grammar", "group": "grammar"})
        for lang, text in _grammar_runs(listening, source_lang, target_lang, original):
            parts.append(_speech("grammar", lang, text))
    parts.append({"type": "cue", "cue": "again", "group": "again"})
    parts.append({"type": "original", "role": "second"})
    return parts


def _speech(group, lang, text):
    return {"type": "speech", "group": group, "lang": lang, "text": text, "clip": clip_id(lang, text)}


def build_cues(target_lang="ja"):
    texts = CUE_TEXTS.get(lang_base(target_lang), CUE_TEXTS["ja"])
    return {k: {"lang": target_lang, "text": v, "clip": clip_id(target_lang, v)} for k, v in texts.items()}

"""語の難しさ（頻度表 wordfreq を使う）。review.py が、教材の語彙・解説の見直しに使う。

- rare_words: 学習者が知らなそうな語（説明が要る候補）
- too_easy: 説明が要らない易しい語・句
"""
import re

LEVELS = {"B1": (4.3, 5.4), "B2": (3.8, 5.0), "C1": (3.3, 4.7)}  # (これ未満は難しい, これ以上は易しい) Zipf 頻度
LATIN_WORD = re.compile(r"[A-Za-zÀ-ɏḀ-ỿ]+(?:['’-][A-Za-zÀ-ɏḀ-ỿ]+)*")
HAN_RUN = re.compile(r"[㐀-鿿]+")
HAS_JA = re.compile(r"[぀-ヿ㐀-鿿]")
LITERAL_POS = ("熟語", "慣用", "句動詞", "成句", "イディオム", "idiom", "phrasal")


def lang_base(lang):
    return (lang or "").split("-")[0].split("_")[0].lower()


def _zipf(word, lang):
    try:
        from wordfreq import zipf_frequency
    except ImportError:
        return None
    try:
        return zipf_frequency(word, lang)
    except Exception:
        return None


def rare_words(text, lang, level="B2"):
    """頻度表で、このレベルの学習者が知らない可能性が高い語（出てきた順・重複なし）。頻度表がなければ空。"""
    lang = lang_base(lang)
    hard = LEVELS.get(level, LEVELS["B2"])[0]
    out = []
    if lang == "zh":
        try:
            import jieba
        except ImportError:
            return []
        tokens = [t for t in jieba.cut(text) if HAN_RUN.fullmatch(t) and len(t) >= 2]
    else:
        tokens = LATIN_WORD.findall(text)
    for i, tok in enumerate(tokens):
        if lang != "zh" and (len(tok) < 4 or (tok[0].isupper() and i > 0)):
            continue  # 短い語と、文中の大文字の語（固有名詞）は見ない
        z = _zipf(tok.lower(), lang)
        if z is not None and 0 < z < hard and tok.lower() not in (o.lower() for o in out):
            out.append(tok)
    return out


def too_easy(word, lang, level="B2", pos=""):
    """このレベルなら知っているはずの語か。
    1語: 頻度が高い。句: 全部の語がとても易しく、熟語・句動詞・慣用句でもない（「don't know if」など）。"""
    lang = lang_base(lang)
    w = word.strip()
    if not w or (lang == "zh" and len(w) > 2):
        return False
    tokens = w.split() if lang != "zh" else [w]
    if len(tokens) == 1:
        z = _zipf(w.lower(), lang)
        return z is not None and z >= LEVELS.get(level, LEVELS["B2"])[1]
    if len(tokens) > 4 or any(p in (pos or "").lower() for p in LITERAL_POS):
        return False
    zs = [_zipf(t.lower().strip(".,!?"), lang) for t in tokens]
    return all(z is not None and z >= 5.8 for z in zs)

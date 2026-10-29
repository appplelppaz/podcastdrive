"""
Check an episode JSON before handing it over (Language Drill and the listening app).

    python3 validate_json.py "<episode>.json" ["<episode>.ttml"]

Errors (exit code 1) — the file must be fixed:
  - structure: metadata keys, segment keys and types, unique ascending ids,
    times as "HH:MM:SS.mmm" with begin < end and in order, empty original/translation
  - translations shifted against the originals (numbers in the original missing
    from the translation in many segments — the typical one-off shift)
  - with the TTML: segments whose original does not match the words spoken at its time
  - speak (what the listening app reads aloud, and in which language) missing on a
    segment that has words or grammar, or not in the form
    {"vocab": [["原語", "日本語の意味"]], "grammar": [["言語", "文"], ...]} with the
    languages limited to the source language and "ja"
Warnings — fix when easy:
  - pinyin / reading missing for Chinese
  - wordTimings missing although the TTML has word times (run add_word_timings.py)
  - notes that read badly aloud in the listening app (see the skill's
    "聞き流しモード向けの書き方")
  - speak parts that look like the wrong language (source-language words inside a
    "ja" part, Japanese inside a source-language part) or that use symbols
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_ttml import ttml_timing, ttml_words, to_seconds  # noqa: E402
from add_word_timings import match_ratio  # noqa: E402

TIME = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")
KANA = re.compile("[\u3040-\u30ff]")
HAN = re.compile("[\u3400-\u9fff\uf900-\ufaff]{2,}")
LATIN_WORD = re.compile(r"[A-Za-z\u00c0-\u024f\u1e00-\u1eff]{2,}")
SPEAK_SYMBOLS = re.compile(r"[→⇒／/＝=＋+〜～…「」『』（）()]")


def check_speak(sp, lang, original, where, errors, warnings):
    """読み上げ用の指定（speak）の形と、言語の取り違えらしいところを確かめる。"""
    if not isinstance(sp, dict):
        errors.append(f"{where}: speak は {{\"vocab\": [...], \"grammar\": [...]}} にしてください")
        return
    vocab, grammar = sp.get("vocab", []), sp.get("grammar", [])
    if not isinstance(vocab, list) or not all(isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) and x.strip() for x in v)
                                             for v in vocab):
        errors.append(f"{where}: speak.vocab は [[\"原語\", \"日本語の意味\"], ...] にしてください（なければ []）")
        vocab = []
    if not isinstance(grammar, list) or not all(isinstance(g, list) and len(g) == 2 and all(isinstance(x, str) and x.strip() for x in g)
                                               for g in grammar):
        errors.append(f"{where}: speak.grammar は [[\"言語\", \"文\"], ...] にしてください（なければ []）")
        grammar = []
    if len(vocab) > 2:
        warnings.append(f"{where}: speak.vocab は2つまで（3つ目以降は読み上げません）")
    for term, meaning in vocab:
        if KANA.search(term):
            warnings.append(f"{where}: speak.vocab の原語に仮名があります（{term}）")
        if lang != "zh" and LATIN_WORD.search(meaning) and not re.fullmatch(r"[A-Z]{2,5}", LATIN_WORD.search(meaning).group(0)):
            warnings.append(f"{where}: speak.vocab の意味に原語があります（{meaning}）。意味は日本語の声で読みます")
    for g_lang, text in grammar:
        base = g_lang.split("-")[0].lower()
        if base not in (lang, "ja"):
            errors.append(f"{where}: speak.grammar の言語は \"{lang}\" か \"ja\" にしてください（{g_lang}）")
        elif base == "ja":
            if lang == "zh":
                hit = [w for w in HAN.findall(text) if w in original]
                if hit or (not KANA.search(text) and HAN.search(text)):
                    warnings.append(f"{where}: speak.grammar の \"ja\" の部分に中国語らしい語があります（{text}）。中国語は \"zh\" に分けてください")
            elif any(not re.fullmatch(r"[A-Z]{2,5}", w) for w in LATIN_WORD.findall(text)):
                warnings.append(f"{where}: speak.grammar の \"ja\" の部分に原語があります（{text}）。原語は \"{lang}\" に分けてください")
            if SPEAK_SYMBOLS.search(text):
                warnings.append(f"{where}: speak.grammar の日本語は記号（→ ／ ＝ ＋ 〜 … 「」 括弧）を使わず、読み上げる文で書いてください（{text}）")
        elif KANA.search(text):
            warnings.append(f"{where}: speak.grammar の \"{g_lang}\" の部分に仮名があります（{text}）。日本語は \"ja\" に分けてください")
    if len("".join(t for _, t in grammar)) > 90:
        warnings.append(f"{where}: speak.grammar が長すぎます（読み上げは1文・60字程度まで）")


def _plain_numbers(text):
    t = text.translate(FULLWIDTH)
    t = re.sub(r"(?<=\d)[.,  '](?=\d{3}\b)", "", t)  # 50,000 / 50.000 / 50 000

    def kanji(m):  # 5万 / 4万8300 / 1000億 → 数値
        total = 0
        for num, unit in re.findall(r"(\d+)(億|万)?", m.group(0)):
            total += int(num) * {"億": 10 ** 8, "万": 10 ** 4, "": 1}[unit]
        return str(total)
    return re.sub(r"\d+(?:億|万)(?:\d+(?:万)?)*", kanji, t)


def _numbers(text):
    t = _plain_numbers(text)
    # 100 billion → 1000億 のように桁の言い方が変わる数は照合しない
    t = re.sub(r"\d+(?:\.\d+)?\s*(?:billion|million|trillion|thousand|bn|m|mil|millones|milliards?)\b", "", t, flags=re.I)
    return set(re.findall(r"\d{2,}", t))


def numbers_consistent(original, translation):
    nums = _numbers(original)
    return not nums or nums <= set(re.findall(r"\d{2,}", _plain_numbers(translation)))


def validate(json_path, ttml_path=None):
    errors, warnings = [], []
    with open(json_path, encoding="utf-8") as f:
        doc = json.load(f)
    meta = doc.get("metadata")
    if not isinstance(meta, dict):
        errors.append("metadata がありません")
        meta = {}
    for k in ("title", "channel", "episode", "publishDate", "sourceLanguage", "targetLanguage"):
        if not isinstance(meta.get(k), str) or (k != "publishDate" and not meta.get(k)):
            errors.append(f"metadata.{k} がありません")
    if meta.get("publishDate") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta["publishDate"]):
        errors.append("metadata.publishDate は YYYY-MM-DD にしてください")
    lang = (meta.get("sourceLanguage") or "").split("-")[0]
    segs = doc.get("segments")
    if not isinstance(segs, list) or not segs:
        return ["segments がありません"], warnings

    prev_id, prev_begin = 0, -1.0
    empty_tr, mism, no_speak = [], [], []
    with_numbers = 0
    for i, s in enumerate(segs):
        where = f"segments[{i}] (id {s.get('id')})"
        if not isinstance(s.get("id"), int) or s["id"] <= prev_id:
            errors.append(f"{where}: id は 1 から順に増える整数にしてください")
        else:
            prev_id = s["id"]
        for k in ("begin", "end"):
            if not isinstance(s.get(k), str) or not TIME.match(s[k]):
                errors.append(f"{where}: {k} は \"HH:MM:SS.mmm\" にしてください（{s.get(k)!r}）")
        try:
            b, e = to_seconds(s["begin"]), to_seconds(s["end"])
            if e <= b:
                errors.append(f"{where}: end が begin より前です")
            if b < prev_begin - 0.001:
                errors.append(f"{where}: begin が前の区間より前です（時刻順に並べてください）")
            prev_begin = b
        except (KeyError, ValueError, TypeError):
            pass
        if not str(s.get("original") or "").strip():
            errors.append(f"{where}: original が空です")
        if not str(s.get("translation") or "").strip():
            empty_tr.append(s.get("id"))
        if not isinstance(s.get("words"), list):
            errors.append(f"{where}: words は配列にしてください（なければ []）")
        else:
            for w in s["words"]:
                if not isinstance(w, dict) or not w.get("word") or not w.get("meaning"):
                    errors.append(f"{where}: words の各項目に word と meaning が必要です")
                    break
                if lang == "zh" and not w.get("reading"):
                    warnings.append(f"{where}: 中国語の words に reading（ピンイン）がありません（{w.get('word')}）")
        if not isinstance(s.get("grammar", ""), str):
            errors.append(f"{where}: grammar は文字列にしてください（なければ \"\"）")
        if lang == "zh" and not s.get("pinyin"):
            warnings.append(f"{where}: pinyin がありません")
        if "wordTimings" in s and not (isinstance(s["wordTimings"], list) and all(
                isinstance(w, list) and len(w) == 3 for w in s["wordTimings"])):
            errors.append(f"{where}: wordTimings は [[開始秒, 終了秒, \"語\"], ...] にしてください")
        if _numbers(s.get("original", "")):
            with_numbers += 1
            if not numbers_consistent(s.get("original", ""), s.get("translation", "")):
                mism.append(s.get("id"))
        # 聞き流しアプリが読み上げる内容と言語（speak）
        if "speak" in s:
            check_speak(s["speak"], lang, str(s.get("original") or ""), where, errors, warnings)
        elif s.get("words") or str(s.get("grammar") or "").strip():
            no_speak.append(s.get("id"))
        # 聞き流しモード: 文法の1行目を読み上げる。語の意味は最初の項目を読み上げる
        lines = (s.get("grammar") or "").split("\n")
        if any("【訂正】" in ln and ln.index("【訂正】") > 0 for ln in lines):
            warnings.append(f"{where}: 【訂正】メモは文法の説明とは別の行にしてください（読み上げで混ざります）")
        g = next((ln for ln in lines if ln.strip() and not ln.startswith("【訂正】")), "")
        if len(g) > 90:
            warnings.append(f"{where}: grammar の1行目が長すぎます（{len(g)}字。読み上げは1文・60字程度まで）")

    if empty_tr:
        errors.append(f"translation が空の区間: {empty_tr[:20]}")
    if no_speak:
        errors.append(f"speak（聞き流しアプリが読み上げる語彙・文法とその言語）がない区間: {no_speak[:20]}")
    if mism:
        msg = f"原文の数字が訳に見当たらない区間: {mism[:20]}"
        if with_numbers and len(mism) >= 3 and len(mism) / with_numbers >= 0.3:
            errors.append("訳が原文とずれている可能性が高いです（1つずれていないか確認）。" + msg)
        else:
            warnings.append(msg)

    if ttml_path and os.path.exists(ttml_path):
        if ttml_timing(ttml_path) == "word":
            words = ttml_words(ttml_path)
            joiner = "" if lang in ("zh", "ja", "ko") else " "
            bad = []
            for s in segs:
                try:
                    b, e = to_seconds(s["begin"]), to_seconds(s["end"])
                except (KeyError, ValueError, TypeError):
                    continue
                spoken = joiner.join(w[2] for w in words if b <= (w[0] + w[1]) / 2 <= e)
                if match_ratio(s.get("original", ""), spoken) < 0.6:
                    bad.append(s.get("id"))
            if bad:
                errors.append(f"時刻の範囲で話されている語と original が合わない区間: {bad[:20]}"
                              "（時刻か original がずれています）")
            missing = sum(1 for s in segs if not s.get("wordTimings"))
            if missing:
                warnings.append(f"wordTimings がない区間が {missing} あります（add_word_timings.py で付けられます）")
        else:
            warnings.append("TTML に単語ごとの時刻がありません（podcast-transcribe で作り直すと時刻が正確になります）")
    return errors, warnings


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(2)
    ttml = args[1] if len(args) > 1 else os.path.splitext(args[0])[0] + ".ttml"
    errs, warns = validate(args[0], ttml if os.path.exists(ttml) else None)
    for w in warns[:30]:
        print("WARN ", w)
    if len(warns) > 30:
        print(f"WARN  …ほか {len(warns) - 30} 件")
    for e in errs:
        print("ERROR", e)
    print(f"{'NG' if errs else 'OK'}: errors {len(errs)}, warnings {len(warns)}")
    sys.exit(1 if errs else 0)

"""文ごとの訳・語彙・解説を、Mac の Claude Code（claude コマンド）に作ってもらう。

- 追加の料金や API キーは使わない（ログイン済みの Claude Code をそのまま呼ぶ）。
- 語彙と解説は「難しさ」で選ぶ（guide.md）。頻度表（wordfreq）で、
  学習者が知らなそうな語を先に拾って渡し、易しすぎる語の説明は後から外す。
"""
import json
import os
import re
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
BATCH = 30
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


def claude_cli(prompt, model=None, timeout=900):
    """Claude Code を1回呼んで、返事の文章を返す。"""
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("Claude Code (the `claude` command) was not found. Install it and sign in, then try again.")
    cmd = [exe, "-p", "--output-format", "json"] + (["--model", model] if model else [])
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError("Claude Code failed: " + (r.stderr or r.stdout).strip()[:400])
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return r.stdout
    if isinstance(out, dict):
        if out.get("is_error"):
            raise RuntimeError("Claude Code failed: " + str(out.get("result"))[:400])
        return out.get("result") or ""
    return r.stdout


def _json_array(text):
    text = text.strip()
    a, b = text.find("["), text.rfind("]")
    if a < 0 or b <= a:
        raise ValueError("no JSON array in the reply")
    return json.loads(text[a:b + 1])


def _digits(text):
    return set(re.findall(r"\d+", text.translate(str.maketrans("０１２３４５６７８９", "0123456789"))))


def check_batch(items, reply):
    """返事が入力と1対1で対応しているか。問題があれば理由を返す。"""
    if not isinstance(reply, list) or len(reply) != len(items):
        return "expected %d items, got %s" % (len(items), len(reply) if isinstance(reply, list) else "no list")
    drift = 0
    for it, r in zip(items, reply):
        if not isinstance(r, dict) or r.get("id") != it["id"]:
            return "ids do not match at %s" % it["id"]
        if not isinstance(r.get("translation"), str) or not r["translation"].strip():
            return "empty translation at %s" % it["id"]
        nums = {n for n in _digits(it["text"]) if len(n) >= 2}
        if nums and not nums & _digits(r["translation"]):
            drift += 1
    with_nums = sum(1 for it in items if any(len(n) >= 2 for n in _digits(it["text"])))
    if with_nums >= 4 and drift > with_nums / 2:
        return "translations look shifted (numbers do not match)"
    return None


def _short_meaning(meaning):
    """読み上げる意味: 「、」で区切った最初の、日本語を含む項目。"""
    for part in re.split(r"[、,，]", meaning):
        if HAS_JA.search(part):
            return part.strip()
    return ""


def _clean(r, lang, level, original=""):
    """1文ぶんの返事を、教材 JSON の形に整える。
    易しすぎる語と、日本語の意味がない語の説明は外す。訳に挟まった原語は外す。"""
    from listening.speech import strip_glosses
    if "say" not in r and isinstance(r.get("speak"), dict):  # 以前の形で保存した途中の結果
        r = dict(r, say=r["speak"].get("grammar") or [])
    words = []
    for w in r.get("words") or []:
        if not isinstance(w, dict) or not str(w.get("word", "")).strip() or not _short_meaning(str(w.get("meaning", ""))):
            continue
        if too_easy(str(w["word"]), lang, level, str(w.get("pos", ""))):
            continue
        entry = {"word": str(w["word"]).strip(), "meaning": str(w["meaning"]).strip(), "pos": str(w.get("pos", "")).strip()}
        if lang_base(lang) == "zh" and w.get("reading"):
            entry["reading"] = str(w["reading"]).strip()
        words.append(entry)
    say = []
    for run in r.get("say") or []:
        if isinstance(run, (list, tuple)) and len(run) == 2 and str(run[1]).strip():
            code = lang_base(str(run[0]))
            say.append([code if code in ("ja", lang_base(lang)) else "ja", str(run[1]).strip()])
    grammar = str(r.get("grammar") or "").strip()
    if not grammar:
        say = []
    elif not say:
        say = [["ja", grammar]]
    vocab = [[w["word"], _short_meaning(w["meaning"])] for w in words[:2]]
    translation = r["translation"].strip()
    if lang_base(lang) in ("en", "es", "fr", "de", "it", "pt"):
        translation = strip_glosses(translation, original)
    return {"translation": translation, "words": words, "grammar": grammar,
            "speak": {"vocab": vocab, "grammar": say}, "fix": str(r.get("fix") or "").strip()}


def annotate(sentences, lang, level="B2", ask=claude_cli, log=print, progress=None, cache_path=None):
    """sentences: [{"id", "original"}] → {id: {translation, words, grammar, speak, fix}}。

    cache_path があれば、できた分をそこへ保存し、途中で止まっても続きから始める。
    """
    with open(os.path.join(HERE, "guide.md"), encoding="utf-8") as f:
        guide = f.read().replace("{level}", level).replace("{lang}", lang_base(lang))
    # 途中の結果は Claude の返事のまま保存し、返すときに整える（整え方を直したら、作り直しで反映される）
    raw = {}
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            saved = json.load(f)
        if saved.get("level") == level:
            raw = {int(k): v for k, v in saved.get("items", {}).items()}
    originals = {s["id"]: s["original"] for s in sentences}
    done = raw
    todo = [s for s in sentences if s["id"] not in done]
    total = len(sentences)
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        items = [{"id": s["id"], "text": s["original"], "rare": rare_words(s["original"], lang, level)} for s in chunk]
        prompt = guide + "\n\n## 入力（言語: %s）\n\n" % lang + json.dumps(items, ensure_ascii=False, indent=1)
        problem = None
        for attempt in range(3):
            extra = "\n\n前回の返事は使えませんでした（%s）。入力と同じ id・件数で、JSON 配列だけを返してください。" % problem if problem else ""
            try:
                reply = _json_array(ask(prompt + extra))
                problem = check_batch(items, reply)
            except (ValueError, RuntimeError, subprocess.TimeoutExpired) as err:
                if isinstance(err, RuntimeError) and "not found" in str(err):
                    raise
                problem = str(err)[:200]
            if not problem:
                break
            log("Retrying sentences %d–%d (%s)" % (chunk[0]["id"], chunk[-1]["id"], problem))
        if problem:
            raise RuntimeError("Could not get notes for sentences %d–%d: %s" % (chunk[0]["id"], chunk[-1]["id"], problem))
        for r in reply:
            done[r["id"]] = r
        if cache_path:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump({"level": level, "items": done}, f, ensure_ascii=False)
        if progress:
            progress(len(done), total)
        log("Notes %d/%d" % (len(done), total))
    return {i: _clean(r, lang, level, originals.get(i, "")) for i, r in done.items() if i in originals}

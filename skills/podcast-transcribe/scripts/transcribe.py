"""
Make an Apple-Podcasts-style TTML (a time for every word) from a podcast's audio.

    python3 transcribe.py "<episode>.mp3" [--lang zh] [--out "<episode>.ttml"]
                          [--backend auto|mlx|faster] [--model large-v3]
                          [--script auto|keep|simplified|traditional] [--prompt "..."]

- Whisper large-v3 with word timestamps. On an Apple Silicon Mac it uses mlx-whisper
  (pip install mlx-whisper); elsewhere faster-whisper (pip install faster-whisper, needs ffmpeg).
- Output: <p> per sentence, <span podcasts:unit="sentence">, and <span podcasts:unit="word">
  per word with its own begin/end — the same structure as Apple's transcripts, so
  parse_ttml.py, Language Drill's tools and the listening app all read it.
- Sentences end at . ! ? 。！？, at a pause of 1 s or more, or when too long (split at a comma).
- Lowers common Whisper failures: no conditioning on previous text (stops repetition loops),
  voice activity detection (faster-whisper), drops repeated lines and known silence phrases.
- Chinese: --script auto looks at the episode's folder (other TTML/JSON) and keeps the same
  characters (simplified or traditional). Conversion needs OpenCC
  (pip install opencc-python-reimplemented); without it the prompt asks Whisper for that script.
"""
import argparse
import glob
import json
import os
import platform
import re
import subprocess
import sys
import time
from xml.sax.saxutils import escape

SENT_END = re.compile(r"([.!?][\"'\)\]»”’]?|[。！？][」』”’）]?)$")
ABBREVS = {"mr.", "mrs.", "ms.", "dr.", "prof.", "sr.", "jr.", "vs.", "etc.", "e.g.", "i.e.", "u.s.", "u.k.", "u.n."}
CJK = re.compile(r"[぀-ヿ㐀-鿿豈-﫿가-힯]")
PAUSE = 1.0             # 声が途切れたら文を区切る（秒）
MAX_WORDS = 45          # 英語などの1文の上限（語）
MAX_CHARS = 60          # 中国語・日本語・韓国語の1文の上限（字）
# Whisper が無音や音楽のところで作りがちな文（完全一致のときだけ捨てる）
SILENCE_PHRASES = {
    "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目", "字幕由Amara.org社区提供", "优优独播剧场——YoYo Television Series Exclusive",
    "謝謝觀看", "谢谢观看", "感谢观看", "Thank you for watching.", "Thanks for watching!", "Subtitles by the Amara.org community",
}
PROMPTS = {
    ("zh", "simplified"): "以下是普通话的播客节目，使用简体中文，并加上标点符号。",
    ("zh", "traditional"): "以下是國語的播客節目，使用繁體中文，並加上標點符號。",
    ("zh", "keep"): "以下是中文播客节目，并加上标点符号。",
    ("ja", "keep"): "以下はポッドキャストの番組です。句読点を付けます。",
}
# 簡体字だけ・繁体字だけで使う字（字の種類の判定用）
SIMPLIFIED_ONLY = set("这们说为时个来对国会过还发经学开关见动问长门东车马语读书钱")
TRADITIONAL_ONLY = set("這們說為時個來對國會過還發經學開關見動問長門東車馬語讀書錢")


def fmt(t):
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def guess_script(audio_path):
    """Look at other transcripts in the same folder: 'simplified', 'traditional' or 'keep'."""
    simp = trad = 0
    folder = os.path.dirname(os.path.abspath(audio_path))
    pat = glob.escape(folder)  # フォルダ名の [ ] を記号として扱わない
    for p in glob.glob(os.path.join(pat, "*.ttml")) + glob.glob(os.path.join(pat, "*.json")):
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read(200_000)
        except OSError:
            continue
        simp += sum(text.count(c) for c in SIMPLIFIED_ONLY)
        trad += sum(text.count(c) for c in TRADITIONAL_ONLY)
    if simp + trad < 20:
        return "keep"
    return "simplified" if simp > trad else "traditional"


def converter(script):
    if script not in ("simplified", "traditional"):
        return None
    try:
        from opencc import OpenCC
    except ImportError:
        return None
    return OpenCC("t2s" if script == "simplified" else "s2twp").convert


def load_audio_16k(path):
    import numpy as np
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.float32)


def pick_backend(name):
    if name in ("auto", "mlx") and platform.system() == "Darwin" and platform.machine() == "arm64":
        try:
            import mlx_whisper  # noqa: F401
            return "mlx"
        except ImportError:
            if name == "mlx":
                raise SystemExit("mlx-whisper がありません: pip install mlx-whisper")
    try:
        import faster_whisper  # noqa: F401
        return "faster"
    except ImportError:
        raise SystemExit("Whisper がありません。Apple Silicon の Mac: pip install mlx-whisper ／ "
                         "それ以外: pip install faster-whisper（ffmpeg も必要）")


def run_whisper(audio, lang, model, backend, prompt):
    """[(start, end, text, space_before)] for every word, and the detected language."""
    common = dict(language=lang, word_timestamps=True, initial_prompt=prompt,
                  condition_on_previous_text=False)
    words = []
    if backend == "mlx":
        import mlx_whisper
        repo = model if "/" in model else f"mlx-community/whisper-{model}-mlx"
        res = mlx_whisper.transcribe(audio, path_or_hf_repo=repo, hallucination_silence_threshold=2.0, **common)
        for seg in res.get("segments", []):
            for w in seg.get("words", []):
                words.append((float(w["start"]), float(w["end"]), w["word"]))
        detected = res.get("language") or lang
    else:
        from faster_whisper import WhisperModel
        try:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            device = "cpu"
        m = WhisperModel(model, device=device, compute_type="float16" if device == "cuda" else "int8",
                         cpu_threads=os.cpu_count() or 4)
        segs, info = m.transcribe(load_audio_16k(audio), beam_size=5, vad_filter=True,
                                  vad_parameters=dict(min_silence_duration_ms=500),
                                  hallucination_silence_threshold=2.0, **common)
        for seg in segs:
            for w in seg.words or []:
                words.append((float(w.start), float(w.end), w.word))
        detected = info.language
    out = []
    for b, e, t in words:
        text = t.strip()
        if text:
            out.append([b, e, text, t[:1].isspace()])
    return out, detected


def clean_times(words):
    """Times in order, every word at least 20 ms long."""
    prev = 0.0
    for w in words:
        w[0] = max(w[0], prev)
        w[1] = max(w[1], w[0] + 0.02)
        prev = w[1]
    return words


def sentence_end(text):
    t = text.lower()
    if t.endswith("...") or t == "…" or t in ABBREVS or re.match(r"^[a-z]\.$", t):
        return False
    return bool(SENT_END.search(text))


def join(words):
    out = ""
    for w in words:
        text, space = w[2], w[3]
        if out and (space or not (CJK.search(out[-1]) or CJK.search(text[0]))) and not re.match(r"^[,.!?;:%)\]}'’”，。！？、]", text):
            out += " "
        out += text
    return out


def sentences_of(words, cjk):
    sents, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        size = len(re.sub(r"\s", "", join(cur))) if cjk else len(cur)
        too_long = size >= (MAX_CHARS if cjk else MAX_WORDS)
        if nxt is None or sentence_end(w[2]) or nxt[0] - w[1] >= PAUSE:
            sents.append(cur)
            cur = []
        elif too_long:
            # 読点・カンマのところで分ける（なければここで）
            cut = max((k for k, x in enumerate(cur[:-1]) if re.search(r"[,，、;；:：]$", x[2])), default=len(cur) - 1)
            sents.append(cur[:cut + 1])
            cur = cur[cut + 1:]
    sents = [s for s in sents if s]
    # 同じ文が3回以上続くのは Whisper の繰り返し。1つだけ残す。無音で作られがちな文は捨てる
    out = []
    for s in sents:
        text = join(s)
        if text.strip() in SILENCE_PHRASES:
            continue
        if len(out) >= 2 and join(out[-1]) == text and join(out[-2]) == text:
            continue
        out.append(s)
    if cjk:
        for s in out:  # 文末に句点がなければ付ける（時刻は変えない）
            if not re.search(r"[。！？…」』”）]$", s[-1][2]):
                s[-1][2] += "。"
    return out


def write_ttml(path, sents, lang, duration, note):
    cjk_lang = lang.split("-")[0] in ("zh", "ja", "ko")
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<tt xmlns="http://www.w3.org/ns/ttml" xmlns:podcasts="http://podcasts.apple.com/transcript-ttml-internal"'
             f' xmlns:ttm="http://www.w3.org/ns/ttml#metadata" xml:lang="{escape(lang)}">',
             f'<head><metadata><ttm:desc>{escape(note)}</ttm:desc></metadata></head>',
             f'<body dur="{fmt(duration)}"><div begin="{fmt(sents[0][0][0])}" end="{fmt(sents[-1][-1][1])}">']
    for s in sents:
        b, e = fmt(s[0][0]), fmt(s[-1][1])
        sep = "" if cjk_lang else " "
        spans = sep.join(f'<span podcasts:unit="word" begin="{fmt(w[0])}" end="{fmt(w[1])}">{escape(w[2])}</span>' for w in s)
        lines.append(f'<p begin="{b}" end="{e}"><span podcasts:unit="sentence" begin="{b}" end="{e}">{spans}</span></p>')
    lines.append("</div></body></tt>")
    with open(path + ".part", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(path + ".part", path)


def duration_of(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio")
    ap.add_argument("--lang", help="zh, en, fr, es …（省略時は自動判定）")
    ap.add_argument("--out", help="出力 TTML（省略時は音声と同じ名前の .ttml）")
    ap.add_argument("--backend", default="auto", choices=["auto", "mlx", "faster"])
    ap.add_argument("--model", default=os.environ.get("WHISPER_MODEL", "large-v3"),
                    help="モデル名、またはダウンロード済みのモデルのフォルダ（環境変数 WHISPER_MODEL でも指定できる）")
    ap.add_argument("--script", default="auto", choices=["auto", "keep", "simplified", "traditional"])
    ap.add_argument("--prompt", help="Whisper への最初の指示（番組名・固有名詞を入れると精度が上がる）")
    ap.add_argument("--force", action="store_true", help="同じ名前の TTML があっても作り直す")
    a = ap.parse_args(argv)

    out = a.out or os.path.splitext(a.audio)[0] + ".ttml"
    if os.path.exists(out) and not a.force:
        raise SystemExit(f"既にあります（作り直すときは --force。元の TTML は先に別名で残してください）: {out}")
    backend = pick_backend(a.backend)
    lang = a.lang
    script = a.script
    if (lang or "").startswith("zh") and script == "auto":
        script = guess_script(a.audio)
    prompt = a.prompt or PROMPTS.get(((lang or "").split("-")[0], script if lang and lang.startswith("zh") else "keep"))
    t0 = time.time()
    words, detected = run_whisper(a.audio, lang, a.model, backend, prompt)
    lang = lang or detected or "und"
    if lang.startswith("zh") and script == "auto":
        script = guess_script(a.audio)
    conv = converter(script) if lang.startswith("zh") else None
    if conv:
        for w in words:
            w[2] = conv(w[2])
    words = clean_times(words)
    if not words:
        raise SystemExit("音声から言葉を取り出せませんでした")
    sents = sentences_of(words, lang.split("-")[0] in ("zh", "ja", "ko"))
    dur = duration_of(a.audio) or words[-1][1]
    model_name = os.path.basename(os.path.normpath(a.model)) or a.model
    note = f"podcast-transcribe: whisper {model_name} ({backend}), word timestamps" + (f", script {script}" if lang.startswith("zh") else "")
    write_ttml(out, sents, lang, dur, note)
    took = time.time() - t0
    print(json.dumps({"ttml": out, "lang": lang, "backend": backend, "model": a.model, "script": script if lang.startswith("zh") else None,
                      "sentences": len(sents), "words": sum(len(s) for s in sents), "audioSeconds": round(dur, 1),
                      "seconds": round(took, 1), "realtimeFactor": round(took / dur, 2) if dur else None,
                      "opencc": bool(conv) if lang.startswith("zh") and script in ("simplified", "traditional") else None},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()

"""1つのエピソードを、音声（＋字幕）から聞き流しアプリ用の2つのファイルにする。

  音声 ─┬─ 字幕（.ttml）があればそのまま ─┐
        └─ なければ Whisper で作る ───────┴→ 文に分ける → 訳・語彙・解説（Claude Code）
           → 確かめる → 訳・解説の音声（Kokoro）→ 「名前.listening.json」「名前.listening.m4a」

途中の結果は作業フォルダに残し、止まっても続きから始める。
"""
import contextlib
import io
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import annotate as annotate_mod  # noqa: E402
import sources  # noqa: E402
import ttml as ttml_mod  # noqa: E402
import validate as validate_mod  # noqa: E402
from listening import convert, sprite, tts  # noqa: E402

stem_of = sources.stem_of
STEPS = ("Subtitles", "Notes", "Voice")


class Cancelled(Exception):
    pass


def finished(folder):
    """フォルダの中の、できあがったエピソード（.listening.json と .listening.m4a の組）。"""
    out = []
    if folder and os.path.isdir(folder):
        for name in sorted(os.listdir(folder)):
            if name.endswith(".listening.json") and not name.startswith("."):
                stem = name[:-len(".listening.json")]
                files = [os.path.join(folder, stem + ext) for ext in (".listening.json", ".listening.m4a")]
                if os.path.exists(files[1]):
                    out.append({"name": stem, "files": files, "bytes": sum(os.path.getsize(f) for f in files)})
    return out


def out_dir(ep, library, ready):
    """できたファイルを置くフォルダ。
    ライブラリの回は元の音声と同じフォルダ。Apple Podcasts の回はライブラリの「言語/番組名」。言語が分からなければ ready。"""
    if not ep.get("apple"):
        return os.path.dirname(ep["audio"])
    folder = sources.LANG_FOLDER_NAMES.get(ep.get("lang"))
    if not folder or not library or not os.path.isdir(library):
        return ready
    for name in sorted(os.listdir(library)):  # すでにある言語のフォルダ（English など別の名前でも）を使う
        if sources.LANG_FOLDERS.get(name.lower()) == ep["lang"] and os.path.isdir(os.path.join(library, name)):
            folder = name
            break
    return os.path.join(library, folder, sources.channel_of(ep["name"]) or "Podcast")


def scan(library, ready, home=None):
    """(エピソードの一覧, Apple Podcasts を読めなかった理由)。置き場所（out）と、できあがっているか（done）も付ける。"""
    eps, problem = sources.collect(library, home)
    for e in eps:
        e["out"] = out_dir(e, library, ready)
        target = _out_stem(e["name"]) + ".listening.m4a"
        e["done"] = any(os.path.exists(os.path.join(d, target)) for d in {e["out"], ready})
    return eps, problem


def _meta(stem, lang):
    m = re.match(r"^\[(.+?)\]\s+(.+?)\s+\((\d{8})\)$", stem)
    channel, episode, date = (m.group(1), m.group(2), m.group(3)) if m else ("", stem, "")
    return {"title": ("%s - %s" % (channel, episode)) if channel else episode, "channel": channel or "Podcast",
            "episode": episode, "publishDate": "%s-%s-%s" % (date[:4], date[4:6], date[6:]) if date else "",
            "sourceLanguage": lang, "targetLanguage": "ja"}


def _out_stem(stem):
    """できあがるファイルの名前（拡張子の前）。エピソードの名前だけで決まる。"""
    return convert.episode_stem(_meta(stem, ""), stem + ".json")


def make(ep, ready, work, level="B2", voices=None, voice_engine=None, lang=None, voice=True, fresh=False,
         log=print, step=lambda name, done=0, total=0: None, cancelled=lambda: False, ask=None):
    """ep: scan() の1件。できたファイルの場所を返す。"""
    name = ep["name"]
    wdir = os.path.join(work, re.sub(r"[^\w.-]+", "_", name)[:80])
    os.makedirs(wdir, exist_ok=True)
    os.makedirs(ready, exist_ok=True)

    def check():
        if cancelled():
            raise Cancelled()

    # 1. 字幕。なければ音声から作る（単語ごとの時刻付き）
    ttml_path = ep.get("ttml")
    if not ttml_path:
        ttml_path = os.path.join(wdir, "whisper.ttml")
        if not os.path.exists(ttml_path):
            step("Subtitles")
            log("No subtitles. Transcribing with Whisper (this takes a while)…")
            import transcribe
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    transcribe.main([ep["audio"], "--out", ttml_path] + (["--lang", lang] if lang else []))
            except SystemExit as err:
                if err.code not in (0, None):
                    raise RuntimeError(str(err.code))
    check()

    # 2. 訳・語彙・解説。すでに .json がある回はそれを使う
    json_path = None if fresh else ep.get("json")  # fresh: すでにある訳・解説を使わず、作り直す
    if not json_path:
        json_path = os.path.join(wdir, name + ".json")
        source_lang, segs = ttml_mod.parse_ttml(ttml_path)
        source_lang = lang or source_lang
        if not segs:
            raise RuntimeError("No sentences found in the subtitles")
        step("Notes", 0, len(segs))
        notes = annotate_mod.annotate(
            [{"id": s["id"], "original": s["original"]} for s in segs], source_lang, level, log=log,
            progress=lambda d, t: (check(), step("Notes", d, t)), cache_path=os.path.join(wdir, "notes.json"),
            **({"ask": ask} if ask else {}))
        out = []
        for s in segs:
            n = notes[s["id"]]
            seg = {"id": s["id"], "begin": s["begin"], "end": s["end"], "original": n["fix"] or s["original"],
                   "translation": n["translation"], "words": n["words"], "grammar": n["grammar"], "speak": n["speak"]}
            if n["fix"] and n["fix"] != s["original"]:
                seg["grammar"] = (seg["grammar"] + "\n" if seg["grammar"] else "") + "【訂正】音声認識では「%s」となっていたのを訂正。" % s["original"]
            if "wordTimings" in s:
                seg["wordTimings"] = s["wordTimings"]
            out.append(seg)
        ttml_mod.write_episode_json({"metadata": _meta(name, source_lang), "segments": out}, json_path)
        errors, warnings = validate_mod.validate(json_path, ttml_path)
        for w in warnings[:8]:
            log("Check: " + w)
        if errors:
            raise RuntimeError("The notes did not pass the check: " + "; ".join(errors[:3]))
    check()

    # 3. 聞き流しアプリの形にして、訳・解説の音声を作る
    listening = os.path.join(ready, _out_stem(name) + ".listening.json")
    doc = convert.convert(json_path, ttml_path, ep["audio"], listening, log=lambda *a: None)
    for w in doc.get("warnings", [])[:5]:
        log("Check: " + w)
    if not voice:
        return {"json": listening}
    step("Voice")
    log("Making the voice…")
    sprite.build(listening, ep["audio"], voice_engine, voices, "m4a", cache_dir=os.path.join(wdir, "cache"),
                 log=lambda *a: None)
    return {"json": listening, "audio": listening[:-len(".json")] + ".m4a"}


def voice_sample(text, lang, voice, out_wav, engine=None):
    tts.Engine(engine, {lang: voice} if voice else None).synthesize(lang, text, out_wav)

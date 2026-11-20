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
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

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


def library_dir(ep, library):
    """Apple Podcasts の回を集める、ライブラリの「言語/番組名」フォルダ（言語が分からなければ その他/番組名）。"""
    folder = sources.LANG_FOLDER_NAMES.get(ep.get("lang"), "その他")
    if ep.get("lang") and os.path.isdir(library):
        for name in sorted(os.listdir(library)):  # すでにある言語のフォルダ（English など別の名前でも）を使う
            if sources.LANG_FOLDERS.get(name.lower()) == ep["lang"] and os.path.isdir(os.path.join(library, name)):
                folder = name
                break
    return os.path.join(library, folder, sources.channel_of(ep["name"]) or "Podcast")


def collect(ep, library):
    """Apple Podcasts の回の音声と字幕を、ライブラリへコピーする（元のファイルは変えない）。コピー後の ep を返す。"""
    if not ep.get("apple"):
        return ep
    if not library:
        raise RuntimeError("Set the Podcasts folder in Settings.")
    dest = library_dir(ep, library)
    os.makedirs(dest, exist_ok=True)
    out = dict(ep, apple=False, source=os.path.relpath(dest, library))
    for key in ("audio", "ttml"):
        if ep.get(key):
            dst = os.path.join(dest, ep["name"] + os.path.splitext(ep[key])[1].lower())
            if not os.path.exists(dst):
                shutil.copy2(ep[key], dst + ".part")
                os.replace(dst + ".part", dst)
            out[key] = dst
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


def make(ep, ready, work, voices=None, voice_engine=None, lang=None, voice=True,
         log=print, step=lambda name, done=0, total=0: None, cancelled=lambda: False):
    """ep: scan() の1件。できたファイルの場所を返す。"""
    name = ep["name"]
    wdir = os.path.join(work, re.sub(r"[^\w.-]+", "_", name)[:80])
    os.makedirs(wdir, exist_ok=True)
    os.makedirs(ready, exist_ok=True)

    def check():
        if cancelled():
            raise Cancelled()

    # 1. 字幕。なければ音声から作る（単語ごとの時刻付き）。
    #    1行ごとの時刻しかない字幕（以前の Whisper）は、読んでいる単語を強調できないので作り直す（訳・解説の .json はそのまま使う）
    ttml_path = ep.get("ttml")
    if ttml_path and ttml_mod.ttml_timing(ttml_path) != "word":
        lang = lang or sources.language_of(ep) or None
        log("The subtitles have no word times. Transcribing again for word highlighting…")
        ttml_path = None
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

    # 2. 訳・語彙・解説の .json（Claude に作ってもらったもの）。なければ先に作ってもらう
    json_path = ep.get("json")
    if not json_path:
        raise RuntimeError("No notes yet. Ask Claude to make them first (Prompt).")
    # 以前に作った .json（speak がないものなど）もそのまま使えるので、確かめた結果は知らせるだけ
    errors, warnings = validate_mod.validate(json_path, ttml_path)
    for w in (errors + warnings)[:5]:
        log("Check: " + w)
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

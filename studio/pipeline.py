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

import annotate as annotate_mod  # noqa: E402
import ttml as ttml_mod  # noqa: E402
import validate as validate_mod  # noqa: E402
from listening import convert, sprite, tts  # noqa: E402

AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav")
STEPS = ("Subtitles", "Notes", "Voice")


class Cancelled(Exception):
    pass


def stem_of(name):
    return re.sub(r"\.(listening\.json|listening\.m4a|json|ttml|xml|mp3|m4a|aac|wav)$", "", name, flags=re.I)


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


def move_finished(names, ready, drive):
    """選んだエピソードの2つのファイルを Ready から Drive へ移す。移した名前を返す。"""
    if not drive or not os.path.isdir(drive):
        raise RuntimeError("The Drive folder was not found. Set it in Settings.")
    if os.path.realpath(drive) == os.path.realpath(ready):
        raise RuntimeError("Drive and Ready are the same folder.")
    moved = []
    for ep in finished(ready):
        if ep["name"] in names:
            for f in ep["files"]:
                dst = os.path.join(drive, os.path.basename(f))
                shutil.move(f, dst + ".part")
                os.replace(dst + ".part", dst)
            moved.append(ep["name"])
    return moved


def find_drive(home=None):
    """持ち主が作った PodcastDrive フォルダ（Google ドライブと同期している）を探す。なければ空文字。"""
    home = home or os.path.expanduser("~")
    names = ("podcastsdrive", "podcastdrive", "podcasts drive", "podcast drive", "ポッドキャストドライブ")  # 持ち主のフォルダ名は Podcastsdrive
    roots = [os.path.join(home, "Desktop"), os.path.join(home, "Desktop", "Public"), os.path.join(home, "Documents"), home]
    cloud = os.path.join(home, "Library", "CloudStorage")
    if os.path.isdir(cloud):
        for acct in sorted(os.listdir(cloud)):
            if acct.startswith("GoogleDrive"):
                base = os.path.join(cloud, acct)
                roots += [os.path.join(base, d) for d in ("My Drive", "マイドライブ")] + [base]
    for root in roots:
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for n in names:  # 大文字・小文字の違いは気にしない
            for e in entries:
                if e.lower() == n and os.path.isdir(os.path.join(root, e)):
                    return os.path.join(root, e)
    return ""


def scan(inbox, ready, drive=None):
    """Inbox の中のエピソード（同じ名前のファイルの組）と、できあがっているかどうか（Drive に移した分も含む）。"""
    groups = {}
    if os.path.isdir(inbox):
        for name in sorted(os.listdir(inbox)):
            path = os.path.join(inbox, name)
            if name.startswith(".") or not os.path.isfile(path) or ".listening." in name:
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = "audio" if ext in AUDIO_EXTS else "ttml" if ext in (".ttml", ".xml") else "json" if ext == ".json" else None
            if kind:
                groups.setdefault(stem_of(name), {})[kind] = path
    out = []
    for stem, g in groups.items():
        if "audio" not in g:
            continue
        target = _out_stem(stem, g.get("json")) + ".listening.m4a"
        done = any(d and os.path.exists(os.path.join(d, target)) for d in (ready, drive))
        out.append({"name": stem, "audio": g["audio"], "ttml": g.get("ttml"), "json": g.get("json"), "done": done})
    return out


def _meta(stem, lang):
    m = re.match(r"^\[(.+?)\]\s+(.+?)\s+\((\d{8})\)$", stem)
    channel, episode, date = (m.group(1), m.group(2), m.group(3)) if m else ("", stem, "")
    return {"title": ("%s - %s" % (channel, episode)) if channel else episode, "channel": channel or "Podcast",
            "episode": episode, "publishDate": "%s-%s-%s" % (date[:4], date[4:6], date[6:]) if date else "",
            "sourceLanguage": lang, "targetLanguage": "ja"}


def _out_stem(stem, json_path=None):
    meta = {}
    if json_path and os.path.exists(json_path):
        try:
            with open(json_path, encoding="utf-8") as f:
                raw = json.load(f)
            meta = raw.get("metadata", {}) if isinstance(raw, dict) else {}
        except (OSError, ValueError):
            meta = {}
    return convert.episode_stem(meta or _meta(stem, ""), stem + ".json")


def make(ep, ready, work, level="B2", voices=None, voice_engine=None, lang=None, voice=True,
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
    json_path = ep.get("json")
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
    listening = os.path.join(ready, _out_stem(name, json_path) + ".listening.json")
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

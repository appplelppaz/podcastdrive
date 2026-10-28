"""設定を焼き込んだ学習用音声（章付き m4a）を書き出す。

アプリ内の再生が使えないとき（ロック中に止まる端末など）の最終手段。普通の音楽・Podcastアプリで
最後まで流せる。章は区間ごと（車載の「次へ」が使えるプレーヤーでは文単位で移動できる）。

再生順はアプリ（app/engine.js の buildSteps）と同じ。
再生成は必要な部分だけ:
- 速度ごとに伸縮したスプライトをキャッシュ（声の高さを保つ atempo）
- 回数・無音・オンオフの変更は、切り貼りのやり直しだけ
"""

import argparse
import array
import json
import os
import subprocess

from . import sprite as sp
from . import tts as ttsmod
from .convert import sha256_file

SR = ttsmod.SAMPLE_RATE

DEFAULTS = {
    "firstRepeats": 1, "secondRepeats": 1, "originalRate": 0.9,
    "translationRate": 1.0, "explanationRate": 1.0, "gapSeconds": 0.7,
    "translation": True, "vocab": True, "grammar": True, "cues": True,
}
REPEAT_PAUSE = 0.5
CUE_PAUSE = 0.25
GROUP_PAUSE = 0.4
PART_PAUSE = 0.15


# 案内の声は語彙（表現）と解説の前だけ。訳の前の「訳」と、元音声に戻る前の「もう一度」は流さない
CUED_GROUPS = ("vocab", "grammar")


def build_steps(doc, seg, s):
    """1区間ぶんの再生手順 [(kind, key, rate)]。kind は clip / pause。"""
    steps = []
    orig = ("clip", "orig:" + seg["id"], s["originalRate"])
    for r in range(s["firstRepeats"]):
        if r:
            steps.append(("pause", REPEAT_PAUSE, 1))
        steps.append(orig)
    spoke = False
    for group, on, rate in (("translation", s["translation"], s["translationRate"]),
                            ("vocab", s["vocab"], s["explanationRate"]),
                            ("grammar", s["grammar"], s["explanationRate"])):
        parts = [p for p in seg["parts"] if p["type"] == "speech" and p["group"] == group]
        if not on or not parts:
            continue
        steps.append(("pause", GROUP_PAUSE, 1))
        if s["cues"] and group in CUED_GROUPS:
            steps.append(("clip", doc["cues"][group]["clip"], s["explanationRate"]))
            steps.append(("pause", CUE_PAUSE, 1))
        for i, p in enumerate(parts):
            if i:
                steps.append(("pause", PART_PAUSE, 1))
            steps.append(("clip", p["clip"], rate))
        spoke = True
    steps.append(("pause", GROUP_PAUSE if spoke else REPEAT_PAUSE, 1))
    for r in range(s["secondRepeats"]):
        if r:
            steps.append(("pause", REPEAT_PAUSE, 1))
        steps.append(orig)
    steps.append(("pause", s["gapSeconds"], 1))
    return steps


def validate(s):
    for k in ("firstRepeats", "secondRepeats"):
        if not 1 <= int(s[k]) <= 5:
            raise SystemExit("%s は 1〜5 です" % k)
        s[k] = int(s[k])
    if not 0.5 <= s["originalRate"] <= 1.5:
        raise SystemExit("originalRate は 0.5〜1.5 です")
    for k in ("translationRate", "explanationRate"):
        if not 0.5 <= s[k] <= 2.0:
            raise SystemExit("%s は 0.5〜2.0 です" % k)
    if not 0 <= s["gapSeconds"] <= 3:
        raise SystemExit("gapSeconds は 0〜3 です")
    return s


def tempo_wav(sprite_path, rate, cache_dir):
    """スプライト全体を rate 倍速にした WAV（キャッシュ）。"""
    key = sha256_file(sprite_path)[:12]
    path = os.path.join(cache_dir, "export", "sprite-%s-x%.3f.wav" % (key, rate))
    if os.path.exists(path):
        return path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    af = []
    r = rate
    while r < 0.5:  # atempo は 1段あたり 0.5〜2.0
        af.append("atempo=0.5")
        r /= 0.5
    af.append("atempo=%.5f" % r)
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", sprite_path, "-ac", "1",
                    "-ar", str(SR), "-sample_fmt", "s16", "-af", ",".join(af), path + ".part.wav"], check=True)
    os.replace(path + ".part.wav", path)
    return path


def export(listening_path, out_path=None, settings=None, start=None, end=None, log=print):
    with open(listening_path, encoding="utf-8") as f:
        doc = json.load(f)
    if not doc.get("audio", {}).get("sprite"):
        raise SystemExit("先に再生用音声を作ってください（tools/render_listening_audio.py）")
    s = validate(dict(DEFAULTS, **(settings or {})))
    folder = os.path.dirname(os.path.abspath(listening_path))
    cache_dir = os.path.join(folder, ".listening_cache")
    sprite_path = os.path.join(folder, doc["audio"]["sprite"]["file"])
    clips = doc["audio"]["clips"]

    segs = doc["segments"][(start or 1) - 1: end or None]
    rates = {s["originalRate"], s["translationRate"], s["explanationRate"]}
    pcm = {}
    for r in sorted(rates):
        log("速度 %.2f 倍の音声を用意中…" % r)
        pcm[r] = sp.read_wav(tempo_wav(sprite_path, r, cache_dir))

    out = array.array("h")
    chapters = []
    for seg in segs:
        t0 = len(out)
        for kind, key, rate in build_steps(doc, seg, s):
            if kind == "pause":
                out.extend(bytes(int(SR * key) * 2))
                continue
            c = clips[key]
            src = pcm[rate]
            b, e = int(c["start"] / rate * SR), int(c["end"] / rate * SR)
            out.extend(sp.faded(src[b:e]))
        chapters.append((t0, len(out), seg))

    base = os.path.basename(listening_path).replace(".listening.json", "")
    out_path = out_path or os.path.join(folder, base + ".learning.m4a")
    raw = os.path.join(cache_dir, "export.wav")
    sp.write_wav(raw, out)
    meta = os.path.join(cache_dir, "export.ffmeta")
    with open(meta, "w", encoding="utf-8") as f:
        f.write(";FFMETADATA1\ntitle=%s（聞き流し）\n" % _esc(doc["title"]))
        for b, e, seg in chapters:
            name = "%d/%d %s" % (doc["segments"].index(seg) + 1, len(doc["segments"]), seg["original"][:40])
            f.write("[CHAPTER]\nTIMEBASE=1/%d\nSTART=%d\nEND=%d\ntitle=%s\n" % (SR, b, e, _esc(name)))
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", raw, "-i", meta,
                    "-map_metadata", "1", "-map_chapters", "1", "-c:a", "aac", "-b:a", "96k",
                    "-movflags", "+faststart", "-f", "mp4", out_path + ".part"], check=True)
    os.replace(out_path + ".part", out_path)
    os.remove(raw)
    log("書き出し: %s（%.1f 分、章 %d）" % (out_path, len(out) / SR / 60, len(chapters)))
    return out_path, chapters


def _esc(t):
    return t.replace("\\", "\\\\").replace("=", "\\=").replace(";", "\\;").replace("#", "\\#").replace("\n", " ")


def main(argv=None):
    p = argparse.ArgumentParser(description="設定を焼き込んだ学習用音声（章付き m4a）を書き出す")
    p.add_argument("listening")
    p.add_argument("--out")
    p.add_argument("--settings", help="アプリの「設定を書き出す」で保存したJSON")
    p.add_argument("--first", type=int, help="最初の元音声の回数 1〜5")
    p.add_argument("--second", type=int, help="解説後の元音声の回数 1〜5")
    p.add_argument("--speed", type=float, help="元音声の速度 0.5〜1.5")
    p.add_argument("--translation-rate", type=float)
    p.add_argument("--explanation-rate", type=float)
    p.add_argument("--gap", type=float, help="区間の間の無音 0〜3秒")
    p.add_argument("--no-translation", action="store_true")
    p.add_argument("--no-vocab", action="store_true")
    p.add_argument("--no-grammar", action="store_true")
    p.add_argument("--no-cues", action="store_true")
    p.add_argument("--from", dest="start", type=int, help="この区間番号から（1始まり）")
    p.add_argument("--to", dest="end", type=int, help="この区間番号まで")
    a = p.parse_args(argv)
    s = {}
    if a.settings:
        with open(a.settings, encoding="utf-8") as f:
            s.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    for k, v in (("firstRepeats", a.first), ("secondRepeats", a.second), ("originalRate", a.speed),
                 ("translationRate", a.translation_rate), ("explanationRate", a.explanation_rate),
                 ("gapSeconds", a.gap)):
        if v is not None:
            s[k] = v
    for k, flag in (("translation", a.no_translation), ("vocab", a.no_vocab),
                    ("grammar", a.no_grammar), ("cues", a.no_cues)):
        if flag:
            s[k] = False
    export(a.listening, a.out, s, a.start, a.end)


if __name__ == "__main__":
    main()

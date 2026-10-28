"""再生用の1本の音声（スプライト）を作り、.listening.json に参照を書き込む。

構成: [無音 4秒] [区間1の元音声][無音][区間1の訳・解説…][無音] [区間2の元音声]… [案内の音声]…

- ①④で流す元音声は元のファイルから時刻どおりに切り出したもの（合成音声で代用しない）。
- アプリは1つの <audio> の中でシークして再生するので、音が重ならない。
- 各クリップの後に無音を置くので、停止の判定が少し遅れても隣の文は聞こえない。
- 合成音声はクリップごとにキャッシュし、文章が変わったものだけ作り直す。
"""

import argparse
import array
import json
import math
import os
import subprocess
import sys
import wave

from . import tts as ttsmod
from .convert import sha256_file, write_json

SR = ttsmod.SAMPLE_RATE
GAP_REGION = 4.0     # 区間の間の無音（最大3秒）に使う領域
PAD = 0.8            # 各クリップの後の無音
FADE = 0.008


def read_wav(path):
    with wave.open(path, "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == SR, path
        data = array.array("h")
        data.frombytes(w.readframes(w.getnframes()))
    if sys.byteorder == "big":
        data.byteswap()
    return data


def write_wav(path, data):
    out = data
    if sys.byteorder == "big":
        out = array.array("h", data)
        out.byteswap()
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(out.tobytes())


def speech_rms(data):
    """50ms ごとのRMSのうち、無音でない部分の平均（音量合わせの目安）。"""
    n = int(SR * 0.05)
    vals = []
    for i in range(0, len(data) - n, n):
        s = 0
        for x in data[i:i + n:4]:
            s += x * x
        r = math.sqrt(s / (n / 4))
        if r > 300:
            vals.append(r)
    return sum(vals) / len(vals) if vals else 0.0


def scaled(data, gain):
    peak = max((abs(x) for x in data), default=0)
    if peak and peak * gain > 31000:
        gain = 31000 / peak
    if abs(gain - 1) < 0.02:
        return data
    return array.array("h", (max(-32768, min(32767, int(x * gain))) for x in data))


def faded(data):
    n = min(int(SR * FADE), len(data) // 2)
    out = array.array("h", data)
    for i in range(n):
        g = i / n
        out[i] = int(out[i] * g)
        out[-1 - i] = int(out[-1 - i] * g)
    return out


def build(listening_path, audio_path=None, engine_name=None, voices=None, fmt="m4a",
          cache_dir=None, log=print):
    with open(listening_path, encoding="utf-8") as f:
        doc = json.load(f)
    folder = os.path.dirname(os.path.abspath(listening_path))
    if not audio_path:
        name = doc.get("sources", {}).get("audio", {}).get("file")
        if not name:
            raise RuntimeError("元音声が分かりません（--audio で指定してください）")
        audio_path = os.path.join(folder, name)
    cache_dir = cache_dir or os.path.join(folder, ".listening_cache")
    os.makedirs(cache_dir, exist_ok=True)

    audio_sha = sha256_file(audio_path)
    expected = doc.get("sources", {}).get("audio", {}).get("sha256")
    if expected and expected != audio_sha:
        raise RuntimeError("元音声の内容が .listening.json 作成時と異なります: %s" % audio_path)
    orig_wav = os.path.join(cache_dir, "original-%s.wav" % audio_sha[:12])
    if not os.path.exists(orig_wav):
        log("元音声をデコード中…")
        ttsmod.to_wav(audio_path, orig_wav)
    original = read_wav(orig_wav)
    orig_dur = len(original) / SR
    target_rms = speech_rms(original[: SR * 120])

    engine = ttsmod.Engine(engine_name, voices)
    speech_items = {}
    for cue in doc["cues"].values():
        speech_items[cue["clip"]] = (cue["lang"], cue["text"])
    for seg in doc["segments"]:
        for part in seg["parts"]:
            if part["type"] == "speech":
                speech_items[part["clip"]] = (part["lang"], part["text"])

    made = 0
    tts_files = {}
    for cid, (lang, text) in speech_items.items():
        voice = engine.voice_for(lang)
        d = os.path.join(cache_dir, "tts", "%s-%s" % (engine.name, voice.replace(" ", "_")))
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, cid + ".wav")
        if not os.path.exists(path):
            engine.synthesize(lang, text, path)
            made += 1
        tts_files[cid] = path
    log("合成音声 %d 件（新規 %d、キャッシュ利用 %d）" % (len(tts_files), made, len(tts_files) - made))

    out = array.array("h", bytes(int(SR * GAP_REGION) * 2))
    pad = array.array("h", bytes(int(SR * PAD) * 2))
    clips = {"silence": {"start": 0.0, "end": GAP_REGION}}

    def append(key, data):
        start = len(out) / SR
        out.extend(data)
        clips[key] = {"start": round(start, 4), "end": round(len(out) / SR, 4)}
        out.extend(pad)

    def append_tts(cid):
        if cid in clips:
            return
        data = read_wav(tts_files[cid])
        rms = speech_rms(data)
        gain = target_rms / rms if rms and target_rms else 1.0
        append(cid, faded(scaled(data, gain)))

    # 区間ごとに「元音声 → その区間の訳・解説」の順に並べる。万一ロック中にアプリの処理が止まっても、
    # そのまま流れれば学習の順にほぼ沿って聞こえる。
    for seg in doc["segments"]:
        b = int(seg["clip"]["begin"] * SR)
        e = int(min(seg["clip"]["end"], orig_dur) * SR)
        if e <= b:
            raise RuntimeError("区間 %s の時刻が元音声の範囲外です" % seg["id"])
        append("orig:" + seg["id"], faded(original[b:e]))
        for part in seg["parts"]:
            if part["type"] == "speech":
                append_tts(part["clip"])
    for cid in tts_files:
        append_tts(cid)

    base = os.path.basename(listening_path)
    base = base[: -len(".listening.json")] if base.endswith(".listening.json") else os.path.splitext(base)[0]
    sprite_name = base + ".listening." + fmt
    sprite_path = os.path.join(folder, sprite_name)
    raw = os.path.join(cache_dir, "sprite.wav")
    write_wav(raw, out)
    codec = ["-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart"] if fmt == "m4a" \
        else ["-c:a", "libmp3lame", "-b:a", "64k", "-write_xing", "0"]
    # 音楽アプリやファイル一覧でもエピソード名がわかるように、タイトル情報を入れる
    tags = ["-metadata", "title=%s" % (doc.get("episode") or doc.get("title", "")),
            "-metadata", "artist=%s" % doc.get("channel", ""), "-metadata", "album=Listening Mode"]
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", raw] + codec + tags
                   + ["-f", "mp4" if fmt == "m4a" else "mp3", sprite_path + ".part"], check=True)
    os.replace(sprite_path + ".part", sprite_path)
    os.remove(raw)

    # 実際の長さから、各区間の解説の所要時間（通常速度）を記録する
    for seg in doc["segments"]:
        secs = 0.0
        for part in seg["parts"]:
            if part["type"] == "speech" and part["group"] in ("vocab", "grammar"):
                c = clips[part["clip"]]
                secs += c["end"] - c["start"]
            elif part["type"] == "cue" and part["group"] in ("vocab", "grammar"):
                c = clips[doc["cues"][part["cue"]]["clip"]]
                secs += c["end"] - c["start"]
        seg["listening"]["measuredSeconds"] = round(secs, 1)

    doc["audio"] = {
        "mode": "sprite",
        "sprite": {"file": sprite_name, "sha256": sha256_file(sprite_path), "format": fmt,
                   "durationSeconds": round(len(out) / SR, 3), "padSeconds": PAD},
        "tts": {"engine": engine.name, "voices": {k: engine.voice_for(k) for k in
                                                   sorted({l for l, _ in speech_items.values()})}},
        "clips": clips,
    }
    write_json(listening_path, doc)
    log("書き出し: %s（%.1f 分）" % (sprite_path, len(out) / SR / 60))
    return doc


def parse_voices(items):
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        if not v:
            raise SystemExit("--voice は 言語=声 の形で指定してください（例: ja=Kyoko）")
        out[k.strip()] = v.strip()
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=".listening.json から再生用音声を作る")
    p.add_argument("listening", help="エピソード名 (日付).listening.json")
    p.add_argument("--audio", help="元音声（省略時は同じフォルダの元ファイル）")
    p.add_argument("--engine", choices=["say", "openjtalk", "espeak-ng"], help="音声合成（既定: say があれば say）")
    p.add_argument("--voice", action="append", help="言語=声 例: ja=Kyoko en=Daniel")
    p.add_argument("--format", choices=["m4a", "mp3"], default="m4a")
    a = p.parse_args(argv)
    build(a.listening, a.audio, a.engine, parse_voices(a.voice), a.format)


if __name__ == "__main__":
    main()

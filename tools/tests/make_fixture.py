#!/usr/bin/env python3
"""テスト用の架空の教材（音声・TTML・既存形式のJSON）を作る。espeak-ng と ffmpeg が必要。

    python3 tools/tests/make_fixture.py OUT_DIR

本物の番組の音声・字幕はリポジトリに入れられないため、自作の英文から音声を合成し、
その実際の長さから TTML（文・単語の時刻）と既存形式のJSONを書く。
"""

import json
import os
import subprocess
import sys
import tempfile
import wave

SHOWS = {
    "[Demo Radio] Morning market check (20260101)": [
        ("Good morning and welcome to the market check.", "おはようございます。マーケット・チェックへようこそ。", [], ""),
        ("Prices have gone up again this week.", "今週も物価がまた上がりました。",
         [{"word": "gone up", "meaning": "上がった、上昇した", "pos": "句動詞"}], ""),
        ("Many shoppers are looking for cheaper options.", "多くの買い物客が、より安い選択肢を探しています。",
         [{"word": "options", "meaning": "選択肢", "pos": "名詞"},
          {"word": "looking for", "meaning": "探している", "pos": "句動詞"}],
         "are looking for — 現在進行形。今まさに続いている動作を表す。"),
        ("That's all for today.", "今日はここまでです。", [], ""),
    ],
    "[Demo Radio] Weather and travel (20260102)": [
        ("Here is the weather for the weekend.", "週末の天気です。", [], ""),
        ("Heavy rain is expected in the north.", "北部では大雨が予想されています。",
         [{"word": "heavy rain", "meaning": "大雨", "pos": "名詞句"},
          {"word": "is expected", "meaning": "予想されている", "pos": "動詞句"}], ""),
        ("Drivers should take extra care on the roads.", "運転する人は、道路でいつも以上に注意してください。",
         [{"word": "take extra care", "meaning": "いつも以上に注意する", "pos": "熟語"}],
         "should — 助言を表す助動詞。"),
    ],
}


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wav_seconds(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


def fmt(t):
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return "%02d:%02d:%06.3f" % (h, m, s)


def build(out_dir, name, sentences):
    with tempfile.TemporaryDirectory() as tmp:
        pieces, t, segs, ttml_p = [], 0.8, [], []
        silence = os.path.join(tmp, "sil.wav")
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "0.8", silence])
        pieces.append(silence)
        for i, (text, tr, words, grammar) in enumerate(sentences, 1):
            wav = os.path.join(tmp, "s%d.wav" % i)
            run(["espeak-ng", "-v", "en-us", "-s", "150", "-w", wav, text])
            raw = os.path.join(tmp, "r%d.wav" % i)
            run(["ffmpeg", "-y", "-i", wav, "-ar", "22050", "-ac", "1",
                 "-af", "silenceremove=start_periods=1:start_threshold=-50dB,"
                        "areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse", raw])
            d = wav_seconds(raw)
            begin, end = t, t + d
            toks = text.split()
            weights = [len(w) + 1 for w in toks]
            acc, spans = begin, []
            for w, k in zip(toks, weights):
                nxt = acc + d * k / sum(weights)
                spans.append('<span begin="%.3f" end="%.3f" podcasts:unit="word">%s</span>' % (acc, nxt, w))
                acc = nxt
            ttml_p.append('<p begin="%.3f" end="%.3f"><span begin="%.3f" end="%.3f" podcasts:unit="sentence">%s</span></p>'
                          % (begin, end, begin, end, "".join(spans)))
            segs.append({"id": i, "begin": fmt(begin), "end": fmt(end), "original": text,
                         "translation": tr, "words": words, "grammar": grammar})
            pieces += [raw, silence]
            t = end + 0.8
        concat = os.path.join(tmp, "list.txt")
        with open(concat, "w") as f:
            f.writelines("file '%s'\n" % p for p in pieces)
        run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat, "-c:a", "libmp3lame", "-b:a", "64k",
             os.path.join(out_dir, name + ".mp3")])
    ttml = ('<tt xmlns="http://www.w3.org/ns/ttml" xmlns:podcasts="http://podcasts.apple.com/transcript-ttml-internal" '
            'xml:lang="en"><body dur="%.3f"><div>%s</div></body></tt>' % (t, "".join(ttml_p)))
    with open(os.path.join(out_dir, name + ".ttml"), "w", encoding="utf-8") as f:
        f.write(ttml)
    channel, episode = name[1:].split("] ", 1)
    meta = {"title": "%s - %s" % (channel, episode[:-11]), "channel": channel, "episode": episode[:-11],
            "publishDate": "", "sourceLanguage": "en", "targetLanguage": "ja"}
    with open(os.path.join(out_dir, name + ".json"), "w", encoding="utf-8") as f:
        json.dump({"metadata": meta, "segments": segs}, f, ensure_ascii=False, indent=2)


def main():
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    for name, sentences in SHOWS.items():
        build(out, name, sentences)
        print(os.path.join(out, name + ".json"))


if __name__ == "__main__":
    main()

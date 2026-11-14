#!/usr/bin/env python3
"""コマンドで1つ作る（アプリを開かずに使うとき・確認用）。

  python3 studio/make.py "…/[Channel] Title (20260101).mp3"     同じフォルダの .ttml / .json も使う
  python3 studio/make.py "…/Title.json" --voice-engine espeak-ng  すでにある JSON から音声だけ作る
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file", help="音声（.mp3 など）か、すでにある教材の .json")
    p.add_argument("--out", help="できたファイルを置くフォルダ（省略時は同じフォルダ）")
    p.add_argument("--level", default="B2", choices=["B1", "B2", "C1"])
    p.add_argument("--lang", help="zh, en, es, fr（字幕がないとき。省略時は自動）")
    p.add_argument("--voice", action="append", help="言語=声 例: ja=jm_kumo")
    p.add_argument("--voice-engine", choices=["kokoro", "say", "openjtalk", "espeak-ng"])
    p.add_argument("--no-voice", action="store_true", help=".listening.json だけ作る")
    p.add_argument("--new-notes", action="store_true", help="すでにある .json を使わず、訳・解説を作り直す")
    a = p.parse_args()
    folder = os.path.dirname(os.path.abspath(a.file))
    name = pipeline.stem_of(os.path.basename(a.file))
    ep = next((e for e in pipeline.sources.library_episodes(folder) if e["name"] == name), None)
    if not ep:
        raise SystemExit("音声が見つかりません: " + name)
    voices = dict(v.split("=", 1) for v in a.voice or [])
    out = pipeline.make(ep, a.out or folder, os.path.join(a.out or folder, ".studio_work"), a.level, voices,
                        a.voice_engine, a.lang, not a.no_voice, a.new_notes)
    print("\n".join(out.values()))


if __name__ == "__main__":
    main()

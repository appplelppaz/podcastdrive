#!/usr/bin/env python3
"""教材1本の準備をまとめて行う: 既存JSON → .listening.json → 再生用音声（スプライト）。

  python3 tools/prepare_listening.py "英語/Business Daily/[Business Daily] ... (20260101).json"

同名の .ttml と .mp3 を同じフォルダから探す。2回目以降は、作者の解説と合成音声のキャッシュを
引き継ぎ、変わった部分だけ作り直す。
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from listening import convert, sprite  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("json", help="既存の教材JSON")
    p.add_argument("--authored", help="聞くための解説（作者による）JSON")
    p.add_argument("--engine", choices=["say", "openjtalk", "espeak-ng"])
    p.add_argument("--voice", action="append", help="言語=声 例: ja=Kyoko")
    p.add_argument("--format", choices=["m4a", "mp3"], default="m4a")
    p.add_argument("--text-only", action="store_true", help=".listening.json だけ作る（音声は作らない）")
    a = p.parse_args()
    ttml = convert.find_sibling(a.json, (".ttml",))
    audio = convert.find_sibling(a.json, convert.AUDIO_EXTS)
    out = convert.listening_path_for(a.json)
    previous = None
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            previous = json.load(f)
    doc = convert.convert(a.json, ttml, audio, out, a.authored, previous=previous)
    for w in doc["warnings"]:
        print("警告:", w, file=sys.stderr)
    print("書き出し:", out)
    if a.text_only:
        return
    if not audio:
        raise SystemExit("元音声（同名の .mp3/.m4a）が見つかりません")
    sprite.build(out, audio, a.engine, sprite.parse_voices(a.voice), a.format)


if __name__ == "__main__":
    main()

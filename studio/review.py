#!/usr/bin/env python3
"""作った教材 .json の語彙・解説・訳を、難しさの基準で見直すための一覧を出す（Claude に渡すプロンプトから使う）。

  python3 review.py "<episode>.json" [--level B2]

出すもの（直すべき候補。決めるのは読む人）:
  - 頻度の低い語なのに説明がない（rare）
  - 易しすぎる語・句に説明がある（easy）
  - 日本語の意味がない語彙（no meaning）
  - 訳の中に原語が挟まっている（gloss）
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from difficulty import HAS_JA, rare_words, too_easy  # noqa: E402
from listening.speech import strip_glosses  # noqa: E402


def review(path, level="B2"):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    lang = (doc.get("metadata") or {}).get("sourceLanguage", "")
    out = []
    for s in doc.get("segments") or []:
        words = s.get("words") or []
        known = " ".join(w.get("word", "") for w in words).lower()
        rare = [w for w in rare_words(s.get("original", ""), lang, level) if w.lower() not in known]
        easy = [w["word"] for w in words if too_easy(w.get("word", ""), lang, level, w.get("pos", ""))]
        empty = [w.get("word", "") for w in words if not HAS_JA.search(str(w.get("meaning", "")))]
        tr = s.get("translation", "")
        gloss = strip_glosses(tr, s.get("original", "")) != tr
        notes = []
        if rare:
            notes.append("rare: " + ", ".join(rare))
        if easy:
            notes.append("easy: " + ", ".join(easy))
        if empty:
            notes.append("no meaning: " + ", ".join(empty))
        if gloss:
            notes.append("gloss in translation")
        if notes:
            out.append("%s: %s" % (s.get("id"), " | ".join(notes)))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("json")
    p.add_argument("--level", default="B2", choices=["B1", "B2", "C1"])
    a = p.parse_args()
    lines = review(a.json, a.level)
    print("\n".join(lines) if lines else "OK: nothing to review")


if __name__ == "__main__":
    main()

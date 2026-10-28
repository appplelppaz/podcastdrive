#!/usr/bin/env python3
"""訳の訂正データ（元の名前.fix.json）を既存JSONに当てて、訂正版と .listening.json を作る。

    python3 tools/apply_fix.py "…/[Business Daily] Iran’s economy_ Counting the cost of war (20260101).json"

同じフォルダの「元の名前.fix.json」を読み、次を書き出す（元のJSONは変更しない）。
- 元の名前.retranslated.json   既存と同じ形式の訂正版（translation・words・grammar を差し替え）
- エピソード名 (日付).listening.json  聞き流し用（訂正データの「聞くための解説」入り）

訂正データの形式:
  {"format": "podcast-listening-fix", "formatVersion": 1, "base": {"file": "…", "sha256": "…"}, "note": "…",
   "segments": {"9": {"translation": "…", "vocab": [[語, この文での意味, 品詞, 補足], …], "grammar": "…",
                      "fix": "【訂正】…", "translationSpeech": "…"},
                "10#1": {…}, "10#2": {…},          ← TTMLの文境界で分ける区間
                "3": {"translation": null, "vocab": […]}}}  ← 訳は既存のまま、聞くための解説だけ
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from listening import convert  # noqa: E402


def apply(json_path, fix_path):
    with open(json_path, encoding="utf-8") as f:
        orig = json.load(f)
    with open(fix_path, encoding="utf-8") as f:
        fix = json.load(f)
    if fix.get("format") != "podcast-listening-fix":
        raise SystemExit("訂正データの形式が違います: %s" % fix_path)
    base_sha = fix.get("base", {}).get("sha256")
    if base_sha and base_sha != convert.sha256_file(json_path):
        raise SystemExit("訂正データは別の版のJSON用です（%s）" % fix["base"].get("file"))

    strip = lambda t: re.sub(r"\[\[(.+?)\]\]", r"\1", t)  # noqa: E731
    entries = fix["segments"]
    new_segs, authored = [], {}
    for seg in orig["segments"]:
        keys = sorted((k for k in entries if k.split("#")[0] == str(seg["id"])),
                      key=lambda k: int(k.split("#")[1]) if "#" in k else 0)
        seg = dict(seg)
        if keys:
            parts = [entries[k] for k in keys]
            if parts[0].get("translation") is not None:
                if len(parts) > 1:
                    seg["translationParts"] = [p["translation"] for p in parts]
                    seg["translation"] = "".join(seg["translationParts"])
                else:
                    seg["translation"] = parts[0]["translation"]
                seg["words"] = [{"word": w, "meaning": m, "pos": pos}
                                for p in parts for w, m, pos, _ in p.get("vocab", [])]
                seg["grammar"] = "\n".join(t for p in parts for t in (p.get("fix", ""), strip(p.get("grammar", ""))) if t)
            for k, p in zip(keys, parts):
                a = {"vocab": [{"term": w, "meaning": m, "note": n} for w, m, _, n in p.get("vocab", [])],
                     "grammar": p.get("grammar", "")}
                if p.get("translationSpeech"):
                    a["translationSpeech"] = p["translationSpeech"]
                authored[k] = a
        new_segs.append(seg)

    stem = json_path[:-5]
    meta = dict(orig.get("metadata", {}))
    if fix.get("note"):
        meta["note"] = fix["note"]
    fixed_path = stem + ".retranslated.json"
    with open(fixed_path, "w", encoding="utf-8") as f:
        json.dump({"metadata": meta, "segments": new_segs}, f, ensure_ascii=False, indent=2)
    authored_path = stem + ".listening-authored.json"
    with open(authored_path, "w", encoding="utf-8") as f:
        json.dump({"segments": authored}, f, ensure_ascii=False, indent=1)

    out = convert.listening_path_for(json_path, orig.get("metadata", {}))
    previous = None
    if os.path.exists(out):
        with open(out, encoding="utf-8") as f:
            previous = json.load(f)
    doc = convert.convert(fixed_path, convert.find_sibling(json_path, (".ttml",)),
                          convert.find_sibling(json_path, convert.AUDIO_EXTS), out, authored_path,
                          previous=previous, original_json_path=json_path)
    for w in doc["warnings"]:
        print("警告:", w, file=sys.stderr)
    print("書き出し:", fixed_path)
    print("書き出し:", out)
    return doc


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    json_path = sys.argv[1]
    fix_path = sys.argv[2] if len(sys.argv) > 2 else json_path[:-5] + ".fix.json"
    apply(json_path, fix_path)


if __name__ == "__main__":
    main()

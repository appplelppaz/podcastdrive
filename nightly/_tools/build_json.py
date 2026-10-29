"""
TTML の文と、訳・語彙・文法のデータから、Language Drill と聞き流しアプリ用の JSON を作る。

    python build_json.py dump   "<…>.ttml" --from 1 --to 40   # 文の番号と原文を表示（訳を書くため）
    python build_json.py status "<…>.ttml"                    # 訳を書いた番号・まだの番号
    python build_json.py build  "<…>.ttml"                    # JSON を書き出して検証する（エラーなら終了コード 1）

訳のデータは _work/<TTMLの名前>/ に trans_001.json, trans_002.json … として置く（1ファイル40文まで）:
    {"12": {"t": "日本語訳", "w": [{"word": "…", "meaning": "…", "pos": "…", "reading": "（中国語のみ）"}],
            "g": "文法の説明（なければ空）", "p": "（中国語のみ）全文のピンイン",
            "s": {"v": [["原語", "日本語の意味"]], "g": [["zh", "原来"], ["ja", "と"], ["zh", "才是"], ["ja", "で、実はこちらこそがそうだった、という気づきを表します。"]]}}}
- "s" は聞き流しアプリが読み上げる語彙（2つまで）と文法と、その言語。JSON の "speak" になる（Language Drill は使わない）。
  語彙・文法がない文は "s": {"v": [], "g": []}。
- キーは dump で表示された番号（文字列）。1つの番号に1つの訳。番号をまたいで訳をまとめたり分けたりしない。
- 原文の誤認識を直すときだけ "o": "直した原文" を入れる（直した理由は g の別の行に【訂正】として書く）。
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "scripts"))
from parse_ttml import parse_ttml, write_episode_json  # noqa: E402
from validate_json import validate  # noqa: E402

ROOT = os.environ.get("PODCAST_ROOT") or os.path.dirname(HERE)
NAME = re.compile(r"^\[(?P<ch>.+?)\]\s+(?P<title>.+?)\s+\((?P<date>\d{8})\)$")


def work_dir(ttml):
    d = os.path.join(ROOT, "_work", os.path.splitext(os.path.basename(ttml))[0])
    os.makedirs(d, exist_ok=True)
    return d


def load_data(ttml):
    data, problems = {}, []
    # 名前の [ ] を記号として扱わない
    for path in sorted(glob.glob(os.path.join(glob.escape(work_dir(ttml)), "trans_*.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                part = json.load(f)
        except (OSError, ValueError) as e:
            problems.append(f"{os.path.basename(path)} を読めません: {e}")
            continue
        for k, v in part.items():
            if not str(k).isdigit() or not isinstance(v, dict):
                problems.append(f"{os.path.basename(path)}: 形が違う項目 {k!r}")
                continue
            data[int(k)] = v
    return data, problems


def cmd_dump(ttml, a, b):
    lang, segs = parse_ttml(ttml)
    print(f"# lang={lang} sentences={len(segs)}")
    for s in segs:
        if a <= s["id"] <= b:
            print(f"{s['id']}\t{s['original']}")


def cmd_status(ttml):
    lang, segs = parse_ttml(ttml)
    data, problems = load_data(ttml)
    missing = [s["id"] for s in segs if s["id"] not in data or not str(data[s["id"]].get("t", "")).strip()]
    extra = sorted(set(data) - {s["id"] for s in segs})
    print(json.dumps({"lang": lang, "sentences": len(segs), "done": len(segs) - len(missing),
                      "missing_first": missing[:1], "missing_count": len(missing), "extra_ids": extra[:20],
                      "problems": problems, "work_dir": work_dir(ttml)}, ensure_ascii=False, indent=1))


def cmd_build(ttml):
    lang, segs = parse_ttml(ttml)
    data, problems = load_data(ttml)
    missing = [s["id"] for s in segs if s["id"] not in data or not str(data[s["id"]].get("t", "")).strip()]
    if problems or missing:
        print(json.dumps({"ok": False, "problems": problems, "missing": missing[:50]}, ensure_ascii=False, indent=1))
        return 1
    base = os.path.splitext(os.path.basename(ttml))[0]
    m = NAME.match(base)
    channel = m.group("ch") if m else os.path.basename(os.path.dirname(ttml))
    episode = m.group("title") if m else base
    date = f"{m.group('date')[:4]}-{m.group('date')[4:6]}-{m.group('date')[6:]}" if m else ""
    zh = lang.split("-")[0] == "zh"
    out = []
    for s in segs:
        d = data[s["id"]]
        words = []
        for w in d.get("w") or []:
            if not isinstance(w, dict) or not w.get("word") or not w.get("meaning"):
                continue
            e = {"word": str(w["word"]).strip(), "meaning": str(w["meaning"]).strip(), "pos": str(w.get("pos", "")).strip()}
            if zh and w.get("reading"):
                e["reading"] = str(w["reading"]).strip()
            words.append(e)
        obj = {"id": s["id"], "begin": s["begin"], "end": s["end"],
               "original": str(d.get("o") or s["original"]).strip(),
               "translation": str(d["t"]).strip(), "words": words, "grammar": str(d.get("g") or "").strip()}
        if zh:
            obj["pinyin"] = str(d.get("p") or "").strip()
        sp = d.get("s")
        if isinstance(sp, dict):
            obj["speak"] = {"vocab": sp.get("v", []), "grammar": sp.get("g", [])}
        if "wordTimings" in s:
            obj["wordTimings"] = s["wordTimings"]
        out.append(obj)
    doc = {"metadata": {"title": f"{channel} - {episode}", "channel": channel, "episode": episode,
                        "publishDate": date, "sourceLanguage": lang, "targetLanguage": "ja"},
           "segments": out}
    path = os.path.splitext(ttml)[0] + ".json"
    tmp = path + ".part"
    write_episode_json(doc, tmp)
    errors, warnings = validate(tmp, ttml)
    if errors:
        os.remove(tmp)
        print(json.dumps({"ok": False, "errors": errors, "warnings": warnings[:20]}, ensure_ascii=False, indent=1))
        return 1
    os.replace(tmp, path)
    print(json.dumps({"ok": True, "json": path, "segments": len(out), "warnings": warnings[:20]}, ensure_ascii=False, indent=1))
    return 0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd, ttml = sys.argv[1], sys.argv[2]
    if cmd == "dump":
        args = sys.argv[3:]
        a = int(args[args.index("--from") + 1]) if "--from" in args else 1
        b = int(args[args.index("--to") + 1]) if "--to" in args else 10 ** 9
        cmd_dump(ttml, a, b)
        return 0
    if cmd == "status":
        cmd_status(ttml)
        return 0
    if cmd == "build":
        return cmd_build(ttml)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())

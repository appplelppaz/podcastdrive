"""
夜間処理の受け箱（podcast フォルダの直下）を調べ、処理の終わった回を言語別フォルダへ移す。

    python inbox.py scan                       # 次に何をするかを JSON で出す
                                               #   resume: 言語別フォルダにあって JSON がまだない回（先に処理する）
                                               #   inbox:  直下に置かれたファイルを回ごとにまとめたもの
    python inbox.py place --key KEY --lang zh --channel "..." --title "..." --date YYYYMMDD
                                               # 音声・TTML・JSON を 言語/チャンネル/[チャンネル] 題名 (日付).拡張子 へ移す
    python inbox.py review --key KEY --reason "..."   # 処理できない回を _needs_review へ移す（理由を書いたメモも置く）

フォルダ（podcast の直下）:
  受け箱    … 直下に置かれた .mp3 .m4a .wav .aac .ttml .json
  英語/ 中国語/ … 完成した回（言語/チャンネル/）
  _work/   … 作業中のファイル    _needs_review/ … 人が見る必要がある回
  _duplicates/ … 同じ名前の回が既にあったときの退避先    _logs/ … 実行記録
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import xml.etree.ElementTree as ET

ROOT = os.environ.get("PODCAST_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIO = (".mp3", ".m4a", ".wav", ".aac")
LANG_LABELS = {"en": "英語", "fr": "フランス語", "es": "スペイン語", "zh": "中国語", "de": "ドイツ語",
               "it": "イタリア語", "pt": "ポルトガル語", "ko": "韓国語", "ja": "日本語"}
UNKNOWN_LABEL = "その他"
NAME = re.compile(r"^\[(?P<ch>.+?)\]\s+(?P<title>.+?)\s+\((?P<date>\d{8})\)$")
BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')  # Windows で使えない文字


def safe(text, limit=120):
    """ファイル名に使える形に（使えない文字は _、前後の空白とピリオドを除く、長すぎれば切る）。"""
    t = BAD.sub("_", unicodedata.normalize("NFC", text or "")).strip().strip(".")
    t = re.sub(r"\s+", " ", t)
    return t[:limit].rstrip()


def stem_of(name):
    base, ext = os.path.splitext(name)
    if ext.lower() == ".json" and base.lower().endswith((".listening", ".fix", ".before-fix")):
        return None  # 他のアプリ用・控えのファイルは扱わない
    return base


def ffprobe_tags(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:format_tags", "-of", "json", path],
                           capture_output=True, text=True, encoding="utf-8", timeout=60)
        fmt = json.loads(r.stdout or "{}").get("format", {})
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}, None
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    try:
        dur = float(fmt.get("duration"))
    except (TypeError, ValueError):
        dur = None
    return tags, dur


def ttml_info(path):
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return None, None
    lang = root.get("{http://www.w3.org/XML/1998/namespace}lang")
    ps = [p for p in root.iter("{http://www.w3.org/ns/ttml}p") if p.get("begin")]
    words = [s for s in root.iter("{http://www.w3.org/ns/ttml}span")
             if s.get("{http://podcasts.apple.com/transcript-ttml-internal}unit") == "word" and s.get("begin")]
    timing = "word" if ps and len(words) >= 2 * len(ps) else "line"
    return (lang or "").split("-")[0].lower() or None, timing


def date_from(tags):
    for k in ("date", "creation_time", "year", "tdrc", "tdat"):
        m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", tags.get(k, ""))
        if m:
            return "".join(m.groups())
    return None


def file_date(path):
    from datetime import datetime
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y%m%d")
    except (OSError, TypeError):
        return None


def scan():
    groups = {}
    for name in sorted(os.listdir(ROOT)):
        path = os.path.join(ROOT, name)
        if not os.path.isfile(path) or name.startswith((".", "~$")):
            continue
        ext = os.path.splitext(name)[1].lower()
        if ext not in AUDIO + (".ttml", ".json"):
            continue
        stem = stem_of(name)
        if stem is None:
            continue
        g = groups.setdefault(unicodedata.normalize("NFC", stem), {"key": unicodedata.normalize("NFC", stem), "files": {}})
        kind = "audio" if ext in AUDIO else ext[1:]
        g["files"][kind] = path
    out = []
    for g in groups.values():
        f = g["files"]
        info = {"key": g["key"], "files": f}
        m = NAME.match(g["key"])
        tags, dur = ffprobe_tags(f["audio"]) if "audio" in f else ({}, None)
        lang, timing = ttml_info(f["ttml"]) if "ttml" in f else (None, None)
        info["durationMinutes"] = round(dur / 60, 1) if dur else None
        info["ttmlTiming"] = timing
        info["proposed"] = {
            "channel": m.group("ch") if m else tags.get("album") or tags.get("artist"),
            "title": m.group("title") if m else tags.get("title") or g["key"],
            "date": m.group("date") if m else date_from(tags),
            "lang": lang or (tags.get("language") or "")[:2].lower() or None,
            "nameFromFile": bool(m),
            # 配信日が分からないときの代わり（ファイルの更新日）
            "fileDate": file_date(f.get("audio") or f.get("ttml") or f.get("json")),
        }
        if "json" in f and "ttml" not in f and "audio" not in f:
            info["action"] = "review: JSON だけでは処理できない"
        elif "ttml" in f and timing == "word":
            info["action"] = "json_from_ttml"
        elif "audio" in f:
            info["action"] = "transcribe_then_json"  # TTML なし、または1行ごとの時刻だけの古い TTML
        elif "ttml" in f:
            info["action"] = "json_from_ttml"        # 音声なし・1行ごとの時刻の TTML（そのまま使う）
        else:
            info["action"] = "review: 対象のファイルがない"
        if "json" in f and info["action"] != "review: JSON だけでは処理できない":
            info["note"] = "JSON が既にある。作り直さず、置き場所と名前だけ整える（action=place_only）"
            info["action"] = "place_only"
        out.append(info)
    # 小さい回から（短い時間で多くの回を終える）
    out.sort(key=lambda x: (x["action"].startswith("review"), x["durationMinutes"] or 0))
    return out


def pending_placed():
    """言語別フォルダに移したが JSON がまだない回（前の夜に途中で止まったものなど）。"""
    out = []
    for label in list(LANG_LABELS.values()) + [UNKNOWN_LABEL]:
        top = os.path.join(ROOT, label)
        if not os.path.isdir(top):
            continue
        for dirpath, dirs, files in os.walk(top):
            dirs[:] = [d for d in dirs if not d.startswith(("_", "."))]
            for name in sorted(files):
                if name.lower().endswith(".ttml") and not os.path.exists(os.path.join(dirpath, name[:-5] + ".json")):
                    path = os.path.join(dirpath, name)
                    lang, timing = ttml_info(path)
                    out.append({"key": name[:-5], "placed": True, "ttml": path, "ttmlTiming": timing, "lang": lang,
                                "action": "json_from_ttml"})
    return out


def place(key, lang, channel, title, date):
    groups = {g["key"]: g for g in scan()}
    if key not in groups:
        raise SystemExit(f"見つかりません: {key}")
    g = groups[key]
    channel, title = safe(channel, 60), safe(title, 120)
    if not channel or not title or not re.fullmatch(r"\d{8}", date or ""):
        raise SystemExit("channel・title・date（YYYYMMDD）が必要です")
    base = f"[{channel}] {title} ({date})"
    folder = os.path.join(ROOT, LANG_LABELS.get((lang or "").split("-")[0], UNKNOWN_LABEL), channel)
    os.makedirs(folder, exist_ok=True)
    moved = {}
    for kind, src in g["files"].items():
        ext = os.path.splitext(src)[1].lower()
        dst = os.path.join(folder, base + ext)
        if os.path.exists(dst):
            dup = os.path.join(ROOT, "_duplicates")
            os.makedirs(dup, exist_ok=True)
            dst = os.path.join(dup, base + ext)
            n = 2
            while os.path.exists(dst):
                dst = os.path.join(dup, f"{base} ({n}){ext}")
                n += 1
        shutil.move(src, dst)
        moved[kind] = dst
    return {"folder": folder, "base": base, "files": moved}


def review(key, reason):
    groups = {g["key"]: g for g in scan()}
    if key not in groups:
        raise SystemExit(f"見つかりません: {key}")
    dest = os.path.join(ROOT, "_needs_review")
    os.makedirs(dest, exist_ok=True)
    for src in groups[key]["files"].values():
        shutil.move(src, os.path.join(dest, os.path.basename(src)))
    with open(os.path.join(dest, safe(key) + ".理由.txt"), "w", encoding="utf-8") as f:
        f.write(reason.strip() + "\n")
    return {"moved_to": dest}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    p = sub.add_parser("place")
    for k in ("key", "lang", "channel", "title", "date"):
        p.add_argument("--" + k, required=True)
    r = sub.add_parser("review")
    r.add_argument("--key", required=True)
    r.add_argument("--reason", required=True)
    a = ap.parse_args()
    if a.cmd == "scan":
        res = {"resume": pending_placed(), "inbox": scan()}
    elif a.cmd == "place":
        res = place(a.key, a.lang, a.channel, a.title, a.date)
    else:
        res = review(a.key, a.reason)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

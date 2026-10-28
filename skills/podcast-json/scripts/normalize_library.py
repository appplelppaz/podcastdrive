#!/usr/bin/env python3
"""
Podcasts ライブラリの名寄せ・重複整理・言語別フォルダ再編スクリプト。

処理内容:
  1. 同一チャンネルの重複フォルダ（名前の切り詰め違い）を統合
  2. 旧形式「タイトル.ext」を新形式「[チャンネル] タイトル (YYYYMMDD).ext」へ統一
  3. 同一エピソードの重複音声を _duplicates/ へ退避（削除はしない）
  4. TTML / JSON から各チャンネルの言語を判定し、言語フォルダへ移動
       Podcasts/英語/<チャンネル>/..., Podcasts/フランス語/..., Podcasts/スペイン語/... など
  5. ライブラリ全体の状態を library_status.json に書き出し

Usage:
    python3 normalize_library.py [--root PATH] [--apply]

デフォルトは dry-run（変更内容の表示のみ）。--apply で実際に変更する。
"""

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata
from datetime import datetime

AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav")
MEDIA_EXTS = AUDIO_EXTS + (".ttml", ".json", ".context.json")
NEW_FMT = re.compile(r"^\[(?P<ch>.+?)\]\s+(?P<title>.+?)\s+\((?P<date>\d{8})\)$")
SKIP_DIRS = {"_duplicates", "__pycache__"}

LANG_LABELS = {
    "en": "英語", "fr": "フランス語", "es": "スペイン語",
    "zh": "中国語", "de": "ドイツ語", "it": "イタリア語", "pt": "ポルトガル語",
}
UNKNOWN_LABEL = "その他"

# TTML/JSON から言語を判定できないチャンネル用の手動マップ
LANG_OVERRIDES = {
    "EL MUNDO al día": "es",
    "Easy French": "fr",
    "Easy Spanish": "es",
    "Español con Juan": "es",
    "Españolistos": "es",
    "French Baratin": "fr",
    "Les Pachas": "fr",
    "Standard Issue Podcast": "en",
    "The Girls Bathroom": "en",
    "World Business Report": "en",
    "姐妹悄悄話": "zh",
}


def norm(s: str) -> str:
    """照合用の正規化: NFKC → 小文字 → 英数字と CJK 以外を除去。"""
    s = unicodedata.normalize("NFKC", s).lower()
    return "".join(ch for ch in s if ch.isalnum())


def split_known_ext(fname: str):
    if fname.endswith(".context.json"):
        return fname[: -len(".context.json")], ".context.json"
    base, ext = os.path.splitext(fname)
    return base, ext


def collect(dirpath: str):
    """dir 直下のメディアファイルを basename ごとにグループ化。"""
    groups = {}
    for f in sorted(os.listdir(dirpath)):
        if f.startswith(".") or f == "library_status.json":
            continue
        p = os.path.join(dirpath, f)
        if not os.path.isfile(p):
            continue
        base, ext = split_known_ext(f)
        if ext.lower() not in MEDIA_EXTS:
            continue
        groups.setdefault(base, {})[ext.lower()] = f
    return groups


def channel_dirs(root: str):
    """言語フォルダの下も含め、メディアを直接含むチャンネルフォルダを列挙。"""
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith((".", "_"))]
        if dirpath == root:
            continue
        if any(split_known_ext(f)[1].lower() in MEDIA_EXTS for f in files):
            out.append(dirpath)
    return out


# ── 1. チャンネルフォルダ統合 ────────────────────────────────────────────────

def merge_channel_dirs(root: str, apply: bool):
    """名前の切り詰め違いで分裂したチャンネルフォルダを統合（短い方→長い方）。"""
    dirs = channel_dirs(root)
    by_norm = {}
    for d in dirs:
        by_norm.setdefault(norm(os.path.basename(d))[:40], []).append(d)
    merged = 0
    names = [(os.path.basename(d), d) for d in dirs]
    for name_a, dir_a in names:
        for name_b, dir_b in names:
            if dir_a == dir_b or not os.path.isdir(dir_a) or not os.path.isdir(dir_b):
                continue
            na, nb = norm(name_a), norm(name_b)
            # a が b の切り詰め（前方一致・a の方が短い・十分な長さ）
            if len(na) >= 20 and len(na) < len(nb) and nb.startswith(na):
                print(f"  MERGE FOLDER: {name_a}  ->  {name_b}")
                if apply:
                    for f in os.listdir(dir_a):
                        src, dst = os.path.join(dir_a, f), os.path.join(dir_b, f)
                        if os.path.exists(dst):
                            dupdir = os.path.join(root, "_duplicates", name_b)
                            os.makedirs(dupdir, exist_ok=True)
                            shutil.move(src, os.path.join(dupdir, f))
                        else:
                            shutil.move(src, dst)
                    os.rmdir(dir_a)
                merged += 1
    return merged


# ── 2a. 正規化名が一致する basename 同士を統合（句読点サニタイズ違い対策） ──

def merge_exact_norm(root: str, apply: bool):
    """norm() が完全一致する basename グループを 1 つに統合する。
    正準名は「音声を持つ base」を優先、なければ新形式 > 長い名前。"""
    total_ren = total_dup = 0
    for dirpath in channel_dirs(root):
        groups = collect(dirpath)
        by_norm = {}
        for b in groups:
            by_norm.setdefault(norm(b), []).append(b)
        for bases in by_norm.values():
            if len(bases) < 2:
                continue
            def rank(b):
                has_audio = any(e in groups[b] for e in AUDIO_EXTS)
                return (has_audio, bool(NEW_FMT.match(b)), len(b))
            bases.sort(key=rank, reverse=True)
            canon = bases[0]
            print(f"\n## {os.path.relpath(dirpath, root)} (正規化一致の統合)")
            dup_dir = os.path.join(root, "_duplicates", os.path.basename(dirpath))
            for b in bases[1:]:
                for ext, fname in groups[b].items():
                    if ext in groups[canon]:
                        print(f"  DUP -> _duplicates/: {fname}")
                        if apply:
                            os.makedirs(dup_dir, exist_ok=True)
                            shutil.move(os.path.join(dirpath, fname), os.path.join(dup_dir, fname))
                        total_dup += 1
                    else:
                        print(f"  RENAME: {fname}\n       -> {canon + ext}")
                        if apply:
                            os.rename(os.path.join(dirpath, fname), os.path.join(dirpath, canon + ext))
                            groups[canon][ext] = canon + ext
                        total_ren += 1
    return total_ren, total_dup


# ── 2b. 旧形式 → 新形式リネーム & 重複退避 ──────────────────────────────────

def match_old_to_new(old_bases, new_bases):
    mapping = {}
    new_norm = {nb: norm(NEW_FMT.match(nb).group("title")) for nb in new_bases}
    for ob in old_bases:
        on = norm(ob)
        if len(on) < 8:
            continue
        candidates = []
        for nb, nn in new_norm.items():
            if not nn:
                continue
            shorter = min(len(on), len(nn))
            if shorter >= 8 and on[:shorter] == nn[:shorter]:
                candidates.append((shorter, nb))
        uniq = {c[1] for c in candidates}
        if len(uniq) == 1:
            mapping[ob] = candidates[0][1]
        elif len(candidates) > 1:
            candidates.sort(reverse=True)
            if candidates[0][0] > candidates[1][0]:
                mapping[ob] = candidates[0][1]
    return mapping


def normalize_names(root: str, apply: bool):
    total_ren = total_dup = 0
    for dirpath in channel_dirs(root):
        groups = collect(dirpath)
        new_bases = [b for b in groups if NEW_FMT.match(b)]
        old_bases = [b for b in groups if not NEW_FMT.match(b)]
        mapping = match_old_to_new(old_bases, new_bases)
        if not mapping:
            continue
        print(f"\n## {os.path.relpath(dirpath, root)}")
        dup_dir = os.path.join(root, "_duplicates", os.path.basename(dirpath))
        for ob, nb in mapping.items():
            for ext, fname in groups[ob].items():
                if ext in groups.get(nb, {}):
                    print(f"  DUP -> _duplicates/: {fname}")
                    if apply:
                        os.makedirs(dup_dir, exist_ok=True)
                        shutil.move(os.path.join(dirpath, fname), os.path.join(dup_dir, fname))
                    total_dup += 1
                else:
                    print(f"  RENAME: {fname}\n       -> {nb + ext}")
                    if apply:
                        os.rename(os.path.join(dirpath, fname), os.path.join(dirpath, nb + ext))
                    total_ren += 1
    return total_ren, total_dup


# ── 3. 言語判定 & 言語フォルダへ移動 ────────────────────────────────────────

def detect_channel_lang(dirpath: str) -> str:
    votes = {}
    for f in os.listdir(dirpath):
        p = os.path.join(dirpath, f)
        try:
            if f.endswith(".ttml"):
                head = open(p, encoding="utf-8", errors="ignore").read(2000)
                m = re.search(r'xml:lang="([^"]+)"', head)
                if m:
                    votes[m.group(1).split("-")[0]] = votes.get(m.group(1).split("-")[0], 0) + 1
            elif f.endswith(".json") and not f.endswith(".context.json"):
                data = json.load(open(p, encoding="utf-8"))
                sl = (data.get("metadata") or {}).get("sourceLanguage")
                if sl:
                    votes[sl.split("-")[0]] = votes.get(sl.split("-")[0], 0) + 1
        except Exception:
            pass
    if votes:
        return max(votes, key=votes.get)
    name = os.path.basename(dirpath)
    for key, lang in LANG_OVERRIDES.items():
        if key in name:
            return lang
    return ""


def organize_by_language(root: str, apply: bool):
    moved = 0
    for dirpath in channel_dirs(root):
        rel = os.path.relpath(dirpath, root)
        parts = rel.split(os.sep)
        parent = parts[0] if len(parts) > 1 else None
        lang = detect_channel_lang(dirpath)
        label = LANG_LABELS.get(lang, UNKNOWN_LABEL)
        if parent == label:
            continue  # すでに正しい言語フォルダ内
        target = os.path.join(root, label, os.path.basename(dirpath))
        print(f"  LANG MOVE [{label}]: {rel}")
        if apply:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.exists(target):  # 同名フォルダが既にある → 中身を統合
                for f in os.listdir(dirpath):
                    dst = os.path.join(target, f)
                    if not os.path.exists(dst):
                        shutil.move(os.path.join(dirpath, f), dst)
                os.rmdir(dirpath)
            else:
                shutil.move(dirpath, target)
        moved += 1
    return moved


# ── 4. 状態スキャン ─────────────────────────────────────────────────────────

def scan_status(root: str):
    episodes = []
    for dirpath in channel_dirs(root):
        rel = os.path.relpath(dirpath, root)
        parts = rel.split(os.sep)
        language = parts[0] if len(parts) > 1 else ""
        channel = os.path.basename(dirpath)
        for base, exts in collect(dirpath).items():
            has_audio = any(e in exts for e in AUDIO_EXTS)
            has_ttml = ".ttml" in exts
            has_json = ".json" in exts
            if not (has_audio or has_ttml):
                continue
            if has_json and has_audio:
                status = "ready"
            elif has_ttml and has_audio:
                status = "pending"        # JSON 変換待ち
            elif has_audio:
                status = "no_transcript"  # TTML なし
            else:
                status = "no_audio"
            episodes.append({
                "language": language,
                "channel": channel,
                "base": base,
                "status": status,
                "dir": rel,
            })
    counts = {}
    for e in episodes:
        counts[e["status"]] = counts.get(e["status"], 0) + 1
    return {
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "counts": counts,
        "episodes": sorted(episodes, key=lambda e: (e["language"], e["channel"], e["base"])),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/Desktop/Public/Podcasts"))
    ap.add_argument("--apply", action="store_true", help="実際に変更を行う")
    args = ap.parse_args()
    root = args.root
    if not os.path.isdir(root):
        sys.exit(f"フォルダが見つかりません: {root}")

    print("=== 1) チャンネルフォルダ統合 ===")
    merged = merge_channel_dirs(root, args.apply)
    print("=== 2a) 正規化一致 basename の統合 ===")
    ren2a, dup2a = merge_exact_norm(root, args.apply)
    print("=== 2b) ファイル名の統一・重複退避 ===")
    ren, dup = normalize_names(root, args.apply)
    ren += ren2a
    dup += dup2a
    print("\n=== 3) 言語別フォルダへ再編 ===")
    moved = organize_by_language(root, args.apply)

    status = scan_status(root)
    print(f"\n=== {'適用済み' if args.apply else 'DRY-RUN（--apply で実行）'} ===")
    print(f"フォルダ統合: {merged} / リネーム: {ren} / 重複退避: {dup} / 言語移動: {moved}")
    print("状態集計:", json.dumps(status["counts"], ensure_ascii=False))
    if args.apply:
        out = os.path.join(root, "library_status.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
        print(f"状態ファイル書き出し: {out}")


if __name__ == "__main__":
    main()

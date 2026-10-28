"""
Apple Podcasts Importer — import downloaded episodes into ~/Desktop/Public/Podcasts/.

Scans the Apple Podcasts SQLite DB to find downloaded episodes, then copies
audio + TTML files with standardised names:
    [Channel] Episode Title (YYYYMMDD).mp3
    [Channel] Episode Title (YYYYMMDD).ttml

Usage:
    python3 import_podcasts.py                  # import all new episodes
    python3 import_podcasts.py -q "Business"    # filter by keyword
    python3 import_podcasts.py --list           # list only, don't copy

Env overrides (mainly for testing): PODCASTS_GROUP, PODCASTS_DIR

Returns JSON to stdout so the caller can parse results programmatically.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

HOME = Path.home()
GROUP = Path(os.environ.get("PODCASTS_GROUP")
             or HOME / "Library/Group Containers/243LU875E5.groups.com.apple.podcasts")
DB_PATH = GROUP / "Documents/MTLibrary.sqlite"
TTML_ROOT = GROUP / "Library/Cache/Assets/TTML"
DEST_ROOT = Path(os.environ.get("PODCASTS_DIR") or HOME / "Desktop/Public/Podcasts")
CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


def sanitize(name: str) -> str:
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', " ", name or "")
    return re.sub(r"\s+", " ", name).strip() or "Unknown"


def core_data_to_datestr(ts):
    if not ts:
        return "unknown"
    dt = CORE_DATA_EPOCH + timedelta(seconds=ts)
    return dt.strftime("%Y%m%d")


def asset_url_to_path(asset_url: str) -> Path | None:
    """file:// URL をデコードして実パスへ（%20 などの percent-encoding 対応）。
    パスが見つからない場合は GROUP を基準に付け替えて再試行する
    （DB内の絶対パスと実際のマウント位置が異なる場合への対処）。"""
    if not asset_url:
        return None
    if asset_url.startswith("file://"):
        p = Path(unquote(urlparse(asset_url).path))
    else:
        p = Path(unquote(asset_url))
    if p.exists():
        return p
    marker = GROUP.name  # "243LU875E5.groups.com.apple.podcasts"
    s = str(p)
    if marker in s:
        rebased = GROUP / s.split(marker, 1)[1].lstrip("/")
        if rebased.exists():
            return rebased
    return p


def open_db():
    """WAL 未反映分も含めて読むため、DB+WAL+SHM を一時ディレクトリへコピーして開く。
    （直接 immutable=1 で開くと、ダウンロード直後のエピソードが見えないことがある）"""
    tmpdir = tempfile.mkdtemp(prefix="mtlib_")
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(DB_PATH) + suffix)
        if src.exists():
            shutil.copy2(src, Path(tmpdir) / src.name)
    conn = sqlite3.connect(str(Path(tmpdir) / DB_PATH.name))
    conn.row_factory = sqlite3.Row
    return conn, tmpdir


def fetch_episodes(query=None):
    if not DB_PATH.exists():
        print(json.dumps({"error": f"DB not found: {DB_PATH}"}))
        return []

    try:
        conn, tmpdir = open_db()
    except (OSError, sqlite3.Error) as e:
        print(json.dumps({"error": f"cannot open DB: {e}"}))
        return []
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(ZMTEPISODE)")
    ep_cols = {row[1] for row in cur.fetchall()}
    cur.execute("PRAGMA table_info(ZMTPODCAST)")
    pod_cols = {row[1] for row in cur.fetchall()}

    def pick(cols, *candidates):
        for c in candidates:
            if c in cols:
                return c
        return None

    c_title = pick(ep_cols, "ZTITLE", "ZCLEANEDTITLE")
    c_asset = pick(ep_cols, "ZASSETURL")
    c_pod = pick(ep_cols, "ZPODCAST", "ZPODCASTUUID")
    c_pub = pick(ep_cols, "ZPUBDATE", "ZPUBLISHDATE")
    c_uuid = pick(ep_cols, "ZUUID")
    c_store = pick(ep_cols, "ZSTORETRACKID")
    c_pod_title = pick(pod_cols, "ZTITLE", "ZCLEANEDTITLE")
    # トランスクリプト識別子（存在する列だけ COALESCE）
    ti_cols = [c for c in ("ZTRANSCRIPTIDENTIFIER", "ZFREETRANSCRIPTIDENTIFIER",
                           "ZENTITLEDTRANSCRIPTIDENTIFIER") if c in ep_cols]
    # COALESCE は2つ以上のときだけ使える
    ti_expr = ("COALESCE(" + ", ".join(f"e.{c}" for c in ti_cols) + ")") if len(ti_cols) > 1 \
        else (f"e.{ti_cols[0]}" if ti_cols else "NULL")

    if not (c_title and c_asset and c_pod and c_pod_title):
        print(json.dumps({"error": "Unexpected DB schema"}))
        return []

    q = f"""
        SELECT e.{c_title} AS title,
               e.{c_asset} AS asset_url,
               p.{c_pod_title} AS channel,
               {"e." + c_pub if c_pub else "NULL"} AS pub,
               {"e." + c_uuid if c_uuid else "NULL"} AS uuid,
               {"e." + c_store if c_store else "NULL"} AS store_id,
               {ti_expr} AS transcript_id
        FROM ZMTEPISODE e
        LEFT JOIN ZMTPODCAST p ON e.{c_pod} = p.Z_PK
        WHERE e.{c_asset} IS NOT NULL AND e.{c_asset} != ''
    """
    rows = cur.execute(q).fetchall()
    conn.close()
    shutil.rmtree(tmpdir, ignore_errors=True)

    episodes = []
    for r in rows:
        path = asset_url_to_path(r["asset_url"])
        if not path or not path.exists():
            continue
        ep = {
            "title": r["title"] or "Untitled",
            "channel": r["channel"] or "Unknown",
            "date": core_data_to_datestr(r["pub"]),
            "audio": str(path),
            "uuid": r["uuid"] or "",
            "store_id": str(r["store_id"] or ""),
            "transcript_id": r["transcript_id"] or "",
        }
        if query and query.lower() not in (ep["title"] + " " + ep["channel"]).lower():
            continue
        episodes.append(ep)
    return episodes


def find_ttml(ep) -> Path | None:
    """TTML を確実な順に探す:
    1. DB の transcript identifier → Cache/Assets/TTML/<id>-<storeTrackId>.ttml
    2. storeTrackId を含むファイル名を TTML キャッシュから検索
    3. 音声と同じフォルダ / uuid 一致（旧ロジック）"""
    ti, sid = ep.get("transcript_id"), ep.get("store_id")

    if ti:
        exact = TTML_ROOT / f"{ti}-{sid}.ttml"
        if exact.exists():
            return exact
        base = TTML_ROOT / ti
        if base.exists():
            return base
        parent = (TTML_ROOT / ti).parent
        # 識別子がフォルダを含むときだけ、そのフォルダの字幕を使う（TTML 置き場の直下を探すと別の回の字幕を拾う）
        if parent != TTML_ROOT and parent.exists():
            hits = sorted(parent.glob("*.ttml"))
            if hits:
                return hits[0]

    if sid and TTML_ROOT.exists():
        for f in TTML_ROOT.rglob(f"*{sid}*.ttml"):
            return f

    audio = Path(ep["audio"])
    uuid = ep.get("uuid") or ""
    if audio.parent.exists():
        same_dir = sorted(audio.parent.glob("*.ttml"))
        for f in same_dir:
            if f.stem == audio.stem or (uuid and uuid in f.name):
                return f
    if uuid and TTML_ROOT.exists():
        for f in TTML_ROOT.rglob(f"*{uuid}*.ttml"):
            return f
    return None


def dest_dir_for(channel):
    """言語フォルダ（英語/フランス語/…）配下に既存のチャンネルフォルダがあれば
    そこを使う。なければルート直下（後で normalize_library.py が言語別に移動する）。"""
    if DEST_ROOT.exists():
        for lang_dir in DEST_ROOT.iterdir():
            if lang_dir.is_dir() and not lang_dir.name.startswith(("_", ".")):
                cand = lang_dir / channel
                if cand.is_dir():
                    return cand
    return DEST_ROOT / channel


def process(ep):
    channel = sanitize(ep["channel"])
    title = sanitize(ep["title"])
    date = ep["date"]
    basename = f"[{channel}] {title} ({date})"
    dest_dir = dest_dir_for(channel)
    dest_dir.mkdir(parents=True, exist_ok=True)

    result = {"channel": channel, "title": title, "date": date, "basename": basename}

    audio_src = Path(ep["audio"])
    audio_ext = audio_src.suffix or ".mp3"
    dest_audio = dest_dir / f"{basename}{audio_ext}"
    if not dest_audio.exists():
        shutil.copy2(audio_src, dest_audio)
        result["audio"] = "copied"
    else:
        result["audio"] = "exists"

    ttml_src = find_ttml(ep)
    if not ttml_src:
        result["ttml"] = "not_found"
        return result

    dest_ttml = dest_dir / f"{basename}.ttml"
    if not dest_ttml.exists():
        shutil.copy2(ttml_src, dest_ttml)
        result["ttml"] = "copied"
    else:
        result["ttml"] = "exists"

    dest_json = dest_dir / f"{basename}.json"
    result["json_exists"] = dest_json.exists()
    result["ttml_path"] = str(dest_ttml)
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-q", "--query", help="Filter by keyword")
    ap.add_argument("--list", action="store_true", help="List only")
    args = ap.parse_args()

    episodes = fetch_episodes(args.query)
    if not episodes:
        print(json.dumps({"status": "no_episodes"}))
        return 0

    if args.list:
        for ep in episodes:
            print(json.dumps({"channel": ep["channel"], "title": ep["title"],
                              "date": ep["date"],
                              "has_transcript_id": bool(ep["transcript_id"])},
                             ensure_ascii=False))
        return 0

    DEST_ROOT.mkdir(parents=True, exist_ok=True)
    results = []
    for ep in episodes:
        try:
            r = process(ep)
            results.append(r)
        except Exception as e:
            results.append({"title": ep["title"], "error": str(e)})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

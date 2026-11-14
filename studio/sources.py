"""教材のもとになる音声と字幕を、この Mac から自動で探す。

- ライブラリのフォルダ（既定: デスクトップの Podcasts）の中すべて。同じ名前の .mp3 / .ttml / .json が1つのエピソード。
- Apple Podcasts のアプリがダウンロードした音声と、自動で作られた字幕（TTML）。読むだけで、何も変えない。
"""
import os
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav")
CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
SKIP_DIRS = {"_duplicates", "PodcastsChatGPT"}


def apple_group(home=None):
    return Path(os.environ.get("PODCASTS_GROUP")
                or Path(home or Path.home()) / "Library/Group Containers/243LU875E5.groups.com.apple.podcasts")


def find_library(home=None):
    """持ち主のライブラリ（デスクトップの Podcasts）。なければ空文字。"""
    home = Path(home or Path.home())
    for d in (home / "Desktop/Podcasts", home / "Desktop/Public/Podcasts", home / "Desktop/ポッドキャスト", home / "Desktop/podcasts"):
        if d.is_dir():
            return str(d)
    return ""


def stem_of(name):
    return re.sub(r"\.(listening\.json|listening\.m4a|fix\.json|json|ttml|xml|mp3|m4a|aac|wav)$", "", name, flags=re.I)


def library_episodes(folder):
    """フォルダの中（下のフォルダも）の、音声があるエピソード。"""
    out = []
    if not folder or not os.path.isdir(folder):
        return out
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS)
        groups = {}
        for name in files:
            if name.startswith(".") or ".listening." in name or name.endswith(".fix.json"):
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = "audio" if ext in AUDIO_EXTS else "ttml" if ext in (".ttml", ".xml") else "json" if ext == ".json" else None
            if kind:
                groups.setdefault(stem_of(name), {})[kind] = os.path.join(root, name)
        for stem, g in groups.items():
            if "audio" in g:
                rel = os.path.relpath(root, folder)
                out.append({"name": stem, "audio": g["audio"], "ttml": g.get("ttml"), "json": g.get("json"),
                            "source": "" if rel == "." else rel, "time": os.path.getmtime(g["audio"])})
    return out


def _sanitize(name):
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', " ", name or "")
    return re.sub(r"\s+", " ", name).strip() or "Unknown"


def _asset_path(url, group):
    p = Path(unquote(urlparse(url).path)) if url.startswith("file://") else Path(unquote(url))
    if p.exists():
        return p
    if group.name in str(p):
        rebased = group / str(p).split(group.name, 1)[1].lstrip("/")
        if rebased.exists():
            return rebased
    return p


def _find_ttml(ep, root):
    ti, sid, audio = ep["transcript_id"], ep["store_id"], ep["audio"]
    if ti:
        for cand in (root / f"{ti}-{sid}.ttml", root / ti):
            if cand.is_file():
                return cand
        parent = (root / ti).parent
        # 識別子がフォルダを含むときだけ、そのフォルダの字幕を使う（置き場の直下を探すと別の回の字幕を拾う）
        if parent != root and parent.is_dir():
            hits = sorted(parent.glob("*.ttml"))
            if hits:
                return hits[0]
    if sid and root.is_dir():
        for f in root.rglob(f"*{sid}*.ttml"):
            return f
    if audio.parent.is_dir():
        for f in sorted(audio.parent.glob("*.ttml")):
            if f.stem == audio.stem or (ep["uuid"] and ep["uuid"] in f.name):
                return f
    if ep["uuid"] and root.is_dir():
        for f in root.rglob(f"*{ep['uuid']}*.ttml"):
            return f
    return None


def apple_episodes(home=None):
    """Apple Podcasts にダウンロードしてある回。読めないときは PermissionError などをそのまま上げる。"""
    group = apple_group(home)
    db = group / "Documents/MTLibrary.sqlite"
    if not db.exists():
        if group.exists():
            os.listdir(group)  # フォルダはあるのに読めないとき、ここで PermissionError になる
        return []
    tmp = tempfile.mkdtemp(prefix="mtlib_")
    try:
        # 書き込み途中（WAL）のものも含めて読むため、DB 一式を一時フォルダにコピーして開く
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(db) + suffix)
            if src.exists():
                shutil.copy2(src, Path(tmp) / src.name)
        conn = sqlite3.connect(str(Path(tmp) / db.name))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        ep_cols = {r[1] for r in cur.execute("PRAGMA table_info(ZMTEPISODE)")}
        pod_cols = {r[1] for r in cur.execute("PRAGMA table_info(ZMTPODCAST)")}

        def pick(cols, *names):
            return next((n for n in names if n in cols), None)

        c_title, c_asset = pick(ep_cols, "ZTITLE", "ZCLEANEDTITLE"), pick(ep_cols, "ZASSETURL")
        c_pod, c_pub = pick(ep_cols, "ZPODCAST", "ZPODCASTUUID"), pick(ep_cols, "ZPUBDATE", "ZPUBLISHDATE")
        c_uuid, c_store = pick(ep_cols, "ZUUID"), pick(ep_cols, "ZSTORETRACKID")
        c_ptitle = pick(pod_cols, "ZTITLE", "ZCLEANEDTITLE")
        ti = ["e." + c for c in ("ZTRANSCRIPTIDENTIFIER", "ZFREETRANSCRIPTIDENTIFIER", "ZENTITLEDTRANSCRIPTIDENTIFIER") if c in ep_cols]
        ti_expr = ("COALESCE(" + ", ".join(ti) + ")") if len(ti) > 1 else (ti[0] if ti else "NULL")
        if not (c_title and c_asset and c_pod and c_ptitle):
            raise RuntimeError("Apple Podcasts data has an unexpected shape")
        rows = cur.execute(f"""
            SELECT e.{c_title} AS title, e.{c_asset} AS asset, p.{c_ptitle} AS channel,
                   {"e." + c_pub if c_pub else "NULL"} AS pub, {"e." + c_uuid if c_uuid else "NULL"} AS uuid,
                   {"e." + c_store if c_store else "NULL"} AS store_id, {ti_expr} AS transcript_id
            FROM ZMTEPISODE e LEFT JOIN ZMTPODCAST p ON e.{c_pod} = p.Z_PK
            WHERE e.{c_asset} IS NOT NULL AND e.{c_asset} != ''""").fetchall()
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = []
    ttml_root = group / "Library/Cache/Assets/TTML"
    for r in rows:
        audio = _asset_path(r["asset"], group)
        if not audio.exists():
            continue
        ep = {"audio": audio, "uuid": r["uuid"] or "", "store_id": str(r["store_id"] or ""), "transcript_id": r["transcript_id"] or ""}
        date = (CORE_DATA_EPOCH + timedelta(seconds=r["pub"])).strftime("%Y%m%d") if r["pub"] else ""
        name = "[%s] %s" % (_sanitize(r["channel"] or "Unknown"), _sanitize(r["title"] or "Untitled")) + (" (%s)" % date if date else "")
        ttml = _find_ttml(ep, ttml_root)
        out.append({"name": name, "audio": str(audio), "ttml": str(ttml) if ttml else None, "json": None,
                    "source": "Apple Podcasts", "time": audio.stat().st_mtime})
    return out


def collect(library, home=None):
    """(エピソードの一覧（新しい順）, Apple Podcasts を読めなかった理由 or "")。同じ名前はライブラリのものを使う。"""
    eps = {e["name"]: e for e in library_episodes(library)}
    problem = ""
    try:
        for e in apple_episodes(home):
            eps.setdefault(e["name"], e)
    except PermissionError:
        problem = "permission"
    except Exception as err:  # アプリの更新でデータの形が変わったときなど
        problem = str(err)[:200]
    return sorted(eps.values(), key=lambda e: -e["time"]), problem

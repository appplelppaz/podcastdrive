"""
Apple Podcasts でダウンロードしたエピソードの音声と字幕（TTML）を、
Dropbox の PodcastsChatGPT フォルダに集める（Dropbox のアプリがアップロードし、
別の場所の Windows パソコンの ChatGPT が JSON を作るため）。

- 対象: このアプリを実行した日（今日）に Mac にダウンロード・保存された音声と字幕だけ（配信日ではなく、ファイルの日付）。
  前の日にダウンロード・保存されたものは集めない。音声と字幕はそれぞれの日付で判断する。
- コピーするだけ。Apple Podcasts の中のファイルも、既存のライブラリ（英語/中国語…）も変えない。
- 名前は [チャンネル] エピソード名 (配信日YYYYMMDD).mp3 / .ttml（podcast-json と同じ）。
- 一度集めた回は覚えておき、PodcastsChatGPT から移したり消したりしても、もう一度はコピーしない。
- 字幕（TTML）がまだない回は音声だけをコピーし、字幕が保存された日に実行すれば字幕を足す。
- 初回に、デスクトップへ PodcastsChatGPT フォルダのショートカットを作る。
- 以前の版の集め先（~/Desktop/Public/PodcastsChatGPT など）にあるファイルと記録は Dropbox へ移し、
  以前の版の「Dropbox へ自動コピー」の設定（launchd）は止める。

Mac に Dropbox のアプリを入れてログインしておく（なければ案内する）。

ターミナルに「フルディスクアクセス」が必要（Apple Podcasts のデータを読むため）。なければ設定の画面を開いて案内する。
"""
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

FOLDER_NAME = "PodcastsChatGPT"

HOME = Path.home()
GROUP = Path(os.environ.get("PODCASTS_GROUP")
             or HOME / "Library/Group Containers/243LU875E5.groups.com.apple.podcasts")
DB_PATH = GROUP / "Documents/MTLibrary.sqlite"
TTML_ROOT = GROUP / "Library/Cache/Assets/TTML"
PUBLIC = Path(os.environ.get("PUBLIC_DIR") or HOME / "Desktop/Public")
OLD_DESTS = [PUBLIC / FOLDER_NAME, PUBLIC / "Podcasts" / FOLDER_NAME]  # 以前の版の集め先（あれば中身を移す）
DESKTOP = Path(os.environ.get("DESKTOP_DIR") or HOME / "Desktop")
OLD_AGENT = HOME / "Library/LaunchAgents/com.podcastdrive.podcastschatgpt.dropbox.plist"


def find_dropbox():
    """Dropbox のアプリが Mac に作るフォルダ。"""
    if os.environ.get("DROPBOX_DIR"):
        return Path(os.environ["DROPBOX_DIR"])
    cloud = HOME / "Library/CloudStorage"
    candidates = [cloud / "Dropbox", cloud / "Dropbox-Personal"]
    candidates += sorted(cloud.glob("Dropbox*")) if cloud.is_dir() else []
    candidates.append(HOME / "Dropbox")
    for d in candidates:
        if d.is_dir():
            return d
    return None


DROPBOX = find_dropbox()
DEST = (DROPBOX / FOLDER_NAME) if DROPBOX else None   # ~/Library/CloudStorage/Dropbox/PodcastsChatGPT
STATE = (DEST / ".collected.json") if DEST else None
CORE_DATA_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
FDA_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"


def sanitize(name):
    name = re.sub(r'[\x00-\x1f/\\:*?"<>|]', " ", name or "")
    return re.sub(r"\s+", " ", name).strip() or "Unknown"


def pub_date(ts):
    if not ts:
        return None
    return (CORE_DATA_EPOCH + timedelta(seconds=ts)).strftime("%Y%m%d")


def asset_path(url):
    if not url:
        return None
    p = Path(unquote(urlparse(url).path)) if url.startswith("file://") else Path(unquote(url))
    if p.exists():
        return p
    marker = GROUP.name
    if marker in str(p):
        rebased = GROUP / str(p).split(marker, 1)[1].lstrip("/")
        if rebased.exists():
            return rebased
    return p


def copy_file(src, dst):
    """途中のファイルを見せないよう、「.」で始まる名前でコピーしてから名前を変える（Dropbox への自動コピーが拾わない）。"""
    tmp = dst.with_name("." + dst.name + ".copying")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def downloaded_at(path):
    """ファイルが Mac に来た時刻（作成日。なければ更新日）。"""
    st = path.stat()
    return datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime))


def read_episodes():
    """Apple Podcasts のライブラリ（MTLibrary.sqlite）から、音声ファイルがある回を読む。"""
    tmp = tempfile.mkdtemp(prefix="mtlib_")
    try:
        # 書き込み途中（WAL）のものも含めて読むため、DB 一式を一時フォルダにコピーして開く
        for suffix in ("", "-wal", "-shm"):
            src = Path(str(DB_PATH) + suffix)
            if src.exists():
                shutil.copy2(src, Path(tmp) / src.name)
        conn = sqlite3.connect(str(Path(tmp) / DB_PATH.name))
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
        # 字幕の識別子の列（あるものだけ。COALESCE は2つ以上のときだけ使える）
        ti_expr = ("COALESCE(" + ", ".join(ti) + ")") if len(ti) > 1 else (ti[0] if ti else "NULL")
        if not (c_title and c_asset and c_pod and c_ptitle):
            raise RuntimeError("Apple Podcasts のデータの形が想定と違います（アプリの更新で変わった可能性）")
        rows = cur.execute(f"""
            SELECT e.{c_title} AS title, e.{c_asset} AS asset, p.{c_ptitle} AS channel,
                   {"e." + c_pub if c_pub else "NULL"} AS pub, {"e." + c_uuid if c_uuid else "NULL"} AS uuid,
                   {"e." + c_store if c_store else "NULL"} AS store_id,
                   {ti_expr} AS transcript_id
            FROM ZMTEPISODE e LEFT JOIN ZMTPODCAST p ON e.{c_pod} = p.Z_PK
            WHERE e.{c_asset} IS NOT NULL AND e.{c_asset} != ''""").fetchall()
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out = []
    for r in rows:
        path = asset_path(r["asset"])
        if path and path.exists():
            out.append({"title": r["title"] or "Untitled", "channel": r["channel"] or "Unknown", "pub": r["pub"],
                        "audio": path, "uuid": r["uuid"] or "", "store_id": str(r["store_id"] or ""),
                        "transcript_id": r["transcript_id"] or ""})
    return out


def find_ttml(ep):
    """podcast-json の import_podcasts.py と同じ順に字幕を探す。"""
    ti, sid = ep["transcript_id"], ep["store_id"]
    if ti:
        for cand in (TTML_ROOT / f"{ti}-{sid}.ttml", TTML_ROOT / ti):
            if cand.is_file():
                return cand
        parent = (TTML_ROOT / ti).parent
        # 識別子がフォルダを含むときだけ、そのフォルダの字幕を使う（TTML 置き場の直下を探すと別の回の字幕を拾う）
        if parent != TTML_ROOT and parent.is_dir():
            hits = sorted(parent.glob("*.ttml"))
            if hits:
                return hits[0]
    if sid and TTML_ROOT.is_dir():
        for f in TTML_ROOT.rglob(f"*{sid}*.ttml"):
            return f
    audio = ep["audio"]
    if audio.parent.is_dir():
        for f in sorted(audio.parent.glob("*.ttml")):
            if f.stem == audio.stem or (ep["uuid"] and ep["uuid"] in f.name):
                return f
    if ep["uuid"] and TTML_ROOT.is_dir():
        for f in TTML_ROOT.rglob(f"*{ep['uuid']}*.ttml"):
            return f
    return None


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"episodes": {}}


def save_state(state):
    tmp = str(STATE) + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def move_old_folders():
    """以前の版の集め先にあるファイルと記録を、Dropbox の集め先へ移す。"""
    moved = 0
    for old in OLD_DESTS:
        if not old.is_dir() or old.is_symlink():
            continue
        for f in sorted(old.iterdir()):
            target = DEST / f.name
            if f.name == ".DS_Store" or target.exists():
                continue
            shutil.move(str(f), str(target))
            moved += 1
        try:
            (old / ".DS_Store").unlink()
        except OSError:
            pass
        try:
            old.rmdir()  # 空になったときだけ消える
        except OSError:
            pass
    return moved


def stop_old_sync():
    """以前の版の「Dropbox へ自動コピー」（launchd）を止める。今は Dropbox に直接集めるので要らない。"""
    if not OLD_AGENT.exists():
        return False
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{OLD_AGENT.stem}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    OLD_AGENT.unlink()
    return True


def make_desktop_shortcut():
    """デスクトップに PodcastsChatGPT フォルダへのショートカット（エイリアスと同じように開ける）を作る。"""
    link = DESKTOP / FOLDER_NAME
    if link.is_symlink() and os.readlink(link) != str(DEST):
        link.unlink()  # 以前の集め先を指すショートカットは作り直す
    if link.is_symlink() or link.exists():
        return False
    try:
        os.symlink(DEST, link)
        return True
    except OSError:
        return False


def needs_full_disk_access(err):
    print("\n" + "=" * 60)
    print("Apple Podcasts のデータを読む許可がありません。")
    print("「システム設定 → プライバシーとセキュリティ → フルディスクアクセス」で")
    print("「ターミナル」をオンにして、ターミナルを一度終了してから、もう一度このアプリを開いてください。")
    print(f"（詳細: {err}）")
    print("=" * 60)
    if sys.platform == "darwin":
        subprocess.run(["open", FDA_PANE])


def main():
    if not DEST:
        print("Dropbox のフォルダが見つかりません。")
        print("Dropbox のアプリ（https://www.dropbox.com/install）を入れてログインし、")
        print("Finder のサイドバーに Dropbox が出てから、もう一度このアプリを開いてください。")
        if sys.platform == "darwin" and not os.environ.get("NO_OPEN"):
            subprocess.run(["open", "https://www.dropbox.com/install"])
        return 1
    DEST.mkdir(parents=True, exist_ok=True)
    moved = move_old_folders()
    if moved:
        print(f"以前の集め先から Dropbox へ {moved} 個のファイルを移しました。")
    if stop_old_sync():
        print("以前の「Dropbox へ自動コピー」の設定を止めました（今は Dropbox に直接集めます）。")
    if make_desktop_shortcut():
        print(f"デスクトップに {FOLDER_NAME} のショートカットを作りました。")
    if not DB_PATH.exists():
        print(f"Apple Podcasts のライブラリが見つかりません: {DB_PATH}")
        print("Apple Podcasts を一度開いてから、もう一度実行してください。")
        return 1
    try:
        episodes = read_episodes()
    except PermissionError as e:
        needs_full_disk_access(e)
        return 1
    except (sqlite3.DatabaseError, OSError) as e:
        if "denied" in str(e).lower() or "not permitted" in str(e).lower() or "unable to open" in str(e).lower():
            needs_full_disk_access(e)
        else:
            print(f"Apple Podcasts のデータを読めませんでした: {e}")
        return 1
    except RuntimeError as e:
        print(e)
        return 1

    state = load_state()
    state.pop("since", None)  # 以前の版の設定（決まった日以降）。今は「実行した日」だけ
    today = datetime.now().date()
    done = state.setdefault("episodes", {})
    new_audio, new_ttml, waiting = [], [], []
    for ep in episodes:
        key = ep["uuid"] or str(ep["audio"])
        rec = done.get(key)
        if rec and rec.get("audio") and rec.get("ttml"):
            continue  # 音声も字幕も集めた回
        try:
            audio_day = downloaded_at(ep["audio"]).date()
        except OSError:
            continue
        ttml = find_ttml(ep)
        try:
            ttml_day = downloaded_at(ttml).date() if ttml else None
        except OSError:
            ttml, ttml_day = None, None
        # 実行した日にダウンロード・保存されたファイルだけを集める（音声と字幕はそれぞれの日付で判断）
        take_audio = audio_day == today and not (rec and rec.get("audio"))
        take_ttml = ttml_day == today and not (rec and rec.get("ttml"))
        if not (take_audio or take_ttml):
            continue
        date = pub_date(ep["pub"]) or audio_day.strftime("%Y%m%d")
        base = rec["name"] if rec else f"[{sanitize(ep['channel'])}] {sanitize(ep['title'])} ({date})"
        rec = rec or {"name": base}
        if take_audio:
            dst = DEST / (base + (ep["audio"].suffix or ".mp3"))
            if not dst.exists():
                copy_file(ep["audio"], dst)
            rec["audio"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_audio.append(base)
        if take_ttml:
            dst = DEST / (base + ".ttml")
            if not dst.exists():
                copy_file(ttml, dst)
            rec["ttml"] = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_ttml.append(base)
        elif not ttml:
            waiting.append(base)
        done[key] = rec
        save_state(state)

    print(f"\n集める先: {DEST}")
    print("（Dropbox のアプリが自動でアップロードします）")
    print(f"対象: 今日（{today:%Y-%m-%d}）ダウンロード・保存された音声と字幕だけ\n")
    if new_audio:
        print(f"音声をコピー（{len(new_audio)}件）:")
        for n in new_audio:
            print("  ・" + n)
    if new_ttml:
        print(f"字幕（TTML）をコピー（{len(new_ttml)}件）:")
        for n in new_ttml:
            print("  ・" + n)
    if waiting:
        print(f"字幕がまだない回（{len(waiting)}件。音声だけコピーしました。字幕は保存された日に実行すれば足します。"
              "字幕がない番組は ChatGPT 側で音声から作ります）:")
        for n in waiting:
            print("  ・" + n)
    if not (new_audio or new_ttml or waiting):
        print("新しくダウンロードされた回はありませんでした。")
    if sys.platform == "darwin" and not os.environ.get("NO_OPEN"):
        subprocess.run(["open", str(DEST)])
    return 0


if __name__ == "__main__":
    sys.exit(main())

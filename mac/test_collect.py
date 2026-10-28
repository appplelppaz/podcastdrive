"""collect_for_chatgpt.py の確認（Apple Podcasts のデータを模したもので）。

    python3 -m unittest mac/test_collect.py
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "collect_for_chatgpt.py")
EPOCH = datetime(2001, 1, 1).timestamp()
TODAY = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


SETUP = os.path.join(HERE, "dropbox_sync_setup.sh")
# launchctl の代わり: 登録した plist を覚え、kickstart で中のコマンドを動かす
FAKE_LAUNCHCTL = """#!/bin/bash
case "$1" in
  bootstrap) echo "$3" > "$FAKE_STATE/plist" ;;
  bootout) rm -f "$FAKE_STATE/plist" ;;
  kickstart)
    exec python3 - "$(cat "$FAKE_STATE/plist")" <<'PY'
import plistlib, subprocess, sys
with open(sys.argv[1], "rb") as f:
    p = plistlib.load(f)
with open(p["StandardErrorPath"], "a") as log:
    subprocess.run(p["ProgramArguments"], stdout=log, stderr=log)
PY
    ;;
esac
"""
# フルディスクアクセスがない rsync（1回目だけ拒まれる）
FAKE_RSYNC = """#!/bin/bash
if [ ! -f "$FAKE_STATE/allowed" ]; then
  touch "$FAKE_STATE/allowed"
  echo "rsync: opendir failed: Operation not permitted" >&2
  exit 23
fi
exec rsync "$@"
"""


class DropboxSyncSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.home = os.path.join(t, "home")
        self.src = os.path.join(self.home, "Desktop", "Public", "PodcastsChatGPT")
        self.dropbox = os.path.join(self.home, "Library", "CloudStorage", "Dropbox")
        self.state = os.path.join(t, "state")
        bin_dir = os.path.join(t, "bin")
        for d in (self.src, self.dropbox, self.state, bin_dir):
            os.makedirs(d)
        for name, body in (("launchctl", FAKE_LAUNCHCTL), ("fake_rsync", FAKE_RSYNC)):
            path = os.path.join(bin_dir, name)
            with open(path, "w") as f:
                f.write(body)
            os.chmod(path, 0o755)
        self.env = dict(os.environ, HOME=self.home, NO_OPEN="1", FAKE_STATE=self.state,
                        PATH=bin_dir + os.pathsep + os.environ["PATH"], RSYNC=os.path.join(bin_dir, "fake_rsync"))
        self.plist = os.path.join(self.home, "Library", "LaunchAgents",
                                  "com.podcastdrive.podcastschatgpt.dropbox.plist")

    def tearDown(self):
        self.tmp.cleanup()

    def run_setup(self, answers=""):
        r = subprocess.run(["bash", SETUP], input=answers, capture_output=True, text=True, env=self.env, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def test_setup_copies_and_guides_full_disk_access(self):
        for name in ("[A] B (20261028).mp3", "[A] B (20261028).ttml", ".collected.json", ".x.mp3.copying"):
            with open(os.path.join(self.src, name), "w") as f:
                f.write(name)
        rc, out = self.run_setup("\n")  # 1回目は許可がない → 案内 → Enter で確かめ直す
        self.assertEqual(rc, 0, out)
        self.assertIn("フルディスクアクセス", out)
        self.assertIn("設定しました", out)
        dst = os.path.join(self.dropbox, "PodcastsChatGPT")
        self.assertEqual(sorted(os.listdir(dst)), ["[A] B (20261028).mp3", "[A] B (20261028).ttml"])
        with open(self.plist, "rb") as f:
            import plistlib
            p = plistlib.load(f)
        self.assertEqual(p["WatchPaths"], [self.src])
        self.assertEqual(p["ProgramArguments"][-2:], [self.src + "/", dst + "/"])
        # 集めたファイルを移しても Dropbox 側は消さない。新しいファイルは足す
        os.remove(os.path.join(self.src, "[A] B (20261028).mp3"))
        with open(os.path.join(self.src, "[C] D (20261029).mp3"), "w") as f:
            f.write("x")
        subprocess.run(["launchctl", "kickstart", "x"], env=self.env, check=True)
        self.assertEqual(sorted(os.listdir(dst)),
                         ["[A] B (20261028).mp3", "[A] B (20261028).ttml", "[C] D (20261029).mp3"])
        # もう一度開いて s → 止める
        rc, out = self.run_setup("s\n")
        self.assertEqual(rc, 0, out)
        self.assertIn("止めました", out)
        self.assertFalse(os.path.exists(self.plist))
        self.assertFalse(os.path.exists(os.path.join(self.state, "plist")))

    def test_no_dropbox(self):
        os.rmdir(self.dropbox)
        rc, out = self.run_setup()
        self.assertEqual(rc, 1)
        self.assertIn("Dropbox のフォルダが見つかりません", out)
        self.assertFalse(os.path.exists(self.plist))


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.group = os.path.join(t, "group")
        self.public = os.path.join(t, "Desktop", "Public")
        self.lib = os.path.join(self.public, "PodcastsChatGPT")
        self.desktop = os.path.join(t, "Desktop")
        os.makedirs(os.path.join(self.group, "Documents"))
        os.makedirs(os.path.join(self.group, "Library", "Cache", "Assets", "TTML"))
        os.makedirs(os.path.join(self.group, "Library", "Cache", "Audio"))
        db = sqlite3.connect(os.path.join(self.group, "Documents", "MTLibrary.sqlite"))
        db.execute("CREATE TABLE ZMTPODCAST (Z_PK INTEGER PRIMARY KEY, ZTITLE TEXT)")
        db.execute("CREATE TABLE ZMTEPISODE (Z_PK INTEGER PRIMARY KEY, ZTITLE TEXT, ZASSETURL TEXT, ZPODCAST INTEGER,"
                   " ZPUBDATE REAL, ZUUID TEXT, ZSTORETRACKID INTEGER, ZTRANSCRIPTIDENTIFIER TEXT)")
        db.execute("INSERT INTO ZMTPODCAST VALUES (1, 'Business Daily'), (2, 'TeaTime Chinese 茶歇中文')")
        self.db = db

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def add(self, pk, pod, title, pub, downloaded, ttml=True, ttml_saved=None):
        audio = os.path.join(self.group, "Library", "Cache", "Audio", f"{pk}.mp3")
        with open(audio, "wb") as f:
            f.write(b"ID3" + bytes(100))
        ts = datetime.strptime(downloaded, "%Y-%m-%d %H:%M").timestamp()
        os.utime(audio, (ts, ts))
        tid = f"transcript_{pk}"
        if ttml:
            self.add_ttml(pk, ttml_saved)
        self.db.execute("INSERT INTO ZMTEPISODE VALUES (?,?,?,?,?,?,?,?)",
                        (pk, title, "file://" + audio.replace(" ", "%20"), pod,
                         datetime.strptime(pub, "%Y-%m-%d").timestamp() - EPOCH, f"uuid-{pk}", 1000 + pk, tid))
        self.db.commit()

    def add_ttml(self, pk, saved=None):
        path = os.path.join(self.group, "Library", "Cache", "Assets", "TTML", f"transcript_{pk}-{1000 + pk}.ttml")
        with open(path, "w") as f:
            f.write("<tt/>")
        if saved:
            ts = datetime.strptime(saved, "%Y-%m-%d %H:%M").timestamp()
            os.utime(path, (ts, ts))

    def run_app(self):
        env = dict(os.environ, PODCASTS_GROUP=self.group, PUBLIC_DIR=self.public, DESKTOP_DIR=self.desktop, NO_OPEN="1")
        r = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def files(self):
        dest = self.lib
        return sorted(f for f in os.listdir(dest) if not f.startswith("."))

    def test_collect(self):
        self.add(1, 1, "Iran’s economy: Counting the cost of war", "2026-01-01", TODAY + " 00:01")
        self.add(2, 1, "Old episode", "2026-10-20", YESTERDAY + " 23:59", ttml_saved=YESTERDAY + " 23:59")  # 前の日（対象外）
        self.add(3, 2, "第95集 汉字/拼音?", "2026-10-25", TODAY + " 00:02", ttml=False)  # 字幕がまだない
        self.add(4, 1, "Old audio, old transcript today?", "2026-10-21", YESTERDAY + " 10:00", ttml=False)
        out = self.run_app()
        self.assertEqual(sorted(os.listdir(self.lib)), [".collected.json"] + self.files())  # 途中の名前が残らない
        self.assertEqual(self.files(), [
            "[Business Daily] Iran’s economy Counting the cost of war (20260101).mp3",
            "[Business Daily] Iran’s economy Counting the cost of war (20260101).ttml",
            "[TeaTime Chinese 茶歇中文] 第95集 汉字 拼音 (20261025).mp3",
        ])
        self.assertIn("字幕がまだない回（1件", out)
        self.assertIn("今日（" + TODAY, out)
        link = os.path.join(self.desktop, "PodcastsChatGPT")
        self.assertTrue(os.path.islink(link) and os.path.samefile(link, self.lib))

        # 2回目: 新しいものはない（同じファイルを二度コピーしない）
        self.run_app()
        self.assertEqual(len(self.files()), 3)
        # 字幕が今日保存された → 字幕だけ足す。前の日にダウンロードした音声の回でも、今日の字幕は集める（音声は集めない）
        self.add_ttml(3)
        self.add_ttml(4)
        out = self.run_app()
        self.assertIn("字幕（TTML）をコピー（2件", out)
        self.assertNotIn("音声をコピー", out)
        self.assertIn("[TeaTime Chinese 茶歇中文] 第95集 汉字 拼音 (20261025).ttml", self.files())
        self.assertIn("[Business Daily] Old audio, old transcript today (20261021).ttml", self.files())
        # 前の日に保存された字幕は集めない
        self.add_ttml(2, YESTERDAY + " 12:00")
        self.run_app()
        self.assertNotIn("Old episode", " ".join(self.files()))
        # 集めたファイルを移しても（会社へ送った後など）、もう一度はコピーしない
        dest = self.lib
        for f in self.files():
            os.remove(os.path.join(dest, f))
        self.assertIn("新しくダウンロードされた回はありませんでした", self.run_app())
        self.assertEqual(self.files(), [])

    def test_moves_old_folder(self):
        old = os.path.join(self.public, "Podcasts", "PodcastsChatGPT")
        os.makedirs(old)
        with open(os.path.join(old, "[A] B (20261028).mp3"), "w") as f:
            f.write("x")
        with open(os.path.join(old, ".collected.json"), "w") as f:
            json.dump({"since": "2026-10-28", "episodes": {"uuid-9": {"name": "[A] B (20261028)", "audio": "x", "ttml": "x"}}}, f)
        os.makedirs(self.desktop, exist_ok=True)
        os.symlink(old, os.path.join(self.desktop, "PodcastsChatGPT"))  # 以前の版のショートカット
        out = self.run_app()
        self.assertIn("2 個のファイルを移しました", out)
        self.assertFalse(os.path.exists(old))
        self.assertEqual(self.files(), ["[A] B (20261028).mp3"])
        with open(os.path.join(self.lib, ".collected.json"), encoding="utf-8") as f:
            self.assertIn("uuid-9", json.load(f)["episodes"])
        link = os.path.join(self.desktop, "PodcastsChatGPT")
        self.assertEqual(os.readlink(link), self.lib)

    def test_missing_library(self):
        os.remove(os.path.join(self.group, "Documents", "MTLibrary.sqlite"))
        env = dict(os.environ, PODCASTS_GROUP=self.group, PUBLIC_DIR=self.public, DESKTOP_DIR=self.desktop, NO_OPEN="1")
        r = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Apple Podcasts のライブラリが見つかりません", r.stdout)


if __name__ == "__main__":
    unittest.main()

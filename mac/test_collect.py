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


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.group = os.path.join(t, "group")
        self.home = os.path.join(t, "home")
        self.desktop = os.path.join(self.home, "Desktop")
        self.public = os.path.join(self.desktop, "Public")
        self.dropbox = os.path.join(self.home, "Library", "CloudStorage", "Dropbox")
        self.lib = os.path.join(self.dropbox, "PodcastsChatGPT")
        os.makedirs(self.dropbox)
        os.makedirs(self.desktop)
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

    def run_app(self, code=0):
        env = dict(os.environ, HOME=self.home, PODCASTS_GROUP=self.group, NO_OPEN="1")
        r = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, code, r.stdout + r.stderr)
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

    def test_moves_old_folders_to_dropbox(self):
        old = os.path.join(self.public, "PodcastsChatGPT")
        older = os.path.join(self.public, "Podcasts", "PodcastsChatGPT")
        os.makedirs(old)
        os.makedirs(older)
        with open(os.path.join(old, "[A] B (20261028).mp3"), "w") as f:
            f.write("x")
        with open(os.path.join(old, ".DS_Store"), "w") as f:
            f.write("x")
        with open(os.path.join(old, ".collected.json"), "w") as f:
            json.dump({"episodes": {"uuid-9": {"name": "[A] B (20261028)", "audio": "x", "ttml": "x"}}}, f)
        with open(os.path.join(older, "[C] D (20261027).ttml"), "w") as f:
            f.write("x")
        os.symlink(old, os.path.join(self.desktop, "PodcastsChatGPT"))  # 以前の版のショートカット
        agent = os.path.join(self.home, "Library", "LaunchAgents", "com.podcastdrive.podcastschatgpt.dropbox.plist")
        os.makedirs(os.path.dirname(agent))
        with open(agent, "w") as f:
            f.write("<plist/>")  # 以前の版の「Dropbox へ自動コピー」
        out = self.run_app()
        self.assertIn("Dropbox へ 3 個のファイルを移しました", out)
        self.assertIn("自動コピー」の設定を止めました", out)
        self.assertFalse(os.path.exists(old) or os.path.exists(older) or os.path.exists(agent))
        self.assertEqual(self.files(), ["[A] B (20261028).mp3", "[C] D (20261027).ttml"])
        with open(os.path.join(self.lib, ".collected.json"), encoding="utf-8") as f:
            self.assertIn("uuid-9", json.load(f)["episodes"])
        self.assertEqual(os.readlink(os.path.join(self.desktop, "PodcastsChatGPT")), self.lib)

    def test_missing_dropbox(self):
        os.rmdir(self.dropbox)
        out = self.run_app(1)
        self.assertIn("Dropbox のフォルダが見つかりません", out)
        self.assertFalse(os.path.exists(os.path.join(self.desktop, "PodcastsChatGPT")))

    def test_missing_library(self):
        os.remove(os.path.join(self.group, "Documents", "MTLibrary.sqlite"))
        self.assertIn("Apple Podcasts のライブラリが見つかりません", self.run_app(1))


if __name__ == "__main__":
    unittest.main()

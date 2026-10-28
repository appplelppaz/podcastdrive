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
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "collect_for_chatgpt.py")
EPOCH = datetime(2001, 1, 1).timestamp()


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.group = os.path.join(t, "group")
        self.lib = os.path.join(t, "Desktop", "Public", "Podcasts")
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

    def add(self, pk, pod, title, pub, downloaded, ttml=True):
        audio = os.path.join(self.group, "Library", "Cache", "Audio", f"{pk}.mp3")
        with open(audio, "wb") as f:
            f.write(b"ID3" + bytes(100))
        ts = datetime.strptime(downloaded, "%Y-%m-%d %H:%M").timestamp()
        os.utime(audio, (ts, ts))
        tid = f"transcript_{pk}"
        if ttml:
            self.add_ttml(pk)
        self.db.execute("INSERT INTO ZMTEPISODE VALUES (?,?,?,?,?,?,?,?)",
                        (pk, title, "file://" + audio.replace(" ", "%20"), pod,
                         datetime.strptime(pub, "%Y-%m-%d").timestamp() - EPOCH, f"uuid-{pk}", 1000 + pk, tid))
        self.db.commit()

    def add_ttml(self, pk):
        with open(os.path.join(self.group, "Library", "Cache", "Assets", "TTML", f"transcript_{pk}-{1000 + pk}.ttml"), "w") as f:
            f.write("<tt/>")

    def run_app(self):
        env = dict(os.environ, PODCASTS_GROUP=self.group, PODCASTS_DIR=self.lib, DESKTOP_DIR=self.desktop, NO_OPEN="1")
        r = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def files(self):
        dest = os.path.join(self.lib, "PodcastsChatGPT")
        return sorted(f for f in os.listdir(dest) if not f.startswith("."))

    def test_collect(self):
        self.add(1, 1, "Iran’s economy: Counting the cost of war", "2026-01-01", "2026-10-28 09:00")
        self.add(2, 1, "Old episode", "2026-10-20", "2026-10-27 23:59")       # 前日のダウンロード（対象外）
        self.add(3, 2, "第95集 汉字/拼音?", "2026-10-25", "2026-10-28 10:00", ttml=False)  # 字幕がまだない
        out = self.run_app()
        self.assertEqual(self.files(), [
            "[Business Daily] Iran’s economy Counting the cost of war (20260101).mp3",
            "[Business Daily] Iran’s economy Counting the cost of war (20260101).ttml",
            "[TeaTime Chinese 茶歇中文] 第95集 汉字 拼音 (20261025).mp3",
        ])
        self.assertIn("字幕がまだない回（1件", out)
        link = os.path.join(self.desktop, "PodcastsChatGPT")
        self.assertTrue(os.path.islink(link) and os.path.samefile(link, os.path.join(self.lib, "PodcastsChatGPT")))

        # 2回目: 新しいものはない
        self.assertIn("字幕がまだない回（1件", self.run_app())
        # 字幕が後からできた → 字幕だけ足す
        self.add_ttml(3)
        out = self.run_app()
        self.assertIn("字幕（TTML）をコピー（1件", out)
        self.assertIn("[TeaTime Chinese 茶歇中文] 第95集 汉字 拼音 (20261025).ttml", self.files())
        # 集めたファイルを移しても（会社へ送った後など）、もう一度はコピーしない
        dest = os.path.join(self.lib, "PodcastsChatGPT")
        for f in self.files():
            os.remove(os.path.join(dest, f))
        self.assertIn("新しくダウンロードされた回はありませんでした", self.run_app())
        self.assertEqual(self.files(), [])
        with open(os.path.join(dest, ".collected.json"), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)["episodes"]), 2)

    def test_missing_library(self):
        os.remove(os.path.join(self.group, "Documents", "MTLibrary.sqlite"))
        env = dict(os.environ, PODCASTS_GROUP=self.group, PODCASTS_DIR=self.lib, DESKTOP_DIR=self.desktop, NO_OPEN="1")
        r = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Apple Podcasts のライブラリが見つかりません", r.stdout)


if __name__ == "__main__":
    unittest.main()

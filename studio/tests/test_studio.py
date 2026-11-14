"""Listening Studio のテスト。  python3 -m unittest discover -s studio/tests

Claude Code の代わりに決まった返事を返す関数を使い、音声は espeak-ng で作る（どちらも外部サービスを使わない）。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import annotate  # noqa: E402
import pipeline  # noqa: E402


def fake_claude(replies=None, calls=None):
    """入力の各文に、決まった形の返事を返す。replies があれば先にそれを順に返す。"""
    replies = list(replies or [])

    def ask(prompt):
        if calls is not None:
            calls.append(prompt)
        if replies:
            return replies.pop(0)
        items = json.loads(prompt[prompt.rindex("\n\n[") + 2:].split("\n\n前回の返事")[0])
        out = []
        for it in items:
            hard = "cheaper" in it["text"]
            out.append({"id": it["id"], "translation": "訳%d。" % it["id"],
                        "words": [{"word": "the", "meaning": "その", "pos": "冠詞"},
                                  {"word": "look for", "meaning": "探す、求める", "pos": "句動詞"}] if hard else [],
                        "grammar": "looking for が進行形で、今まさに探していることを表します。" if hard else "",
                        "say": [["en", "looking for"], ["xx", "が進行形です。"]] if hard else [["ja", "不要な説明"]]})
        return "Here you go:\n```json\n" + json.dumps(out, ensure_ascii=False) + "\n```"
    return ask


class AnnotateTest(unittest.TestCase):
    SENTS = [{"id": 1, "original": "Good morning."}, {"id": 2, "original": "Many shoppers are looking for cheaper options."}]

    def test_shapes_and_selection(self):
        notes = annotate.annotate(self.SENTS, "en", "B2", ask=fake_claude(), log=lambda *a: None)
        self.assertEqual(notes[1]["words"], [])
        self.assertEqual(notes[1]["speak"], {"vocab": [], "grammar": []})  # 解説がなければ読み上げも空
        words = [w["word"] for w in notes[2]["words"]]
        self.assertIn("look for", words)
        self.assertEqual(notes[2]["speak"]["vocab"][-1], ["look for", "探す"])  # 意味は最初の項目だけ
        self.assertEqual(notes[2]["speak"]["grammar"], [["en", "looking for"], ["ja", "が進行形です。"]])  # 知らない言語は ja に

    @unittest.skipUnless(annotate._zipf("the", "en"), "wordfreq が必要")
    def test_frequency(self):
        notes = annotate.annotate(self.SENTS, "en", "B2", ask=fake_claude(), log=lambda *a: None)
        self.assertNotIn("the", [w["word"] for w in notes[2]["words"]])  # 易しすぎる語の説明は外す
        self.assertEqual(annotate.rare_words("The hyphae retract in Paris today.", "en"), ["hyphae", "retract"])
        self.assertFalse(annotate.too_easy("have a hard time", "en"))  # 熟語は外さない

    def test_retry_and_cache(self):
        calls = []
        bad = json.dumps([{"id": 1, "translation": "訳"}])  # 件数が足りない
        with tempfile.TemporaryDirectory() as tmp:
            cache = os.path.join(tmp, "notes.json")
            notes = annotate.annotate(self.SENTS, "en", "B2", ask=fake_claude([bad], calls), log=lambda *a: None, cache_path=cache)
            self.assertEqual(len(calls), 2)
            self.assertIn("前回の返事は使えませんでした", calls[1])
            self.assertEqual(sorted(notes), [1, 2])
            again = annotate.annotate(self.SENTS, "en", "B2", ask=lambda p: self.fail("should use the cache"), cache_path=cache)
            self.assertEqual(again, notes)

    def test_gives_up(self):
        with self.assertRaises(RuntimeError):
            annotate.annotate(self.SENTS, "en", ask=lambda p: "sorry", log=lambda *a: None)

    def test_shift_detected(self):
        items = [{"id": i, "text": "It rose %d percent in 20%d." % (i + 10, i + 10)} for i in range(1, 6)]
        ok = [{"id": i, "translation": "20%d年に%dパーセント上がった。" % (i + 10, i + 10)} for i in range(1, 6)]
        self.assertIsNone(annotate.check_batch(items, ok))
        shifted = [{"id": i, "translation": "別の文です。"} for i in range(1, 6)]
        self.assertIn("shifted", annotate.check_batch(items, shifted))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("espeak-ng"), "ffmpeg と espeak-ng が必要")
class PipelineTest(unittest.TestCase):
    def test_make(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx, lib, ready = (os.path.join(tmp, d) for d in ("fx", "Podcasts", "Ready"))
            home = os.path.join(tmp, "nohome")
            subprocess.run([sys.executable, os.path.join(HERE, "make_fixture.py"), fx], check=True, capture_output=True)
            inbox = os.path.join(lib, "英語", "Demo Radio")  # ライブラリは下のフォルダも探す
            os.makedirs(inbox)
            name = "[Demo Radio] Morning market check (20260101)"
            for ext in (".mp3", ".ttml"):
                shutil.copy(os.path.join(fx, name + ext), inbox)
            shutil.copy(os.path.join(fx, name + ".mp3"), os.path.join(inbox, "lonely.mp3"))
            open(os.path.join(inbox, "notes.txt"), "w").close()
            eps, problem = pipeline.scan(lib, ready, home=home)
            eps.sort(key=lambda e: e["name"])
            self.assertEqual(problem, "")
            self.assertEqual([(e["name"], bool(e["ttml"]), e["done"], e["source"]) for e in eps],
                             [(name, True, False, os.path.join("英語", "Demo Radio")), ("lonely", False, False, os.path.join("英語", "Demo Radio"))])
            steps = []
            out = pipeline.make(eps[0], ready, os.path.join(tmp, "work"), "B2", None, "espeak-ng", ask=fake_claude(),
                                log=lambda *a: None, step=lambda s, d=0, t=0: steps.append(s))
            self.assertEqual(sorted(os.listdir(ready)), ["Morning market check (2026-01-01).listening.json",
                                                         "Morning market check (2026-01-01).listening.m4a"])
            with open(out["json"], encoding="utf-8") as f:
                doc = json.load(f)
            self.assertEqual(doc["audio"]["mode"], "sprite")
            self.assertEqual(len(doc["segments"]), 4)
            self.assertEqual(doc["segments"][2]["translation"], "訳3。")
            self.assertTrue(all("orig:" + s["id"] in doc["audio"]["clips"] for s in doc["segments"]))
            self.assertEqual([s for i, s in enumerate(steps) if i == 0 or s != steps[i - 1]], ["Notes", "Voice"])
            done_of = lambda *a: {e["name"]: e["done"] for e in pipeline.scan(lib, *a, home=home)[0]}  # noqa: E731
            self.assertEqual(done_of(ready), {name: True, "lonely": False})
            # 選んだものだけ Drive へ移す。移した後も「できあがり」のまま
            drive = os.path.join(tmp, "PodcastDrive")
            with self.assertRaises(RuntimeError):
                pipeline.move_finished({"x"}, ready, drive)
            os.makedirs(drive)
            done = pipeline.finished(ready)
            self.assertEqual([e["name"] for e in done], ["Morning market check (2026-01-01)"])
            self.assertEqual(pipeline.move_finished({"something else"}, ready, drive), [])
            self.assertEqual(pipeline.move_finished({done[0]["name"]}, ready, drive), [done[0]["name"]])
            self.assertEqual(os.listdir(ready), [])
            self.assertEqual(len(os.listdir(drive)), 2)
            self.assertTrue(done_of(ready, drive)[name])
            home = os.path.join(tmp, "home")
            self.assertEqual(pipeline.find_drive(home), "")
            os.makedirs(os.path.join(home, "Library", "CloudStorage", "GoogleDrive-a@b.c", "My Drive", "Podcastsdrive"))
            self.assertTrue(pipeline.find_drive(home).endswith(os.path.join("My Drive", "Podcastsdrive")))

    def test_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = os.path.join(tmp, "fx")
            subprocess.run([sys.executable, os.path.join(HERE, "make_fixture.py"), fx], check=True, capture_output=True)
            ep = dict(pipeline.sources.library_episodes(fx)[0], json=None)
            with self.assertRaises(pipeline.Cancelled):
                pipeline.make(ep, tmp, os.path.join(tmp, "work"), ask=fake_claude(), log=lambda *a: None, cancelled=lambda: True)


class AppleTest(unittest.TestCase):
    """Apple Podcasts のライブラリ（の形をまねた小さなデータ）から、音声と字幕を見つける。"""

    def test_collect(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as home:
            group = os.path.join(home, "Library", "Group Containers", "243LU875E5.groups.com.apple.podcasts")
            cache, ttml_dir = os.path.join(group, "Library", "Cache"), os.path.join(group, "Library", "Cache", "Assets", "TTML", "PodcastContent")
            os.makedirs(os.path.join(group, "Documents"))
            os.makedirs(ttml_dir)
            for n in ("a.mp3", "b.mp3"):
                open(os.path.join(cache, n), "wb").close()
            open(os.path.join(ttml_dir, "transcript_111.ttml"), "w").close()
            db = sqlite3.connect(os.path.join(group, "Documents", "MTLibrary.sqlite"))
            db.execute("CREATE TABLE ZMTPODCAST (Z_PK INTEGER, ZTITLE TEXT)")
            db.execute("CREATE TABLE ZMTEPISODE (ZTITLE TEXT, ZASSETURL TEXT, ZPODCAST INTEGER, ZPUBDATE REAL, ZUUID TEXT, ZSTORETRACKID INTEGER, ZFREETRANSCRIPTIDENTIFIER TEXT)")
            db.execute("INSERT INTO ZMTPODCAST VALUES (1, 'Demo: Radio')")
            day = 86400 * (365 * 25 + 6)  # 2001-01-01 から25年後 = 2026-01-01
            db.executemany("INSERT INTO ZMTEPISODE VALUES (?,?,?,?,?,?,?)", [
                ("With subtitles", "file://" + os.path.join(cache, "a.mp3"), 1, day, "u1", 111, "PodcastContent/transcript_111.ttml"),
                ("Audio only", "file://" + os.path.join(cache, "b.mp3"), 1, day, "u2", 222, None),
                ("Not downloaded", "file://" + os.path.join(cache, "gone.mp3"), 1, day, "u3", 333, None),
                ("Streaming", None, 1, day, "u4", 444, None)])
            db.commit()
            db.close()
            lib = os.path.join(home, "Desktop", "Podcasts")
            os.makedirs(lib)
            self.assertEqual(pipeline.sources.find_library(home), lib)
            open(os.path.join(lib, "[Demo Radio] Audio only (20260101).mp3"), "wb").close()  # 同じ名前はライブラリを使う
            eps, problem = pipeline.sources.collect(lib, home)
            self.assertEqual(problem, "")
            got = {e["name"]: (e["source"], bool(e["ttml"])) for e in eps}
            self.assertEqual(got, {"[Demo Radio] With subtitles (20260101)": ("Apple Podcasts", True),
                                   "[Demo Radio] Audio only (20260101)": ("", False)})


if __name__ == "__main__":
    unittest.main()

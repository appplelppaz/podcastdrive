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

import difficulty  # noqa: E402
import pipeline  # noqa: E402
import prompt  # noqa: E402
import review  # noqa: E402


class DifficultyTest(unittest.TestCase):
    @unittest.skipUnless(difficulty._zipf("the", "en"), "wordfreq が必要")
    def test_frequency(self):
        self.assertEqual(difficulty.rare_words("The hyphae retract in Paris today.", "en"), ["hyphae", "retract"])
        self.assertTrue(difficulty.too_easy("the", "en"))
        self.assertFalse(difficulty.too_easy("have a hard time", "en"))  # 熟語は外さない
        self.assertTrue(difficulty.too_easy("don't know if", "en"))  # 足せば分かる易しい句は外す
        self.assertFalse(difficulty.too_easy("look for", "en", pos="句動詞"))

    def test_review(self):
        doc = {"metadata": {"sourceLanguage": "en"}, "segments": [
            {"id": 1, "original": "It is quite damning.", "translation": "かなり致命的、damning、だ。",
             "words": [{"word": "damning", "meaning": "", "pos": "形容詞"}]},
            {"id": 2, "original": "Good morning.", "translation": "おはよう。", "words": []}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False)
            out = review.review(path)
        self.assertEqual(len(out), 1)
        self.assertIn("no meaning: damning", out[0])
        self.assertIn("gloss in translation", out[0])


class VoiceTest(unittest.TestCase):
    def test_mac_voices(self):
        import server
        listing = "Kyoko               ja_JP    # こんにちは\nKyoko (Enhanced)    ja_JP    # こんにちは\nSamantha            en_US    # Hello\n"
        real_which, real_run = server.shutil.which, server.subprocess.run
        server.shutil.which = lambda n: "/usr/bin/say"
        server.subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=listing)
        try:
            self.assertEqual(server.mac_voices(), ["mac:Kyoko", "mac:Kyoko (Enhanced)"])
        finally:
            server.shutil.which, server.subprocess.run = real_which, real_run
        self.assertEqual(server.voice_setup("mac:Kyoko (Enhanced)"), ("say", {"ja": "Kyoko (Enhanced)"}))


class PromptTest(unittest.TestCase):
    def test_prompt(self):
        text = prompt.build([{"name": "A", "audio": "/p/A.mp3", "ttml": "/p/A.ttml", "json_out": "/p/A.json", "lang": "en"},
                             {"name": "B", "audio": "/p/B.mp3", "ttml": None, "json_out": "/p/B.json", "lang": "zh", "replace": True}],
                            "C1", "/venv/bin/python")
        for want in ('"/p/A.ttml"', '"/p/B.json"', "guide.md", "validate.py", "review.py", "transcribe.py", "--force", "C1", "/venv/bin/python"):
            self.assertIn(want, text)
        self.assertNotIn("transcribe.py", prompt.build([{"name": "A", "audio": "/a", "ttml": "/t", "json_out": "/j"}]))


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
            for ext in (".mp3", ".ttml", ".json"):
                shutil.copy(os.path.join(fx, name + ext), inbox)
            shutil.copy(os.path.join(fx, name + ".mp3"), os.path.join(inbox, "lonely.mp3"))
            open(os.path.join(inbox, "notes.txt"), "w").close()
            eps, problem = pipeline.scan(lib, ready, home=home)
            eps.sort(key=lambda e: e["name"])
            self.assertEqual(problem, "")
            self.assertEqual([(e["name"], bool(e["ttml"]), e["done"], e["source"]) for e in eps],
                             [(name, True, False, os.path.join("英語", "Demo Radio")), ("lonely", False, False, os.path.join("英語", "Demo Radio"))])
            steps = []
            with self.assertRaises(RuntimeError):  # .json（Claude に作ってもらうもの）がまだない回は作れない
                pipeline.make(eps[1], ready, os.path.join(tmp, "work"), None, "espeak-ng", log=lambda *a: None)
            out = pipeline.make(eps[0], ready, os.path.join(tmp, "work"), None, "espeak-ng",
                                log=lambda *a: None, step=lambda s, d=0, t=0: steps.append(s))
            self.assertEqual(sorted(os.listdir(ready)), ["Morning market check (2026-01-01).listening.json",
                                                         "Morning market check (2026-01-01).listening.m4a"])
            with open(out["json"], encoding="utf-8") as f:
                doc = json.load(f)
            self.assertEqual(doc["audio"]["mode"], "sprite")
            self.assertEqual(len(doc["segments"]), 4)
            self.assertTrue(doc["segments"][2]["translation"])
            self.assertTrue(all("orig:" + s["id"] in doc["audio"]["clips"] for s in doc["segments"]))
            self.assertEqual([s for i, s in enumerate(steps) if i == 0 or s != steps[i - 1]], ["Voice"])
            done_of = lambda *a: {e["name"]: e["done"] for e in pipeline.scan(lib, *a, home=home)[0]}  # noqa: E731
            self.assertEqual(done_of(ready), {name: True, "lonely": False})
            for f in os.listdir(ready):  # 音声と同じフォルダに置いたものも「できあがり」
                shutil.move(os.path.join(ready, f), inbox)
            self.assertEqual(done_of(ready), {name: True, "lonely": False})
            for f in os.listdir(inbox):
                if ".listening." in f:
                    shutil.move(os.path.join(inbox, f), ready)
            self.assertEqual([e["name"] for e in pipeline.finished(ready)], ["Morning market check (2026-01-01)"])

    def test_line_timed_subtitles(self):
        # 1行ごとの時刻しかない字幕は、単語の時刻を作り直す（Whisper を呼ぶ）。訳・解説の .json はそのまま
        with tempfile.TemporaryDirectory() as tmp:
            fx = os.path.join(tmp, "fx")
            subprocess.run([sys.executable, os.path.join(HERE, "make_fixture.py"), fx], check=True, capture_output=True)
            name = "[Demo Radio] Morning market check (20260101)"
            line = ('<tt xmlns="http://www.w3.org/ns/ttml" xml:lang="en"><body><div>'
                    '<p begin="00:00:00.000" end="00:00:12.000">Good morning and welcome to the market check.</p></div></body></tt>')
            with open(os.path.join(fx, name + ".ttml"), "w") as f:
                f.write(line)
            ep = next(e for e in pipeline.sources.library_episodes(fx) if e["name"] == name)
            self.assertFalse(pipeline.sources.has_word_times(ep["ttml"]))
            calls = []
            import transcribe
            real = transcribe.main

            def fake_whisper(argv):
                calls.append(argv)
                raise SystemExit("Whisper is not installed")
            transcribe.main = fake_whisper
            try:
                with self.assertRaises(RuntimeError):
                    pipeline.make(ep, tmp, os.path.join(tmp, "work"), log=lambda *a: None)
            finally:
                transcribe.main = real
            self.assertEqual(len(calls), 1)
            self.assertIn("--lang", calls[0])
            self.assertEqual(calls[0][calls[0].index("--lang") + 1], "en")

    def test_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = os.path.join(tmp, "fx")
            subprocess.run([sys.executable, os.path.join(HERE, "make_fixture.py"), fx], check=True, capture_output=True)
            ep = pipeline.sources.library_episodes(fx)[0]
            with self.assertRaises(pipeline.Cancelled):
                pipeline.make(ep, tmp, os.path.join(tmp, "work"), log=lambda *a: None, cancelled=lambda: True)


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
            # 言語: 言語のフォルダ名か、字幕の xml:lang。分からなければ空
            lang = pipeline.sources.language_of
            self.assertEqual(lang({"source": os.path.join("中国語", "Show"), "ttml": None}), "zh")
            with open(os.path.join(ttml_dir, "transcript_111.ttml"), "w") as f:
                f.write('<tt xmlns="http://www.w3.org/ns/ttml" xml:lang="fr-FR"><body/></tt>')
            self.assertEqual(lang({"source": "Apple Podcasts", "ttml": os.path.join(ttml_dir, "transcript_111.ttml")}), "fr")
            self.assertEqual(lang({"source": "Apple Podcasts", "ttml": None}), "")
            self.assertEqual(lang({"source": os.path.join("Shows", "English", "BBC"), "ttml": None}), "en")  # 途中のフォルダ名でもよい
            self.assertEqual(lang({"source": "x", "show_lang": "es"}), "es")
            text = pipeline.sources.language_from_text
            self.assertEqual([text(t) for t in ("[故事FM] 我在北京送外卖", "[Radio Ambulante] El niño que no quería hablar",
                                                "[Hoy Hablamos] Noticias de la semana", "[InnerFrench] Les Français et le travail",
                                                "[The Daily] What the election means", "[BBC] Newshour", "x")],
                             ["zh", "es", "es", "fr", "en", "en", ""])
            # できたファイルの置き場所: ライブラリの回は音声と同じフォルダ、Apple の回は「言語/番組名」、言語が分からなければ ready
            ready = os.path.join(home, "Ready")
            eps, _ = pipeline.sources.collect(lib, home)  # 字幕に言語を書いた後で、もう一度探す
            by = {e["name"]: e for e in eps}
            self.assertEqual(by["[Demo Radio] With subtitles (20260101)"]["lang"], "fr")
            self.assertEqual(pipeline.out_dir(by["[Demo Radio] With subtitles (20260101)"], lib, ready), os.path.join(lib, "フランス語", "Demo Radio"))
            self.assertEqual(pipeline.out_dir(by["[Demo Radio] Audio only (20260101)"], lib, ready), lib)
            os.makedirs(os.path.join(lib, "French"))  # すでにある言語のフォルダを使う
            self.assertEqual(pipeline.out_dir(by["[Demo Radio] With subtitles (20260101)"], lib, ready), os.path.join(lib, "French", "Demo Radio"))
            self.assertEqual(pipeline.out_dir({"apple": True, "lang": "", "name": "[X] y", "audio": "a"}, lib, ready), ready)
            self.assertEqual(by["[Demo Radio] Audio only (20260101)"]["lang"], "fr")  # 同じ番組のほかの回に合わせる
            # Prompt の前に、Apple の回をライブラリの「言語/番組名」へコピーする（元はそのまま）
            col = pipeline.collect(by["[Demo Radio] With subtitles (20260101)"], lib)
            self.assertEqual(os.path.dirname(col["audio"]), os.path.join(lib, "French", "Demo Radio"))
            self.assertTrue(os.path.exists(col["ttml"]) and os.path.exists(os.path.join(cache, "a.mp3")))
            self.assertEqual(os.path.basename(col["audio"]), "[Demo Radio] With subtitles (20260101).mp3")
            got = {e["name"]: (e["source"], bool(e["ttml"])) for e in eps}
            self.assertEqual(got, {"[Demo Radio] With subtitles (20260101)": ("Apple Podcasts", True),
                                   "[Demo Radio] Audio only (20260101)": ("", False)})


if __name__ == "__main__":
    unittest.main()

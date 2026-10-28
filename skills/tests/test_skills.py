"""スキル（podcast-json / ttml-to-json / podcast-transcribe）のスクリプトの確認。

    python3 -m unittest skills/tests/test_skills.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.dirname(HERE)
PJ = os.path.join(SKILLS, "podcast-json", "scripts")
sys.path.insert(0, PJ)

import parse_ttml  # noqa: E402
import add_word_timings  # noqa: E402
import validate_json  # noqa: E402


def load_transcribe():
    path = os.path.join(SKILLS, "podcast-transcribe", "scripts", "transcribe.py")
    spec = importlib.util.spec_from_file_location("transcribe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


APPLE = """<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:podcasts="http://podcasts.apple.com/transcript-ttml-internal" xml:lang="en">
<body dur="00:00:12.000"><div>
<p begin="1.0" end="6.0"><span podcasts:unit="sentence" begin="1.0" end="3.0">
<span podcasts:unit="word" begin="1.0" end="1.4">Prices</span> <span podcasts:unit="word" begin="1.4" end="1.9">rose</span>
<span podcasts:unit="word" begin="1.9" end="2.3">by</span> <span podcasts:unit="word" begin="2.3" end="3.0">50%.</span></span>
<span podcasts:unit="sentence" begin="3.2" end="6.0"><span podcasts:unit="word" begin="3.2" end="3.6">Mr.</span>
<span podcasts:unit="word" begin="3.6" end="4.2">Smith</span> <span podcasts:unit="word" begin="4.2" end="6.0">agreed.</span></span></p>
<p begin="7.0" end="9.0"><span podcasts:unit="word" begin="7.0" end="9.0">Yes.</span></p>
</div></body></tt>
"""

WHISPER_LINES = """<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:podcasts="http://podcasts.apple.com/transcript-ttml-internal" xml:lang="zh"><body dur="00:00:30.000"><div>
<p begin="00:00:00.000" end="00:00:05.820"><span podcasts:unit="word" begin="00:00:00.000" end="00:00:05.820">大家好，我是Nathan。</span></p>
<p begin="00:00:05.820" end="00:00:12.000"><span podcasts:unit="word" begin="00:00:05.820" end="00:00:12.000">今天我们聊丝绸之路</span></p>
</div></body></tt>
"""


def episode(segs, lang="en"):
    return {"metadata": {"title": "C - E", "channel": "C", "episode": "E", "publishDate": "2026-01-01",
                         "sourceLanguage": lang, "targetLanguage": "ja"}, "segments": segs}


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path


class ParseTtmlTest(Temp):
    def test_apple_word_level(self):
        path = self.write("a.ttml", APPLE)
        lang, segs = parse_ttml.parse_ttml(path)
        self.assertEqual(lang, "en")
        self.assertEqual(parse_ttml.ttml_timing(path), "word")
        self.assertEqual([s["original"] for s in segs], ["Prices rose by 50%.", "Mr. Smith agreed.", "Yes."])
        self.assertEqual([(s["begin"], s["end"]) for s in segs],
                         [("00:00:01.000", "00:00:03.000"), ("00:00:03.200", "00:00:06.000"), ("00:00:07.000", "00:00:09.000")])
        self.assertEqual(segs[0]["wordTimings"][3], [2.3, 3.0, "50%."])
        self.assertEqual(segs[2]["wordTimings"], [[7.0, 9.0, "Yes."]])  # 1語の文にも付ける

    def test_whisper_line_level_has_no_word_timings(self):
        path = self.write("z.ttml", WHISPER_LINES)
        lang, segs = parse_ttml.parse_ttml(path)
        self.assertEqual((lang, parse_ttml.ttml_timing(path)), ("zh", "line"))
        self.assertTrue(all("wordTimings" not in s for s in segs))
        self.assertEqual(segs[0]["original"], "大家好，我是Nathan。")

    def test_cjk_words_join_without_spaces(self):
        self.assertEqual(parse_ttml.join_words(["今天", "我们", "聊", "Elon", "Musk", "。"]), "今天我们聊Elon Musk。")
        self.assertEqual(parse_ttml.join_words(["Hello", ",", "world."]), "Hello, world.")

    def test_write_keeps_word_timings_on_one_line(self):
        out = os.path.join(self.dir, "e.json")
        doc = episode([{"id": 1, "begin": "00:00:01.000", "end": "00:00:02.000", "original": 'Hi "there".',
                        "translation": "やあ。", "words": [], "grammar": "", "wordTimings": [[1.0, 1.5, "Hi"], [1.5, 2.0, '"there".']]}])
        parse_ttml.write_episode_json(doc, out)
        with open(out, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(json.loads(text), doc)
        self.assertIn('"wordTimings": [[1.0,1.5,"Hi"],[1.5,2.0,"\\"there\\"."]]', text)


class AddWordTimingsTest(Temp):
    def test_adds_only_word_timings(self):
        self.write("a.ttml", APPLE)
        segs = [{"id": 1, "begin": "00:00:01.000", "end": "00:00:06.000", "original": "Prices rose by 50%. Mr. Smith agreed.",
                 "translation": "物価は50%上がった。スミス氏は同意した。", "words": [{"word": "rose", "meaning": "上がった", "pos": "動詞"}],
                 "grammar": "", "extra": {"keep": True}},
                {"id": 2, "begin": "00:00:07.000", "end": "00:00:09.000", "original": "No.", "translation": "いいえ。", "words": [], "grammar": ""}]
        path = self.write("a.json", json.dumps(episode(segs), ensure_ascii=False))
        rep = add_word_timings.add_word_timings(path)
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(len(doc["segments"][0]["wordTimings"]), 7)
        self.assertEqual(rep["with_timings"], 2)
        self.assertEqual([m[0] for m in rep["mismatch"]], [2])  # "No." と話された "Yes." は合わない
        for s in doc["segments"]:
            s.pop("wordTimings")
        self.assertEqual(doc, episode(segs))  # ほかは何も変えない

    def test_refuses_line_level_ttml(self):
        self.write("z.ttml", WHISPER_LINES)
        path = self.write("z.json", json.dumps(episode([], "zh")))
        with self.assertRaises(SystemExit):
            add_word_timings.add_word_timings(path)


class ValidateTest(Temp):
    def seg(self, i, orig, tr, b, e):
        return {"id": i, "begin": parse_ttml.norm_time(b), "end": parse_ttml.norm_time(e), "original": orig,
                "translation": tr, "words": [], "grammar": ""}

    def test_detects_shifted_translations(self):
        pairs = [("It costs 50 dollars.", "50ドルかかる。"), ("About 300 people came.", "約300人が来た。"),
                 ("In 2017 it began.", "2017年に始まった。"), ("Prices rose 12 percent.", "物価は12%上がった。"),
                 ("We waited 40 days.", "40日待った。")]
        good = [self.seg(i + 1, o, t, i * 3, i * 3 + 2) for i, (o, t) in enumerate(pairs)]
        path = self.write("g.json", json.dumps(episode(good), ensure_ascii=False))
        self.assertEqual(validate_json.validate(path)[0], [])
        trs = [t for _, t in pairs]
        shifted = [self.seg(i + 1, o, trs[i - 1] if i else "最初。", i * 3, i * 3 + 2) for i, (o, _) in enumerate(pairs)]
        path = self.write("s.json", json.dumps(episode(shifted), ensure_ascii=False))
        errs, _ = validate_json.validate(path)
        self.assertTrue(any("ずれている" in e for e in errs), errs)

    def test_structure_errors(self):
        segs = [self.seg(1, "A.", "あ。", 5, 4), self.seg(1, "B.", "", 6, 7)]
        segs[1]["begin"] = "6.0"
        path = self.write("x.json", json.dumps(episode(segs), ensure_ascii=False))
        errs, _ = validate_json.validate(path)
        text = "\n".join(errs)
        for msg in ("end が begin より前", "id は 1 から順に", "HH:MM:SS.mmm", "translation が空"):
            self.assertIn(msg, text)

    def test_text_against_ttml(self):
        self.write("a.ttml", APPLE)
        segs = [self.seg(1, "Prices rose by 50%.", "物価は50%上がった。", 1, 3), self.seg(2, "Completely different words.", "別。", 3.2, 6)]
        path = self.write("a.json", json.dumps(episode(segs), ensure_ascii=False))
        errs, warns = validate_json.validate(path, os.path.join(self.dir, "a.ttml"))
        self.assertTrue(any("合わない区間: [2]" in e for e in errs), errs)
        self.assertTrue(any("wordTimings" in w for w in warns))

    def test_listening_notes(self):
        s = self.seg(1, "A.", "あ。", 1, 2)
        s["grammar"] = "ここは受け身。【訂正】音声認識では「X」。"
        path = self.write("n.json", json.dumps(episode([s]), ensure_ascii=False))
        _, warns = validate_json.validate(path)
        self.assertTrue(any("別の行" in w for w in warns))


class TranscribeTest(Temp):
    """Whisper を使わずに、単語の時刻から文と TTML を作るところを確かめる。"""

    def test_chinese_sentences_and_ttml(self):
        tr = load_transcribe()
        w = lambda b, e, t, sp=False: [b, e, t, sp]  # noqa: E731
        words = [w(0.0, 0.4, "大家"), w(0.4, 0.8, "好，"), w(0.8, 1.2, "我"), w(1.2, 1.6, "是"), w(1.6, 2.0, " Nathan", True),
                 w(2.0, 2.2, "。"),
                 # 句点がなくても 1 秒以上の間で区切る
                 w(3.5, 3.9, "今天"), w(3.9, 4.3, "我们"), w(4.3, 4.7, "聊"), w(4.7, 5.0, "丝绸之路"),
                 w(6.5, 7.0, "谢谢观看"),
                 w(9.0, 9.5, "再见"), w(9.5, 10.0, "。")]
        words = tr.clean_times([[x[0], x[1], x[2].strip(), x[3]] for x in words])
        sents = tr.sentences_of(words, cjk=True)
        self.assertEqual([tr.join(s) for s in sents], ["大家好，我是 Nathan。", "今天我们聊丝绸之路。", "再见。"])
        path = os.path.join(self.dir, "zh.ttml")
        tr.write_ttml(path, sents, "zh", 10.0, "test")
        lang, segs = parse_ttml.parse_ttml(path)
        self.assertEqual((lang, parse_ttml.ttml_timing(path)), ("zh", "word"))
        self.assertEqual([s["original"] for s in segs], ["大家好，我是Nathan。", "今天我们聊丝绸之路。", "再见。"])
        self.assertEqual(segs[1]["begin"], "00:00:03.500")
        self.assertEqual(segs[1]["wordTimings"][-1], [4.7, 5.0, "丝绸之路。"])

    def test_long_sentence_splits_at_comma_and_drops_repeats(self):
        tr = load_transcribe()
        words, t = [], 0.0
        for k in range(50):
            text = f"word{k}," if k == 30 else f"word{k}"
            words.append([t, t + 0.3, text, True])
            t += 0.3
        words[-1][2] += "."
        sents = tr.sentences_of(words, cjk=False)
        self.assertEqual(len(sents[0]), 31)  # 上限（45語）を超えたのでカンマのところで分ける
        rep = [[i * 1.0, i * 1.0 + 0.5, "Thanks.", True] for i in range(4)]
        self.assertEqual(len(tr.sentences_of(rep, cjk=False)), 2)  # 同じ文が3回以上続いたら繰り返しとして除く

    def test_script_guess(self):
        tr = load_transcribe()
        self.write("x.json", "这们说为时个来对国会过还发经学开关见动问长门东车马语读书钱")
        self.assertEqual(tr.guess_script(os.path.join(self.dir, "a.mp3")), "simplified")


class NightlyToolsTest(Temp):
    """夜間処理（ChatGPT Work）の _tools: 回のまとめ・言語別フォルダへの移動・訳のデータから JSON。"""

    def run_tool(self, *args):
        tools = os.path.join(os.path.dirname(SKILLS), "nightly", "_tools")
        env = dict(os.environ, PODCAST_ROOT=self.dir, PYTHONUTF8="1")
        r = subprocess.run([sys.executable, os.path.join(tools, args[0])] + list(args[1:]),
                           capture_output=True, text=True, env=env)
        return r.returncode, json.loads(r.stdout) if r.stdout.strip().startswith(("{", "[")) else r.stdout

    def test_scan_place_and_build(self):
        self.write("some episode.ttml", APPLE)
        self.write("lonely.json", "{}")
        code, scan = self.run_tool("inbox.py", "scan")
        acts = {g["key"]: g["action"] for g in scan["inbox"]}
        self.assertEqual(acts, {"some episode": "json_from_ttml", "lonely": "review: JSON だけでは処理できない"})
        code, placed = self.run_tool("inbox.py", "place", "--key", "some episode", "--lang", "en",
                                     "--channel", "Daily: News", "--title", "Prices / costs?", "--date", "20260101")
        ttml = placed["files"]["ttml"]
        self.assertEqual(os.path.relpath(ttml, self.dir),
                         os.path.join("英語", "Daily_ News", "[Daily_ News] Prices _ costs_ (20260101).ttml"))
        code, scan = self.run_tool("inbox.py", "scan")
        self.assertEqual([g["key"] for g in scan["resume"]], ["[Daily_ News] Prices _ costs_ (20260101)"])
        code, st = self.run_tool("build_json.py", "status", ttml)
        self.assertEqual((st["sentences"], st["missing_count"]), (3, 3))
        with open(os.path.join(st["work_dir"], "trans_001.json"), "w", encoding="utf-8") as f:  # 名前に [ ] があっても読む
            json.dump({"1": {"t": "物価は50%上がった。", "w": [{"word": "rose", "meaning": "上がった、増えた", "pos": "動詞"}], "g": ""},
                       "2": {"t": "スミス氏は同意した。", "w": [], "g": ""}}, f, ensure_ascii=False)
        code, res = self.run_tool("build_json.py", "build", ttml)
        self.assertEqual((code, res["missing"]), (1, [3]))  # 全部そろうまで書き出さない
        with open(os.path.join(st["work_dir"], "trans_002.json"), "w", encoding="utf-8") as f:
            json.dump({"3": {"t": "はい。", "w": [], "g": ""}}, f, ensure_ascii=False)
        code, res = self.run_tool("build_json.py", "build", ttml)
        self.assertEqual((code, res["ok"]), (0, True), res)
        with open(res["json"], encoding="utf-8") as f:
            doc = json.load(f)
        self.assertEqual(doc["metadata"]["channel"], "Daily_ News")
        self.assertEqual(doc["metadata"]["publishDate"], "2026-01-01")
        self.assertEqual(doc["segments"][0]["wordTimings"][0], [1.0, 1.4, "Prices"])


class SkillPackageTest(unittest.TestCase):
    """SKILL.md の書式（名前・説明）と、スクリプトの構文。"""

    def test_shared_scripts_are_in_sync(self):
        nightly = os.path.join(os.path.dirname(SKILLS), "nightly", "_tools", "scripts")
        pairs = [(os.path.join(PJ, f), os.path.join(SKILLS, name, "scripts", f))
                 for name in ("ttml-to-json", "podcast-transcribe")
                 for f in ("parse_ttml.py", "add_word_timings.py", "validate_json.py")]
        pairs += [(os.path.join(PJ, f), os.path.join(nightly, f)) for f in ("parse_ttml.py", "add_word_timings.py", "validate_json.py")]
        pairs.append((os.path.join(SKILLS, "podcast-transcribe", "scripts", "transcribe.py"), os.path.join(nightly, "transcribe.py")))
        for src, dst in pairs:
            with open(src, encoding="utf-8") as a, open(dst, encoding="utf-8") as b:
                self.assertEqual(a.read(), b.read(), f"{dst} が古い（python3 skills/build.py を実行）")

    def test_frontmatter_and_syntax(self):
        for name in ("podcast-json", "ttml-to-json", "podcast-transcribe"):
            with open(os.path.join(SKILLS, name, "SKILL.md"), encoding="utf-8") as f:
                text = f.read()
            self.assertTrue(text.startswith("---\n"), name)
            head = text.split("---")[1]
            self.assertIn(f"name: {name}", head.replace('"', ""))
            self.assertIn("description:", head)
            self.assertNotIn("<", head)
        for root, _, files in os.walk(SKILLS):
            for f in files:
                if f.endswith(".py"):
                    subprocess.run([sys.executable, "-m", "py_compile", os.path.join(root, f)], check=True)


if __name__ == "__main__":
    unittest.main()

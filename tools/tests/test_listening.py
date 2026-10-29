"""変換処理と読み上げテキストのテスト。  python3 -m unittest discover -s tools/tests"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from listening import convert, export, source, speech  # noqa: E402

APP = os.path.join(os.path.dirname(os.path.dirname(HERE)), "app")

TTML = """<tt xmlns="http://www.w3.org/ns/ttml" xmlns:podcasts="http://podcasts.apple.com/transcript-ttml-internal" xml:lang="en">
<body dur="20.0"><div><p begin="1.000" end="9.000">
<span begin="1.000" end="4.000" podcasts:unit="sentence"><span begin="1.000" end="2.000" podcasts:unit="word">Prices</span><span begin="2.000" end="3.000" podcasts:unit="word">went</span><span begin="3.000" end="4.000" podcasts:unit="word">up.</span></span>
<span begin="5.000" end="9.000" podcasts:unit="sentence"><span begin="5.000" end="6.000" podcasts:unit="word">People</span><span begin="6.000" end="7.000" podcasts:unit="word">are</span><span begin="7.000" end="9.000" podcasts:unit="word">worried.</span></span>
</p>
<p begin="10.000" end="13.000"><span begin="10.000" end="13.000" podcasts:unit="sentence"><span begin="10.000" end="11.500" podcasts:unit="word">Not</span><span begin="11.500" end="13.000" podcasts:unit="word">much.</span></span></p>
</div></body></tt>"""


def episode(segments, **meta):
    m = {"title": "T", "channel": "Demo", "episode": "E", "publishDate": "2026-01-01",
         "sourceLanguage": "en", "targetLanguage": "ja"}
    m.update(meta)
    return {"metadata": m, "segments": segments}


class TempDir(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, name, content):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
        return path


class TimeTest(unittest.TestCase):
    def test_formats(self):
        self.assertAlmostEqual(source.parse_time("00:00:07.980"), 7.98)
        self.assertAlmostEqual(source.parse_time("17:09.168"), 1029.168)
        self.assertAlmostEqual(source.parse_time("3.46"), 3.46)
        self.assertAlmostEqual(source.parse_time("00:01:02,500"), 62.5)
        self.assertAlmostEqual(source.parse_time("980ms"), 0.98)
        self.assertAlmostEqual(source.parse_time(12), 12.0)
        self.assertEqual(source.format_time(3661.5), "01:01:01.500")
        with self.assertRaises(ValueError):
            source.parse_time("1::2")


class LoadTest(TempDir):
    def test_variants(self):
        p = self.write("a.json", episode([
            {"id": 2, "begin": 5, "end": "00:00:09.000", "original": "B", "translation": "ビー"},
            {"id": 1, "begin": "00:00:01.000", "end": "00:00:04.000", "original": "A", "translation": "エー",
             "words": [{"word": "x", "reading": "x̌", "meaning": "m", "pos": "名詞"}, "y"], "pinyin": "a"},
            {"id": 3, "begin": "bad", "end": "1", "original": "C"},
        ]))
        ep = source.load_episode_json(p)
        self.assertEqual([s["id"] for s in ep["segments"]], [1, 2])  # 時刻順・壊れた区間は除外
        self.assertEqual(ep["segments"][0]["words"][0]["reading"], "x̌")
        self.assertEqual(ep["segments"][0]["words"][1]["word"], "y")
        self.assertEqual(ep["segments"][1]["words"], [])
        self.assertTrue(any("3" in w for w in ep["warnings"]))


class AlignTest(TempDir):
    def test_split_only_with_ttml_evidence(self):
        ttml = source.load_ttml(self.write("a.ttml", TTML))
        seg = {"id": 1, "begin": 1.0, "end": 9.0, "original": "Prices went up. People are worried.",
               "translation": "", "translationParts": ["物価が上がった。", "人々は心配している。"],
               "words": [{"word": "went up", "meaning": "上がった", "pos": ""},
                         {"word": "worried", "meaning": "心配して", "pos": ""}], "grammar": "g"}
        parts = source.split_by_ttml_sentences(seg, ttml)
        self.assertEqual([(p["begin"], p["end"]) for p in parts], [(1.0, 4.0), (5.0, 9.0)])
        self.assertEqual([w["word"] for w in parts[1]["words"]], ["worried"])
        # 訳の数が文の数と合わなければ分けない（時刻を作らない）
        seg["translationParts"] = ["全部"]
        self.assertIsNone(source.split_by_ttml_sentences(seg, ttml))

    def test_snap_within_tolerance(self):
        ttml = source.load_ttml(self.write("a.ttml", TTML))
        b, e, info = source.align_segment({"begin": 10.03, "end": 12.98, "original": "Not much."}, ttml)
        self.assertEqual((b, e, info["method"], info["textMatches"]), (10.0, 13.0, "ttml-words", True))
        b, e, info = source.align_segment({"begin": 10.5, "end": 12.0, "original": "x"}, ttml)
        self.assertEqual((b, e, info["method"]), (10.5, 12.0, "json"))


class SpeechTest(unittest.TestCase):
    def test_clean_ja(self):
        self.assertEqual(speech.clean_ja("（放送を）聴く／見る"), "放送を、聴く、見る")
        self.assertEqual(speech.clean_ja("正在＋動詞：〜しているところ"), "正在と動詞、しているところ")
        self.assertEqual(speech.clean_ja("中間層——すでに縮小——が"), "中間層、すでに縮小、が")
        self.assertNotIn("訂正", speech.clean_ja("文法の説明。\n【訂正】音声認識では「x」"))

    def test_clean_term(self):
        self.assertEqual(speech.clean_term("tamp down (on)"), "tamp down on")
        self.assertEqual(speech.clean_term("keep sb on"), "keep somebody on")
        self.assertEqual(speech.clean_term("A/B"), "A, B")

    def test_runs(self):
        runs = speech.split_runs("[[how]]で始まる部分が、[[looking at]]の目的語です。", "en")
        self.assertEqual(runs, [("en", "how"), ("ja", "で始まる部分が、"), ("en", "looking at"), ("ja", "の目的語です。")])
        self.assertEqual(speech.split_runs("looking at の目的語", "en")[0], ("en", "looking at"))
        self.assertEqual(speech.split_runs("正在 は進行", "zh"), [("ja", "正在 は進行")])  # 中国語は印だけを信じる

    def test_pick_meaning_in_context(self):
        self.assertEqual(speech.pick_meaning("衰退しつつある、低迷している", "経済が低迷している"), "低迷している")
        self.assertEqual(speech.pick_meaning("ようこそ", "x"), "ようこそ")

    def test_plain_meaning(self):
        self.assertEqual(speech.plain_meaning("ここでは、真剣に、という意味です"), "真剣に")
        self.assertEqual(speech.plain_meaning("ここでは「真剣に」という意味"), "真剣に")
        self.assertEqual(speech.plain_meaning("この文では、価格、の意味です。"), "価格")
        for keep in ("意味", "人生の意味", "選択肢、オプション"):
            self.assertEqual(speech.plain_meaning(keep), keep)
        self.assertEqual(speech.vocab_runs({"term": "认真", "meaning": "ここでは、真剣に、という意味です"}, "zh"),
                         [("zh", "认真"), ("ja", "真剣に。")])

    def test_auto_listening_limits(self):
        seg = {"translation": "本日は影響を与えている経済を", "grammar": "how ... — 間接疑問文。how が名詞節を導く。",
               "words": [{"word": "a", "meaning": "あ", "pos": "名詞"},
                         {"word": "be impacting", "meaning": "影響を与えている", "pos": "動詞"},
                         {"word": "Tehran", "meaning": "テヘラン", "pos": "固有名詞"},
                         {"word": "look into", "meaning": "調べる", "pos": "句動詞"}]}
        li = speech.auto_listening(seg, "en")
        self.assertEqual([v["term"] for v in li["vocab"]], ["be impacting", "look into"])  # 最大2つ・熟語優先・固有名詞なし
        self.assertNotIn("...", li["grammar"])
        self.assertNotIn("—", li["grammar"])
        self.assertTrue(li["grammar"].endswith("。"))

    def test_budget(self):
        li = {"vocab": [{"term": "a b", "meaning": "あ" * 40, "note": "い" * 40},
                        {"term": "c", "meaning": "う" * 40, "note": ""}], "grammar": "え" * 50}
        li, notes = speech.fit_budget(li, "en")
        self.assertTrue(notes)
        self.assertLessEqual(len(li["vocab"]), 2)
        self.assertEqual(li["vocab"][0]["note"], "")

    def test_parts_order_and_languages(self):
        li = {"vocab": [{"term": "under pressure", "meaning": "厳しい状況にある", "note": ""}], "grammar": ""}
        parts = speech.build_parts("訳です。", li, "en")
        kinds = [(p["type"], p.get("group"), p.get("lang")) for p in parts]
        self.assertEqual(kinds, [("original", None, None), ("cue", "translation", None), ("speech", "translation", "ja"),
                                 ("cue", "vocab", None), ("speech", "vocab", "en"), ("speech", "vocab", "ja"),
                                 ("cue", "again", None), ("original", None, None)])
        self.assertEqual(parts[5]["text"], "厳しい状況にある。")


class ConvertTest(TempDir):
    def make(self, segments, authored=None, **meta):
        j = self.write("[Demo] Ep (20260101).json", episode(segments, **meta))
        t = self.write("[Demo] Ep (20260101).ttml", TTML)
        a = self.write("auth.json", authored) if authored else None
        out = convert.listening_path_for(j)
        prev = None
        if os.path.exists(out):
            with open(out, encoding="utf-8") as f:
                prev = json.load(f)
        return convert.convert(j, t, None, out, a, previous=prev, log=lambda *x: None), j, out

    SEGS = [
        {"id": 1, "begin": "00:00:01.000", "end": "00:00:09.000", "original": "Prices went up. People are worried.",
         "translation": "物価が上がり、人々は心配しています。", "words": [], "grammar": ""},
        {"id": 2, "begin": "00:00:10.000", "end": "00:00:13.000", "original": "Not much.",
         "translation": "大したことはない。", "words": [{"word": "not much", "meaning": "あまりない", "pos": "熟語"}],
         "grammar": ""},
    ]

    def test_output_schema(self):
        doc, j, out = self.make(self.SEGS)
        self.assertEqual(doc["format"], "podcast-listening")
        self.assertEqual(doc["formatVersion"], 1)
        self.assertTrue(out.endswith("/E (2026-01-01).listening.json"))  # エピソード名 (日付)
        self.assertEqual(doc["sourceLanguage"], "en")
        s = doc["segments"][1]
        for k in ("id", "key", "sourceSegmentIds", "begin", "end", "clip", "original", "translation",
                  "display", "listening", "parts"):
            self.assertIn(k, s)
        # 実際の無音（単語のない隙間）にだけ余白を取る: 前は最大0.12秒、後ろは最大0.25秒
        self.assertEqual(s["clip"], {"begin": 9.88, "end": 13.25})
        self.assertEqual(doc["segments"][0]["alignment"]["ttmlSentences"], 2)  # 2文だが訳が1つなので分けない
        self.assertEqual(s["wordTimings"], [[10.0, 11.5, "Not"], [11.5, 13.0, "much."]])  # 字幕の強調用
        self.assertTrue(os.path.exists(j))  # 元ファイルは残す

    def test_language_from_metadata(self):
        doc, _, _ = self.make(self.SEGS, sourceLanguage="es")
        self.assertEqual(doc["sourceLanguage"], "es")
        self.assertTrue(any("xml:lang" in w for w in doc["warnings"]))
        vocab = [p for p in doc["segments"][1]["parts"] if p.get("group") == "vocab" and p["type"] == "speech"]
        self.assertEqual(vocab[0]["lang"], "es")

    def test_authored_kept_until_text_changes(self):
        authored = {"2": {"vocab": [{"term": "not much", "meaning": "ほとんどない", "note": ""}], "grammar": ""}}
        doc, j, out = self.make(self.SEGS, authored)
        self.assertEqual(doc["segments"][1]["listening"]["source"], "authored")
        doc, _, _ = self.make(self.SEGS)  # 作者の解説なしで再変換 → 引き継ぐ
        self.assertEqual(doc["segments"][1]["listening"]["vocab"][0]["meaning"], "ほとんどない")
        changed = [dict(self.SEGS[0]), dict(self.SEGS[1], translation="あまりありません。")]
        doc, _, _ = self.make(changed)  # 訳が変わった区間だけ作り直す
        self.assertEqual(doc["segments"][1]["listening"]["source"], "auto")

    def test_drift_warning(self):
        segs = [{"id": i, "begin": "00:00:%02d.000" % (i * 2), "end": "00:00:%02d.500" % (i * 2 + 1),
                 "original": "It took %d days." % (10 + i), "translation": "関係のない訳です。"} for i in range(1, 5)]
        doc, _, _ = self.make(segs)
        self.assertTrue(any("ずれている可能性" in w for w in doc["warnings"]))
        self.assertTrue(convert.numbers_consistent("more than 50,000 people", "5万人以上"))
        self.assertTrue(convert.numbers_consistent("$100 billion", "1000億ドル"))

    def test_merge_fragments(self):
        segs = [{"id": 1, "begin": 1.0, "end": 4.0, "original": "Prices went", "translation": "物価が",
                 "words": [], "grammar": "", "sourceIds": [1]},
                {"id": 2, "begin": 5.0, "end": 9.0, "original": "up today.", "translation": "上がった。",
                 "words": [], "grammar": "", "sourceIds": [2]}]
        merged = convert.merge_fragments(segs, "en")
        self.assertEqual(len(merged), 1)
        self.assertEqual((merged[0]["begin"], merged[0]["end"], merged[0]["sourceIds"]), (1.0, 9.0, [1, 2]))
        self.assertEqual(merged[0]["translation"], "物価が上がった。")


class NamingTest(unittest.TestCase):
    def test_episode_stem(self):
        stem = convert.episode_stem
        self.assertEqual(stem({"episode": "Iran's economy: Counting the cost of war", "publishDate": "2026-01-01"},
                              "/x/[Business Daily] Iran’s economy_ Counting the cost of war (20260101).json"),
                         "Iran's economy - Counting the cost of war (2026-01-01)")
        self.assertEqual(stem({"episode": "Reinventing Kodak", "publishDate": ""},
                              "/x/[Business Daily] Reinventing Kodak (20260414).json"), "Reinventing Kodak (2026-04-14)")
        self.assertEqual(stem({"episode": "A/B? <live>"}, "/x/[Demo] AB (20260102).json"), "A-B live (2026-01-02)")
        self.assertEqual(stem({}, "/x/plain.json"), "plain")


class ApplyFixTest(TempDir):
    def test_apply_fix(self):
        sys.path.insert(0, os.path.dirname(HERE))
        import apply_fix
        j = self.write("[Demo] Ep (20260101).json", episode(ConvertTest.SEGS))
        self.write("[Demo] Ep (20260101).ttml", TTML)
        fix = {"format": "podcast-listening-fix", "formatVersion": 1, "base": {"file": "x", "sha256": convert.sha256_file(j)},
               "segments": {"1#1": {"translation": "物価が上がった。", "vocab": [["went up", "上がった", "句動詞", ""]]},
                            "1#2": {"translation": "人々は心配している。", "vocab": [], "fix": "【訂正】メモ"},
                            "2": {"translation": None, "vocab": [["not much", "ほとんどない", "熟語", ""]], "grammar": "[[not]]で否定。"}}}
        self.write("[Demo] Ep (20260101).fix.json", fix)
        doc = apply_fix.apply(j, j[:-5] + ".fix.json")
        self.assertEqual([s["key"] for s in doc["segments"]], ["1#1", "1#2", "2"])  # TTMLの文境界で分割
        self.assertEqual(doc["segments"][1]["translation"], "人々は心配している。")
        self.assertEqual(doc["segments"][2]["translation"], "大したことはない。")  # null は既存の訳のまま
        self.assertEqual(doc["segments"][2]["listening"]["grammar"], "[[not]]で否定。")
        self.assertIn("originalJson", doc["sources"])
        with open(j[:-5] + ".retranslated.json", encoding="utf-8") as f:
            fixed = json.load(f)
        self.assertEqual(fixed["segments"][0]["grammar"], "【訂正】メモ")
        fix["base"]["sha256"] = "0" * 64
        self.write("[Demo] Ep (20260101).fix.json", fix)
        with self.assertRaises(SystemExit):
            apply_fix.apply(j, j[:-5] + ".fix.json")


class MaterialParityTest(TempDir):
    """アプリ（app/material.js）が既存ファイルから作るデータが、Python の変換と同じであること。"""

    @unittest.skipUnless(shutil.which("node"), "node が必要")
    def test_same_material(self):
        segs = [dict(s) for s in ConvertTest.SEGS]
        segs[1]["words"] = [{"word": "not much", "meaning": "あまりない、大したことはない", "pos": "熟語"},
                            {"word": "much", "meaning": "多く", "pos": "副詞"},
                            {"word": "Tehran", "meaning": "テヘラン", "pos": "固有名詞"}]
        segs[1]["grammar"] = "not much — 否定。「あまり〜ない」（量）を表す。短い答えでよく使う。"
        cases = [(segs, None)]
        fix = {"format": "podcast-listening-fix", "formatVersion": 1, "segments": {
            "1#1": {"translation": "物価が上がった。", "vocab": [["went up", "ここでは、上がった、という意味です", "句動詞", ""]]},
            "1#2": {"translation": "人々は心配している。", "vocab": [], "grammar": "[[worried]]は形容詞です。"},
            "2": {"translation": None, "vocab": [["not much", "ほとんどない", "熟語", "短い答えです。"]],
                  "translationSpeech": "たいしたことはない。"}}}
        cases.append((segs, fix))
        for segments, fx in cases:
            j = self.write("[Demo] Ep (20260101).json", episode(segments))
            t = self.write("[Demo] Ep (20260101).ttml", TTML)
            if fx:
                fx = dict(fx, base={"file": "x", "sha256": convert.sha256_file(j)})
                self.write("[Demo] Ep (20260101).fix.json", fx)
                sys.path.insert(0, os.path.dirname(HERE))
                import apply_fix
                py = apply_fix.apply(j, j[:-5] + ".fix.json")
            else:
                py = convert.convert(j, t, None, None, log=lambda *a: None)
            script = """
import { buildMaterial } from %s;
import fs from "fs";
const [j, t, f] = process.argv.slice(1);
const doc = buildMaterial({ json: JSON.parse(fs.readFileSync(j, "utf8")), ttml: fs.readFileSync(t, "utf8"),
  fix: f ? JSON.parse(fs.readFileSync(f, "utf8")) : undefined, name: j.split("/").pop() });
console.log(JSON.stringify(doc));""" % json.dumps("file://" + os.path.join(APP, "material.js"))
            args = ["node", "--input-type=module", "-e", script, j, t] + ([j[:-5] + ".fix.json"] if fx else [])
            js = json.loads(subprocess.run(args, capture_output=True, text=True, check=True).stdout)
            keys = ("key", "sourceSegmentIds", "begin", "end", "clip", "original", "translation", "wordTimings")
            pick = lambda d: [{**{k: s.get(k) for k in keys},  # noqa: E731
                               "listening": {k: s["listening"][k] for k in ("vocab", "grammar", "estimatedSeconds")},
                               "parts": [{k: p.get(k) for k in ("type", "group", "lang", "text")} for p in s["parts"]]}
                              for s in d["segments"]]
            self.assertEqual(pick(js), pick(py))
            self.assertEqual(js["warnings"], py["warnings"])
            self.assertEqual(len(js["segments"]), 3 if fx else 2)


class SubtitleAlignTest(unittest.TestCase):
    """app/subtitle.js: 原文と単語の時刻を照らし合わせて、単語ごとに時刻を付ける。"""

    @unittest.skipUnless(shutil.which("node"), "node が必要")
    def test_align(self):
        script = """
import { alignSubtitle } from %s;
const cases = JSON.parse(process.argv[1]);
console.log(JSON.stringify(cases.map(([o, t, l]) => alignSubtitle(o, t, l))));""" % json.dumps(
            "file://" + os.path.join(APP, "subtitle.js"))
        cases = [
            ["Today, we're here.", [[1, 1.5, "Today,"], [1.5, 1.8, "we're"], [1.8, 2.1, "here."]], "en"],
            # 原文は音声認識の誤り（是懂非懂）を直してある。直した文字には時刻を付けない
            ["我是Nathan。似懂非懂。", [[0, 0.2, "我"], [0.2, 0.4, "是"], [0.4, 0.8, "Nathan"], [0.8, 1.4, "是懂非懂"]], "zh"],
            ["Completely different words.", [[0, 1, "Hello"], [1, 2, "world"]], "en"],
        ]
        res = json.loads(subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(cases)],
                                        capture_output=True, text=True, check=True).stdout)
        en, zh, other = res
        self.assertEqual([(u["text"], u["b"]) for u in en["units"] if u["text"].strip()],
                         [("Today,", 1), ("we're", 1.5), ("here.", 1.8)])
        self.assertEqual("".join(u["text"] for u in zh["units"]), "我是Nathan。似懂非懂。")  # 原文はそのまま
        self.assertEqual([(u["text"], u["b"]) for u in zh["units"]],
                         [("我", 0), ("是", 0.2), ("Nathan", 0.4), ("。", None), ("似", None), ("懂非懂", 0.8), ("。", None)])
        self.assertIsNone(other)  # 合わない文は強調しない


class StepParityTest(TempDir):
    """書き出し（Python）とアプリ（JavaScript）の再生順が同じであること。"""

    @unittest.skipUnless(shutil.which("node"), "node が必要")
    def test_same_steps(self):
        doc, _, out = ConvertTest.make(self, ConvertTest.SEGS,
                                       {"2": {"vocab": [{"term": "not much", "meaning": "ほとんどない", "note": ""}],
                                              "grammar": "[[not]]で否定しています。"}})
        settings = [dict(export.DEFAULTS),
                    dict(export.DEFAULTS, firstRepeats=3, secondRepeats=2, cues=False, vocab=False, gapSeconds=0),
                    dict(export.DEFAULTS, translation=False, grammar=False, gapSeconds=2.5)]
        script = """
import { buildSteps } from %s;
const doc = JSON.parse(process.argv[1]); const all = JSON.parse(process.argv[2]);
const r = all.map((s) => doc.segments.map((seg) => buildSteps(doc, seg, s).map((x) =>
  x.kind === 'pause' ? ['pause', x.seconds] : ['clip', x.clip, x.rate])));
console.log(JSON.stringify(r));""" % json.dumps("file://" + os.path.join(APP, "engine.js"))
        res = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(doc), json.dumps(settings)],
                             capture_output=True, text=True, check=True)
        js = json.loads(res.stdout)
        py = [[[list(x[:2]) if x[0] == "pause" else list(x) for x in export.build_steps(doc, seg, s)]
               for seg in doc["segments"]] for s in settings]
        # Python の pause は (kind, 秒, 1)、clip は (kind, clip, rate)
        py = [[[["pause", x[1]] if x[0] == "pause" else x for x in seg] for seg in s] for s in py]
        py = [[[x for x in seg if not (x[0] == "pause" and x[1] == 0)] for seg in s] for s in py]
        self.assertEqual(js, py)


class BoundaryTest(unittest.TestCase):
    """app/boundary.js: 文の切れ目を元音声の静かなところに合わせる。"""

    @unittest.skipUnless(shutil.which("node"), "node が必要")
    def test_refine(self):
        script = """
import { refineClips, mp3Frames } from %s;
// 10ms ごとの音量。声 -20dB、静か -70dB、音楽 -30dB
const db = new Float32Array(2000).fill(-20);
const quiet = (a, b) => { for (let i = a * 100; i < b * 100; i++) db[Math.round(i)] = -70; };
quiet(0, 1.0);           // 最初の声は 1.0 秒から
quiet(3.0, 3.3);         // 文1と文2の間（時刻は 3.2 で区切られている）
quiet(6.0, 6.03);        // 時刻の切れ目にある短い間（そのまま使う）
quiet(9.0, 9.4); for (let i = 940; i < 1200; i++) db[i] = -30; // 間の後に音楽、文4は 12.0 から
quiet(15.0, 15.3);       // 時刻から 0.6 秒離れた静かなところ（単語ごとの時刻なら動かさない）
const seg = (b, e) => ({ begin: b, end: e, clip: { begin: b, end: e } });
const make = () => [seg(1.1, 3.2), seg(3.2, 6.0), seg(6.0, 9.0), seg(12.0, 14.4), seg(14.4, 19.0)];
const a = make(); const st = refineClips(a, { rate: 100, db }, 20);
const b = make(); refineClips(b, { rate: 100, db }, 20, { search: 1.0 });
// MP3 でないデータ
const notMp3 = mp3Frames(new Uint8Array(5000));
console.log(JSON.stringify({ a: a.map((s) => s.clip), b: b.map((s) => s.clip), st, notMp3 }));
""" % json.dumps("file://" + os.path.join(HERE, "..", "..", "app", "boundary.js"))
        res = json.loads(subprocess.run(["node", "--input-type=module", "-e", script],
                                        capture_output=True, text=True, check=True).stdout)
        a, b = res["a"], res["b"]
        self.assertAlmostEqual(a[0]["begin"], 0.92)          # 声の始まり（1.0）の少し前から
        self.assertAlmostEqual(a[0]["end"], 3.12)            # 文1は声が消えた 3.0 の少し後まで
        self.assertAlmostEqual(a[1]["begin"], 3.22)          # 文2は声が出る 3.3 の少し前から
        self.assertAlmostEqual(a[1]["end"], 6.015)           # 短い間の中で区切る
        self.assertAlmostEqual(a[2]["begin"], 6.015)
        self.assertAlmostEqual(a[2]["end"], 9.12)            # 文3は声が消えたところまで
        self.assertAlmostEqual(a[3]["begin"], 12.0)          # 音楽の中には広げない（元のまま）
        self.assertAlmostEqual(a[3]["end"], 14.4)            # 近くに静かなところがない（元のまま）
        self.assertAlmostEqual(a[4]["begin"], 14.4)
        self.assertAlmostEqual(b[3]["end"], 15.12)           # 広く探すと（文ごとの時刻）静かなところで区切る
        self.assertAlmostEqual(b[4]["begin"], 15.22)
        self.assertIsNone(res["notMp3"])


class OldSafariSyntaxTest(unittest.TestCase):
    """iOS 16.4 より前の Safari で構文エラーになる書き方を、アプリで使っていないこと。

    1か所でもあるとモジュールの読み込みが失敗し、ファイルを選んでも何も起きなくなる。
    """

    def test_no_regex_lookbehind(self):
        import re
        app = os.path.join(HERE, "..", "..", "app")
        found = []
        for name in sorted(os.listdir(app)):
            if not name.endswith(".js"):
                continue
            with open(os.path.join(app, name), encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    code = line.split("//")[0]
                    if re.search(r"\(\?<[=!]", code):
                        found.append("%s:%d" % (name, n))
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()

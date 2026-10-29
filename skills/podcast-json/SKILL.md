---
name: "podcast-json"
description: "ポッドキャストの一括処理パイプライン。Apple Podcasts から音声+TTMLをインポートし、ライブラリを整理した上で、TTMLを語学学習JSON（日本語訳・語彙・文法注釈・単語ごとの時刻付き）に変換する。JSON は Language Drill と聞き流しアプリの両方で使える。TTML がない回は podcast-transcribe で音声から作る。「ポッドキャストを処理して」「JSONを作って」「TTMLを変換」「続けて」「既存のJSONに単語の時刻を付けて」などで使う。"
---

# ポッドキャスト一括処理スキル（インポート → 整理 → TTML→JSON変換）

ユーザーが「ポッドキャストを処理して」と言うだけで、以下を**一括で**実行する：

1. **インポート** — Apple Podcasts から音声+TTMLを `~/Desktop/Public/Podcasts/` へコピー
2. **整理** — ファイル名の統一・重複退避・**言語別フォルダへの再編**・状態ファイル更新
3. **変換対象の決定** — `conversion_queue.json`（アプリの「変換依頼」ボタンが生成）があれば
   それを優先。なければ未変換TTMLを自動検出
4. **変換** — TTML → 日本語訳・語彙・文法注釈・単語ごとの時刻付きJSON（1回の実行で最大3件）
5. **検証** — `validate_json.py` で訳のずれ・時刻・必須項目を確認（エラー0まで直す）
6. **報告** — 処理結果と残件を報告し、続行を促す

作る JSON は **Language Drill（iPad）と聞き流しアプリ（iPhone）の両方**で使う。
既存の項目（`begin` `end` `original` `translation` `words` `grammar` `pinyin`）の形は変えず、
聞き流しアプリ用の `wordTimings` を足す（知らない項目は Language Drill 側で無視される）。

TTML がない回（`no_transcript`）や、1行ごとの時刻しかない古い Whisper の TTML の回は、
**podcast-transcribe スキル**で音声から単語ごとの時刻付き TTML を作ってから変換する。

全フェーズとも既存ファイルはスキップするので、何度実行しても安全。

---

## 0. パスの解決

```python
import glob
mounts = glob.glob("/sessions/*/mnt/Podcasts")
PODCASTS_DIR = mounts[0] if mounts else None
```

マウントが無い場合は `mcp__cowork__request_cowork_directory` で
`~/Desktop/Public/Podcasts` を要求する。

## 1. ライブラリのフォルダ構成

```
Podcasts/
├── 英語/
│   └── <チャンネル名>/
│       ├── [Channel] Episode Title (YYYYMMDD).mp3
│       ├── [Channel] Episode Title (YYYYMMDD).ttml
│       └── [Channel] Episode Title (YYYYMMDD).json   ← 変換で生成
├── フランス語/ …
├── スペイン語/ …
├── 中国語/ …
├── _duplicates/          ← 重複退避先（削除しない）
├── library_status.json   ← 全エピソードの状態（normalize が生成）
└── conversion_queue.json ← アプリの「変換依頼」が生成（あれば優先処理）
```

---

## 2. Phase 1 — Apple Podcasts からのインポート

`scripts/import_podcasts.py` が Apple Podcasts の内部DB
（`~/Library/Group Containers/243LU875E5.groups.com.apple.podcasts/`）を読み取り専用で参照し、
新しいエピソードの音声+TTMLをコピーする。既存の言語フォルダ配下にチャンネルフォルダが
あればそこへ、なければルート直下へ（次の normalize で言語別へ移動される）。

```bash
cp <skill-path>/scripts/import_podcasts.py .
python3 import_podcasts.py          # インポート実行（JSON出力）
python3 import_podcasts.py --list   # 一覧のみ
python3 import_podcasts.py -q "キーワード"
```

**前提:** ターミナルに**フルディスクアクセス**が必要
（システム設定 → プライバシーとセキュリティ → フルディスクアクセス）。
Cowork のサンドボックスから Group Container に届かない場合はこのフェーズをスキップし、
その旨をユーザーに伝えて Phase 2 へ進む。

## 3. Phase 2 — ライブラリ整理（必ず実行）

```bash
cp <skill-path>/scripts/normalize_library.py .
python3 normalize_library.py --root $PODCASTS_DIR          # dry-run
python3 normalize_library.py --root $PODCASTS_DIR --apply  # 適用
```

このスクリプトは冪等で、以下を行う：

- 切り詰め違いで分裂したチャンネルフォルダの統合
- 旧形式ファイル名 → `[チャンネル] タイトル (YYYYMMDD)` 形式へ統一
- 同一エピソードの重複音声を `_duplicates/` へ退避
- TTML/JSON の言語判定 → チャンネルを言語フォルダ（英語/フランス語/…）へ移動
- `library_status.json` の書き出し（ready / pending / no_transcript / no_audio）

## 4. Phase 3 — 変換対象の決定

```python
import os, json

queue_path = os.path.join(PODCASTS_DIR, "conversion_queue.json")
targets = []
if os.path.exists(queue_path):
    # アプリの「変換依頼」ボタンで作成されたキューを優先
    q = json.load(open(queue_path, encoding="utf-8"))
    for e in q.get("episodes", []):
        p = os.path.join(PODCASTS_DIR, e.get("dir", ""), e["ttml"])
        if os.path.exists(p) and not os.path.exists(p[:-5] + ".json"):
            targets.append(p)

if not targets:
    # フォールバック：未変換TTMLの自動検出
    for root, dirs, files in os.walk(PODCASTS_DIR):
        dirs[:] = [d for d in dirs if not d.startswith(("_", "."))]
        for f in files:
            if f.endswith(".ttml") and not os.path.exists(os.path.join(root, f[:-5] + ".json")):
                targets.append(os.path.join(root, f))

to_process = targets[:3]   # 1回の実行で最大3件（トークン予算のため）
```

**ルール:**
- `.json` の兄弟ファイルがある TTML はスキップ（処理済み）。
- 1回の実行で最大 **3件**。残りはユーザーに続行を促す。
- 変換完了したエピソードは `conversion_queue.json` から取り除き、
  キューが空になったらファイル自体を削除する。

## 5. TTML のパース

**必ず `scripts/parse_ttml.py` を使用する。**

```python
import sys, shutil
shutil.copy("<skill-path>/scripts/parse_ttml.py", ".")
sys.path.insert(0, ".")
from parse_ttml import parse_ttml

lang, segs = parse_ttml("/path/to/episode.ttml")
```

各 Apple Podcasts TTML の `<p>` 要素は複数の文を含むことがある。パーサーは：

1. `<span podcasts:unit="word">` の**単語レベルのタイムスタンプ**を収集
2. 文末記号（`. ! ?`）で**個別の文**に分割（`Mr.` `Dr.` `BBC.` `U.S.` などの略語と
   1文字イニシャルは除外）
3. 各文に独自の `begin`（最初の単語）と `end`（最後の単語）を付与

`<p>` 数よりセグメント数が大幅に増えるのは正常（例: 73 `<p>` → 282文）。
`segs` の各要素のキー: `id`, `begin`, `end`, `original`、
TTML に単語ごとの時刻があるときは `wordTimings`（`[[開始秒, 終了秒, "語"], ...]`）。

**TTML の時刻の細かさを最初に確認する**: `python3 parse_ttml.py x.ttml` の出力の `timing=`。
- `timing=word`（Apple の自動字幕、podcast-transcribe で作ったもの）: そのまま変換する。
- `timing=line`（古い mlx_whisper の TTML。1行に1つの時刻）: 時刻が粗く（1行10秒以上・音楽を含むことがある）、
  `wordTimings` も作れない。ユーザーに podcast-transcribe で作り直すか確認する（作り直さずに変換してもよい）。

**ファイル名パターン:**
```
[Channel Name] Episode Title (YYYYMMDD).ttml
```
パース: `re.match(r"^\[(.+?)\]\s+(.+?)\s+\((\d{8})\)\.ttml$", fname)`
（旧形式名が残っている場合はチャンネル名を親フォルダ名から、日付はファイル更新日時から補う）

## 6. 出力 JSON スキーマ

```json
{
  "metadata": {
    "title": "<channel> - <episode>",
    "channel": "string",
    "episode": "string",
    "publishDate": "YYYY-MM-DD",
    "sourceLanguage": "<xml:lang から取得>",
    "targetLanguage": "ja"
  },
  "segments": [
    {
      "id": 1,
      "begin": "HH:MM:SS.mmm",
      "end":   "HH:MM:SS.mmm",
      "original":    "原文テキスト",
      "translation": "自然な日本語訳",
      "words": [
        {
          "word":    "原語の単語またはフレーズ",
          "reading": "ピンイン（zh ソースのみ、それ以外は省略）",
          "meaning": "日本語の意味",
          "pos":     "品詞（名詞、動詞 など）"
        }
      ],
      "grammar": "主要な文法ポイントの日本語解説、または空文字列",
      "pinyin":  "sourceLanguage が zh の場合のみ存在",
      "wordTimings": [[3.46, 3.82, "Hello"], [3.82, 4.06, "and"]]
    }
  ]
}
```

- `wordTimings`（任意・聞き流しアプリ用）: その文で話された語と時刻（秒、小数3桁）。TTML の単語の時刻をそのまま入れる。
  文の `begin`〜`end` に入る語だけ。TTML に単語ごとの時刻がなければ省く。Language Drill は使わない（無視される）。
- それ以外の項目の名前・形は変えない（Language Drill が読む）。項目を増やすときも既存の項目は消さない。

書き出し: `parse_ttml.write_episode_json(output, path)`（`indent=2` と同じで、`wordTimings` だけ1文1行にまとめる）
出力パス: TTML と同じディレクトリ・同じ basename の `.json`。

## 7. 翻訳・注釈ルール

### 翻訳（`translation`）
- 自然な日本語に意訳する — 逐語訳しない。
- 固有名詞・ブランド名・番組名はそのまま（必要に応じてカタカナ併記）。
- 言語が混在しても全体を日本語に翻訳する。
- スラング・口語・卑俗語も自然に訳し、レジスターを `words` に注記。

### 語彙（`words`）
- 1セグメントあたり **3〜6項目**。
- 対象レベル: 原語の高校〜大学レベル学習者。
- 優先: ニュース特有表現・慣用句・高度な語彙・専門用語・知るべきスラング。
- 5語未満の短文や相槌（"OK", "Yeah"）は空配列 `[]`。
- 中国語ソースのみ全エントリに `"reading"`（声調付きピンイン）。
- **カタカナ読みは追加しない**。`reading` は中国語ピンイン専用。

### 文法（`grammar`）
- 注目すべき文法構造を**1つ**、日本語で解説。単純な文は `""`。

### 聞き流しモード向けの書き方（Language Drill の表示にもそのまま使える）
聞き流しアプリは画面を見ずに聞く（運転中など）。JSON から次のように読み上げるので、それに合う書き方にする:
原文の音声 → `translation` → `words` から2語（「原語 → `meaning` の最初の項目」）→ `grammar` の最初の1文 → 原文の音声。

- **`translation`**: 1文の自然な日本語。数字は原文と同じ算用数字で書く（40日、50%、2017年、1000億ドル）。
  算用数字が訳にないと「訳がずれている」と判定される（validate_json.py・アプリとも）。
- **`words` の並び**: 聞いて覚えてほしい順に並べる（熟語・句動詞・その文の要の語を先に）。読み上げは上位2語だけ。
- **`meaning`**: 最初の項目を**この文での意味**にする。言い換えは「、」で後ろに続ける（読み上げは最初の項目だけ）。
  「ここでは」「〜という意味です」「この文では」などの決まり文句は書かず、意味の語句だけを書く（例: `真剣に` ○ / `ここでは、真剣に、という意味です` ×）。
  記号（→ ／ 〜 ＝ 《》）や括弧の補足、品詞名は `meaning` に入れない（`pos` に書く）。
- **`grammar`**: 1行目を、読み上げて分かる**1文（60字程度まで）**にする。
  「A — B」「→」「/」「( )」などの記号に頼らず、文で書く
  （悪い例: `how the war is impacting ... — 間接疑問文。` 良い例: `how から後ろが「どのように影響しているか」という意味のまとまりで、looking at の目的語になっています。`）。
  2行目以降は補足として画面にだけ出る（読み上げない）。
  **中国語の回**は、中国語の語句・文型を原文と同じ字で「」に入れる（例: `「原来…才是…」は「実は〜こそが…だった」という気づきを表す。`）。
  日本語の訳・説明は仮名を含む形で書き（「受身」のような漢字だけの日本語を「」に入れない）、中国語のすぐ後ろに読み（ピンイン）を書かない。
  聞き流しアプリは「」の中の中国語を中国語の声で読む。英語・スペイン語・フランス語の回は原語をそのまま書けばよい。
- **`【訂正】` メモは別の行**に書く（文法の説明と同じ行に書かない）。読み上げでは `【訂正】` の行を飛ばす。

### 音声認識（Whisper）の誤りの訂正（中国語を中心に、全言語共通）
Whisper で作った TTML には誤認識が残る（特に台湾華語・中英混じりの会話、固有名詞）。
翻訳しながら原文を読み、**文脈上明らかな誤認識**を見つけたら次のようにする：

1. **`original` を正しい表記に直す**（例: 是懂非懂 → 似懂非懂、冯济 → 冯骥、tattoo → taboo）。
   - 自信がない箇所（聞き取りの推測が必要なもの）は直さず、`grammar` に「聞き取り不確か」と書く。
   - 句読点の半角→全角（`,`→`，` `?`→`？`）も直してよい。
   - 文単位にまとめる場合は、TTML 側は直さず JSON の `original` だけを直してよい。
2. **`grammar` に `【訂正】音声認識では「誤」となっていたのを訂正。` を追記**する
   （既存の文法メモがあれば後ろに続ける）。
3. `translation`・`words`・`pinyin` は**訂正後の文**に基づいて作る。
4. **同じ TTML の該当箇所も同じように直す**（`<span>` のテキストだけを置換し、属性・時刻は変えない。
   セグメント数が変わる変更はしない）。
5. 今後も起きそうな誤り（固有名詞・番組の決まり文句など）は
   `~/Desktop/PodcastApp/scripts/asr_fixes.json` の該当言語に追加する。
   - 部分一致で別の語を壊す恐れがある場合は `"re:"` で始まる正規表現キーにする
     （例: `"re:普[里利理](?!斯)": "埔里"`、`"re:刘大导(?!演)": "刘大导演"`）。
   - 追加後、既存の他エピソードに同じ誤りが無いか検索し、あれば同様に訂正する。

### 文単位へのまとめ（Whisper で作った TTML、特に中国語）
Whisper の TTML は 1 行が短く（約25字・8秒目安）、1 つの文が 2〜数行にまたがる。
**JSON では「1 セグメント＝1 文」にまとめる**（iPad アプリは長い文も折り返し・スクロールで表示できる）。

1. 連続する行のうち、1 つの文（「因为…所以…」「虽然…但是…」「如果…就…」「不是…而是…」など
   行をまたぐ構文を含む）を構成する行を 1 セグメントに結合する。
   - `begin` は先頭行の開始、`end` は最終行の終了。
   - `original` は各行を「，」でつなぎ、本当の文末は「。」（疑問は「？」）にする。
   - 1 セグメントの上限は約 150 字。超える場合は区切り、続きがある側の行末は「，」のままにする。
   - 明らかな言い直し・重複（同じ語句を 2 回言い直した断片）は捨ててよい。
2. `translation` は**文全体の自然な日本語**で書く（行ごとの訳をつなげない）。
   まとめたセグメントの `wordTimings` は、JSON を書いた後に `add_word_timings.py` を実行すれば時刻の範囲から付く。
3. `grammar` は行をまたぐ構文を優先して解説する（これがまとめる最大の目的）。
4. `words` は文全体から 3〜8 項目。
5. JSON のセグメント数は TTML の `<p>` 数と一致しなくてよい（TTML は時刻の素材、JSON が表示単位）。
6. Apple の自動字幕 TTML（英・仏・西など）は文単位なので、このまとめは不要。

## 8. 長いエピソードの処理（> 約200セグメント）

翻訳データは複数の Python ファイルに分割する。

### ⚠️ 最重要: セグメントIDの整合性

**最も多い重大エラーは翻訳のズレ**（translation が segment ID に対して1つ以上シフト）。

防止ルール:
1. **各エントリに原文をコメントで併記**して目視確認できるようにする：
   ```python
   99: {  # "But of course, this has got opposition leaders..."
       "t": "しかし、もちろん、これは野党の指導者たちに…",
       "w": [...], "g": ""},
   ```
2. **1セグメント = 1エントリ**。分割も結合もしない。
3. **エントリ数を検証**: `assert len(SEGS) == expected_count`

### データファイルのテンプレート

```python
# data_1.py  — segments 1–200
SEGS = {
    1: {  # "Original text here..."
        "t": "日本語訳", "w": [{"word": "...", "meaning": "...", "pos": "..."}], "g": "文法メモ"},
    2: {  # "Next original text..."
        "t": "...",      "w": [],  "g": ""},
}
```

ジェネレーター側で結合:
```python
from data_1 import SEGS as S1
from data_2 import SEGS as S2
SEGS = {**S1, **S2}
```

分割サイズは 200 セグメント/ファイルが目安。

## 9. ジェネレータースクリプトのパターン

```python
import json, re, os
from parse_ttml import parse_ttml, write_episode_json
# ... データファイルの import

TTML_PATH = "..."
lang, segs = parse_ttml(TTML_PATH)

output_segs = []
for seg in segs:
    d = SEGS.get(seg["id"], {})
    words_out = []
    for wd in d.get("w", []):
        entry = {"word": wd["word"], "meaning": wd["meaning"], "pos": wd["pos"]}
        if "reading" in wd:
            entry["reading"] = wd["reading"]
        words_out.append(entry)
    obj = {
        "id": seg["id"], "begin": seg["begin"], "end": seg["end"],
        "original": seg["original"],
        "translation": d.get("t", ""),
        "words": words_out,
        "grammar": d.get("g", ""),
    }
    if lang == "zh":
        obj["pinyin"] = d.get("pinyin", "")
    if "wordTimings" in seg:
        obj["wordTimings"] = seg["wordTimings"]   # 聞き流しアプリ用（単語ごとの時刻）
    output_segs.append(obj)

fname = os.path.basename(TTML_PATH)
m = re.match(r"^\[(.+?)\]\s+(.+?)\s+\((\d{8})\)\.ttml$", fname)
if m:
    channel, episode, raw = m.group(1), m.group(2), m.group(3)
    pub_date = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
else:
    channel = os.path.basename(os.path.dirname(TTML_PATH))
    episode = fname[:-5]
    pub_date = ""

output = {
    "metadata": {
        "title": f"{channel} - {episode}", "channel": channel, "episode": episode,
        "publishDate": pub_date, "sourceLanguage": lang, "targetLanguage": "ja",
    },
    "segments": output_segs,
}
out_path = TTML_PATH[:-5] + ".json"
write_episode_json(output, out_path)
print(f"✓ {len(output_segs)} segments → {out_path}")
```

## 10. 変換後の後処理（毎回必ず）

1. **自動検証**: `python3 validate_json.py "<...>.json" "<...>.ttml"` を実行し、**ERROR が0になるまで直す**。
   - 訳のずれ（原文の数字が多くの訳に見当たらない＝1つずれている）、時刻の範囲で話された語と original の不一致、
     id・時刻・必須項目の形を確認する。
   - 文単位にまとめたセグメントは `add_word_timings.py "<...>.json"` で `wordTimings` を付けてから検証する。
   - WARN（聞き流しモードで読みにくい書き方など）は簡単に直せるものは直す。
2. **目視の整合性検証**: 中盤・終盤から最低5セグメントを抜き取り、
   original と translation の内容一致・オフバイワンなし・空訳ゼロを確認。
   文単位にまとめた場合は、時刻が単調増加し、TTML の全行がどれかのセグメントに
   含まれている（または意図的に捨てた重複のみ）ことを確認。
3. **キュー更新**: 変換済みエントリを `conversion_queue.json` から削除。
   空になったらファイルを削除。
4. **誤認識の訂正確認**: 7章「音声認識の誤りの訂正」で直した箇所を報告用に控え、
   asr_fixes.json に追加した項目があれば一緒に報告する。
5. **状態更新**: `python3 normalize_library.py --root $PODCASTS_DIR --apply` を再実行して
   `library_status.json` を最新化（アプリの表示に反映される）。
6. **報告**: 処理済みファイル・セグメント数・検証結果・スキップ数・残件数を報告。
   残件があれば「続けて」と言えば続行できることを伝える。

## 11. 既存の JSON に単語ごとの時刻を付ける（聞き流しアプリ用）

以前に作った JSON は `wordTimings` がない。訳などはそのままで時刻だけ足せる:

```bash
cp <skill-path>/scripts/add_word_timings.py <skill-path>/scripts/parse_ttml.py .
python3 add_word_timings.py "<...>.json"            # 同じ名前の .ttml を使う
python3 add_word_timings.py "<...>.json" --dry-run  # 書き換えずに結果だけ見る
```

- `wordTimings` 以外の項目は一切変えない（Language Drill はそのまま使える）。
- TTML が1行ごとの時刻だけ（古い Whisper）のときは付けられない。podcast-transcribe で TTML を作り直す。
- 「時刻の範囲の語と original が合わない区間」が出たら、その JSON の時刻か原文がずれている。報告して直し方を相談する。
- 付けた後に `validate_json.py` を実行する。

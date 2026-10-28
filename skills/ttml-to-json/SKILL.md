---
name: ttml-to-json
description: >
  TTMLファイルをパースして PodcastPlayer 用の JSON ファイルを生成する。
  ファイルの移動・インポートは行わない — 変換処理のみ。
  Use this skill whenever the user says anything like
  "TTMLからJSONを作って", "TTMLを変換して", "JSONを生成して",
  "ttml to json", "convert ttml", "ポッドキャストのJSONを作って（TTMLはある）",
  or asks to process/convert already-present TTML transcript files.
  Do NOT use for importing from Apple Podcasts or moving/copying audio files
  — those tasks belong to the podcast-json skill.
---

# TTML → JSON 変換スキル

TTMLトランスクリプトファイルをパースし、日本語訳・語彙注釈・文法メモを付与した
PodcastPlayer 用 JSON ファイルを生成する。ファイルの移動やインポートは行わない。

---

## 1. 対象ファイルの発見

```python
import os, re

PODCASTS_DIR = "/sessions/<session-id>/mnt/Podcasts"
# Cowork マウントパスは実行時に解決する（下記「パスの解決」参照）

unprocessed = []
for root, dirs, files in os.walk(PODCASTS_DIR):
    for f in files:
        if f.endswith(".ttml"):
            json_path = os.path.join(root, f[:-5] + ".json")
            if not os.path.exists(json_path):
                unprocessed.append(os.path.join(root, f))

# 1回の実行で最大3件まで処理する
to_process = unprocessed[:3]
```

**ルール：**
- `.json` の兄弟ファイルがすでに存在する TTML はスキップする（処理済み）。
- 1回の実行で処理するのは **最大3件**。残りがあればユーザーに続行を促す。

---

## 2. TTML のパース

**必ず `parse_ttml.py` を使用すること。**  
スキルの `scripts/parse_ttml.py` を作業ディレクトリにコピーしてからインポートする：

```python
import sys, shutil
shutil.copy("/mnt/skills/user/podcast-json/scripts/parse_ttml.py", ".")
sys.path.insert(0, ".")
from parse_ttml import parse_ttml

lang, segs = parse_ttml("/path/to/episode.ttml")
```

### parse_ttml.py の動作

各 Apple Podcasts TTML の `<p>` 要素は複数の文を含む場合がある。パーサーは：

1. `<span podcasts:unit="word">` の **単語レベルのタイムスタンプ**（`begin`/`end`）を収集。
2. 文末記号（`. ! ?`）で **個別の文** に分割（`Mr.` `Dr.` `BBC.` `U.S.` などの略語と
   1文字イニシャルは除外）。
3. 各文に独自の `begin`（最初の単語のタイムスタンプ）と `end`（最後の単語のタイムスタンプ）
   を付与。

`<p>` 要素の数よりセグメント数が大幅に増えるのは正常な動作。

```
# 例：Six O'Clock News
# 生の <p> 要素：73
# 文分割後のセグメント：282
```

返される `segs` リストの各要素のキー：`id`, `begin`, `end`, `original`

**ファイル名パターン** — TTMLファイル名は以下の形式に従う：
```
[Channel Name] Episode Title (YYYYMMDD).ttml
```
パース：`re.match(r"^\[(.+?)\]\s+(.+?)\s+\((\d{8})\)\.ttml$", fname)`

---

## 3. 出力 JSON スキーマ

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
          "reading": "ローマ字（zh ソースのみ、それ以外は省略）",
          "meaning": "日本語の意味",
          "pos":     "品詞（名詞、動詞 など）"
        }
      ],
      "grammar": "主要な文法ポイントの日本語解説、または空文字列",
      "pinyin":  "sourceLanguage が zh の場合のみ存在"
    }
  ]
}
```

書き出し：`json.dump(output, f, ensure_ascii=False, indent=2)`  
出力パス：TTML ファイルと同じディレクトリ、拡張子 `.json`

---

## 4. 翻訳・注釈ルール

### 翻訳（`translation` フィールド）
- 自然な日本語に意訳する — 逐語訳しない。
- 固有名詞・ブランド名・番組名はそのまま残す（必要に応じてカタカナを追加）。
- ポッドキャストで言語が混在する場合（例：フランス語話者が英語に切り替え）も
  全体を日本語に翻訳する。
- スラング・口語・卑俗語：自然に翻訳し、レジスターを `words` に注記する。

### 語彙（`words` 配列）
- **1セグメントあたり3〜6項目**を選ぶ。
- 対象レベル：原語の **高校〜大学レベル** 学習者。
- 優先項目：ニュース特有の表現・慣用句・高度な語彙・専門用語・知っておくべきスラング。
- 5語未満の短いセグメントや純粋な相槌（"OK", "Yeah, yeah", "Uh-huh"）は
  **空配列 `[]`** を使用。
- 中国語ソースのみ：すべての単語エントリに `"reading"` (声調付きピンイン) を追加。
- **カタカナ読みは追加しない**。`reading` フィールドは中国語ピンイン専用。

### 文法（`grammar` フィールド）
- セグメント内の **注目すべき文法構造を1つ**、日本語で説明する。
- 極めてシンプルなセグメントは空文字列 `""` を使用。

---

## 5. 長いエピソードの処理（セグメント数 > 約200）

文分割によりセグメント数が大幅に増えるため（591 `<p>` のエピソードが
1200以上の文に展開されることもある）、翻訳データは複数の Python ファイルに分割する。

### ⚠️ 重要：セグメントID の整合性

**最も多く深刻なエラーは翻訳のずれ** — 翻訳がセグメントIDからずれてしまうこと。
原因：
- 長い原文の翻訳を誤って2つの SEGS エントリにまたがって分割
- どのセグメント ID を処理しているかを見失う
- 各 ID の原文を確認せずに順番に翻訳

**防止ルール：**

1. **各エントリの隣に原文をコメントとして記載** し、目視で整合性を確認できるようにする：
   ```python
   99: {  # "But of course, this has got opposition leaders..."
       "t": "しかし、もちろん、これは野党の指導者たちに…",
       "w": [...], "g": ""},
   100: {  # "I mean, isn't it possible that prime ministers..."
       "t": "つまり、首相が…知らないことはあり得ますか。",
       "w": [...], "g": ""},
   ```
2. **1セグメント = 1翻訳エントリ。** 1つのセグメントの翻訳を2つのエントリに分割したり、
   2つのセグメントを1つのエントリにまとめたりしない。
3. **エントリ数を確認**：想定セグメント範囲と一致することを検証する：
   `assert len(SEGS) == expected_count`

### データファイルのテンプレート

```python
# data_1.py  — セグメント 1〜200
SEGS = {
    1: {  # "Original text here for alignment check..."
        "t": "日本語訳", "w": [{"word": "...", "meaning": "...", "pos": "..."}], "g": "文法メモ"},
    2: {  # "Next original text..."
        "t": "...",      "w": [],                                                 "g": ""},
    ...
    200: {...}
}
```

ジェネレータースクリプトで結合：
```python
from data_1 import SEGS as S1
from data_2 import SEGS as S2
from data_3 import SEGS as S3
SEGS = {**S1, **S2, **S3}
```

1ファイルあたり200セグメントが適切な分割サイズ。

---

## 6. ジェネレータースクリプトのパターン

```python
import json, re, os, sys, shutil

# parse_ttml.py をコピーして使用
shutil.copy("/mnt/skills/user/podcast-json/scripts/parse_ttml.py", ".")
sys.path.insert(0, ".")
from parse_ttml import parse_ttml

# 翻訳データをインポート（分割している場合）
from data_1 import SEGS as S1
# from data_2 import SEGS as S2
# SEGS = {**S1, **S2}
SEGS = S1

TTML_PATH = "/path/to/episode.ttml"
lang, segs = parse_ttml(TTML_PATH)

# パース済みセグメントと翻訳データを結合
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
        "id":          seg["id"],
        "begin":       seg["begin"],
        "end":         seg["end"],
        "original":    seg["original"],
        "translation": d.get("t", ""),
        "words":       words_out,
        "grammar":     d.get("g", "")
    }
    if lang == "zh":
        obj["pinyin"] = d.get("pinyin", "")
    output_segs.append(obj)

# メタデータを構築して出力
fname = os.path.basename(TTML_PATH)
m = re.match(r"^\[(.+?)\]\s+(.+?)\s+\((\d{8})\)\.ttml$", fname)
channel, episode, raw_date = m.group(1), m.group(2), m.group(3)
pub_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"

output = {
    "metadata": {
        "title": f"{channel} - {episode}",
        "channel": channel,
        "episode": episode,
        "publishDate": pub_date,
        "sourceLanguage": lang,
        "targetLanguage": "ja"
    },
    "segments": output_segs
}

out_path = TTML_PATH[:-5] + ".json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"✓ {len(output_segs)} セグメント → {out_path}")
```

---

## 7. ワークフロー（変換のみ）

1. **発見**：処理対象の TTML ファイルをスキャン（`.json` の兄弟ファイルがないもの）。
2. **パース**：各エピソードに `scripts/parse_ttml.py` を使って
   正確なタイムスタンプ付きの文レベルセグメントを抽出（最大3件/回）。
3. **翻訳**：翻訳データ（`SEGS` 辞書）を記述 — 200セグメント超は複数ファイルに分割。
4. **生成**：ジェネレータースクリプトを書いて実行し、パース済みセグメントと翻訳を結合。
5. **整合性確認**：各セグメントの `original` と `translation` を比較して
   ずれがないか確認。中間・末尾の最低5セグメントをスポットチェック：
   - 翻訳の内容が原文と一致しているか
   - 1つ以上のずれ（よくある失敗パターン）がないか
   - 総セグメント数が TTML パース数と一致しているか
   - 空の翻訳が0件であること
6. **報告**：処理済みファイル・セグメント数・スキップ数・エラーを一覧表示。
   未処理の TTML が残っている場合はユーザーに続行を促す。

---

## 8. Cowork でのパス解決

セッションの作業ディレクトリはセッションごとに変わる。実行時に正しいマウントパスを導出する：

```python
import glob
mounts = glob.glob("/sessions/*/mnt/Podcasts")
PODCASTS_DIR = mounts[0] if mounts else None
```

# 聞き流し学習モード（Listening Mode）

ポッドキャスト教材を、画面を見ずに耳だけで学ぶためのモードです。運転中の利用を想定しています。
各文について次の順に自動で再生し、最後の文まで止まらずに進みます。

1. 元のポッドキャスト音声の該当区間（合成音声では代用しません）
2. 日本語訳の読み上げ
3. 重要な語彙・表現（最大2つ）と、文法・背景の短い説明
4. 同じ元音声の区間
5. 次の文へ

```
tools/   教材の準備（Mac で実行。標準ライブラリの Python と ffmpeg だけを使う）
app/     iPhone 向けの再生アプリ（PWA。ホーム画面に追加して使う）
```

外部の有料サービスや認証は使いません。音声合成は macOS 標準の `say`（無料・オフライン）です。

## 使い方

### 1. 教材を準備する（Mac、教材ごとに1回）

ffmpeg が必要です（`brew install ffmpeg`）。Drive の教材フォルダ（同名の `.json`・`.ttml`・`.mp3`）で実行します。

```bash
python3 tools/prepare_listening.py "英語/Business Daily/[Business Daily] Reinventing Kodak (20260414).json"
```

同じフォルダに次の2つができます。元ファイルは変更しません。

- `元の名前.listening.json` — 聞き流し専用のデータ（下記）
- `元の名前.listening.m4a` — 再生用音声。元音声の各区間と、訳・解説の合成音声を1本にまとめたもの

2回目以降は、合成音声をキャッシュ（`.listening_cache/`）から使い回し、文章が変わった部分だけ作り直します。
回数・速度・無音・オン/オフはアプリ側で変えるので、作り直しは不要です。

既に `.listening.json` がある教材は、音声だけを作ります。

```bash
python3 tools/render_listening_audio.py "…/元の名前.listening.json"
```

**訳の訂正データ（`元の名前.fix.json`）がある教材**：既存JSONの訳が音声とずれていた回のために、訂正データを用意しています。
訂正データを当てて `.listening.json` を作ってから、音声を作ります。元のJSONは変更しません。

```bash
python3 tools/apply_fix.py "英語/Business Daily/[Business Daily] Iran’s economy_ Counting the cost of war (20260101).json"
```

```bash
python3 tools/render_listening_audio.py "英語/Business Daily/[Business Daily] Iran’s economy_ Counting the cost of war (20260101).listening.json"
```

声を変えるときは `--voice ja=Otoya --voice en=Daniel` を付けます。
Mac 以外では `--engine openjtalk`（`pip install pyopenjtalk-plus` と espeak-ng が必要）も使えます。

### 2. iPhone に入れる（出発前）

1. Safari でアプリを開き、共有メニューの「ホーム画面に追加」を選ぶ。
2. **Add material** を押し、ファイルアプリ（Google Drive）から `.listening.json` と `.listening.m4a` を**一緒に**選ぶ。
   教材は端末内に保存されるので、運転中は通信なしで再生できます。
3. 設定を選んで **Start**（前回の続きがあれば **Resume**）を押す。

`.listening.m4a` がなくても、`.listening.json` と元の `.mp3` だけで「basic mode」として使えます。
この場合、訳と解説は端末の音声合成で読み上げます。画面がついている間の利用向けです。

### 3. 運転中の操作

| 操作 | 画面のジェスチャー | 車・ロック画面・イヤホン |
| --- | --- | --- |
| 一時停止 / 再開 | タップ | 再生 / 一時停止 |
| 次の文 | 左にスワイプ | 次へ |
| 前の文 | 右にスワイプ | 前へ（設定で「今の文を最初から」にもできる） |
| 今の文を最初から | 長押し | — |

画面には、今聞いている原文を字幕で表示します。元音声の再生中は、今読まれている単語が強調されます。
訳や解説の読み上げ中は、その日本語を原文の下に表示します。

**教材ごとの再生履歴**：文を移るたび、また一時停止・中断・アプリを閉じたときに、教材ごとの位置を保存します。
一覧には各教材の前回の位置が出ます。**Resume** でその文から、**Start over** で最初から再生します。
通話などで再生が止められたときは、その文の位置を保ったまま止まります。

### 設定（Settings）

| 項目 | 範囲 | 初期値 |
| --- | --- | --- |
| 最初の元音声の回数 / 解説後の元音声の回数 | 各1〜5回 | 各1回 |
| 元音声の速度（声の高さを保つ） | 0.5〜1.5倍 | 0.9倍 |
| 訳の読み上げ速度 / 解説の読み上げ速度 | 各0.5〜2倍 | 1倍 |
| 文と文の間の無音 | 0〜3秒 | 0.7秒 |
| 日本語訳・語彙・文法と背景・案内（「訳」「表現」「もう一度」）・エピソードのリピート | オン/オフ | リピートのみオフ |

### 最終手段：学習用音声の書き出し

ロック中にアプリが止まる端末では、設定を焼き込んだ1本の音声（文ごとに章付き）を書き出して、
普通の音楽・ポッドキャストアプリで聞けます。アプリの **Export settings** で保存した設定も使えます。

```bash
python3 tools/export_learning_track.py "…/元の名前.listening.json" --settings listening-settings.json
python3 tools/export_learning_track.py "…/元の名前.listening.json" --first 2 --speed 0.8 --gap 1.5 --no-grammar
```

速度ごとの音声をキャッシュするので、回数・無音・オン/オフを変えた書き出しは数秒で終わります。

## アプリの置き場所

アプリは静的ファイルだけでできていて、Vercel の `podcastdrive-app` プロジェクト（Root Directory: `app`）で公開しています。

- URL: https://podcastdrive-app.vercel.app
- `main` に変更を入れると、自動で再デプロイされます。

公開されるのはアプリ本体だけで、教材は含みません。教材は各端末に取り込みます。

## 専用JSON（`元の名前.listening.json`）

| キー | 内容 |
| --- | --- |
| `format`, `formatVersion` | `"podcast-listening"`, `1` |
| `materialId` | 教材ID（番組・日付・元ファイル名から作る） |
| `sourceLanguage`, `targetLanguage` | 既存JSONの `metadata` に従う（英語固定ではない） |
| `sources` | 元ファイル（JSON・TTML・音声）の名前と SHA-256。訂正版から作ったときは `originalJson` も記録 |
| `cues` | 案内（訳・表現・解説・もう一度）の文と音声クリップID |
| `segments[]` | 区間ごとのデータ（下記） |
| `audio` | 再生用音声のファイル名・SHA-256、各クリップの位置（`clips`）、使った声 |
| `warnings` | 変換時の注意（訳のずれの疑いなど） |

`segments[]` の各要素：

- `id`, `key`, `sourceSegmentIds`, `sourcePart` — 区間IDと元のJSONとの対応
- `begin`, `end`（秒）, `clip`（元音声を切り出す範囲）, `alignment`（TTMLとの照合結果）, `wordTimings`（字幕用の単語時刻）
- `original`, `translation` — 原文と日本語訳（そのまま）
- `display` — 表示用の語彙・文法（既存JSONのまま）
- `listening` — 聞くための短い解説（語彙は最大2つ。`term`・この文での意味・補足と、文法・背景1文）。
  `source` は作者が書いたもの（`authored`）か、規則で作ったもの（`auto`）か
- `parts` — 読み上げ順のパーツ。各パーツに言語（`lang`）と音声クリップID（`clip`）がある

解説の文中の `[[...]]` は教材の言語の声で読みます（例：`[[how]]で始まる部分が…`）。

### 変換の規則

- **時刻**：JSON の `begin`/`end` を、TTML の単語の境界が 60ms 以内にあるときだけそれに合わせます。
  時刻を推測で作ることはしません。
- **文の区切り**：1区間に複数の文があり、文ごとの訳（`translationParts`）と TTML の文の時刻が両方そろうときだけ分割します。
  文の途中で切れた区間は、次の区間とまとめます。どちらにも当てはまらなければ既存の区間をそのまま使います。
- **聞くための解説**：教材の準備時に一度だけ作って保存します。再生時に AI は呼びません。
  記号・括弧・スラッシュ・品詞ラベル・【訂正】メモは読み上げません。解説は通常速度で20秒以内に収めます。
- **訳のずれの検出**：原文の数字が訳に見当たらない区間が多いと警告を出します。

## テスト

```bash
python3 -m unittest discover -s tools/tests   # 変換・読み上げテキスト・アプリと書き出しの再生順の一致
tools/tests/run_e2e.sh                        # 架空の教材を作り、Chromium でアプリを操作して確認
```

# 聞き流し学習モード（Listening Mode）

ポッドキャスト教材を、画面を見ずに耳だけで学ぶためのモードです。運転中の利用を想定しています。
各文について次の順に自動で再生し、最後の文まで止まらずに進みます。

1. 元のポッドキャスト音声の該当区間（合成音声では代用しません）
2. 日本語訳の読み上げ
3. 重要な語彙・表現（最大2つ）と、文法・背景の短い説明
4. 同じ元音声の区間
5. 次の文へ

```
app/     iPhone 向けの再生アプリ（PWA。ホーム画面に追加して使う）
tools/   必要なときだけ使う Mac 用の道具（ロック中向けの音声の作成、学習用音声の書き出し）
```

**準備は要りません。** 今ある音声（`.mp3`）・JSON・原稿（`.ttml`）をそのままアプリに取り込みます。
訳・語彙・文法の説明は、iPhone の音声で読み上げます（説明がない文は自然に省きます）。
外部の有料サービスや認証は使いません。

## 使い方

### 1. iPhone に教材を入れる（出発前）

1. Safari で https://podcastdrive-app.vercel.app を開き、共有メニューの「ホーム画面に追加」を選ぶ。
2. **Add material** を押し、ファイルアプリ（Google Drive）の番組フォルダから、
   聴きたい回の **`.mp3`・`.json`・`.ttml` を一緒に**選ぶ。
   - 何回分かをまとめて選べます。同じ名前のファイルが1つの教材になります。
   - `.ttml` があると、時刻が単語単位で正確になり、字幕で今の単語が強調されます（なくても再生できます）。
   - 訳の訂正データ（`.fix.json`）がある回は、それも一緒に選びます（例：Iran's economy の回）。
     既存JSONの訳が音声とずれている回は、取り込み時に警告が出ます。
3. 取り込んだ教材は端末内に保存されるので、運転中は通信なしで再生できます。
4. 設定を選んで **Start**（前回の続きがあれば **Resume**）を押す。

**読み上げの声**：iPhone の 設定 → アクセシビリティ → 読み上げコンテンツ → 声 から、
日本語「Kyoko（拡張）」、英語「Samantha（拡張）」などをダウンロードしておくと、自動でその声を使います。

**画面ロックについて**：iPhone の音声読み上げは、画面がロックされると止まることがあります。
アプリは再生中に画面を消さないようにしています（設定 **Keep screen on while playing**、初期値オン）。
車ではホルダーに置いたまま、画面を点けておく使い方を想定しています。

### 2. 運転中の操作

| 操作 | 画面のジェスチャー | 車・ロック画面・イヤホン |
| --- | --- | --- |
| 一時停止 / 再開 | タップ | 再生 / 一時停止 |
| 次へ（原文 → 訳 → 語彙 → 解説 → 次の文） | 左にスワイプ | 次の文 |
| 1つ戻る（解説 → 語彙 → 訳 → 原文 → 前の文の解説） | 右にスワイプ | 前の文（設定で「今の文を最初から」にもできる） |
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
| 再生中は画面を消さない | オン/オフ | オン |

## 必要なときだけ：Mac で作る音声（`tools/`）

画面をロックしたまま聞きたいときは、Mac で訳・解説を事前に音声にしておく方法もあります
（macOS 標準の `say` を使用。無料・オフライン）。ffmpeg が必要です（`brew install ffmpeg`）。

```bash
python3 tools/prepare_listening.py "英語/Business Daily/[Business Daily] Reinventing Kodak (20260414).json"
```

同じフォルダに `エピソード名 (日付).listening.json` と `エピソード名 (日付).listening.m4a` ができます。
この2つを一緒に **Add material** で選ぶと、元音声と読み上げを1本の音声として再生します。
訂正データがある回は `python3 tools/apply_fix.py "…/元の名前.json"` のあと
`python3 tools/render_listening_audio.py "…/エピソード名 (日付).listening.json"` を実行します。

設定を焼き込んだ1本の音声（文ごとに章付き）を書き出して、普通の音楽アプリで聞くこともできます。

```bash
python3 tools/export_learning_track.py "…/エピソード名 (日付).listening.json" --first 2 --speed 0.8 --gap 1.5 --no-grammar
```

## JSON を作るスキル（`skills/`）

Claude のスキルで、Language Drill とこのアプリの両方で使える JSON を作ります。

| スキル | 使うとき |
|---|---|
| `podcast-json` | Apple Podcasts から取り込み → 整理 → TTML から JSON（一括） |
| `ttml-to-json` | TTML から JSON（変換だけ）。既存の JSON に単語の時刻だけを足すことも |
| `podcast-transcribe` | TTML がない回：音声から単語ごとの時刻付き TTML を作り、JSON まで作る |

- JSON の既存の項目（`begin` `end` `original` `translation` `words` `grammar` `pinyin`）は変えず、
  各文に `wordTimings`（`[[開始秒, 終了秒, "語"], ...]`）を足します。Language Drill は知らない項目を使いません。
- 各文の `speak` に、聞き流しアプリが読み上げる語彙（`[原語, 日本語の意味]` を2つまで）と文法（`[言語, 文]` の並び）を書きます。
  アプリは `speak` に書かれた言語の声で読み、推測しません（`speak` がない古い JSON だけ、文字の種類と原文から言語を推測します）。
  `validate_json.py` は、語彙か文法がある文に `speak` がないとエラーにします。
- 共通スクリプト（`podcast-json/scripts/`）: `parse_ttml.py`（文ごとに分け、単語の時刻も返す）、
  `add_word_timings.py`（既存の JSON に単語の時刻だけを足す）、`validate_json.py`（訳のずれ・時刻・形の確認）。
- `podcast-transcribe/scripts/transcribe.py`: Whisper large-v3（Mac では mlx-whisper、ほかは faster-whisper）で
  単語の時刻付きの Apple 形式 TTML を作ります。
- 直したら `python3 skills/build.py` で共通スクリプトを各スキルへ写し、`skills/dist/*.skill` を作ります（claude.ai に入れるファイル）。
- 確認: `python3 -m unittest skills/tests/test_skills.py`

## Mac で ChatGPT 用に集める（`mac/`）

`PodcastsChatGPTに集める.command`（`python3 mac/build_command.py` で作る）をデスクトップに置いてダブルクリックすると、
Apple Podcasts で、実行した日（今日）にダウンロード・保存された音声と字幕（TTML）だけを
Dropbox の `PodcastsChatGPT` フォルダ（`~/Library/CloudStorage/Dropbox/PodcastsChatGPT`）にコピーします（元のファイルはそのまま）。
アップロードは Mac の Dropbox アプリがするので、別の場所の Windows パソコンでも Dropbox から受け取れます。
初回にデスクトップへ `PodcastsChatGPT` フォルダのショートカットを作ります。
一度集めた回は `.collected.json` に記録し、集めたファイルを移した後も二度はコピーしません。
前の日のものは集めません（音声と字幕はそれぞれの日付で判断）。
以前の集め先（`~/Desktop/Public/PodcastsChatGPT`）にあるファイルと記録は Dropbox へ移します。
Mac に Dropbox のアプリが要り、ターミナルにフルディスクアクセスが要ります。確認: `python3 -m unittest mac/test_collect.py`

## アプリの置き場所

アプリは静的ファイルだけでできていて、Vercel の `podcastdrive-app` プロジェクト（Root Directory: `app`）で公開しています。

- URL: https://podcastdrive-app.vercel.app
- iPad 版: https://podcastdrive-app.vercel.app/ipad/ （同じアプリを左右2列で表示。教材・設定は共通。`app/vercel.json` の書き換えで配る）
- `main` に変更を入れると、自動で再デプロイされます。

公開されるのはアプリ本体だけで、教材は含みません。教材は各端末に取り込みます。

## 専用JSON（`エピソード名 (日付).listening.json`）

アプリは既存ファイルを取り込むときに、これと同じ形のデータを端末内で作ります（`app/material.js`。Mac の `tools/` と同じ規則で、テストで結果の一致を確かめています）。

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
- **切れ目を音声に合わせる（アプリで取り込むとき）**：元音声の音量を調べ、文の切れ目の近くで声が途切れているところ（60ms 以上静かなところ）に、前の文の終わりと次の文の始まりを合わせます。前の文は声が消えた少し後（0.12秒）まで、次の文は声が出る少し前（0.08秒）から流します。単語ごとの時刻がある TTML（Apple）では前後 0.35 秒、文ごとの時刻しかないとき（音声から作った TTML、JSON のみ）は前後 0.8 秒の範囲だけを探し、静かなところがなければ時刻どおりにします。以前に取り込んだ回は、最初に開いたときに一度だけ行います。
- **文の区切り**：1区間に複数の文があり、文ごとの訳（`translationParts`）と TTML の文の時刻が両方そろうときだけ分割します。
  文の途中で切れた区間は、次の区間とまとめます。どちらにも当てはまらなければ既存の区間をそのまま使います。
- **聞くための解説**：教材を取り込むときに一度だけ作って端末に保存します。AI や外部サービスは使いません。
  記号・括弧・スラッシュ・品詞ラベル・【訂正】メモは読み上げません。解説は通常速度で20秒以内に収めます。
- **訳のずれの検出**：原文の数字が訳に見当たらない区間が多いと警告を出します。

## テスト

```bash
python3 -m unittest discover -s tools/tests   # 変換・読み上げテキスト・アプリと書き出しの再生順の一致
tools/tests/run_e2e.sh                        # 架空の教材を作り、Chromium でアプリを操作して確認
```

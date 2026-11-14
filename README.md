# Listening

ポッドキャストを、1文ずつ「原文 → 日本語訳 → 語彙 → 解説 → 原文」の順に聞いて学ぶための道具です。
運転中・ランニング中に、画面をほとんど見ずに使うことを想定しています。

```
app/      Listening          iPhone・iPad で聞くアプリ（PWA）
studio/   Listening Studio   Mac で教材を作るアプリ
docs/     デザインの計画
```

## 流れ

1. **Mac**: Apple Podcasts から音声と字幕（`.ttml`）を取り出して、`Inbox` フォルダに置く。字幕がない回は音声だけでよい。
2. **Mac**: `Listening Studio.command` をダブルクリックして **Make** を押す。
   `Ready` フォルダに `名前.listening.json` と `名前.listening.m4a` ができる。
3. **Mac**: iPhone に送りたい回にチェックを入れて **Move to Drive** を押す（Google ドライブと同期している `Podcastsdrive` フォルダへ移る）。
4. **iPhone・iPad**: Listening を開き、**Add** でその2つのファイルを選ぶ。

## Listening Studio（Mac）

`studio/Listening Studio.command` をダブルクリックすると、ブラウザに画面が開きます。
初めてのときは、右クリック →「開く」で開いてください（インターネットから取ったファイルのため）。

### 用意するもの

| もの | 何に使うか | 費用 |
| --- | --- | --- |
| [Homebrew](https://brew.sh) | Python と ffmpeg を入れる（起動ファイルが自動で入れる） | 無料 |
| [Claude Code](https://claude.com/claude-code)（`claude` コマンド。一度 `claude` を実行してログイン） | 訳・語彙・解説を作る | 今の Claude の契約の範囲。API キーや追加の料金は使わない |

最初の起動のとき、音声合成（Kokoro）・文字起こし（Whisper）・辞書をこの Mac に入れます（数分、数 GB）。
2回目からはすぐ開きます。Apple シリコンの Mac を想定しています。

### すること

| 段階 | 内容 |
| --- | --- |
| Subtitles | 字幕（`.ttml`）があればそのまま使う。なければ Whisper（large-v3）で、単語ごとの時刻が付いた字幕を作る |
| Notes | Claude Code が、文ごとに訳・語彙・解説を作る。途中で止まっても続きから始める |
| Voice | 訳と解説を、Mac の中の AI 音声（Kokoro）で読み上げて、元の音声と1本にまとめる |

**語彙と解説は、難しさで選びます。** 指示は `studio/guide.md` にあります。

- 言語ごとの頻度表（wordfreq）で、設定したレベル（B1・B2・C1。既定は B2）の学習者が知らなそうな語を先に拾い、Claude に渡します。
- 易しい語は、熟語や、いつもと違う意味で使われているときだけ説明します。頻度表で易しすぎる1語の説明は、後から自動で外します。
- 解説は、語彙が分かっても文の組み立てや背景のせいで意味が取りにくい文にだけ付けます（目安は全体の 2〜4 割）。
- 難しい語句も取りにくい構文もない文には、何も付けません。

**声**は Settings の Voice で選べます（日本語の声 5 種類。**Test** で聞けます）。
原語の語句は、その言語の声（英語・中国語・スペイン語・フランス語）で読みます。

### フォルダ

| フォルダ | 既定の場所 | 中身 |
| --- | --- | --- |
| Inbox | `~/Desktop/Listening/Inbox` | 音声（`.mp3` `.m4a`）と字幕（`.ttml`）。同じ名前のものが1つのエピソード。すでにある `.json` を置くと、訳はそのまま使い、音声だけ作る |
| Ready | `~/Desktop/Listening/Ready` | できあがった2つのファイル |
| Drive | 自動で探す（`Podcastsdrive` という名前のフォルダ） | 選んで移したもの。Google ドライブと同期して iPhone から取り込む |

場所は画面の Settings で変えられます。ファイル名が `[番組名] 題名 (20260101).mp3` の形なら、番組名と日付も読み取ります。

コマンドで1つだけ作ることもできます: `python3 studio/make.py "…/エピソード.mp3"`

## Listening（iPhone・iPad）

- iPhone: https://podcastdrive-app.vercel.app
- iPad: https://podcastdrive-app.vercel.app/ipad/

Safari で開き、共有メニューの「ホーム画面に追加」を選びます。取り込んだ教材は端末の中に保存され、通信なしで聞けます。

| 操作 | ジェスチャー | 車・ロック画面・イヤホン |
| --- | --- | --- |
| 一時停止 / 再開（止めた所から） | タップ | 再生 / 一時停止 |
| 次へ（原文 → 訳 → 語彙 → 解説 → 次の文） | 左にスワイプ | 次の文 |
| 1つ戻る | 右にスワイプ | 前の文 |
| 今の文を最初から | 長押し | — |

Studio で作った教材は、訳・解説も音声ファイルに入っているので、画面をロックしたままでも続けて流れます。

以前の形（`.mp3`・`.json`・`.ttml` を一緒に選ぶ）もそのまま取り込めます。その場合、訳と解説は端末の声で読み上げます。

## 確認

```bash
python3 -m unittest discover -s studio/tests
```

```bash
bash app/tests/run_e2e.sh
```

必要なもの: python3、ffmpeg、espeak-ng、node、Playwright（Chromium）。テストは外部のサービスを使いません。

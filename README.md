# Listening

ポッドキャストを、1文ずつ「原文 → 日本語訳 → 語彙 → 解説 → 原文」の順に聞いて学ぶための道具です。
運転中・ランニング中に、画面をほとんど見ずに使うことを想定しています。

```
app/      Listening          iPhone・iPad で聞くアプリ（PWA）
studio/   Listening Studio   Mac で教材を作るアプリ
docs/     デザインの計画
```

## 流れ

1. **Mac**: `Listening Studio.command` をダブルクリックする。この Mac にある音声と字幕を自動で探して、候補に並べる。
   - デスクトップの `Podcasts` フォルダ（中のフォルダもすべて）
   - Apple Podcasts のアプリでダウンロードした音声と、自動で作られた字幕
2. **Mac**: 作りたい回にチェックを入れて **Make** を押す。
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

### 探す場所と置く場所

| 名前 | 既定の場所 | 中身 |
| --- | --- | --- |
| Podcasts | 自動で探す（`~/Desktop/Podcasts` か `~/Desktop/Public/Podcasts`） | 音声（`.mp3` `.m4a`）・字幕（`.ttml`）・`.json`。同じ名前のものが1つのエピソード |
| Apple Podcasts | アプリが作るフォルダ（読むだけ。変えない） | ダウンロードした音声と字幕。名前は `[番組名] 題名 (配信日)` にする |
| Ready | `~/Desktop/Listening/Ready` | できあがった2つのファイル |
| Drive | 自動で探す（`Podcastsdrive` という名前のフォルダ） | 選んで移したもの。Google ドライブと同期して iPhone から取り込む |

- Podcasts・Ready・Drive の場所は、画面の Settings で変えられます。
- 同じ名前の回が両方にあるときは、Podcasts フォルダのものを使います。
- すでに `.json` がある回は、その訳・解説をそのまま使い、音声だけ作ります。
  新しい選び方で作り直すときは、Settings の **Existing notes** を **Write new** にします。
- Apple Podcasts を読むには、「システム設定 → プライバシーとセキュリティ → フルディスクアクセス」で
  「ターミナル」をオンにします（読めないときは画面の上に案内が出ます）。

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

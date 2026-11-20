# Listening

ポッドキャストを、1文ずつ「原文 → 日本語訳 → 語彙 → 解説 → 原文」の順に聞いて学ぶための道具です。
運転中・ランニング中に、画面をほとんど見ずに使うことを想定しています。

```
app/      Listening          iPhone・iPad で聞くアプリ（PWA）
studio/   Listening Studio   Mac で教材を作るアプリ
docs/     デザインの計画
```

## 流れ

1. **Mac**: `studio/Listening Studio.command` をダブルクリックする。この Mac にある音声と字幕を自動で探して、言語ごとに並べる。
   - デスクトップの `Podcasts` フォルダ（中のフォルダもすべて）
   - Apple Podcasts のアプリでダウンロードした音声と、自動で作られた字幕
2. **Mac**: 訳・解説がまだない回にチェックを入れて **Prompt** を押す。
   - Apple Podcasts の回は、Podcasts フォルダの `言語/番組名` にコピーされる（フォルダ分け）。
   - Claude に頼むための文（ファイルの場所と手順）ができるので **Copy** して、Mac の Claude Code に貼る。
   - Claude が、字幕があれば字幕から、なければ音声から字幕を作ってから、教材の `.json` を音声の隣に作る。
3. **Mac**: Studio に戻ると、その回が **Notes ready** になる。チェックを入れて **Make** を押すと、
   訳・解説の音声（Mac の中の AI 音声 Kokoro）を作り、音声の隣に `名前.listening.json` と `名前.listening.m4a` ができる。
4. **iPhone・iPad**: その2つのファイルを Google ドライブなど iPhone から見える場所に置き、Listening の **Add** で選ぶ。

## Listening Studio（Mac）

初めてのときは、右クリック →「開く」で開いてください（インターネットから取ったファイルのため）。
最初の起動のとき、音声合成（Kokoro）・文字起こし（Whisper）・辞書をこの Mac に入れます（数分、数 GB）。

- **Studio は Claude を呼びません。** 訳・語彙・解説は、Prompt の文を Claude に渡して作ってもらいます（Studio の中で待たされず、利用量も自分で調整できます）。
  決まりは `studio/guide.md`、作った後の確かめは `studio/validate.py`（形・時刻・訳のずれ）と `studio/review.py`（説明が足りない難しい語・易しすぎる説明・訳に混ざった原語）。どちらも Prompt の文に書いてあります。
- **Level**（B1・B2・C1、既定 B2）は Prompt の文に入り、どこからを「難しい」とするかが変わります。

| Level | 説明する語 | 向いている人 |
| --- | --- | --- |
| B1 | やや珍しい語から説明する。説明がいちばん多い | 日常の話題なら分かるが、ニュースの語彙はまだ足りない |
| B2 | ニュースや専門の話題でつまずく語だけ | 普通の話題は大体分かる |
| C1 | かなり珍しい語・専門用語だけ。説明がいちばん少ない | ほとんど分かり、難しい語だけ確かめたい |

- **Voice**: 訳・解説を読む日本語の声（5種類。**Test** で聞ける）。原語の語句は、その言語の声で読みます。
- **Made**: 音声を作り終えた回は Made になり、一覧の下にまとまって選べなくなります（記録は Mac に残る）。作り直すときだけ **Again**、Finder で見るときは **Show**。
- 単語ごとの時刻がない字幕（以前の Whisper で作ったもの）は、Make のときに音声から字幕を作り直します（読んでいる単語を強調するため。訳・解説の `.json` はそのまま）。
- **言語の見分け方**（確かなものから順に）: 言語のフォルダ名 → 字幕の `xml:lang` → `.json` の `sourceLanguage` → Apple Podcasts の番組の言語 → 題名の文字と語。
- Apple Podcasts を読むには、「システム設定 → プライバシーとセキュリティ → フルディスクアクセス」で「ターミナル」をオンにします。
- Settings の **Podcasts** はライブラリの場所、**Other files** は置き場所が決まらない回のファイルの置き場所です。

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

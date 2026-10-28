---
name: podcast-transcribe
description: "ポッドキャストの音声から、単語ごとの時刻が付いた TTML（Apple Podcasts と同じ形式）を作り、続けて語学学習 JSON（Language Drill と聞き流しアプリの両方で使える形）まで作る。TTML がないエピソード（中国語など）や、1行ごとの時刻しかない古い Whisper の TTML を作り直すときに使う。「音声から字幕を作って」「TTMLがない回のJSONを作って」「文字起こしして」「TTMLを作り直して」「中国語の回を処理して」などで使う。TTML が既にあって変換するだけなら podcast-json / ttml-to-json を使う。"
---

# 音声 → TTML（単語ごとの時刻）→ JSON

TTML がないエピソードの音声を Whisper large-v3 で文字起こしし、**単語ごとに時刻が付いた TTML** を作る。
その TTML から podcast-json スキルの規則で JSON を作る。

単語ごとの時刻があると:
- JSON の各文の `begin`/`end` が正確になる（文の最初の音が欠けたり、前の文の最後が聞こえたりしない）
- 聞き流しアプリで字幕を単語単位で強調でき、文の切れ目を音声に正確に合わせられる（`wordTimings`）
- Language Drill は今までどおり JSON の `begin`/`end`/`original`/`translation`/`words`/`grammar`/`pinyin` を使う

以前の中国語の TTML（mlx_whisper で1行ごとの時刻だけ）は、1行が10秒を超えたり音楽を含んだりして時刻が粗い。
**作り直すと時刻の精度が大きく上がる。**

---

## 0. 実行する場所と費用（使う前にユーザーに伝える）

| 場所 | 使うもの | 速さ（目安） | 費用 |
|---|---|---|---|
| Apple Silicon の Mac（Cowork） | `mlx-whisper`（`pip install mlx-whisper`） | CPU 版より速い（未計測） | 無料。初回にモデル約3GBをダウンロード |
| Claude Code のクラウド（Linux） | `faster-whisper`（`pip install faster-whisper`、ffmpeg） | 4コアCPUで音声の長さの約0.6〜1.1倍の時間（実測） | 無料（セッションの利用時間のみ）。初回にモデル約3GB |

- 外部の有料 API は使わない。どちらも端末内（クラウドの場合はセッションのコンテナ内）で動く。
- クラウドで行う場合、音声をセッションに渡す必要がある（Drive の一時的なリンク共有など）。終わったら共有を戻すよう伝える。
- 30分の回は、クラウドの CPU で15〜30分ほどかかる。複数の回はまとめて実行し、終わるまで待つ。

## 1. 対象を決める

- ユーザーが指定した音声ファイル、または `library_status.json` で `"status": "no_transcript"` の回。
- 中国語の回で、既存の TTML が1行ごとの時刻だけの場合（`python3 parse_ttml.py x.ttml` の出力が `timing=line`）は、
  ユーザーが望めば作り直す。**元の TTML は消さず `_old_ttml/` フォルダへ移す**（JSON も作り直す場合は元の JSON も同様に移す）。
- 1回の実行で処理するのは **最大3件**（JSON 作成まで含めるため）。

## 2. 文字起こし

```bash
cp <skill-path>/scripts/transcribe.py .
python3 transcribe.py "<フォルダ>/[Channel] Episode (YYYYMMDD).mp3" --lang zh
# 出力: 同じ名前の .ttml。最後に JSON 1行（文の数・単語数・かかった時間）を表示
```

- `--lang` は言語フォルダ（英語/中国語/…）やチャンネルから決める（en, zh, fr, es …）。分からなければ省略（自動判定）。
- **中国語の字体**: `--script auto`（既定）は同じフォルダの他の TTML/JSON を見て簡体字・繁体字を合わせる
  （茶歇中文・不合时宜は簡体字、曾寶儀の人生藏寶圖・姐妹悄悄話などは繁体字）。
  変換には OpenCC（`pip install opencc-python-reimplemented`）があると確実。
- `--prompt` に番組名・司会者名・よく出る固有名詞を入れると誤認識が減る
  （例: `--prompt "茶歇中文播客，主持人Nathan。张骞、丝绸之路、汉武帝。"`）。
- 既に同じ名前の TTML があると止まる。作り直すときは元の TTML を `_old_ttml/` に移してから実行する（`--force` は使わない）。

`transcribe.py` が行っていること:
- Whisper large-v3、単語の時刻付き。前の文に引きずられた繰り返しを防ぐ設定（condition_on_previous_text=False）。
- faster-whisper では音声のない部分を除いてから認識（VAD）。無音で作られがちな定型文（「谢谢观看」など）と、同じ文の3回以上の繰り返しは捨てる。
- 文は「. ! ? 。！？」、1秒以上の間、長すぎる文（英語45語・中国語60字）は読点で区切る。中国語で文末に句点がなければ「。」を付ける。
- `<p>` ごとに1文、`<span podcasts:unit="sentence">` の中に `<span podcasts:unit="word" begin end>` を並べる（Apple と同じ形）。

## 3. 文字起こしの確認と訂正

1. 出力の `sentences`・`words` が音声の長さに見合っているか（目安: 1分あたり英語150語前後、中国語200字前後）。
2. `python3 parse_ttml.py "<...>.ttml"` で `timing=word` と最初の10文を確認。
3. 冒頭・中盤・終盤から数文を読み、明らかな誤認識（固有名詞・番組の決まり文句）を探す。
   - 直すときは podcast-json スキルの「音声認識の誤りの訂正」に従う
     （TTML は `<span>` の文字だけを直し、属性・時刻・数は変えない。JSON の `original` も同じに直す）。
   - `~/Desktop/PodcastApp/scripts/asr_fixes.json` に今後も起きそうな誤りを追加する。

## 4. JSON を作る（podcast-json スキルの規則で）

podcast-json スキルの 5〜10 章に従って JSON を作る。要点:

- `parse_ttml()` が返す各文には `wordTimings` が入っている。**JSON の各セグメントにそのまま入れる**。
- 中国語で複数の文を1セグメントにまとめた場合も、JSON を書いた後に
  `python3 add_word_timings.py "<...>.json"` を実行すれば、時刻の範囲から `wordTimings` を付け直せる。
- 訳・語彙・文法は podcast-json の「聞き流しモード向けの書き方」も守る。
- 書き出しは `parse_ttml.write_episode_json(output, path)`（`wordTimings` を1行にまとめて読みやすくする）。

## 5. 検証（毎回必ず）

```bash
python3 validate_json.py "<...>.json" "<...>.ttml"
```

- `ERROR` が0になるまで直す（訳のずれ、時刻と原文の不一致、必須項目の欠け）。
- `WARN` は簡単に直せるものは直す。

## 6. 報告

- 作った TTML・JSON、文の数、かかった時間、使った方法（mlx / faster-whisper）。
- 直した誤認識と、確信がなく直さなかった箇所。
- 作り直した場合は、元のファイルを `_old_ttml/` に残したこと。
- `normalize_library.py --root $PODCASTS_DIR --apply` を実行して `library_status.json` を更新する（podcast-json と同じ）。
- 残りの対象があれば「続けて」で続行できることを伝える。

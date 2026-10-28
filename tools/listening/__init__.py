"""聞き流し学習モード用の教材準備ツール（標準ライブラリのみ）。

- source.py   既存JSON・TTMLの読み込みと時刻の照合
- speech.py   耳で聞くための読み上げテキストとパーツ列の生成
- convert.py  既存JSON → 「元の名前.listening.json」
- tts.py      音声合成エンジン（macOS say / espeak-ng）
- sprite.py   元音声の区間と合成音声を1本にまとめた再生用音声の作成
- export.py   設定を焼き込んだ学習用音声（章付き）の書き出し
"""

FORMAT_NAME = "podcast-listening"
FORMAT_VERSION = 1
GENERATOR_VERSION = "1.0.0"

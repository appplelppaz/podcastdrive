"""音声合成エンジン。訳・解説・案内を事前に音声ファイルにする（再生時には使わない）。

- kokoro    Kokoro-82M（Apache 2.0）。Mac の中だけで動く AI 音声。無料・オフライン。本番用。
             pip install kokoro "misaki[ja,zh]" と、日本語の辞書（python -m unidic download）が必要。
- say       macOS 標準。無料・オフライン・認証不要。本番用。
- openjtalk 日本語は Open JTalk（pip install pyopenjtalk-plus）、外国語は espeak-ng。無料・オフライン。
             macOS 以外で準備するとき用。外国語の声の品質は低い。
- espeak-ng Linux 用。無料・オフライン。日本語の漢字は正しく読めないので、パイプラインの確認専用。

出力はすべて 44.1kHz モノラル 16bit WAV（ffmpeg で変換）。
"""

import os
import shutil
import subprocess
import tempfile

SAMPLE_RATE = 44100

# 言語ごとの既定の声。--voice ja=Otoya のように上書きできる。
SAY_VOICES = {
    "ja": "Kyoko",
    "en": "Samantha",
    "es": "Monica",
    "fr": "Thomas",
    "de": "Anna",
    "it": "Alice",
    "zh": "Tingting",
    "ko": "Yuna",
}
ESPEAK_VOICES = {"ja": "ja", "en": "en-us", "es": "es", "fr": "fr", "de": "de", "it": "it",
                 "zh": "cmn", "ko": "ko"}


# Kokoro の声と、その言語の読み方（lang_code）。--voice ja=jm_kumo のように上書きできる。
KOKORO_VOICES = {"ja": "jf_alpha", "en": "af_heart", "es": "ef_dora", "fr": "ff_siwis", "zh": "zf_xiaoxiao", "it": "if_sara", "pt": "pf_dora"}
KOKORO_LANG = {"ja": "j", "en": "a", "es": "e", "fr": "f", "zh": "z", "it": "i", "pt": "p"}
KOKORO_REPO = "hexgrad/Kokoro-82M"

ENGINES = ("kokoro", "say", "openjtalk", "espeak-ng")


def _has_kokoro():
    import importlib.util
    return importlib.util.find_spec("kokoro") is not None


def _has_openjtalk():
    try:
        import pyopenjtalk  # noqa: F401
    except ImportError:
        return False
    return bool(shutil.which("espeak-ng"))


def available_engine():
    if _has_kokoro():
        return "kokoro"
    if shutil.which("say"):
        return "say"
    if _has_openjtalk():
        return "openjtalk"
    if shutil.which("espeak-ng"):
        return "espeak-ng"
    return None


class Engine:
    def __init__(self, name=None, voices=None):
        self.name = name or available_engine()
        if self.name not in ENGINES:
            raise RuntimeError("音声合成エンジンが見つかりません（macOS の say、pyopenjtalk-plus＋espeak-ng、"
                               "espeak-ng のいずれかが必要です）")
        table = KOKORO_VOICES if self.name == "kokoro" else SAY_VOICES if self.name == "say" else ESPEAK_VOICES
        self.voices = dict(table)
        self.voices.update(voices or {})
        self._pipelines = {}

    def _kokoro(self, lang, text, path):
        """Kokoro で読む。モデルは最初の1回だけ読み込み、言語ごとの読み方（G2P）を使い回す。"""
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline

        base = (lang or "").split("-")[0].split("_")[0].lower()
        if base not in self._pipelines:
            model = next(iter(self._pipelines.values())).model if self._pipelines else True
            self._pipelines[base] = KPipeline(lang_code=KOKORO_LANG[base], repo_id=KOKORO_REPO, model=model)
        chunks = [r.audio.numpy() for r in self._pipelines[base](text, voice=self.voice_for(lang)) if r.audio is not None]
        if not chunks:
            raise RuntimeError("Kokoro が音声を作れませんでした: %s" % text[:40])
        sf.write(path, np.concatenate(chunks), 24000)

    def voice_for(self, lang):
        base = (lang or "").split("-")[0].split("_")[0].lower()
        if self.name == "openjtalk" and base == "ja":
            return self.voices.get("ja-openjtalk", "openjtalk")
        if base not in self.voices:
            raise RuntimeError("言語 %s の声が設定されていません（--voice %s=声の名前）" % (lang, base))
        return self.voices[base]

    def synthesize(self, lang, text, out_wav):
        voice = self.voice_for(lang)
        with tempfile.TemporaryDirectory() as tmp:
            if self.name == "kokoro":
                raw = os.path.join(tmp, "tts.wav")
                self._kokoro(lang, text, raw)
            elif self.name == "openjtalk" and voice == "openjtalk":
                raw = os.path.join(tmp, "tts.wav")
                _openjtalk_wav(text, raw)
            elif self.name == "say":
                raw = os.path.join(tmp, "tts.aiff")
                txt = os.path.join(tmp, "tts.txt")
                with open(txt, "w", encoding="utf-8") as f:
                    f.write(text)
                subprocess.run(["say", "-v", voice, "-o", raw, "-f", txt], check=True)
            else:
                raw = os.path.join(tmp, "tts.wav")
                subprocess.run(["espeak-ng", "-v", voice, "-w", raw, text], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            to_wav(raw, out_wav, trim=True)


def _openjtalk_wav(text, path):
    import wave

    import numpy as np
    import pyopenjtalk

    x, sr = pyopenjtalk.tts(text)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.clip(x, -32768, 32767).astype("<i2").tobytes())


def to_wav(src, dst, trim=False):
    """任意の音声 → 44.1kHz モノラル 16bit WAV。trim のとき前後の無音を詰める。"""
    af = []
    if trim:
        af.append("silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.02")
        af.append("areverse,silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05,areverse")
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", src, "-vn",
           "-ac", "1", "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16"]
    if af:
        cmd += ["-af", ",".join(af)]
    tmp = dst + ".part.wav"
    subprocess.run(cmd + [tmp], check=True)
    os.replace(tmp, dst)

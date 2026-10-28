// 聞き流し学習の再生エンジン。
//
// 再生順（1区間）: 元音声×最初の回数 → 訳 → 表現 → 解説 → もう一度 → 元音声×解説後の回数 → 無音
//   tools/listening/export.py の build_steps と同じ順序・同じ間合い。
//
// 2つの方式:
//   sprite  事前に音声化したスプライト（元音声の区間＋合成音声）を1つの <audio> で再生する。
//           再生要素を切り替えないので音が重ならず、無音も音声として流れ続ける。ロック中向け。
//   direct  元音声（MP3）＋端末の音声合成（speechSynthesis）。準備が要らないが、画面表示中向け。
//
// 操作のたびに token を進め、古い手順は token が変わった時点で何もせずに終わる。

export const DEFAULTS = {
  firstRepeats: 1,
  secondRepeats: 1,
  originalRate: 0.9,
  translationRate: 1.0,
  explanationRate: 1.0,
  gapSeconds: 0.7,
  translation: true,
  vocab: true,
  grammar: true,
  cues: true,
  repeatEpisode: false,
  carPrevious: "previous", // 車・ロック画面の「前へ」: previous（前の文）/ restart（今の文を最初から）
};

export const LIMITS = {
  firstRepeats: [1, 5], secondRepeats: [1, 5], originalRate: [0.5, 1.5],
  translationRate: [0.5, 2.0], explanationRate: [0.5, 2.0], gapSeconds: [0, 3],
};

const PAUSE = { repeat: 0.5, cue: 0.25, group: 0.4, part: 0.15 };
const GROUP_LABEL = { translation: "Translation", vocab: "Vocabulary", grammar: "Notes" };

export function clampSettings(s) {
  const out = { ...DEFAULTS, ...s };
  for (const [k, [lo, hi]] of Object.entries(LIMITS)) {
    const v = Number(out[k]);
    out[k] = Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : DEFAULTS[k];
  }
  out.firstRepeats = Math.round(out.firstRepeats);
  out.secondRepeats = Math.round(out.secondRepeats);
  return out;
}

// 1区間ぶんの再生手順
export function buildSteps(doc, seg, s) {
  const steps = [];
  const orig = (n, total, phase) => ({
    kind: "original", clip: "orig:" + seg.id, rate: s.originalRate,
    label: total > 1 ? `Original ${n}/${total}` : phase === "first" ? "Original" : "Original (again)",
  });
  for (let r = 0; r < s.firstRepeats; r++) {
    if (r) steps.push({ kind: "pause", seconds: PAUSE.repeat });
    steps.push(orig(r + 1, s.firstRepeats, "first"));
  }
  let spoke = false;
  for (const [group, on, rate] of [
    ["translation", s.translation, s.translationRate],
    ["vocab", s.vocab, s.explanationRate],
    ["grammar", s.grammar, s.explanationRate],
  ]) {
    const parts = seg.parts.filter((p) => p.type === "speech" && p.group === group);
    if (!on || !parts.length) continue;
    steps.push({ kind: "pause", seconds: PAUSE.group });
    if (s.cues) {
      const cue = doc.cues[group];
      steps.push({ kind: "speech", cue: true, clip: cue.clip, lang: cue.lang, text: cue.text, rate: s.explanationRate, label: GROUP_LABEL[group], group });
      steps.push({ kind: "pause", seconds: PAUSE.cue });
    }
    parts.forEach((p, i) => {
      if (i) steps.push({ kind: "pause", seconds: PAUSE.part });
      steps.push({ kind: "speech", clip: p.clip, lang: p.lang, text: p.text, rate, label: GROUP_LABEL[group], group });
    });
    spoke = true;
  }
  steps.push({ kind: "pause", seconds: spoke ? PAUSE.group : PAUSE.repeat });
  if (s.cues && spoke) {
    const cue = doc.cues.again;
    steps.push({ kind: "speech", cue: true, clip: cue.clip, lang: cue.lang, text: cue.text, rate: s.explanationRate, label: "Again", group: "again" });
    steps.push({ kind: "pause", seconds: PAUSE.cue });
  }
  for (let r = 0; r < s.secondRepeats; r++) {
    if (r) steps.push({ kind: "pause", seconds: PAUSE.repeat });
    steps.push(orig(r + 1, s.secondRepeats, "second"));
  }
  if (s.gapSeconds > 0) steps.push({ kind: "pause", seconds: s.gapSeconds, label: "Next sentence" });
  return steps;
}

// ---------------------------------------------------------------- 音の出し方

function setRate(el, rate) {
  el.preservesPitch = true;        // 声の高さを保つ
  el.webkitPreservesPitch = true;  // 古い Safari
  el.mozPreservesPitch = true;
  el.playbackRate = rate;
}

// 1つの <audio> の start〜end を再生する。終わり・中止・失敗で resolve する。
class RangePlayer {
  constructor(el) {
    this.el = el;
    this.cancel = null;
    this.ownPause = false;
  }

  play(start, end, rate) {
    const el = this.el;
    return new Promise((resolve) => {
      let done = false;
      let timer = null;
      let seeked = false;
      const finish = (reason) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        el.removeEventListener("timeupdate", check);
        el.removeEventListener("seeked", onSeeked);
        el.removeEventListener("ended", onEnded);
        if (this.cancel === cancel) this.cancel = null;
        resolve(reason);
      };
      const cancel = () => finish("cancel");
      this.cancel = cancel;
      const check = () => {
        if (done) return;
        if (!seeked) {
          // シーク完了前の古い再生位置で終わりと判断しない
          if (el.seeking || el.currentTime < start - 0.05 || el.currentTime > start + 1.0) {
            timer = setTimeout(check, 50);
            return;
          }
          seeked = true;
        }
        const remain = end - el.currentTime;
        if (remain <= 0.015) return finish("end");
        clearTimeout(timer);
        // 画面表示中は時刻ちょうどで止める。ロック中にタイマーが遅れても、
        // timeupdate で止まり、はみ出した分はクリップ後ろの無音になる。
        timer = setTimeout(check, Math.max(5, Math.min(200, (remain / el.playbackRate) * 1000 - 3)));
      };
      const onSeeked = () => { seeked = Math.abs(el.currentTime - start) < 0.5; check(); };
      const onEnded = () => finish("end");
      el.addEventListener("timeupdate", check);
      el.addEventListener("seeked", onSeeked);
      el.addEventListener("ended", onEnded);
      setRate(el, rate);
      el.currentTime = start;
      const p = el.paused ? el.play() : Promise.resolve();
      Promise.resolve(p).then(() => { setRate(el, rate); check(); },
        (err) => finish("error:" + (err && err.name)));
    });
  }

  stop() {
    if (this.cancel) this.cancel();
    if (!this.el.paused) {
      this.ownPause = true;
      this.el.pause();
    }
  }
}

function speakWithSynthesis(text, lang, rate) {
  const synth = window.speechSynthesis;
  return {
    promise: new Promise((resolve) => {
      if (!synth) return resolve("unsupported");
      const u = new SpeechSynthesisUtterance(text);
      u.lang = lang === "ja" ? "ja-JP" : lang;
      const voices = synth.getVoices();
      const v = voices.find((x) => x.lang.replace("_", "-").toLowerCase().startsWith(u.lang.toLowerCase())) ||
        voices.find((x) => x.lang.toLowerCase().startsWith(lang.toLowerCase()));
      if (v) u.voice = v;
      u.rate = rate;
      let done = false;
      const finish = (r) => { if (!done) { done = true; clearTimeout(t); resolve(r); } };
      u.onend = () => finish("end");
      u.onerror = (e) => finish("error:" + e.error);
      // onend が来ない環境への保険
      const t = setTimeout(() => finish("timeout"), (text.length / (lang === "ja" ? 6 : 12) / rate + 4) * 1000);
      synth.speak(u);
    }),
    cancel: () => synth && synth.cancel(),
  };
}

// ---------------------------------------------------------------- エンジン

export class ListeningEngine extends EventTarget {
  constructor({ doc, audioUrl, mode, el, settings, position }) {
    super();
    this.doc = doc;
    this.mode = mode;
    this.el = el;
    this.el.src = audioUrl;
    this.el.preload = "auto";
    this.player = new RangePlayer(el);
    this.settings = clampSettings(settings);
    this.segIndex = Math.min(Math.max(0, (position && position.segIndex) || 0), doc.segments.length - 1);
    this.stepIndex = 0;
    this.steps = this.makeSteps();
    this.playing = false;
    this.token = 0;
    this.speech = null;
    this.log = [];
    this.clips = (doc.audio && doc.audio.clips) || {};
    this.silence = this.clips.silence;
    el.addEventListener("pause", () => this.onElementPause());
  }

  get segment() { return this.doc.segments[this.segIndex]; }

  // 元音声の再生中なら、元のファイルでの再生位置（秒）。字幕の単語の強調に使う。
  originalTime() {
    const step = this.step;
    if (!this.playing || !step || step.kind !== "original") return null;
    if (this.mode !== "sprite") return this.el.currentTime;
    const c = this.clips[step.clip];
    return c ? this.segment.clip.begin + (this.el.currentTime - c.start) : null;
  }
  get step() { return this.steps[this.stepIndex]; }

  makeSteps() { return buildSteps(this.doc, this.segment, this.settings); }

  updateSettings(s) {
    this.settings = clampSettings(s);
    // 再生中の区間は今の手順のまま終え、次の区間から反映する（途中で順序が変わらないように）
    if (!this.playing) {
      this.steps = this.makeSteps();
      this.stepIndex = 0;
    }
    this.emit();
  }

  emit(type = "state") {
    this.dispatchEvent(new CustomEvent(type, { detail: this.snapshot() }));
  }

  snapshot() {
    const step = this.step;
    return {
      playing: this.playing, segIndex: this.segIndex, total: this.doc.segments.length,
      stepIndex: this.stepIndex, steps: this.steps.length,
      label: step ? step.label || "" : "",
      kind: step ? step.kind : null, segment: this.segment,
    };
  }

  // 自分で止めたのでない pause（電話・他のアプリ・Bluetooth切断など）は中断として扱う
  onElementPause() {
    if (this.player.ownPause) { this.player.ownPause = false; return; }
    if (this.mode === "direct" && this.step && this.step.kind !== "original") return;
    if (this.playing) {
      this.halt();
      this.emit("interrupted");
    }
  }

  play() {
    if (this.playing) return;
    this.playing = true;
    if (navigator.audioSession) {
      try { navigator.audioSession.type = "playback"; } catch { /* 未対応 */ }
    }
    this.run(++this.token);
    this.emit();
  }

  pause() {
    if (!this.playing) return;
    this.halt();
    this.emit();
  }

  toggle() { this.playing ? this.pause() : this.play(); }

  halt() {
    this.playing = false;
    this.token++;
    this.player.stop();
    if (this.speech) { this.speech.cancel(); this.speech = null; }
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    // 中断した手順は、再開時にその手順の最初から（元音声なら区間の頭から）やり直す
    this.emit("position");
  }

  goto(index, { keepPlaying = true } = {}) {
    const wasPlaying = this.playing && keepPlaying;
    if (this.playing) this.halt();
    this.segIndex = Math.min(Math.max(0, index), this.doc.segments.length - 1);
    this.steps = this.makeSteps();
    this.stepIndex = 0;
    this.emit("position");
    if (wasPlaying) this.play(); else this.emit();
  }

  next() { if (this.segIndex < this.doc.segments.length - 1) this.goto(this.segIndex + 1); }
  previous() { this.goto(this.segIndex - 1); }
  restart() { this.goto(this.segIndex); }

  async run(token) {
    while (token === this.token) {
      const step = this.step;
      if (!step) {
        if (!this.advanceSegment()) return;
        continue;
      }
      this.log.push({ t: performance.now(), seg: this.segment.id, i: this.stepIndex, kind: step.kind, clip: step.clip, label: step.label, rate: step.rate });
      this.emit("step");
      const result = await this.exec(step, token);
      if (token !== this.token) return;
      if (result && String(result).startsWith("error")) {
        this.halt();
        const blocked = String(result).includes("NotAllowedError");
        const message = blocked ? "Tap to start playback" : "Playback failed (" + result + ")";
        this.dispatchEvent(new CustomEvent("error", { detail: { message, blocked } }));
        this.emit();
        return;
      }
      this.stepIndex++;
    }
  }

  advanceSegment() {
    if (this.segIndex + 1 >= this.doc.segments.length) {
      if (this.settings.repeatEpisode) {
        this.segIndex = 0;
      } else {
        this.playing = false;
        this.token++;
        this.player.stop();
        this.segIndex = 0;
        this.steps = this.makeSteps();
        this.stepIndex = 0;
        this.emit("position");
        this.emit("finished");
        this.emit();
        return false;
      }
    } else {
      this.segIndex++;
    }
    this.steps = this.makeSteps();
    this.stepIndex = 0;
    this.emit("position");
    return true;
  }

  async exec(step, token) {
    if (this.mode === "sprite") {
      if (step.kind === "pause") {
        return this.player.play(this.silence.start, this.silence.start + step.seconds, 1);
      }
      const c = this.clips[step.clip];
      if (!c) return "error:clip-missing";
      return this.player.play(c.start, c.end, step.rate);
    }
    // direct: 元音声は MP3 の時刻、読み上げは端末の音声合成
    if (step.kind === "original") {
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      const { begin, end } = this.segment.clip;
      return this.player.play(begin, end, step.rate);
    }
    this.player.stop();
    if (step.kind === "pause") {
      return new Promise((resolve) => {
        const t = setTimeout(() => resolve("end"), step.seconds * 1000);
        this.speech = { cancel: () => { clearTimeout(t); resolve("cancel"); } };
      });
    }
    const sp = speakWithSynthesis(step.text, step.lang, step.rate);
    this.speech = sp;
    const r = await sp.promise;
    if (token !== this.token) return "cancel";
    this.speech = null;
    return r === "unsupported" ? "error:speech-unsupported" : "end";
  }
}

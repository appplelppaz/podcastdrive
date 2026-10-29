import { DEFAULTS, LIMITS, ListeningEngine, clampSettings, unlockSpeech } from "./engine.js";
import * as store from "./store.js";
import { alignSubtitle } from "./subtitle.js";
import { tidyVocabSpeech } from "./material.js";

const $ = (id) => document.getElementById(id);
let settings = clampSettings(store.loadSettings(DEFAULTS));
let engine = null;
let current = null; // 開いている教材の行
let audioUrl = null;
let rowsById = new Map(); // 一覧の教材（音声は参照だけ。開くときに待たずに再生を始めるため）

// ---------------------------------------------------------------- 設定

function renderSettings() {
  for (const el of document.querySelectorAll("[data-key]")) {
    const k = el.dataset.key;
    if (el.classList.contains("stepper")) {
      if (!el.firstChild) {
        el.innerHTML = `<button type="button" aria-label="Decrease">−</button><span class="value"></span><button type="button" aria-label="Increase">+</button>`;
        const [minus, , plus] = el.children;
        minus.onclick = () => changeSetting(k, settings[k] - Number(el.dataset.step));
        plus.onclick = () => changeSetting(k, settings[k] + Number(el.dataset.step));
      }
      el.querySelector(".value").textContent = settings[k] + (el.dataset.unit || "");
    } else if (el.type === "checkbox") {
      el.checked = !!settings[k];
    } else {
      el.value = settings[k];
    }
  }
  for (const out of document.querySelectorAll("output[data-for]")) {
    const k = out.dataset.for;
    out.textContent = k === "gapSeconds" ? settings[k].toFixed(1) + " s" : settings[k].toFixed(2) + "×";
  }
}

function changeSetting(k, v) {
  if (LIMITS[k]) v = Math.min(LIMITS[k][1], Math.max(LIMITS[k][0], v));
  settings = clampSettings({ ...settings, [k]: v });
  store.saveSettings(settings);
  if (engine) engine.updateSettings(settings);
  renderSettings();
}

for (const el of document.querySelectorAll("[data-key]")) {
  if (el.classList.contains("stepper")) continue;
  el.addEventListener("input", () => {
    const k = el.dataset.key;
    changeSetting(k, el.type === "checkbox" ? el.checked : el.tagName === "SELECT" ? el.value : Number(el.value));
  });
}
$("resetSettings").onclick = () => {
  settings = clampSettings(DEFAULTS);
  store.saveSettings(settings);
  if (engine) engine.updateSettings(settings);
  renderSettings();
};
$("exportSettings").onclick = () => {
  const blob = new Blob([JSON.stringify(settings, null, 1)], { type: "application/json" });
  const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(blob), download: "listening-settings.json" });
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

// ---------------------------------------------------------------- 教材一覧（教材ごとの再生履歴）

function historyText(row) {
  const pos = store.loadPosition(row.id);
  if (!pos) return "Not started";
  const when = new Date(pos.savedAt).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  if (pos.finished) return `Finished · ${when}`;
  return `Last: sentence ${pos.segIndex + 1} of ${row.segments} · ${when}`;
}

async function renderLibrary() {
  const rows = await store.listMaterials();
  rowsById = new Map(rows.map((r) => [r.id, r]));
  const ul = $("library");
  ul.innerHTML = "";
  $("libraryEmpty").hidden = rows.length > 0;
  for (const row of rows) {
    const pos = store.loadPosition(row.id);
    const resumable = pos && !pos.finished && pos.segIndex > 0;
    const li = document.createElement("li");
    li.dataset.id = row.id;
    li.innerHTML = `<div class="name"></div><div class="meta"></div><div class="actions"></div>`;
    li.querySelector(".name").textContent = row.title;
    const notes = [];
    if (row.mode === "direct" && row.hasTtml === false) notes.push("no transcript timing");
    if (row.translationMismatch) notes.push("⚠ translations may not match the audio");
    li.querySelector(".meta").textContent =
      `${row.channel ? row.channel + " · " : ""}${row.segments} sentences${notes.length ? " · " + notes.join(" · ") : ""} — ${historyText(row)}`;
    const actions = li.querySelector(".actions");
    const add = (label, cls, fn) => {
      const b = Object.assign(document.createElement("button"), { type: "button", className: "button " + cls, textContent: label });
      b.onclick = fn;
      actions.append(b);
    };
    if (resumable) {
      add(`Resume (${pos.segIndex + 1})`, "start resume", () => open(row.id, "resume"));
      add("Start over", "outline from-start", () => open(row.id, "start"));
    } else {
      add("Start", "start", () => open(row.id, "start"));
    }
    add("Delete", "outline delete", async () => {
      if (!confirm(`Delete “${row.title}” from this device?`)) return;
      await store.deleteMaterial(row.id);
      renderLibrary();
    });
    ul.append(li);
  }
}

$("fileInput").addEventListener("change", async (e) => {
  const status = $("importStatus");
  status.className = "status";
  status.textContent = "Importing… (matching sentences to the audio, this takes a few seconds)";
  try {
    const { imported, errors } = await store.importFiles(e.target.files);
    const lines = imported.map((r) => `Imported “${r.title}”` +
      (r.translationMismatch ? " — ⚠ the translations may not match the audio. If you have its .fix.json, import it together." : ""));
    status.textContent = [...lines, ...errors].join("\n");
    if (errors.length || imported.some((r) => r.translationMismatch)) status.className = "status error";
  } catch (err) {
    status.className = "status error";
    status.textContent = err.message;
  }
  e.target.value = "";
  renderLibrary();
});

// ---------------------------------------------------------------- 再生画面

function savePos(extra = {}) {
  if (!engine || !current) return;
  store.savePosition(current.id, { segIndex: engine.segIndex, segId: engine.segment.id, total: engine.doc.segments.length, finished: false, ...extra });
}

function resumeIndex(doc, pos) {
  if (!pos || pos.finished) return 0;
  const byId = doc.segments.findIndex((s) => s.id === pos.segId);
  return byId >= 0 ? byId : Math.min(pos.segIndex || 0, doc.segments.length - 1);
}

// 0.1秒の無音（WAV）。タップの中でこれを再生して <audio> の再生を許可させておく
const SILENCE = (() => {
  const n = 800, buf = new DataView(new ArrayBuffer(44 + n * 2));
  const w = (o, str) => [...str].forEach((c, i) => buf.setUint8(o + i, c.charCodeAt(0)));
  w(0, "RIFF"); buf.setUint32(4, 36 + n * 2, true); w(8, "WAVEfmt "); buf.setUint32(16, 16, true);
  buf.setUint16(20, 1, true); buf.setUint16(22, 1, true); buf.setUint32(24, 8000, true); buf.setUint32(28, 16000, true);
  buf.setUint16(32, 2, true); buf.setUint16(34, 16, true); w(36, "data"); buf.setUint32(40, n * 2, true);
  return URL.createObjectURL(new Blob([buf], { type: "audio/wav" }));
})();

// iPhone はタップの処理の外（音声の読み込みを待った後）だと再生を許さないことがあるので、
// タップの中で同じ <audio> を一度鳴らし、読み上げも準備しておく
function unlockAudio() {
  const el = $("audio");
  el.src = SILENCE;
  const p = el.play();
  if (p) p.catch(() => { /* 許可されなければ、再生ボタンのタップで始める */ });
}

let opening = 0;
function open(id, how) {
  const row = rowsById.get(id);
  if (!row) return;
  const token = ++opening;
  if (engine) { engine.pause(); engine = null; }
  unlockSpeech();
  unlockAudio();
  current = row;
  store.setLastMaterialId(id);
  setStatus("Loading audio…");
  show("player");
  store.getAudio(row).then(async (audio) => {
    if (store.needsRefine(row)) {
      setStatus("Matching sentences to the audio… (first time only)");
      await store.ensureRefined(row, audio);
    }
    // 無音がまだ鳴っていれば止め終えてから始める（その pause を中断と取り違えないように）
    const el = $("audio");
    if (!el.paused) await new Promise((r) => { el.addEventListener("pause", r, { once: true }); el.pause(); setTimeout(r, 300); });
    if (token !== opening) return;
    start(row, audio, how);
  }).catch((err) => {
    if (token === opening) setStatus(err.message, true);
  });
}

function start(row, audio, how) {
  const id = row.id;
  if (audioUrl) URL.revokeObjectURL(audioUrl);
  audioUrl = URL.createObjectURL(audio);
  const segIndex = how === "resume" ? resumeIndex(row.doc, store.loadPosition(id)) : 0;
  tidyVocabSpeech(row.doc);
  engine = new ListeningEngine({ doc: row.doc, audioUrl, mode: row.mode, el: $("audio"), settings, position: { segIndex } });
  window.__engine = engine; // 動作確認用
  window.__systemPause = systemPause;
  engine.addEventListener("state", renderPlayer);
  engine.addEventListener("step", renderPlayer);
  engine.addEventListener("position", () => { savePos(); updateMediaSession(); });
  engine.addEventListener("finished", () => {
    store.savePosition(current.id, { segIndex: 0, segId: row.doc.segments[0].id, total: row.doc.segments.length, finished: true });
    setStatus("Finished");
  });
  engine.addEventListener("interrupted", () => { savePos(); setStatus("Interrupted — tap to resume from here"); });
  engine.addEventListener("error", (e) => setStatus(e.detail.message, true));
  $("subtitle").lang = row.doc.sourceLanguage || "en";
  $("spoken").lang = row.doc.targetLanguage || "ja";
  setStatus(row.mode === "sprite" || settings.keepAwake ? "" : "Playback may stop when the screen locks");
  renderedSeg = null;
  savePos();
  setupMediaSession();
  renderPlayer();
  engine.play();
}

function setStatus(text, error = false) {
  $("playerStatus").textContent = text;
  $("playerStatus").className = "status center" + (error ? " error" : "");
}

// 字幕: 原文を単語ごとに分けて表示し、元音声の再生位置に合わせて強調する
let renderedSeg = null;
let wordEls = [];
let spokenText = "";

function renderSubtitle(seg) {
  if (renderedSeg === seg) return;
  renderedSeg = seg;
  const box = $("subtitle");
  box.textContent = "";
  wordEls = [];
  const aligned = alignSubtitle(seg.original, seg.wordTimings, engine && engine.doc.sourceLanguage);
  if (aligned) {
    for (const u of aligned.units) {
      if (!u.text.trim()) { box.append(u.text); continue; }
      const span = document.createElement("span");
      span.className = "w";
      span.textContent = u.text;
      if (u.b !== null) { span.dataset.b = u.b; span.dataset.e = u.e; }
      wordEls.push(span);
      box.append(span);
    }
  } else {
    box.textContent = seg.original; // 時刻がない・原文と合わない区間は文全体を表示するだけ
  }
  spokenText = "";
  $("spoken").textContent = "";
}

function highlight() {
  if (!engine) return;
  const t = engine.originalTime();
  const box = $("subtitle");
  box.classList.toggle("idle", t === null);
  if (t === null) return;
  let prevDone = false;
  for (const w of wordEls) {
    if (w.dataset.b === undefined) {
      // 時刻のない文字（句読点や照らし合わせで合わなかった文字）は直前の単語に合わせる
      w.classList.remove("now");
      w.classList.toggle("done", prevDone);
      continue;
    }
    const b = Number(w.dataset.b), e = Number(w.dataset.e);
    w.classList.toggle("now", t >= b && t < e);
    w.classList.toggle("done", t >= e);
    prevDone = t >= e;
  }
}

function frame() {
  if (engine && !$("player").hidden && document.visibilityState === "visible") highlight();
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

function renderPlayer() {
  if (!engine) return;
  const s = engine.snapshot();
  const step = engine.step;
  renderSubtitle(s.segment);
  // 読み上げ中（<audio> は止めている）も、ロック画面には再生中と伝える
  if ("mediaSession" in navigator) {
    try { navigator.mediaSession.playbackState = s.playing ? "playing" : "paused"; } catch { /* 未対応 */ }
  }
  // 訳・語彙・解説の読み上げ中は、その項目の全文を原文の下に出す（案内の「訳」「表現」などは出さない）
  if (step && step.kind === "speech" && !step.cue) {
    const text = s.segment.parts.filter((p) => p.type === "speech" && p.group === step.group).map((p) => p.text).join(" ");
    if (text !== spokenText) {
      spokenText = text;
      $("spoken").textContent = text;
    }
  } else if (step && step.kind === "original") {
    spokenText = "";
    $("spoken").textContent = "";
  }
  $("player").classList.toggle("paused", !s.playing);
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = s.playing ? "playing" : "paused";
  updateWakeLock();
}

// 再生中は画面を消さない（端末の音声合成は画面ロックで止まることがあるため）
let wakeLock = null;
async function updateWakeLock() {
  const want = engine && engine.playing && settings.keepAwake && document.visibilityState === "visible";
  try {
    if (want && !wakeLock && navigator.wakeLock) {
      wakeLock = await navigator.wakeLock.request("screen");
      wakeLock.addEventListener("release", () => { wakeLock = null; });
    } else if (!want && wakeLock) {
      await wakeLock.release();
      wakeLock = null;
    }
  } catch { wakeLock = null; /* 未対応・拒否 */ }
}

function show(name) {
  $("setup").hidden = name !== "setup";
  $("player").hidden = name !== "player";
  if (name === "setup") { renderSettings(); renderLibrary(); }
}

// ---------------------------------------------------------------- 操作（ジェスチャー・キーボード）

let flashTimer = null;
function flash(text) {
  const f = $("flash");
  f.textContent = text;
  f.classList.add("show");
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => f.classList.remove("show"), 700);
  if (navigator.vibrate) navigator.vibrate(10);
}

const actions = {
  toggle() { if (!engine) return; const was = engine.playing; if (!was) unlockSpeech(); engine.toggle(); flash(was ? "Ⅱ Paused" : "▶ Playing"); },
  next() { if (!engine) return; engine.next(); flash("Next ›"); },
  previous() { if (!engine) return; engine.previous(); flash("‹ Previous"); },
  restart() { if (!engine) return; engine.restart(); flash("↺ Restart"); },
};

$("back").onclick = () => {
  if (engine) { engine.pause(); savePos(); }
  show("setup");
};

// タップ: 一時停止・再開 / 左スワイプ: 次の文 / 右スワイプ: 前の文 / 長押し: 今の文を最初から
const SWIPE = 50, TAP_MOVE = 12, HOLD_MS = 600;
let g = null;
const stage = $("stage");
stage.addEventListener("pointerdown", (e) => {
  if (e.button > 0) return;
  g = { x: e.clientX, y: e.clientY, t: performance.now(), held: false };
  g.timer = setTimeout(() => { if (g) { g.held = true; actions.restart(); } }, HOLD_MS);
  try { stage.setPointerCapture(e.pointerId); } catch { /* なくてもよい */ }
});
stage.addEventListener("pointermove", (e) => {
  if (g && Math.hypot(e.clientX - g.x, e.clientY - g.y) > TAP_MOVE) clearTimeout(g.timer);
});
stage.addEventListener("pointerup", (e) => {
  if (!g) return;
  clearTimeout(g.timer);
  const dx = e.clientX - g.x, dy = e.clientY - g.y;
  const held = g.held;
  g = null;
  if (held) return;
  if (Math.abs(dx) >= SWIPE && Math.abs(dx) > Math.abs(dy) * 1.5) {
    dx < 0 ? actions.next() : actions.previous();
  } else if (Math.hypot(dx, dy) < TAP_MOVE) {
    actions.toggle();
  }
});
stage.addEventListener("pointercancel", () => { if (g) clearTimeout(g.timer); g = null; });
stage.addEventListener("contextmenu", (e) => e.preventDefault());
document.addEventListener("keydown", (e) => {
  if ($("player").hidden || e.target.closest("input, select")) return;
  const map = { " ": actions.toggle, ArrowRight: actions.next, ArrowLeft: actions.previous, r: actions.restart };
  if (map[e.key]) { e.preventDefault(); map[e.key](); }
});

// ---------------------------------------------------------------- ロック画面・車の操作

function setupMediaSession() {
  if (!("mediaSession" in navigator)) return;
  const handlers = {
    play: () => engine && engine.play(),
    pause: systemPause,
    stop: systemPause,
    nexttrack: () => engine && engine.next(),
    previoustrack: () => engine && (settings.carPrevious === "restart" ? engine.restart() : engine.previous()),
    // シーク操作を登録すると iOS のロック画面が「前へ・次へ」ではなく秒送りになるため登録しない
    seekbackward: null, seekforward: null, seekto: null,
  };
  for (const [action, fn] of Object.entries(handlers)) {
    try { navigator.mediaSession.setActionHandler(action, fn); } catch { /* 未対応の操作 */ }
  }
  updateMediaSession();
}

// ロック画面・車・イヤホンからの一時停止。元音声と読み上げの切り替え直後に iPhone が自動で送ってくる
// 「一時停止」は無視する（押した操作なら、切り替えから時間がたっているので効く）
function systemPause() {
  if (!engine || !engine.playing) return;
  if (engine.settling()) return;
  engine.pause();
}

function updateMediaSession() {
  if (!("mediaSession" in navigator) || !engine) return;
  const s = engine.snapshot();
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: `${s.segIndex + 1}/${s.total} ${current.title}`,
      artist: current.channel || "Listening Mode",
      album: "Listening Mode",
      artwork: [{ src: "icons/icon-512.png", sizes: "512x512", type: "image/png" }],
    });
  } catch { /* 未対応 */ }
}

// アプリが裏に回る・閉じるときにも位置を保存する
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") savePos();
  updateWakeLock();
});
window.addEventListener("pagehide", () => savePos());

// ---------------------------------------------------------------- 起動

if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
  navigator.serviceWorker.register("sw.js").catch(() => { /* オフライン化できなくても使える */ });
}

const params = new URLSearchParams(location.search);
if (params.get("import")) {
  // 例: ?import=materials/xxx.listening.json （同じサーバーに置いた教材を取り込む）
  $("importStatus").textContent = "Importing…";
  store.importFromUrl(params.get("import"))
    .then((row) => { $("importStatus").textContent = `Imported “${row.title}”`; renderLibrary(); })
    .catch((err) => { $("importStatus").className = "status error"; $("importStatus").textContent = err.message; });
}
show("setup");
window.__appStarted = true; // index.html の起動確認用
try { sessionStorage.removeItem(window.__appResetKey || "listening.autoReset"); } catch { /* なくてもよい */ }

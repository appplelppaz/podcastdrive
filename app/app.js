import { DEFAULTS, LIMITS, ListeningEngine, clampSettings, unlockSpeech } from "./engine.js";
import * as store from "./store.js";
import { alignSubtitle } from "./subtitle.js";
import { langBase, tidySpeech } from "./material.js";

const $ = (id) => document.getElementById(id);
let settings = clampSettings(store.loadSettings(DEFAULTS));
let engine = null;
let current = null; // 開いている教材の行
let audioUrl = null;
let rowsById = new Map(); // 一覧の教材（音声は参照だけ。開くときに待たずに再生を始めるため）
// iPad 版（index.html が html に split を付ける）: 一覧（左）は出したまま、右にエピソードか設定を出す
const SPLIT = document.documentElement.classList.contains("split");

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

const LANG_NAMES = { zh: "Chinese", en: "English", es: "Spanish", fr: "French" };
const langOf = (row) => langBase((row.doc && row.doc.sourceLanguage) || "");
const langName = (code) => LANG_NAMES[code] || (code ? code.toUpperCase() : "Other");

// どこまで聞いたか（短く）
function historyText(row, pos) {
  if (!pos) return `${row.segments} sentences`;
  if (pos.finished) return "Finished";
  return `Sentence ${pos.segIndex + 1} of ${row.segments}`;
}
const progressOf = (row, pos) => (!pos ? 0 : pos.finished ? 1 : pos.segIndex / Math.max(1, row.segments));

function notesOf(row) {
  const notes = [];
  if (row.mode === "direct" && row.hasTtml === false) notes.push("No word timing");
  if (row.translationMismatch) notes.push("Translations may not match the audio");
  return notes;
}

let langFilter = "";
try { langFilter = localStorage.getItem("listening.langFilter") || ""; } catch { /* なくてもよい */ }

async function renderLibrary() {
  const rows = await store.listMaterials();
  rowsById = new Map(rows.map((r) => [r.id, r]));
  renderHero(rows);
  // 言語の切り替え（取り込んだ言語が2つ以上のときだけ）
  const langs = [...new Set(rows.map(langOf))].sort((a, b) => Object.keys(LANG_NAMES).indexOf(a) - Object.keys(LANG_NAMES).indexOf(b));
  if (langFilter && !langs.includes(langFilter)) langFilter = "";
  const tabs = $("langTabs");
  tabs.hidden = langs.length < 2;
  tabs.innerHTML = "";
  for (const code of ["", ...langs]) {
    const b = Object.assign(document.createElement("button"), { type: "button", className: "tab", textContent: code ? langName(code) : "All" });
    b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(code === langFilter));
    b.onclick = () => {
      langFilter = code;
      try { localStorage.setItem("listening.langFilter", code); } catch { /* なくてもよい */ }
      renderLibrary();
    };
    tabs.append(b);
  }
  const ul = $("library");
  ul.innerHTML = "";
  $("libraryEmpty").hidden = rows.length > 0;
  for (const row of rows.filter((r) => !langFilter || langOf(r) === langFilter)) {
    const pos = store.loadPosition(row.id);
    const resumable = pos && !pos.finished && pos.segIndex > 0;
    const li = document.createElement("li");
    li.dataset.id = row.id;
    if (SPLIT && row.id === episodeId) li.classList.add("selected");
    li.innerHTML = `<button type="button" class="card-open"><span class="name"></span><span class="channel"><span class="lang-tag"></span><span class="ch"></span></span></button>` +
      `<div class="track" aria-hidden="true"><span></span></div><div class="meta"></div><div class="actions"></div>`;
    li.querySelector(".name").textContent = row.title;
    li.querySelector(".lang-tag").textContent = langs.length > 1 && !langFilter ? langName(langOf(row)) : "";
    li.querySelector(".ch").textContent = row.channel || "";
    li.querySelector(".card-open").onclick = () => openEpisode(row.id);
    li.querySelector(".track span").style.width = (progressOf(row, pos) * 100).toFixed(1) + "%";
    li.querySelector(".meta").textContent = historyText(row, pos);
    for (const n of notesOf(row)) li.querySelector(".actions").before(Object.assign(document.createElement("div"), { className: "note", textContent: n }));
    const actions = li.querySelector(".actions");
    const add = (label, cls, fn) => {
      const b = Object.assign(document.createElement("button"), { type: "button", className: "button " + cls, textContent: label });
      b.onclick = fn;
      actions.append(b);
    };
    if (resumable) {
      add("Continue", "start resume", () => open(row.id, "resume"));
      add("Restart", "outline from-start", () => open(row.id, "start"));
    } else {
      add("Play", "start", () => open(row.id, "start"));
    }
    add("Sentences", "outline pick", () => openEpisode(row.id));
    ul.append(li);
  }
}

// ホームの一番上: 最後に聞いたエピソードの、止めた文そのものを大きく見せて、すぐ続きを再生できるようにする
function renderHero(rows) {
  const id = store.lastMaterialId();
  const row = rows.find((r) => r.id === id);
  const pos = row && store.loadPosition(row.id);
  const hero = $("hero");
  hero.hidden = !row || !pos || pos.finished;
  if (hero.hidden) return;
  const i = resumeIndex(row.doc, pos);
  const seg = row.doc.segments[i];
  $("heroLang").textContent = langName(langOf(row));
  $("heroChannel").textContent = row.channel || "";
  $("heroTitle").textContent = row.title;
  $("heroLine").textContent = seg ? seg.original : "";
  $("heroLine").lang = row.doc.sourceLanguage || "";
  $("heroTrack").style.width = (progressOf(row, pos) * 100).toFixed(1) + "%";
  $("heroMeta").textContent = historyText(row, pos);
  $("heroPlay").onclick = () => open(row.id, "resume");
  $("heroPick").onclick = () => openEpisode(row.id);
}

// エピソード: 文の一覧から好きな文を選んで再生する
let episodeId = null;
function openEpisode(id) {
  const row = rowsById.get(id);
  if (!row) return;
  episodeId = id;
  const pos = store.loadPosition(id);
  const at = pos && !pos.finished ? resumeIndex(row.doc, pos) : -1;
  $("epLang").textContent = langName(langOf(row));
  $("epChannel").textContent = row.channel || "";
  $("epTitle").textContent = row.title;
  $("epTrack").style.width = (progressOf(row, pos) * 100).toFixed(1) + "%";
  $("epMeta").textContent = historyText(row, pos);
  $("epNotes").innerHTML = "";
  for (const n of notesOf(row)) $("epNotes").append(Object.assign(document.createElement("p"), { className: "note", textContent: n }));
  const actions = $("epActions");
  actions.innerHTML = "";
  const add = (label, cls, fn) => {
    const b = Object.assign(document.createElement("button"), { type: "button", className: "button " + cls, textContent: label });
    b.onclick = fn;
    actions.append(b);
  };
  if (at > 0) add("Continue", "start resume", () => open(id, "resume"));
  add(at > 0 ? "Restart" : "Play", at > 0 ? "outline from-start" : "start", () => open(id, "start"));
  const ol = $("sentences");
  ol.innerHTML = "";
  const lang = row.doc.sourceLanguage || "";
  row.doc.segments.forEach((seg, i) => {
    const li = document.createElement("li");
    if (i === at) li.className = "here";
    const b = document.createElement("button");
    b.type = "button";
    b.innerHTML = `<span class="t"></span><span class="o"></span><span class="j" lang="ja"></span>`;
    const sec = Math.max(0, Math.floor(seg.begin));
    b.querySelector(".t").textContent = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
    b.querySelector(".o").textContent = seg.original;
    b.querySelector(".o").lang = lang;
    b.querySelector(".j").textContent = seg.translation || "";
    b.onclick = () => open(id, "at", i);
    li.append(b);
    ol.append(li);
  });
  show("episode");
  const here = ol.querySelector(".here");
  if (here) here.scrollIntoView({ block: "center" });
  else if (SPLIT) $("episode").scrollTop = 0;
  else window.scrollTo(0, 0);
}

$("epDelete").onclick = async () => {
  const row = rowsById.get(episodeId);
  if (!row || !confirm(`Delete “${row.title}”?`)) return;
  await store.deleteMaterial(row.id);
  episodeId = null;
  show("home");
};
$("episodeBack").onclick = () => show("home");
$("openSettings").onclick = () => show("settings");
$("settingsBack").onclick = () => {
  if (SPLIT) rightPane = episodeId ? "episode" : null;
  show("home");
};

$("fileInput").addEventListener("change", async (e) => {
  const status = $("importStatus");
  status.className = "status";
  status.textContent = "Adding…";
  try {
    const { imported, errors } = await store.importFiles(e.target.files);
    const lines = imported.map((r) => `Added “${r.title}”` + (r.translationMismatch ? " (translations may not match)" : ""));
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
function open(id, how, index = 0) {
  const row = rowsById.get(id);
  if (!row) return;
  const token = ++opening;
  if (engine) { engine.pause(); engine = null; }
  unlockSpeech();
  unlockAudio();
  current = row;
  store.setLastMaterialId(id);
  setStatus("Loading…");
  show("player");
  store.getAudio(row).then(async (audio) => {
    if (store.needsRefine(row)) {
      setStatus("Aligning…");
      await store.ensureRefined(row, audio);
    }
    // 無音がまだ鳴っていれば止め終えてから始める（その pause を中断と取り違えないように）
    const el = $("audio");
    if (!el.paused) await new Promise((r) => { el.addEventListener("pause", r, { once: true }); el.pause(); setTimeout(r, 300); });
    if (token !== opening) return;
    start(row, audio, how, index);
  }).catch((err) => {
    if (token === opening) setStatus(err.message, true);
  });
}

function start(row, audio, how, index = 0) {
  const id = row.id;
  if (audioUrl) URL.revokeObjectURL(audioUrl);
  audioUrl = URL.createObjectURL(audio);
  const segIndex = how === "resume" ? resumeIndex(row.doc, store.loadPosition(id))
    : how === "at" ? Math.min(Math.max(0, index), row.doc.segments.length - 1) : 0;
  tidySpeech(row.doc);
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
  engine.addEventListener("interrupted", () => { savePos(); setStatus("Stopped. Tap to resume."); });
  engine.addEventListener("error", (e) => setStatus(e.detail.message, true));
  $("subtitle").lang = row.doc.sourceLanguage || "en";
  $("spoken").lang = row.doc.targetLanguage || "ja";
  setStatus(row.mode === "sprite" || settings.keepAwake ? "" : "Screen lock may stop playback");
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

// 読み上げの区切りをつないで表示する。言語が変わるところ（原語とその説明の間）には空白を入れて切れ目を見せる。
// 同じ言語の続き（日本語・中国語どうし）、句読点の前、開き括弧の後には空白を入れない
function joinForDisplay(parts) {
  let out = "", lastLang = null;
  for (const { lang, text } of parts) {
    const prev = out.slice(-1), next = text[0] || "";
    const tight = /[「『（]/.test(prev) || /[、。，」』）]/.test(next) || (lang === lastLang && /[^\x00-\x7F]/.test(prev + next));
    if (out && !tight) out += " ";
    out += text;
    lastLang = lang;
  }
  return out;
}

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
    const text = joinForDisplay(s.segment.parts.filter((p) => p.type === "speech" && p.group === step.group));
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

const SCREENS = { home: "home", episode: "episode", settings: "settingsScreen", player: "player" };
let rightPane = null;
function show(name) {
  if (SPLIT && name !== "player") {
    if (name === "episode" || name === "settings") rightPane = name;
    if (rightPane === "episode" && !episodeId) rightPane = null;
    $("home").hidden = false;
    $("episode").hidden = rightPane !== "episode";
    $("settingsScreen").hidden = rightPane !== "settings";
    $("paneEmpty").hidden = !!rightPane;
    $("player").hidden = true;
    renderLibrary();
    if (rightPane === "settings") renderSettings();
    return;
  }
  $("paneEmpty").hidden = true;
  for (const [key, id] of Object.entries(SCREENS)) $(id).hidden = key !== name;
  if (name === "home") renderLibrary();
  if (name === "settings") renderSettings();
  if (name !== "episode") window.scrollTo(0, 0);
}

// ---------------------------------------------------------------- 操作（ジェスチャー・キーボード）

// 操作したことは軽い振動だけで伝える（字幕を隠す表示は出さない）
const tick = () => { if (navigator.vibrate) navigator.vibrate(10); };

const actions = {
  toggle() { if (!engine) return; const was = engine.playing; if (!was) unlockSpeech(); engine.toggle(); tick(); },
  next() { if (!engine) return; engine.next(); tick(); },
  previous() { if (!engine) return; engine.previous(); tick(); },
  restart() { if (!engine) return; engine.restart(); tick(); },
};

$("back").onclick = () => {
  if (engine) { engine.pause(); savePos(); }
  if (SPLIT && episodeId) openEpisode(episodeId); // 止めた文の印を新しくする
  else show("home");
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
      artist: current.channel || "Listening",
      album: "Listening",
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
  $("importStatus").textContent = "Adding…";
  store.importFromUrl(params.get("import"))
    .then((row) => { $("importStatus").textContent = `Added “${row.title}”`; renderLibrary(); })
    .catch((err) => { $("importStatus").className = "status error"; $("importStatus").textContent = err.message; });
}
show("home");
if (SPLIT) {
  // 最後に聞いたエピソード（なければ最初のもの）を右に開いておく
  store.listMaterials().then((rows) => {
    const last = rows.find((r) => r.id === store.lastMaterialId()) || rows[0];
    if (last && !episodeId) { rowsById = new Map(rows.map((r) => [r.id, r])); openEpisode(last.id); }
  });
}
window.__appStarted = true; // index.html の起動確認用
try { sessionStorage.removeItem(window.__appResetKey || "listening.autoReset"); } catch { /* なくてもよい */ }

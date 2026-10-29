// 教材（変換したデータと音声）を端末内の IndexedDB に保存する。オフラインで再生できる。
// 設定と学習位置は localStorage に保存する。

import { buildMaterial } from "./material.js";
import { audioEnvelope, refineClips } from "./boundary.js";

const DB_NAME = "listening-mode";
const STORE = "materials";
const AUDIO = "audio";

// 音声は教材の行とは別に、ArrayBuffer で保存する。
// iPhone の Safari（特にホーム画面のアプリ）は大きな Blob/File を IndexedDB に保存できないことがある
// （"Error preparing Blob/File data to be stored in object store"）。ArrayBuffer なら保存できる。
function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 2);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: "id" });
      if (!db.objectStoreNames.contains(AUDIO)) db.createObjectStore(AUDIO, { keyPath: "id" });
    };
    req.onsuccess = () => {
      // 新しい版が保存形式を更新するときに邪魔をしない
      req.result.onversionchange = () => req.result.close();
      resolve(req.result);
    };
    req.onerror = () => reject(req.error);
    req.onblocked = () => reject(new Error("ほかのタブで開いているこのアプリを閉じてから、もう一度試してください"));
  });
}

async function tx(stores, mode, fn) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const t = db.transaction(stores, mode);
    const result = fn(...[].concat(stores).map((n) => t.objectStore(n)));
    t.oncomplete = () => resolve(result && "result" in result ? result.result : result);
    t.onerror = () => reject(t.error);
    // 容量不足などで中止されたときも、待ち続けずに失敗を返す
    t.onabort = () => reject(t.error || new Error("この端末に保存できませんでした（空き容量が足りないかもしれません）"));
  });
}

// 一覧用。音声は読み込まないので、たくさんあっても軽い。
export const listMaterials = () =>
  tx(STORE, "readonly", (s) => s.getAll()).then((rows) => rows.sort((a, b) => b.addedAt - a.addedAt));
export const getMaterial = (id) => tx(STORE, "readonly", (s) => s.get(id));
export const deleteMaterial = (id) => tx([STORE, AUDIO], "readwrite", (s, a) => { s.delete(id); a.delete(id); });

async function saveMaterial(row, audioFile, data) {
  data = data || await audioFile.arrayBuffer();
  const type = audioFile.type || guessType(audioFile.name);
  try {
    await tx([STORE, AUDIO], "readwrite", (s, a) => { a.put({ id: row.id, data, type }); s.put(row); });
  } catch (err) {
    throw new Error("Could not save the audio on this device: " + (err && err.message || err));
  }
  return row;
}

// 文の切れ目を元音声の実際の切れ目に合わせる（boundary.js）。取り込み時に一度だけ行う
const REFINE_VERSION = 1;
async function refineDoc(doc, data) {
  let stats = null;
  try {
    const env = await audioEnvelope(data);
    // 単語ごとの時刻があれば近くだけ、文ごとの時刻しかなければ広く探す
    if (env) stats = refineClips(doc.segments, env, doc.sources.durationSeconds, { search: doc.timingDetail === "word" ? 0.35 : 0.8 });
  } catch { /* 音声を調べられなければ時刻どおり */ }
  doc.clipsRefined = { version: REFINE_VERSION, ...(stats || { skipped: true }) };
}

export const needsRefine = (row) => row.mode === "direct" && !(row.doc.clipsRefined && row.doc.clipsRefined.version >= REFINE_VERSION);

// 以前の版で取り込んだ教材は、最初に開いたときに合わせて保存する
export async function ensureRefined(row, audio) {
  if (!needsRefine(row)) return;
  await refineDoc(row.doc, await audio.arrayBuffer());
  try { await tx(STORE, "readwrite", (s) => s.put(row)); } catch { /* 保存できなくても今回は使える */ }
}

// 再生用の音声。以前の版で行に Blob として保存した教材もそのまま使う。
export async function getAudio(row) {
  if (row.audio instanceof Blob) return row.audio;
  const rec = await tx(AUDIO, "readonly", (a) => a.get(row.id));
  if (!rec) throw new Error("このエピソードの音声が見つかりません。削除してから、もう一度追加してください");
  return new Blob([rec.data], { type: rec.type || "audio/mpeg" });
}

export async function sha256(blob) {
  const buf = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// 選んだファイル群から教材を作る。複数の回をまとめて選べる。
// 同じ名前のファイルを1つの教材として組にする:
//   既存の教材   元の名前.mp3 ＋ 元の名前.json（＋ 元の名前.ttml ＋ 元の名前.fix.json）
//   準備済みの教材 エピソード名.listening.json ＋ エピソード名.listening.m4a
// 返り値: { imported: [行], errors: [文] }
const AUDIO_EXT = /\.(mp3|m4a|aac|wav)$/i;

// 準備ツールの中間ファイルと、訂正前の元ファイルの控え。選ばれても黙って使わない
const SIDE_FILE = /\.(retranslated|listening-authored|before-fix)\.json$/i;

function kindOf(name) {
  if (/\.listening\.json$/i.test(name)) return "listening";
  if (/\.fix\.json$/i.test(name)) return "fix";
  if (SIDE_FILE.test(name)) return null;
  if (/\.json$/i.test(name)) return "json";
  if (/\.(ttml|xml)$/i.test(name)) return "ttml";
  if (AUDIO_EXT.test(name)) return "audio";
  return null;
}

const stemOf = (name) => normalizeName(name.replace(/(\.listening|\.fix)?\.(json|ttml|xml|mp3|m4a|aac|wav)$/i, ""));

export async function importFiles(fileList) {
  const groups = new Map();
  const errors = [];
  for (const f of fileList) {
    const kind = kindOf(f.name);
    if (!kind) {
      if (!SIDE_FILE.test(f.name)) errors.push(`${f.name}: .mp3・.json・.ttml ではないので追加しませんでした`);
      continue;
    }
    const key = stemOf(f.name);
    if (!groups.has(key)) groups.set(key, {});
    groups.get(key)[kind] = f;
  }
  // 名前が違っていても、JSON と音声が1つずつだけなら組にする
  const all = [...groups.values()];
  const jsons = all.filter((g) => g.json), audios = all.filter((g) => g.audio);
  if (jsons.length === 1 && audios.length === 1 && !jsons[0].audio) {
    Object.assign(jsons[0], { audio: audios[0].audio }, audios[0].ttml && !jsons[0].ttml ? { ttml: audios[0].ttml } : {});
    groups.delete([...groups.entries()].find(([, g]) => g === audios[0])[0]);
  }
  const imported = [];
  for (const g of groups.values()) {
    const label = (g.json || g.listening || g.audio || g.ttml || g.fix).name;
    try {
      if (g.listening) {
        const doc = JSON.parse(await g.listening.text());
        imported.push(await importDoc(doc, async (name) => (g.audio && (g.audio.name === name || normalizeName(g.audio.name) === normalizeName(name)) ? g.audio : null), g.audio || null));
      } else if (g.json && g.audio) {
        imported.push(await importSources(g));
      } else if (g.json || g.ttml || g.fix) {
        errors.push(`${label}: 同じ名前の音声ファイル（.mp3）も一緒に選んでください`);
      } else {
        errors.push(`${label}: also select the JSON file with the same name`);
      }
    } catch (err) {
      errors.push(`${label}: ${err.message}`);
    }
  }
  if (!imported.length && !errors.length) errors.push("エピソードの .mp3 と .json（あれば .ttml も）を選んでください");
  return { imported, errors };
}

// 既存の音声・JSON・原稿（TTML）・訳の訂正データをそのまま使う
async function importSources(g) {
  const json = JSON.parse(await g.json.text());
  if (json.format === "podcast-listening") throw new Error("this is a prepared file; select it with its .listening.m4a");
  const doc = buildMaterial({
    json,
    ttml: g.ttml ? await g.ttml.text() : undefined,
    fix: g.fix ? JSON.parse(await g.fix.text()) : undefined,
    name: g.json.name,
  });
  doc.sources.audio = g.audio.name;
  const data = await g.audio.arrayBuffer();
  await refineDoc(doc, data);
  const row = {
    id: doc.materialId,
    title: doc.episode || doc.title,
    channel: doc.channel || "",
    mode: "direct",
    segments: doc.segments.length,
    hasTtml: !!g.ttml,
    translationMismatch: doc.translationMismatch,
    addedAt: Date.now(),
    doc,
  };
  return saveMaterial(row, g.audio, data);
}

// URL から取り込む（音声・TTML・訂正データは JSON と同じ場所の同じ名前のものを探す）
export async function importFromUrl(jsonUrl) {
  const res = await fetch(jsonUrl);
  if (!res.ok) throw new Error("Could not fetch JSON: " + res.status);
  const doc = await res.json();
  const base = new URL(jsonUrl, location.href);
  const fetchFile = async (name) => {
    const r = await fetch(new URL(encodeURIComponent(name).replace(/%2F/g, "/"), base));
    return r.ok ? new File([await r.blob()], name, { type: r.headers.get("content-type") || "" }) : null;
  };
  if (doc.format === "podcast-listening") return importDoc(doc, fetchFile);
  const name = decodeURIComponent(base.pathname.split("/").pop());
  const stem = name.replace(/\.json$/i, "");
  const audio = (await fetchFile(stem + ".mp3")) || (await fetchFile(stem + ".m4a"));
  if (!audio) throw new Error("No audio next to " + name);
  const g = { json: new File([JSON.stringify(doc)], name), audio,
    ttml: await fetchFile(stem + ".ttml"), fix: await fetchFile(stem + ".fix.json") };
  return importSources(g);
}

function normalizeName(n) {
  return n.normalize("NFC").replace(/[’']/g, "'").replace(/\s+/g, " ").toLowerCase();
}

async function importDoc(doc, findFile, onlyAudio = null) {
  let mode = null;
  let audio = null;
  const sprite = doc.audio && doc.audio.sprite;
  if (sprite) {
    audio = await findFile(sprite.file);
    if (audio) mode = "sprite";
  }
  if (!audio && doc.sources && doc.sources.audio) {
    audio = await findFile(doc.sources.audio.file);
    if (audio) mode = "direct";
  }
  if (!audio && onlyAudio) {
    // ファイル名が変わっていても、内容（SHA-256）が一致すれば使う
    const h = await sha256(onlyAudio);
    if (sprite && h === sprite.sha256) { audio = onlyAudio; mode = "sprite"; }
    else if (doc.sources && doc.sources.audio && h === doc.sources.audio.sha256) { audio = onlyAudio; mode = "direct"; }
    else throw new Error("The selected audio does not match this JSON");
  }
  if (!audio) {
    const want = [sprite && sprite.file, doc.sources && doc.sources.audio && doc.sources.audio.file].filter(Boolean);
    throw new Error("音声ファイルも一緒に選んでください: " + want.join(" または "));
  }
  const expected = mode === "sprite" ? sprite.sha256 : doc.sources.audio.sha256;
  if (expected && (await sha256(audio)) !== expected) {
    throw new Error("この JSON を作ったときの音声と違うため追加しませんでした（文の位置がずれるため）");
  }
  const row = {
    id: doc.materialId,
    title: doc.episode || doc.title,
    channel: doc.channel || "",
    mode,
    segments: doc.segments.length,
    addedAt: Date.now(),
    doc,
  };
  return saveMaterial(row, audio);
}

function guessType(name) {
  name = name.toLowerCase();
  if (name.endsWith(".m4a")) return "audio/mp4";
  if (name.endsWith(".mp3")) return "audio/mpeg";
  if (name.endsWith(".wav")) return "audio/wav";
  return "";
}

// ---------------------------------------------------------------- 設定・位置

const SETTINGS_KEY = "listening.settings.v1";

export function loadSettings(defaults) {
  try {
    return { ...defaults, ...JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}") };
  } catch {
    return { ...defaults };
  }
}

export function saveSettings(s) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch { /* 保存できなくても再生は続ける */ }
}

export function loadPosition(id) {
  try {
    return JSON.parse(localStorage.getItem("listening.pos." + id) || "null");
  } catch {
    return null;
  }
}

export function savePosition(id, pos) {
  try { localStorage.setItem("listening.pos." + id, JSON.stringify({ ...pos, savedAt: Date.now() })); } catch { /* 同上 */ }
}

export function lastMaterialId() {
  try { return localStorage.getItem("listening.last"); } catch { return null; }
}

export function setLastMaterialId(id) {
  try { localStorage.setItem("listening.last", id); } catch { /* 同上 */ }
}

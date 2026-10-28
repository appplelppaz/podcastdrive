// 教材（.listening.json と音声）を端末内の IndexedDB に保存する。オフラインで再生できる。
// 設定と学習位置は localStorage に保存する。

const DB_NAME = "listening-mode";
const STORE = "materials";

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "id" });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx(mode, fn) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode);
    const result = fn(t.objectStore(STORE));
    t.oncomplete = () => resolve(result && "result" in result ? result.result : result);
    t.onerror = () => reject(t.error);
  });
}

export const listMaterials = () =>
  tx("readonly", (s) => s.getAll()).then((rows) =>
    rows.map(({ id, title, channel, mode, segments, addedAt }) => ({ id, title, channel, mode, segments, addedAt }))
      .sort((a, b) => b.addedAt - a.addedAt));
export const getMaterial = (id) => tx("readonly", (s) => s.get(id));
export const putMaterial = (row) => tx("readwrite", (s) => s.put(row));
export const deleteMaterial = (id) => tx("readwrite", (s) => s.delete(id));

export async function sha256(blob) {
  const buf = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// 選んだファイル群から教材を作る。
// .listening.json と、そこに記録された再生用音声（スプライト）か元音声の組を探す。
export async function importFiles(fileList) {
  const files = [...fileList];
  const jsonFile = files.find((f) => f.name.endsWith(".listening.json")) || files.find((f) => f.name.endsWith(".json"));
  if (!jsonFile) throw new Error("Select the .listening.json file together with its audio");
  const doc = JSON.parse(await jsonFile.text());
  if (doc.format !== "podcast-listening") {
    throw new Error("This is not a listening-mode JSON. Create one with tools/prepare_listening.py");
  }
  const audios = files.filter((f) => f !== jsonFile);
  return importDoc(doc, async (name) => audios.find((f) => f.name === name) ||
    audios.find((f) => normalizeName(f.name) === normalizeName(name)) || null,
  audios.length === 1 ? audios[0] : null);
}

// URL から取り込む（音声は JSON と同じ場所にあるものとする）
export async function importFromUrl(jsonUrl) {
  const res = await fetch(jsonUrl);
  if (!res.ok) throw new Error("Could not fetch JSON: " + res.status);
  const doc = await res.json();
  return importDoc(doc, async (name) => {
    const r = await fetch(new URL(encodeURIComponent(name).replace(/%2F/g, "/"), new URL(jsonUrl, location.href)));
    return r.ok ? new File([await r.blob()], name, { type: r.headers.get("content-type") || "" }) : null;
  });
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
    throw new Error("Also select the audio file: " + want.join(" or "));
  }
  const expected = mode === "sprite" ? sprite.sha256 : doc.sources.audio.sha256;
  if (expected && (await sha256(audio)) !== expected) {
    throw new Error("The audio differs from the one this JSON was made for (sentences would be misaligned), so it was not imported");
  }
  const row = {
    id: doc.materialId,
    title: doc.episode || doc.title,
    channel: doc.channel || "",
    mode,
    segments: doc.segments.length,
    addedAt: Date.now(),
    doc,
    audio: new Blob([await audio.arrayBuffer()], { type: audio.type || guessType(audio.name) }),
  };
  await putMaterial(row);
  return row;
}

function guessType(name) {
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

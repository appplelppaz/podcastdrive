// 既存の教材ファイル（JSON・TTML・訳の訂正データ）を、そのまま聞き流し用のデータに変換する。
// 事前の準備は要らない。アプリが取り込むときにこの変換を行い、端末に保存する。
//
// tools/listening（Python）の変換と同じ規則・同じ結果になるようにしている
// （tools/tests/test_listening.py の MaterialParityTest で突き合わせる）。
//   - 時刻は JSON の begin/end を使い、TTML の単語の境界が 60ms 以内にあるときだけそれに合わせる
//   - 文の途中で切れた区間は次とまとめる。訂正データに文ごとの訳があり TTML の文境界と合うときだけ分ける
//   - 読み上げ文: 訳、語彙（最大2つ「語 → この文での意味」）、文法・背景（1文）。記号は話し言葉に直す
// DOM を使わないので、ブラウザでも Node でも動く。

const LATIN_LANGS = new Set(["en", "es", "fr", "de", "it", "pt", "nl", "id", "vi", "tr", "pl", "sv"]);
const PHRASE_POS = ["熟語", "句", "慣用", "表現", "idiom", "phrase", "expression", "collocation"];
const SKIP_POS = ["固有名詞", "proper", "人名", "地名"];
const CUE_TEXTS = { translation: "訳", vocab: "表現", grammar: "解説", again: "もう一度" };
const JA_CHARS_PER_SEC = 7.5;
const WORDS_PER_SEC = 2.6;
const MAX_MERGED_SECONDS = 30;
const MAX_EXPLANATION_SECONDS = 20;

const round3 = (x) => Math.round(x * 1000) / 1000;
const round1 = (x) => Math.round(x * 10) / 10;
export const langBase = (lang) => (lang || "").split("-")[0].split("_")[0].toLowerCase();
const norm = (t) => t.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
const strip = (t, chars) => {
  let a = 0, b = t.length;
  while (a < b && chars.includes(t[a])) a++;
  while (b > a && chars.includes(t[b - 1])) b--;
  return t.slice(a, b);
};

// ---------------------------------------------------------------- 時刻

export function parseTime(value) {
  if (value === null || value === undefined) throw new Error("time is missing");
  if (typeof value === "number") return value;
  const s = String(value).trim().replace(/,/g, ".");
  const m = s.match(/^([\d.]+)(h|m|s|ms)$/);
  if (m) return Number(m[1]) * { h: 3600, m: 60, s: 1, ms: 0.001 }[m[2]];
  let total = 0;
  for (const part of s.split(":")) {
    if (part === "" || !/^[\d.]+$/.test(part)) throw new Error("bad time: " + value);
    total = total * 60 + Number(part);
  }
  return total;
}

// ---------------------------------------------------------------- 既存JSON

export function loadEpisode(raw) {
  const warnings = [];
  let meta, segs;
  if (Array.isArray(raw)) {
    meta = {};
    segs = raw;
    warnings.push("トップレベルが配列のため metadata なしとして読み込みました");
  } else {
    meta = raw.metadata || {};
    segs = raw.segments;
    if (!segs) {
      for (const key of ["sentences", "items", "cues"]) {
        if (Array.isArray(raw[key])) { segs = raw[key]; warnings.push(`segments の代わりに ${key} を使用しました`); break; }
      }
    }
    if (!segs) throw new Error("JSON に segments がありません");
  }
  const out = [];
  segs.forEach((s, i) => {
    const id = s.id ?? i + 1;
    let begin, end;
    try {
      begin = parseTime(s.begin ?? s.start);
      end = parseTime(s.end ?? s.stop);
    } catch (e) {
      warnings.push(`区間 ${id} の時刻を読めないため除外: ${e.message}`);
      return;
    }
    if (end <= begin) { warnings.push(`区間 ${id} は end<=begin のため除外`); return; }
    const words = [];
    for (const w of s.words || []) {
      if (typeof w === "string") { words.push({ word: w, meaning: "", pos: "" }); continue; }
      const entry = {
        word: String(w.word || w.term || "").trim(),
        meaning: String(w.meaning || w.ja || "").trim(),
        pos: String(w.pos || "").trim(),
      };
      if (w.reading) entry.reading = String(w.reading).trim();
      if (entry.word) words.push(entry);
    }
    let grammar = s.grammar || "";
    if (Array.isArray(grammar)) grammar = grammar.filter(Boolean).map(String).join("\n");
    const seg = {
      id, begin, end,
      original: String(s.original || s.text || "").trim(),
      translation: String(s.translation || "").trim(),
      words, grammar: String(grammar).trim(),
    };
    if (s.pinyin) seg.pinyin = String(s.pinyin).trim();
    if (Array.isArray(s.wordTimings)) {
      try {
        seg.wordTimings = s.wordTimings.filter((w) => w.length >= 3).map((w) => [parseTime(w[0]), parseTime(w[1]), String(w[2]).trim()]);
      } catch {
        warnings.push(`区間 ${id} の wordTimings を読めないため使いません`);
      }
    }
    if (Array.isArray(s.translationParts)) seg.translationParts = s.translationParts.map((t) => String(t).trim());
    out.push(seg);
  });
  out.sort((a, b) => a.begin - b.begin);
  return { metadata: meta, segments: out, warnings };
}

// ---------------------------------------------------------------- TTML（Apple Podcasts / Whisper）

const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };
const decode = (t) => t.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (m, e) =>
  e[0] === "#" ? String.fromCodePoint(e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : Number(e.slice(1)))
    : ENTITIES[e] ?? m);

function attrs(s) {
  const out = {};
  for (const m of s.matchAll(/([\w:.-]+)\s*=\s*("([^"]*)"|'([^']*)')/g)) out[m[1]] = decode(m[3] ?? m[4]);
  return out;
}

// XML を要素の木にする（TTML 程度の単純な XML 用）
function parseXml(text) {
  const root = { name: "#root", attrs: {}, children: [] };
  const stack = [root];
  const re = /<!--[\s\S]*?-->|<\?[\s\S]*?\?>|<!\[CDATA\[([\s\S]*?)\]\]>|<(\/?)([\w:.-]+)([^>]*?)(\/?)>|([^<]+)/g;
  for (const m of text.matchAll(re)) {
    const top = stack[stack.length - 1];
    if (m[1] !== undefined) top.children.push(m[1]);
    else if (m[3]) {
      if (m[2]) { if (stack.length > 1) stack.pop(); continue; }
      const el = { name: m[3], attrs: attrs(m[4]), children: [] };
      top.children.push(el);
      if (!m[5]) stack.push(el);
    } else if (m[6] !== undefined) top.children.push(decode(m[6]));
  }
  return root;
}

const localName = (n) => n.split(":").pop();
const textOf = (el) => el.children.map((c) => (typeof c === "string" ? c : textOf(c))).join("");
function* walk(el) {
  for (const c of el.children) if (typeof c !== "string") { yield c; yield* walk(c); }
}
const unitOf = (el) => {
  for (const [k, v] of Object.entries(el.attrs)) if (localName(k) === "unit") return v;
  return null;
};

export function loadTtml(text) {
  const root = parseXml(text);
  const tt = [...walk(root)].find((e) => localName(e.name) === "tt");
  const lang = tt ? tt.attrs["xml:lang"] || "" : "";
  const body = [...walk(root)].find((e) => localName(e.name) === "body");
  const duration = body && body.attrs.dur ? parseTime(body.attrs.dur) : null;
  const words = [], sentences = [];
  for (const el of walk(root)) {
    const unit = unitOf(el);
    if (!el.attrs.begin || !el.attrs.end) continue;
    if (unit === "word") {
      words.push([parseTime(el.attrs.begin), parseTime(el.attrs.end), textOf(el).trim()]);
    } else if (unit === "sentence") {
      let t = el.children.filter((c) => typeof c !== "string" && unitOf(c) === "word").map((c) => textOf(c).trim()).join(" ");
      if (!t) t = textOf(el).trim();
      sentences.push([parseTime(el.attrs.begin), parseTime(el.attrs.end), t]);
    }
  }
  if (!sentences.length) {
    for (const el of walk(root)) {
      if (localName(el.name) === "p" && el.attrs.begin && el.attrs.end) {
        sentences.push([parseTime(el.attrs.begin), parseTime(el.attrs.end), textOf(el).trim()]);
      }
    }
  }
  const cmp = (a, b) => a[0] - b[0] || a[1] - b[1] || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0);
  words.sort(cmp);
  sentences.sort(cmp);
  return { lang, duration, words, sentences };
}

function alignSegment(seg, ttml, tol = 0.06) {
  const info = { method: "json" };
  let { begin, end } = seg;
  if (!ttml) return [begin, end, info];
  const words = ttml.words;
  if (words.length) {
    let wb = words[0], we = words[0];
    for (const w of words) {
      if (Math.abs(w[0] - begin) < Math.abs(wb[0] - begin)) wb = w;
      if (Math.abs(w[1] - end) < Math.abs(we[1] - end)) we = w;
    }
    if (Math.abs(wb[0] - begin) <= tol && Math.abs(we[1] - end) <= tol && wb[0] < we[1]) {
      begin = wb[0];
      end = we[1];
      info.method = "ttml-words";
      const inside = words.filter((w) => w[0] >= begin - 1e-3 && w[1] <= end + 1e-3);
      info.wordCount = inside.length;
      info.textMatches = norm(inside.map((w) => w[2]).join(" ")) === norm(seg.original);
    }
  }
  const starts = ttml.sentences.filter((s) => begin - 1e-3 <= s[0] && s[0] < end - 1e-3);
  if (starts.length) info.ttmlSentences = starts.length;
  return [begin, end, info];
}

function splitByTtmlSentences(seg, ttml) {
  const parts = seg.translationParts;
  if (!parts || !parts.length || !ttml) return null;
  const sents = ttml.sentences.filter((s) => s[0] >= seg.begin - 1e-3 && s[1] <= seg.end + 1e-3);
  if (sents.length !== parts.length || sents.length < 2) return null;
  if (norm(sents.map((s) => s[2]).join(" ")) !== norm(seg.original)) return null;
  return sents.map(([b, e, text], k) => {
    const words = seg.words.filter((w) => norm(w.word) && norm(text).includes(norm(w.word)));
    const piece = { ...seg, begin: b, end: e, original: text, translation: parts[k], words, grammar: k === 0 ? seg.grammar : "", part: k + 1 };
    delete piece.translationParts;
    return piece;
  });
}

const isSentenceEnd = (t) => /[.!?。！？…‼⁇]["'”’»」』）)\]]*$/.test(t.trim());

// ---------------------------------------------------------------- 読み上げ文

export function cleanJa(text) {
  let t = text;
  t = t.replace(/【訂正】[^\n]*/g, "");
  t = t.replace(/[「」『』"“”]/g, "");
  t = t.replace(/[（(]([^（）()]*)[）)]/g, "、$1、");
  t = t.replace(/\s*[／/]\s*/g, "、");
  t = t.replace(/[〜～]/g, "");
  t = t.replace(/\s*(?:…+|\.{2,})\s*/g, "、");
  t = t.replace(/\s*[—–―]+\s*/g, "、");
  t = t.replace(/\s*[＝=→⇒]\s*/g, "、");
  t = t.replace(/\s*[＋+]\s*/g, "と");
  t = t.replace(/[*＊#＃_＿|｜<>＜＞[\]［］{}｛｝]/g, "");
  t = t.replace(/\s*[:：;；]\s*/g, "、");
  t = t.replace(/\s+/g, " ");
  t = t.replace(/(、\s*)+/g, "、");
  t = t.replace(/、([。？！])/g, "$1");
  return strip(t, " 、");
}

export function cleanTerm(term) {
  let t = term;
  t = t.replace(/\bsb\b/g, "somebody");
  t = t.replace(/\bsth\b/g, "something");
  t = t.replace(/[（(]([^（）()]*)[）)]/g, "$1");
  t = t.replace(/\s*[／/]\s*/g, ", ");
  t = t.replace(/\s*(?:…+|\.{2,}|[〜～])\s*/g, " ");
  t = t.replace(/[[\]{}<>「」『』"“”*]/g, "");
  return strip(t.replace(/\s+/g, " "), " ,");
}

function splitPlain(text, src, tgt) {
  if (!LATIN_LANGS.has(langBase(src))) return [[tgt, text]];
  const out = [];
  let pos = 0;
  const re = /[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’-]*(?:[ ,]+[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’-]*)*/g;
  for (const m of text.matchAll(re)) {
    if (m[0].replace(/ /g, "").length < 2) continue;
    if (m.index > pos) out.push([tgt, text.slice(pos, m.index)]);
    out.push([src, strip(m[0], " ,")]);
    pos = m.index + m[0].length;
  }
  if (pos < text.length) out.push([tgt, text.slice(pos)]);
  return out;
}

export function splitRuns(text, src, tgt = "ja") {
  const runs = [];
  let pos = 0;
  for (const m of text.matchAll(/\[\[(.+?)\]\]/g)) {
    if (m.index > pos) runs.push(...splitPlain(text.slice(pos, m.index), src, tgt));
    runs.push([src, m[1].trim()]);
    pos = m.index + m[0].length;
  }
  if (pos < text.length) runs.push(...splitPlain(text.slice(pos), src, tgt));
  const merged = [];
  for (let [lang, t] of runs) {
    t = t.trim();
    if (!t || /^[\s、。,.!?！？]+$/.test(t)) {
      if (merged.length && t.trim()) merged[merged.length - 1][1] += t.trim();
      continue;
    }
    const last = merged[merged.length - 1];
    if (last && last[0] === lang) last[1] += (lang !== tgt ? " " : "") + t;
    else merged.push([lang, t]);
  }
  return merged;
}

export function pickMeaning(meaning, translation) {
  const cands = meaning.split(/[、，,；;／/]/).map((c) => c.trim()).filter(Boolean);
  if (!cands.length) return "";
  for (const c of cands) {
    const core = c.replace(/[（(][^（）()]*[）)]|[〜～]/g, "");
    const stem = core.replace(/(している|する|した|される|な|の|い|だ)$/, "");
    if ((core && translation.includes(core)) || ([...stem].length >= 2 && translation.includes(stem))) return c;
  }
  return cands[0];
}

function rankWords(words) {
  const usable = words.map((w, i) => [i, w])
    .filter(([, w]) => w.meaning && !SKIP_POS.some((p) => (w.pos || "").toLowerCase().includes(p)));
  const score = ([, w]) => {
    const pos = (w.pos || "").toLowerCase();
    let s = 0;
    if (PHRASE_POS.some((p) => pos.includes(p)) || w.word.trim().includes(" ") || w.word.includes("-")) s += 10;
    return s + Math.min([...w.word].length, 14) / 2;
  };
  return usable.sort((a, b) => score(b) - score(a) || a[0] - b[0]).map(([, w]) => w);
}

function autoGrammar(text) {
  let t = (text || "").replace(/【訂正】[^\n]*/g, "").trim();
  if (!t) return "";
  t = t.split("\n")[0];
  if (/\s[—–―]\s/.test(t)) t = t.slice(t.search(/\s[—–―]\s/)).replace(/^\s[—–―]\s/, "");
  // 後読み (?<=…) は iOS 16.4 より前の Safari で構文エラーになり、アプリ全体が動かなくなるので使わない
  const sentences = (t.match(/[^。！？]*[。！？]|[^。！？]+/g) || []).filter((s) => s.trim());
  if (!sentences.length) return "";
  let s = sentences[0].trim();
  if (sentences.length > 1 && [...s].length <= 12) s = s.replace(/。+$/, "") + "で、" + sentences[1].trim();
  s = cleanJa(s);
  if (s && !/[。？！]$/.test(s)) s += "。";
  return s;
}

function autoListening(seg) {
  const vocab = [];
  for (const w of rankWords(seg.words || []).slice(0, 2)) {
    const meaning = cleanJa(pickMeaning(w.meaning, seg.translation || ""));
    const term = cleanTerm(w.word);
    if (term && meaning) vocab.push({ term, meaning, note: "" });
  }
  return { source: "auto", vocab, grammar: autoGrammar(seg.grammar) };
}

const vocabRuns = (item, src, tgt) => [
  [src, item.term],
  [tgt, item.meaning.replace(/。+$/, "") + "。"],
  ...(item.note ? splitRuns(item.note, src, tgt) : []),
];

function estimateSeconds(runs, tgt) {
  let total = 0;
  for (const [lang, text] of runs) {
    total += lang === tgt ? [...text.replace(/\s/g, "")].length / JA_CHARS_PER_SEC
      : Math.max(1, text.split(/\s+/).filter(Boolean).length) / WORDS_PER_SEC;
  }
  return total;
}

function explanationSeconds(li, src, tgt) {
  const runs = [];
  if (li.vocab && li.vocab.length) {
    runs.push([tgt, CUE_TEXTS.vocab]);
    for (const item of li.vocab) runs.push(...vocabRuns(item, src, tgt));
  }
  if (li.grammar) {
    runs.push([tgt, CUE_TEXTS.grammar]);
    runs.push(...splitRuns(li.grammar, src, tgt));
  }
  return estimateSeconds(runs, tgt);
}

function fitBudget(li, src, tgt) {
  const notes = [];
  if (explanationSeconds(li, src, tgt) <= MAX_EXPLANATION_SECONDS) return notes;
  for (const item of li.vocab || []) if (item.note) { item.note = ""; notes.push("補足を省略"); }
  if (explanationSeconds(li, src, tgt) > MAX_EXPLANATION_SECONDS && (li.vocab || []).length > 1) {
    notes.push(`語彙 ${li.vocab.pop().term} を省略`);
  }
  return notes;
}

function buildParts(translation, li, src, tgt) {
  const speech = (group, lang, text) => ({ type: "speech", group, lang, text });
  const parts = [{ type: "original", role: "first" }];
  if (translation) {
    parts.push({ type: "cue", cue: "translation", group: "translation" });
    parts.push(speech("translation", tgt, cleanJa(translation)));
  }
  if (li.vocab && li.vocab.length) {
    parts.push({ type: "cue", cue: "vocab", group: "vocab" });
    for (const item of li.vocab) for (const [lang, text] of vocabRuns(item, src, tgt)) parts.push(speech("vocab", lang, text));
  }
  if (li.grammar) {
    parts.push({ type: "cue", cue: "grammar", group: "grammar" });
    for (const [lang, text] of splitRuns(li.grammar, src, tgt)) parts.push(speech("grammar", lang, text));
  }
  parts.push({ type: "cue", cue: "again", group: "again" });
  parts.push({ type: "original", role: "second" });
  return parts;
}

// ---------------------------------------------------------------- 訳の訂正データ（.fix.json）

// 既存JSONに訂正データを当てる（元のJSONの内容は変えず、新しいオブジェクトを返す）
export function applyFix(raw, fix) {
  if (!fix || fix.format !== "podcast-listening-fix") throw new Error("訂正データの形式が違います");
  const entries = fix.segments || {};
  const stripMarks = (t) => t.replace(/\[\[(.+?)\]\]/g, "$1");
  const authored = {};
  const segments = (raw.segments || []).map((orig) => {
    const seg = { ...orig };
    const keys = Object.keys(entries).filter((k) => k.split("#")[0] === String(seg.id))
      .sort((a, b) => (Number(a.split("#")[1]) || 0) - (Number(b.split("#")[1]) || 0));
    if (!keys.length) return seg;
    const parts = keys.map((k) => entries[k]);
    if (parts[0].translation !== null && parts[0].translation !== undefined) {
      if (parts.length > 1) {
        seg.translationParts = parts.map((p) => p.translation);
        seg.translation = seg.translationParts.join("");
      } else {
        seg.translation = parts[0].translation;
      }
      seg.words = parts.flatMap((p) => (p.vocab || []).map(([word, meaning, pos]) => ({ word, meaning, pos })));
      seg.grammar = parts.flatMap((p) => [p.fix || "", stripMarks(p.grammar || "")]).filter(Boolean).join("\n");
    }
    keys.forEach((k, i) => {
      const p = parts[i];
      const a = { vocab: (p.vocab || []).map(([term, meaning, , note]) => ({ term, meaning, note })), grammar: p.grammar || "" };
      if (p.translationSpeech) a.translationSpeech = p.translationSpeech;
      authored[k] = a;
    });
    return seg;
  });
  const metadata = { ...(raw.metadata || {}) };
  if (fix.note) metadata.note = fix.note;
  return { raw: { metadata, segments }, authored };
}

// ---------------------------------------------------------------- 変換

const FULLWIDTH = (t) => t.replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0));
function plainNumbers(text) {
  // Python の \b（Unicode の単語境界）に合わせる
  let t = FULLWIDTH(text).replace(/(\d)[.,  '](?=\d{3}(?![\p{L}\p{N}_]))/gu, "$1");
  return t.replace(/\d+(?:億|万)(?:\d+(?:万)?)*/g, (m) => {
    let total = 0;
    for (const [, num, unit] of m.matchAll(/(\d+)(億|万)?/g)) total += Number(num) * ({ 億: 1e8, 万: 1e4 }[unit] || 1);
    return String(total);
  });
}
function numbers(text) {
  const t = plainNumbers(text).replace(/\d+(?:\.\d+)?\s*(?:billion|million|trillion|thousand|bn|m|mil|millones|milliards?)(?![\p{L}\p{N}_])/giu, "");
  return new Set(t.match(/\d{2,}/g) || []);
}
function numbersConsistent(original, translation) {
  const nums = numbers(original);
  if (!nums.size) return true;
  const tr = new Set(plainNumbers(translation).match(/\d{2,}/g) || []);
  return [...nums].every((n) => tr.has(n));
}

function continues(prev, seg, lang) {
  const text = prev.original.trimEnd();
  if (isSentenceEnd(text)) return false;
  if (/[,，、;；:：]$/.test(text)) return true;
  return LATIN_LANGS.has(langBase(lang)) && /^[a-zà-ÿ]/.test(seg.original.trimStart());
}

function mergeFragments(segs, lang) {
  const out = [];
  for (const s of segs) {
    const prev = out[out.length - 1];
    if (prev && continues(prev, s, lang) && s.end - prev.begin <= MAX_MERGED_SECONDS) {
      const joiner = ["zh", "ja"].includes(langBase(lang)) ? "" : " ";
      prev.original += joiner + s.original;
      prev.translation += s.translation;
      prev.words = prev.words.concat(s.words);
      prev.grammar = [prev.grammar, s.grammar].filter(Boolean).join("\n");
      if (s.pinyin) prev.pinyin = ((prev.pinyin || "") + " " + s.pinyin).trim();
      prev.end = s.end;
      prev.sourceIds.push(s.id);
      continue;
    }
    out.push({ ...s, sourceIds: [s.id] });
  }
  return out;
}

function clipBounds(segs, i, words, duration) {
  const seg = segs[i];
  let prevEnd = i > 0 ? segs[i - 1].end : 0;
  let nextBegin = i + 1 < segs.length ? segs[i + 1].begin : duration || seg.end + 0.3;
  if (words.length) {
    const before = words.filter((w) => w[1] <= seg.begin + 1e-3).map((w) => w[1]);
    const after = words.filter((w) => w[0] >= seg.end - 1e-3).map((w) => w[0]);
    if (before.length) prevEnd = Math.max(prevEnd, before[before.length - 1]);
    if (after.length) nextBegin = Math.min(nextBegin, after[0]);
  }
  const lead = Math.max(0, Math.min(0.12, (seg.begin - prevEnd) / 2));
  const tail = Math.max(0, Math.min(0.25, (nextBegin - seg.end) / 2));
  let end = seg.end + tail;
  if (duration) end = Math.min(end, duration);
  return { begin: round3(Math.max(0, seg.begin - lead)), end: round3(end) };
}

// 区間の時間内にある単語（中国語などは文字）の時刻。どの文字に付けるかは表示側で照らし合わせる
function wordTimings(seg, words) {
  const inside = words.filter((w) => w[0] >= seg.begin - 1e-3 && w[1] <= seg.end + 1e-3);
  if (!inside.length) return null;
  return inside.map(([b, e, t]) => [round3(b), round3(e), t]);
}

const segmentKey = (s) => s.sourceIds.join("+") + (s.part ? "#" + s.part : "");

function hashString(s) {
  let h = 2166136261;
  for (const c of s) { h ^= c.codePointAt(0); h = Math.imul(h, 16777619); }
  return (h >>> 0).toString(16).padStart(8, "0");
}

// ファイル名「[番組] エピソード (YYYYMMDD)」から番組・エピソード・日付を読む
export function parseFileName(name) {
  const base = name.replace(/\.[^.]+$/, "");
  const m = base.match(/^\[(.+?)\]\s+(.+?)\s+\((\d{8})\)$/);
  return m ? { base, channel: m[1], episode: m[2], date: `${m[3].slice(0, 4)}-${m[3].slice(4, 6)}-${m[3].slice(6)}` }
    : { base, channel: "", episode: base, date: "" };
}

/**
 * 既存の教材ファイルから聞き流し用のデータを作る。
 * @param {object} p
 * @param {object} p.json   既存JSON（パース済み）
 * @param {string} [p.ttml] TTML の本文
 * @param {object} [p.fix]  訳の訂正データ（.fix.json、パース済み）
 * @param {string} p.name   元のファイル名（拡張子なしでもよい）
 */
export function buildMaterial({ json, ttml, fix, name }) {
  const file = parseFileName(name || "material");
  let raw = json;
  let authored = {};
  if (fix) ({ raw, authored } = applyFix(json, fix));
  const ep = loadEpisode(raw);
  const meta = ep.metadata;
  const warnings = [...ep.warnings];
  const tt = ttml ? loadTtml(ttml) : null;
  let src = meta.sourceLanguage || "";
  const tgt = meta.targetLanguage || "ja";
  if (tt && !src) src = tt.lang;
  if (!src) throw new Error("教材の言語が分かりません（JSON の metadata.sourceLanguage も TTML の xml:lang もありません）");
  if (tt && tt.lang && langBase(tt.lang) !== langBase(src)) {
    warnings.push(`metadata.sourceLanguage=${src} と TTML の xml:lang=${tt.lang} が異なります（metadata を採用）`);
  }
  const duration = tt && tt.duration ? tt.duration : null;

  let segs = ep.segments;
  for (const s of segs) {
    const [b, e, info] = alignSegment(s, tt);
    s.begin = b; s.end = e; s.alignment = info;
    if (info.textMatches === false) warnings.push(`区間 ${s.id}: 原文とTTMLの単語列が一致しません（時刻はJSONのまま）`);
  }
  const expanded = [];
  for (const s of segs) {
    const pieces = splitByTtmlSentences(s, tt);
    if (pieces) {
      for (const p of pieces) p.alignment = { ...s.alignment, method: "ttml-sentences", splitFrom: s.id };
      expanded.push(...pieces);
      continue;
    }
    if (s.translationParts) {
      warnings.push(`区間 ${s.id}: 文ごとの訳がありますが、TTMLの文境界と一致しないため分けません`);
      if (!s.translation) s.translation = s.translationParts.join("");
    }
    expanded.push(s);
  }
  segs = expanded;
  const suspicious = segs.filter((s) => !numbersConsistent(s.original, s.translation)).map((s) => s.id);
  const withNumbers = segs.filter((s) => numbers(s.original).size).length;
  let translationMismatch = false;
  if (suspicious.length) {
    let msg = "数字が訳に見当たらない区間: " + suspicious.slice(0, 20).join(", ");
    if (withNumbers && suspicious.length >= 3 && suspicious.length / withNumbers >= 0.3) {
      msg = "訳が原文とずれている可能性が高いです。" + msg;
      translationMismatch = true;
    }
    warnings.push(msg);
  }
  segs = mergeFragments(segs, src);

  // 単語の時刻: JSON に wordTimings があればそれを、なければ TTML の単語
  const cmp = (a, b) => a[0] - b[0] || a[1] - b[1] || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0);
  const jsonWords = ep.segments.flatMap((x) => x.wordTimings || []).sort(cmp);
  const words = jsonWords.length ? jsonWords : tt ? tt.words : [];
  const outSegs = segs.map((s, i) => {
    const key = segmentKey(s);
    let li;
    const a = authored[key];
    if (a) {
      li = { source: "authored", vocab: a.vocab.map((v) => ({ ...v })), grammar: a.grammar || "" };
      if (a.translationSpeech) li.translationSpeech = a.translationSpeech;
    } else {
      li = autoListening(s);
    }
    for (const v of li.vocab) if (v.note === undefined) v.note = "";
    if (li.vocab.length > 2) {
      warnings.push(`区間 ${s.id}: 語彙が3つ以上あるため先頭2つに絞りました`);
      li.vocab = li.vocab.slice(0, 2);
    }
    for (const n of fitBudget(li, src, tgt)) warnings.push(`区間 ${s.id}: 解説が長いため${n}`);
    li.estimatedSeconds = round1(explanationSeconds(li, src, tgt));
    const timings = wordTimings(s, words);
    const display = { words: s.words, grammar: s.grammar };
    if (s.pinyin) display.pinyin = s.pinyin;
    return {
      id: "s" + String(i + 1).padStart(3, "0"),
      key,
      sourceSegmentIds: s.sourceIds,
      ...(s.part ? { sourcePart: s.part } : {}),
      begin: round3(s.begin),
      end: round3(s.end),
      clip: clipBounds(segs, i, words, duration),
      alignment: s.alignment,
      ...(timings ? { wordTimings: timings } : {}),
      original: s.original,
      translation: s.translation,
      display,
      listening: li,
      parts: buildParts(li.translationSpeech || s.translation, li, src, tgt),
    };
  });

  const date = meta.publishDate || file.date;
  const cues = {};
  for (const [k, text] of Object.entries(CUE_TEXTS)) cues[k] = { lang: tgt, text };
  return {
    format: "podcast-listening",
    formatVersion: 1,
    materialId: [(meta.channel || file.channel || "material").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 24) || "material",
      date.replace(/-/g, ""), hashString(file.base)].filter(Boolean).join("-"),
    title: meta.title || file.base,
    channel: meta.channel || file.channel,
    episode: meta.episode || file.episode,
    publishDate: date,
    sourceLanguage: src,
    targetLanguage: tgt,
    sources: { name: file.base, ttml: !!tt, fix: !!fix, ...(duration ? { durationSeconds: duration } : {}) },
    translationMismatch,
    cues,
    segments: outSegs,
    warnings,
  };
}

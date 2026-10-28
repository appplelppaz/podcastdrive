// 文の切れ目を、元音声の実際の切れ目（声が途切れて静かになるところ）に合わせる。
//
// TTML の単語の時刻は、実際の声より少し内側に付いていることが多い（声は時刻より前から始まり、後まで続く）。
// また、続けて話している文どうしは、前の文の終わりと次の文の始まりが同じ時刻になっている。
// そのため時刻どおりに切ると、文の最初の音が欠けたり、前の文の最後の語が聞こえたりする。
// そこで元音声の音量を調べ、時刻の近くでいちばん静かなところで区切る。
// 静かなところが見つからなければ、元の時刻のまま（推測で大きく動かさない）。

export const ENV_RATE = 100; // 音量は 10ms ごと

const SEARCH = 0.35;     // 時刻の切れ目の前後を探す範囲（秒）
const MIN_QUIET = 0.06;  // これより短い静かなところ（語の中の子音の前など）は切れ目と見なさない
const LEAD = 0.08;       // 声の始まりの少し前から流す
const TAIL = 0.12;       // 声の終わりの少し後まで流す

const CHUNK_SECONDS = 120; // MP3 はこの長さずつ読む（長い回を一度に読むと iPhone のメモリが足りなくなる）
const OVERLAP_FRAMES = 4;  // 前の部分から少し重ねて読む（MP3 は前のフレームのデータを使うことがあるため）

// 音声ファイル（ArrayBuffer）から、10ms ごとの音量（dB）を作る。使えない環境では null。
export async function audioEnvelope(buffer) {
  const Ctx = globalThis.OfflineAudioContext || globalThis.webkitOfflineAudioContext;
  if (!Ctx) return null;
  let ctx = null;
  for (const rate of [8000, 16000, 22050, 44100]) {
    try { ctx = new Ctx(1, 1, rate); break; } catch { /* この標本化周波数に対応していない */ }
  }
  if (!ctx) return null;
  const decode = (ab) => new Promise((resolve, reject) => {
    const p = ctx.decodeAudioData(ab, resolve, reject);
    if (p && p.then) p.then(resolve, reject);
  });
  try {
    const frames = mp3Frames(new Uint8Array(buffer));
    if (!frames) {
      // MP3 以外（m4a など）はまとめて読む。decodeAudioData は渡したものを使えなくするので複製を渡す
      return envelopeOf(await decode(buffer.slice(0)));
    }
    const last = frames[frames.length - 1];
    const db = new Float32Array(Math.ceil((last.time + last.dur) * ENV_RATE) + 1).fill(-100);
    for (let i = 0; i < frames.length;) {
      let j = i;
      while (j < frames.length && frames[j].time - frames[i].time < CHUNK_SECONDS) j++;
      const from = Math.max(0, i - OVERLAP_FRAMES);
      const end = j < frames.length ? frames[j].pos : buffer.byteLength;
      const audio = await decode(buffer.slice(frames[from].pos, end));
      const env = envelopeOf(audio).db;
      // 重ねて読んだ部分を除き、元音声の時刻の位置に書き込む
      const skip = Math.round((frames[i].time - frames[from].time) * ENV_RATE);
      const at = Math.round(frames[i].time * ENV_RATE);
      for (let k = skip; k < env.length && at + k - skip < db.length; k++) db[at + k - skip] = env[k];
      i = j;
    }
    return { rate: ENV_RATE, db };
  } catch {
    return null;
  }
}

// MP3 のフレームの位置（バイト）と時刻。MP3 でなければ null
const BITRATES = {
  1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],  // MPEG-1 Layer III
  2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],       // MPEG-2/2.5 Layer III
};
const RATES = { 3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000] };
export function mp3Frames(b) {
  let p = 0;
  if (b[0] === 0x49 && b[1] === 0x44 && b[2] === 0x33) { // ID3v2
    p = 10 + ((b[6] & 0x7f) << 21 | (b[7] & 0x7f) << 14 | (b[8] & 0x7f) << 7 | (b[9] & 0x7f));
    if (b[5] & 0x10) p += 10;
  }
  const header = (q) => {
    if (q + 4 > b.length || b[q] !== 0xff || (b[q + 1] & 0xe0) !== 0xe0) return null;
    const ver = (b[q + 1] >> 3) & 3, layer = (b[q + 1] >> 1) & 3;
    const bi = b[q + 2] >> 4, si = (b[q + 2] >> 2) & 3, pad = (b[q + 2] >> 1) & 1;
    if (ver === 1 || layer !== 1 || bi === 0 || bi === 15 || si === 3) return null;
    const kbps = BITRATES[ver === 3 ? 1 : 2][bi], sr = RATES[ver][si];
    const spf = ver === 3 ? 1152 : 576;
    return { len: Math.floor((spf / 8) * kbps * 1000 / sr) + pad, dur: spf / sr };
  };
  // 最初のフレーム（続くフレームとつながっていることを確かめる）
  for (let n = 0; p < b.length && n < 65536; p++, n++) {
    const h = header(p);
    if (h && header(p + h.len)) break;
  }
  const frames = [];
  let t = 0;
  while (p < b.length) {
    const h = header(p);
    if (!h) break;
    frames.push({ pos: p, time: t, dur: h.dur });
    t += h.dur;
    p += h.len;
  }
  return frames.length > 100 ? frames : null;
}

function envelopeOf(audio) {
  const sr = audio.sampleRate, hop = sr / ENV_RATE;
  const n = Math.floor(audio.length / hop);
  const chans = [];
  for (let c = 0; c < audio.numberOfChannels; c++) chans.push(audio.getChannelData(c));
  const db = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let sum = 0;
    const k0 = Math.round(i * hop), k1 = Math.round((i + 1) * hop);
    for (let k = k0; k < k1; k++) {
      let x = 0;
      for (const ch of chans) x += ch[k];
      x /= chans.length;
      sum += x * x;
    }
    db[i] = 10 * Math.log10(sum / (k1 - k0) + 1e-10);
  }
  return { rate: ENV_RATE, db };
}

// 16bit PCM（モノラル）から作る。動作確認用
export function envelopeFromPcm16(samples, sampleRate) {
  const ch = new Float32Array(samples.length);
  for (let i = 0; i < samples.length; i++) ch[i] = samples[i] / 32768;
  return envelopeOf({ sampleRate, length: ch.length, numberOfChannels: 1, getChannelData: () => ch });
}


function levels(db) {
  const sorted = Float32Array.from(db).sort();
  const noise = sorted[Math.floor(sorted.length * 0.1)];
  const loud = sorted.filter((d) => d > noise + 15);
  const speech = loud.length ? loud[Math.floor(loud.length / 2)] : noise + 30;
  return { noise, speech, quiet: noise + 0.35 * (speech - noise) };
}

/**
 * 区間の clip（再生する範囲）を元音声の切れ目に合わせて直す。segments を書き換える。
 * segments: [{ begin, end, clip: { begin, end } }]（begin/end は TTML・JSON の時刻）
 * @returns 動かした切れ目の数など
 */
export function refineClips(segments, env, duration, { search = SEARCH } = {}) {
  if (!env || !env.db || !env.db.length || !segments.length) return null;
  const { db, rate } = env;
  const lv = levels(db);
  // 30ms の平均（一瞬の音の上下に惑わされないように）
  const sm = new Float32Array(db.length);
  for (let i = 0; i < db.length; i++) {
    sm[i] = (db[Math.max(0, i - 1)] + db[i] + db[Math.min(db.length - 1, i + 1)]) / 3;
  }
  const frame = (t) => Math.max(0, Math.min(sm.length - 1, Math.round(t * rate)));
  const dur = duration || db.length / rate;
  const stats = { boundaries: 0, moved: 0, kept: 0 };

  // t0〜t1 にかかる「静かな区間」（MIN_QUIET 以上続くもの）を [始まり, 終わり] で返す
  const quietRuns = (t0, t1, minLen = MIN_QUIET) => {
    const runs = [];
    let i = frame(t0), last = frame(t1);
    while (i > 0 && sm[i - 1] <= lv.quiet && sm[i] <= lv.quiet) i--; // 窓の外へ続く静かな区間も含める
    for (let s = -1; i <= last + 1 || s >= 0; i++) {
      const q = i < sm.length && sm[i] <= lv.quiet;
      if (q && s < 0) s = i;
      if (!q && s >= 0) {
        if ((i - s) / rate >= minLen - 1e-9) runs.push([s / rate, i / rate]);
        s = -1;
        if (i > last) break;
      }
      if (i >= sm.length) break;
    }
    return runs;
  };
  // c にいちばん近い静かな区間
  const nearestRun = (c, t0, t1) => {
    let best = null, bestDist = Infinity;
    for (const r of quietRuns(t0, t1)) {
      const d = c < r[0] ? r[0] - c : c > r[1] ? c - r[1] : 0;
      if (d < bestDist) { bestDist = d; best = r; }
    }
    return best;
  };

  for (let k = 1; k < segments.length; k++) {
    const A = segments[k - 1], B = segments[k];
    stats.boundaries++;
    const a = A.end, b = B.begin;
    if (b < a) { stats.kept++; continue; } // 時刻が重なっている。触らない
    // 時刻の切れ目がもう静かなところにあれば（短くても）そこ。なければ近くで声が途切れているところ
    const r = quietRuns(a - 0.03, b + 0.03, 0.02).find((q) => q[1] >= a - 0.03 && q[0] <= b + 0.03) ||
      nearestRun((a + b) / 2, a - search, b + search);
    if (!r) { stats.kept++; continue; }
    const half = (r[1] - r[0]) / 2;
    const old = [A.clip.end, B.clip.begin];
    // それぞれ元の時刻から SEARCH 以内のときだけ動かす（間に音楽などがあるとき、遠くまで動かさない）
    if (Math.abs(r[0] - a) <= search) A.clip.end = round3(r[0] + Math.min(TAIL, half));
    if (Math.abs(r[1] - b) <= search) B.clip.begin = round3(r[1] - Math.min(LEAD, half));
    if (A.clip.end > B.clip.begin) [A.clip.end, B.clip.begin] = old;
    if (A.clip.end !== old[0] || B.clip.begin !== old[1]) stats.moved++; else stats.kept++;
  }

  // 最初の文の始まりと最後の文の終わり（前後が静かなときだけ）
  const first = segments[0], last = segments[segments.length - 1];
  const r0 = quietRuns(first.begin - search, first.begin + 0.1).filter((r) => r[1] <= first.begin + 0.1).pop();
  if (r0) first.clip.begin = round3(Math.max(0, r0[1] - Math.min(LEAD, (r0[1] - r0[0]) / 2)));
  const r1 = quietRuns(last.end - 0.1, Math.min(dur, last.end + search)).find((r) => r[0] >= last.end - 0.1);
  if (r1) last.clip.end = round3(Math.min(dur, r1[0] + Math.min(TAIL, (r1[1] - r1[0]) / 2)));
  for (const s of segments) if (!(s.clip.end > s.clip.begin)) s.clip = { begin: round3(s.begin), end: round3(s.end) };
  return stats;
}

const round3 = (x) => Math.round(x * 1000) / 1000;

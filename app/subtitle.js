// 字幕の原文に、元音声での時刻を割り当てる。
//
// 単語の時刻（TTML や文字起こし）の文字列は、原文と完全には一致しないことがある
// （音声認識の誤りを直した原文、句読点の違い、中国語の行をまとめた文など）。
// そこで文字（記号を除く）を照らし合わせ、一致した文字の時刻を、その文字を含む表示単位に付ける。
// 一致が少ない文は強調しない（時刻を推測で作らない）。
// 表示単位は単語。英語などは空白で区切り、中国語・日本語・韓国語は単語の時刻の区切りを原文に当てはめる。

const CJK = /[぀-ヿ㐀-鿿豈-﫿가-힯\u{20000}-\u{2fa1f}]/u;
const IS_CHAR = /[\p{L}\p{N}]/u;
export const MIN_COVERAGE = 0.6;

export function isCjk(text, lang) {
  const base = (lang || "").split(/[-_]/)[0].toLowerCase();
  return ["zh", "ja", "ko"].includes(base) || (CJK.test(text) && !/[A-Za-z]{3,}\s+[A-Za-z]{3,}/.test(text));
}

// 表示単位に分ける。text は単位の text をつなげると原文に戻る
export function splitUnits(text, lang) {
  if (isCjk(text, lang)) {
    // 漢字・かな・ハングルは1文字ずつ、ラテン文字の並び（Nathan など）と数字はまとめる
    return text.match(/[A-Za-zÀ-ÿ0-9'’-]+|\s+|./gsu) || [];
  }
  return text.match(/\S+|\s+/g) || [];
}

// 最長共通部分列で、a の各文字に対応する b の文字の位置（なければ -1）を返す
function lcsMatch(a, b) {
  const n = a.length, m = b.length;
  const dp = new Uint16Array((n + 1) * (m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i * (m + 1) + j] = a[i] === b[j] ? dp[(i + 1) * (m + 1) + j + 1] + 1
        : Math.max(dp[(i + 1) * (m + 1) + j], dp[i * (m + 1) + j + 1]);
    }
  }
  const out = new Int32Array(n).fill(-1);
  let i = 0, j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { out[i++] = j++; }
    else if (dp[(i + 1) * (m + 1) + j] >= dp[i * (m + 1) + j + 1]) i++;
    else j++;
  }
  return out;
}

const chars = (s) => [...s.toLowerCase()].filter((c) => IS_CHAR.test(c));

/**
 * @returns {{units: {text: string, b: number|null, e: number|null}[], coverage: number} | null}
 *   null: 時刻がない、または一致が少なく強調しないほうがよい
 */
export function alignSubtitle(original, timings, lang) {
  if (!timings || !timings.length) return null;
  const parts = splitUnits(original, lang);
  const a = [], aUnit = [];
  parts.forEach((p, u) => { for (const c of chars(p)) { a.push(c); aUnit.push(u); } });
  const b = [], bTok = [];
  timings.forEach(([, , t], k) => { for (const c of chars(String(t))) { b.push(c); bTok.push(k); } });
  if (!a.length || !b.length || a.length * b.length > 4e6) return null;
  const match = lcsMatch(a, b);
  let matched = 0;
  const span = parts.map(() => null);
  match.forEach((j, i) => {
    if (j < 0) return;
    matched++;
    const [tb, te] = timings[bTok[j]];
    const u = aUnit[i];
    span[u] = span[u] ? [Math.min(span[u][0], tb), Math.max(span[u][1], te)] : [tb, te];
  });
  const coverage = matched / a.length;
  if (coverage < MIN_COVERAGE) return null;
  const units = parts.map((text, u) => ({ text, b: span[u] ? span[u][0] : null, e: span[u] ? span[u][1] : null }));
  if (!isCjk(original, lang)) return { coverage, units };
  // 中国語・日本語・韓国語は空白で単語が分かれていないので、単語の時刻（文字起こしの単語）の区切りを
  // 原文に当てはめて単語にまとめる。同じ単語の時刻に対応した、続いている文字を1つにする。
  const tokOf = parts.map(() => null);
  match.forEach((j, i) => { if (j >= 0 && tokOf[aUnit[i]] === null) tokOf[aUnit[i]] = bTok[j]; });
  const words = [];
  units.forEach((unit, u) => {
    const last = words[words.length - 1];
    if (last && tokOf[u] !== null && last.tok === tokOf[u]) {
      last.text += unit.text;
      last.b = Math.min(last.b, unit.b);
      last.e = Math.max(last.e, unit.e);
    } else {
      words.push({ ...unit, tok: tokOf[u] });
    }
  });
  return { coverage, units: words.map(({ text, b, e }) => ({ text, b, e })) };
}

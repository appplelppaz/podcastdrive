// 聞き流しモードのブラウザ確認（Chromium / Playwright）。tools/tests/run_e2e.sh から実行する。
//   node app/tests/e2e.cjs <教材フォルダ> <アプリのURL>
const { chromium } = require("playwright");
const path = require("path");
const fs = require("fs");

const [DIR, BASE] = process.argv.slice(2);
const PREPARED = "Morning market check (2026-01-01)"; // 準備ツールで作った .listening.json（以前の方式）
const results = [];
const check = (name, cond, info = "") => {
  results.push(!!cond);
  console.log(cond ? "OK " : "NG ", name, info);
};
const waitFor = (page, fn, arg, timeout = 60000) => page.waitForFunction(fn, arg, { timeout, polling: 50 });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const ascii = (n) => path.join(DIR, "ascii", n);

// 端末の音声合成の代わり。読み上げの順序・声・重なりを記録する（ヘッドレスの Chromium には声がないため）
function mockSpeech() {
  window.__speech = [];
  let cur = null, timer = null;
  const voices = [
    { name: "Kyoko", lang: "ja-JP", localService: true },
    { name: "Kyoko (Enhanced)", lang: "ja-JP", localService: true },
    { name: "Alex", lang: "en-US", localService: true },
    { name: "Samantha", lang: "en-US", localService: true },
  ];
  const synth = {
    speaking: false,
    getVoices: () => voices,
    speak(u) {
      if (cur) { window.__overlap = true; throw new Error("読み上げが重なった"); }
      const audio = document.getElementById("audio");
      window.__speech.push({ t: performance.now(), text: u.text, lang: u.lang, voice: u.voice && u.voice.name,
        rate: u.rate, audioPaused: audio.paused });
      if (u.text.trim() === "") { setTimeout(() => u.onend && u.onend(), 0); return; }
      cur = u; synth.speaking = true;
      // 4文字ごとに単語の区切りを知らせる（実機の onboundary の代わり）
      const words = [];
      for (let i = 4; i < u.text.length; i += 4) words.push(setTimeout(() => cur === u && u.onboundary && u.onboundary({ name: "word", charIndex: i }), 250 * i / u.text.length));
      timer = setTimeout(() => { cur = null; synth.speaking = false; u.onend && u.onend(); }, 250);
      u.__words = words;
    },
    cancel() {
      if (cur) { clearTimeout(timer); cur.__words.forEach(clearTimeout); const u = cur; cur = null; synth.speaking = false; u.onerror && u.onerror({ error: "interrupted" }); }
    },
  };
  Object.defineProperty(window, "speechSynthesis", { value: synth });
  window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; this.lang = ""; this.rate = 1; this.volume = 1; this.voice = null; } };
}

async function openMaterial(page, title, button) {
  await page.evaluate(() => { window.__old = window.__engine; });
  await page.locator("#library li", { hasText: title }).locator(button).click();
  await waitFor(page, () => window.__engine && window.__engine !== window.__old && window.__engine.log.length > 0);
}

(async () => {
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  await ctx.addInitScript(mockSpeech);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => check("JSエラーなし", false, e.message));

  // ---- 取り込み: 既存の MP3・JSON・TTML をそのまま、2回分をまとめて選ぶ
  await page.goto(BASE);
  await page.setInputFiles("#fileInput", ["a.json", "a.ttml", "a.mp3", "b.json", "b.ttml", "b.mp3"].map(ascii));
  await waitFor(page, () => document.querySelectorAll("#library li").length === 2);
  const status = await page.textContent("#importStatus");
  check("既存ファイルのまま2回分をまとめて取り込み", (status.match(/Added/g) || []).length === 2, status.replace(/\n/g, " / "));
  const titleA = "Morning market check", titleB = "Weather and travel";
  check("起動エラーの表示なし", await page.evaluate(() => window.__appStarted && document.getElementById("appError").hidden));
  // iPhone は種類の分からないファイル（Drive の .ttml は application/octet-stream）を選べなくなるので、種類で絞らない
  check("ファイル選択で種類を絞らない（.ttml を選べる）", (await page.getAttribute("#fileInput", "accept")) === null);
  // iPhone の Safari は大きな Blob を IndexedDB に保存できないことがあるので、音声は ArrayBuffer で別に保存
  const stored = await page.evaluate(() => new Promise((resolve) => {
    const req = indexedDB.open("listening-mode");
    req.onsuccess = () => {
      const t = req.result.transaction(["materials", "audio"]);
      const rows = t.objectStore("materials").getAll(), audio = t.objectStore("audio").getAll();
      t.oncomplete = () => resolve({ rowAudio: rows.result.some((r) => "audio" in r),
        audio: audio.result.map((a) => a.data instanceof ArrayBuffer && a.data.byteLength > 1000 && a.type) });
    };
  }));
  const refined = await page.evaluate(() => new Promise((resolve) => {
    const req = indexedDB.open("listening-mode");
    req.onsuccess = () => { const g = req.result.transaction("materials").objectStore("materials").getAll(); g.onsuccess = () => resolve(g.result.map((r) => r.doc.clipsRefined)); };
  }));
  check("取り込み時に文の切れ目を元音声に合わせる", refined.length === 2 && refined.every((r) => r && r.version >= 1 && !r.skipped && r.boundaries > 0), JSON.stringify(refined));
  check("音声は ArrayBuffer で別に保存（行には持たない）", !stored.rowAudio && stored.audio.length === 2 && stored.audio.every((t) => t === "audio/mpeg"), JSON.stringify(stored));

  // ---- 再生順序: ①元音声 → 訳 → 語彙・文法 → ④元音声（読み上げは端末の声）
  await openMaterial(page, titleA, ".start");
  await page.evaluate(() => window.__engine.goto(2));
  await waitFor(page, () => window.__engine.segIndex === 3 && window.__engine.stepIndex >= 1);
  const log = await page.evaluate(() => window.__engine.log.filter((l) => l.seg === "s003" && l.kind !== "pause"));
  const seg = await page.evaluate(() => window.__engine.doc.segments[2]);
  const n = (g) => seg.parts.filter((p) => p.type === "speech" && p.group === g).length;
  const rep = (label, k) => Array(k).fill(label);
  // 訳は案内なしで本文だけ。語彙・解説は「案内（表現・解説）＋本文」。元音声に戻る前の「もう一度」はない
  const expected = ["Original", ...rep("Translation", n("translation")),
    ...(n("vocab") ? rep("Vocabulary", 1 + n("vocab")) : []), ...(n("grammar") ? rep("Notes", 1 + n("grammar")) : []),
    "Original (again)"];
  const labels = log.map((l) => l.label);
  check("再生順序 ①元音声→訳→語彙→文法→④元音声", JSON.stringify(labels) === JSON.stringify(expected), labels.join(" → "));
  const speech = await page.evaluate(() => window.__speech.filter((s) => s.text.trim()));
  check("「訳」「もう一度」の案内を読み上げない", !speech.some((s) => s.text === "訳" || s.text === "もう一度"), speech.map((s) => s.text).slice(0, 6).join(" / "));
  check("読み上げ中は元音声が止まっている（重なりなし）", speech.every((s) => s.audioPaused) && !(await page.evaluate(() => window.__overlap)), speech.length + "件");
  check("日本語は日本語の声（高品質版を優先）", speech.filter((s) => s.lang === "ja-JP").every((s) => s.voice === "Kyoko (Enhanced)"));
  check("英語の語句は英語の声", speech.some((s) => s.lang === "en-US" && s.voice === "Samantha"), speech.filter((s) => s.lang === "en-US").map((s) => s.text).join(", "));
  // 語の後は意味だけを読む（「ここでは、…という意味です」は付けない）
  check("語彙の読み上げは意味だけ", !speech.some((s) => /ここでは|という意味です/.test(s.text)) && speech.some((s, i) => i > 0 && speech[i - 1].lang.startsWith("en") && s.lang.startsWith("ja") && /。$/.test(s.text)));

  // ---- 回数・速度
  await page.evaluate(() => window.__engine.updateSettings({ ...window.__engine.settings, firstRepeats: 2, secondRepeats: 3, originalRate: 0.7, translationRate: 1.3 }));
  await page.evaluate(() => window.__engine.goto(1));
  await waitFor(page, () => window.__engine.segIndex === 2, null, 120000);
  const log2 = await page.evaluate(() => window.__engine.log.filter((l) => l.seg === "s002"));
  const origs = log2.filter((l) => l.kind === "original");
  check("最初の元音声2回・解説後3回", origs.length === 5, origs.map((o) => o.label).join(","));
  check("元音声の速度 0.7倍", origs.every((o) => o.rate === 0.7));
  const tr = await page.evaluate(() => window.__speech.filter((s) => s.rate === 1.3).length);
  check("訳の読み上げ速度 1.3倍", tr >= 1);
  await page.evaluate(() => window.__engine.updateSettings({ ...window.__engine.settings, firstRepeats: 1, secondRepeats: 1, originalRate: 0.9, translationRate: 1 }));

  // ---- 字幕: 原文を表示し、元音声の再生中は今の単語を強調
  await page.evaluate(() => window.__engine.goto(2));
  await waitFor(page, () => window.__engine.step && window.__engine.step.kind === "original" && window.__engine.originalTime() > window.__engine.segment.begin + 0.3);
  await waitFor(page, () => document.querySelector("#subtitle .w.now"));
  const hl = await page.evaluate(() => {
    const t = window.__engine.originalTime(), w = document.querySelector("#subtitle .w.now");
    return { t, b: +w.dataset.b, e: +w.dataset.e, text: document.getElementById("subtitle").textContent, orig: window.__engine.segment.original };
  });
  check("字幕に原文を表示", hl.text === hl.orig, hl.text);
  check("元音声の再生位置の単語を強調（TTMLの時刻）", hl.t >= hl.b - 0.1 && hl.t <= hl.e + 0.1, JSON.stringify(hl));
  const sp = await page.waitForFunction(() => {
    const e = window.__engine, st = e.step, shown = document.getElementById("spoken").textContent;
    if (!st || st.kind !== "speech" || st.cue || st.group !== "vocab") return false;
    const expected = e.segment.parts.filter((p) => p.type === "speech" && p.group === "vocab").map((p) => p.text).join(" ");
    return shown.replace(/\s/g, "") === expected.replace(/\s/g, "") && { shown }; // 表示は日本語の前後の空白を詰める
  }, null, { timeout: 30000, polling: 20 }).then((h) => h.jsonValue()).catch(() => null);
  check("読み上げ中の語彙の説明を原文の下に表示", sp, sp && sp.shown);

  // ---- 停止・再開と移動（ジェスチャー）
  const box = await page.locator("#stage").boundingBox();
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  const swipe = async (dx) => { await page.mouse.move(cx, cy); await page.mouse.down(); await page.mouse.move(cx + dx / 2, cy, { steps: 4 }); await page.mouse.move(cx + dx, cy, { steps: 4 }); await page.mouse.up(); };
  await waitFor(page, () => window.__engine.step && window.__engine.step.kind === "speech" && !window.__engine.step.cue);
  await page.mouse.click(cx, cy);
  const n0 = await page.evaluate(() => window.__speech.length);
  await sleep(1200);
  const after = await page.evaluate(() => ({ n: window.__speech.length, speaking: speechSynthesis.speaking, paused: document.getElementById("audio").paused, playing: window.__engine.playing }));
  check("タップで一時停止し、古い読み上げが続かない", !after.playing && after.n === n0 && !after.speaking && after.paused);
  await page.mouse.click(cx, cy);
  check("もう一度タップで再開", await page.evaluate(() => window.__engine.playing));

  // ---- 一時停止した位置の続きから再開（元音声・読み上げ）
  await page.evaluate(() => window.__engine.restart());
  await waitFor(page, () => window.__engine.step.kind === "original" && window.__engine.originalTime() > window.__engine.segment.clip.begin + 0.4);
  const tPause = await page.evaluate(() => { const e = window.__engine; e.pause(); return e.el.currentTime; });
  await page.evaluate(() => window.__engine.play());
  await waitFor(page, () => window.__engine.originalTime() > 0);
  const resumedOrig = await page.evaluate(() => ({ log: window.__engine.log.at(-1), t: window.__engine.el.currentTime }));
  check("元音声は止めた位置から再開", resumedOrig.log.kind === "original" && Math.abs(resumedOrig.log.from - tPause) < 0.05 && resumedOrig.t >= tPause - 0.05,
    JSON.stringify({ tPause, resumedOrig }));
  await waitFor(page, () => window.__engine.step.kind === "speech" && !window.__engine.step.cue && window.__engine.speechAt > 0);
  const said = await page.evaluate(() => { const e = window.__engine; const r = { text: e.step.text, at: e.speechAt, step: e.stepIndex }; e.pause(); return r; });
  const n1 = await page.evaluate(() => { const n = window.__speech.length; window.__engine.play(); return n; });
  await waitFor(page, (n) => window.__speech.length > n, n1);
  const resumedSpeech = await page.evaluate((n) => ({ text: window.__speech[n].text }), n1);
  check("解説は止めた単語から読み直す（最初からではない）", resumedSpeech.text === said.text.slice(said.at) && said.at > 0,
    JSON.stringify({ said, resumedSpeech }));
  // 左スワイプは 原文 → 訳 → 語彙 → 解説 → 次の文 と段階を踏んで進む。右スワイプはその逆
  await page.evaluate(() => window.__engine.restart());
  const before = await page.evaluate(() => window.__engine.segIndex);
  const where = () => page.evaluate(() => { const e = window.__engine; return { seg: e.segIndex, stage: e.step.group || e.step.kind, spoken: document.getElementById("spoken").textContent }; });
  const stages = await page.evaluate(() => window.__engine.stages().map((i) => window.__engine.steps[i].group || "original"));
  const trText = await page.evaluate(() => window.__engine.segment.parts.filter((p) => p.group === "translation").map((p) => p.text).join(""));
  const fwd = [];
  for (let i = 0; i < stages.length; i++) { await swipe(-150); fwd.push(await where()); }
  check("左スワイプで訳・語彙・解説と1段ずつ進む", JSON.stringify(fwd.slice(0, -1).map((w) => w.stage)) === JSON.stringify(stages.slice(1)) && fwd.every((w, i) => i === fwd.length - 1 || w.seg === before),
    JSON.stringify({ stages, fwd: fwd.map((w) => w.stage) }));
  check("訳に進むと訳を表示", stages[1] === "translation" && fwd[0].spoken.replace(/\s/g, "") === trText.replace(/\s/g, ""), fwd[0].spoken);
  check("最後の説明から左スワイプで次の文", fwd.at(-1).seg === before + 1 && fwd.at(-1).stage === "original", JSON.stringify(fwd.at(-1)));
  check("操作しても字幕に重なる表示を出さない", await page.evaluate(() => ![...document.querySelectorAll("#stage *")]
    .some((el) => !["subtitle", "spoken", "pausedMark"].includes(el.id) && !el.closest("#subtitle, #spoken") && el.getBoundingClientRect().height > 0)));
  const bwd = [];
  for (let i = 0; i < stages.length; i++) { await swipe(150); bwd.push(await where()); }
  check("右スワイプで前の文の最後の説明から1段ずつ戻る", bwd.every((w) => w.seg === before) && JSON.stringify(bwd.map((w) => w.stage)) === JSON.stringify(stages.slice().reverse()),
    JSON.stringify(bwd.map((w) => w.stage)));
  await page.evaluate(() => window.__engine.pause());
  await swipe(-150);
  check("一時停止中もスワイプで訳を表示", await page.evaluate(() => !window.__engine.playing && window.__engine.step.group === "translation" && document.getElementById("spoken").textContent.length > 0));
  await page.evaluate(() => window.__engine.play());
  await waitFor(page, () => window.__engine.stepIndex >= 2);
  await page.mouse.move(cx, cy); await page.mouse.down(); await sleep(800); await page.mouse.up();
  check("長押しで今の文を最初から", await page.evaluate(() => window.__engine.stepIndex === 0 && window.__engine.playing && window.__engine.segIndex) === before);
  // ---- 長い文: 画面に収まらなければ縦にスクロールして全部読める（上は切れない）
  await page.evaluate(() => window.__engine.pause());
  await page.setViewportSize({ width: 390, height: 520 });
  const long = await page.evaluate(() => {
    const st = document.getElementById("stage"), sp = document.getElementById("spoken");
    sp.textContent = "まず菌類だけで言えば、環境条件でストレスを受けると、ほぼ休眠のような状態に入り、菌糸が引っ込みます。".repeat(4);
    st.scrollTop = 0;
    const subTop = document.getElementById("subtitle").getBoundingClientRect().top - st.getBoundingClientRect().top;
    return { over: st.scrollHeight > st.clientHeight, subTop, page: document.documentElement.scrollHeight <= innerHeight + 1 };
  });
  await page.mouse.move(cx, 300); await page.mouse.wheel(0, 2000); await sleep(300);
  const scrolled = await page.evaluate(() => { const st = document.getElementById("stage"), sp = document.getElementById("spoken");
    return { top: st.scrollTop, spokenEnd: sp.getBoundingClientRect().bottom <= st.getBoundingClientRect().bottom + 1, whole: sp.scrollHeight <= sp.clientHeight + 1 }; });
  check("長い文は縦にスクロールして最後まで読める", long.over && long.subTop >= 0 && long.page && scrolled.top > 0 && scrolled.spokenEnd && scrolled.whole, JSON.stringify({ long, scrolled }));
  await page.screenshot({ path: path.join(DIR, "player-390-long.png") });
  await page.setViewportSize({ width: 1366, height: 760 });
  await page.evaluate(() => { document.getElementById("stage").scrollTop = 0; });
  await page.screenshot({ path: path.join(DIR, "player-1366-long.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => { document.getElementById("spoken").textContent = ""; window.__engine.play(); });
  await page.screenshot({ path: path.join(DIR, "player-390.png") });
  await page.emulateMedia({ colorScheme: "dark" });
  await page.screenshot({ path: path.join(DIR, "player-390-dark.png") });
  await page.emulateMedia({ colorScheme: "light" });

  // ---- 教材ごとの再生履歴
  await page.click("#back");
  await openMaterial(page, titleB, ".start");
  await page.evaluate(() => window.__engine.goto(1));
  await waitFor(page, () => window.__engine.log.at(-1).seg === "s002");
  await page.click("#back");
  await waitFor(page, (t) => [...document.querySelectorAll("#library li")].some((li) => li.textContent.includes(t) && li.textContent.includes("Sentence 2 of 3")), titleB);
  const metaA = await page.locator("#library li", { hasText: titleA }).locator(".meta").textContent();
  const metaB = await page.locator("#library li", { hasText: titleB }).locator(".meta").textContent();
  check("教材ごとに前回の位置を表示（A）", /Sentence 3 of 4/.test(metaA), metaA);
  check("教材ごとに前回の位置を表示（B）", /Sentence 2 of 3/.test(metaB), metaB);
  // ホームの「続きから」: 最後に聞いた B の、止めた文の原文をそのまま出す
  const hero = await page.evaluate(() => ({ shown: !document.getElementById("hero").hidden, line: document.getElementById("heroLine").textContent }));
  check("ホームの続きからに止めた文を表示", hero.shown && hero.line.length > 0, hero.line);
  await page.screenshot({ path: path.join(DIR, "setup-390.png"), fullPage: true });
  check("画面の幅からはみ出さない", await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth));
  await page.emulateMedia({ colorScheme: "dark" }); // 暗い画面（夜の運転）の見た目も残す
  await page.screenshot({ path: path.join(DIR, "setup-390-dark.png"), fullPage: true });
  await page.emulateMedia({ colorScheme: "light" });
  await page.reload();
  await page.waitForSelector("#library li .resume");
  await openMaterial(page, titleA, ".resume");
  check("再読み込み後、A は続き（3文目）から", (await page.evaluate(() => window.__engine.log[0].seg)) === "s003");
  await page.click("#back");
  await openMaterial(page, titleB, ".resume");
  check("B は B の続き（2文目）から", (await page.evaluate(() => window.__engine.log[0].seg)) === "s002");
  await page.click("#back");

  // ---- 文を選んで再生（エピソード画面）と設定の画面
  await page.locator("#library li", { hasText: titleA }).locator(".pick").click();
  await page.waitForSelector("#sentences li");
  const rows = await page.locator("#sentences li").count();
  check("エピソード画面に文の一覧", rows === 4, String(rows));
  check("止めた文を示す", (await page.locator("#sentences li.here").count()) === 1);
  await page.screenshot({ path: path.join(DIR, "episode-390.png"), fullPage: true });
  await page.evaluate(() => { window.__old = window.__engine; });
  await page.locator("#sentences li").nth(1).locator("button").click();
  await waitFor(page, () => window.__engine && window.__engine !== window.__old && window.__engine.log.length > 0);
  check("選んだ文（2文目）から再生", (await page.evaluate(() => window.__engine.log[0].seg)) === "s002");
  await page.click("#back");
  await page.click("#openSettings");
  check("設定は別の画面", await page.isVisible("#originalRate") && !(await page.isVisible("#library")));
  await page.screenshot({ path: path.join(DIR, "settings-390.png"), fullPage: true });
  await page.click("#settingsBack");

  // ---- iPad 版（?layout=ipad。本番は /ipad/）: 一覧とエピソードを左右に並べる
  const ipad = await ctx.newPage();
  await ipad.setViewportSize({ width: 1366, height: 1024 }); // iPad Pro 12.9（横）
  await ipad.goto(BASE + "?layout=ipad");
  await ipad.waitForSelector("#sentences li");
  const split = await ipad.evaluate(() => {
    const h = document.getElementById("home").getBoundingClientRect(), e = document.getElementById("episode").getBoundingClientRect();
    return { side: h.width > 0 && e.width > 0 && e.left >= h.right - 1, overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth };
  });
  check("iPad: 一覧とエピソードを左右に並べる", split.side && !split.overflow, JSON.stringify(split));
  await ipad.screenshot({ path: path.join(DIR, "ipad-1180.png") });
  await ipad.click("#openSettings");
  check("iPad: 設定は右に出て一覧は残る", await ipad.isVisible("#originalRate") && await ipad.isVisible("#library"));
  await ipad.click("#settingsBack");
  await ipad.evaluate(() => { window.__old = window.__engine; });
  await ipad.locator("#sentences li").first().locator("button").click();
  await waitFor(ipad, () => window.__engine && window.__engine !== window.__old && window.__engine.log.length > 0);
  check("iPad: 再生画面は全画面", await ipad.evaluate(() => { const r = document.getElementById("player").getBoundingClientRect(); return r.width === innerWidth && document.getElementById("home").hidden; }));
  await ipad.screenshot({ path: path.join(DIR, "ipad-player-1180.png") });
  await ipad.setViewportSize({ width: 1024, height: 1366 }); // 縦
  await sleep(200);
  check("iPad: 再生画面の原文は大きい", await ipad.evaluate(() => parseFloat(getComputedStyle(document.getElementById("subtitle")).fontSize) >= 60));
  await ipad.screenshot({ path: path.join(DIR, "ipad-player-portrait.png") });
  await ipad.click("#back");
  check("iPad: 戻ると左右の画面", await ipad.isVisible("#library") && await ipad.isVisible("#sentences"));
  await ipad.close();

  // ---- 以前の方式（準備済みの .listening.json ＋ .listening.m4a）も読める
  await page.goto(BASE + "?import=" + encodeURIComponent("materials/" + PREPARED + ".listening.json"));
  await waitFor(page, () => /Added/.test(document.getElementById("importStatus").textContent));
  check("準備済みの教材も取り込める", (await page.locator("#library li").count()) === 3);

  // ---- 組になっていないファイルは理由を表示
  await page.setInputFiles("#fileInput", [ascii("a.json")]);
  await waitFor(page, () => /add its \.mp3/.test(document.getElementById("importStatus").textContent));
  check("音声がないときは理由を表示", true, await page.textContent("#importStatus"));

  // ---- Drive からの .ttml（種類が application/octet-stream）も字幕として使う。関係ないファイルは理由を表示
  const buf = (n) => fs.readFileSync(ascii(n));
  await page.setInputFiles("#fileInput", [
    { name: "a.json", mimeType: "application/json", buffer: buf("a.json") },
    { name: "a.mp3", mimeType: "audio/mpeg", buffer: buf("a.mp3") },
    { name: "a.ttml", mimeType: "application/octet-stream", buffer: buf("a.ttml") },
    { name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("x") },
  ]);
  await waitFor(page, () => /Added/.test(document.getElementById("importStatus").textContent));
  const st = await page.textContent("#importStatus");
  const metaA2 = await page.locator("#library li", { hasText: titleA }).first().textContent(); // 新しいものが先頭
  check("octet-stream の .ttml も取り込む", /Added/.test(st) && !/No word timing/.test(metaA2), metaA2);
  check("対象外のファイルは理由を表示", /notes\.txt: skipped/.test(st), st.replace(/\n/g, " / "));

  // ---- 以前の版で音声を Blob として行に保存した教材も再生できる
  await page.evaluate(() => new Promise((resolve, reject) => {
    const req = indexedDB.open("listening-mode");
    req.onsuccess = async () => {
      const db = req.result;
      const row = await new Promise((r) => { const g = db.transaction("materials").objectStore("materials").getAll(); g.onsuccess = () => r(g.result.find((x) => x.mode === "direct")); });
      const a = await new Promise((r) => { const g = db.transaction("audio").objectStore("audio").get(row.id); g.onsuccess = () => r(g.result); });
      const t = db.transaction("materials", "readwrite");
      const doc = { ...row.doc, clipsRefined: undefined }; // 文の切れ目を合わせる前の版で取り込んだもの
      t.objectStore("materials").put({ ...row, doc, id: "legacy", title: "Legacy blob row", addedAt: 1, audio: new Blob([a.data], { type: a.type }) });
      t.oncomplete = resolve; t.onerror = reject;
    };
  }));
  await page.reload();
  await page.waitForSelector("#library li");
  await openMaterial(page, "Legacy blob row", ".start");
  await waitFor(page, () => window.__engine.log.some((l) => l.kind === "original") && !document.getElementById("audio").paused);
  check("以前の版で保存した教材も再生できる", true);
  const legacyRefined = await page.evaluate(() => new Promise((resolve) => {
    const req = indexedDB.open("listening-mode");
    req.onsuccess = () => { const g = req.result.transaction("materials").objectStore("materials").get("legacy"); g.onsuccess = () => resolve(g.result.doc.clipsRefined); };
  }));
  check("以前の版の教材は最初に開いたときに切れ目を合わせて保存", legacyRefined && legacyRefined.version >= 1, JSON.stringify(legacyRefined));
  await page.click("#back");

  // ---- 元音声と読み上げの切り替え直後に iPhone が送ってくる停止では止まらない。それ以外の停止は中断
  await openMaterial(page, titleB, ".from-start");
  await waitFor(page, () => window.__engine.step.kind === "original" && !document.getElementById("audio").paused);
  await page.evaluate(() => document.getElementById("audio").pause()); // 切り替え直後の停止（iPhone の動きの代わり）
  await sleep(600);
  const afterSwitch = await page.evaluate(() => ({ playing: window.__engine.playing, audio: !document.getElementById("audio").paused,
    ignored: window.__engine.log.some((l) => l.kind === "ignored-pause") }));
  check("切り替え直後の音声停止では止まらず続ける", afterSwitch.playing && afterSwitch.audio && afterSwitch.ignored, JSON.stringify(afterSwitch));
  await waitFor(page, () => window.__engine.step.kind === "speech" && window.__engine.settling());
  await page.evaluate(() => window.__systemPause());
  check("切り替え直後のロック画面の一時停止は無視", await page.evaluate(() => window.__engine.playing));
  await waitFor(page, () => window.__engine.step.kind === "original" && window.__engine.playing &&
    performance.now() - window.__engine.stepStartedAt > 1600 && !document.getElementById("audio").paused);
  await page.evaluate(() => document.getElementById("audio").pause()); // 再生中の本当の中断（電話など）
  await waitFor(page, () => !window.__engine.playing);
  check("再生の途中の停止は中断として止まる", /Stopped/.test(await page.textContent("#playerStatus")));
  await page.evaluate(() => window.__engine.play());
  check("再開したら「Stopped」のお知らせを消す", await page.textContent("#playerStatus") === "");
  await waitFor(page, () => window.__engine.playing && window.__engine.step.kind === "original" && !window.__engine.settling());
  await page.evaluate(() => window.__systemPause());
  check("切り替えから時間がたてばロック画面で一時停止できる", await page.evaluate(() => !window.__engine.playing));
  await page.click("#back");

  // ---- 元音声の終わり際に iPhone が止めても、一時停止にならず訳へ進む
  await openMaterial(page, titleB, ".from-start, .start:not(.resume)");
  await waitFor(page, () => window.__engine.step.kind === "original" && !document.getElementById("audio").paused &&
    performance.now() - window.__engine.stepStartedAt > 400);
  await page.evaluate(() => new Promise((resolve) => {
    const el = document.getElementById("audio");
    el.addEventListener("seeked", () => { el.pause(); resolve(); }, { once: true }); // 終わり際で止まる（iPhone の動きの代わり）
    el.currentTime = window.__engine.segment.clip.end - 0.2;
  }));
  await waitFor(page, () => window.__engine.step && window.__engine.step.kind !== "original", null, 5000);
  const endPause = await page.evaluate(() => ({ playing: window.__engine.playing, kind: window.__engine.step.kind,
    logged: window.__engine.log.some((l) => l.kind === "end-pause") }));
  check("元音声の終わり際の停止では止まらず訳へ進む", endPause.playing && endPause.logged, JSON.stringify(endPause));
  await page.click("#back");

  // ---- アプリ本体が読めないとき（古い Safari の構文エラーなど）は理由を画面に出す
  const bctx = await browser.newContext({ serviceWorkers: "block" }); // Service Worker 経由だと差し替えが効かない
  const broken = await bctx.newPage();
  await broken.route("**/material.js", (r) => r.fulfill({ contentType: "text/javascript", body: "export const x = ;" }));
  await broken.goto(BASE);
  // 1回目は古い保存を消して自動で読み直し、2回目に理由とボタンを出す
  const waitShown = async () => {
    for (let k = 0; k < 40; k++) {
      try {
        const ok = await broken.evaluate(() => sessionStorage.getItem("listening.autoReset") === "1" &&
          !!document.getElementById("resetApp") && document.getElementById("appError").textContent.length > 0);
        if (ok) return;
      } catch { /* 読み直しの最中 */ }
      await sleep(250);
    }
  };
  await waitShown();
  const shown = await broken.textContent("#appError").catch(() => "");
  check("本体が読めないときは画面に理由を表示し、一度だけ自動で読み直す", /Error|did not start|Could not load/.test(shown) &&
    (await broken.evaluate(() => sessionStorage.getItem("listening.autoReset"))) === "1", shown.slice(0, 120));
  await bctx.close();

  await browser.close();
  const ng = results.filter((r) => !r).length;
  console.log(`\n${results.length - ng}/${results.length} OK`);
  process.exit(ng ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });

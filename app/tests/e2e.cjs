// 聞き流しモードのブラウザ確認（Chromium / Playwright）。tools/tests/run_e2e.sh から実行する。
//   node app/tests/e2e.cjs <教材フォルダ> <アプリのURL>
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const [DIR, BASE] = process.argv.slice(2);
const B = "Weather and travel (2026-01-02)"; // .listening.json はエピソード名 (日付) で名付けられる
const results = [];
const check = (name, cond, info = "") => {
  results.push(!!cond);
  console.log(cond ? "OK " : "NG ", name, info);
};
const waitFor = (page, fn, arg, timeout = 60000) => page.waitForFunction(fn, arg, { timeout, polling: 50 });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function openMaterial(page, title, button) {
  await page.evaluate(() => { window.__old = window.__engine; });
  await page.locator("#library li", { hasText: title }).locator(button).click();
  await waitFor(page, () => window.__engine && window.__engine !== window.__old && window.__engine.log.length > 0);
}

(async () => {
  const browser = await chromium.launch({ args: ["--autoplay-policy=no-user-gesture-required"] });
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => check("JSエラーなし", false, e.message));

  // ---- 取り込み: A はファイル選択（ASCII名のコピー。内容のハッシュで照合）、B は URL から
  await page.goto(BASE);
  await page.setInputFiles("#fileInput", [path.join(DIR, "ascii", "a.listening.json"), path.join(DIR, "ascii", "a.listening.m4a")]);
  await page.waitForSelector("#library li");
  check("ファイル選択で取り込み", /^Imported “[^”]+”$/.test(await page.textContent("#importStatus")));
  await page.goto(BASE + "?import=" + encodeURIComponent("materials/" + B + ".listening.json"));
  await waitFor(page, () => document.querySelectorAll("#library li").length === 2);
  check("URL から取り込み", true);

  // ---- 教材ごとの再生履歴
  const titleA = "Morning market check", titleB = "Weather and travel";
  await openMaterial(page, titleA, ".start");
  await page.evaluate(() => window.__engine.goto(2));
  await waitFor(page, () => window.__engine.log.at(-1).seg === "s003");
  await page.click("#back");
  await openMaterial(page, titleB, ".start");
  await page.evaluate(() => window.__engine.goto(1));
  await waitFor(page, () => window.__engine.log.at(-1).seg === "s002");
  await page.click("#back");
  await waitFor(page, (t) => [...document.querySelectorAll("#library li")].some((li) => li.textContent.includes(t) && li.textContent.includes("Last:")), titleB);
  const metaA = await page.locator("#library li", { hasText: titleA }).locator(".meta").textContent();
  const metaB = await page.locator("#library li", { hasText: titleB }).locator(".meta").textContent();
  check("教材ごとに前回の位置を表示（A）", /Last: sentence 3 of 4/.test(metaA), metaA);
  check("教材ごとに前回の位置を表示（B）", /Last: sentence 2 of 3/.test(metaB), metaB);
  await page.screenshot({ path: path.join(DIR, "setup-390.png"), fullPage: true });

  await page.reload();
  await page.waitForSelector("#library li .resume");
  await openMaterial(page, titleA, ".resume");
  check("再読み込み後、A は続き（3文目）から", (await page.evaluate(() => window.__engine.log[0].seg)) === "s003");
  await page.screenshot({ path: path.join(DIR, "player-390.png") });
  // ---- 字幕: 元音声の再生中は今の単語を強調、読み上げ中はその文を表示
  await page.evaluate(() => window.__engine.restart());
  await waitFor(page, () => window.__engine.step && window.__engine.step.kind === "original" && window.__engine.originalTime() > 0);
  await waitFor(page, () => document.querySelector("#subtitle .w.now"));
  const hl = await page.evaluate(() => {
    const t = window.__engine.originalTime(), w = document.querySelector("#subtitle .w.now");
    return { t, b: +w.dataset.b, e: +w.dataset.e, text: document.getElementById("subtitle").textContent, orig: window.__engine.segment.original };
  });
  check("字幕に原文を表示", hl.text === hl.orig, hl.text);
  check("元音声の再生位置の単語を強調", hl.t >= hl.b - 0.1 && hl.t <= hl.e + 0.1, JSON.stringify(hl));
  await waitFor(page, () => window.__engine.step && window.__engine.step.kind === "speech" && window.__engine.step.group === "translation");
  const sp = await page.waitForFunction(() => {
    const e = window.__engine, st = e.step, shown = document.getElementById("spoken").textContent;
    if (!st || st.kind !== "speech" || st.cue || st.group !== "vocab") return false;
    const expected = e.segment.parts.filter((p) => p.type === "speech" && p.group === "vocab").map((p) => p.text).join(" ");
    return shown === expected && { shown, expected };
  }, null, { timeout: 30000, polling: 20 }).then((h) => h.jsonValue()).catch(() => null);
  check("読み上げ中の項目（語彙）を原文の下に表示", sp, sp && sp.shown);
  check("案内の「訳」「表現」などは表示しない", sp && !["訳", "表現", "解説", "もう一度"].includes(sp.shown));

  // ---- ジェスチャー: タップ / 左右スワイプ / 長押し
  const box = await page.locator("#stage").boundingBox();
  const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
  const swipe = async (dx) => { await page.mouse.move(cx, cy); await page.mouse.down(); await page.mouse.move(cx + dx / 2, cy, { steps: 4 }); await page.mouse.move(cx + dx, cy, { steps: 4 }); await page.mouse.up(); };
  await page.mouse.click(cx, cy);
  check("タップで一時停止", !(await page.evaluate(() => window.__engine.playing)) && /Paused/.test(await page.textContent("#stepLabel")));
  await page.mouse.click(cx, cy);
  check("もう一度タップで再開", await page.evaluate(() => window.__engine.playing));
  const before = await page.evaluate(() => window.__engine.segIndex);
  await swipe(-150);
  check("左スワイプで次の文", (await page.evaluate(() => window.__engine.segIndex)) === before + 1);
  await swipe(150);
  check("右スワイプで前の文", (await page.evaluate(() => window.__engine.segIndex)) === before);
  await waitFor(page, () => window.__engine.stepIndex >= 2);
  await page.mouse.move(cx, cy); await page.mouse.down(); await sleep(800); await page.mouse.up();
  check("長押しで今の文を最初から（停止しない）", await page.evaluate(() => window.__engine.stepIndex === 0 && window.__engine.playing && window.__engine.segIndex) === before);
  await page.click("#back");
  await openMaterial(page, titleB, ".resume");
  check("B は B の続き（2文目）から", (await page.evaluate(() => window.__engine.log[0].seg)) === "s002");
  await page.click("#back");

  // ---- 簡易モード（元MP3＋端末の音声合成）: 重ならない・停止後に読み上げが残らない
  const ctx2 = await browser.newContext();
  const p2 = await ctx2.newPage();
  await p2.addInitScript(() => {
    // 端末の音声合成の代わり（読み上げ中かどうかと順序を記録する）
    window.__speech = [];
    let cur = null, timer = null;
    const synth = {
      speaking: false, getVoices: () => [],
      speak(u) {
        if (cur) throw new Error("重なり");
        cur = u; synth.speaking = true;
        window.__speech.push({ t: performance.now(), text: u.text, lang: u.lang, rate: u.rate,
          audioPaused: document.getElementById("audio").paused });
        timer = setTimeout(() => { cur = null; synth.speaking = false; u.onend && u.onend(); }, 300);
      },
      cancel() {
        if (cur) { clearTimeout(timer); const u = cur; cur = null; synth.speaking = false; u.onerror && u.onerror({ error: "interrupted" }); }
        window.__speech.push({ cancel: true, t: performance.now() });
      },
    };
    Object.defineProperty(window, "speechSynthesis", { value: synth });
  });
  await p2.goto(BASE);
  await p2.setInputFiles("#fileInput", [path.join(DIR, "ascii", "b-direct.listening.json"), path.join(DIR, "ascii", "b.mp3")]);
  await p2.waitForSelector("#library li");
  check("元音声だけで簡易モードとして取り込み", /basic mode/.test(await p2.textContent("#importStatus")));
  await p2.click("#library li .start");
  await waitFor(p2, () => window.__engine && window.__engine.segIndex === 1 && window.__engine.stepIndex > 2);
  const log = await p2.evaluate(() => window.__engine.log.filter((l) => l.seg === "s001" || l.seg === "s002"));
  const speech = await p2.evaluate(() => window.__speech.filter((s) => !s.cancel));
  check("読み上げ中は元音声が止まっている", speech.every((s) => s.audioPaused), speech.length + "件");
  check("言語ごとの声（日本語は ja-JP）", speech.some((s) => s.lang === "ja-JP"));
  const order = log.filter((l) => l.seg === "s001" && l.kind !== "pause").map((l) => l.kind);
  check("簡易モードも ①元音声 → 訳 → ④元音声 の順", order[0] === "original" && order.includes("speech") && order.at(-1) === "original", order.join(","));
  await waitFor(p2, () => window.__engine.step && window.__engine.step.kind === "speech");
  await p2.click("#playPause");
  const n = await p2.evaluate(() => window.__speech.filter((s) => !s.cancel).length);
  await sleep(1500);
  const after = await p2.evaluate(() => ({ n: window.__speech.filter((s) => !s.cancel).length, speaking: speechSynthesis.speaking,
    paused: document.getElementById("audio").paused }));
  check("停止後に古い読み上げが続かない", after.n === n && !after.speaking && after.paused);

  await browser.close();
  const ng = results.filter((r) => !r).length;
  console.log(`\n${results.length - ng}/${results.length} OK`);
  process.exit(ng ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(2); });

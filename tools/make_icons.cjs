// app/icons/icon.svg から、アプリのアイコンの PNG（180・192・512）を作る。
//   node tools/make_icons.cjs
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

(async () => {
  const dir = path.join(__dirname, "..", "app", "icons");
  const svg = fs.readFileSync(path.join(dir, "icon.svg"), "utf8");
  const browser = await chromium.launch();
  for (const size of [180, 192, 512]) {
    const page = await browser.newPage({ viewport: { width: size, height: size } });
    await page.setContent(`<html><body style="margin:0">${svg.replace("<svg ", `<svg width="${size}" height="${size}" `)}</body></html>`);
    await page.screenshot({ path: path.join(dir, `icon-${size}.png`), omitBackground: false });
    await page.close();
  }
  await browser.close();
})();

// Listening Studio の画面。1秒ごとに状態を読み直して描く。
const $ = (id) => document.getElementById(id);
const STEP_SHARE = { Subtitles: [0, 0.3], Notes: [0.3, 0.9], Voice: [0.9, 1] };
let last = null;
let editing = false;
const picked = new Set(); // Drive へ移すエピソード
const size = (b) => (b >= 1e9 ? (b / 1e9).toFixed(1) + " GB" : Math.max(1, Math.round(b / 1e6)) + " MB");

async function api(path, body) {
  const res = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
  return res.json();
}

function progress(job) {
  const [a, b] = STEP_SHARE[job.step] || [0, 0];
  return a + (b - a) * (job.total ? job.done / job.total : 0.5);
}

function render(s) {
  last = s;
  const fresh = s.episodes.filter((e) => e.status === "new" || e.status === "failed");
  const busy = !!s.job.name || s.episodes.some((e) => e.status === "queued");
  $("make").hidden = busy || !fresh.length;
  $("make").textContent = fresh.length > 1 ? `Make ${fresh.length}` : "Make";
  $("stop").hidden = !busy;
  const needs = [];
  if (!s.checks.claude) needs.push("Claude Code is not installed. Notes cannot be made.");
  if (!s.checks.ffmpeg) needs.push("ffmpeg is not installed.");
  if (!s.checks.voice) needs.push("No voice engine found.");
  if (!s.checks.whisper && s.episodes.some((e) => !e.subtitles && !e.notes)) needs.push("Whisper is not installed. Episodes without subtitles cannot be made.");
  $("needs").replaceChildren(...needs.map((t) => Object.assign(document.createElement("li"), { textContent: t })));

  $("empty").hidden = s.episodes.length > 0;
  $("episodes").replaceChildren(...s.episodes.map((e) => {
    const li = document.createElement("li");
    li.className = e.status;
    li.innerHTML = `<span class="what"><span class="name"></span><span class="meta"></span><span class="error"></span></span><span class="status"></span><div class="track"><span></span></div>`;
    li.querySelector(".name").textContent = e.name;
    li.querySelector(".meta").textContent = e.notes ? "Notes included" : e.subtitles ? "Subtitles included" : "No subtitles";
    li.querySelector(".error").textContent = e.error;
    const running = e.status === "running";
    const count = running && s.job.total ? ` ${s.job.done}/${s.job.total}` : "";
    li.querySelector(".status").textContent = { new: "", queued: "Waiting", ready: "Ready", failed: "Failed", running: (s.job.step || "Starting") + count }[e.status];
    li.querySelector(".track span").style.width = (e.status === "ready" ? 100 : running ? progress(s.job) * 100 : 0) + "%";
    return li;
  }));
  $("log").textContent = busy || s.episodes.some((e) => e.status === "failed") ? s.log.slice(-2).join("\n") : "";

  // できあがったもの。選んだものだけ Drive（Google ドライブと同期するフォルダ）へ移す
  const names = new Set(s.ready.map((e) => e.name));
  for (const n of [...picked]) if (!names.has(n)) picked.delete(n);
  $("readyEmpty").hidden = s.ready.length > 0;
  $("readyList").replaceChildren(...s.ready.map((e) => {
    const li = document.createElement("li");
    li.innerHTML = `<label><input type="checkbox"><span class="name"></span></label><span class="status"></span><div class="track"><span></span></div>`;
    li.querySelector(".name").textContent = e.name;
    li.querySelector(".status").textContent = size(e.bytes);
    const box = li.querySelector("input");
    box.checked = picked.has(e.name);
    box.onchange = () => { box.checked ? picked.add(e.name) : picked.delete(e.name); showMove(); };
    return li;
  }));
  showMove();
  $("driveInfo").textContent = !s.drive.found ? "Set the Drive folder in Settings." : s.drive.count === 1 ? "1 episode" : `${s.drive.count} episodes`;
  if (s.error !== undefined) $("moveError").textContent = s.error;

  if (!editing) {
    $("drive").value = s.config.drive;
    $("level").value = s.config.level;
    $("inbox").value = s.config.inbox;
    $("ready").value = s.config.ready;
    $("voiceRow").hidden = !s.voices.length;
    if ($("voice").options.length !== s.voices.length) $("voice").replaceChildren(...s.voices.map((v) => new Option(v, v)));
    $("voice").value = s.config.voice;
  }
}

function showMove() {
  $("move").hidden = !picked.size || !(last && last.drive.found);
  $("move").textContent = picked.size > 1 ? `Move ${picked.size} to Drive` : "Move to Drive";
}

async function refresh() {
  try { render(await api("/api/state")); } catch { $("log").textContent = "Studio is not running. Open it again from the launcher."; }
}

$("make").onclick = async () => render(await api("/api/make", { names: last.episodes.filter((e) => e.status === "new" || e.status === "failed").map((e) => e.name) }));
$("stop").onclick = async () => render(await api("/api/stop", {}));
for (const b of document.querySelectorAll("[data-open]")) b.onclick = () => api("/api/open", { folder: b.dataset.open });
$("move").onclick = async () => {
  $("moveError").textContent = "";
  render(await api("/api/move", { names: [...picked] }));
};
for (const id of ["level", "voice", "inbox", "ready", "drive"]) {
  $(id).addEventListener("focus", () => { editing = true; });
  $(id).addEventListener("change", async () => { editing = false; render(await api("/api/config", { [id]: $(id).value })); });
  $(id).addEventListener("blur", () => { editing = false; });
}
$("test").onclick = () => {
  $("test").textContent = "…";
  const a = new Audio("/api/voice.wav?voice=" + encodeURIComponent($("voice").value) + "&t=" + Date.now());
  a.onended = a.onerror = () => { $("test").textContent = "Test"; };
  a.play().catch(() => { $("test").textContent = "Test"; });
};
refresh();
setInterval(refresh, 1000);

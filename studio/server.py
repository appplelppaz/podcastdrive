#!/usr/bin/env python3
"""Listening Studio: この Mac の中だけで動く小さな画面（http://127.0.0.1:8760）。

訳・語彙・解説（.json）は Studio では作らない。選んだ回の音声と字幕をライブラリに集め、
Claude に頼むためのプロンプトを作る。.json ができた回は、訳・解説の音声（Kokoro）を Studio が作る。

この Mac にある音声と字幕（ライブラリのフォルダと Apple Podcasts）を自動で探して候補に出し、
選んだものから Listening 用のファイルを Ready フォルダに作る。
"""
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import pipeline  # noqa: E402
import prompt as prompt_mod  # noqa: E402
from listening import tts  # noqa: E402

PORT = int(os.environ.get("STUDIO_PORT", "8760"))
HOME = os.path.expanduser("~")
DATA = os.environ.get("STUDIO_DATA") or (os.path.join(HOME, "Library", "Application Support", "ListeningStudio")
                                          if platform.system() == "Darwin" else os.path.join(HOME, ".listening-studio"))
CONFIG = os.path.join(DATA, "config.json")
MADE = os.path.join(DATA, "made.json")  # 作り終えた回の記録。できたファイルを Ready から動かしても、作り直さないため
DEFAULTS = {"library": "", "ready": os.path.join(HOME, "Desktop", "Listening", "Ready"), "level": "B2",
            "voice": "jf_alpha"}
JA_VOICES = ["jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo"]  # Kokoro


def mac_voices():
    """この Mac の日本語の声（iPhone と同じ Kyoko・Otoya など）。「mac:声の名前」で選ぶ。"""
    if not shutil.which("say"):
        return []
    try:
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    names = []
    for line in out.splitlines():
        m = __import__("re").match(r"^(.+?)\s+(ja[_-]JP)\b", line)
        if m:
            names.append("mac:" + m.group(1).strip())
    return names


def all_voices():
    return (JA_VOICES if tts._has_kokoro() else []) + mac_voices()


def voice_setup(voice):
    """(音声合成の種類, 声の指定)。mac: の声は macOS の say で読む。"""
    if voice.startswith("mac:"):
        return "say", {"ja": voice[4:]}
    engine = engine_name()
    return engine, ({"ja": voice} if engine == "kokoro" and voice in JA_VOICES else None)
SAMPLE = "この声で、訳と解説を読み上げます。聞き取りにくい語句と、文の組み立てを説明します。"

lock = threading.Lock()
state = {"queue": [], "current": None, "step": "", "done": 0, "total": 0, "log": [], "failed": {}, "stop": False}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if k in DEFAULTS and isinstance(v, str) and v})
    except (OSError, ValueError):
        pass
    if not cfg["library"]:
        cfg["library"] = pipeline.sources.find_library()
    return cfg


# 探すのは重い（フォルダの中すべてと Apple Podcasts のデータ）ので、数秒おきにだけやり直す
_scan = {"at": 0.0, "key": None, "eps": [], "problem": ""}


def scan(cfg, force=False):
    key = (cfg["library"], cfg["ready"])
    with lock:
        fresh = not force and _scan["key"] == key and time.time() - _scan["at"] < 8
    if not fresh:
        eps, problem = pipeline.scan(*key)
        with lock:
            _scan.update(at=time.time(), key=key, eps=eps, problem=problem)
    return _scan["eps"], _scan["problem"]


def save_config(cfg):
    os.makedirs(DATA, exist_ok=True)
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)


def load_made():
    try:
        with open(MADE, encoding="utf-8") as f:
            made = json.load(f)
        return made if isinstance(made, dict) else {}
    except (OSError, ValueError):
        return {}


def mark_made(name):
    made = load_made()
    made[name] = time.strftime("%Y-%m-%d")
    os.makedirs(DATA, exist_ok=True)
    with open(MADE + ".part", "w", encoding="utf-8") as f:
        json.dump(made, f, ensure_ascii=False, indent=1)
    os.replace(MADE + ".part", MADE)


def log(msg):
    with lock:
        state["log"] = (state["log"] + [str(msg)])[-60:]


def engine_name():
    return os.environ.get("STUDIO_VOICE_ENGINE") or tts.available_engine()


def checks():
    return {"ffmpeg": bool(shutil.which("ffmpeg")),
            "voice": engine_name() or "",
            "whisper": any(importlib.util.find_spec(m) for m in ("mlx_whisper", "faster_whisper"))}


def worker():
    while True:
        with lock:
            name = state["queue"].pop(0) if state["queue"] else None
            if name:
                state.update(current=name, step="", done=0, total=0, stop=False)
        if not name:
            threading.Event().wait(0.3)
            continue
        cfg = load_config()
        try:
            ep = next((e for e in scan(cfg)[0] if e["name"] == name), None)
            if not ep or not os.path.exists(ep["audio"]):
                raise RuntimeError("The audio file was not found")
            if not ep.get("json"):
                raise RuntimeError("No notes yet. Use Prompt and ask Claude first.")
            log("— " + name)

            def step(s, done=0, total=0):
                with lock:
                    state.update(step=s, done=done, total=total)

            engine, voices = voice_setup(cfg["voice"])
            pipeline.make(ep, ep["out"], os.path.join(DATA, "work"), voices, engine,
                          log=log, step=step, cancelled=lambda: state["stop"])
            mark_made(name)
            scan(cfg, force=True)
            log("Ready: " + name)
            with lock:
                state["failed"].pop(name, None)
        except pipeline.Cancelled:
            log("Stopped: " + name)
        except Exception as err:  # 画面に理由を出す
            traceback.print_exc()
            log("Failed: %s" % err)
            with lock:
                state["failed"][name] = str(err)[:300]
        finally:
            with lock:
                state.update(current=None, step="")


def snapshot():
    cfg = load_config()
    os.makedirs(cfg["ready"], exist_ok=True)
    with lock:
        s = {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in state.items()}
    eps = []
    found, problem = scan(cfg)
    made = load_made()
    for e in found:
        status = ("running" if e["name"] == s["current"] else "queued" if e["name"] in s["queue"]
                  else "failed" if e["name"] in s["failed"] else "ready" if e["done"] or e["name"] in made else "new")
        eps.append({"name": e["name"], "subtitles": bool(e["ttml"]), "notes": bool(e["json"]), "status": status,
                    "wordTimes": e.get("word_times", False),
                    "source": e["source"], "lang": e.get("lang", ""), "made": made.get(e["name"], ""),
                    "out": os.path.relpath(e["out"], cfg["library"]) if cfg["library"] and e["out"].startswith(cfg["library"]) else e["out"],
                    "error": s["failed"].get(e["name"], "")})
    return {"config": cfg,
            "voices": all_voices(), "episodes": eps,
            "checks": dict(checks(), apple=problem, library=bool(cfg["library"]) and os.path.isdir(cfg["library"])),
            "job": {"name": s["current"], "step": s["step"], "done": s["done"], "total": s["total"]}, "log": s["log"][-12:]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, body, ctype="application/json", code=200):
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def local(self):
        # この Mac のブラウザからだけ使える（ほかのサイトからの呼び出しは断る）
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        return host in ("127.0.0.1", "localhost") and (not origin or urlparse(origin).hostname in ("127.0.0.1", "localhost"))

    def do_GET(self):
        if not self.local():
            return self.send({"error": "forbidden"}, code=403)
        url = urlparse(self.path)
        if url.path == "/api/state":
            return self.send(snapshot())
        if url.path == "/api/voice.wav":
            voice = (parse_qs(url.query).get("voice") or [""])[0]
            if voice not in all_voices():
                voice = load_config()["voice"]
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "v.wav")
                try:
                    engine, voices = voice_setup(voice)
                    pipeline.voice_sample(SAMPLE, "ja", (voices or {}).get("ja"), out, engine)
                    with open(out, "rb") as f:
                        return self.send(f.read(), "audio/wav")
                except Exception as err:
                    return self.send({"error": str(err)[:300]}, code=500)
        name = {"/": "index.html", "/app.css": "app.css", "/app.js": "app.js"}.get(url.path)
        if not name:
            return self.send({"error": "not found"}, code=404)
        with open(os.path.join(HERE, "ui", name), "rb") as f:
            self.send(f.read(), {"html": "text/html; charset=utf-8", "css": "text/css", "js": "text/javascript"}[name.rsplit(".", 1)[1]])

    def do_POST(self):
        if not self.local():
            return self.send({"error": "forbidden"}, code=403)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            return self.send({"error": "bad request"}, code=400)
        path = urlparse(self.path).path
        if path == "/api/make":
            cfg = load_config()
            known = {e["name"] for e in scan(cfg)[0]}
            with lock:
                for n in body.get("names") or []:
                    if n in known and n not in state["queue"] and n != state["current"]:
                        state["queue"].append(n)
                        state["failed"].pop(n, None)
        elif path == "/api/prompt":
            # Apple Podcasts の回はライブラリの「言語/番組名」へ集めてから、Claude に頼むプロンプトを作る
            cfg = load_config()
            names = set(body.get("names") or [])
            picked = []
            try:
                for e in scan(cfg)[0]:
                    if e["name"] in names and not e.get("json"):
                        e = pipeline.collect(e, cfg["library"])
                        picked.append({"name": e["name"], "audio": e["audio"], "ttml": e["ttml"] if e.get("word_times") else None,
                                       "lang": e.get("lang", ""), "replace": bool(e.get("ttml")) and not e.get("word_times"),
                                       "json_out": os.path.join(os.path.dirname(e["audio"]), e["name"] + ".json")})
            except (RuntimeError, OSError) as err:
                return self.send(dict(snapshot(), error=str(err)[:300]))
            scan(cfg, force=True)
            text = prompt_mod.build(picked, cfg["level"], os.path.join(sys.prefix, "bin", "python")) if picked else ""
            return self.send(dict(snapshot(), prompt=text))
        elif path == "/api/stop":
            with lock:
                state["queue"] = []
                state["stop"] = True
        elif path == "/api/config":
            cfg = load_config()
            for k in ("library", "ready"):
                if isinstance(body.get(k), str) and body[k].strip():
                    cfg[k] = os.path.abspath(os.path.expanduser(body[k].strip()))
            if body.get("level") in ("B1", "B2", "C1"):
                cfg["level"] = body["level"]
            if body.get("voice") in all_voices():
                cfg["voice"] = body["voice"]
            save_config(cfg)
        elif path == "/api/show":
            # できたファイルを Finder で見せる
            cfg = load_config()
            ep = next((e for e in scan(cfg)[0] if e["name"] == body.get("name")), None)
            if ep and shutil.which("open"):
                target = pipeline._out_stem(ep["name"]) + ".listening.m4a"
                hit = next((os.path.join(d, target) for d in (ep["out"], cfg["ready"]) if os.path.exists(os.path.join(d, target))), None)
                subprocess.run(["open", "-R", hit] if hit else ["open", ep["out"] if os.path.isdir(ep["out"]) else cfg["ready"]], check=False)
        else:
            return self.send({"error": "not found"}, code=404)
        self.send(snapshot())


def main():
    threading.Thread(target=worker, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = "http://127.0.0.1:%d/" % PORT
    print("Listening Studio: " + url + "  (close this window to quit)")
    if not os.environ.get("STUDIO_NO_BROWSER"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

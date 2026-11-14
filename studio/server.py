#!/usr/bin/env python3
"""Listening Studio: この Mac の中だけで動く小さな画面（http://127.0.0.1:8760）。

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
from listening import tts  # noqa: E402

PORT = int(os.environ.get("STUDIO_PORT", "8760"))
HOME = os.path.expanduser("~")
DATA = os.environ.get("STUDIO_DATA") or (os.path.join(HOME, "Library", "Application Support", "ListeningStudio")
                                          if platform.system() == "Darwin" else os.path.join(HOME, ".listening-studio"))
CONFIG = os.path.join(DATA, "config.json")
DEFAULTS = {"library": "", "ready": os.path.join(HOME, "Desktop", "Listening", "Ready"), "drive": "", "level": "B2",
            "voice": "jf_alpha", "notes": "keep"}  # notes: keep = すでにある .json の訳・解説を使う / new = 作り直す
JA_VOICES = ["jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo"]
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
    if not cfg["drive"]:
        cfg["drive"] = pipeline.find_drive()
    if not cfg["library"]:
        cfg["library"] = pipeline.sources.find_library()
    return cfg


# 探すのは重い（フォルダの中すべてと Apple Podcasts のデータ）ので、数秒おきにだけやり直す
_scan = {"at": 0.0, "key": None, "eps": [], "problem": ""}


def scan(cfg, force=False):
    key = (cfg["library"], cfg["ready"], cfg["drive"])
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


def log(msg):
    with lock:
        state["log"] = (state["log"] + [str(msg)])[-60:]


def engine_name():
    return os.environ.get("STUDIO_VOICE_ENGINE") or tts.available_engine()


def checks():
    return {"claude": bool(shutil.which("claude")), "ffmpeg": bool(shutil.which("ffmpeg")),
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
            log("— " + name)

            def step(s, done=0, total=0):
                with lock:
                    state.update(step=s, done=done, total=total)

            engine = engine_name()
            pipeline.make(ep, cfg["ready"], os.path.join(DATA, "work"), cfg["level"],
                          {"ja": cfg["voice"]} if engine == "kokoro" else None, engine, fresh=cfg["notes"] == "new",
                          log=log, step=step, cancelled=lambda: state["stop"])
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
    for e in found:
        status = ("running" if e["name"] == s["current"] else "queued" if e["name"] in s["queue"]
                  else "failed" if e["name"] in s["failed"] else "ready" if e["done"] else "new")
        eps.append({"name": e["name"], "subtitles": bool(e["ttml"]), "notes": bool(e["json"]), "status": status,
                    "source": e["source"],
                    "error": s["failed"].get(e["name"], "")})
    drive_ok = bool(cfg["drive"]) and os.path.isdir(cfg["drive"])
    return {"config": cfg, "ready": [{"name": e["name"], "bytes": e["bytes"]} for e in pipeline.finished(cfg["ready"])],
            "drive": {"found": drive_ok, "count": len(pipeline.finished(cfg["drive"])) if drive_ok else 0},
            "voices": JA_VOICES if engine_name() == "kokoro" else [], "episodes": eps,
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
            if voice not in JA_VOICES:
                voice = None
            with tempfile.TemporaryDirectory() as tmp:
                out = os.path.join(tmp, "v.wav")
                try:
                    pipeline.voice_sample(SAMPLE, "ja", voice, out, engine_name())
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
        elif path == "/api/move":
            cfg = load_config()
            try:
                moved = pipeline.move_finished(set(body.get("names") or []), cfg["ready"], cfg["drive"])
                log("Moved to Drive: %d" % len(moved))
                scan(cfg, force=True)
            except (RuntimeError, OSError) as err:
                return self.send(dict(snapshot(), error=str(err)[:300]))
        elif path == "/api/stop":
            with lock:
                state["queue"] = []
                state["stop"] = True
        elif path == "/api/config":
            cfg = load_config()
            for k in ("library", "ready", "drive"):
                if isinstance(body.get(k), str) and body[k].strip():
                    cfg[k] = os.path.abspath(os.path.expanduser(body[k].strip()))
            if body.get("level") in ("B1", "B2", "C1"):
                cfg["level"] = body["level"]
            if body.get("voice") in JA_VOICES:
                cfg["voice"] = body["voice"]
            if body.get("notes") in ("keep", "new"):
                cfg["notes"] = body["notes"]
            save_config(cfg)
        elif path == "/api/open":
            folder = load_config().get(body.get("folder"))
            if folder and os.path.isdir(folder) and shutil.which("open"):
                subprocess.run(["open", folder], check=False)
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

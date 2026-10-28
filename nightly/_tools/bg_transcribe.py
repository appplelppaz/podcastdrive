"""
文字起こしを裏で動かす（ChatGPT の利用上限で作業が止まっても、文字起こしは続く）。

    python _tools/bg_transcribe.py start "<音声>" [--lang zh] [--prompt "..."]   # 裏で開始してすぐ戻る
    python _tools/bg_transcribe.py status                                      # 状態の一覧（JSON）
    python _tools/bg_transcribe.py run "<音声>" [...]                           # その場で実行（終わるまで戻らない）

状態は _work/transcribe/<音声の名前>.json:
  running（1分ごとに heartbeat を更新）/ done / failed / interrupted（heartbeat が3分以上止まった）
終わると音声と同じ名前の .ttml ができる（作りかけは .ttml.part）。
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("PODCAST_ROOT") or os.path.dirname(HERE)
STATE_DIR = os.path.join(ROOT, "_work", "transcribe")
sys.path.insert(0, os.path.join(HERE, "scripts"))


def now():
    return datetime.now().isoformat(timespec="seconds")


def state_path(audio):
    return os.path.join(STATE_DIR, os.path.splitext(os.path.basename(audio))[0] + ".json")


def read_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    if st.get("state") == "running":
        try:
            age = (datetime.now() - datetime.fromisoformat(st["heartbeat"])).total_seconds()
        except (KeyError, ValueError):
            age = 1e9
        if age > 180:
            st["state"] = "interrupted"
    return st


def write_state(path, st):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def run(audio, extra):
    path = state_path(audio)
    st = {"audio": audio, "args": extra, "state": "running", "started": now(), "heartbeat": now(), "pid": os.getpid()}
    write_state(path, st)
    stop = threading.Event()

    def beat():
        while not stop.wait(60):
            st["heartbeat"] = now()
            write_state(path, st)
    threading.Thread(target=beat, daemon=True).start()
    log = os.path.join(STATE_DIR, os.path.splitext(os.path.basename(audio))[0] + ".log")
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "scripts", "transcribe.py"), audio] + extra,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        with open(log, "w", encoding="utf-8") as f:
            f.write(r.stdout + "\n" + r.stderr)
        last = (r.stdout.strip().splitlines() or [""])[-1]
        if r.returncode == 0:
            st.update(state="done", result=json.loads(last) if last.startswith("{") else last)
        else:
            st.update(state="failed", error=(r.stderr.strip().splitlines() or [last or "unknown"])[-1])
    except Exception:  # noqa: BLE001
        st.update(state="failed", error=traceback.format_exc(limit=2))
    stop.set()
    st["finished"] = now()
    write_state(path, st)
    return st


def start(audio, extra):
    path = state_path(audio)
    st = read_state(path)
    if st and st["state"] == "running":
        return {"started": False, "reason": "already running", "state": st}
    if os.path.exists(os.path.splitext(audio)[0] + ".ttml"):
        return {"started": False, "reason": "TTML already exists"}
    write_state(path, {"audio": audio, "args": extra, "state": "running", "started": now(), "heartbeat": now()})
    cmd = [sys.executable, os.path.abspath(__file__), "run", audio] + extra
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": ROOT}
    if os.name == "nt":
        kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen(cmd, **kw)
    return {"started": True, "pid": p.pid, "state_file": path}


def status():
    out = []
    if os.path.isdir(STATE_DIR):
        for name in sorted(os.listdir(STATE_DIR)):
            if name.endswith(".json"):
                st = read_state(os.path.join(STATE_DIR, name))
                if st:
                    out.append(st)
    return out


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["start", "status", "run"])
    ap.add_argument("audio", nargs="?")
    a, extra = ap.parse_known_args()
    if a.cmd == "status":
        res = status()
    elif not a.audio:
        raise SystemExit("音声ファイルを指定してください")
    elif a.cmd == "start":
        res = start(os.path.abspath(a.audio), extra)
    else:
        res = run(os.path.abspath(a.audio), extra)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()

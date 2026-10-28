#!/usr/bin/env bash
# 架空の教材を作って準備し、アプリをブラウザで確認する。
# 必要: python3, ffmpeg, espeak-ng, node, playwright（Chromium）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'kill "${SERVER:-0}" 2>/dev/null || true' EXIT

A="[Demo Radio] Morning market check (20260101)"
B="[Demo Radio] Weather and travel (20260102)"
python3 "$ROOT/tools/tests/make_fixture.py" "$WORK/materials" >/dev/null
for n in "$A" "$B"; do
  python3 "$ROOT/tools/prepare_listening.py" "$WORK/materials/$n.json" --engine espeak-ng >/dev/null
done
# 簡易モード用（再生用音声の参照がない .listening.json）
python3 "$ROOT/tools/build_listening.py" "$WORK/materials/$B.json" --out "$WORK/b-direct.listening.json" --fresh >/dev/null
mkdir -p "$WORK/ascii"
cp "$WORK/materials/$A.listening.json" "$WORK/ascii/a.listening.json"
cp "$WORK/materials/$A.listening.m4a" "$WORK/ascii/a.listening.m4a"
cp "$WORK/b-direct.listening.json" "$WORK/ascii/b-direct.listening.json"
cp "$WORK/materials/$B.mp3" "$WORK/ascii/b.mp3"

# アプリと教材を同じサーバーから配る
mkdir -p "$WORK/site"
cp -r "$ROOT/app/." "$WORK/site/"
ln -s "$WORK/materials" "$WORK/site/materials"
PORT="${PORT:-8765}"
(cd "$WORK/site" && exec python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1) &
SERVER=$!
sleep 1
NODE_PATH="${NODE_PATH:-$(npm root -g)}" node "$ROOT/app/tests/e2e.cjs" "$WORK" "http://localhost:$PORT/"
echo "スクリーンショット: $WORK/setup-390.png $WORK/player-390.png"

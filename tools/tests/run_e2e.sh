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
# 以前の方式（準備済みの .listening.json ＋ .listening.m4a）の確認用
python3 "$ROOT/tools/prepare_listening.py" "$WORK/materials/$A.json" --engine espeak-ng >/dev/null
# Playwright は「[]」を含むパスのファイルを選べないので、同じ内容の短い名前のコピーを使う
mkdir -p "$WORK/ascii"
for x in json ttml mp3; do
  cp "$WORK/materials/$A.$x" "$WORK/ascii/a.$x"
  cp "$WORK/materials/$B.$x" "$WORK/ascii/b.$x"
done

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

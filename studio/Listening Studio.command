#!/bin/bash
# Listening Studio を開く。ダブルクリックで使う。
# 最初の1回だけ、必要なもの（Python の部品・音声のモデル）をこの Mac に入れる（数分・数GB）。
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.claude/local:$PATH"
DATA="$HOME/Library/Application Support/ListeningStudio"
VENV="$DATA/venv"
STAMP="$DATA/requirements.sha"

stop() { echo; echo "$1"; echo; read -r -p "Press Return to close." _; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# python.org の Python は、入れただけでは https の証明書を確かめられない（CERTIFICATE_VERIFY_FAILED になる）。
# Python の部品に入っている証明書（certifi）を使うようにする
use_certs() {
  local ca
  ca="$("$VENV/bin/python" -c 'import certifi; print(certifi.where())' 2>/dev/null)"
  if [ -n "$ca" ]; then export SSL_CERT_FILE="$ca" REQUESTS_CA_BUNDLE="$ca"; fi
}

have brew || stop "Homebrew is needed. Install it from https://brew.sh and open this again."
for pair in "python3.12 python@3.12" "ffmpeg ffmpeg"; do
  set -- $pair
  if ! have "$1"; then
    echo "Installing $2…"
    brew install "$2" || stop "Could not install $2."
  fi
done
have claude || echo "Claude Code was not found. Install it and sign in (run: claude) to make notes."

WANT="$(shasum requirements.txt | cut -d' ' -f1)"
if [ ! -x "$VENV/bin/python" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
  echo "Setting up (first time only, a few minutes)…"
  mkdir -p "$DATA"
  [ -x "$VENV/bin/python" ] || python3.12 -m venv "$VENV" || stop "Could not set up Python."
  "$VENV/bin/python" -m pip install --quiet --upgrade pip || stop "Could not set up Python."
  "$VENV/bin/python" -m pip install --quiet -r requirements.txt || stop "Could not install the parts Studio needs."
  use_certs
  "$VENV/bin/python" -m unidic download || stop "Could not download the Japanese dictionary."
  "$VENV/bin/python" -m spacy download en_core_web_sm --quiet || stop "Could not download the English dictionary."
  echo "$WANT" > "$STAMP"
fi

export PATH="$VENV/bin:$PATH"
use_certs
exec "$VENV/bin/python" server.py

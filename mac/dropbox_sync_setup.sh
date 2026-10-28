#!/bin/bash
# ~/Desktop/Public/PodcastsChatGPT に入ったファイルを、Dropbox の PodcastsChatGPT フォルダへ
# 自動でコピーし続ける設定をする（Mac のバックグラウンドの仕組み launchd を使う）。
#
# - フォルダに変化があるとすぐ、ほかに念のため 10 分ごとにコピーする（Mac が起動している間）。
# - 新しいファイルと、中身が変わったファイルをコピーする。PodcastsChatGPT から移したり消したりしても、
#   Dropbox 側のファイルは消さない。名前が「.」で始まるファイル（記録用）はコピーしない。
# - Dropbox へのアップロードは Dropbox のアプリがする（Mac に入れて、ログインしておく）。
# - コピーは /usr/bin/rsync がする。デスクトップを読むため、rsync にフルディスクアクセスが要る（初回に案内する）。
# - もう一度開くと、設定し直すか、止めるかを選べる。
set -u

FOLDER=PodcastsChatGPT
SRC="${PUBLIC_DIR:-$HOME/Desktop/Public}/$FOLDER"
LABEL=com.podcastdrive.podcastschatgpt.dropbox
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$LABEL.plist"
LOG="$HOME/Library/Logs/PodcastsChatGPT-Dropbox.log"
RSYNC="${RSYNC:-/usr/bin/rsync}"
DOMAIN="gui/$(id -u)"
FDA_PANE="x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles"

open_mac() { [ -z "${NO_OPEN:-}" ] && open "$@"; }

find_dropbox() {
  if [ -n "${DROPBOX_DIR:-}" ]; then
    echo "$DROPBOX_DIR"
    return
  fi
  local d
  for d in "$HOME/Library/CloudStorage/Dropbox" "$HOME/Library/CloudStorage/Dropbox-Personal" \
           "$HOME"/Library/CloudStorage/Dropbox* "$HOME/Dropbox"; do
    if [ -d "$d" ]; then
      echo "$d"
      return
    fi
  done
}

stop_sync() {
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
  rm -f "$PLIST"
  echo "Dropbox への自動コピーを止めました（Dropbox にあるファイルはそのままです）。"
}

if [ -f "$PLIST" ]; then
  echo "Dropbox への自動コピーは、もう設定されています。"
  printf "止めるときは s と入力して Enter。設定し直すときはそのまま Enter: "
  read -r ans
  if [ "$ans" = s ] || [ "$ans" = S ]; then
    stop_sync
    exit 0
  fi
fi

DROPBOX="$(find_dropbox)"
if [ -z "$DROPBOX" ]; then
  echo "Dropbox のフォルダが見つかりません。"
  echo "Dropbox のアプリ（https://www.dropbox.com/install）を入れてログインし、"
  echo "Finder のサイドバーに Dropbox が出てから、もう一度これを開いてください。"
  open_mac "https://www.dropbox.com/install"
  exit 1
fi
DST="$DROPBOX/$FOLDER"
mkdir -p "$SRC" "$DST" "$AGENTS" "$(dirname "$LOG")" || exit 1

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$RSYNC</string>
    <string>-rt</string>
    <string>--exclude=.*</string>
    <string>$SRC/</string>
    <string>$DST/</string>
  </array>
  <key>WatchPaths</key><array><string>$SRC</string></array>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null
if ! launchctl bootstrap "$DOMAIN" "$PLIST"; then
  echo "自動コピーの登録に失敗しました。Mac を再起動してから、もう一度開いてください。"
  exit 1
fi

# 一度動かして、デスクトップを読めるか（フルディスクアクセスがあるか）を確かめる
check() {
  : > "$LOG"
  launchctl kickstart -k "$DOMAIN/$LABEL" >/dev/null 2>&1
  sleep 3
  if grep -qiE "not permitted|permission denied" "$LOG"; then
    return 1
  fi
  local f
  for f in "$SRC"/*; do
    [ -f "$f" ] || continue
    [ -f "$DST/$(basename "$f")" ] || return 2
  done
  return 0
}

while true; do
  check
  rc=$?
  if [ $rc -eq 0 ]; then
    break
  fi
  if [ $rc -eq 2 ]; then
    echo "（まだコピーの途中かもしれません。大きなファイルは少し時間がかかります）"
    break
  fi
  echo
  echo "============================================================"
  echo "コピー係（rsync）にデスクトップを読む許可がありません。"
  echo "1. 開いた「フルディスクアクセス」の画面で、一覧の下の「＋」を押す"
  echo "2. Command + Shift + G を押して  /usr/bin/rsync  と入力し、「開く」を押す"
  echo "   （または、一緒に開いた Finder の中の rsync を一覧へドラッグする）"
  echo "3. 一覧に出た rsync をオンにする"
  echo "============================================================"
  open_mac "$FDA_PANE"
  open_mac /usr/bin
  printf "できたら Enter（やめるときは q と入力して Enter）: "
  read -r ans
  if [ "$ans" = q ] || [ "$ans" = Q ]; then
    echo "設定は残してあります。許可をしてから、もう一度これを開くと確かめ直します。"
    exit 1
  fi
done

echo
echo "設定しました。"
echo "  コピー元: $SRC"
echo "  コピー先: $DST"
echo "PodcastsChatGPT にファイルが入ると、自動で Dropbox のこのフォルダへコピーし、Dropbox のアプリがアップロードします。"
echo "PodcastsChatGPT から移したり消したりしても、Dropbox 側には残ります（要らなくなったら Dropbox 側で消してください）。"
echo "無料の Dropbox（2GB）がいっぱいになると、アップロードが止まります。"
echo "止めるときは、もう一度これを開いて s を選んでください。"
open_mac "$DST"
exit 0

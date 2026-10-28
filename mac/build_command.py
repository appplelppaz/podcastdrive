"""collect_for_chatgpt.py を1つの .command ファイル（ダブルクリックでターミナルが開いて動く）にまとめ、
dropbox_sync_setup.sh（Dropbox への自動コピーの設定）と一緒に zip にする。

    python3 mac/build_command.py [出力フォルダ]   # 既定: mac/dist
"""
import os
import stat
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "PodcastsChatGPTに集める.command"
DROPBOX_NAME = "PodcastsChatGPTをDropboxへ自動コピー.command"

HEADER = """#!/bin/bash
# Apple Podcasts でダウンロードした音声と字幕（TTML）を
# ~/Desktop/Public/PodcastsChatGPT に集める。ダブルクリックで動く。
cd "$HOME" || exit 1
PY="$(command -v python3)"
if [ -z "$PY" ]; then
  echo "python3 が見つかりません。ターミナルで  xcode-select --install  を実行してから、もう一度開いてください。"
  exit 1
fi
"$PY" - <<'PYCODE'
"""

FOOTER = """PYCODE
echo
echo "終わりました。このウィンドウは閉じてかまいません。"
"""


def build(out_dir):
    with open(os.path.join(HERE, "collect_for_chatgpt.py"), encoding="utf-8") as f:
        code = f.read()
    assert "PYCODE" not in code
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, NAME)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER + code + FOOTER)
    with open(os.path.join(HERE, "dropbox_sync_setup.sh"), encoding="utf-8") as f:
        setup = f.read()
    dropbox_path = os.path.join(out_dir, DROPBOX_NAME)
    with open(dropbox_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(setup)
    for p in (path, dropbox_path):
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    # zip に実行できる印を残す（Mac で展開したとき、そのままダブルクリックで動く）
    zpath = os.path.join(out_dir, "PodcastsChatGPT.zip")
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in (path, dropbox_path):
            info = zipfile.ZipInfo(os.path.basename(p))
            info.external_attr = (0o100755 << 16)
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(p, "rb") as f:
                z.writestr(info, f.read())
    return path, dropbox_path, zpath


if __name__ == "__main__":
    for p in build(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "dist")):
        print(p)

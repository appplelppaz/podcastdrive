"""共通スクリプトを各スキルへ写し、アップロード用の .skill ファイルを作る。

    python3 skills/build.py [出力フォルダ]   # 既定: skills/dist

共通スクリプトの元は podcast-json/scripts/ にある。ttml-to-json と podcast-transcribe は
単体でも動くように同じものを持つ（直すときは元だけを直してこのスクリプトを実行する）。
"""
import os
import shutil
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = ("podcast-json", "ttml-to-json", "podcast-transcribe")
SHARED = ("parse_ttml.py", "add_word_timings.py", "validate_json.py")
SOURCE = os.path.join(HERE, "podcast-json", "scripts")


NIGHTLY = os.path.join(os.path.dirname(HERE), "nightly", "_tools")


def sync():
    for name in SKILLS[1:]:
        dest = os.path.join(HERE, name, "scripts")
        os.makedirs(dest, exist_ok=True)
        for f in SHARED:
            shutil.copy2(os.path.join(SOURCE, f), os.path.join(dest, f))
    # 夜間処理（ChatGPT Work）の _tools にも同じものと文字起こしを入れる
    dest = os.path.join(NIGHTLY, "scripts")
    os.makedirs(dest, exist_ok=True)
    for f in SHARED:
        shutil.copy2(os.path.join(SOURCE, f), os.path.join(dest, f))
    shutil.copy2(os.path.join(HERE, "podcast-transcribe", "scripts", "transcribe.py"), os.path.join(dest, "transcribe.py"))


def package_tools(out_dir):
    """作業フォルダに置く _tools を zip にする（展開すると _tools フォルダができる）。"""
    path = os.path.join(out_dir, "podcast_tools.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirs, files in os.walk(NIGHTLY):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for f in sorted(files):
                if not f.endswith(".pyc"):
                    full = os.path.join(dirpath, f)
                    z.write(full, os.path.join("_tools", os.path.relpath(full, NIGHTLY)))
    return path


def package(name, out_dir):
    """skill-creator の package_skill.py と同じ形（フォルダ名から始まるパスの zip）。"""
    root = os.path.join(HERE, name)
    path = os.path.join(out_dir, name + ".skill")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in ("__pycache__", "evals", "node_modules"))
            for f in sorted(files):
                if f.endswith(".pyc") or f == ".DS_Store":
                    continue
                full = os.path.join(dirpath, f)
                z.write(full, os.path.relpath(full, HERE))
    return path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "dist")
    os.makedirs(out, exist_ok=True)
    sync()
    for name in SKILLS:
        print(package(name, out))
    print(package_tools(out))

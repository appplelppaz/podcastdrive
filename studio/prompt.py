"""選んだエピソードの教材 .json を、Claude に作ってもらうためのプロンプト。

Studio は Claude を呼ばない（時間と利用量がかかるので）。ファイルの場所と手順をまとめた文を作り、
持ち主がそれを Claude（Mac の Claude Code など）に貼って頼む。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def build(episodes, level="B2", python=None):
    """episodes: [{"name", "audio", "ttml", "json_out", "lang"}]"""
    py = python or sys.executable or "python3"
    q = lambda p: '"%s"' % p  # noqa: E731
    with_ttml = [e for e in episodes if e.get("ttml")]
    without = [e for e in episodes if not e.get("ttml")]
    lines = [
        "ポッドキャストの語学教材 JSON を作ってください（%d 件）。" % len(episodes),
        "",
        "## 作り方",
        "",
        "- 訳・語彙・解説の決まりは %s にあります。最初に読んで、そのとおりに作ってください（学習者のレベルは %s）。" % (q(os.path.join(HERE, "guide.md")), level),
        "- 文の分け方と時刻は、字幕（.ttml）から次のコマンドで取り出します。文の数・順・時刻は変えません。",
        "  `%s %s <字幕.ttml>`（Python から使うときは `from ttml import parse_ttml, write_episode_json`。場所: %s）" % (py, q(os.path.join(HERE, "ttml.py")), q(HERE)),
        "- 書き出す形（1文ごと）: `id` `begin` `end` `original` `translation` `words`（[{word, meaning, pos, reading?}]） `grammar` "
        "`speak`（{\"vocab\": [[語句, 日本語の意味]] を2つまで, \"grammar\": [[言語, 文], …]}） `wordTimings`（parse_ttml が返したもの）。",
        "  `metadata` は title・channel・episode・publishDate（YYYY-MM-DD）・sourceLanguage・targetLanguage（ja）。",
        "  書き出しは `write_episode_json(output, path)` を使ってください。",
        "- 長いエピソードは、200文ずつ分けて作ってかまいません。訳がずれないよう、各文の原文と見比べてください。",
        "",
        "## 作ったら確かめる（エラーと指摘がなくなるまで直す）",
        "",
        "1. `%s %s <教材.json> <字幕.ttml>` … 形・時刻・訳のずれ" % (py, q(os.path.join(HERE, "validate.py"))),
        "2. `%s %s <教材.json> --level %s` … rare（説明がない難しい語）・easy（易しすぎる説明）・no meaning・gloss（訳に原語）" % (py, q(os.path.join(HERE, "review.py")), level),
        "   rare はそのレベルの学習者に必要なら words に足し、easy は外してください（熟語・句動詞は残してよい）。",
        "",
    ]
    if without:
        lines += [
            "## 字幕がない回",
            "",
            "先に音声から、単語ごとの時刻が付いた字幕を作ってください（数分〜十数分かかります）。",
            "`%s %s <音声> --out <字幕.ttml>`（言語が分かっていれば `--lang en` など）" % (py, q(os.path.join(HERE, "transcribe.py"))),
            "",
        ]
    lines += ["## エピソード", ""]
    for i, e in enumerate(with_ttml + without, 1):
        lines.append("%d. %s" % (i, e["name"]))
        lines.append("   - 音声: %s" % q(e["audio"]))
        if e.get("ttml"):
            lines.append("   - 字幕: %s" % q(e["ttml"]))
        else:
            old = " （単語の時刻がない古い字幕があるので `--force` で作り直す）" if e.get("replace") else ""
            lines.append("   - 字幕: まだない → %s に作る%s" % (q(os.path.splitext(e["json_out"])[0] + ".ttml"), old))
        if e.get("lang"):
            lines.append("   - 言語: %s" % e["lang"])
        lines.append("   - 書き出し先: %s" % q(e["json_out"]))
    lines += ["", "全部できたら、各回の文の数と、確かめた結果を短く報告してください。"]
    return "\n".join(lines)

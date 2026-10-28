"""既存の教材JSON（＋TTML・音声）から「エピソード名 (日付).listening.json」を作る。

元ファイルは変更しない。既存の .listening.json があれば、作者による解説（source="authored"）と
事前生成した音声の参照を引き継ぎ、原文・訳が変わった区間だけ作り直す。
"""

import argparse
import hashlib
import json
import os
import re
import sys

from . import FORMAT_NAME, FORMAT_VERSION, GENERATOR_VERSION
from . import source as src
from . import speech

AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav")
MAX_MERGED_SECONDS = 30.0


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def base_name(json_path):
    name = os.path.basename(json_path)
    return name[:-5] if name.endswith(".json") else name


def find_sibling(json_path, exts):
    stem = os.path.join(os.path.dirname(json_path), base_name(json_path))
    for ext in exts:
        if os.path.exists(stem + ext):
            return stem + ext
    return None


def episode_stem(meta, json_path):
    """出力ファイル名の幹。エピソード名を先頭にして見つけやすくする。例: "Iran's economy - Counting the cost of war (2026-01-01)"

    番組名はフォルダで分かれているので入れない。日付は同名のエピソードを区別するために付ける。
    """
    base = base_name(json_path)
    m = re.match(r"^\[(.+?)\]\s+(.+?)\s+\((\d{8})\)$", base)
    episode = (meta.get("episode") or (m.group(2) if m else "") or base).strip()
    date = (meta.get("publishDate") or "").strip()
    if not date and m:
        date = "%s-%s-%s" % (m.group(3)[:4], m.group(3)[4:6], m.group(3)[6:])
    name = re.sub(r"\s*:\s*", " - ", episode)          # Mac・Drive で使えない「:」
    name = re.sub(r'[\\/]', "-", name)
    name = re.sub(r'[?*"<>|\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")[:120] or base
    return "%s (%s)" % (name, date) if date else name


def listening_path_for(json_path, meta=None):
    """「エピソード名 (日付).listening.json」。元のJSONと同じフォルダに置く。"""
    if meta is None:
        with open(json_path, encoding="utf-8") as f:
            raw = json.load(f)
        meta = raw.get("metadata", {}) if isinstance(raw, dict) else {}
    return os.path.join(os.path.dirname(json_path), episode_stem(meta, json_path) + ".listening.json")


def material_id(meta, json_path):
    digest = hashlib.sha1(base_name(json_path).encode("utf-8")).hexdigest()[:8]
    channel = re.sub(r"[^a-z0-9]+", "-", (meta.get("channel") or "").lower()).strip("-")[:24]
    date = (meta.get("publishDate") or "").replace("-", "")
    m = re.search(r"\((\d{8})\)$", base_name(json_path))
    if not date and m:
        date = m.group(1)
    return "-".join(p for p in (channel or "material", date, digest) if p)


def text_hash(original, translation):
    return hashlib.sha1(("%s\n%s" % (original, translation)).encode("utf-8")).hexdigest()[:12]


def merge_fragments(segs, lang):
    """文の途中で切れている区間を、次の区間とまとめる（時刻は元の区間の端をそのまま使う）。

    まとめるのは「文末記号がなく、かつ次が小文字（ラテン文字）や読点で続く」場合だけ。
    訳はつなげるだけで、意味を省略・改変しない。
    """
    out = []
    for seg in segs:
        prev = out[-1] if out else None
        if prev is not None and _continues(prev, seg, lang) \
                and seg["end"] - prev["begin"] <= MAX_MERGED_SECONDS:
            joiner = "" if speech.lang_base(lang) in ("zh", "ja") else " "
            prev["original"] = prev["original"] + joiner + seg["original"]
            prev["translation"] = prev["translation"] + seg["translation"]
            prev["words"] = prev["words"] + seg["words"]
            prev["grammar"] = "\n".join(g for g in (prev["grammar"], seg["grammar"]) if g)
            if seg.get("pinyin"):
                prev["pinyin"] = (prev.get("pinyin", "") + " " + seg["pinyin"]).strip()
            prev["end"] = seg["end"]
            prev["sourceIds"].append(seg["id"])
            continue
        seg = dict(seg)
        seg["sourceIds"] = [seg["id"]]
        out.append(seg)
    return out


def _continues(prev, seg, lang):
    text = prev["original"].rstrip()
    if src.is_sentence_end(text):
        return False
    nxt = seg["original"].lstrip()
    if text.endswith((",", "，", "、", ";", "；", ":", "：")):
        return True
    return speech.lang_base(lang) in speech.LATIN_LANGS and bool(re.match(r"[a-zà-ÿ]", nxt))


def clip_bounds(segs, i, words, duration):
    """元音声を切り出す範囲。前後に実際の無音（単語のない隙間）があるときだけ少し余白を取る。"""
    seg = segs[i]
    prev_end = segs[i - 1]["end"] if i > 0 else 0.0
    next_begin = segs[i + 1]["begin"] if i + 1 < len(segs) else (duration or seg["end"] + 0.3)
    if words:
        before = [w[1] for w in words if w[1] <= seg["begin"] + 1e-3]
        after = [w[0] for w in words if w[0] >= seg["end"] - 1e-3]
        if before:
            prev_end = max(prev_end, before[-1])
        if after:
            next_begin = min(next_begin, after[0])
    lead = max(0.0, min(0.12, (seg["begin"] - prev_end) / 2))
    tail = max(0.0, min(0.25, (next_begin - seg["end"]) / 2))
    begin = max(0.0, seg["begin"] - lead)
    end = seg["end"] + tail
    if duration:
        end = min(end, duration)
    return round(begin, 3), round(end, 3)


def word_timings(seg, words):
    """字幕の単語ごとの強調表示に使う、元音声での単語の時刻 [[開始, 終了, 単語], ...]。

    TTMLの単語列が原文と一致する区間だけ（一致しなければ強調しない）。
    """
    inside = [w for w in words if w[0] >= seg["begin"] - 1e-3 and w[1] <= seg["end"] + 1e-3]
    if not inside or src._norm(" ".join(w[2] for w in inside)) != src._norm(seg["original"]):
        return None
    return [[round(b, 3), round(e, 3), t] for b, e, t in inside]


def segment_key(seg):
    """作者の解説と前回の結果を対応づける鍵。元の区間id（まとめたときは 1+2、分けたときは 10#1）。"""
    key = "+".join(str(x) for x in seg["sourceIds"])
    return key + ("#%d" % seg["part"] if seg.get("part") else "")


FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")


def _plain_numbers(text):
    t = text.translate(FULLWIDTH)
    t = re.sub(r"(?<=\d)[.,  '](?=\d{3}\b)", "", t)  # 50,000 / 50.000 / 50 000
    # 5万 / 4万8300 / 1000億 → 数値
    def kanji(m):
        total = 0
        for num, unit in re.findall(r"(\d+)(億|万)?", m.group(0)):
            total += int(num) * {"億": 10 ** 8, "万": 10 ** 4, "": 1}[unit]
        return str(total)
    return re.sub(r"\d+(?:億|万)(?:\d+(?:万)?)*", kanji, t)


def _numbers(text):
    t = _plain_numbers(text)
    # 100 billion → 1000億 のように桁の言い方が変わる数は照合しない
    t = re.sub(r"\d+(?:\.\d+)?\s*(?:billion|million|trillion|thousand|bn|m|mil|millones|milliards?)\b",
               "", t, flags=re.I)
    return set(re.findall(r"\d{2,}", t))


def numbers_consistent(original, translation):
    """原文の2桁以上の数字が訳にもあるか（訳のずれを見つける目安）。"""
    nums = _numbers(original)
    if not nums:
        return True
    return nums <= set(re.findall(r"\d{2,}", _plain_numbers(translation)))


def load_authored(path):
    """作者（人またはAI）による聞くための解説。{"<元の区間id>": {"vocab": [...], "grammar": "..."}}"""
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "segments" in data and isinstance(data["segments"], dict):
        data = data["segments"]
    return {str(k): v for k, v in data.items()}


def convert(json_path, ttml_path=None, audio_path=None, out_path=None, authored_path=None,
            merge=True, previous=None, max_explanation_seconds=20.0, original_json_path=None, log=print):
    ep = src.load_episode_json(json_path)
    meta = ep["metadata"]
    warnings = list(ep["warnings"])
    source_lang = meta.get("sourceLanguage") or ""
    target_lang = meta.get("targetLanguage") or "ja"
    ttml = src.load_ttml(ttml_path) if ttml_path else None
    if ttml and not source_lang:
        source_lang = ttml["lang"]
    if not source_lang:
        raise ValueError("metadata.sourceLanguage も TTML の xml:lang もないため、教材の言語が分かりません")
    if ttml and ttml["lang"] and speech.lang_base(ttml["lang"]) != speech.lang_base(source_lang):
        warnings.append("metadata.sourceLanguage=%s と TTML の xml:lang=%s が異なります（metadata を採用）"
                        % (source_lang, ttml["lang"]))

    duration = None
    audio_info = None
    if audio_path:
        audio_info = {"file": os.path.basename(audio_path), "sha256": sha256_file(audio_path)}
    if ttml and ttml["duration"]:
        duration = ttml["duration"]

    segs = ep["segments"]
    for seg in segs:
        b, e, info = src.align_segment(seg, ttml)
        seg["begin"], seg["end"], seg["alignment"] = b, e, info
        if info.get("textMatches") is False:
            warnings.append("区間 %s: 原文とTTMLの単語列が一致しません（時刻はJSONのまま）" % seg["id"])
    expanded = []
    for seg in segs:
        pieces = src.split_by_ttml_sentences(seg, ttml)
        if pieces:
            for piece in pieces:
                piece["alignment"] = dict(seg["alignment"], method="ttml-sentences", splitFrom=seg["id"])
            expanded.extend(pieces)
            log("区間 %s をTTMLの文境界で %d つに分けました" % (seg["id"], len(pieces)))
            continue
        if seg.get("translationParts"):
            warnings.append("区間 %s: 文ごとの訳がありますが、TTMLの文境界と一致しないため分けません" % seg["id"])
            if not seg["translation"]:
                seg["translation"] = "".join(seg["translationParts"])
        expanded.append(seg)
    segs = expanded
    suspicious = [s["id"] for s in segs if not numbers_consistent(s["original"], s["translation"])]
    with_numbers = sum(1 for s in segs if _numbers(s["original"]))
    if suspicious:
        msg = "数字が訳に見当たらない区間: %s" % ", ".join(str(x) for x in suspicious[:20])
        if with_numbers and len(suspicious) >= 3 and len(suspicious) / with_numbers >= 0.3:
            msg = "訳が原文とずれている可能性が高いです。" + msg
        warnings.append(msg)
    if merge:
        before = len(segs)
        segs = merge_fragments(segs, source_lang)
        if len(segs) != before:
            log("文の途中で切れていた区間を %d 件まとめました" % (before - len(segs)))
    else:
        for seg in segs:
            seg["sourceIds"] = [seg["id"]]

    authored = load_authored(authored_path)
    prev_by_key = {}
    if previous:
        for ps in previous.get("segments", []):
            prev_by_key[(ps.get("key") or "",)] = ps

    words = ttml["words"] if ttml else []
    out_segs = []
    counts = {"authored": 0, "auto": 0, "kept": 0}
    for i, seg in enumerate(segs):
        key_str = segment_key(seg)
        key = (key_str,)
        th = text_hash(seg["original"], seg["translation"])
        listening = None
        a = authored.get(key_str)
        if a is not None:
            listening = {"source": "authored", "vocab": [dict(v) for v in a.get("vocab", [])],
                         "grammar": a.get("grammar", "")}
            if a.get("translationSpeech"):
                listening["translationSpeech"] = a["translationSpeech"]
            counts["authored"] += 1
        elif key in prev_by_key and prev_by_key[key].get("textHash") == th \
                and prev_by_key[key].get("listening", {}).get("source") == "authored":
            listening = prev_by_key[key]["listening"]
            counts["kept"] += 1
        else:
            listening = speech.auto_listening(seg, source_lang)
            counts["auto"] += 1
        for v in listening.get("vocab", []):
            v.setdefault("note", "")
        if len(listening.get("vocab", [])) > 2:
            warnings.append("区間 %s: 語彙が3つ以上あるため先頭2つに絞りました" % seg["id"])
            listening["vocab"] = listening["vocab"][:2]
        listening, notes = speech.fit_budget(listening, source_lang, target_lang, max_explanation_seconds)
        for n in notes:
            warnings.append("区間 %s: 解説が長いため%s" % (seg["id"], n))
        est = speech.explanation_seconds(listening, source_lang, target_lang)
        listening["estimatedSeconds"] = round(est, 1)

        cb, ce = clip_bounds(segs, i, words, duration)
        timings = word_timings(seg, words)
        display = {"words": seg["words"], "grammar": seg["grammar"]}
        if seg.get("pinyin"):
            display["pinyin"] = seg["pinyin"]
        out_segs.append({
            "id": "s%03d" % (i + 1),
            "key": key_str,
            "sourceSegmentIds": seg["sourceIds"],
            **({"sourcePart": seg["part"]} if seg.get("part") else {}),
            "begin": round(seg["begin"], 3),
            "end": round(seg["end"], 3),
            "beginText": src.format_time(seg["begin"]),
            "endText": src.format_time(seg["end"]),
            "clip": {"begin": cb, "end": ce},
            "alignment": seg["alignment"],
            **({"wordTimings": timings} if timings else {}),
            "original": seg["original"],
            "translation": seg["translation"],
            "textHash": th,
            "display": display,
            "listening": listening,
            "parts": speech.build_parts(listening.get("translationSpeech") or seg["translation"],
                                        listening, source_lang, target_lang),
        })

    sources = {"json": {"file": os.path.basename(json_path), "sha256": sha256_file(json_path)}}
    if original_json_path:
        sources["originalJson"] = {"file": os.path.basename(original_json_path),
                                   "sha256": sha256_file(original_json_path),
                                   "note": "json はこの元ファイルを訂正したもの（元ファイルは変更していない）"}
    if ttml_path:
        sources["ttml"] = {"file": os.path.basename(ttml_path), "sha256": sha256_file(ttml_path)}
    if audio_info:
        sources["audio"] = audio_info
    if duration:
        sources["durationSeconds"] = duration

    doc = {
        "format": FORMAT_NAME,
        "formatVersion": FORMAT_VERSION,
        "materialId": material_id(meta, json_path),
        "title": meta.get("title") or base_name(json_path),
        "channel": meta.get("channel", ""),
        "episode": meta.get("episode", ""),
        "publishDate": meta.get("publishDate", ""),
        "sourceLanguage": source_lang,
        "targetLanguage": target_lang,
        "sources": sources,
        "generator": {"name": "tools/build_listening.py", "version": GENERATOR_VERSION},
        "cues": speech.build_cues(target_lang),
        "segments": out_segs,
        "warnings": warnings,
    }
    # 事前生成音声の参照は、内容が変わっていなければ引き継ぐ（sprite.py が更新する）
    if previous and previous.get("audio"):
        doc["audio"] = previous["audio"]
    log("区間 %d（作者の解説 %d、引き継ぎ %d、自動 %d）" % (
        len(out_segs), counts["authored"], counts["kept"], counts["auto"]))
    if out_path:
        write_json(out_path, doc)
    return doc


def write_json(path, doc):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def main(argv=None):
    p = argparse.ArgumentParser(description="既存JSON → エピソード名 (日付).listening.json")
    p.add_argument("json", help="既存の教材JSON")
    p.add_argument("--ttml", help="TTML（省略時は同名の .ttml を探す）")
    p.add_argument("--audio", help="元音声（省略時は同名の .mp3/.m4a を探す）")
    p.add_argument("--out", help="出力先（省略時は 同じフォルダの エピソード名 (日付).listening.json）")
    p.add_argument("--authored", help="聞くための解説（作者による）JSON")
    p.add_argument("--no-merge", action="store_true", help="文の途中で切れた区間をまとめない")
    p.add_argument("--fresh", action="store_true", help="既存の .listening.json を引き継がない")
    p.add_argument("--original-json", help="json が訂正版のとき、その元になった既存JSON（記録用）")
    a = p.parse_args(argv)
    ttml = a.ttml or find_sibling(a.json, (".ttml",))
    audio = a.audio or find_sibling(a.json, AUDIO_EXTS)
    out = a.out or listening_path_for(a.json)
    previous = None
    if os.path.exists(out) and not a.fresh:
        with open(out, encoding="utf-8") as f:
            previous = json.load(f)
    doc = convert(a.json, ttml, audio, out, a.authored, merge=not a.no_merge, previous=previous,
                  original_json_path=a.original_json)
    for w in doc["warnings"]:
        print("警告:", w, file=sys.stderr)
    print("書き出し:", out)


if __name__ == "__main__":
    main()

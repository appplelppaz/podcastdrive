"""
Add word-level times ("wordTimings") to an episode JSON that was made earlier.

    python3 add_word_timings.py "<episode>.json" ["<episode>.ttml"] [--dry-run]

- Takes the timed words from the TTML (same name as the JSON when omitted)
  and gives each segment the words whose middle falls inside its begin–end.
- Changes nothing else in the JSON: ids, times, original, translation, words,
  grammar and pinyin stay exactly as they are (other apps keep working).
- A TTML that only has a time per line (older Whisper TTML) has no word times:
  make a new TTML with podcast-transcribe first.
- Reports segments whose text does not match the words at its time
  (a sign that the JSON's times or original text are off).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_ttml import ttml_timing, ttml_words, to_seconds, write_episode_json  # noqa: E402

IS_CHAR = lambda c: c.isalnum()  # noqa: E731


def chars(text):
    return [c for c in text.lower() if IS_CHAR(c)]


def match_ratio(a, b):
    """Share of the characters of a that appear in b in the same order (longest common subsequence)."""
    a, b = chars(a), chars(b)
    if not a:
        return 1.0
    if not b or len(a) * len(b) > 4_000_000:
        return 0.0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1] / len(a)


def add_word_timings(json_path, ttml_path=None, dry_run=False):
    ttml_path = ttml_path or os.path.splitext(json_path)[0] + ".ttml"
    if not os.path.exists(ttml_path):
        raise SystemExit(f"TTML not found: {ttml_path}")
    if ttml_timing(ttml_path) != "word":
        raise SystemExit("This TTML has no word-level times (one time per line). "
                         "Make a new TTML from the audio with the podcast-transcribe skill first.")
    with open(json_path, encoding="utf-8") as f:
        doc = json.load(f)
    words = ttml_words(ttml_path)
    segs = doc["segments"]
    bounds = [(to_seconds(s["begin"]), to_seconds(s["end"])) for s in segs]
    buckets = [[] for _ in segs]
    k = 0
    for b, e, text in words:
        mid = (b + e) / 2
        while k < len(segs) and bounds[k][1] <= mid:
            k += 1
        if k < len(segs) and bounds[k][0] <= mid:
            buckets[k].append([round(b, 3), round(e, 3), text])
    lang = (doc.get("metadata") or {}).get("sourceLanguage", "")
    joiner = "" if lang.split("-")[0] in ("zh", "ja", "ko") else " "
    report = {"segments": len(segs), "with_timings": 0, "mismatch": []}
    for s, ws in zip(segs, buckets):
        if ws:
            s["wordTimings"] = ws
            report["with_timings"] += 1
            r = match_ratio(s.get("original", ""), joiner.join(w[2] for w in ws))
            if r < 0.6:
                report["mismatch"].append((s.get("id"), round(r, 2), s.get("original", "")[:50]))
        else:
            s.pop("wordTimings", None)
    if not dry_run:
        write_episode_json(doc, json_path)
    return report


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        sys.exit(1)
    rep = add_word_timings(args[0], args[1] if len(args) > 1 else None, "--dry-run" in sys.argv)
    print(f"word timings on {rep['with_timings']}/{rep['segments']} segments")
    if rep["mismatch"]:
        print(f"{len(rep['mismatch'])} segments whose text does not match the words at its time:")
        for sid, r, text in rep["mismatch"][:20]:
            print(f"  id {sid}: {r:.0%} match  {text}")

# -*- coding: utf-8 -*-
r"""wx_voice_all.py - transcribe EVERY voice note in a decrypted backup.

Reads <backup>\media\voice\*.silk, converts each through the WeChat EXP
/api/voice endpoint (silk -> wav), transcribes with faster-whisper and stores
the text as <backup>\_voice_text\<stem>.txt plus an append-only JSONL.
The temporary WAV is deleted immediately (audio is not kept).

Usage:
  python wx_voice_all.py --backup <backup> --shard 0 --shards 3
  python wx_voice_all.py --backup <backup> --merge
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

MODEL_CACHE = {}


def fetch_wav(base: str, rel_path: str, tmp_dir: str) -> str:
    url = "%s/api/voice?path=%s" % (base, urllib.parse.quote(rel_path))
    h = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()
    dst = os.path.join(tmp_dir, h + ".wav")
    with urllib.request.urlopen(url, timeout=300) as r:
        data = r.read()
    if len(data) < 44 or data[:4] != b"RIFF":
        raise RuntimeError("not wav (%d bytes)" % len(data))
    with open(dst, "wb") as f:
        f.write(data)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5001")
    ap.add_argument("--backup", required=True)
    ap.add_argument("--model", default="small")
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--language", default="zh")
    ap.add_argument("--beam", type=int, default=1)
    ap.add_argument("--shard", type=int, default=0)
    ap.add_argument("--shards", type=int, default=1)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    voice_dir = os.path.join(args.backup, "media", "voice")
    text_dir = os.path.join(args.backup, "_voice_text")
    tmp_dir = os.path.join(args.backup, "_voice_tmp")
    os.makedirs(text_dir, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)

    if args.merge:
        out = os.path.join(args.backup, "voice_transcripts.jsonl")
        n = 0
        with open(out, "w", encoding="utf-8") as w:
            for name in sorted(os.listdir(text_dir)):
                if not name.endswith(".txt"):
                    continue
                stem = name[:-4]
                with open(os.path.join(text_dir, name), encoding="utf-8") as f:
                    txt = f.read()
                w.write(json.dumps({"file": stem + ".silk", "text": txt},
                                   ensure_ascii=False) + "\n")
                n += 1
        print("merged %d transcripts -> %s" % (n, out))
        return 0

    files = sorted(f for f in os.listdir(voice_dir) if f.endswith(".silk"))
    files = [f for i, f in enumerate(files) if i % args.shards == args.shard]
    if args.limit:
        files = files[:args.limit]
    print("[shard %d/%d] %d files" % (args.shard, args.shards, len(files)))

    from faster_whisper import WhisperModel
    model = WhisperModel(args.model, device=args.device,
                         compute_type="float16" if args.device == "cuda" else "int8")

    jsonl = os.path.join(args.backup, "voice_shard%d.jsonl" % args.shard)
    done = fail = 0
    t0 = time.time()
    with open(jsonl, "w", encoding="utf-8") as jf:
        for idx, fn in enumerate(files, 1):
            stem = fn[:-5]
            cache = os.path.join(text_dir, stem + ".txt")
            if os.path.exists(cache):
                with open(cache, encoding="utf-8") as f:
                    txt = f.read()
                jf.write(json.dumps({"file": fn, "text": txt}, ensure_ascii=False) + "\n")
                continue
            wav = None
            try:
                wav = fetch_wav(args.base, "media/voice/" + fn, tmp_dir)
                segs, info = model.transcribe(wav, language=args.language,
                                              beam_size=args.beam, vad_filter=True)
                txt = "".join(s.text for s in segs).strip()
                with open(cache, "w", encoding="utf-8") as f:
                    f.write(txt)
                jf.write(json.dumps({"file": fn, "text": txt}, ensure_ascii=False) + "\n")
                jf.flush()
                done += 1
                if done % 10 == 0 or idx == len(files):
                    el = time.time() - t0
                    rate = done / el if el else 0
                    left = (len(files) - idx) / rate / 60 if rate else 0
                    print("[%d/%d] done=%d fail=%d %.1f/s eta=%.0fmin last=%r" % (
                        idx, len(files), done, fail, rate, left, txt[:40]), flush=True)
            except Exception as e:  # noqa: BLE001
                fail += 1
                print("[warn] %s: %s" % (fn, str(e)[:120]), flush=True)
            finally:
                if wav and os.path.exists(wav):
                    try:
                        os.remove(wav)
                    except OSError:
                        pass
    print("[shard %d] finished done=%d fail=%d" % (args.shard, done, fail))
    return 0


if __name__ == "__main__":
    sys.exit(main())

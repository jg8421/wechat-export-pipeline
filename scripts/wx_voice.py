# -*- coding: utf-8 -*-
r"""wx_voice.py - batch voice-note transcription for WeChat EXP exports.

For every chat.csv produced by wx_export.py, fetch each voice note through the
WeChat EXP /api/voice endpoint (silk -> wav) and transcribe it with
faster-whisper, then write the text back into the 语音转写 column.

Usage:
  python wx_voice.py --root <EXPORT_DIR>
  python wx_voice.py --root ... --chat "<CONTACT>" --model small
  python wx_voice.py --root ... --model large-v3 --device cuda

Results are cached in <root>\_voice_cache\<sha1>.txt so reruns are cheap.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

CSV_HEADER = ["时间", "发送者", "类型", "内容", "图片文件", "语音转写", "媒体文件"]


def fetch_wav(base: str, rel_path: str, out_dir: str) -> str:
    url = "%s/api/voice?path=%s" % (base, urllib.parse.quote(rel_path))
    h = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()
    dst = os.path.join(out_dir, h + ".wav")
    if os.path.exists(dst) and os.path.getsize(dst) > 44:
        return dst
    with urllib.request.urlopen(url, timeout=180) as r:
        data = r.read()
    if len(data) < 44 or data[:4] != b"RIFF":
        raise RuntimeError("not a wav: %s (%d bytes)" % (rel_path, len(data)))
    with open(dst, "wb") as f:
        f.write(data)
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5000")
    ap.add_argument("--root", required=True, help="export root (contains <chat>/chat.csv)")
    ap.add_argument("--chat", default="", help="only this chat folder")
    ap.add_argument("--model", default="small")
    ap.add_argument("--device", default="auto", choices=["cpu", "cuda", "auto"])
    ap.add_argument("--language", default="zh")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cache_dir = os.path.join(args.root, "_voice_cache")
    os.makedirs(cache_dir, exist_ok=True)

    targets = []
    for d in sorted(os.listdir(args.root)):
        p = os.path.join(args.root, d, "chat.csv")
        if os.path.isfile(p):
            if args.chat and args.chat not in d:
                continue
            targets.append((d, p))
    if not targets:
        print("no chat.csv found under %s" % args.root)
        return 1

    model = None
    done = 0
    for name, csv_path in targets:
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        todo = [i for i, r in enumerate(rows)
                if (r.get("类型") or "") == "语音" and (r.get("媒体文件") or "").strip()
                and not (r.get("语音转写") or "").strip()]
        if not todo:
            print("[skip] %s (no untranscribed voice)" % name)
            continue
        if args.limit:
            todo = todo[:args.limit]
        if model is None:
            from faster_whisper import WhisperModel
            print("[init] faster-whisper model=%s device=%s" % (args.model, args.device))
            dev = args.device
            if dev == "auto":
                try:
                    WhisperModel(args.model, device="cuda", compute_type="float16")
                    dev = "cuda"
                except Exception:  # noqa: BLE001
                    dev = "cpu"
            ct = "float16" if dev == "cuda" else "int8"
            model = WhisperModel(args.model, device=dev, compute_type=ct)
        print("[chat] %s: %d voice notes" % (name, len(todo)))
        for i in todo:
            r = rows[i]
            rel = r["媒体文件"].strip()
            ck = os.path.join(cache_dir, hashlib.sha1(
                (rel + "|" + args.model).encode("utf-8")).hexdigest() + ".txt")
            if os.path.exists(ck):
                with open(ck, "r", encoding="utf-8") as f:
                    text = f.read()
            else:
                try:
                    wav = fetch_wav(args.base, rel, cache_dir)
                    segs, _info = model.transcribe(wav, language=args.language,
                                                   beam_size=5, vad_filter=True)
                    text = "".join(s.text for s in segs).strip()
                except Exception as e:  # noqa: BLE001
                    text = ""
                    sys.stderr.write("[warn] %s: %s\n" % (rel, e))
                with open(ck, "w", encoding="utf-8") as f:
                    f.write(text)
            r["语音转写"] = text
            done += 1
            print("   %s %s -> %s" % (r["时间"], rel.split("/")[-1], (text[:60] or "(empty)")))
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=CSV_HEADER)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in CSV_HEADER})
    print("done: %d voice notes transcribed" % done)
    return 0


if __name__ == "__main__":
    sys.exit(main())

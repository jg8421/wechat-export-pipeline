# -*- coding: utf-8 -*-
r"""wx_fix_voice_paths.py - backfill the 媒体文件 column for voice rows.

Exports produced before voice paths were synthesised leave 媒体文件 empty for
voice notes. This fills them from the matching raw.jsonl entry using the
deterministic <create_time>_<local_id>.silk naming.

Usage:
  python wx_fix_voice_paths.py --root <EXPORT_DIR> \
      --backup <BACKUP_DIR>
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

CSV_HEADER = ["时间", "发送者", "类型", "内容", "图片文件", "语音转写", "媒体文件"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--backup", required=True)
    args = ap.parse_args()

    voice_dir = os.path.join(args.backup, "media", "voice")
    fixed = missing = 0
    for d in sorted(os.listdir(args.root)):
        folder = os.path.join(args.root, d)
        csv_path = os.path.join(folder, "chat.csv")
        raw_path = os.path.join(folder, "raw.jsonl")
        if not (os.path.isfile(csv_path) and os.path.isfile(raw_path)):
            continue
        raw = []
        with open(raw_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        raw.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        by_key = {}
        for m in raw:
            try:
                by_key[(int(m.get("create_time") or 0), int(m.get("id") or 0))] = m
            except (TypeError, ValueError):
                continue
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        changed = False
        for r in rows:
            if (r.get("类型") or "") != "语音":
                continue
            if (r.get("媒体文件") or "").strip():
                continue
            try:
                import time as _t
                ct = int(_t.mktime(_t.strptime(r["时间"], "%Y-%m-%d %H:%M:%S")))
            except Exception:  # noqa: BLE001
                continue
            cand = [k for k in by_key if k[0] == ct]
            for k in cand:
                m = by_key[k]
                if m.get("msg_type") != 34:
                    continue
                rel = "media/voice/%s_%s.silk" % (k[0], k[1])
                if os.path.exists(os.path.join(args.backup, rel.replace("/", os.sep))):
                    r["媒体文件"] = rel
                    fixed += 1
                    changed = True
                else:
                    missing += 1
                break
        if changed:
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=CSV_HEADER)
                w.writeheader()
                for r in rows:
                    w.writerow({k: r.get(k, "") for k in CSV_HEADER})
            print("[fix] %s" % d)
    print("voice paths fixed=%d unresolved=%d" % (fixed, missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())

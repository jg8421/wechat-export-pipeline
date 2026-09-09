# -*- coding: utf-8 -*-
r"""wx_deliver_index.py - regenerate readable per-person txt (with voice text),
extract per-person voice transcripts, and build the deliverable index.

Usage:
  python wx_deliver_index.py --deliver "<微信所有工作skill dir>"
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time


def write_text(path, text, tries=6):
    """OneDrive occasionally holds a freshly synced file open -> retry."""
    for i in range(tries):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return True
        except PermissionError:
            time.sleep(1.5 * (i + 1))
    sys.stderr.write("[skip] locked: %s\n" % path)
    return False

try:
    csv.field_size_limit(1024 * 1024 * 64)
except OverflowError:
    csv.field_size_limit(2 ** 31 - 1)

NOISE = {"表情", "系统消息"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deliver", required=True)
    args = ap.parse_args()

    people_root = os.path.join(args.deliver, "01_原始消息", "按人员")
    voice_root = os.path.join(args.deliver, "01_原始消息", "语音转写")
    os.makedirs(voice_root, exist_ok=True)

    idx = []
    n_voice = 0
    for person in sorted(os.listdir(people_root)):
        pdir = os.path.join(people_root, person)
        if not os.path.isdir(pdir):
            continue
        rows = []
        for tag in ("1v1", "groups"):
            p = os.path.join(pdir, "%s_%s.csv" % (person, tag))
            if not os.path.isfile(p):
                continue
            with open(p, "r", encoding="utf-8-sig", newline="") as f:
                for r in csv.DictReader(f):
                    r["_tag"] = tag
                    rows.append(r)
        if not rows:
            continue
        # readable transcript including voice transcripts
        tp = os.path.join(pdir, "%s_all.txt" % person)
        buf = ["# %s 微信记录（1:1 + 群聊发言及上下文）\n" % person]
        cur = None
        for r in rows:
            typ = (r.get("类型") or "").strip()
            body = (r.get("内容") or "").strip()
            vt = (r.get("语音转写") or "").strip()
            if typ == "语音" and vt:
                body = "【语音】" + vt
            if typ in NOISE and not body:
                continue
            chat = r.get("会话") or ""
            if chat != cur:
                cur = chat
                buf.append("\n## 会话：%s\n" % chat)
            buf.append("[%s] %s: %s" % (r.get("时间"), r.get("发送者"), body))
        write_text(tp, "\n".join(buf) + "\n")
        # per-person voice transcripts
        voices = [(r.get("时间"), r.get("会话"), (r.get("语音转写") or "").strip())
                  for r in rows if (r.get("类型") or "").strip() == "语音"
                  and (r.get("语音转写") or "").strip()]
        if voices:
            vp = os.path.join(voice_root, "%s_语音转写.txt" % person)
            vb = ["# %s 语音转文字（%d 条）\n" % (person, len(voices))]
            for ts, chat, txt in voices:
                vb.append("[%s] %s | %s" % (ts, chat, txt))
            write_text(vp, "\n".join(vb) + "\n")
            n_voice += len(voices)
        idx.append({
            "person": person,
            "msgs": len(rows),
            "1v1": len([r for r in rows if r["_tag"] == "1v1"]),
            "voice": len(voices),
            "chars": sum(len(r.get("内容") or "") + len(r.get("语音转写") or "") for r in rows),
        })

    idx.sort(key=lambda x: -x["chars"])
    lines = ["# 微信记录整理 · 原始消息索引", "",
             "- 人员数：**%d**" % len(idx),
             "- 语音转写条数：**%d**" % n_voice, "",
             "| # | 人员 | 消息数 | 其中1:1 | 语音条数 | 字数 |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(idx, 1):
        lines.append("| %d | %s | %s | %s | %s | %s |" % (
            i, r["person"], format(r["msgs"], ","), format(r["1v1"], ","),
            format(r["voice"], ","), format(r["chars"], ",")))
    write_text(os.path.join(args.deliver, "01_原始消息", "_索引.md"), "\n".join(lines) + "\n")
    write_text(os.path.join(args.deliver, "01_原始消息", "_索引.json"),
               json.dumps(idx, ensure_ascii=False, indent=1))
    print("people=%d voice=%d" % (len(idx), n_voice))
    return 0


if __name__ == "__main__":
    sys.exit(main())

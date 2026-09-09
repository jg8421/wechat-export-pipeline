# -*- coding: utf-8 -*-
r"""wx_report.py - QA / overview report over an export root.

Usage:
  python wx_report.py --root <EXPORT_DIR>
  python wx_report.py --root ... --md <EXPORT_DIR>\_report.md
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter


try:
    csv.field_size_limit(1024 * 1024 * 64)
except OverflowError:
    csv.field_size_limit(2 ** 31 - 1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--md", default="")
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()

    rows = []
    types = Counter()
    voices_with_text = voices_total = 0
    for d in sorted(os.listdir(args.root)):
        folder = os.path.join(args.root, d)
        csv_path = os.path.join(folder, "chat.csv")
        meta_path = os.path.join(folder, "meta.json")
        if not os.path.isfile(csv_path):
            continue
        meta = {}
        if os.path.isfile(meta_path):
            try:
                meta = json.load(open(meta_path, encoding="utf-8"))
            except Exception:  # noqa: BLE001
                meta = {}
        n = 0
        chars = 0
        first = last = ""
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                n += 1
                t = (r.get("类型") or "").strip()
                types[t] += 1
                body = r.get("内容") or ""
                chars += len(body)
                if t == "语音":
                    voices_total += 1
                    if (r.get("语音转写") or "").strip():
                        voices_with_text += 1
                ts = (r.get("时间") or "").strip()
                if ts:
                    if not first:
                        first = ts
                    last = ts
        rows.append({
            "folder": d, "rows": n, "chars": chars, "first": first, "last": last,
            "type": meta.get("type") or "", "chat_id": meta.get("chat_id") or "",
        })

    rows.sort(key=lambda r: -r["chars"])
    total_rows = sum(r["rows"] for r in rows)
    total_chars = sum(r["chars"] for r in rows)

    lines = []
    lines.append("# 微信导出总览\n")
    lines.append("- 会话数：**%d**" % len(rows))
    lines.append("- 消息条数：**%s**" % format(total_rows, ","))
    lines.append("- 正文字数：**%s**" % format(total_chars, ","))
    if voices_total:
        lines.append("- 语音：%d 条，已转写 %d 条（%.0f%%）" % (
            voices_total, voices_with_text, 100.0 * voices_with_text / voices_total))
    lines.append("")
    lines.append("## 消息类型分布\n")
    lines.append("| 类型 | 条数 |")
    lines.append("|---|---|")
    for t, n in types.most_common():
        lines.append("| %s | %s |" % (t or "(空)", format(n, ",")))
    lines.append("")
    lines.append("## 按正文字数排名（前 %d）\n" % args.top)
    lines.append("| # | 会话 | 条数 | 字数 | 时间范围 |")
    lines.append("|---|---|---|---|---|")
    for i, r in enumerate(rows[:args.top], 1):
        lines.append("| %d | %s | %s | %s | %s ~ %s |" % (
            i, r["folder"], format(r["rows"], ","), format(r["chars"], ","),
            (r["first"] or "-")[:10], (r["last"] or "-")[:10]))
    text = "\n".join(lines) + "\n"

    out = args.md or os.path.join(args.root, "_report.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(text[:3000])
    print("...")
    print("report -> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

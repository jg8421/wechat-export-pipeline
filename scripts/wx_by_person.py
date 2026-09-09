# -*- coding: utf-8 -*-
r"""wx_by_person.py - regroup every exported chat.csv by person.

For each counterparty:
  * all 1:1 messages (both sides)            -> <person>_1v1.csv
  * their messages in group chats, with a
    context window of surrounding messages   -> <person>_groups.csv
  * a readable transcript for analysis       -> <person>_all.txt
Plus _people_index.json with per-person counts.

Usage:
  python wx_by_person.py --root <EXPORT_DIR> \
      --out <BY_PERSON_DIR>
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict

sys.setrecursionlimit(10000)
try:
    csv.field_size_limit(1024 * 1024 * 64)
except OverflowError:
    csv.field_size_limit(2 ** 31 - 1)

CSV_HEADER = ["时间", "发送者", "类型", "内容", "图片文件", "语音转写", "媒体文件"]
NOISE_TYPES = {"表情", "系统消息"}


def safe(name: str, fallback: str = "unknown") -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (name or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s or fallback)[:80]


def load_chat(folder: str):
    csv_path = os.path.join(folder, "chat.csv")
    meta_path = os.path.join(folder, "meta.json")
    if not os.path.isfile(csv_path):
        return None, None
    meta = {}
    if os.path.isfile(meta_path):
        try:
            meta = json.load(open(meta_path, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append({k: (r.get(k) or "") for k in CSV_HEADER})
    return rows, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ctx", type=int, default=3, help="context window in group chats")
    ap.add_argument("--min-group-msgs", type=int, default=3,
                    help="ignore people with fewer group messages")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    # person -> {"1v1": [(chat, row)], "groups": [(chat, row)]}
    people = defaultdict(lambda: {"1v1": [], "groups": []})
    chat_meta = {}
    total_rows = 0

    for d in sorted(os.listdir(args.root)):
        folder = os.path.join(args.root, d)
        if not os.path.isdir(folder):
            continue
        rows, meta = load_chat(folder)
        if rows is None:
            continue
        chat_meta[d] = {"rows": len(rows), "type": meta.get("type", ""),
                        "chat_id": meta.get("chat_id", "")}
        total_rows += len(rows)
        chat_id = str(meta.get("chat_id") or "")
        is_group = (meta.get("type") == "group") or chat_id.endswith("@chatroom") \
            or len({r["发送者"] for r in rows
                    if r["发送者"] and r["发送者"] != "我"}) > 5
        if not is_group:
            other = ""
            for r in rows:
                if r["发送者"] and r["发送者"] != "我":
                    other = r["发送者"]
                    break
            other = other or d
            people[other]["1v1"].extend((d, r) for r in rows)
        else:
            n = len(rows)
            mine = set()
            for i, r in enumerate(rows):
                s = r["发送者"]
                if s and s != "我":
                    mine.add(s)
            for s in mine:
                idxs = [i for i, r in enumerate(rows) if r["发送者"] == s]
                if len(idxs) < args.min_group_msgs:
                    continue
                keep = set()
                for i in idxs:
                    for j in range(max(0, i - args.ctx), min(n, i + args.ctx + 1)):
                        keep.add(j)
                for j in sorted(keep):
                    people[s]["groups"].append((d, rows[j]))

    index = {}
    for person, buckets in people.items():
        one = buckets["1v1"]
        grp = buckets["groups"]
        if not one and len(grp) < args.min_group_msgs:
            continue
        pdir = os.path.join(args.out, safe(person))
        os.makedirs(pdir, exist_ok=True)
        for tag, data in (("1v1", one), ("groups", grp)):
            if not data:
                continue
            p = os.path.join(pdir, "%s_%s.csv" % (safe(person), tag))
            with open(p, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["会话"] + CSV_HEADER)
                for chat, r in data:
                    w.writerow([chat] + [r[k] for k in CSV_HEADER])
        # readable transcript
        tp = os.path.join(pdir, "%s_all.txt" % safe(person))
        with open(tp, "w", encoding="utf-8") as f:
            f.write("# %s 微信记录（1:1 + 群聊发言及上下文）\n\n" % person)
            f.write("## 一对一\n\n" if one else "")
            prev = ""
            for chat, r in one:
                if r["类型"] in NOISE_TYPES and not r["内容"].strip():
                    continue
                f.write("[%s] %s: %s\n" % (r["时间"], r["发送者"], r["内容"]))
            if grp:
                f.write("\n## 群聊发言（含上下文）\n\n")
                cur = ""
                for chat, r in grp:
                    if chat != cur:
                        cur = chat
                        f.write("\n### 群：%s\n\n" % chat)
                    f.write("[%s] %s: %s\n" % (r["时间"], r["发送者"], r["内容"]))
        index[person] = {
            "1v1_msgs": len(one),
            "group_msgs": len([1 for _c, r in grp if r["发送者"] == person]),
            "group_ctx_lines": len(grp),
            "chars": sum(len(r["内容"]) for _c, r in one) +
                     sum(len(r["内容"]) for _c, r in grp if r["发送者"] == person),
            "folder": pdir,
        }

    with open(os.path.join(args.out, "_people_index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.out, "_chats_index.json"), "w", encoding="utf-8") as f:
        json.dump(chat_meta, f, ensure_ascii=False, indent=2)

    top = sorted(index.items(), key=lambda kv: -kv[1]["chars"])
    print("chats=%d rows=%d people=%d" % (len(chat_meta), total_rows, len(index)))
    for p, v in top[:40]:
        print("  %-30s 1v1=%-6d grp=%-6d chars=%d" % (
            p[:30], v["1v1_msgs"], v["group_msgs"], v["chars"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

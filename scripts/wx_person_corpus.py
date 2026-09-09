# -*- coding: utf-8 -*-
r"""wx_person_corpus.py - per-person analysis corpus from wx_by_person output.

Merges <person>_1v1.csv + <person>_groups.csv into one chronological stream,
drops noise, keeps substantive messages with a context window, then writes a
readable transcript plus 40k-char chunks ready for parallel extraction.

Usage:
  python wx_person_corpus.py --people <by_person dir> --out <corpus dir>
  python wx_person_corpus.py --people ... --out ... --min-chars 2000
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict

try:
    csv.field_size_limit(1024 * 1024 * 64)
except OverflowError:
    csv.field_size_limit(2 ** 31 - 1)

NOISE_TYPES = {"表情", "系统消息"}
BOT_NAMES = re.compile(
    r"(文件传输|微信团队|QQ邮箱提醒|微信支付|系统消息|未知|外卖红包助手|"
    r"美团品质推荐官|北京移动业务助手|招商银行|美团|"
    r"腾讯客服|微信运动|订阅号|服务通知|群收款|企业微信|小助手)", re.I)
SPAM = re.compile(
    r"(扫码|扫码进群|加我微信|免费领取|开户|万1|万1\.5|佣金|返佣|"
    r"点击链接|领取优惠|拼团|砍价|优惠券|下载APP|注册即|荐股|股票群|"
    r"带你赚钱|日入|稳赚|加微信|进群|拉群|私聊我|扫码关注)")
CHUNK = 40000


def clean(s):
    return (s or "").replace("\r", " ").replace("\n", " ⏎ ").strip()


def is_substantive(text, sender, mine_min, theirs_min):
    if not text:
        return False
    if sender == "我":
        return len(text) >= mine_min
    return len(text) >= theirs_min


def load_rows(pdir, person):
    rows = []
    for tag in ("1v1", "groups"):
        p = os.path.join(pdir, "%s_%s.csv" % (person, tag))
        if not os.path.isfile(p):
            continue
        with open(p, "r", encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                chat = (r.get("会话") or "").strip()
                sender = (r.get("发送者") or "").strip()
                typ = (r.get("类型") or "").strip()
                body = clean(r.get("内容"))
                vt = clean(r.get("语音转写"))
                if typ in NOISE_TYPES:
                    continue
                if typ == "语音":
                    body = ("【语音】" + vt) if vt else ""
                if not body and typ not in ("图片", "视频", "文件"):
                    continue
                if not body:
                    body = "（%s）" % typ
                if BOT_NAMES.search(sender):
                    continue
                if sender != "我" and SPAM.search(body) and len(body) < 200:
                    continue
                rows.append({
                    "ts": (r.get("时间") or "").strip(),
                    "sender": sender,
                    "type": typ,
                    "text": body,
                    "chat": chat,
                    "is_1v1": tag == "1v1",
                })
    rows.sort(key=lambda x: (x["ts"], 0 if x["is_1v1"] else 1))
    return rows


def build(rows, person, mine_min, theirs_min, ctx):
    n = len(rows)
    keep = [False] * n
    for i, r in enumerate(rows):
        if is_substantive(r["text"], r["sender"], mine_min, theirs_min):
            keep[i] = True
    mask = [False] * n
    for i, k in enumerate(keep):
        if k:
            for j in range(max(0, i - ctx), min(n, i + ctx + 1)):
                mask[j] = True
    out = []
    prev_ts = ""
    gap = False
    for i, r in enumerate(rows):
        if not mask[i]:
            continue
        if prev_ts and r["ts"][:10] != prev_ts[:10]:
            gap = True
        if gap:
            out.append({"ts": "", "sender": "", "type": "gap",
                        "text": "----- 日期跳转 -----", "chat": ""})
            gap = False
        out.append(r)
        prev_ts = r["ts"]
    return out


def render(msgs, person):
    lines = ["# %s 微信语料" % person, ""]
    cur_chat = None
    for r in msgs:
        if r["type"] == "gap":
            lines.append(r["text"])
            continue
        if r["chat"] != cur_chat:
            cur_chat = r["chat"]
            lines.append("")
            lines.append("### 会话：%s" % cur_chat)
            lines.append("")
        lines.append("[%s] %s: %s" % (r["ts"], r["sender"], r["text"]))
    return "\n".join(lines)


def chunk_text(text, size=CHUNK):
    lines = text.split("\n")
    chunks, cur, curlen = [], [], 0
    for ln in lines:
        if curlen + len(ln) + 1 > size and cur:
            chunks.append("\n".join(cur))
            cur, curlen = [], 0
        cur.append(ln)
        curlen += len(ln) + 1
    if cur:
        chunks.append("\n".join(cur))
    return chunks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--people", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mine", type=int, default=20)
    ap.add_argument("--theirs", type=int, default=12)
    ap.add_argument("--ctx", type=int, default=3)
    ap.add_argument("--min-chars", type=int, default=1500,
                    help="skip people whose kept corpus is smaller than this")
    ap.add_argument("--only", default="", help="comma-separated person name substrings")
    args = ap.parse_args()

    chunk_dir = os.path.join(args.out, "chunks")
    os.makedirs(chunk_dir, exist_ok=True)
    only = [s.strip() for s in args.only.split(",") if s.strip()]

    index = {}
    for person in sorted(os.listdir(args.people)):
        pdir = os.path.join(args.people, person)
        if not os.path.isdir(pdir):
            continue
        if only and not any(o.lower() in person.lower() for o in only):
            continue
        rows = load_rows(pdir, person)
        if not rows:
            continue
        msgs = build(rows, person, args.mine, args.theirs, args.ctx)
        text = render(msgs, person)
        if len(text) < args.min_chars:
            continue
        safe = re.sub(r'[\\/:*?"<>|]+', "_", person)[:60]
        tp = os.path.join(args.out, safe + "_corpus.txt")
        with open(tp, "w", encoding="utf-8") as f:
            f.write(text)
        chunks = chunk_text(text)
        for i, c in enumerate(chunks, 1):
            cp = os.path.join(chunk_dir, "%s_part%02d.md" % (safe, i))
            with open(cp, "w", encoding="utf-8") as f:
                f.write("# %s 微信语料片段 part%02d\n\n" % (person, i))
                f.write(c)
        index[person] = {
            "raw_rows": len(rows),
            "kept": len([m for m in msgs if m["type"] != "gap"]),
            "chars": len(text),
            "chunks": len(chunks),
            "corpus": tp,
        }
        print("[ok] %-30s kept=%-6d chars=%-8d chunks=%d" % (
            person[:30], index[person]["kept"], len(text), len(chunks)))

    with open(os.path.join(args.out, "_corpus_index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print("done: %d people -> %s" % (len(index), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

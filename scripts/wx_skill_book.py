# -*- coding: utf-8 -*-
r"""wx_skill_book.py - merge every per-person skill doc into ONE markdown book.

Reads <dir>/*_工作skill.md (one file per person, produced by wx_skill_merge.py)
and concatenates them into a single book with a cover, table of contents and
optional role-based sections.

Usage:
  python wx_skill_book.py --dir <skill_docs_dir> --out book.md
  python wx_skill_book.py --dir ... --out ... --plan sections.json
  python wx_skill_book.py --dir ... --out ... --preface preface.md --appendix appendix.md

sections.json (optional, controls order and grouping):
  [
    {"title": "Part 1 - Colleagues", "people": ["Alice", "Bob"]},
    {"title": "Part 2 - Advisors",  "people": ["Carol"]}
  ]
People not listed in the plan are appended under "Other".
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

TITLE_SUFFIX = "_工作skill.md"


def write_text(path, text, tries=8):
    for i in range(tries):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return True
        except (PermissionError, OSError):
            time.sleep(1.5 * (i + 1))
    return False


def strip_h1_and_quote(text: str) -> str:
    text = re.sub(r"^#\s+[^\n]*\n+", "", text.strip())
    text = re.sub(r"^(>[^\n]*\n)+", "", text)
    return text.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="folder with *_工作skill.md")
    ap.add_argument("--out", required=True, help="output markdown path")
    ap.add_argument("--plan", default="", help="optional sections.json")
    ap.add_argument("--preface", default="", help="optional markdown to include as 绪论")
    ap.add_argument("--appendix", default="", help="optional markdown appended as final part")
    ap.add_argument("--title", default="微信工作 skill 全书")
    args = ap.parse_args()

    D = args.dir
    have = {}
    for fn in os.listdir(D):
        if fn.endswith(TITLE_SUFFIX):
            have[fn[:-len(TITLE_SUFFIX)]] = os.path.join(D, fn)
    if not have:
        sys.stderr.write("no *_工作skill.md found in %s\n" % D)
        return 1

    plan = []
    if args.plan and os.path.isfile(args.plan):
        plan = json.load(open(args.plan, encoding="utf-8"))
    if not plan:
        plan = [{"title": "人物篇", "people": sorted(
            have, key=lambda k: -os.path.getsize(have[k]))}]

    lines = ["# %s" % args.title, ""]
    lines.append("> 每个人物一章，结构一致：① 人物定位与关系 ② 工作方法与判断标准 "
                 "③ 沟通与指令模式 ④ 数字口径与红线 ⑤ 高频场景与应对 "
                 "⑥ 可复用句式（原文引用） ⑦ 常见错误 ⑧ 协作要点。")
    lines.append("> 引文均为聊天原文（含语音转写），标注日期。")
    lines.append("")
    lines.append("## 目录")
    lines.append("")
    if args.preface and os.path.isfile(args.preface):
        lines.append("- **绪论**")
    for sec in plan:
        lines.append("- **%s**" % sec.get("title", ""))
        for p in sec.get("people", []):
            if p in have:
                lines.append("  - %s" % p)
    if args.appendix and os.path.isfile(args.appendix):
        lines.append("- **附录**")
    lines.append("")
    lines.append("---")
    lines.append("")

    if args.preface and os.path.isfile(args.preface):
        lines.append("# 绪论")
        lines.append("")
        lines.append(strip_h1_and_quote(open(args.preface, encoding="utf-8").read()))
        lines.append("")
        lines.append("---")
        lines.append("")

    used = set()
    for sec in plan:
        lines.append("# %s" % sec.get("title", ""))
        lines.append("")
        for p in sec.get("people", []):
            fp = have.get(p)
            if not fp:
                sys.stderr.write("[warn] missing: %s\n" % p)
                continue
            used.add(p)
            lines.append(open(fp, encoding="utf-8").read().strip())
            lines.append("")
            lines.append("---")
            lines.append("")

    leftovers = [k for k in have if k not in used]
    if leftovers:
        lines.append("# 其他")
        lines.append("")
        for p in sorted(leftovers):
            lines.append(open(have[p], encoding="utf-8").read().strip())
            lines.append("")
            lines.append("---")
            lines.append("")

    if args.appendix and os.path.isfile(args.appendix):
        lines.append("# 附录")
        lines.append("")
        txt = open(args.appendix, encoding="utf-8").read()
        idx = txt.find("\n# 1. ")
        lines.append((txt[idx + 1:] if idx > 0 else txt).strip())
        lines.append("")

    text = "\n".join(lines)
    ok = write_text(args.out, text)
    print("book -> %s (%s, %d chars, %d people)" % (
        args.out, "ok" if ok else "FAILED", len(text), len(used)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

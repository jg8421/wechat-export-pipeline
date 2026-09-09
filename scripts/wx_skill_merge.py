# -*- coding: utf-8 -*-
r"""wx_skill_merge.py - merge per-part extractions into one per-person skill doc.

Usage:
  python wx_skill_merge.py --parts <_parts dir> --out <out dir> [--plan plan.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

SECTIONS = [
    "1. 人物定位与关系",
    "2. 工作方法与判断标准",
    "3. 沟通与指令模式",
    "4. 数字口径与红线",
    "5. 高频场景与应对",
    "6. 可复用句式（原文引用）",
    "7. 他会挑的毛病 / 常见错误",
    "8. 与本人的协作要点",
]


def write_text(path, text, tries=6):
    for i in range(tries):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return True
        except PermissionError:
            time.sleep(1.5 * (i + 1))
    sys.stderr.write("[skip] locked: %s\n" % path)
    return False


def split_sections(text):
    """-> {section_title: body}"""
    out = {}
    parts = re.split(r"^##\s+", text, flags=re.M)
    for p in parts[1:]:
        head, _, body = p.partition("\n")
        head = head.strip()
        for s in SECTIONS:
            key = s.split(".", 1)[0]
            if head.startswith(key + ".") or head == s or head.replace(" ", "").startswith(key + "."):
                out[s] = body.strip()
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--plan", default=r"<PLAN_JSON>")
    ap.add_argument("--meta", default=r"<CORPUS_INDEX_JSON>")
    args = ap.parse_args()

    plan = json.load(open(args.plan, encoding="utf-8"))
    meta = {}
    if os.path.isfile(args.meta):
        meta = json.load(open(args.meta, encoding="utf-8"))
    os.makedirs(args.out, exist_ok=True)

    built = []
    pending = []
    for p in plan:
        person, safe = p["person"], p["safe"]
        files = []
        for i in range(1, len(p["parts"]) + 1):
            fp = os.path.join(args.parts, "%s_part%02d.md" % (safe, i))
            if os.path.isfile(fp) and os.path.getsize(fp) > 200:
                files.append(fp)
        if not files:
            pending.append(person)
            continue
        merged = {s: [] for s in SECTIONS}
        for fp in files:
            txt = open(fp, encoding="utf-8").read()
            secs = split_sections(txt)
            for s in SECTIONS:
                body = (secs.get(s) or "").strip()
                if body and body not in ("（本片段无）", "（本片段无）"):
                    merged[s].append(body)
        m = meta.get(person, {})
        lines = ["# %s · 微信工作 skill" % person, ""]
        lines.append("> 语料：与本人的微信对话，共 %s 条保留消息 / 约 %s 字（2026-09-10 导出）。"
                     % (format(m.get("kept", 0), ","), format(m.get("chars", 0), ",")))
        lines.append("> 本文件由 %d 个语料片段的独立提炼合并而成，引文均为微信原文（含语音转写），标注日期。" % len(files))
        lines.append("")
        for s in SECTIONS:
            lines.append("## %s" % s)
            lines.append("")
            bodies = merged[s]
            if not bodies:
                lines.append("（语料未涉及）")
            elif len(bodies) == 1:
                lines.append(bodies[0])
            else:
                for i, b in enumerate(bodies, 1):
                    lines.append("### 片段 %d" % i)
                    lines.append("")
                    lines.append(b)
            lines.append("")
        out_path = os.path.join(args.out, "%s_工作skill.md" % safe)
        write_text(out_path, "\n".join(lines))
        built.append((person, len(files), sum(len(b) for v in merged.values() for b in v)))
    print("built %d docs, pending %d" % (len(built), len(pending)))
    for person, nf, chars in built:
        print("  %-30s parts=%d chars=%d" % (person[:30], nf, chars))
    if pending:
        print("PENDING:", " | ".join(pending))
    return 0


if __name__ == "__main__":
    sys.exit(main())

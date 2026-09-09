# -*- coding: utf-8 -*-
r"""wx_html.py - render chat.csv (per chat or per person) into a browsable HTML.

Usage:
  python wx_html.py --root <export root>                  # every <chat>/chat.csv -> chat.html
  python wx_html.py --dir <person folder>                 # every *.csv in folder -> *.html
"""
from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys

try:
    csv.field_size_limit(1024 * 1024 * 64)
except OverflowError:
    csv.field_size_limit(2 ** 31 - 1)

CSS = """
:root{--bg:#0d1117;--panel:#161b22;--line:#30363d;--txt:#e6edf3;--dim:#8b949e;--me:#1f6feb;--them:#238636}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.6 "Microsoft YaHei","PingFang SC",system-ui,sans-serif}
header{position:sticky;top:0;background:var(--panel);border-bottom:1px solid var(--line);padding:10px 16px;z-index:5}
h1{font-size:15px;margin:0 0 4px 0}
.meta{color:var(--dim);font-size:12px}
#q{width:100%;margin-top:8px;padding:7px 10px;background:#0d1117;border:1px solid var(--line);border-radius:6px;color:var(--txt)}
main{max-width:1000px;margin:0 auto;padding:16px}
.day{color:var(--dim);font-size:12px;text-align:center;margin:18px 0 8px}
.msg{display:flex;gap:8px;margin:6px 0;align-items:flex-start}
.who{flex:0 0 110px;text-align:right;color:var(--dim);font-size:12px;padding-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bubble{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:6px 10px;max-width:76%;white-space:pre-wrap;word-break:break-word}
.me .bubble{border-color:#1f6feb55;background:#0d2a4d}
.me .who{color:#79c0ff}
.type{color:var(--dim);font-size:11px;margin-right:6px}
.ts{color:#6e7681;font-size:11px;margin-left:6px}
a{color:#58a6ff}
.hidden{display:none}
mark{background:#e3b341;color:#000}
"""

JS = """
const q=document.getElementById('q');
q.addEventListener('input',()=>{
  const v=q.value.trim().toLowerCase();
  document.querySelectorAll('.msg').forEach(m=>{
    m.classList.toggle('hidden', v && !m.dataset.t.includes(v));
  });
  document.querySelectorAll('.day').forEach(d=>d.classList.add('hidden'));
});
"""


def render(csv_path: str, out_path: str, title: str):
    rows = []
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    n = len(rows)
    chars = sum(len(r.get("内容") or "") for r in rows)
    first = rows[0].get("时间", "") if rows else ""
    last = rows[-1].get("时间", "") if rows else ""
    parts = ["<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
             "<meta name='viewport' content='width=device-width,initial-scale=1'>",
             "<title>%s</title><style>%s</style></head><body>" % (html.escape(title), CSS)]
    parts.append("<header><h1>%s</h1><div class='meta'>%s 条 · %s 字 · %s ~ %s</div>"
                 "<input id='q' placeholder='搜索…'></header><main>" % (
                     html.escape(title), format(n, ","), format(chars, ","),
                     first[:16], last[:16]))
    cur_day = ""
    for r in rows:
        ts = r.get("时间") or ""
        day = ts[:10]
        if day and day != cur_day:
            cur_day = day
            parts.append("<div class='day'>%s</div>" % html.escape(day))
        who = r.get("发送者") or ""
        typ = r.get("类型") or ""
        body = r.get("内容") or ""
        voice = r.get("语音转写") or ""
        if voice:
            body = (body + "  " if body else "") + "【语音转写】" + voice
        media = r.get("媒体文件") or ""
        img = r.get("图片文件") or ""
        extra = ""
        if media:
            extra = "<div class='meta'>附件：%s</div>" % html.escape(media)
        elif img:
            extra = "<div class='meta'>图片：%s</div>" % html.escape(img)
        cls = "msg me" if who == "我" else "msg"
        text = html.escape(body)
        text = re.sub(r"(https?://[^\s<]+)", r"<a href='\1' target='_blank'>\1</a>", text)
        search = html.escape((who + " " + typ + " " + body + " " + voice).lower(), quote=True)
        parts.append("<div class='%s' data-t=\"%s\"><div class='who'>%s</div>"
                     "<div class='bubble'><span class='type'>%s</span>%s<span class='ts'>%s</span>%s</div></div>"
                     % (cls, search, html.escape(who), html.escape(typ), text,
                        html.escape(ts[11:16] if len(ts) > 15 else ts), extra))
    parts.append("</main><script>%s</script></body></html>" % JS)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    return n, chars


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="", help="export root with <chat>/chat.csv")
    ap.add_argument("--dir", default="", help="folder with *.csv files")
    ap.add_argument("--title", default="", help="title override (single file mode)")
    args = ap.parse_args()

    jobs = []
    if args.root:
        for d in sorted(os.listdir(args.root)):
            p = os.path.join(args.root, d, "chat.csv")
            if os.path.isfile(p):
                jobs.append((p, os.path.join(args.root, d, "chat.html"), d))
    if args.dir:
        for fn in sorted(os.listdir(args.dir)):
            if fn.lower().endswith(".csv"):
                p = os.path.join(args.dir, fn)
                jobs.append((p, p[:-4] + ".html",
                             args.title or fn[:-4]))
    if not jobs:
        print("nothing to do")
        return 1
    for p, o, t in jobs:
        n, c = render(p, o, t)
        print("[html] %-46s %6d msgs %9d chars -> %s" % (t[:46], n, c, os.path.basename(o)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

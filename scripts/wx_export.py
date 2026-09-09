# -*- coding: utf-8 -*-
"""wx_export.py — WeChat EXP -> chat.csv exporter (DSH side).

Pulls decrypted messages from a running WeChat EXP web service (default
http://127.0.0.1:5000) and writes the exact CSV schema the existing analysis
pipeline expects:

    时间,发送者,类型,内容,图片文件,语音转写,媒体文件

Usage:
  python wx_export.py --list                       # list chats (id/name/type/count)
  python wx_export.py --chat "<CONTACT>" --out D:\out
  python wx_export.py --all --out D:\out --since 2025-01-01
  python wx_export.py --chat a27246266 --chat "汪劼" --out D:\out

Notes:
  * 时间 is local time "YYYY-MM-DD HH:MM:SS".
  * 发送者 is "我" for outgoing messages, otherwise the display name.
  * 类型 mirrors the legacy export vocabulary: 文本 / 图片 / 语音 / 视频 /
    表情 / 链接/应用 / 名片 / 系统消息 / 位置 / 网络电话 / 其他.
  * raw.jsonl keeps every field returned by the API for later re-processing.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:5000"
CSV_HEADER = ["时间", "发送者", "类型", "内容", "图片文件", "语音转写", "媒体文件"]

# msg_type -> legacy 类型 label
TYPE_MAP = {
    1: "文本",
    3: "图片",
    34: "语音",
    37: "好友请求",
    42: "名片",
    43: "视频",
    47: "表情",
    48: "位置",
    49: "链接/应用",
    50: "网络电话",
    51: "状态",
    62: "视频",
    10000: "系统消息",
}


def http_json(url: str, timeout: int = 120, retries: int = 3):
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError("request failed: %s (%s)" % (url, last))


def http_post_sse(url: str, body: dict, timeout: int = 3600):
    """POST a JSON body and collect SSE frames -> list of (event, data)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    events = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        buf = ""
        cur_event = None
        for raw in r:
            buf += raw.decode("utf-8", "replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.rstrip("\r")
                if line.startswith("event: "):
                    cur_event = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        payload = json.loads(line[6:])
                    except Exception:  # noqa: BLE001
                        payload = {"raw": line[6:]}
                    events.append((cur_event, payload))
                    cur_event = None
    return events


def safe_name(name: str, fallback: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (name or "").strip())
    s = re.sub(r"\s+", " ", s).strip(" .")
    if not s:
        s = fallback
    return s[:80]


def fmt_time(ts) -> str:
    try:
        ts = int(ts)
    except (TypeError, ValueError):
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def fmt_duration(mi: dict, xp: dict) -> str:
    """Voice duration in seconds. xml_parsed.duration is ms; media_info.duration
    is unreliable (often a raw voice-format length), so it is only a fallback."""
    ms = (xp or {}).get("duration")
    if not isinstance(ms, (int, float)) or ms <= 0:
        ms = None
    if ms is None:
        v = (mi or {}).get("duration")
        if isinstance(v, (int, float)) and v > 0:
            ms = v if v > 60000 else v * 1000
    if not ms:
        return ""
    return "%d\"" % int(round(ms / 1000.0))


def classify(msg: dict):
    """Return (类型, 内容, 图片文件, 媒体文件)."""
    mt = msg.get("msg_type")
    mi = msg.get("media_info") or {}
    xp = msg.get("xml_parsed") or {}
    content = msg.get("content") or ""
    label = TYPE_MAP.get(mt, "其他")

    img = ""
    media = ""
    body = ""

    if mt == 1:
        body = content
    elif mt == 3:
        img = mi.get("local_path") or (mi.get("md5") or "") + ".dat"
        media = mi.get("md5") or ""
        body = "[图片]"
    elif mt == 34:
        dur = fmt_duration(mi, xp)
        body = "[语音]" + ((" " + dur) if dur else "")
        # deterministic silk name used by wx_voice_dump.py / /api/voice
        ct = msg.get("create_time")
        lid = msg.get("id")
        guess = ""
        try:
            guess = "media/voice/%s_%s.silk" % (int(ct), int(lid))
        except (TypeError, ValueError):
            guess = ""
        media = mi.get("voice_path") or guess
    elif mt in (43, 62):
        media = mi.get("local_path") or mi.get("file_name") or ""
        body = "[视频]"
    elif mt == 47:
        body = "[表情]"
    elif mt == 42:
        body = "[名片] " + (xp.get("nickname") or xp.get("text") or "")
    elif mt == 48:
        body = "[位置] " + (xp.get("label") or xp.get("poiname") or "")
    elif mt == 50:
        body = xp.get("text") or "[网络电话]"
    elif mt == 10000:
        body = xp.get("text") or content[:200]
    elif mt == 49:
        # appmsg: link / file / quote / mini program / transfer ...
        t = xp.get("text") or ""
        if not t:
            m = re.search(r"<title>(.*?)</title>", content, re.S)
            t = m.group(1).strip() if m else ""
        app_type = xp.get("app_type")
        prefix = ""
        if app_type == 6:
            prefix = "[文件] "
        elif app_type == 5:
            prefix = "[链接] "
        elif app_type == 57:
            prefix = "[引用] "
        elif app_type == 33 or app_type == 36:
            prefix = "[小程序] "
        elif app_type == 2000:
            prefix = "[转账] "
        elif app_type == 19:
            prefix = "[合并转发] "
        if prefix and t and not t.lstrip().startswith("["):
            body = prefix + t
        else:
            body = t or content[:300]
        media = mi.get("local_path") or mi.get("file_name") or ""
        if media and app_type == 6:
            img = ""
        if xp.get("url"):
            body += "  " + str(xp.get("url"))
    else:
        body = xp.get("text") or re.sub(r"<[^>]+>", " ", content)[:300].strip()

    return label, body, img, media


def clean(s) -> str:
    if s is None:
        return ""
    return re.sub(r"[\r\n]+", " ⏎ ", str(s)).strip()


def fetch_chat_messages(base: str, chat_id: str, per_page: int = 500, cap: int = 0):
    """Newest-first from the API; returns oldest-first list."""
    per_page = max(1, min(200, int(per_page)))
    out = []
    page = 1
    total = None
    while True:
        url = "%s/api/messages?chat_id=%s&page=%d&per_page=%d" % (
            base, urllib.parse.quote(chat_id), page, per_page)
        d = http_json(url)
        msgs = d.get("messages") or []
        total = (d.get("pagination") or {}).get("total", total)
        if not msgs:
            break
        out.extend(msgs)
        if len(msgs) < per_page:
            break
        page += 1
        if cap and len(out) >= cap:
            break
        if page > 4000:
            break
    # The API returns pages newest-block-first, each block ascending; normalise
    # to a strict ascending timeline.
    out.sort(key=lambda m: (int(m.get("create_time") or 0), int(m.get("id") or 0)))
    return out, total


def list_contacts(base: str, q: str = ""):
    url = base + "/api/contacts" + (("?q=" + urllib.parse.quote(q)) if q else "")
    d = http_json(url)
    if isinstance(d, dict):
        return d.get("contacts") or d.get("items") or []
    return d


def resolve_targets(base: str, wanted: list[str]):
    """Map user-provided names/ids to contact entries."""
    contacts = list_contacts(base)
    by_id = {c.get("id"): c for c in contacts if c.get("id")}
    picked = []
    for w in wanted:
        w = w.strip()
        if not w:
            continue
        if w in by_id:
            picked.append(by_id[w])
            continue
        hits = [c for c in contacts if w.lower() in (c.get("name") or "").lower()]
        if not hits:
            sys.stderr.write("[warn] no contact matched: %r\n" % w)
            continue
        if len(hits) > 1:
            exact = [c for c in hits if (c.get("name") or "") == w]
            if len(exact) == 1:
                hits = exact
            else:
                sys.stderr.write("[warn] %r matched %d contacts, using first: %s\n" % (
                    w, len(hits), ", ".join((h.get("name") or h.get("id")) for h in hits[:6])))
                hits = [hits[0]]
        picked.append(hits[0])
    # dedupe by id preserving order
    seen = set()
    out = []
    for c in picked:
        cid = c.get("id")
        if cid in seen:
            continue
        seen.add(cid)
        out.append(c)
    return out, contacts


def export_one(base: str, contact: dict, out_root: str, since: str, until: str,
               per_page: int, cap: int, quiet: bool = False):
    chat_id = contact.get("id")
    name = contact.get("name") or chat_id
    msgs, total = fetch_chat_messages(base, chat_id, per_page=per_page, cap=cap)
    folder = os.path.join(out_root, safe_name(name, chat_id))
    os.makedirs(folder, exist_ok=True)

    since_ts = int(time.mktime(time.strptime(since, "%Y-%m-%d"))) if since else 0
    until_ts = int(time.mktime(time.strptime(until, "%Y-%m-%d"))) + 86399 if until else 0

    rows = []
    for m in msgs:
        ct = m.get("create_time")
        try:
            cti = int(ct)
        except (TypeError, ValueError):
            cti = 0
        if since_ts and cti and cti < since_ts:
            continue
        if until_ts and cti and cti > until_ts:
            continue
        label, body, img, media = classify(m)
        sender = m.get("sender_name") or ("我" if m.get("is_sender") else name)
        rows.append({
            "时间": fmt_time(ct),
            "发送者": sender,
            "类型": label,
            "内容": clean(body),
            "图片文件": clean(img),
            "语音转写": "",
            "媒体文件": clean(media),
        })

    csv_path = os.path.join(folder, "chat.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    with open(os.path.join(folder, "raw.jsonl"), "w", encoding="utf-8") as f:
        for m in msgs:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    meta = {
        "chat_id": chat_id,
        "name": name,
        "type": contact.get("type"),
        "api_total": total,
        "fetched": len(msgs),
        "rows_written": len(rows),
        "first_time": rows[0]["时间"] if rows else "",
        "last_time": rows[-1]["时间"] if rows else "",
        "chars": sum(len(r["内容"]) for r in rows),
        "since": since,
        "until": until,
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "folder": folder,
    }
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    if not quiet:
        print("[ok] %-28s rows=%-6d total=%-6d %s ~ %s -> %s" % (
            name[:28], len(rows), total or 0, meta["first_time"] or "-",
            meta["last_time"] or "-", csv_path))
    return meta


def main():
    ap = argparse.ArgumentParser(description="WeChat EXP -> chat.csv exporter")
    ap.add_argument("--base", default=DEFAULT_BASE, help="WeChat EXP base url")
    ap.add_argument("--chat", action="append", default=[], help="contact name or wxid (repeatable)")
    ap.add_argument("--all", action="store_true", help="export every chat that has messages")
    ap.add_argument("--out", default=r"<EXPORT_DIR>", help="output root")
    ap.add_argument("--since", default="", help="YYYY-MM-DD")
    ap.add_argument("--until", default="", help="YYYY-MM-DD")
    ap.add_argument("--per-page", type=int, default=200, help="1..200 (API cap)")
    ap.add_argument("--cap", type=int, default=0, help="max messages per chat (0=all)")
    ap.add_argument("--min-msgs", type=int, default=1, help="skip chats below this count (--all)")
    ap.add_argument("--resume", action="store_true",
                    help="skip chats whose <out>/<name>/meta.json already exists")
    ap.add_argument("--list", action="store_true", help="list contacts and exit")
    ap.add_argument("--probe", action="store_true", help="check service health and exit")
    args = ap.parse_args()

    if args.probe:
        try:
            contacts = list_contacts(args.base)
            print("service OK: %s contacts=%d" % (args.base, len(contacts)))
        except Exception as e:  # noqa: BLE001
            print("service DOWN: %s (%s)" % (args.base, e))
            return 2
        return 0

    if args.list:
        contacts = list_contacts(args.base)
        contacts.sort(key=lambda c: (c.get("name") or ""))
        print("%-34s %-8s %s" % ("chat_id", "type", "name"))
        for c in contacts:
            print("%-34s %-8s %s" % (c.get("id"), c.get("type"), c.get("name")))
        print("total contacts: %d" % len(contacts))
        return 0

    os.makedirs(args.out, exist_ok=True)

    if args.all:
        contacts = list_contacts(args.base)
        targets = contacts
    elif args.chat:
        targets, _ = resolve_targets(args.base, args.chat)
    else:
        ap.error("need --chat NAME, --all, --list or --probe")

    metas = []
    skipped = 0
    for c in targets:
        if args.resume:
            folder = os.path.join(args.out, safe_name(c.get("name") or "", c.get("id") or ""))
            if os.path.isfile(os.path.join(folder, "meta.json")):
                skipped += 1
                continue
        try:
            meta = export_one(args.base, c, args.out, args.since, args.until,
                              args.per_page, args.cap)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write("[error] %s: %s\n" % (c.get("name"), e))
            continue
        if args.all and meta["rows_written"] < args.min_msgs:
            continue
        metas.append(meta)

    idx = os.path.join(args.out, "_index.json")
    with open(idx, "w", encoding="utf-8") as f:
        json.dump(metas, f, ensure_ascii=False, indent=2)
    tot = sum(m["rows_written"] for m in metas)
    if skipped:
        print("resumed: skipped %d already-exported chats" % skipped)
    print("done: %d chats, %d rows -> %s (index: %s)" % (len(metas), tot, args.out, idx))


if __name__ == "__main__":
    sys.exit(main())

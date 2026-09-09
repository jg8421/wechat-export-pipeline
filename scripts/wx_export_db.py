# -*- coding: utf-8 -*-
r"""wx_export_db.py - export messages straight from the decrypted SQLite DBs.

The WeChat EXP web API is correct but very slow on large group chats
(up to 90 s per 200 messages), so this reads the decrypted databases directly:
~405k messages in a few seconds.

Output schema is identical to wx_export.py:
    时间,发送者,类型,内容,图片文件,语音转写,媒体文件
plus raw.jsonl / meta.json so downstream tools are interchangeable.

Usage:
  python wx_export_db.py --backup <backup> --out <dir> --list
  python wx_export_db.py --backup <backup> --out <dir> --chat a27246266
  python wx_export_db.py --backup <backup> --out <dir> --all --min-msgs 2
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from xml.etree import ElementTree as ET

try:
    csv.field_size_limit(1024 * 1024 * 64)
except OverflowError:
    csv.field_size_limit(2 ** 31 - 1)

try:
    import zstandard as zstd
    DCTX = zstd.ZstdDecompressor()
except ImportError:
    DCTX = None

ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
CSV_HEADER = ["时间", "发送者", "类型", "内容", "图片文件", "语音转写", "媒体文件"]
# Your own wxid (the account that owns the data). Only needed to label YOUR OWN
# messages in GROUP chats as "我". Find it in the data folder name:
#   ...\xwechat_files\<your-wxid>_abcd\db_storage
# Override with --self-id or the WX_SELF_ID environment variable.
SELF = os.environ.get("WX_SELF_ID", "")
ME = "我"

BASE_TYPE = {
    1: "文本", 3: "图片", 34: "语音", 37: "好友请求", 42: "名片", 43: "视频",
    47: "表情", 48: "位置", 49: "链接/应用", 50: "网络电话", 51: "状态",
    62: "视频", 10000: "系统消息", 10002: "系统消息", 64: "语音",
}
APP_SUB = {
    5: "[链接] ", 4: "[视频号] ", 6: "[文件] ", 19: "[合并转发] ",
    33: "[小程序] ", 36: "[小程序] ", 44: "[微信红包] ", 57: "[引用] ",
    63: "[直播] ", 87: "[群公告] ", 2000: "[转账] ", 2001: "[红包] ",
    51: "[视频号直播] ", 53: "[接龙] ", 62: "[音乐] ", 74: "[文件] ",
}


def conn(path):
    return sqlite3.connect("file:" + path.replace("\\", "/") + "?mode=ro", uri=True)


def decode_blob(b):
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    if b[:4] == ZSTD_MAGIC:
        if DCTX is None:
            return ""
        try:
            return DCTX.decompress(b).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            try:
                return DCTX.decompressobj().decompress(b).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                return ""
    try:
        return bytes(b).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def xml_text(xml: str, *tags):
    for t in tags:
        m = re.search(r"<%s>(.*?)</%s>" % (t, t), xml, re.S)
        if m:
            v = m.group(1)
            v = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", v, flags=re.S)
            return html_unescape(v.strip())
    return ""


def html_unescape(s: str) -> str:
    import html as _h
    return _h.unescape(s or "")


def plain_xml(s: str, limit: int = 160) -> str:
    """Turn a possibly-escaped XML fragment into readable one-line text."""
    s = html_unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def fmt_time(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(ts)))
    except Exception:  # noqa: BLE001
        return ""


def classify(base, sub, content, chat_is_group):
    """-> (类型, 内容, 图片文件, 媒体文件)"""
    img = media = ""
    body = content or ""
    label = BASE_TYPE.get(base, "其他")

    if base == 1:
        return label, body, img, media
    if base == 3:
        md5 = xml_text(body, "img") or ""
        m = re.search(r"md5=\"([0-9a-f]{32})\"", body)
        img = (m.group(1) + ".dat") if m else ""
        return label, "[图片]", img, media
    if base == 34:
        vl = xml_text(body, "voicemsg")
        m = re.search(r"voicelength=\"(\d+)\"", body)
        dur = ""
        if m:
            try:
                dur = "%d\"" % int(round(int(m.group(1)) / 1000.0))
            except ValueError:
                dur = ""
        return label, "[语音]" + ((" " + dur) if dur else ""), img, media
    if base in (43, 62):
        return "视频", "[视频]", img, media
    if base == 47:
        return label, "[表情]", img, media
    if base == 42:
        return label, "[名片] " + xml_text(body, "nickname", "username"), img, media
    if base == 48:
        return label, "[位置] " + xml_text(body, "label", "poiname"), img, media
    if base == 50:
        return label, "[网络电话] " + xml_text(body, "msg"), img, media
    if base in (10000, 10002):
        txt = xml_text(body, "content", "text") or re.sub(r"<[^>]+>", " ", body)
        return label, ("[系统] " + txt.strip())[:300], img, media
    if base == 49:
        app_type = xml_text(body, "type")
        title = xml_text(body, "title")
        des = xml_text(body, "des")
        url = xml_text(body, "url")
        prefix = ""
        try:
            prefix = APP_SUB.get(int(app_type), "")
        except (TypeError, ValueError):
            prefix = ""
        if not prefix:
            prefix = "[链接] " if url else ""
        out = (prefix + plain_xml(title, 300)).strip() or plain_xml(des, 200)
        if url and url.startswith("http"):
            out += "  " + url
        if int(app_type or 0) == 57:
            q = plain_xml(xml_text(body, "content"))
            head = plain_xml(title) or q
            out = "[引用] " + head[:160] + (("  ← " + q[:120]) if q and q != head else "")
        return label, out, img, media
    # fallback: strip xml
    return label, re.sub(r"<[^>]+>", " ", body)[:300].strip(), img, media


def load_contacts(backup):
    names = {}
    p = os.path.join(backup, "contact", "contact.db")
    if not os.path.exists(p):
        return names
    c = conn(p)
    cur = c.cursor()
    for username, remark, nick in cur.execute(
            "SELECT username, remark, nick_name FROM contact"):
        names[username] = (remark or "").strip() or (nick or "").strip() or username
    c.close()
    return names


def load_hardlink(backup):
    files = {}
    imgs = {}
    p = os.path.join(backup, "hardlink", "hardlink.db")
    if not os.path.exists(p):
        return files, imgs
    c = conn(p)
    cur = c.cursor()
    for md5, fn in cur.execute("SELECT md5, file_name FROM file_hardlink_info_v4"):
        if md5 and fn:
            files[md5] = fn
    for md5, fn in cur.execute("SELECT md5, file_name FROM image_hardlink_info_v4"):
        if md5 and fn:
            imgs[md5] = fn
    c.close()
    return files, imgs


def scan_chats(backup):
    """-> {username: {"entries": [(db, table, rows)], "maps": {db: {rowid: name}}}}"""
    out = {}
    for i in range(8):
        p = os.path.join(backup, "message", "message_%d.db" % i)
        if not os.path.exists(p):
            continue
        c = conn(p)
        cur = c.cursor()
        id2name = {r[0]: r[1] for r in cur.execute("SELECT rowid, user_name FROM Name2Id")}
        by_hash = {}
        for rid, un in id2name.items():
            if un:
                by_hash[hashlib.md5(un.encode()).hexdigest()] = un
        tabs = [r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
        ).fetchall()]
        for t in tabs:
            uname = by_hash.get(t[4:])
            if uname is None:
                continue
            try:
                n = cur.execute("SELECT COUNT(*) FROM [%s]" % t).fetchone()[0]
            except sqlite3.Error:
                continue
            slot = out.setdefault(uname, {"entries": [], "maps": {}})
            slot["entries"].append((p, t, n))
            slot["maps"][p] = id2name
        c.close()
    return out


def load_voice_text(backup):
    """<create_time>_<local_id>.txt -> {(ct, lid): text}"""
    out = {}
    d = os.path.join(backup, "_voice_text")
    if not os.path.isdir(d):
        return out
    for fn in os.listdir(d):
        if not fn.endswith(".txt"):
            continue
        stem = fn[:-4]
        if "_" not in stem:
            continue
        a, _, b = stem.rpartition("_")
        try:
            out[(int(a), int(b))] = open(os.path.join(d, fn), encoding="utf-8").read().strip()
        except (ValueError, OSError):
            continue
    return out


def export_chat(backup, username, slot, contacts, files, imgs, out_root,
                min_chars=0, voice_map=None):
    is_group = username.endswith("@chatroom")
    chat_name = contacts.get(username, username)
    entries = slot["entries"]
    maps = slot.get("maps", {})
    rows = []
    for db_path, table, _n in entries:
        id2name = maps.get(db_path, {})
        c = conn(db_path)
        cur = c.cursor()
        try:
            cur.execute("SELECT local_id, create_time, real_sender_id, local_type, "
                        "message_content FROM [%s]" % table)
            for lid, ct, rsid, lt, blob in cur:
                try:
                    lt = int(lt)
                except (TypeError, ValueError):
                    lt = 0
                base = lt & 0xFFFFFFFF
                sub = lt >> 32
                raw = decode_blob(blob)
                su = id2name.get(rsid) or ""
                if is_group:
                    sender = ME if su == SELF else (contacts.get(su, su) if su else chat_name)
                else:
                    # a 1:1 chat only has two participants: the peer or me
                    sender = chat_name if su == username else ME
                content = raw
                if is_group:
                    m = re.match(r"^([A-Za-z0-9_\-@\.]{3,64}):\n", raw)
                    if m:
                        prefix_user = m.group(1)
                        content = raw[m.end():]
                        sender = ME if prefix_user == SELF else contacts.get(
                            prefix_user, prefix_user)
                label, body, img, media = classify(base, sub, content, is_group)
                vt = ""
                if base == 34:
                    try:
                        key = (int(ct), int(lid))
                    except (TypeError, ValueError):
                        key = None
                    if key and voice_map:
                        vt = voice_map.get(key, "")
                    if not media and key:
                        media = "media/voice/%s_%s.silk" % key
                rows.append({
                    "时间": fmt_time(ct),
                    "发送者": sender,
                    "类型": label,
                    "内容": body,
                    "图片文件": img,
                    "语音转写": vt,
                    "媒体文件": media,
                    "_ct": int(ct or 0), "_lid": int(lid or 0),
                })
        except sqlite3.Error as e:
            sys.stderr.write("[warn] %s %s: %s\n" % (username, table, e))
        c.close()
    rows.sort(key=lambda r: (r["_ct"], r["_lid"]))
    for r in rows:
        r.pop("_ct", None)
        r.pop("_lid", None)
    if min_chars and sum(len(r["内容"]) for r in rows) < min_chars:
        return None

    folder = os.path.join(out_root, re.sub(r'[\\/:*?"<>|]+', "_", chat_name)[:80] or username)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "chat.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    meta = {
        "chat_id": username, "name": chat_name,
        "type": "group" if is_group else "user",
        "rows_written": len(rows),
        "first_time": rows[0]["时间"] if rows else "",
        "last_time": rows[-1]["时间"] if rows else "",
        "chars": sum(len(r["内容"]) for r in rows),
        "source": "direct-db",
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "folder": folder,
    }
    with open(os.path.join(folder, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--chat", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--min-msgs", type=int, default=1)
    ap.add_argument("--min-chars", type=int, default=0)
    ap.add_argument("--self-id", default="",
                    help="your own wxid; labels your own group messages as 我 "
                         "(or set WX_SELF_ID)")
    args = ap.parse_args()

    global SELF
    if args.self_id:
        SELF = args.self_id

    contacts = load_contacts(args.backup)
    files, imgs = load_hardlink(args.backup)
    voice_map = load_voice_text(args.backup)
    chats = scan_chats(args.backup)
    sys.stderr.write("[info] voice transcripts loaded: %d\n" % len(voice_map))

    if args.list:
        items = sorted(chats.items(), key=lambda kv: -sum(e[2] for e in kv[1]["entries"]))
        print("%-40s %-8s %s" % ("username", "msgs", "display_name"))
        for u, e in items:
            print("%-40s %-8d %s" % (u, sum(x[2] for x in e["entries"]), contacts.get(u, u)))
        print("total chats: %d  messages: %d" % (
            len(items), sum(sum(x[2] for x in e["entries"]) for _u, e in items)))
        return 0

    os.makedirs(args.out, exist_ok=True)
    targets = []
    if args.all:
        targets = sorted(chats.keys())
    for w in args.chat:
        if w in chats:
            targets.append(w)
            continue
        hits = [u for u in chats if w.lower() in u.lower()
                or w.lower() in contacts.get(u, "").lower()]
        if hits:
            targets.extend(hits)
        else:
            sys.stderr.write("[warn] no chat matched %r\n" % w)

    metas = []
    t0 = time.time()
    for u in targets:
        entries = chats[u]
        slot = chats[u]
        if sum(e[2] for e in slot["entries"]) < args.min_msgs:
            continue
        m = export_chat(args.backup, u, slot, contacts, files, imgs, args.out,
                        min_chars=args.min_chars, voice_map=voice_map)
        if m:
            metas.append(m)
            print("[ok] %-34s rows=%-7d %s ~ %s" % (
                (m["name"] or u)[:34], m["rows_written"],
                m["first_time"][:10] or "-", m["last_time"][:10] or "-"), flush=True)
    with open(os.path.join(args.out, "_index.json"), "w", encoding="utf-8") as f:
        json.dump(metas, f, ensure_ascii=False, indent=2)
    print("done: %d chats, %d rows in %.1fs -> %s" % (
        len(metas), sum(m["rows_written"] for m in metas), time.time() - t0, args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())

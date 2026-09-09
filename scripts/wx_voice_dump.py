# -*- coding: utf-8 -*-
r"""wx_voice_dump.py - extract voice-note blobs from decrypted media_*.db.

WeChat 4.x keeps every voice note as a SILK blob in media_*.db -> VoiceInfo.
The WeChat EXP backup only materialises voice files for its recent window, so
this dumper writes them all as <create_time>_<local_id>.silk into
<backup>\media\voice\, matching the naming the /api/voice endpoint expects.

Usage:
  python wx_voice_dump.py --backup <BACKUP_DIR>
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys

SILK_MAGIC = b"\x02#!SILK_"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backup", required=True, help="decrypted backup dir")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    msg_dir = os.path.join(args.backup, "message")
    out_dir = os.path.join(args.backup, "media", "voice")
    os.makedirs(out_dir, exist_ok=True)

    written = skipped = 0
    for name in sorted(os.listdir(msg_dir)):
        if not (name.startswith("media_") and name.endswith(".db")):
            continue
        p = os.path.join(msg_dir, name)
        con = sqlite3.connect("file:" + p.replace("\\", "/") + "?mode=ro", uri=True)
        cur = con.cursor()
        try:
            rows = cur.execute(
                "SELECT create_time, local_id, voice_data FROM VoiceInfo").fetchall()
        except sqlite3.Error as e:
            sys.stderr.write("[warn] %s: %s\n" % (name, e))
            con.close()
            continue
        for ct, lid, blob in rows:
            if not blob:
                continue
            if not blob.startswith(SILK_MAGIC):
                # some rows wrap the silk payload; keep them anyway for debugging
                pass
            fn = "%s_%s.silk" % (ct, lid)
            dst = os.path.join(out_dir, fn)
            if os.path.exists(dst) and not args.force:
                skipped += 1
                continue
            with open(dst, "wb") as f:
                f.write(blob)
            written += 1
        print("[%s] VoiceInfo=%d" % (name, len(rows)))
        con.close()
    print("voice blobs written=%d skipped=%d -> %s" % (written, skipped, out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())

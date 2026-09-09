# -*- coding: utf-8 -*-
import json, sys, urllib.request, collections

BASE = "http://127.0.0.1:5000"

def get(path):
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))

chat = sys.argv[1] if len(sys.argv) > 1 else "a27246266"
cnt = collections.Counter()
samples = {}
total = None
page = 1
while True:
    d = get("/api/messages?chat_id=%s&page=%d&per_page=200" % (urllib.parse.quote(chat), page))
    total = d.get("pagination", {}).get("total")
    msgs = d.get("messages", [])
    if not msgs:
        break
    for m in msgs:
        mt = m.get("msg_type")
        mi = m.get("media_info") or {}
        key = (mt, mi.get("media_type"))
        cnt[key] += 1
        if key not in samples:
            samples[key] = {k: (str(v)[:180] if not isinstance(v, (dict, list)) else json.dumps(v, ensure_ascii=False)[:300]) for k, v in m.items()}
    if len(msgs) < 200:
        break
    page += 1
    if page > 40:
        break

print("chat=%s total=%s fetched_types=%d" % (chat, total, len(cnt)))
for k, v in cnt.most_common():
    print("  msg_type=%s media_type=%s  n=%d" % (k[0], k[1], v))
print("=== samples ===")
for k in list(samples)[:12]:
    print("--- msg_type=%s media_type=%s" % k)
    print(json.dumps(samples[k], ensure_ascii=False, indent=1)[:1400])

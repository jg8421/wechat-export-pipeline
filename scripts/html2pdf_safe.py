# -*- coding: utf-8 -*-
"""HTML -> PDF via Edge/Chrome DevTools Protocol (page numbers, backgrounds).

Usage: py -3 html2pdf.py input.html output.pdf [footer_label]
"""
import base64, json, os, socket, struct, subprocess, sys, time, urllib.request, secrets

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class WS:
    def __init__(self, url):
        # ws://127.0.0.1:PORT/devtools/page/<id>
        assert url.startswith("ws://")
        rest = url[5:]
        hostport, path = rest.split("/", 1)
        host, port = hostport.split(":")
        self.sock = socket.create_connection((host, int(port)), timeout=900)
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        req = (
            "GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            buf += self.sock.recv(4096)
        if b"101" not in buf.split(b"\r\n")[0]:
            raise RuntimeError("handshake failed: %r" % buf[:200])
        self.buf = buf.split(b"\r\n\r\n", 1)[1]

    def _recv(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, obj):
        data = json.dumps(obj).encode()
        header = bytearray([0x81])
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = secrets.token_bytes(4)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv(self):
        b1, b2 = self._recv(2)
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv(8))[0]
        payload = self._recv(length)
        return payload.decode("utf-8", "replace")

    def call(self, method, params=None, timeout=120):
        self._id = getattr(self, "_id", 0) + 1
        mid = self._id
        self.send({"id": mid, "method": method, "params": params or {}})
        end = time.time() + timeout
        while time.time() < end:
            msg = json.loads(self.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})
        raise TimeoutError(method)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def convert(html_path, pdf_path, footer_label=""):
    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    port = _free_port()
    udd = os.path.join(os.environ.get("TEMP", "."), "edgepdf_%d_%d" % (os.getpid(), port))
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
         "--user-data-dir=" + udd,
         "--remote-debugging-port=%d" % port, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        targets = None
        for _ in range(40):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=2) as r:
                    targets = json.load(r)
                if targets:
                    break
            except Exception:
                time.sleep(0.5)
        if not targets:
            raise RuntimeError("cannot reach DevTools endpoint")
        page = next((t for t in targets if t.get("type") == "page"), targets[0])
        ws = WS(page["webSocketDebuggerUrl"])
        try:
            ws.call("Page.enable")
            ws.call("Page.navigate", {"url": url})
            time.sleep(float(os.environ.get("PDF_WAIT", "3")))
            footer = (
                '<div style="width:100%%;font-size:8px;color:#909090;'
                'text-align:center;font-family:Arial,sans-serif;'
                'padding:0 8mm;">%s<span class="pageNumber"></span> / '
                '<span class="totalPages"></span></div>' % footer_label
            )
            res = ws.call("Page.printToPDF", {
                "printBackground": True,
                "displayHeaderFooter": True,
                "headerTemplate": "<div></div>",
                "footerTemplate": footer,
                "marginTop": 0.63, "marginBottom": 0.75,
                "marginLeft": 0.59, "marginRight": 0.59,
                "preferCSSPageSize": False,
                "paperWidth": 8.27, "paperHeight": 11.69,
            }, timeout=int(os.environ.get("PDF_TIMEOUT", "900")))
            data = base64.b64decode(res["data"])
            with open(pdf_path, "wb") as f:
                f.write(data)
            return len(data)
        finally:
            ws.close()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else ""
    size = convert(src, dst, label)
    print("pdf ok:", dst, size, "bytes")
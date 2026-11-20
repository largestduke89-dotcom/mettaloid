"""Local auto-clip API (127.0.0.1 only).

  POST /clip      {"event":"Ace","before":15,"after":5}
  POST /bookmark  {"label":"Clutch"}
  GET  /status
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class ApiServer:
    def __init__(self, settings, on_clip, on_bookmark, get_status):
        self.settings, self.on_clip, self.on_bookmark, self.get_status = settings, on_clip, on_bookmark, get_status
        self.httpd = None

    def restart(self):
        self.close()
        cfg = self.settings.get()["autoClip"]
        if not cfg["enabled"]:
            return
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, code, body):
                data = json.dumps(body, default=str).encode()
                self.send_response(code)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.headers.get("Origin"):
                    return self.reply(403, {"error": "forbidden"})
                if self.path == "/status":
                    return self.reply(200, outer.get_status())
                self.reply(404, {"error": "not found"})

            def do_POST(self):
                if self.headers.get("Origin"):
                    return self.reply(403, {"error": "forbidden"})
                n = min(int(self.headers.get("content-length") or 0), 100000)
                try:
                    body = json.loads(self.rfile.read(n) or b"{}")
                except ValueError:
                    return self.reply(400, {"error": "invalid json"})
                c = outer.settings.get()["autoClip"]
                try:
                    if self.path == "/clip":
                        before = min(600, max(1, float(body.get("before") or c["secondsBefore"])))
                        after = min(60, max(0, float(body.get("after", c["secondsAfter"]))))
                        return self.reply(200, outer.on_clip(str(body.get("event") or "Event")[:60], before, after))
                    if self.path == "/bookmark":
                        return self.reply(200, outer.on_bookmark(str(body.get("label") or "Bookmark")[:60]))
                    self.reply(404, {"error": "not found"})
                except Exception as e:  # noqa: BLE001
                    self.reply(500, {"error": str(e)})
        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", int(cfg["apiPort"])), H)
            self.httpd.daemon_threads = True
            threading.Thread(target=self.httpd.serve_forever, daemon=True, name="api").start()
        except OSError as e:
            print("auto-clip API could not start:", e)
            self.httpd = None

    def close(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

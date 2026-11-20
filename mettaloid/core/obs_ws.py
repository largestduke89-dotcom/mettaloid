"""Minimal obs-websocket v5 client (blocking requests, events on a reader thread)."""
import base64
import hashlib
import json
import threading
import uuid

import websocket

EVENTS_ALL = (1 << 11) - 1


class ObsError(Exception):
    def __init__(self, code, comment):
        super().__init__(comment or f"OBS request failed ({code})")
        self.code = code


class ObsWs:
    def __init__(self, on_event=None, on_close=None):
        self.ws = None
        self.pending = {}
        self.lock = threading.Lock()
        self.on_event = on_event
        self.on_close = on_close
        self.connected = False

    def connect(self, url, password, timeout=5):
        ws = websocket.create_connection(url, timeout=timeout, enable_multithread=True)
        hello = json.loads(ws.recv())
        if hello.get("op") != 0:
            raise ConnectionError("unexpected hello")
        ident = {"rpcVersion": 1, "eventSubscriptions": EVENTS_ALL}
        auth = hello["d"].get("authentication")
        if auth:
            secret = base64.b64encode(hashlib.sha256((password + auth["salt"]).encode()).digest())
            ident["authentication"] = base64.b64encode(hashlib.sha256(secret + auth["challenge"].encode()).digest()).decode()
        ws.send(json.dumps({"op": 1, "d": ident}))
        msg = json.loads(ws.recv())
        if msg.get("op") != 2:
            raise ConnectionError("identify failed")
        ws.settimeout(None)
        self.ws = ws
        self.connected = True
        threading.Thread(target=self._reader, daemon=True, name="obs-ws").start()

    def _reader(self):
        ws = self.ws
        try:
            while True:
                raw = ws.recv()
                if not raw:
                    break
                msg = json.loads(raw)
                op, d = msg.get("op"), msg.get("d", {})
                if op == 7:
                    with self.lock:
                        slot = self.pending.pop(d.get("requestId"), None)
                    if slot:
                        slot["resp"] = d
                        slot["ev"].set()
                elif op == 5 and self.on_event:
                    try:
                        self.on_event(d.get("eventType"), d.get("eventData") or {})
                    except Exception as e:  # noqa: BLE001
                        print("obs event handler error:", e)
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.connected = False
            with self.lock:
                for slot in self.pending.values():
                    slot["ev"].set()
                self.pending.clear()
            if self.on_close and ws is self.ws:
                self.on_close()

    def call(self, request_type, data=None, timeout=15):
        if not self.connected:
            raise ObsError(-1, "Not connected to OBS")
        rid = uuid.uuid4().hex
        slot = {"ev": threading.Event(), "resp": None}
        with self.lock:
            self.pending[rid] = slot
        msg = {"op": 6, "d": {"requestType": request_type, "requestId": rid}}
        if data is not None:
            msg["d"]["requestData"] = data
        self.ws.send(json.dumps(msg))
        if not slot["ev"].wait(timeout) or slot["resp"] is None:
            with self.lock:
                self.pending.pop(rid, None)
            raise ObsError(-2, f"{request_type} timed out")
        st = slot["resp"].get("requestStatus", {})
        if not st.get("result"):
            raise ObsError(st.get("code"), st.get("comment") or f"{request_type} failed (code {st.get('code')})")
        return slot["resp"].get("responseData") or {}

    def close(self):
        ws, self.ws = self.ws, None
        self.connected = False
        try:
            if ws:
                ws.close()
        except Exception:  # noqa: BLE001
            pass

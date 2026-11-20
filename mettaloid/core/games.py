"""Game detection: in-process process scanning (no child processes).

While a game runs only its PID is checked (practically free); full scans run
every `detectionInterval` seconds when idle and every 10 s while gaming.
"""
import json
import os
import threading
import time

import psutil

from . import paths

with open(os.path.join(paths.DATA, "games.json"), encoding="utf-8") as _f:
    BUILTIN = json.load(_f)


class GameDetector:
    def __init__(self, settings):
        self.settings = settings
        self.current = None
        self.on_change = []
        self.stop_ev = threading.Event()
        self.last_full = 0
        self.miss = 0

    def games(self):
        g = self.settings.get()["games"]
        custom = [{**c, "exe": c["exe"] if isinstance(c["exe"], list) else [c["exe"]], "custom": True, "category": c.get("category", "Custom")} for c in g.get("custom", [])]
        disabled = set(g.get("disabled", []))
        return [x for x in custom + BUILTIN if x["id"] not in disabled]

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="games").start()

    def stop(self):
        self.stop_ev.set()

    def _loop(self):
        while not self.stop_ev.is_set():
            if self.current:
                alive = psutil.pid_exists(self.current["pid"])
                if not alive or time.time() - self.last_full > 10:
                    self.poll()
                wait = 2
            else:
                self.poll()
                wait = max(1, int(self.settings.get()["performance"].get("detectionInterval", 3)))
            self.stop_ev.wait(wait)

    def _window_title_has(self, pid, needle):
        import ctypes
        from ctypes import wintypes
        found = []
        user32 = ctypes.windll.user32

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _):
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == pid and user32.IsWindowVisible(hwnd):
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                if needle.lower() in buf.value.lower():
                    found.append(True)
                    return False
            return True
        user32.EnumWindows(cb, 0)
        return bool(found)

    def poll(self):
        self.last_full = time.time()
        running = {}
        for p in psutil.process_iter(["name", "pid"]):
            n = (p.info.get("name") or "").lower()
            if n:
                running.setdefault(n, p.info["pid"])
        found = None
        for g in self.games():
            exe = next((e for e in g["exe"] if e.lower() in running), None)
            if not exe:
                continue
            pid = running[exe.lower()]
            if g.get("requireWindowTitle") and not self._window_title_has(pid, g["requireWindowTitle"]):
                continue
            found = {"id": g["id"], "name": g["name"], "exe": exe, "pid": pid, "category": g.get("category")}
            break
        if found:
            self.miss = 0
            if not self.current or self.current["id"] != found["id"] or self.current["pid"] != found["pid"]:
                self.current = found
                self._emit(found)
        elif self.current:
            self.miss += 1
            if self.miss >= 2:
                self.current = None
                self.miss = 0
                self._emit(None)

    def _emit(self, g):
        for fn in self.on_change:
            try:
                fn(g)
            except Exception as e:  # noqa: BLE001
                print("game change handler error:", e)

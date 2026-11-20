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

    @staticmethod
    def window_pids():
        """PIDs that own a visible, non-cloaked, non-tiny top-level window."""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        dwm = ctypes.windll.dwmapi
        pids = set()

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            cloaked = ctypes.c_int(0)
            dwm.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), 4)  # DWMWA_CLOAKED
            if cloaked.value:
                return True
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if (r.right - r.left) < 160 or (r.bottom - r.top) < 120:
                if not user32.IsIconic(hwnd):  # minimized games still count
                    return True
            p = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            pids.add(p.value)
            return True
        user32.EnumWindows(cb, 0)
        return pids

    def poll(self):
        self.last_full = time.time()
        running = {}
        for p in psutil.process_iter(["name", "pid"]):
            n = (p.info.get("name") or "").lower()
            if n:
                running.setdefault(n, []).append(p.info["pid"])
        with_window = self.window_pids()
        found = None
        for g in self.games():
            # background processes without a window (e.g. Roblox after you close it) don't count
            hit = next(((e, pid) for e in g["exe"] for pid in running.get(e.lower(), []) if pid in with_window), None)
            if not hit:
                continue
            exe, pid = hit
            if g.get("requireWindowTitle") and not self._window_title_has(pid, g["requireWindowTitle"]):
                continue
            found = {"id": g["id"], "name": g["name"], "exe": exe, "pid": pid, "category": g.get("category"), "capture": g.get("capture")}
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

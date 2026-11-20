"""Global hotkeys via RegisterHotKey on a dedicated message-loop thread."""
import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
WM_HOTKEY, WM_QUIT, WM_APP = 0x0312, 0x0012, 0x8000
MOD = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "super": 0x8, "win": 0x8, "cmdorctrl": 0x2}
MOD_NOREPEAT = 0x4000

VK = {f"f{i}": 0x6F + i for i in range(1, 25)}
VK.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
VK.update({str(d): 0x30 + d for d in range(10)})
VK.update({f"num{d}": 0x60 + d for d in range(10)})
VK.update({"space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "backspace": 0x08, "delete": 0x2E,
           "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25,
           "right": 0x27, "printscreen": 0x2C, "pause": 0x13, "scrolllock": 0x91, "numadd": 0x6B, "numsub": 0x6D, "nummult": 0x6A,
           "numdiv": 0x6F, "numdec": 0x6E, "plus": 0xBB, "-": 0xBD, "=": 0xBB, ",": 0xBC, ".": 0xBE, "/": 0xBF, ";": 0xBA,
           "'": 0xDE, "[": 0xDB, "]": 0xDD, "\\": 0xDC, "`": 0xC0})


def parse(acc):
    if not acc:
        return None
    mods, key = 0, None
    for part in acc.split("+"):
        p = part.strip().lower()
        if p in MOD:
            mods |= MOD[p]
        elif p in VK:
            key = VK[p]
        else:
            return None
    return (mods, key) if key else None


class Hotkeys:
    """bindings: list of (accelerator, callback, name). Callbacks run on the hotkey thread."""

    def __init__(self):
        self.thread_id = None
        self.thread = None
        self.failed = []
        self.paused = False
        self.bindings = []

    def register(self, bindings):
        self.bindings = bindings
        self._restart()
        return self.failed

    def _restart(self):
        self.stop()
        self.failed = []
        if self.paused or not self.bindings:
            return
        ready = threading.Event()
        self.thread = threading.Thread(target=self._loop, args=(list(self.bindings), ready), daemon=True, name="hotkeys")
        self.thread.start()
        ready.wait(2)

    def _loop(self, bindings, ready):
        self.thread_id = kernel32.GetCurrentThreadId()
        handlers = {}
        failed = []
        for i, (acc, fn, name) in enumerate(bindings, start=1):
            if not acc:
                continue
            k = parse(acc)
            if not k or not user32.RegisterHotKey(None, i, k[0] | MOD_NOREPEAT, k[1]):
                failed.append({"accelerator": acc, "name": name, "invalid": not k})
                continue
            handlers[i] = fn
        self.failed = failed
        ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in handlers:
                try:
                    threading.Thread(target=handlers[msg.wParam], daemon=True).start()
                except Exception as e:  # noqa: BLE001
                    print("hotkey error:", e)
        for i in handlers:
            user32.UnregisterHotKey(None, i)

    def stop(self):
        if self.thread and self.thread.is_alive() and self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
            self.thread.join(2)
        self.thread = None

    def pause(self):
        self.paused = True
        self.stop()

    def resume(self):
        self.paused = False
        self._restart()
        return self.failed

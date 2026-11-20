"""Process helpers: hidden windows, priorities, killing trees."""
import subprocess

import psutil

CREATE_NO_WINDOW = 0x08000000
PRIORITY = {
    "High": psutil.HIGH_PRIORITY_CLASS,
    "AboveNormal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
    "Normal": psutil.NORMAL_PRIORITY_CLASS,
    "BelowNormal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
    "Idle": psutil.IDLE_PRIORITY_CLASS,
}


def popen(args, **kw):
    kw.setdefault("creationflags", CREATE_NO_WINDOW)
    kw.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.Popen(args, **kw)


def set_priority(pid, name):
    try:
        psutil.Process(pid).nice(PRIORITY.get(name, psutil.NORMAL_PRIORITY_CLASS))
    except Exception:  # noqa: BLE001
        pass


def run_quiet(args, timeout=30):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW).stdout
    except Exception:  # noqa: BLE001
        return ""

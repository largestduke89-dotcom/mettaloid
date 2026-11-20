"""Rotating log file in %APPDATA%/Mettaloid/logs (stdout/stderr and crashes go there too)."""
import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

from . import paths

LOG_DIR = os.path.join(paths.USER_DATA, "logs")


class _Stream:
    """File-like object that forwards writes to the logger (print() → log)."""

    def __init__(self, level, original):
        self.level, self.original, self.buf = level, original, ""

    def write(self, s):
        if self.original:
            try:
                self.original.write(s)
            except Exception:  # noqa: BLE001
                pass
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                logging.getLogger("mettaloid").log(self.level, line.rstrip())

    def flush(self):
        if self.original:
            try:
                self.original.flush()
            except Exception:  # noqa: BLE001
                pass


def setup(version):
    os.makedirs(LOG_DIR, exist_ok=True)
    h = RotatingFileHandler(os.path.join(LOG_DIR, "mettaloid.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(h)
    log = logging.getLogger("mettaloid")
    log.info("Mettaloid %s starting (frozen=%s, python=%s)", version, paths.FROZEN, sys.version.split()[0])
    sys.stdout = _Stream(logging.INFO, sys.stdout)
    sys.stderr = _Stream(logging.ERROR, sys.stderr)

    def hook(t, v, tb):
        log.critical("Uncaught exception", exc_info=(t, v, tb))
    sys.excepthook = hook
    threading.excepthook = lambda a: log.error("Uncaught exception in thread %s", a.thread.name if a.thread else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        lv = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO, QtMsgType.QtWarningMsg: logging.WARNING,
              QtMsgType.QtCriticalMsg: logging.ERROR, QtMsgType.QtFatalMsg: logging.CRITICAL}
        qInstallMessageHandler(lambda mode, ctx, msg: logging.getLogger("qt").log(lv.get(mode, logging.INFO), msg))
    except Exception:  # noqa: BLE001
        pass

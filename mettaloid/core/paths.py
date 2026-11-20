"""Filesystem locations (works from source and from the PyInstaller bundle)."""
import ctypes
import os
import sys
import uuid

FROZEN = bool(getattr(sys, "frozen", False))
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../mettaloid
APP_DIR = os.path.dirname(sys.executable) if FROZEN else os.path.dirname(PKG_DIR)  # install dir / repo root
BUNDLE = getattr(sys, "_MEIPASS", APP_DIR)
VENDOR = os.path.join(APP_DIR, "vendor")
DATA = os.path.join(PKG_DIR, "data")
ASSETS = os.path.join(BUNDLE, "assets")

USER_DATA = os.environ.get("MT_USER_DATA") or os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "Mettaloid")
os.makedirs(USER_DATA, exist_ok=True)

FFMPEG = os.path.join(VENDOR, "ffmpeg", "ffmpeg.exe")
FFPROBE = os.path.join(VENDOR, "ffmpeg", "ffprobe.exe")
ENGINE_SCRIPT = os.path.join(PKG_DIR, "engine", "findframe_engine.py")


def videos_dir():
    """The user's Videos known folder (may be redirected, e.g. to OneDrive)."""
    try:
        class GUID(ctypes.Structure):
            _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]
        u = uuid.UUID("{18989B1D-99B5-455B-841C-AB7C74E4DDFC}")  # FOLDERID_Videos
        g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        p = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(p)) == 0:
            out = p.value
            ctypes.windll.ole32.CoTaskMemFree(p)
            return out
    except Exception:  # noqa: BLE001
        pass
    return os.path.join(os.path.expanduser("~"), "Videos")


def asset(name):
    return os.path.join(ASSETS, name)

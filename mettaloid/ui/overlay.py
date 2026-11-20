"""In-game notification overlay (click-through, never takes focus) + capture sounds."""
import math
import os
import struct
import wave

from PySide6.QtCore import QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QWidget

from ..core import paths
from .theme import C

TONES = {"clip": [880, 1320], "error": [300, 200], "rec": [660, 990, 660], "mark": [1200], "shot": [1500, 1800]}


def _make_wav(path, freqs):
    rate = 44100
    frames = bytearray()
    for i, f in enumerate(freqs):
        n = int(rate * 0.12)
        for k in range(n):
            t = k / rate
            env = min(1.0, k / (rate * 0.012)) * math.exp(-t * 18)
            frames += struct.pack("<h", int(32767 * 0.45 * env * math.sin(2 * math.pi * f * t)))
        if i < len(freqs) - 1:
            frames += b"\x00\x00" * int(rate * 0.015)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))


class Sounds:
    def __init__(self):
        d = os.path.join(paths.USER_DATA, "sounds")
        os.makedirs(d, exist_ok=True)
        self.fx = {}
        for name, freqs in TONES.items():
            p = os.path.join(d, f"{name}.wav")
            if not os.path.exists(p):
                _make_wav(p, freqs)
            self.fx[name] = p
        self.players = {}

    def play(self, name, volume):
        p = self.fx.get(name)
        if not p:
            return
        s = self.players.get(name)
        if s is None:
            s = QSoundEffect()
            s.setSource(QUrl.fromLocalFile(p))
            self.players[name] = s
        s.setVolume(max(0.0, min(1.0, volume)))
        s.play()


class Overlay(QWidget):
    def __init__(self, settings):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.settings = settings
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(380, 96)
        self.data = None
        self.hide_timer = QTimer(self, singleShot=True, timeout=self.hide)

    def place(self):
        g = QGuiApplication.primaryScreen().availableGeometry()
        pos = self.settings.get()["overlay"]["position"]
        x = g.x() + 24 if "left" in pos else g.right() - self.width() - 24
        y = g.bottom() - self.height() - 24 if "bottom" in pos else g.y() + 24
        self.move(x, y)

    def show_toast(self, t):
        self.data = t
        self.place()
        self.show()
        self.raise_()
        self.update()
        self.hide_timer.start(3000)

    def paintEvent(self, _):
        if not self.data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRect(8, 8, self.width() - 16, self.height() - 16)
        p.setPen(QPen(QColor(255, 255, 255, 22)))
        p.setBrush(QColor(16, 17, 22, 236))
        p.drawRoundedRect(r, 14, 14)
        kind = self.data.get("kind")
        ic = QRect(r.x() + 16, r.center().y() - 19, 38, 38)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["red"]) if kind in ("err", "rec") else QColor(C["accent"]))
        p.drawRoundedRect(ic, 10, 10)
        p.setPen(QColor("white") if kind in ("err", "rec") else QColor(C["ink"]))
        f = QFont("Segoe UI", 14)
        f.setBold(True)
        p.setFont(f)
        p.drawText(ic, Qt.AlignCenter, "!" if kind == "err" else "●" if kind == "rec" else "★" if kind == "mark" else "✓")
        f = QFont("Segoe UI", 11)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(C["text"]))
        tx = ic.right() + 14
        p.drawText(QRect(tx, r.y() + 16, r.right() - tx - 12, 22), Qt.AlignLeft | Qt.AlignVCenter, self.data.get("title", ""))
        f = QFont("Segoe UI", 9)
        p.setFont(f)
        p.setPen(QColor(C["text2"]))
        sub = p.fontMetrics().elidedText(self.data.get("subtitle", ""), Qt.ElideRight, r.right() - tx - 12)
        p.drawText(QRect(tx, r.y() + 40, r.right() - tx - 12, 20), Qt.AlignLeft | Qt.AlignVCenter, sub)

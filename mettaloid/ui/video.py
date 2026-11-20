"""Video playback helpers: player wrapper, transport bar, frame canvas with box drawing."""
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget

from . import icons
from .theme import C
from .widgets import btn, fmt_time


class Player(QObject):
    position = Signal(float)   # seconds
    duration = Signal(float)
    playing = Signal(bool)
    loaded = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mp = QMediaPlayer(self)
        self.ao = QAudioOutput(self)
        self.mp.setAudioOutput(self.ao)
        self.mp.positionChanged.connect(lambda ms: self.position.emit(ms / 1000))
        self.mp.durationChanged.connect(lambda ms: self.duration.emit(ms / 1000))
        self.mp.playbackStateChanged.connect(lambda s: self.playing.emit(s == QMediaPlayer.PlayingState))
        self.mp.mediaStatusChanged.connect(self._status)
        self._seek_on_load = None
        self._play_on_load = False

    def _status(self, st):
        if st in (QMediaPlayer.LoadedMedia, QMediaPlayer.BufferedMedia) and self._seek_on_load is not None:
            t, self._seek_on_load = self._seek_on_load, None
            self.mp.setPosition(int(t * 1000))
            if self._play_on_load:
                self.mp.play()
            else:
                # paused start: decode briefly so a frame is actually presented
                self.mp.setAudioOutput(None)
                self.mp.play()

                def settle():
                    self.mp.pause()
                    self.mp.setPosition(int(t * 1000))
                    self.mp.setAudioOutput(self.ao)
                QTimer.singleShot(150, settle)
            self.loaded.emit()
        elif st == QMediaPlayer.LoadedMedia:
            self.loaded.emit()

    def load(self, path, start=0.0, autoplay=True):
        self._seek_on_load = start
        self._play_on_load = autoplay
        self.mp.setSource(QUrl.fromLocalFile(path))
        if autoplay and not start:
            self.mp.play()

    def release(self):
        self.mp.stop()
        self.mp.setSource(QUrl())

    def toggle(self):
        if self.mp.playbackState() == QMediaPlayer.PlayingState:
            self.mp.pause()
        else:
            self.mp.play()

    def is_playing(self):
        return self.mp.playbackState() == QMediaPlayer.PlayingState

    def seek(self, t):
        self.mp.setPosition(int(max(0, t) * 1000))

    def pos(self):
        return self.mp.position() / 1000

    def dur(self):
        return self.mp.duration() / 1000

    def set_volume(self, v):
        self.ao.setVolume(max(0.0, min(1.0, v)))


class TransportBar(QWidget):
    def __init__(self, player, extra=None):
        super().__init__()
        self.player = player
        self.setStyleSheet(f"background: {C['bg2']};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(10)
        self.play = btn("", "play", self.player.toggle, ghost=True, sm=True)
        lay.addWidget(self.play)
        self.time = QLabel("0:00.0 / 0:00.0")
        self.time.setStyleSheet(f"color: {C['text2']}; font-family: 'Cascadia Mono', Consolas; font-size: 12px;")
        lay.addWidget(self.time)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(lambda v: self.player.seek(v / 1000))
        self.seek.sliderPressed.connect(lambda: self.player.seek(self.seek.value() / 1000))
        lay.addWidget(self.seek, 1)
        for w in extra or []:
            lay.addWidget(w)
        vol_ic = QLabel()
        vol_ic.setPixmap(icons.pixmap("volume", C["text2"], 15))
        lay.addWidget(vol_ic)
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setValue(100)
        self.vol.setFixedWidth(80)
        self.vol.valueChanged.connect(lambda v: self.player.set_volume(v / 100))
        lay.addWidget(self.vol)
        player.position.connect(self._pos)
        player.duration.connect(lambda d: self.seek.setRange(0, int(d * 1000)))
        player.playing.connect(lambda p: self.play.setIcon(icons.icon("pause" if p else "play", C["text"], 16, fill="currentColor")))
        self.play.setIcon(icons.icon("play", C["text"], 16, fill="currentColor"))

    def _pos(self, t):
        if not self.seek.isSliderDown():
            self.seek.setValue(int(t * 1000))
        self.time.setText(f"{fmt_time(t)} / {fmt_time(self.player.dur())}")


class FrameCanvas(QWidget):
    """Shows a still frame (aspect-fit). In draw mode the user drags a box → box_drawn(dict normalized)."""
    box_drawn = Signal(dict)
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.img = QImage()
        self.drawing = False
        self.a = self.b = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setStyleSheet("background: black;")

    def set_image(self, img):
        self.img = img
        self.update()

    def content_rect(self):
        if self.img.isNull():
            return QRectF(self.rect())
        s = min(self.width() / self.img.width(), self.height() / self.img.height())
        w, h = self.img.width() * s, self.img.height() * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def start_draw(self):
        self.drawing = True
        self.a = self.b = None
        self.setCursor(Qt.CrossCursor)
        self.setFocus()
        self.update()

    def stop_draw(self):
        self.drawing = False
        self.unsetCursor()
        self.update()

    def _clamp(self, p):
        r = self.content_rect()
        return QPointF(min(max(p.x(), r.left()), r.right()), min(max(p.y(), r.top()), r.bottom()))

    def mousePressEvent(self, e):
        if self.drawing and e.button() == Qt.LeftButton:
            self.a = self.b = self._clamp(e.position())
            self.update()

    def mouseMoveEvent(self, e):
        if self.drawing and self.a is not None:
            self.b = self._clamp(e.position())
            self.update()

    def mouseReleaseEvent(self, e):
        if not (self.drawing and self.a is not None):
            return
        self.b = self._clamp(e.position())
        r = self.content_rect()
        x0, x1 = sorted((self.a.x(), self.b.x()))
        y0, y1 = sorted((self.a.y(), self.b.y()))
        self.a = self.b = None
        self.update()
        if x1 - x0 < 8 or y1 - y0 < 8:
            return
        self.box_drawn.emit({"x": (x0 - r.x()) / r.width(), "y": (y0 - r.y()) / r.height(), "w": (x1 - x0) / r.width(), "h": (y1 - y0) / r.height()})

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.drawing:
            self.stop_draw()
            self.cancelled.emit()
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if not self.img.isNull():
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            p.drawImage(self.content_rect(), self.img)
        if self.drawing:
            if self.a is not None:
                sel = QRectF(self.a, self.b).normalized()
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(0, 0, 0, 130))
                full = QRectF(self.rect())
                p.drawRect(QRectF(full.left(), full.top(), full.width(), sel.top() - full.top()))
                p.drawRect(QRectF(full.left(), sel.bottom(), full.width(), full.bottom() - sel.bottom()))
                p.drawRect(QRectF(full.left(), sel.top(), sel.left() - full.left(), sel.height()))
                p.drawRect(QRectF(sel.right(), sel.top(), full.right() - sel.right(), sel.height()))
                p.setPen(QPen(QColor(C["accent"]), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(sel)
            else:
                p.fillRect(self.rect(), QColor(0, 0, 0, 40))
                msg = "Draw a box around what you want to find  ·  Esc to cancel"
                fm = p.fontMetrics()
                w = fm.horizontalAdvance(msg) + 28
                r = QRectF((self.width() - w) / 2, 14, w, 34)
                p.setPen(QPen(QColor(C["line2"])))
                p.setBrush(QColor(16, 17, 22, 235))
                p.setRenderHint(QPainter.Antialiasing)
                p.drawRoundedRect(r, 10, 10)
                p.setPen(QColor(C["text"]))
                p.drawText(r, Qt.AlignCenter, msg)


_ = QTimer

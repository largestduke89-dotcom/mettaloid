"""Non-destructive editor: live preview, segment timeline, crop/aspect, look, text, per-track audio, export."""
import copy
import hashlib
import json
import os
import time as _time

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QKeySequence, QPainter, QPainterPath, QPen, QRadialGradient, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import (QColorDialog, QDoubleSpinBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QScrollArea, QTabWidget, QVBoxLayout, QWidget)

from ...core import ffmpeg, paths, render
from .. import icons
from ..bg import run_bg
from ..theme import C
from ..video import Player
from ..widgets import Combo, FSlider, Seg, Toggle, btn, flow, fmt_dur, fmt_time, hbox, label
from . import Page
from .timeline import Timeline, seg_dur

DEFAULT_COLOR = {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0, "gamma": 1.0, "sharpen": 0.0, "vignette": 0.0, "hue": 0.0}
LOOKS = [("None", {}), ("Vivid", {"contrast": 1.12, "saturation": 1.35, "sharpen": 0.4}), ("Punchy", {"contrast": 1.25, "saturation": 1.15, "brightness": 0.02, "sharpen": 0.6, "vignette": 0.4}),
         ("Cinematic", {"contrast": 1.1, "saturation": 0.85, "gamma": 0.95, "vignette": 0.6}), ("Bright", {"brightness": 0.07, "contrast": 1.05, "saturation": 1.1}),
         ("B&W", {"saturation": 0.0, "contrast": 1.2}), ("Warm", {"hue": -8, "saturation": 1.15}), ("Cool", {"hue": 10, "saturation": 1.05})]
ASPECTS = [("source", "Original"), ("16:9", "16:9"), ("9:16", "9:16 (TikTok/Shorts)"), ("1:1", "1:1"), ("4:5", "4:5"), ("21:9", "21:9")]
SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4]
TRACK_NAMES = ["Mix (all audio)", "Game / desktop", "Microphone", "Discord"]


def clamp(v, a, b):
    return max(a, min(b, v))


def offsets(segs):
    t, out = 0.0, []
    for s in segs:
        out.append((t, t + seg_dur(s)))
        t += seg_dur(s)
    return out


def locate(segs, offs, t):
    for i, (a, b) in enumerate(offs):
        if t < b - 1e-6 or i == len(segs) - 1:
            local = clamp(t - a, 0, seg_dur(segs[i]))
            return i, segs[i]["start"] + local * (segs[i].get("speed") or 1)
    return 0, 0.0


def parse_aspect(a):
    if not a or a == "source":
        return None
    x, _, y = a.partition(":")
    return float(x) / float(y)


def apply_color(img, col):
    """Approximate the export colour filters on a (small) preview image with numpy."""
    if all(abs(col.get(k, v) - v) < 1e-6 for k, v in DEFAULT_COLOR.items() if k not in ("sharpen", "vignette")):
        return img
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    a = np.frombuffer(img.constBits(), np.uint8).reshape(h, bpl)[:, : w * 3].reshape(h, w, 3).astype(np.float32) / 255.0
    a = (a - 0.5) * col.get("contrast", 1) + 0.5 + col.get("brightness", 0)
    s = col.get("saturation", 1)
    if s != 1:
        lum = (a @ np.array([0.299, 0.587, 0.114], np.float32))[..., None]
        a = lum + (a - lum) * s
    hue = col.get("hue", 0)
    if hue:
        th = np.radians(hue)
        c, sn = np.cos(th), np.sin(th)
        m = np.array([[0.299 + 0.701 * c + 0.168 * sn, 0.587 - 0.587 * c + 0.330 * sn, 0.114 - 0.114 * c - 0.497 * sn],
                      [0.299 - 0.299 * c - 0.328 * sn, 0.587 + 0.413 * c + 0.035 * sn, 0.114 - 0.114 * c + 0.292 * sn],
                      [0.299 - 0.300 * c + 1.250 * sn, 0.587 - 0.588 * c - 1.050 * sn, 0.114 + 0.886 * c - 0.203 * sn]], np.float32)
        a = a @ m.T
    g = col.get("gamma", 1)
    a = np.clip(a, 0, 1)
    if g != 1:
        a = a ** (1 / g)
    out = np.ascontiguousarray((a * 255).astype(np.uint8))
    return QImage(out.data, w, h, w * 3, QImage.Format_RGB888).copy()


def default_project(clips, start=None, end=None, seg=None):
    single = len(clips) == 1
    tracks = clips[0].get("audioTracks") or [] if single else []
    if seg:
        segments = []
        for part in seg.split(","):
            cid, a, b = part.split(":")
            src = next((i for i, c in enumerate(clips) if c["id"] == cid), -1)
            if src >= 0:
                segments.append({"src": src, "start": max(0.0, float(a)), "end": min(clips[src]["duration"], float(b)), "speed": 1})
    else:
        segments = [{"src": i, "start": float(start) if single and start is not None else 0.0,
                     "end": float(end) if single and end is not None else c["duration"], "speed": 1} for i, c in enumerate(clips)]
    return {
        "sources": [c["path"] for c in clips], "segments": segments, "crop": None, "rotate": 0, "flipH": False, "flipV": False,
        "aspect": "source", "fit": "crop", "color": dict(DEFAULT_COLOR), "fadeIn": 0.0, "fadeOut": 0.0, "texts": [],
        "audio": {"tracks": [{"index": i, "title": t["title"], "volume": 1.0, "muted": (i == 0 or i >= 3) if len(tracks) > 1 else False} for i, t in enumerate(tracks)],
                  "normalize": False, "music": None},
        "output": {"resolution": "source", "fps": "source", "format": "mp4", "codec": "h264", "quality": "high", "targetMB": 0, "encoder": "auto",
                   "mode": "encode", "audioKbps": 160, "autoDownscale": True, "twoPass": True},
    }


# ---------------------------------------------------------------- preview
class Preview(QWidget):
    text_moved = Signal(int, float, float)
    text_selected = Signal(int)
    crop_changed = Signal(dict)
    edit_begin = Signal()

    def __init__(self):
        super().__init__()
        self.img = QImage()
        self.p = None
        self.time = 0.0
        self.total = 0.0
        self.crop_mode = False
        self.sel_text = None
        self.drag = None
        self.text_rects = []
        self.frame_rect = QRectF()
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.setStyleSheet("background: #07080a;")

    def set_state(self, project, t, total, crop_mode, sel_text):
        self.p, self.time, self.total, self.crop_mode, self.sel_text = project, t, total, crop_mode, sel_text
        self.update()

    def set_frame(self, img):
        self.img = img
        self.update()

    def geometry_(self):
        p = self.p
        iw, ih = (self.img.width(), self.img.height()) if not self.img.isNull() else (1920, 1080)
        crop = p["crop"] if (p.get("crop") and not self.crop_mode) else {"x": 0, "y": 0, "w": 1, "h": 1}
        pw, ph = iw * crop["w"], ih * crop["h"]
        rot = 0 if self.crop_mode else p.get("rotate", 0)
        rw, rh = (ph, pw) if rot in (90, 270) else (pw, ph)
        ar = rw / rh if (self.crop_mode or not parse_aspect(p.get("aspect"))) else parse_aspect(p["aspect"])
        W, H = self.width() - 32, self.height() - 32
        FW = W
        FH = FW / ar
        if FH > H:
            FH = H
            FW = FH * ar
        fr = QRectF((self.width() - FW) / 2, (self.height() - FH) / 2, FW, FH)
        fit = "crop" if (self.crop_mode or not parse_aspect(p.get("aspect"))) else p.get("fit", "crop")
        s = max(FW / rw, FH / rh) if fit == "crop" else min(FW / rw, FH / rh)
        return fr, crop, (iw, ih), (pw, ph), rot, s, fit

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#07080a"))
        if not self.p:
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        fr, crop, (iw, ih), (pw, ph), rot, s, fit = self.geometry_()
        self.frame_rect = fr
        p.save()
        p.setClipRect(fr)
        p.fillRect(fr, Qt.black)
        if not self.img.isNull():
            if fit == "blur":
                small = self.img.scaled(32, 18, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                p.setOpacity(0.75)
                p.drawImage(fr, small)
                p.setOpacity(1)
            p.save()
            p.translate(fr.center())
            if not self.crop_mode:
                p.scale(-1 if self.p.get("flipH") else 1, -1 if self.p.get("flipV") else 1)
                p.rotate(rot)
            w, h = pw * s, ph * s
            src = QRectF(crop["x"] * iw, crop["y"] * ih, pw, ph)
            p.drawImage(QRectF(-w / 2, -h / 2, w, h), self.img, src)
            p.restore()
        col = self.p.get("color") or {}
        if not self.crop_mode and col.get("vignette", 0) > 0:
            g = QRadialGradient(fr.center(), max(fr.width(), fr.height()) * 0.72)
            g.setColorAt(0.45, QColor(0, 0, 0, 0))
            g.setColorAt(1, QColor(0, 0, 0, int(255 * min(1, col["vignette"] * 0.8))))
            p.fillRect(fr, g)
        self.text_rects = []
        if not self.crop_mode:
            for i, t in enumerate(self.p.get("texts") or []):
                if not (t["start"] <= self.time <= t["end"] or i == self.sel_text):
                    self.text_rects.append(None)
                    continue
                size = t.get("size", 64) / 1080 * fr.height()
                f = QFont("Arial", 10)
                f.setBold(True)
                f.setPixelSize(max(4, int(size)))
                fm = QFontMetricsF(f)
                lines = (t.get("text") or " ").split("\n")
                tw = max(fm.horizontalAdvance(x) for x in lines)
                th = fm.height() * len(lines)
                pad = size / 4 if t.get("box") else 0
                x = fr.x() + (fr.width() - tw) * t.get("x", 0.5)
                y = fr.y() + (fr.height() - th) * t.get("y", 0.9)
                if t.get("box"):
                    bc = QColor(t.get("boxColor", "#000000"))
                    bc.setAlphaF(t.get("boxOpacity", 0.5))
                    p.fillRect(QRectF(x - pad, y - pad, tw + 2 * pad, th + 2 * pad), bc)
                path = QPainterPath()
                for k, line in enumerate(lines):
                    path.addText(QPointF(x, y + fm.ascent() + k * fm.height()), f, line)
                if t.get("outline"):
                    p.strokePath(path, QPen(QColor("black"), max(1.0, size / 9), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                p.fillPath(path, QColor(t.get("color", "#ffffff")))
                r = QRectF(x - pad, y - pad, tw + 2 * pad, th + 2 * pad)
                self.text_rects.append(r)
                if i == self.sel_text:
                    p.setPen(QPen(QColor(C["accent"]), 1, Qt.DashLine))
                    p.setBrush(Qt.NoBrush)
                    p.drawRect(r.adjusted(-4, -4, 4, 4))
        fi, fo = self.p.get("fadeIn", 0), self.p.get("fadeOut", 0)
        op = 0.0
        if not self.crop_mode:
            if fi > 0 and self.time < fi:
                op = 1 - self.time / fi
            elif fo > 0 and self.time > self.total - fo:
                op = (self.time - (self.total - fo)) / fo
        if op > 0:
            p.fillRect(fr, QColor(0, 0, 0, int(255 * clamp(op, 0, 1))))
        p.restore()
        if self.crop_mode:
            c = self.p.get("crop") or {"x": 0, "y": 0, "w": 1, "h": 1}
            r = QRectF(fr.x() + c["x"] * fr.width(), fr.y() + c["y"] * fr.height(), c["w"] * fr.width(), c["h"] * fr.height())
            shade = QPainterPath()
            shade.addRect(fr)
            inner = QPainterPath()
            inner.addRect(r)
            p.fillPath(shade.subtracted(inner), QColor(0, 0, 0, 140))
            p.setPen(QPen(QColor(C["accent"]), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            p.setBrush(QColor(C["accent"]))
            for pt in (r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()):
                p.drawRect(QRectF(pt.x() - 6, pt.y() - 6, 12, 12))
            self.crop_rect = r

    def _lock_ar(self):
        a = self.p.get("aspect")
        return parse_aspect(a) if a != "source" and self.p.get("fit") == "crop" else None

    def mousePressEvent(self, e):
        pos = e.position()
        if self.crop_mode and hasattr(self, "crop_rect"):
            r = self.crop_rect
            mode = None
            for name, pt in (("tl", r.topLeft()), ("tr", r.topRight()), ("bl", r.bottomLeft()), ("br", r.bottomRight())):
                if abs(pos.x() - pt.x()) < 10 and abs(pos.y() - pt.y()) < 10:
                    mode = name
            if not mode and r.contains(pos):
                mode = "move"
            if mode:
                self.edit_begin.emit()
                self.drag = ("crop", mode, pos, dict(self.p.get("crop") or {"x": 0, "y": 0, "w": 1, "h": 1}))
            return
        for i, r in enumerate(self.text_rects):
            if r is not None and r.contains(pos):
                self.edit_begin.emit()
                self.text_selected.emit(i)
                t = self.p["texts"][i]
                self.drag = ("text", i, pos, (t.get("x", 0.5), t.get("y", 0.9), r.width(), r.height()))
                return
        self.text_selected.emit(-1)

    def mouseMoveEvent(self, e):
        if not self.drag:
            return
        fr = self.frame_rect
        pos = e.position()
        if self.drag[0] == "text":
            _, i, p0, (tx, ty, w, h) = self.drag
            nx = clamp(tx + (pos.x() - p0.x()) / max(1, fr.width() - w), 0, 1)
            ny = clamp(ty + (pos.y() - p0.y()) / max(1, fr.height() - h), 0, 1)
            self.text_moved.emit(i, nx, ny)
        else:
            _, mode, p0, c0 = self.drag
            dx, dy = (pos.x() - p0.x()) / fr.width(), (pos.y() - p0.y()) / fr.height()
            x, y, w, h = c0["x"], c0["y"], c0["w"], c0["h"]
            if mode == "move":
                x, y = clamp(x + dx, 0, 1 - w), clamp(y + dy, 0, 1 - h)
            else:
                if "l" in mode:
                    nx = clamp(x + dx, 0, x + w - 0.05)
                    w, x = w + x - nx, nx
                if "r" in mode:
                    w = clamp(w + dx, 0.05, 1 - x)
                if "t" in mode:
                    ny = clamp(y + dy, 0, y + h - 0.05)
                    h, y = h + y - ny, ny
                if "b" in mode:
                    h = clamp(h + dy, 0.05, 1 - y)
                ar = self._lock_ar()
                if ar:
                    h = clamp(w * fr.width() / ar / fr.height(), 0.05, 1 - y)
                    w = h * fr.height() * ar / fr.width()
            self.crop_changed.emit({"x": x, "y": y, "w": w, "h": h})

    def mouseReleaseEvent(self, e):
        self.drag = None


# ---------------------------------------------------------------- page
class EditorPage(Page):
    def __init__(self, win):
        super().__init__(win, "")
        self.project = None
        self.clips = []
        self.past, self.future = [], []
        self.sel_seg, self.sel_text, self.crop_mode = 0, None, False
        self.time = 0.0
        self.cur = 0
        self.playing = False
        self.last_frame_ms = 0
        self.aux = []          # per-track audio players
        self.music = None
        self.film = {}
        b = self.backend
        self.top.insertWidget(0, btn("Back", "back", self.back, ghost=True, sm=True))
        self.top.addStretch(1)
        self.undo_b = btn("", "undo", self.undo, ghost=True, sm=True, tip="Undo (Ctrl+Z)")
        self.redo_b = btn("", "redo", self.redo, ghost=True, sm=True, tip="Redo (Ctrl+Y)")
        self.top.addWidget(self.undo_b)
        self.top.addWidget(self.redo_b)
        self.top.addWidget(btn("Reset", None, self.reset, sm=True))
        self.top.addWidget(btn("Export", "export", lambda: self.tabs.setCurrentIndex(5), primary=True, sm=True))

        body = QHBoxLayout()
        body.setSpacing(0)
        left = QVBoxLayout()
        left.setSpacing(0)
        self.preview = Preview()
        left.addWidget(self.preview, 1)
        ctr = QWidget()
        ctr.setStyleSheet(f"background: {C['bg2']}; border-top: 1px solid {C['line']};")
        cl = QHBoxLayout(ctr)
        cl.setContentsMargins(12, 6, 12, 6)
        self.play_b = btn("", "play", self.toggle_play, sm=True, tip="Play/Pause (Space)")
        cl.addWidget(self.play_b)
        self.time_lb = QLabel()
        self.time_lb.setStyleSheet(f"font-family: 'Cascadia Mono', Consolas; color: {C['text2']}; font-size: 12.5px;")
        self.time_lb.setFixedWidth(190)
        cl.addWidget(self.time_lb)
        cl.addWidget(btn("Split", "scissors", self.split, sm=True, tip="Split at playhead (S)"))
        cl.addWidget(btn("Set in", None, self.set_in, sm=True, tip="Trim segment start to playhead (I)"))
        cl.addWidget(btn("Set out", None, self.set_out, sm=True, tip="Trim segment end to playhead (O)"))
        cl.addStretch(1)
        self.crop_badge = label("Cropping — drag the box", "dim")
        self.crop_badge.hide()
        cl.addWidget(self.crop_badge)
        vic = QLabel()
        vic.setPixmap(icons.pixmap("volume", C["text2"], 15))
        cl.addWidget(vic)
        self.vol = FSlider(0, 1, 0.05, 1.0, on_change=lambda v: self.apply_volumes())
        self.vol.setFixedWidth(100)
        cl.addWidget(self.vol)
        left.addWidget(ctr)
        tlw = QWidget()
        tlw.setStyleSheet(f"background: {C['bg2']}; border-top: 1px solid {C['line']};")
        tll = QVBoxLayout(tlw)
        tll.setContentsMargins(14, 8, 14, 10)
        tools = QHBoxLayout()
        self.tl_info = label("", "dim")
        tools.addWidget(self.tl_info)
        tools.addStretch(1)
        tools.addWidget(btn("Split", "scissors", self.split, ghost=True, sm=True))
        tools.addWidget(btn("Delete", "trash", self.delete_seg, ghost=True, sm=True))
        tools.addWidget(btn("", "zoomOut", lambda: self.set_zoom(self.timeline.zoom / 1.5), ghost=True, sm=True))
        self.zoom_s = FSlider(1, 20, 0.1, 1, on_change=self.set_zoom)
        self.zoom_s.setFixedWidth(100)
        tools.addWidget(self.zoom_s)
        tools.addWidget(btn("", "zoomIn", lambda: self.set_zoom(self.timeline.zoom * 1.5), ghost=True, sm=True))
        tll.addLayout(tools)
        self.tl_scroll = QScrollArea()
        self.tl_scroll.setWidgetResizable(False)
        self.tl_scroll.setFixedHeight(130)
        self.tl_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.timeline = Timeline()
        self.tl_scroll.setWidget(self.timeline)
        tll.addWidget(self.tl_scroll)
        left.addWidget(tlw)
        lw = QWidget()
        lw.setLayout(left)
        body.addWidget(lw, 1)

        self.tabs = QTabWidget()
        self.tabs.setFixedWidth(400)
        self.tabs.setUsesScrollButtons(False)
        self.tabs.currentChanged.connect(lambda i: self.set_crop_mode(False) if i != 1 and self.crop_mode else None)
        self.tabs.setStyleSheet(f"QTabWidget {{ background: {C['bg2']}; border-left: 1px solid {C['line']}; }} QTabBar::tab {{ padding: 8px 5px; font-size: 12px; }}")
        body.addWidget(self.tabs)
        self.root.addLayout(body, 1)

        self.player = Player(self)
        self.sink = QVideoSink(self)
        self.player.mp.setVideoOutput(self.sink)
        self.sink.videoFrameChanged.connect(self.on_frame)
        self.tick_timer = QTimer(self, interval=16, timeout=self.tick)
        self.save_timer = QTimer(self, singleShot=True, interval=800, timeout=self.save_draft)
        self.est_timer = QTimer(self, singleShot=True, interval=400, timeout=self.update_estimate)

        tl = self.timeline
        tl.seek.connect(self.seek)
        tl.select_segment.connect(lambda i: (setattr(self, "sel_seg", i), self.refresh_panels()))
        tl.select_text.connect(self.select_text)
        tl.edit_begin.connect(self.snapshot)
        tl.segment_trim.connect(lambda i, a, b_: self.live(lambda p: p["segments"][i].update(start=a, end=b_)))
        pv = self.preview
        pv.edit_begin.connect(self.snapshot)
        pv.text_selected.connect(lambda i: self.select_text(i if i >= 0 else None))
        pv.text_moved.connect(lambda i, x, y: self.live(lambda p: p["texts"][i].update(x=x, y=y)))
        pv.crop_changed.connect(lambda c: self.live(lambda p: p.update(crop=c)))
        for key, fn in (("Space", self.toggle_play), ("S", self.split), ("Delete", self.delete_key), ("Backspace", self.delete_key), ("I", self.set_in),
                        ("O", self.set_out), ("Left", lambda: self.step_frames(-1)), ("Right", lambda: self.step_frames(1)),
                        ("Shift+Left", lambda: self.seek(self.time - 5)), ("Shift+Right", lambda: self.seek(self.time + 5)),
                        ("Home", lambda: self.seek(0)), ("End", lambda: self.seek(self.total())), ("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo),
                        ("Ctrl+Shift+Z", self.redo)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    # ---------------------------------------------------------------- lifecycle
    def on_show(self, id=None, start=None, end=None, seg=None, ids=None, **_):
        lib = self.backend.library
        if id == "montage":
            idlist = list(dict.fromkeys(x.split(":")[0] for x in seg.split(","))) if seg else (ids or "").split(",")
            clips = [c for c in (lib.get(x) for x in idlist if x) if c]
        else:
            c = lib.get(id) if id else None
            clips = [c] if c else self.clips
        if not clips:
            return self.go("library")
        self.clips = clips
        self.parent_id = clips[0]["id"] if len(clips) == 1 else None
        self.title.setText(f"Montage · {len(clips)} clips" if len(clips) > 1 else clips[0]["title"])
        key = seg or ",".join(c["id"] for c in clips)
        self.draft_file = os.path.join(paths.USER_DATA, "drafts", hashlib.md5(key.encode()).hexdigest() + ".json")
        p = None
        if start is None and not seg:
            try:
                with open(self.draft_file, encoding="utf-8") as f:
                    p = json.load(f)
                if p.get("sources") != [c["path"] for c in clips]:
                    p = None
            except Exception:  # noqa: BLE001
                p = None
        self.project = p or default_project(clips, start, end, seg)
        self.past, self.future = [], []
        self.sel_seg, self.sel_text, self.crop_mode, self.time, self.cur = 0, None, False, 0.0, 0
        self.loaded_src = None
        self.export_title = None
        self.raw_img = None
        self.build_panels()
        self.timeline.set_data(self.project, [c["duration"] for c in clips], self.film)
        self.timeline.marks = []
        self.load_segment(0, self.project["segments"][0]["start"], play=False)
        self.prepare_aux()
        for c in clips:
            run_bg(lambda c=c: self.make_filmstrip(c["path"], c["duration"]), lambda r: self.on_film(r))
        self.tick_timer.start()
        self.refresh()

    def on_hide(self):
        self.tick_timer.stop()
        self.pause()
        self.player.release()
        for pl in self.aux:
            pl[0].stop()
            pl[0].setSource(QUrl())
        self.aux = []
        if self.music:
            self.music[0].stop()
            self.music = None
        self.loaded_src = None
        self.save_draft()

    def back(self):
        self.go("library") if len(self.clips) > 1 else self.go("clip", id=self.clips[0]["id"])

    # ---------------------------------------------------------------- history
    def snapshot(self):
        self.past.append(json.dumps(self.project))
        self.past = self.past[-200:]
        self.future = []

    def commit(self, fn):
        self.snapshot()
        fn(self.project)
        self.changed(rebuild=True)

    def live(self, fn):
        fn(self.project)
        self.changed(rebuild=False)

    def undo(self):
        if self.past:
            self.future.append(json.dumps(self.project))
            self.project = json.loads(self.past.pop())
            self.changed(rebuild=True)

    def redo(self):
        if self.future:
            self.past.append(json.dumps(self.project))
            self.project = json.loads(self.future.pop())
            self.changed(rebuild=True)

    def reset(self):
        self.commit(lambda p: p.update(default_project(self.clips)))
        self.sel_text = None
        self.seek(0)

    def changed(self, rebuild):
        segs = self.project["segments"]
        self.sel_seg = min(self.sel_seg, len(segs) - 1)
        if self.sel_text is not None and self.sel_text >= len(self.project["texts"]):
            self.sel_text = None
        self.timeline.project = self.project
        if self.time > self.total():
            self.seek(self.total())
        if rebuild:
            self.refresh_panels()
        if not self.playing:
            self.show_frame()
        self.refresh()
        self.apply_volumes()
        self.save_timer.start()
        self.est_timer.start()

    def save_draft(self):
        if self.project:
            os.makedirs(os.path.dirname(self.draft_file), exist_ok=True)
            with open(self.draft_file, "w", encoding="utf-8") as f:
                json.dump(self.project, f)

    # ---------------------------------------------------------------- playback
    def total(self):
        o = offsets(self.project["segments"])
        return o[-1][1] if o else 0.0

    def load_segment(self, i, src_t, play):
        self.cur = i
        seg = self.project["segments"][i]
        path = self.project["sources"][seg["src"]]
        if self.loaded_src != path:
            self.loaded_src = path
            self.player.load(path, src_t, autoplay=play)
        else:
            self.player.seek(src_t)
            if play:
                self.player.mp.play()
        self.player.mp.setPlaybackRate(seg.get("speed") or 1)

    def seek(self, t):
        if not self.project:
            return
        t = clamp(t, 0, self.total())
        segs = self.project["segments"]
        i, st = locate(segs, offsets(segs), t)
        self.time = t
        self.load_segment(i, st, play=self.playing)
        self.sync_aux(force=True)
        self.refresh()

    def step_frames(self, n):
        fps = self.clips[0].get("fps") or 60
        self.seek(self.time + n / fps)

    def toggle_play(self):
        self.pause() if self.playing else self.play()

    def play(self):
        if self.time >= self.total() - 0.05:
            self.seek(0)
        self.playing = True
        self.player.mp.setPlaybackRate(self.project["segments"][self.cur].get("speed") or 1)
        self.player.mp.play()
        self.play_b.setIcon(icons.icon("pause", C["text"], 16, fill="currentColor"))
        self.sync_aux(force=True)

    def pause(self):
        self.playing = False
        self.player.mp.pause()
        for pl in self.aux:
            pl[0].pause()
        if self.music:
            self.music[0].pause()
        self.play_b.setIcon(icons.icon("play", C["text"], 16, fill="currentColor"))

    def tick(self):
        if not self.project or not self.playing:
            return
        segs = self.project["segments"]
        offs = offsets(segs)
        seg = segs[self.cur]
        pos = self.player.pos()
        if pos >= seg["end"] - 0.03 or self.player.mp.mediaStatus() == QMediaPlayer.EndOfMedia:
            nxt = self.cur + 1
            if nxt >= len(segs):
                self.pause()
                self.time = self.total()
                self.refresh()
                return
            self.load_segment(nxt, segs[nxt]["start"], play=True)
            self.sync_aux(force=True)
            return
        if pos < seg["start"] - 0.25:
            self.player.seek(seg["start"])
            return
        self.time = offs[self.cur][0] + (pos - seg["start"]) / (seg.get("speed") or 1)
        self.sync_aux()
        self.refresh(light=True)

    def on_frame(self, frame):
        # convert at most ~30 fps while playing; always when paused (seek preview)
        now = int(_time.time() * 1000)
        if self.playing and now - self.last_frame_ms < 33:
            return
        self.last_frame_ms = now
        if not frame.isValid():
            return
        img = frame.toImage()
        if img.isNull():
            return
        target = self.preview.size() * self.devicePixelRatioF()
        if img.width() > target.width() * 1.5:
            img = img.scaled(int(target.width() * 1.2), int(target.width() * 1.2 * img.height() / img.width()), Qt.KeepAspectRatio, Qt.FastTransformation)
        self.raw_img = img
        self.show_frame()

    def show_frame(self):
        img = getattr(self, "raw_img", None)
        if img is None:
            return
        if self.project and not self.crop_mode:
            img = apply_color(img, self.project.get("color") or {})
        self.preview.set_frame(img)

    # ---------------------------------------------------------------- per-track audio preview
    def prepare_aux(self):
        c = self.clips[0]
        if len(self.clips) == 1 and len(c.get("audioTracks") or []) > 1:
            run_bg(lambda: self.extract_tracks(c["path"]), self.on_tracks)
        self.setup_music()

    @staticmethod
    def extract_tracks(path):
        info = ffmpeg.probe(path)
        st = os.stat(path)
        key = hashlib.sha1(f"{path}|{st.st_mtime}|{st.st_size}".encode()).hexdigest()[:16]
        d = os.path.join(paths.USER_DATA, "cache", "tracks", key)
        os.makedirs(d, exist_ok=True)
        outs = [os.path.join(d, f"track{i}.m4a") for i in range(len(info["audioTracks"]))]
        if not all(os.path.exists(o) for o in outs):
            args = ["-i", path]
            for i, t in enumerate(info["audioTracks"]):
                args += ["-map", f"0:a:{i}", "-c", "copy" if t["codec"] == "aac" else "aac", "-vn", outs[i]]
            ffmpeg.run(args, low_priority=True)
        return outs

    def on_tracks(self, files):
        if not self.project or len(files) < 2:
            return
        self.aux = []
        for f in files:
            mp, ao = QMediaPlayer(self), QAudioOutput(self)
            mp.setAudioOutput(ao)
            mp.setSource(QUrl.fromLocalFile(f))
            self.aux.append((mp, ao))
        self.player.ao.setMuted(True)
        self.apply_volumes()
        self.sync_aux(force=True)

    def setup_music(self):
        m = (self.project.get("audio") or {}).get("music")
        if self.music:
            self.music[0].stop()
            self.music = None
        if m and m.get("path"):
            mp, ao = QMediaPlayer(self), QAudioOutput(self)
            mp.setAudioOutput(ao)
            mp.setSource(QUrl.fromLocalFile(m["path"]))
            mp.setLoops(QMediaPlayer.Infinite if m.get("loop") else 1)
            self.music = (mp, ao)
        self.apply_volumes()

    def apply_volumes(self):
        if not self.project:
            return
        master = self.vol.value()
        tracks = self.project["audio"]["tracks"]
        self.player.ao.setVolume(master)
        for i, (mp, ao) in enumerate(self.aux):
            t = tracks[i] if i < len(tracks) else None
            ao.setVolume(0 if (not t or t.get("muted")) else min(1.0, t.get("volume", 1) * master))
        if not self.aux and len(self.clips) == 1 and tracks:
            # single-track preview: the mix is the only thing we can play
            t = tracks[0]
            self.player.ao.setVolume(0 if t.get("muted") and len(tracks) == 1 else min(1.0, t.get("volume", 1) * master))
        m = self.project["audio"].get("music")
        if self.music and m:
            self.music[1].setVolume(min(1.0, m.get("volume", 0.5) * master))

    def sync_aux(self, force=False):
        pos = self.player.pos()
        rate = self.player.mp.playbackRate()
        for mp, _ in self.aux:
            mp.setPlaybackRate(rate)
            if force or abs(mp.position() / 1000 - pos) > 0.12:
                mp.setPosition(int(pos * 1000))
            if self.playing and mp.playbackState() != QMediaPlayer.PlayingState:
                mp.play()
            if not self.playing and mp.playbackState() == QMediaPlayer.PlayingState:
                mp.pause()
        m = self.project["audio"].get("music") if self.project else None
        if self.music and m:
            mp = self.music[0]
            mt = self.time - m.get("start", 0)
            if mt < 0 or not self.playing:
                mp.pause()
            else:
                if force or abs(mp.position() / 1000 - mt) > 0.25:
                    mp.setPosition(int(mt * 1000))
                if mp.playbackState() != QMediaPlayer.PlayingState:
                    mp.play()

    # ---------------------------------------------------------------- filmstrip
    @staticmethod
    def make_filmstrip(path, duration):
        st = os.stat(path)
        key = hashlib.sha1(f"{path}|{st.st_mtime}|{st.st_size}".encode()).hexdigest()[:16]
        d = os.path.join(paths.USER_DATA, "cache", "film", key)
        done = os.path.join(d, ".done")
        if not os.path.exists(done):
            os.makedirs(d, exist_ok=True)
            count = max(4, min(80, round((duration or 60) / 2)))
            iv = max(0.5, (duration or 60) / count)
            ffmpeg.run(["-skip_frame", "nokey", "-i", path, "-an", "-vf", f"fps=1/{iv:.3f},scale=160:-2", "-fps_mode", "vfr", "-q:v", "7",
                        os.path.join(d, "f_%04d.jpg")], low_priority=True, threads=2)
            open(done, "w").close()
        return path, sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jpg"))

    def on_film(self, r):
        path, files = r
        self.film[path] = files
        self.timeline.film = self.film
        self.timeline.update()

    # ---------------------------------------------------------------- edit ops
    def split(self):
        segs = self.project["segments"]
        i, st = locate(segs, offsets(segs), self.time)
        s = segs[i]
        if st - s["start"] < 0.1 or s["end"] - st < 0.1:
            return
        self.commit(lambda p: p["segments"].__setitem__(slice(i, i + 1), [dict(s, end=st), dict(s, start=st)]))
        self.sel_seg = i + 1
        self.refresh_panels()

    def delete_key(self):
        if self.sel_text is not None:
            i = self.sel_text
            self.sel_text = None
            self.commit(lambda p: p["texts"].pop(i))
        else:
            self.delete_seg()

    def delete_seg(self, i=None):
        i = self.sel_seg if i is None else i
        if len(self.project["segments"]) <= 1:
            return self.toast("Can't delete the only segment", "Trim it instead", "err")
        self.commit(lambda p: p["segments"].pop(i))

    def dup_seg(self):
        i = self.sel_seg
        self.commit(lambda p: p["segments"].insert(i + 1, dict(p["segments"][i])))

    def move_seg(self, d):
        i, j = self.sel_seg, self.sel_seg + d
        if 0 <= j < len(self.project["segments"]):
            def f(p):
                p["segments"][i], p["segments"][j] = p["segments"][j], p["segments"][i]
            self.commit(f)
            self.sel_seg = j
            self.refresh_panels()

    def set_seg(self, i, **kw):
        self.commit(lambda p: p["segments"][i].update(**kw))

    def set_in(self):
        segs = self.project["segments"]
        i, st = locate(segs, offsets(segs), self.time)
        if st < segs[i]["end"] - 0.1:
            self.set_seg(i, start=st)

    def set_out(self):
        segs = self.project["segments"]
        i, st = locate(segs, offsets(segs), self.time)
        if st > segs[i]["start"] + 0.1:
            self.set_seg(i, end=st)

    def select_text(self, i):
        self.sel_text = i
        if i is not None:
            self.tabs.setCurrentIndex(3)
        self.refresh_panels()
        self.refresh()

    def set_zoom(self, z):
        z = clamp(z, 1, 20)
        self.timeline.zoom = z
        self.zoom_s.set_value(z)
        self.layout_timeline()

    def layout_timeline(self):
        w = int(max(200, (self.tl_scroll.viewport().width() - 2) * self.timeline.zoom))
        self.timeline.setFixedSize(w, 124)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self.layout_timeline)

    # ---------------------------------------------------------------- refresh
    def refresh(self, light=False):
        if not self.project:
            return
        tot = self.total()
        self.time_lb.setText(f"{fmt_time(self.time)} / {fmt_time(tot)}")
        tl = self.timeline
        tl.time, tl.sel_seg, tl.sel_text = self.time, self.sel_seg, self.sel_text
        if not light:
            segs = self.project["segments"]
            offs = offsets(segs)
            marks = []
            if len(self.clips) == 1:
                for bm in self.backend.library.get(self.clips[0]["id"]).get("bookmarks") or []:
                    for k, s in enumerate(segs):
                        if s["start"] <= bm["time"] <= s["end"]:
                            marks.append((offs[k][0] + (bm["time"] - s["start"]) / (s.get("speed") or 1), bm["label"]))
            tl.marks = marks
            self.tl_info.setText(f"{len(segs)} segment{'s' if len(segs) > 1 else ''} · {fmt_dur(tot)}")
            self.undo_b.setEnabled(bool(self.past))
            self.redo_b.setEnabled(bool(self.future))
            self.layout_timeline()
        tl.update()
        self.preview.set_state(self.project, self.time, tot, self.crop_mode, self.sel_text)

    # ---------------------------------------------------------------- panels
    def build_panels(self):
        self.tabs.clear()
        self.panels = {}
        for name, ic in (("Trim", "scissors"), ("Frame", "crop"), ("Look", "palette"), ("Text", "text"), ("Audio", "volume"), ("Export", "export")):
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(16, 14, 16, 16)
            v.setSpacing(12)
            sa.setWidget(w)
            self.tabs.addTab(sa, name)
            self.panels[name] = v
        self.refresh_panels()

    def _clear(self, v):
        while v.count():
            it = v.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._clear(it.layout())

    def field(self, v, title, widget, value_text=None):
        h = QHBoxLayout()
        t = QLabel(title)
        t.setStyleSheet(f"color: {C['text2']}; font-weight: 650; font-size: 12px;")
        h.addWidget(t)
        h.addStretch(1)
        if value_text is not None:
            vl = label(value_text, "dim")
            h.addWidget(vl)
        v.addLayout(h)
        v.addWidget(widget)

    def refresh_panels(self):
        if not self.project or not hasattr(self, "panels"):
            return
        cur_tab = self.tabs.currentIndex()
        for v in self.panels.values():
            self._clear(v)
        self.panel_trim(self.panels["Trim"])
        self.panel_frame(self.panels["Frame"])
        self.panel_look(self.panels["Look"])
        self.panel_text(self.panels["Text"])
        self.panel_audio(self.panels["Audio"])
        self.panel_export(self.panels["Export"])
        for v in self.panels.values():
            v.addStretch(1)
        self.tabs.setCurrentIndex(max(0, cur_tab))

    def panel_trim(self, v):
        segs = self.project["segments"]
        i = self.sel_seg
        s = segs[i]
        multi = len(self.clips) > 1
        v.addWidget(label(f"SEGMENT {i + 1} OF {len(segs)}", "h3"))
        st, en = QDoubleSpinBox(), QDoubleSpinBox()
        for sp in (st, en):
            sp.setMaximumWidth(110)
            sp.setDecimals(2)
            sp.setSingleStep(0.1)
            sp.setRange(0, self.clips[s["src"]]["duration"])
        st.setValue(s["start"])
        en.setValue(s["end"])
        st.editingFinished.connect(lambda: self.set_seg(i, start=clamp(st.value(), 0, s["end"] - 0.1)))
        en.editingFinished.connect(lambda: self.set_seg(i, end=clamp(en.value(), s["start"] + 0.1, self.clips[s["src"]]["duration"])))
        v.addWidget(hbox(label("Start", "dim"), st, label("End", "dim"), en))
        v.addWidget(label(f"Source: {self.clips[s['src']]['title'] if multi else 'original clip'} · {fmt_dur(s['end'] - s['start'])} → {fmt_time(seg_dur(s))} on timeline", "dim", wrap=True))
        sp_btns = []
        for x in SPEEDS:
            b_ = btn(f"{x}x", None, lambda x=x: self.set_seg(i, speed=x), sm=True, primary=s.get("speed", 1) == x)
            sp_btns.append(b_)
        self.field(v, "Speed", flow(*sp_btns))
        v.addWidget(label("Tip: split around a highlight and set it to 0.5x or 0.25x for slow motion.", "dim", wrap=True))
        v.addWidget(flow(btn("Split", "scissors", self.split, sm=True), btn("Duplicate", "copy", self.dup_seg, sm=True),
                         btn("← Move", None, lambda: self.move_seg(-1), sm=True), btn("Move →", None, lambda: self.move_seg(1), sm=True),
                         btn("Delete", "trash", self.delete_seg, sm=True, danger=True)))
        v.addWidget(label("QUICK TRIM", "h3"))
        dur0 = self.clips[0]["duration"]
        v.addWidget(flow(*[btn(f"Last {n}s", None, lambda n=n: self.commit(lambda p: p.update(segments=[{"src": 0, "start": max(0.0, dur0 - n), "end": dur0, "speed": 1}])), sm=True)
                           for n in (5, 10, 15, 30, 60)]))
        v.addWidget(label("Space play · S split · I/O set in/out · Del delete segment · ←/→ frame step (Shift 5s) · Ctrl+Z undo", "dim", wrap=True))

    def set_crop_mode(self, on):
        if on and not self.project.get("crop"):
            self.commit(lambda p: p.update(crop={"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8}))
        self.crop_mode = on
        self.crop_badge.setVisible(on)
        self.show_frame()
        self.refresh_panels()
        self.refresh()

    def panel_frame(self, v):
        p = self.project
        multi = len(self.clips) > 1
        c0 = self.clips[0]
        self.field(v, "Aspect ratio", Combo(ASPECTS, p["aspect"], on_change=lambda a: self.commit(lambda q: q.update(aspect=a))))
        if p["aspect"] != "source":
            self.field(v, "Fit", Seg([("crop", "Fill (crop)"), ("blur", "Blur bars"), ("pad", "Black bars")], p["fit"], on_change=lambda f: self.commit(lambda q: q.update(fit=f))))
        crop_txt = f"{round(p['crop']['w'] * c0['width'])}×{round(p['crop']['h'] * c0['height'])}" if p.get("crop") else ""
        cb = btn("Done cropping" if self.crop_mode else "Edit crop", "crop", lambda: self.set_crop_mode(not self.crop_mode), sm=True, primary=self.crop_mode)
        cb.setEnabled(not multi)
        row = [cb]
        if p.get("crop"):
            row.append(btn("Remove crop", None, lambda: (self.commit(lambda q: q.update(crop=None)), self.set_crop_mode(False)), sm=True))
        self.field(v, "Crop", hbox(*row, "stretch"), crop_txt)
        presets = []
        for name, a in (("Center 9:16", 9 / 16), ("Center 1:1", 1.0), ("Center 4:5", 0.8)):
            w = min(1.0, a * c0["height"] / max(1, c0["width"]))
            presets.append(btn(name, None, lambda w=w: self.commit(lambda q: q.update(crop={"x": (1 - w) / 2, "y": 0.0, "w": w, "h": 1.0})), sm=True, ghost=True))
        for name, z in (("Zoom 1.25x", 0.8), ("Zoom 1.5x", 0.6667), ("Zoom 2x", 0.5)):
            presets.append(btn(name, None, lambda z=z: self.commit(lambda q: q.update(crop={"x": (1 - z) / 2, "y": (1 - z) / 2, "w": z, "h": z})), sm=True, ghost=True))
        fw = flow(*presets)
        fw.setEnabled(not multi)
        v.addWidget(fw)
        self.field(v, "Rotate", Seg([(0, "0°"), (90, "90°"), (180, "180°"), (270, "270°")], p["rotate"], on_change=lambda r: self.commit(lambda q: q.update(rotate=r))))
        v.addWidget(hbox(Toggle(p["flipH"], lambda x: self.commit(lambda q: q.update(flipH=x))), label("Flip horizontal"), 20,
                         Toggle(p["flipV"], lambda x: self.commit(lambda q: q.update(flipV=x))), label("Flip vertical"), "stretch"))

    def panel_look(self, v):
        p = self.project
        v.addWidget(label("LOOKS", "h3"))
        v.addWidget(flow(*[btn(n, None, lambda c=c: self.commit(lambda q: q.update(color={**DEFAULT_COLOR, **c})), sm=True) for n, c in LOOKS]))
        for k, title, mn, mx, st in (("brightness", "Brightness", -0.3, 0.3, 0.01), ("contrast", "Contrast", 0.5, 1.8, 0.01), ("saturation", "Saturation", 0.0, 2.5, 0.01),
                                     ("gamma", "Gamma", 0.5, 2.0, 0.01), ("hue", "Hue shift", -60, 60, 1.0), ("sharpen", "Sharpen (export only)", 0.0, 1.5, 0.05),
                                     ("vignette", "Vignette", 0.0, 1.0, 0.05)):
            s = FSlider(mn, mx, st, p["color"].get(k, DEFAULT_COLOR[k]), fmt=lambda x: f"{x:.2f}")
            s.pressed.connect(self.snapshot)
            s.changed.connect(lambda x, k=k: self.live(lambda q: q["color"].__setitem__(k, x)))
            self.field(v, title, s)
        v.addWidget(label("TRANSITIONS", "h3"))
        for k, title in (("fadeIn", "Fade in"), ("fadeOut", "Fade out")):
            s = FSlider(0, 3, 0.1, p[k], fmt=lambda x: f"{x:.1f}s")
            s.pressed.connect(self.snapshot)
            s.changed.connect(lambda x, k=k: self.live(lambda q: q.__setitem__(k, x)))
            self.field(v, title, s)

    def force_frame(self):
        # re-run the colour preview on the current frame when paused
        if not self.playing:
            self.player.seek(self.player.pos())

    def panel_text(self, v):
        p = self.project
        tot = self.total()

        def add(**preset):
            base = {"text": "Your text", "start": self.time, "end": min(tot, self.time + 3), "x": 0.5, "y": 0.85, "size": 72, "color": "#ffffff",
                    "box": False, "boxColor": "#000000", "boxOpacity": 0.55, "outline": True}
            base.update(preset)
            self.commit(lambda q: q["texts"].append(base))
            self.sel_text = len(p["texts"]) - 1
            self.refresh_panels()
        v.addWidget(label("ADD TEXT", "h3"))
        v.addWidget(flow(btn("Caption", "plus", lambda: add(), sm=True), btn("Title", None, lambda: add(text="TITLE", y=0.12, size=110, start=0.0, end=min(tot, 3.0)), sm=True),
                         btn("Boxed label", None, lambda: add(text="Label", box=True, outline=False, size=54, y=0.08, x=0.04), sm=True),
                         btn("Watermark", None, lambda: add(text="@yourname", size=40, x=0.97, y=0.95, start=0.0, end=tot), sm=True)))
        if p["texts"]:
            v.addWidget(label("LAYERS", "h3"))
            for i, t in enumerate(p["texts"]):
                b_ = btn(f"T   {t['text'][:28]}   ·   {fmt_dur(t['start'])}–{fmt_dur(t['end'])}", None, lambda i=i: self.select_text(i), sm=True, ghost=i != self.sel_text)
                b_.setStyleSheet("text-align: left;")
                v.addWidget(b_)
        if self.sel_text is None:
            return
        i = self.sel_text
        t = p["texts"][i]
        te = QPlainTextEdit(t["text"])
        te.setFixedHeight(64)
        te.textChanged.connect(lambda: self.live(lambda q: q["texts"][i].__setitem__("text", te.toPlainText())))
        self.field(v, "Text", te)
        st, en = QDoubleSpinBox(), QDoubleSpinBox()
        for sp, k in ((st, "start"), (en, "end")):
            sp.setDecimals(1)
            sp.setSingleStep(0.1)
            sp.setRange(0, tot)
            sp.setValue(t[k])
            sp.editingFinished.connect(lambda sp=sp, k=k: self.commit(lambda q: q["texts"][i].__setitem__(k, sp.value())))
        v.addWidget(hbox(label("Start", "dim"), st, label("End", "dim"), en))
        v.addWidget(flow(btn("Start at playhead", None, lambda: self.commit(lambda q: q["texts"][i].__setitem__("start", self.time)), sm=True),
                         btn("End at playhead", None, lambda: self.commit(lambda q: q["texts"][i].__setitem__("end", max(t["start"] + 0.1, self.time))), sm=True),
                         btn("Whole video", None, lambda: self.commit(lambda q: q["texts"][i].update(start=0.0, end=tot)), sm=True)))
        sz = FSlider(16, 220, 1, t["size"], fmt=lambda x: str(int(x)))
        sz.pressed.connect(self.snapshot)
        sz.changed.connect(lambda x: self.live(lambda q: q["texts"][i].__setitem__("size", x)))
        self.field(v, "Size", sz)

        def pick(key):
            c = QColorDialog.getColor(QColor(t.get(key, "#ffffff")), self, "Choose colour")
            if c.isValid():
                self.commit(lambda q: q["texts"][i].__setitem__(key, c.name()))
        sw = btn("Color", None, lambda: pick("color"), sm=True)
        sw.setStyleSheet(f"border-left: 14px solid {t['color']};")
        v.addWidget(hbox(sw, Toggle(t.get("outline"), lambda x: self.commit(lambda q: q["texts"][i].__setitem__("outline", x))), label("Outline"), 14,
                         Toggle(t.get("box"), lambda x: self.commit(lambda q: q["texts"][i].__setitem__("box", x))), label("Box"), "stretch"))
        if t.get("box"):
            bo = FSlider(0, 1, 0.05, t.get("boxOpacity", 0.5), fmt=lambda x: f"{int(x * 100)}%")
            bo.pressed.connect(self.snapshot)
            bo.changed.connect(lambda x: self.live(lambda q: q["texts"][i].__setitem__("boxOpacity", x)))
            bc = btn("Box color", None, lambda: pick("boxColor"), sm=True)
            v.addWidget(hbox(bc, bo))
        v.addWidget(label("POSITION — or drag the text in the preview", "h3"))
        v.addWidget(flow(*[btn(n, None, lambda x=x, y=y: self.commit(lambda q: q["texts"][i].update(x=x, y=y)), sm=True, ghost=True)
                           for n, x, y in (("Top", 0.5, 0.06), ("Center", 0.5, 0.5), ("Bottom", 0.5, 0.92), ("Top left", 0.04, 0.06), ("Top right", 0.96, 0.06), ("Bottom right", 0.96, 0.94))]))
        v.addWidget(btn("Delete text", "trash", lambda: (setattr(self, "sel_text", None), self.commit(lambda q: q["texts"].pop(i))), sm=True, danger=True))

    def panel_audio(self, v):
        a = self.project["audio"]
        multi = len(self.clips) > 1
        if multi:
            v.addWidget(label("Montages use each clip's mixed audio track.", "muted", wrap=True))
        elif not a["tracks"]:
            v.addWidget(label("This clip has no audio.", "muted"))
        else:
            v.addWidget(label("TRACKS", "h3"))
            if len(a["tracks"]) > 1:
                v.addWidget(label("Track 1 is the combined mix — keep it muted when using the separate tracks to avoid doubled audio.", "dim", wrap=True))
            for i, t in enumerate(a["tracks"]):
                fr = QFrame()
                fr.setObjectName("inset")
                fl = QVBoxLayout(fr)
                fl.setContentsMargins(12, 10, 12, 10)
                name = TRACK_NAMES[i] if len(a["tracks"]) > 1 and i < len(TRACK_NAMES) else t.get("title", "Audio")
                mute = btn("", "micOff" if t.get("muted") else "volume", lambda i=i: self.commit(lambda q: q["audio"]["tracks"][i].__setitem__("muted", not q["audio"]["tracks"][i].get("muted"))), sm=True, danger=t.get("muted"))
                fl.addWidget(hbox(QLabel(f"<b>{name}</b>"), "stretch", mute))
                s = FSlider(0, 2, 0.05, t.get("volume", 1), fmt=lambda x: f"{int(x * 100)}%")
                s.pressed.connect(self.snapshot)
                s.changed.connect(lambda x, i=i: self.live(lambda q: q["audio"]["tracks"][i].__setitem__("volume", x)))
                fl.addWidget(s)
                v.addWidget(fr)
            if len(a["tracks"]) > 1 and not self.aux:
                v.addWidget(label("Preparing per-track preview…", "dim"))
        v.addWidget(hbox(Toggle(a.get("normalize"), lambda x: self.commit(lambda q: q["audio"].__setitem__("normalize", x))), label("Normalize loudness (export)"), "stretch"))
        v.addWidget(label("MUSIC", "h3"))
        m = a.get("music")
        if not m:
            def add_music():
                f, _ = QFileDialog.getOpenFileName(self, "Choose music", "", "Audio (*.mp3 *.wav *.ogg *.m4a *.aac *.flac)")
                if f:
                    self.commit(lambda q: q["audio"].__setitem__("music", {"path": f, "volume": 0.4, "start": 0.0, "loop": True}))
                    self.setup_music()
            v.addWidget(btn("Add background music", "plus", add_music, sm=True))
        else:
            fr = QFrame()
            fr.setObjectName("inset")
            fl = QVBoxLayout(fr)
            fl.setContentsMargins(12, 10, 12, 10)
            fl.addWidget(hbox(label(os.path.basename(m["path"])), "stretch", btn("", "trash", lambda: (self.commit(lambda q: q["audio"].__setitem__("music", None)), self.setup_music()), sm=True, danger=True)))
            s = FSlider(0, 1.5, 0.05, m.get("volume", 0.4), fmt=lambda x: f"{int(x * 100)}%")
            s.pressed.connect(self.snapshot)
            s.changed.connect(lambda x: self.live(lambda q: q["audio"]["music"].__setitem__("volume", x)))
            fl.addWidget(s)
            sp = QDoubleSpinBox()
            sp.setRange(0, 3600)
            sp.setSingleStep(0.5)
            sp.setValue(m.get("start", 0))
            sp.editingFinished.connect(lambda: self.commit(lambda q: q["audio"]["music"].__setitem__("start", sp.value())))
            fl.addWidget(hbox(label("Starts at (s)", "dim"), sp, Toggle(m.get("loop"), lambda x: (self.commit(lambda q: q["audio"]["music"].__setitem__("loop", x)), self.setup_music())), label("Loop"), "stretch"))
            v.addWidget(fr)

    def can_copy(self):
        p = self.project
        o = p["output"]
        return (len(self.clips) == 1 and len(p["segments"]) == 1 and p["segments"][0].get("speed", 1) == 1 and not p.get("crop") and not p["rotate"]
                and not p["flipH"] and not p["flipV"] and p["aspect"] == "source" and not p["texts"] and not p["fadeIn"] and not p["fadeOut"]
                and all(abs(p["color"].get(k, d) - d) < 1e-9 for k, d in DEFAULT_COLOR.items()) and not p["audio"].get("music") and not p["audio"].get("normalize")
                and o["resolution"] == "source" and o["fps"] == "source" and o["format"] == "mp4" and not o.get("targetMB"))

    def panel_export(self, v):
        o = self.project["output"]
        multi = len(self.clips) > 1
        if not hasattr(self, "export_title") or not self.export_title:
            self.export_title = f"Montage {len(self.clips)} clips" if multi else f"{self.clips[0]['title']} (edit)"
        name = QLineEdit(self.export_title)
        name.textChanged.connect(lambda s: setattr(self, "export_title", s))
        self.field(v, "File name", name)

        def setp(**kw):
            self.commit(lambda q: q["output"].update(**kw))
        self.field(v, "Format", Seg([("mp4", "MP4"), ("webm", "WebM"), ("mkv", "MKV"), ("gif", "GIF")], o["format"], on_change=lambda x: setp(format=x)))
        res = Combo([("source", "Original"), (2160, "2160p (4K)"), (1440, "1440p"), (1080, "1080p"), (720, "720p"), (480, "480p"), (360, "360p")], o["resolution"], on_change=lambda x: setp(resolution=x))
        fps = Combo([("source", "Original"), (144, "144"), (120, "120"), (60, "60"), (30, "30"), (24, "24")], o["fps"], on_change=lambda x: setp(fps=x))
        v.addWidget(hbox(label("Resolution", "dim"), res, label("FPS", "dim"), fps))
        if o["format"] != "gif":
            tgt = Seg([(0, "None"), (20, "20 MB"), (50, "50 MB"), (500, "500 MB")], o.get("targetMB") if o.get("targetMB") in (0, 20, 50, 500) else None, accent=True, on_change=lambda x: setp(targetMB=x))
            self.field(v, "File size limit", tgt)
            cust = QDoubleSpinBox()
            cust.setRange(0, 100000)
            cust.setDecimals(0)
            cust.setValue(o.get("targetMB") or 0)
            cust.setSuffix(" MB")
            cust.editingFinished.connect(lambda: setp(targetMB=cust.value()))
            v.addWidget(hbox(label("Custom:", "dim"), cust, "stretch"))
            if not o.get("targetMB"):
                self.field(v, "Quality", Seg([("high", "High"), ("medium", "Medium"), ("low", "Small")], o["quality"], on_change=lambda x: setp(quality=x)))
        if o["format"] in ("mp4", "mkv"):
            enc = Combo([("auto", "Auto (GPU)"), ("x264", "CPU (best size accuracy)"), ("nvenc", "NVIDIA"), ("amf", "AMD"), ("qsv", "Intel")], o["encoder"], on_change=lambda x: setp(encoder=x))
            codec = Combo([("h264", "H.264"), ("hevc", "HEVC")], o["codec"], on_change=lambda x: setp(codec=x))
            v.addWidget(hbox(label("Encoder", "dim"), enc, label("Codec", "dim"), codec))
        if self.can_copy():
            v.addWidget(hbox(Toggle(o.get("mode") == "copy", lambda x: setp(mode="copy" if x else "encode")), label("Lossless fast trim (no re-encode)"), "stretch"))
        self.est_lb = label("", "dim", wrap=True)
        v.addWidget(self.est_lb)
        self.update_estimate()
        v.addWidget(btn(f"  Export {o['format'].upper()}", "export", self.export, primary=True, lg=True))
        v.addWidget(label("Exports are saved to Exports in your clip folder and added to the library. Your original clip is never modified.", "dim", wrap=True))

    def export_project(self):
        p = copy.deepcopy(self.project)
        if len(self.clips) > 1:
            p["audio"]["tracks"] = []
        if p["output"].get("mode") == "copy" and not self.can_copy():
            p["output"]["mode"] = "encode"
        return p

    def update_estimate(self):
        if not self.project or not hasattr(self, "est_lb"):
            return
        proj = self.export_project()
        lb = self.est_lb

        def done(e):
            try:
                txt = f"{fmt_dur(e['duration'])} · {e['W']}×{e['H']} @ {round(e['fps'])} fps"
                if e.get("videoKbps"):
                    txt += f" · {e['videoKbps']} kbps video + {e['audioKbps']} kbps audio · < {proj['output']['targetMB']:g} MB"
                lb.setText(txt)
            except RuntimeError:
                pass
        run_bg(lambda: render.estimate(proj), done, lambda e: None)

    def export(self):
        if self.total() <= 0.05:
            return
        job = self.backend.render(self.export_project(), self.export_title, self.parent_id, "edit")
        self.toast("Export started", job["title"])


_ = (QPen, QUrl, QFrame)

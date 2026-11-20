"""Find Frame dialog: shown after drawing a box on a paused clip.

    FindFrameDialog(win, clip, t, box).exec()

Captures the reference (background), analyses it, and lets the user search right away
or save it as a reusable finder. Also hosts small widgets shared with the Find Frame page.
"""
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSizePolicy, QVBoxLayout, QWidget

from . import icons
from .bg import run_bg
from .clipgrid import THUMBS
from .theme import C
from .widgets import Dialog, FSlider, Seg, Toggle, btn, fmt_dur, label

METHODS = [("auto", "Auto"), ("template", "Exact look"), ("features", "Features")]
AREAS = [("anywhere", "Anywhere on screen"), ("near", "Same spot (faster)")]


def game_name(clip):
    return clip["game"]["name"] if clip and clip.get("game") else "Desktop"


# ---------------------------------------------------------------- shared widgets
class ImageBox(QWidget):
    """Small image painted with object-fit: contain on a black rounded box.

    Pixmaps come from the shared, memory-bounded THUMBS cache (decoded at `decode_w` px wide max),
    so no full-resolution pixmaps are kept around."""
    clicked = Signal()

    def __init__(self, path=None, w=None, h=None, decode_w=320, radius=8, border=False, opacity=1.0):
        super().__init__()
        self.path, self.decode_w, self.radius, self.border, self.opacity = path, decode_w, radius, border, opacity
        self.placeholder = ""
        if w and h:
            self.setFixedSize(w, h)
        elif h:
            self.setFixedHeight(h)
        THUMBS.updated.connect(self._updated)

    def set_path(self, path, opacity=1.0):
        if path != self.path or opacity != self.opacity:
            self.path, self.opacity = path, opacity
            self.update()

    def _updated(self, path):
        if path == self.path:
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        p.fillPath(path, QColor("#000000"))
        p.setClipPath(path)
        pm = THUMBS.get(self.path, self.decode_w) if self.path else None
        if pm and pm.width() and pm.height():
            s = min(r.width() / pm.width(), r.height() / pm.height())
            w, h = pm.width() * s, pm.height() * s
            p.setOpacity(self.opacity)
            p.drawPixmap(QRectF(r.x() + (r.width() - w) / 2, r.y() + (r.height() - h) / 2, w, h), pm, QRectF(pm.rect()))
            p.setOpacity(1.0)
        elif self.placeholder:
            p.setPen(QColor(C["text3"]))
            p.drawText(r, Qt.AlignCenter, self.placeholder)
        p.setClipping(False)
        if self.border:
            p.setPen(QColor(C["line2"]))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r, self.radius, self.radius)


class ElideLabel(QLabel):
    """Single-line label that elides with … instead of growing."""

    def __init__(self, text="", style=""):
        super().__init__()
        self.full = text
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(20)
        if style:
            self.setStyleSheet(style)
        self.set_text(text)

    def set_text(self, text):
        self.full = text or ""
        self.setToolTip(self.full)
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        super().setText(QFontMetrics(self.font()).elidedText(self.full, Qt.ElideRight, max(10, self.width())))


def field(title, *widgets, hint=None):
    """Label above control(s), like the legacy .field. Returns (widget, title_label, hint_label)."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(5)
    t = QLabel(title)
    t.setStyleSheet(f"font-size: 12px; font-weight: 650; color: {C['text2']};")
    lay.addWidget(t)
    for x in widgets:
        lay.addWidget(x, 0, Qt.AlignLeft) if isinstance(x, Seg) else lay.addWidget(x)
    h = None
    if hint is not None:
        h = label(hint, "dim", wrap=True)
        lay.addWidget(h)
    return w, t, h


def toggle_row(text, toggle):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    lay.addWidget(toggle)
    lb = QLabel(text)
    lb.setStyleSheet("font-size: 12.5px;")
    lb.setCursor(Qt.PointingHandCursor)
    lb.mousePressEvent = lambda e: toggle.toggle()
    lay.addWidget(lb)
    lay.addStretch(1)
    return w


# ---------------------------------------------------------------- dialog
class FindFrameDialog(Dialog):
    def __init__(self, win, clip, time, box):
        super().__init__(win, "Find Frame", 680)
        self.win, self.backend, self.ff = win, win.backend, win.backend.findframe
        if isinstance(clip, str):
            clip = self.backend.library.get(clip)
        elif clip:
            clip = self.backend.library.get(clip["id"]) or clip
        self.clip, self.time, self.box = clip, float(time or 0), dict(box)
        self.ref = None
        self.analysis = None
        self.show_kp = False
        self.busy = False
        self._alive = True
        cfg = self.backend.settings.get()["findFrame"]

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(0)
        self.body.addLayout(grid)

        # left: reference preview + analysis
        left = QVBoxLayout()
        left.setSpacing(8)
        self.preview = ImageBox(None, 220, 220, decode_w=440, radius=10, border=True)
        self.preview.placeholder = "Capturing…"
        self.preview.clicked.connect(self._toggle_kp)
        left.addWidget(self.preview)
        self.info = label("", "dim", wrap=True)
        self.info.setTextFormat(Qt.RichText)
        self.info.setFixedWidth(220)
        left.addWidget(self.info)
        self.err = label("", wrap=True)
        self.err.setStyleSheet("color: #fca5a5; font-size: 12px;")
        self.err.setFixedWidth(220)
        self.err.hide()
        left.addWidget(self.err)
        left.addStretch(1)
        grid.addLayout(left, 0, 0)

        # right: options
        right = QVBoxLayout()
        right.setSpacing(12)
        game = game_name(clip)
        self.name = QLineEdit(f"{game if clip and clip.get('game') else 'Clip'} · {fmt_dur(self.time)}")
        right.addWidget(field("Name", self.name)[0])
        self.method = Seg(METHODS, "auto", on_change=lambda v: self._method_changed())
        w, _, self.method_hint = field("Method", self.method, hint="")
        right.addWidget(w)
        self.thr = FSlider(5, 90, 1, cfg.get("threshold", 30), fmt=lambda v: f"{int(v)}%")
        self.thr_w = field("Feature threshold", self.thr)[0]
        right.addWidget(self.thr_w)
        self.tthr = FSlider(0.5, 0.99, 0.01, cfg.get("templateThreshold", 0.8), fmt=lambda v: f"{round(v * 100)}%")
        self.tthr_w = field("Template threshold", self.tthr)[0]
        right.addWidget(self.tthr_w)
        self.area = Seg(AREAS, cfg.get("searchArea", "anywhere") if cfg.get("searchArea") in ("anywhere", "near") else "anywhere")
        right.addWidget(field("Look for it", self.area)[0])
        self.scope = Seg([("clip", "This clip"), ("game", game if clip and clip.get("game") else "Desktop clips"), ("all", "All clips")], "clip")
        right.addWidget(field("Search in", self.scope)[0])
        self.auto = Toggle(True)
        self.tag = QLineEdit()
        self.tag.setPlaceholderText("Tag matches (optional)")
        self.tag.setFixedWidth(190)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(toggle_row("Auto-scan new clips", self.auto), 1)
        row.addWidget(self.tag)
        right.addLayout(row)
        right.addStretch(1)
        grid.addLayout(right, 0, 1)
        grid.setColumnStretch(1, 1)

        self.b_save = self.add_button(btn("Save finder", "save", lambda: self._save(False), icon_size=14))
        self.b_scan = self.add_button(btn("Save && scan", None, lambda: self._save(True)))
        self.b_search = self.add_button(btn("Search", "search", self._search, primary=True, icon_size=14))
        self.b_search.setDefault(True)
        self._method_changed()
        self._set_enabled()

        if not clip:
            self._error("Clip not found.")
        else:
            run_bg(lambda: self.ff.capture(clip["id"], self.time, self.box), self._captured, self._error)

    # ---------------------------------------------------------------- async steps
    def _captured(self, ref):
        if not self._alive:
            return
        self.ref = ref
        self.preview.placeholder = ""
        self.preview.set_path(ref["image"])
        self.info.setText("Analyzing…")
        self._set_enabled()
        run_bg(lambda: self.ff.analyze(ref, {}), self._analyzed, lambda e: self._alive and self.info.setText(f"Analysis failed: {e}"))

    def _analyzed(self, a):
        if not self._alive:
            return
        self.analysis = a or {}
        kp = int(self.analysis.get("keypoints") or 0)
        rec = "feature matching" if self.analysis.get("method") == "features" else "template matching"
        txt = f"{kp} keypoints · {self.analysis.get('width', '?')}×{self.analysis.get('height', '?')}px<br>Auto will use <b style='color:{C['text']}'>{rec}</b>"
        if kp < 60:
            txt += "<br>Low detail: best for icons/HUD at a fixed size."
        if self.analysis.get("preview"):
            txt += "<br><span style='color:%s'>Click the image to toggle keypoints.</span>" % C["text3"]
            self.preview.setCursor(Qt.PointingHandCursor)
            self.preview.setToolTip("Click to toggle detected keypoints")
        self.info.setText(txt)

    def _error(self, e):
        if not self._alive:
            return
        self.preview.placeholder = "No image"
        self.preview.update()
        self.err.setText(str(e))
        self.err.show()

    # ---------------------------------------------------------------- ui
    def _toggle_kp(self):
        if not self.ref or not (self.analysis or {}).get("preview"):
            return
        self.show_kp = not self.show_kp
        self.preview.set_path(self.analysis["preview"] if self.show_kp else self.ref["image"])

    def _method_changed(self):
        m = self.method.value
        self.method_hint.setText({"template": "Pixel-accurate: kill icons, banners, HUD text.",
                                  "features": "FindFrame keypoints: survives zoom, rotation, lighting."}.get(m, "Picks the best method from the image detail."))
        self.thr_w.setVisible(m != "template")
        self.tthr_w.setVisible(m != "features")

    def _set_enabled(self):
        ok = bool(self.ref) and not self.busy
        for b in (self.b_save, self.b_scan, self.b_search):
            b.setEnabled(ok)

    def settings(self):
        m = self.method.value
        s = {"method": m, "searchArea": self.area.value}
        if m != "template":
            s["threshold"] = self.thr.value()
        if m != "features":
            s["templateThreshold"] = self.tthr.value()
        return s

    def clip_ids(self):
        sc = self.scope.value
        if sc == "clip":
            return [self.clip["id"]]
        if sc == "game":
            g = game_name(self.clip)
            return [c["id"] for c in self.backend.library.list() if game_name(c) == g]
        return "all"

    def _toast(self, title, subtitle=""):
        try:
            self.win.toaster.show_toast({"title": title, "subtitle": subtitle})
        except Exception:  # noqa: BLE001
            pass

    def _name(self):
        return self.name.text().strip() or "Finder"

    def _search(self):
        if not self.ref:
            return
        self.busy = True
        self._set_enabled()
        try:
            job = self.ff.search(label=self._name(), reference={**self.ref, "name": self._name(), "settings": self.settings()}, clip_ids=self.clip_ids())
        except Exception as e:  # noqa: BLE001
            self.busy = False
            self._set_enabled()
            return self._error(e)
        self.accept()
        self.win.go("findframe", job=job["id"])

    def _save(self, scan):
        if not self.ref:
            return
        self.busy = True
        self._set_enabled()
        try:
            f = self.ff.create_finder({**self.ref, "name": self._name(), "settings": self.settings(), "autoScan": self.auto.isChecked(),
                                       "tag": self.tag.text().strip()})
            self._toast("Finder saved", self._name())
            job = self.ff.search(finder_ids=[f["id"]], clip_ids=self.clip_ids()) if scan else None
        except Exception as e:  # noqa: BLE001
            self.busy = False
            self._set_enabled()
            return self._error(e)
        self.accept()
        if job:
            self.win.go("findframe", job=job["id"])

    def done(self, r):
        self._alive = False
        super().done(r)
        self.deleteLater()

    def sizeHint(self):
        return QSize(680, 520)


_ = (QFrame, icons)

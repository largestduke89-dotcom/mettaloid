"""Editor timeline (custom painted): ruler, segments with filmstrips, trim handles, text track, bookmarks, playhead."""
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..clipgrid import THUMBS
from ..theme import C
from ..widgets import fmt_dur

RULER, TRACK_Y, TRACK_H, TEXT_Y, TEXT_H = 18, 24, 60, 90, 22


def seg_dur(s):
    return (s["end"] - s["start"]) / (s.get("speed") or 1)


class Timeline(QWidget):
    seek = Signal(float)              # output seconds
    select_segment = Signal(int)
    select_text = Signal(int)
    edit_begin = Signal()             # before a drag edit (for undo)
    segment_trim = Signal(int, float, float)  # index, start, end (live)

    def __init__(self):
        super().__init__()
        self.project = None
        self.durations = []           # source durations
        self.film = {}                # source path -> [thumb paths]
        self.time = 0.0
        self.zoom = 1.0
        self.sel_seg = 0
        self.sel_text = None
        self.marks = []               # [(output_t, label)]
        self.drag = None
        self.setMinimumHeight(TEXT_Y + TEXT_H + 8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.font_s = QFont("Segoe UI", 8)
        self.font_b = QFont("Segoe UI", 8)
        self.font_b.setBold(True)
        THUMBS.updated.connect(lambda p: self.update())

    # ---- geometry
    def offsets(self):
        t, out = 0.0, []
        for s in self.project["segments"]:
            out.append((t, t + seg_dur(s)))
            t += seg_dur(s)
        return out

    def total(self):
        o = self.offsets()
        return o[-1][1] if o else 0.0

    def content_width(self):
        return max(1, int((self.parent().width() if self.parent() else self.width()) * self.zoom))

    def pps(self):
        tot = self.total()
        return (self.width() - 2) / tot if tot > 0 else 1

    def set_data(self, project, durations, film=None):
        self.project = project
        self.durations = durations
        if film is not None:
            self.film = film
        self.update()

    # ---- painting
    def paintEvent(self, _):
        if not self.project:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pps = self.pps()
        tot = self.total()
        # ruler
        step = next((s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800) if s * pps >= 70), 3600)
        p.setFont(self.font_s)
        t = 0.0
        while t <= tot + 1e-6:
            x = t * pps
            p.setPen(QColor(C["line2"]))
            p.drawLine(int(x), RULER - 5, int(x), RULER)
            p.setPen(QColor(C["text3"]))
            p.drawText(QRectF(x - 30, 0, 60, RULER - 4), Qt.AlignCenter, fmt_dur(t))
            t += step
        # track bg
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["bg3"]))
        p.drawRoundedRect(QRectF(0, TRACK_Y, self.width(), TRACK_H), 8, 8)
        # segments
        for i, (s, (a, b)) in enumerate(zip(self.project["segments"], self.offsets())):
            r = QRectF(a * pps + 1, TRACK_Y, max(4, (b - a) * pps - 2), TRACK_H)
            path = QPainterPath()
            path.addRoundedRect(r, 7, 7)
            p.save()
            p.setClipPath(path)
            src = self.project["sources"][s["src"]]
            thumbs = self.film.get(src) or []
            dur = self.durations[s["src"]] or 1
            if thumbs:
                n = max(1, int(r.width() // 60) + 1)
                for k in range(n):
                    st = s["start"] + (s["end"] - s["start"]) * (k + 0.5) / n
                    pm = THUMBS.get(thumbs[min(len(thumbs) - 1, int(st / dur * len(thumbs)))], 160)
                    if pm:
                        cell = QRectF(r.x() + k * r.width() / n, r.y(), r.width() / n, r.height())
                        sc = max(cell.width() / pm.width(), cell.height() / pm.height())
                        w, h = pm.width() * sc, pm.height() * sc
                        p.setOpacity(0.38)
                        p.drawPixmap(QRectF(cell.center().x() - w / 2, cell.center().y() - h / 2, w, h), pm, QRectF(pm.rect()))
                        p.setOpacity(1)
            sel = i == self.sel_seg
            p.fillRect(r, QColor(245, 183, 10, 85 if sel else 50))
            p.restore()
            p.setPen(QPen(QColor(C["accent2"] if sel else "#c99a12"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 7, 7)
            p.setPen(QColor("white"))
            p.setFont(self.font_b)
            lbl = (f"{s.get('speed')}x · " if s.get("speed", 1) != 1 else "") + fmt_dur(b - a)
            if len(self.project["sources"]) > 1:
                lbl += f" · #{s['src'] + 1}"
            p.drawText(r.adjusted(10, 4, -10, 0), Qt.AlignLeft | Qt.AlignTop, lbl)
            # handles
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(C["accent"]))
            p.drawRoundedRect(QRectF(r.left() - 1, r.top() - 1, 9, r.height() + 2), 4, 4)
            p.drawRoundedRect(QRectF(r.right() - 8, r.top() - 1, 9, r.height() + 2), 4, 4)
        # texts
        for i, tx in enumerate(self.project.get("texts") or []):
            r = QRectF(tx["start"] * pps, TEXT_Y, max(10, (tx["end"] - tx["start"]) * pps), TEXT_H - 2)
            p.setPen(QPen(QColor(59, 130, 246, 200), 1))
            p.setBrush(QColor(59, 130, 246, 115 if i == self.sel_text else 60))
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QColor(C["text"]))
            p.setFont(self.font_s)
            p.drawText(r.adjusted(6, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft, p.fontMetrics().elidedText("T  " + tx["text"], Qt.ElideRight, int(r.width() - 10)))
        # bookmarks
        for t, label in self.marks:
            x = t * pps
            p.setPen(QPen(QColor(C["purple"]), 2))
            p.drawLine(int(x), RULER, int(x), TRACK_Y + TRACK_H)
        # playhead
        x = self.time * pps
        p.setPen(QPen(QColor("white"), 2))
        p.drawLine(int(x), 0, int(x), self.height())
        p.setBrush(QColor("white"))
        p.setPen(Qt.NoPen)
        tri = QPainterPath()
        tri.moveTo(x - 6, 0)
        tri.lineTo(x + 6, 0)
        tri.lineTo(x, 8)
        tri.closeSubpath()
        p.drawPath(tri)

    # ---- interaction
    def _hit(self, pos):
        if not self.project:
            return None
        pps = self.pps()
        x, y = pos.x(), pos.y()
        if TRACK_Y <= y <= TRACK_Y + TRACK_H:
            for i, (a, b) in enumerate(self.offsets()):
                l, r = a * pps, b * pps
                if abs(x - l) <= 8:
                    return ("l", i)
                if abs(x - r) <= 8:
                    return ("r", i)
                if l <= x <= r:
                    return ("seg", i)
        if TEXT_Y <= y <= TEXT_Y + TEXT_H:
            for i, tx in enumerate(self.project.get("texts") or []):
                if tx["start"] * pps <= x <= max(tx["start"] * pps + 10, tx["end"] * pps):
                    return ("text", i)
        return ("ruler", None)

    def mouseMoveEvent(self, e):
        if self.drag:
            kind, i, x0, s0 = self.drag
            pps = self.pps()
            if kind in ("l", "r"):
                seg = self.project["segments"][i]
                ds = (e.position().x() - x0) / pps * (s0.get("speed") or 1)
                if kind == "l":
                    st = min(max(0.0, s0["start"] + ds), s0["end"] - 0.1)
                    self.segment_trim.emit(i, st, seg["end"])
                else:
                    en = max(min(self.durations[s0["src"]], s0["end"] + ds), s0["start"] + 0.1)
                    self.segment_trim.emit(i, seg["start"], en)
            else:
                self.seek.emit(max(0.0, min(self.total(), e.position().x() / pps)))
            return
        h = self._hit(e.position())
        self.setCursor(Qt.SizeHorCursor if h and h[0] in ("l", "r") else Qt.ArrowCursor)

    def mousePressEvent(self, e):
        h = self._hit(e.position())
        if not h:
            return
        kind, i = h
        if kind in ("l", "r"):
            self.edit_begin.emit()
            self.sel_seg = i
            self.select_segment.emit(i)
            self.drag = (kind, i, e.position().x(), dict(self.project["segments"][i]))
            return
        if kind == "seg":
            self.sel_seg = i
            self.select_segment.emit(i)
        if kind == "text":
            self.sel_text = i
            self.select_text.emit(i)
            return
        self.drag = ("seek", None, 0, None)
        self.seek.emit(max(0.0, min(self.total(), e.position().x() / self.pps())))
        self.update()

    def mouseReleaseEvent(self, e):
        self.drag = None

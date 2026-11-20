"""Editor timeline (custom painted): ruler, segments with filmstrips, trim handles, text track, bookmarks, playhead."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
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
    zoom_wheel = Signal(float, float)  # zoom factor, anchor x (widget coords)
    scroll_wheel = Signal(int)         # wheel delta to scroll horizontally
    scrub = Signal(float)              # output seconds, while the playhead is dragged (cheap preview)
    drag_end = Signal(str)             # "seek" / "l" / "r" when the mouse button is released

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
        self.frozen = None            # (pps, x0) locked while a trim handle is dragged
        self._tiles = {}              # (thumb path, dpr) -> pixmap scaled to track height
        self._cache = self._cache_key = None   # everything except the playhead, re-rendered only when it changes
        self._thumb_gen = 0
        self.setMinimumHeight(TEXT_Y + TEXT_H + 8)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.font_s = QFont("Segoe UI", 8)
        self.font_b = QFont("Segoe UI", 8)
        self.font_b.setBold(True)
        THUMBS.updated.connect(self._thumb_loaded)

    def _thumb_loaded(self, _path):
        self._thumb_gen += 1
        self.update()

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

    def _ghosts(self):
        """Trimmed-off source before the first segment and after the last one, in output seconds."""
        segs = self.project["segments"] if self.project else []
        if not segs or not self.durations:
            return 0.0, 0.0
        a, b = segs[0], segs[-1]
        lead = a["start"] / (a.get("speed") or 1)
        tail = max(0.0, (self.durations[b["src"]] or b["end"]) - b["end"]) / (b.get("speed") or 1)
        return lead, tail

    def _scale(self):
        """(pixels per second, x of output t=0). The full source span fits the width, so trimming the outer
        edges moves the handles inward instead of re-stretching the clip to fill the track."""
        lead, tail = self._ghosts()
        span = lead + self.total() + tail
        pps = (self.width() - 2) / span if span > 0 else 1
        return pps, lead * pps + 1

    def pps(self):
        return self.frozen[0] if self.frozen else self._scale()[0]

    def x0(self):
        return self.frozen[1] if self.frozen else self._scale()[1]

    def shift(self):
        """While a left handle is dragged, that segment and everything after it is drawn shifted so the handle
        follows the mouse; the gap ripples closed on release. Returns (segment index, px)."""
        if not (self.drag and self.drag[0] == "l"):
            return None, 0.0
        _, i, _, s0 = self.drag
        s = self.project["segments"][i]
        return i, (s["start"] - s0["start"]) / (s0.get("speed") or 1) * self.pps()

    def seg_rects(self):
        pps, x0 = self.pps(), self.x0()
        si, sh = self.shift()
        out = []
        for i, (a, b) in enumerate(self.offsets()):
            d = sh if si is not None and i >= si else 0.0
            out.append((x0 + a * pps + d, x0 + b * pps + d))
        return out

    def x_of(self, t):
        x = self.x0() + t * self.pps()
        si, sh = self.shift()
        if si is not None and t >= self.offsets()[si][0]:
            x += sh
        return x

    def t_of(self, x):
        return max(0.0, min(self.total(), (x - self.x0()) / self.pps()))

    def set_data(self, project, durations, film=None):
        self.project = project
        self.durations = durations
        if film is not None:
            self.film = film
        self.update()

    # ---- painting
    def _tile(self, path):
        """Thumbnail pre-scaled to the track height (cached), so tiles are drawn 1:1 and never stretched."""
        dpr = self.devicePixelRatioF()
        key = (path, dpr)
        pm = self._tiles.get(key)
        if pm is None:
            src = THUMBS.get(path, 240)
            if not src:
                return None
            pm = src.scaledToHeight(int(TRACK_H * dpr), Qt.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            if len(self._tiles) > 600:
                self._tiles.clear()
            self._tiles[key] = pm
        return pm

    def _strip(self, p, r, src_i, origin, speed, opacity):
        """Fixed-size thumbnails laid out along the source's own time axis (origin = x of source t=0), clipped
        to r. Trimming just reveals/hides tiles; nothing moves or stretches while a handle is dragged."""
        thumbs = self.film.get(self.project["sources"][src_i]) or []
        dur = self.durations[src_i] or 1
        if not thumbs or r.width() <= 0:
            return
        first = self._tile(thumbs[0])
        tw = first.width() / first.devicePixelRatio() if first else TRACK_H * 16 / 9
        pps = self.pps()
        dt = tw / pps * speed               # source seconds per tile
        if dt <= 0:
            return
        k0, k1 = int((r.left() - origin) // tw), int((r.right() - origin) // tw) + 1
        p.setOpacity(opacity)
        for k in range(max(0, k0), k1):
            t = (k + 0.5) * dt
            if t > dur + dt:
                break
            pm = self._tile(thumbs[max(0, min(len(thumbs) - 1, int(t / dur * len(thumbs))))])
            if pm:
                p.drawPixmap(QPointF(origin + k * tw, r.top()), pm)
        p.setOpacity(1)

    def _static_key(self):
        return (self.width(), self.height(), self.devicePixelRatioF(), repr(self.project["segments"]), repr(self.project.get("texts")),
                self.sel_seg, self.sel_text, tuple(self.marks), self.frozen, self.shift(), self._thumb_gen, len(self.film))

    def paintEvent(self, _):
        if not self.project:
            return
        key = self._static_key()
        if self._cache is None or self._cache_key != key:
            dpr = self.devicePixelRatioF()
            pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            sp = QPainter(pm)
            self._paint_static(sp)
            sp.end()
            self._cache, self._cache_key = pm, key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        p.setRenderHint(QPainter.Antialiasing)
        # playhead
        x = self.x_of(self.time)
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

    def _paint_static(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        pps = self.pps()
        tot = self.total()
        segs = self.project["segments"]
        rects = self.seg_rects()
        # ruler
        step = next((s for s in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800) if s * pps >= 70), 3600)
        p.setFont(self.font_s)
        t = 0.0
        while t <= tot + 1e-6:
            x = self.x_of(t)
            p.setPen(QColor(C["line2"]))
            p.drawLine(int(x), RULER - 5, int(x), RULER)
            p.setPen(QColor(C["text3"]))
            p.drawText(QRectF(x - 30, 0, 60, RULER - 4), Qt.AlignCenter, fmt_dur(t))
            t += step
        # track bg
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["bg3"]))
        p.drawRoundedRect(QRectF(0, TRACK_Y, self.width(), TRACK_H), 8, 8)
        # trimmed-off source outside the outer handles, dimmed
        if segs:
            lead, tail = self._ghosts()
            f, l = segs[0], segs[-1]
            fsp, lsp = f.get("speed") or 1, l.get("speed") or 1
            ghosts = ((QRectF(rects[0][0] - lead * pps, TRACK_Y, lead * pps, TRACK_H), f["src"], rects[0][0] - f["start"] / fsp * pps, fsp),
                      (QRectF(rects[-1][1], TRACK_Y, tail * pps, TRACK_H), l["src"], rects[-1][0] - l["start"] / lsp * pps, lsp))
            for gr, si, origin, sp in ghosts:
                if gr.width() < 1:
                    continue
                path = QPainterPath()
                path.addRoundedRect(gr, 7, 7)
                p.save()
                p.setClipPath(path)
                self._strip(p, gr, si, origin, sp, 0.14)
                p.fillRect(gr, QColor(0, 0, 0, 90))
                p.restore()
        # segments
        for i, (s, (a, b)) in enumerate(zip(segs, self.offsets())):
            xa, xb = rects[i]
            r = QRectF(xa + 1, TRACK_Y, max(4, xb - xa - 2), TRACK_H)
            path = QPainterPath()
            path.addRoundedRect(r, 7, 7)
            p.save()
            p.setClipPath(path)
            sp = s.get("speed") or 1
            self._strip(p, r, s["src"], xa - s["start"] / sp * pps, sp, 0.38)
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
            r = QRectF(self.x_of(tx["start"]), TEXT_Y, max(10, (tx["end"] - tx["start"]) * pps), TEXT_H - 2)
            p.setPen(QPen(QColor(59, 130, 246, 200), 1))
            p.setBrush(QColor(59, 130, 246, 115 if i == self.sel_text else 60))
            p.drawRoundedRect(r, 5, 5)
            p.setPen(QColor(C["text"]))
            p.setFont(self.font_s)
            p.drawText(r.adjusted(6, 0, -4, 0), Qt.AlignVCenter | Qt.AlignLeft, p.fontMetrics().elidedText("T  " + tx["text"], Qt.ElideRight, int(r.width() - 10)))
        # bookmarks
        for t, label in self.marks:
            x = self.x_of(t)
            p.setPen(QPen(QColor(C["purple"]), 2))
            p.drawLine(int(x), RULER, int(x), TRACK_Y + TRACK_H)

    # ---- interaction
    def _hit(self, pos):
        if not self.project:
            return None
        pps = self.pps()
        x, y = pos.x(), pos.y()
        if TRACK_Y <= y <= TRACK_Y + TRACK_H:
            for i, (l, r) in enumerate(self.seg_rects()):
                if abs(x - l) <= 8:
                    return ("l", i)
                if abs(x - r) <= 8:
                    return ("r", i)
                if l <= x <= r:
                    return ("seg", i)
        if TEXT_Y <= y <= TEXT_Y + TEXT_H:
            for i, tx in enumerate(self.project.get("texts") or []):
                tx0 = self.x_of(tx["start"])
                if tx0 <= x <= max(tx0 + 10, tx0 + (tx["end"] - tx["start"]) * pps):
                    return ("text", i)
        return ("ruler", None)

    def mouseMoveEvent(self, e):
        if self.drag:
            kind, i, x0, s0 = self.drag
            if kind in ("l", "r"):
                seg = self.project["segments"][i]
                ds = (e.position().x() - x0) / self.pps() * (s0.get("speed") or 1)
                if kind == "l":
                    st = min(max(0.0, s0["start"] + ds), s0["end"] - 0.1)
                    self.segment_trim.emit(i, st, seg["end"])
                else:
                    en = max(min(self.durations[s0["src"]], s0["end"] + ds), s0["start"] + 0.1)
                    self.segment_trim.emit(i, seg["start"], en)
                self.update()
            else:
                self.scrub.emit(self.t_of(e.position().x()))
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
            self.frozen = self._scale()
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
        self.scrub.emit(self.t_of(e.position().x()))
        self.update()

    def mouseReleaseEvent(self, e):
        kind = self.drag[0] if self.drag else None
        self.drag = None
        self.frozen = None
        self.update()
        if kind:
            self.drag_end.emit(kind)

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ShiftModifier or (e.angleDelta().x() and not e.angleDelta().y()):
            self.scroll_wheel.emit(d)     # shift+wheel / horizontal trackpad swipe scrolls sideways
        elif d:
            self.zoom_wheel.emit(1.15 ** (d / 120), e.position().x())
        e.accept()

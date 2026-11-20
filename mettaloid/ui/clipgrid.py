"""Virtualized clip grid (QListView + painted delegate) and async thumbnail cache."""
from collections import OrderedDict

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, QRect, QRectF, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate

from . import icons
from .theme import C
from .widgets import fmt_date, fmt_dur, fmt_size


class _Sig(QObject):
    loaded = Signal(str, QImage)


class _Load(QRunnable):
    def __init__(self, path, width, sig):
        super().__init__()
        self.path, self.width, self.sig = path, width, sig

    def run(self):
        r = QImageReader(self.path)
        sz = r.size()
        if sz.isValid() and sz.width() > self.width:
            r.setScaledSize(QSize(self.width, int(sz.height() * self.width / sz.width())))
        img = r.read()
        self.sig.loaded.emit(self.path, img)


class ThumbCache(QObject):
    """LRU cache of decoded thumbnails, bounded by memory."""
    updated = Signal(str)

    def __init__(self, max_mb=64):
        super().__init__()
        self.max_bytes = max_mb * 1024 * 1024
        self.items = OrderedDict()
        self.bytes = 0
        self.pending = set()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(2)
        self.sig = _Sig()
        self.sig.loaded.connect(self._loaded)

    def get(self, path, width=480):
        if not path:
            return None
        pm = self.items.get(path)
        if pm is not None:
            self.items.move_to_end(path)
            return pm
        if path not in self.pending:
            self.pending.add(path)
            self.pool.start(_Load(path, width, self.sig))
        return None

    def _loaded(self, path, img):
        self.pending.discard(path)
        if img.isNull():
            return
        pm = QPixmap.fromImage(img)
        self.items[path] = pm
        self.bytes += pm.width() * pm.height() * 4
        while self.bytes > self.max_bytes and len(self.items) > 1:
            _, old = self.items.popitem(last=False)
            self.bytes -= old.width() * old.height() * 4
        self.updated.emit(path)

    def clear(self):
        self.items.clear()
        self.bytes = 0


THUMBS = ThumbCache()
TYPE_LABEL = {"session": "Session", "screenshot": "Screenshot", "import": "Imported", "export": "Edited"}


class ClipModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.clips = []
        self.selected = set()
        THUMBS.updated.connect(self._thumb)

    def set_clips(self, clips):
        self.beginResetModel()
        self.clips = list(clips)
        ids = {c["id"] for c in self.clips}
        self.selected &= ids
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.clips)

    def data(self, idx, role=Qt.DisplayRole):
        if role == Qt.UserRole:
            return self.clips[idx.row()]
        if role == Qt.ToolTipRole:
            return self.clips[idx.row()]["title"]
        return None

    def _thumb(self, path):
        for i, c in enumerate(self.clips):
            if c.get("thumb") == path:
                ix = self.index(i)
                self.dataChanged.emit(ix, ix)


class ClipDelegate(QStyledItemDelegate):
    def __init__(self, model, card_w=250):
        super().__init__()
        self.model = model
        self.card_w = card_w
        self.f_title = QFont("Segoe UI", 10)
        self.f_title.setWeight(QFont.DemiBold)
        self.f_meta = QFont("Segoe UI", 8)
        self.f_badge = QFont("Segoe UI", 8)
        self.f_badge.setBold(True)

    def sizeHint(self, opt, idx):
        w = self.card_w
        return QSize(w + 16, int(w * 9 / 16) + 60)

    def paint(self, p, opt, idx):
        c = idx.data(Qt.UserRole)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = opt.rect.adjusted(8, 8, -8, -8)
        tw = r.width()
        th = int(tw * 9 / 16)
        tr = QRectF(r.x(), r.y(), tw, th)
        path = QPainterPath()
        path.addRoundedRect(tr, 12, 12)
        p.setClipPath(path)
        p.fillRect(tr, QColor(C["bg3"]))
        pm = THUMBS.get(c.get("thumb"))
        if pm:
            s = max(tw / pm.width(), th / pm.height())
            w, h = pm.width() * s, pm.height() * s
            p.drawPixmap(QRectF(tr.x() + (tw - w) / 2, tr.y() + (th - h) / 2, w, h), pm, QRectF(pm.rect()))
        else:
            ic = icons.pixmap("image" if c["type"] == "screenshot" else "film", C["text3"], 26)
            p.drawPixmap(int(tr.center().x() - 13), int(tr.center().y() - 13), ic)
        p.setClipping(False)
        hover = opt.state & QStyle.State_MouseOver
        sel = c["id"] in self.model.selected
        p.setPen(QPen(QColor(C["accent"]) if sel else QColor("#454a5c" if hover else C["line"]), 2 if sel else 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(tr.adjusted(0.5, 0.5, -0.5, -0.5), 12, 12)

        def pill(text, x, y, fg="#ffffff", right=False):
            p.setFont(self.f_badge)
            fm = p.fontMetrics()
            w = fm.horizontalAdvance(text) + 14
            rr = QRectF(x - w if right else x, y, w, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 190))
            p.drawRoundedRect(rr, 6, 6)
            p.setPen(QColor(fg))
            p.drawText(rr, Qt.AlignCenter, text)
        if c.get("duration"):
            pill(fmt_dur(c["duration"]), tr.right() - 7, tr.bottom() - 27, right=True)
        if c.get("event"):
            pill("⚡ " + c["event"], tr.x() + 7, tr.bottom() - 27, C["accent2"])
        elif TYPE_LABEL.get(c["type"]):
            pill(TYPE_LABEL[c["type"]], tr.x() + 7, tr.bottom() - 27)
        if c.get("favorite"):
            p.drawPixmap(int(tr.right() - 24), int(tr.y() + 7), icons.pixmap("star", C["accent"], 16, fill="currentColor"))
        if sel or (hover and self.model.selected):
            box = QRectF(tr.x() + 8, tr.y() + 8, 22, 22)
            p.setPen(QPen(QColor(C["accent"] if sel else "#ffffff"), 2))
            p.setBrush(QColor(C["accent"]) if sel else QColor(0, 0, 0, 90))
            p.drawRoundedRect(box, 6, 6)
            if sel:
                p.drawPixmap(int(box.x() + 3), int(box.y() + 3), icons.pixmap("check", C["ink"], 16, stroke=3))
        # text
        p.setPen(QColor(C["text"]))
        p.setFont(self.f_title)
        fm = p.fontMetrics()
        p.drawText(QRect(r.x() + 2, int(tr.bottom()) + 8, tw - 4, 20), Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(c["title"], Qt.ElideRight, tw - 4))
        p.setFont(self.f_meta)
        p.setPen(QColor(C["text3"]))
        meta = f"{c['game']['name'] if c.get('game') else 'Desktop'}  ·  {fmt_date(c['createdAt'])}"
        fm = p.fontMetrics()
        size = fmt_size(c.get("size"))
        p.drawText(QRect(r.x() + 2, int(tr.bottom()) + 28, tw - 70, 18), Qt.AlignLeft | Qt.AlignVCenter, fm.elidedText(meta, Qt.ElideRight, tw - 70))
        p.drawText(QRect(r.x(), int(tr.bottom()) + 28, tw - 2, 18), Qt.AlignRight | Qt.AlignVCenter, size)
        p.restore()


class ClipGrid(QListView):
    opened = Signal(dict)
    selection_changed = Signal(set)
    context = Signal(dict, object)

    def __init__(self, card_w=250, selectable=True):
        super().__init__()
        self.model_ = ClipModel()
        self.delegate = ClipDelegate(self.model_, card_w)
        self.setModel(self.model_)
        self.setItemDelegate(self.delegate)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListView.Batched)
        self.setBatchSize(60)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setSpacing(0)
        self.selectable = selectable
        self.last_row = None
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda pos: self._ctx(pos))

    def set_clips(self, clips):
        self.model_.set_clips(clips)

    def set_card_width(self, w):
        self.delegate.card_w = w
        self.model_.layoutChanged.emit()

    def _ctx(self, pos):
        ix = self.indexAt(pos)
        if ix.isValid():
            self.context.emit(ix.data(Qt.UserRole), self.viewport().mapToGlobal(pos))

    def mouseReleaseEvent(self, e):
        ix = self.indexAt(e.position().toPoint())
        if not ix.isValid() or e.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(e)
        c = ix.data(Qt.UserRole)
        r = self.visualRect(ix).adjusted(8, 8, -8, -8)
        in_check = r.x() + 6 <= e.position().x() <= r.x() + 34 and r.y() + 6 <= e.position().y() <= r.y() + 34
        mods = e.modifiers()
        if self.selectable and (self.model_.selected or mods & (Qt.ControlModifier | Qt.ShiftModifier) or in_check):
            sel = self.model_.selected
            if mods & Qt.ShiftModifier and self.last_row is not None:
                a, b = sorted((self.last_row, ix.row()))
                for i in range(a, b + 1):
                    sel.add(self.model_.clips[i]["id"])
            elif c["id"] in sel:
                sel.discard(c["id"])
            else:
                sel.add(c["id"])
            self.last_row = ix.row()
            self.viewport().update()
            self.selection_changed.emit(set(sel))
        else:
            self.opened.emit(c)

    def select_all(self, on=True):
        self.model_.selected = {c["id"] for c in self.model_.clips} if on else set()
        self.viewport().update()
        self.selection_changed.emit(set(self.model_.selected))

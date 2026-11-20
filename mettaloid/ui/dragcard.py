"""Floating card shown when an export finishes: drag it into Discord (or any app / folder) to drop the file there."""
import os

from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
from PySide6.QtGui import QDrag, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QVBoxLayout

from . import icons
from .theme import C
from .widgets import btn, fmt_size


class DragCard(QFrame):
    def __init__(self, parent, backend):
        super().__init__(parent)
        self.backend = backend
        self.path = None
        self.press = None
        self.setObjectName("dragcard")
        self.setFixedSize(320, 86)
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("Drag into Discord, a chat or a folder")
        self.setStyleSheet(f"#dragcard {{ background: {C['bg3']}; border: 1px solid {C['line2']}; border-left: 3px solid {C['accent']}; border-radius: 10px; }}"
                           " #dragcard QLabel { border: 0; background: transparent; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 10, 8, 10)
        lay.setSpacing(10)
        self.thumb = QLabel()
        self.thumb.setFixedSize(96, 54)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(f"background: {C['bg2']}; border-radius: 6px;")
        lay.addWidget(self.thumb)
        txt = QVBoxLayout()
        txt.setSpacing(2)
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: 700;")
        self.sub = QLabel()
        self.sub.setStyleSheet(f"color: {C['text2']}; font-size: 11px;")
        hint = QLabel("Drag me into Discord")
        hint.setStyleSheet(f"color: {C['accent']}; font-size: 11px; font-weight: 600;")
        for w in (self.title, self.sub, hint):
            txt.addWidget(w)
        lay.addLayout(txt, 1)
        side = QVBoxLayout()
        side.setSpacing(4)
        side.addWidget(btn("", "x", self.hide, ghost=True, sm=True, icon_size=12, tip="Dismiss"))
        side.addWidget(btn("", "copy", self._copy, ghost=True, sm=True, icon_size=12, tip="Copy file (paste into Discord)"))
        side.addWidget(btn("", "folder", lambda: self.path and backend.show_in_folder(self.path), ghost=True, sm=True, icon_size=12, tip="Show in folder"))
        lay.addLayout(side)
        self.hide()

    def show_file(self, path, title=None, size=None, thumb=None):
        if not path or not os.path.exists(path):
            return
        self.path = path
        name = title or os.path.basename(path)
        self.title.setText(self.title.fontMetrics().elidedText(name, Qt.ElideRight, 150))
        self.setToolTip(f"{os.path.basename(path)}\nDrag into Discord, a chat or a folder")
        self.sub.setText(f"{os.path.splitext(path)[1][1:].upper()} · {fmt_size(size or os.path.getsize(path))}")
        pm = QPixmap(thumb) if thumb and os.path.exists(thumb) else QPixmap()
        if pm.isNull():
            self.thumb.setPixmap(icons.icon("film", C["text2"], 22).pixmap(22, 22))
        else:
            self.thumb.setPixmap(pm.scaled(96, 54, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation).copy(0, 0, 96, 54))
        self.show()
        self.reposition()
        self.raise_()

    def hideEvent(self, e):
        super().hideEvent(e)
        self._make_room(0)

    def _make_room(self, gap):
        t = getattr(self.parentWidget().window(), "toaster", None)
        if t:
            t.bottom_gap = gap
            t.reposition()

    def reposition(self):
        p = self.parentWidget()
        self.move(p.width() - self.width() - 20, p.height() - self.height() - 20)
        if self.isVisible():
            self._make_room(self.height() + 10)

    def _copy(self):
        if self.path and os.path.exists(self.path):
            self.backend.copy_file(self.path)
            self.parentWidget().window().toaster.show_toast({"title": "Copied", "subtitle": "Paste into Discord"})

    # ---- drag source
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.press = e.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseReleaseEvent(self, e):
        self.press = None
        self.setCursor(Qt.OpenHandCursor)

    def mouseMoveEvent(self, e):
        if self.press is None or not (e.buttons() & Qt.LeftButton) or not self.path:
            return
        if (e.position().toPoint() - self.press).manhattanLength() < QApplication.startDragDistance():
            return
        if not os.path.exists(self.path):
            self.hide()
            return
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(self.path)])
        drag = QDrag(self)
        drag.setMimeData(md)
        pm = self.grab()
        drag.setPixmap(pm.scaledToWidth(220, Qt.SmoothTransformation))
        drag.setHotSpot(QPoint(20, 20))
        self.press = None
        self.setCursor(Qt.OpenHandCursor)
        drag.exec(Qt.CopyAction)

"""Pages. Each page is a QWidget subclass taking the MainWindow; optional on_show(**params) / on_hide()."""
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ..widgets import label


class Page(QWidget):
    def __init__(self, win, title=""):
        super().__init__()
        self.win = win
        self.backend = win.backend
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.root.setSpacing(0)
        self.topbar = QWidget()
        self.topbar.setObjectName("topbar")
        self.topbar.setFixedHeight(54)
        self.top = QHBoxLayout(self.topbar)
        self.top.setContentsMargins(22, 0, 22, 0)
        self.top.setSpacing(10)
        self.title = label(title, "h1")
        self.top.addWidget(self.title)
        self.root.addWidget(self.topbar)

    def go(self, route, **params):
        self.win.go(route, **params)

    def toast(self, title, subtitle="", kind=""):
        self.win.toaster.show_toast({"title": title, "subtitle": subtitle, "kind": kind})

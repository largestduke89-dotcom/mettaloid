"""Main window: sidebar navigation + lazily created pages."""
import ctypes
import importlib

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from ..core import paths
from . import icons
from .theme import C
from .widgets import Badge, Toaster, bar, label

NAV = [("home", "Home", "home"), ("library", "Library", "library"), ("compressor", "Compressor", "compress"),
       ("findframe", "Find Frame", "target"), ("games", "Games", "games"), ("settings", "Settings", "settings")]
# page name → (module, class); pages are imported/created on first visit
PAGES = {
    "home": ("home", "HomePage"), "library": ("library", "LibraryPage"), "clip": ("viewer", "ViewerPage"), "editor": ("editor", "EditorPage"),
    "compressor": ("compressor", "CompressorPage"), "findframe": ("findframe_page", "FindFramePage"), "games": ("games_page", "GamesPage"),
    "settings": ("settings_page", "SettingsPage"),
}
NAV_FOR = {"clip": "library", "editor": "library"}


def dark_titlebar(w):
    try:
        v = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(w.winId()), 20, ctypes.byref(v), 4)
        col = ctypes.c_int(0x00120E0D)  # caption color BGR of #0d0e12
        ctypes.windll.dwmapi.DwmSetWindowAttribute(int(w.winId()), 35, ctypes.byref(col), 4)
    except Exception:  # noqa: BLE001
        pass


class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self.app = app
        self.backend = app.backend
        self.setWindowTitle("Mettaloid")
        self.setWindowIcon(QIcon(paths.asset("icon.ico")))
        self.resize(1400, 880)
        self.setMinimumSize(1040, 640)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._sidebar())
        right = QVBoxLayout()
        right.setSpacing(0)
        self.banner = QLabel()
        self.banner.setStyleSheet("background: rgba(239,68,68,0.12); border-bottom: 1px solid rgba(239,68,68,0.3); padding: 8px 22px; font-size: 12px;")
        self.banner.hide()
        right.addWidget(self.banner)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        lay.addLayout(right, 1)
        self.pages = {}
        self.current = None
        self.toaster = Toaster(root)
        b = self.backend
        b.toast.connect(self.toaster.show_toast)
        b.status.connect(self._status)
        b.job.connect(self._job)
        b.hotkeys_failed.connect(self._hk_failed)
        b.navigate.connect(lambda r: self.go(r))
        self._status(dict(b.obs.status))
        self._hk_failed(b.hotkeys.failed)
        dark_titlebar(self)
        self.go("home")

    # ---------------------------------------------------------------- sidebar
    def _sidebar(self):
        sb = QWidget()
        sb.setObjectName("sidebar")
        sb.setFixedWidth(224)
        v = QVBoxLayout(sb)
        v.setContentsMargins(10, 12, 10, 12)
        v.setSpacing(2)
        brand = QHBoxLayout()
        mark = QLabel("M")
        mark.setFixedSize(30, 30)
        mark.setAlignment(Qt.AlignCenter)
        mark.setStyleSheet(f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {C['accent2']}, stop:1 #e09400); color: {C['ink']}; border-radius: 9px; font-weight: 900; font-size: 15px;")
        brand.addWidget(mark)
        nm = QLabel("Mettaloid")
        nm.setStyleSheet("font-weight: 750; font-size: 15px;")
        brand.addWidget(nm)
        brand.addStretch(1)
        brand.setContentsMargins(6, 4, 0, 14)
        v.addLayout(brand)
        self.nav = {}
        grp = QButtonGroup(self)
        for key, text, ic in NAV:
            b = QPushButton("  " + text)
            b.setProperty("nav", True)
            b.setCheckable(True)
            b.setIcon(icons.icon(ic, C["text2"], 18, active=C["accent"]))
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.go(k))
            grp.addButton(b)
            v.addWidget(b)
            self.nav[key] = b
        v.addStretch(1)
        self.jobs_card = QFrame()
        self.jobs_card.setObjectName("inset")
        jl = QVBoxLayout(self.jobs_card)
        jl.setContentsMargins(10, 8, 10, 10)
        self.jobs_lb = label("", "dim")
        self.jobs_bar = bar()
        jl.addWidget(self.jobs_lb)
        jl.addWidget(self.jobs_bar)
        self.jobs_card.hide()
        v.addWidget(self.jobs_card)
        st = QFrame()
        st.setObjectName("inset")
        st.setCursor(Qt.PointingHandCursor)
        st.mousePressEvent = lambda e: self.go("home")
        sl = QVBoxLayout(st)
        sl.setContentsMargins(10, 8, 10, 10)
        row = QHBoxLayout()
        self.st_badge = Badge("Starting…", "warn")
        row.addWidget(self.st_badge)
        row.addStretch(1)
        self.st_mic = QLabel()
        row.addWidget(self.st_mic)
        sl.addLayout(row)
        self.st_game = label("Waiting for game", "dim")
        sl.addWidget(self.st_game)
        v.addSpacing(8)
        v.addWidget(st)
        return sb

    def _status(self, s):
        if s["recording"]:
            self.st_badge.set("● REC", "rec")
        elif s["replayActive"]:
            self.st_badge.set("● Replay on", "live")
        elif s["obsState"] == "ready":
            self.st_badge.set("Standby")
        elif s["obsState"] in ("starting", "preparing", "idle"):
            self.st_badge.set("Starting OBS…", "warn")
        else:
            self.st_badge.set("Recorder error", "rec")
        self.st_mic.setPixmap(icons.pixmap("micOff" if s["micMuted"] else "mic", C["red"] if s["micMuted"] else C["text2"], 15))
        self.st_game.setText(s["game"]["name"] if s.get("game") else "Waiting for game")

    def _job(self, j):
        running = [x for x in self.backend.list_jobs() if x["status"] == "running"]
        if running:
            self.jobs_card.show()
            self.jobs_lb.setText(f"{len(running)} export{'s' if len(running) > 1 else ''} · {running[0]['phase']} {round(running[0]['progress'] * 100)}%")
            self.jobs_bar.setValue(int(running[0]["progress"] * 1000))
        else:
            self.jobs_card.hide()

    def _hk_failed(self, failed):
        if failed:
            self.banner.setText("Hotkey " + ", ".join(f["accelerator"] for f in failed) + " could not be registered (used by another app). Change it in Settings → Hotkeys.")
            self.banner.show()
        else:
            self.banner.hide()

    # ---------------------------------------------------------------- routing
    def go(self, route, **params):
        if ":" in route and not params:
            route, _, arg = route.partition(":")
            params = {"id": arg}
        if route not in PAGES:
            route = "home"
        page = self.pages.get(route)
        if page is None:
            mod, cls = PAGES[route]
            page = getattr(importlib.import_module(f"mettaloid.ui.pages.{mod}"), cls)(self)
            page.setObjectName("page")
            self.pages[route] = page
            self.stack.addWidget(page)
        if self.current and self.current is not page and hasattr(self.current, "on_hide"):
            self.current.on_hide()
        self.current = page
        self.stack.setCurrentWidget(page)
        if hasattr(page, "on_show"):
            page.on_show(**params)
        nav = NAV_FOR.get(route, route)
        if nav in self.nav:
            self.nav[nav].setChecked(True)

    # ---------------------------------------------------------------- window behaviour
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.toaster.reposition()

    def closeEvent(self, e):
        if self.backend.settings.get()["general"]["closeToTray"] and not self.app.quitting:
            e.ignore()
            self.hide()
            self.app.window_hidden()
        else:
            for p in self.pages.values():
                if hasattr(p, "on_hide"):
                    p.on_hide()
            e.accept()

    def hideEvent(self, e):
        if self.current and hasattr(self.current, "on_hide"):
            self.current.on_hide()
        super().hideEvent(e)


_ = QTimer

"""Application: Qt app, backend, tray, overlay, window lifecycle (UI freed when hidden)."""
import gc
import os
import sys

from PySide6.QtCore import QLockFile, QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .core import paths
from .core.backend import Backend
from .ui.theme import QSS

import hashlib
# one instance per user-data folder (lets tests run side by side)
SINGLE_KEY = "Mettaloid-" + hashlib.md5(paths.USER_DATA.lower().encode()).hexdigest()[:10]


class App:
    def __init__(self, argv):
        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        self.qt = QApplication(argv)
        self.qt.setApplicationName("Mettaloid")
        self.qt.setQuitOnLastWindowClosed(False)
        self.qt.setStyle("Fusion")
        self.qt.setStyleSheet(QSS)
        self.qt.setWindowIcon(QIcon(paths.asset("icon.ico")))
        self.quitting = False
        self.window = None
        self.free_timer = QTimer(singleShot=True, timeout=self.free_ui)
        from .ui import bg
        bg._get()  # create the UI-thread bridge before any worker thread uses it
        self.backend = Backend()
        from .ui.overlay import Overlay, Sounds
        self.overlay = None
        self.Overlay = Overlay
        self.sounds = Sounds()
        self.overlay_idle = QTimer(singleShot=True, timeout=self._drop_overlay)
        self.backend.toast.connect(self.on_toast)
        self.backend.status.connect(lambda s: self.update_tray())
        self.tray = QSystemTrayIcon(QIcon(paths.asset("icon.ico")))
        self.tray.activated.connect(lambda r: self.show_window() if r in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger) else None)
        self.tray_menu = QMenu()
        self.tray.setContextMenu(self.tray_menu)
        self.update_tray()
        self.tray.show()

    # ---------------------------------------------------------------- single instance
    def claim_instance(self):
        sock = QLocalSocket()
        sock.connectToServer(SINGLE_KEY)
        if sock.waitForConnected(300):
            sock.write(b"show")
            sock.waitForBytesWritten(300)
            return False
        QLocalServer.removeServer(SINGLE_KEY)
        self.server = QLocalServer()
        self.server.listen(SINGLE_KEY)
        self.server.newConnection.connect(lambda: (self.server.nextPendingConnection(), self.show_window()))
        return True

    # ---------------------------------------------------------------- window
    def show_window(self, route=None):
        self.free_timer.stop()
        if self.window is None:
            from .ui.mainwindow import MainWindow
            self.window = MainWindow(self)
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        if route:
            self.window.go(route)

    def window_hidden(self):
        if self.backend.settings.get()["performance"]["freeUiWhenHidden"]:
            self.free_timer.start(45000)

    def free_ui(self):
        """Destroy the whole UI while in the tray; recreated on demand."""
        if self.window is None or self.window.isVisible():
            return
        if any(j["status"] == "running" for j in self.backend.list_jobs()):
            self.free_timer.start(30000)
            return
        w, self.window = self.window, None
        for sig in (self.backend.toast, self.backend.status, self.backend.job, self.backend.hotkeys_failed, self.backend.navigate,
                    self.backend.library_changed, self.backend.clip_new, self.backend.ff_job, self.backend.ff_match, self.backend.ff_thumb,
                    self.backend.ff_changed, self.backend.updater, self.backend.settings_changed):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        self.backend.toast.connect(self.on_toast)
        self.backend.status.connect(lambda s: self.update_tray())
        w.deleteLater()
        from .ui.clipgrid import THUMBS
        THUMBS.clear()
        QTimer.singleShot(500, gc.collect)
        QTimer.singleShot(800, self._trim_ws)

    @staticmethod
    def _trim_ws():
        try:
            import ctypes
            ctypes.windll.psapi.EmptyWorkingSet(ctypes.windll.kernel32.GetCurrentProcess())
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- overlay / sounds
    def on_toast(self, t):
        o = self.backend.settings.get()["overlay"]
        if o["sound"] and t.get("sound") is not None:
            self.sounds.play(t.get("sound") or "clip", o["soundVolume"])
        if o["enabled"] and not (self.window and self.window.isActiveWindow()):
            if self.overlay is None:
                self.overlay = self.Overlay(self.backend.settings)
            self.overlay.show_toast(t)
            self.overlay_idle.start(120000)

    def _drop_overlay(self):
        if self.overlay:
            self.overlay.deleteLater()
            self.overlay = None

    # ---------------------------------------------------------------- tray
    def update_tray(self):
        st = self.backend.obs.status
        g = st.get("game")
        state = (f"{g['name']} — {'Replay on' if st['replayActive'] else 'Replay off'}" if g else "Replay buffer on" if st["replayActive"] else "Waiting for game")
        self.tray.setToolTip(f"Mettaloid\n{state}{chr(10) + '● Recording' if st['recording'] else ''}")
        m = self.tray_menu
        m.clear()
        m.addAction(state).setEnabled(False)
        m.addSeparator()
        m.addAction("Open Mettaloid", self.show_window)
        clips = self.backend.settings.get()["hotkeys"]["clips"]
        a = m.addAction(f"Save clip ({clips[0]['accelerator'] if clips else ''})", lambda: self.backend.clip())
        a.setEnabled(st["replayActive"])
        a = m.addAction("Stop recording" if st["recording"] else "Start recording", self.backend.toggle_recording)
        a.setEnabled(st["obsState"] == "ready")
        a = m.addAction("Pause replay buffer" if st["replayActive"] else "Start replay buffer", self.backend.toggle_replay)
        a.setEnabled(st["obsState"] == "ready")
        m.addAction("Unmute microphone" if st["micMuted"] else "Mute microphone", self.backend.toggle_mic)
        m.addSeparator()
        m.addAction("Open clip folder", lambda: os.startfile(self.backend.settings.get()["general"]["clipFolder"]))
        m.addAction("Settings", lambda: self.show_window("settings"))
        m.addSeparator()
        m.addAction("Quit Mettaloid", self.quit)

    def quit(self, update_hidden=None):
        self.quitting = True
        if self.window:
            self.window.close()
        self.tray.hide()
        self.backend.shutdown()
        self.backend.updater_svc.install_on_quit(hidden=bool(update_hidden))
        self.qt.quit()

    def run_onboarding(self, parent=None):
        from .ui.onboarding import OnboardingDialog
        dlg = OnboardingDialog(self.backend.settings, parent)
        from .ui.mainwindow import dark_titlebar
        dark_titlebar(dlg)
        dlg.exec()
        res = getattr(dlg, "result_settings", None)
        if res:
            self.backend.update_settings(res)
            if parent is not None and self.window:
                self.window.toaster.show_toast({"title": "Setup applied", "subtitle": "Your choices are saved."})
        return True

    # ---------------------------------------------------------------- auto-update
    def busy(self):
        """True while installing an update would interrupt the user."""
        st = self.backend.obs.status
        if st.get("game") or st.get("recording"):
            return True
        if any(j["status"] == "running" for j in self.backend.list_jobs()):
            return True
        if any(j["status"] == "running" for j in self.backend.findframe.list_jobs()):
            return True
        if self.window is not None and self.window.isVisible() and self.window.isActiveWindow():
            return True  # the user is using Mettaloid right now
        return False

    def maybe_install_update(self):
        u = self.backend.updater_svc
        s = self.backend.settings.get()["updates"]
        if u.state.get("status") != "ready" or not s.get("autoInstall") or self.busy():
            return
        hidden = self.window is None or not self.window.isVisible()
        import logging
        logging.getLogger("mettaloid").info("installing update %s automatically (idle, hidden=%s)", u.state.get("available"), hidden)
        self.quit(update_hidden=hidden)

    def announce_version(self):
        from . import VERSION
        s = self.backend.settings.get()
        last = s.get("lastVersion") or ""
        if last and last != VERSION:
            self.backend.notify(f"Updated to Mettaloid {VERSION}", f"from {last}", "", "mark")
        if last != VERSION:
            self.backend.settings.update({"lastVersion": VERSION})
        self.backend.updater_svc.cleanup()

    def run(self, hidden=False):
        if not self.claim_instance():
            return 0
        # First run: ask about everything before anything starts recording.
        if not self.backend.settings.get().get("onboarded") and not os.environ.get("MT_SKIP_ONBOARDING"):
            if not self.run_onboarding():
                return 0
            hidden = False
        # MT_NO_RECORDER=1: UI-only mode for development/tests (no OBS, hotkeys, detection)
        if not os.environ.get("MT_NO_RECORDER"):
            self.backend.start()
        else:
            import threading
            threading.Thread(target=self.backend.library.scan, daemon=True).start()
            self.backend.updater_svc.schedule()
        self.announce_version()
        # check once a minute whether a downloaded update can be installed without disturbing anyone
        self.update_timer = QTimer(interval=60000, timeout=self.maybe_install_update)
        self.update_timer.start()
        s = self.backend.settings.get()["general"]
        if not (hidden or s["startMinimized"]):
            self.show_window()
        snap = os.environ.get("MT_SNAPSHOT_DIR")
        if snap:
            from .ui.snapshot import run_snapshots
            QTimer.singleShot(4000, lambda: run_snapshots(self, snap))
        return self.qt.exec()


_ = QLockFile
_ = sys

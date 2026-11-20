"""'Update available' prompt. Nothing is installed unless the user clicks Update now."""
from PySide6.QtWidgets import QTextBrowser

from .. import VERSION
from .theme import C
from .widgets import Dialog, bar, btn, label


class UpdatePrompt(Dialog):
    def __init__(self, win):
        super().__init__(win, "Update available", 560)
        self.win = win
        self.backend = win.backend
        self.svc = self.backend.updater_svc
        st = self.svc.state
        size = st.get("size")
        self.body.addWidget(label(f"Mettaloid <b>{st.get('available')}</b> is available — you have {VERSION}."
                                  + (f" Download size {size / 1e6:.0f} MB." if size else ""), wrap=True))
        notes = QTextBrowser()
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(st.get("notes") or "No release notes.")
        notes.setMinimumHeight(220)
        notes.setStyleSheet(f"QTextBrowser {{ background: {C['bg3']}; border: 1px solid {C['line']}; border-radius: 10px; padding: 8px; }}")
        self.body.addWidget(notes)
        self.status = label("Mettaloid closes, updates and reopens by itself — it takes about a minute.", "dim", wrap=True)
        self.body.addWidget(self.status)
        self.progress = bar()
        self.progress.hide()
        self.body.addWidget(self.progress)
        self.skip_b = self.add_button(btn("Skip this version", None, self.skip, ghost=True), left=True)
        self.later_b = self.add_button(btn("Later", None, self.reject))
        self.update_b = self.add_button(btn("Update now", "download", self.update_now, primary=True))
        self.update_b.setDefault(True)
        self.backend.updater.connect(self.on_state)

    def skip(self):
        self.backend.update_settings({"updates": {"skipVersion": self.svc.state.get("available") or ""}})
        self.reject()

    def update_now(self):
        self.update_b.setEnabled(False)
        self.skip_b.setEnabled(False)
        self.later_b.setText("Hide")
        self.progress.show()
        self.status.setText("Downloading…")
        self.svc.update_now()

    def on_state(self, st):
        try:
            s = st.get("status")
            if s == "downloading":
                self.progress.setValue(int((st.get("progress") or 0) * 1000))
                self.status.setText(f"Downloading… {round((st.get('progress') or 0) * 100)}%")
            elif s == "ready" and self.svc.install_when_ready:
                self.progress.setValue(1000)
                self.status.setText("Installing — Mettaloid will reopen in a moment…")
            elif s == "error":
                self.status.setText(f"Update failed: {st.get('error')}")
                self.update_b.setEnabled(True)
                self.svc.install_when_ready = False
        except RuntimeError:
            pass  # dialog already closed

    def done(self, r):
        try:
            self.backend.updater.disconnect(self.on_state)
        except (RuntimeError, TypeError):
            pass
        super().done(r)

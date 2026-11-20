"""Updates tab: current version, available update + what's new, release history. Never installs on its own."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QTextBrowser, QVBoxLayout, QWidget

from ... import UPDATE_REPO, VERSION
from ..theme import C
from ..widgets import Badge, Section, Seg, Toggle, bar, btn, hbox, label, scroll_page
from . import Page


class UpdatesPage(Page):
    def __init__(self, win):
        super().__init__(win, "Updates")
        self.svc = self.backend.updater_svc
        sa, body = scroll_page(max_width=860)
        self.root.addWidget(sa, 1)

        card = QFrame()
        card.setObjectName("hero")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(22, 20, 22, 20)
        cl.setSpacing(10)
        top = QHBoxLayout()
        txt = QVBoxLayout()
        self.headline = QLabel()
        self.headline.setStyleSheet("font-size: 20px; font-weight: 800;")
        txt.addWidget(self.headline)
        self.sub = label("", "muted", wrap=True)
        txt.addWidget(self.sub)
        top.addLayout(txt, 1)
        self.check_b = btn("Check now", "refresh", self.svc.check)
        self.update_b = btn("Update now", "download", self.update_now, primary=True, lg=True)
        top.addWidget(self.check_b, 0, Qt.AlignTop)
        top.addWidget(self.update_b, 0, Qt.AlignTop)
        cl.addLayout(top)
        self.progress = bar()
        cl.addWidget(self.progress)
        self.notes_title = label("WHAT'S NEW", "h3")
        cl.addWidget(self.notes_title)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMinimumHeight(160)
        self.notes.setStyleSheet(f"QTextBrowser {{ background: {C['bg3']}; border: 1px solid {C['line']}; border-radius: 10px; padding: 8px; }}")
        cl.addWidget(self.notes)
        self.skip_b = btn("Skip this version", None, self.skip, ghost=True, sm=True)
        cl.addWidget(self.skip_b, 0, Qt.AlignLeft)
        body.addWidget(card)

        sec = Section("Preferences")
        s = self.backend.settings.get()["updates"]
        self.auto = Toggle(s["autoCheck"], lambda v: self.backend.update_settings({"updates": {"autoCheck": v}}))
        sec.row("Tell me when an update is available", "Checks GitHub every few hours and shows a prompt. Nothing installs unless you click Update now.", self.auto)
        self.channel = Seg([("latest", "Stable"), ("beta", "Beta")], s["channel"], on_change=lambda v: self.backend.update_settings({"updates": {"channel": v}}))
        sec.row("Channel", "Beta gets new features earlier.", self.channel)
        body.addWidget(sec)

        body.addWidget(hbox(label("RELEASE HISTORY", "h3"), "stretch",
                            btn("Open on GitHub", "external", lambda: self.backend.open_path(f"https://github.com/{UPDATE_REPO}/releases"), ghost=True, sm=True)))
        self.history = QVBoxLayout()
        self.history.setSpacing(10)
        hw = QWidget()
        hw.setLayout(self.history)
        body.addWidget(hw)
        body.addStretch(1)
        self.backend.updater.connect(self.render)
        self.render(self.svc.state)

    def on_show(self, **_):
        st = self.svc.state.get("status")
        if st in ("idle", "latest", "error") and not self.svc.state.get("checkedAt"):
            self.svc.check()
        self.render(self.svc.state)

    def update_now(self):
        if self.svc.state.get("status") in ("available", "ready", "error"):
            self.svc.update_now()

    def skip(self):
        self.backend.update_settings({"updates": {"skipVersion": self.svc.state.get("available") or ""}})
        self.toast("Version skipped", "You'll be told about the next one.")

    def render(self, st):
        s = st.get("status")
        avail = st.get("available")
        head = {
            "dev": f"Mettaloid {VERSION} (development build)",
            "checking": "Checking for updates…",
            "available": f"Mettaloid {avail} is available",
            "downloading": f"Downloading Mettaloid {avail}…",
            "ready": f"Mettaloid {avail} is ready to install",
            "error": "Couldn't check for updates",
        }.get(s, f"You're up to date — Mettaloid {VERSION}")
        self.headline.setText(head)
        sub = {
            "dev": "Updates are only installed in the released app.",
            "available": f"You have {VERSION}. Read what's new below, then click Update now — Mettaloid closes, updates and reopens by itself.",
            "downloading": f"{round((st.get('progress') or 0) * 100)}% downloaded",
            "ready": "Click Update now to install. Mettaloid will reopen in about a minute.",
            "error": st.get("error") or "",
        }.get(s, f"Last checked {st.get('checkedAt')}." if st.get("checkedAt") else "")
        self.sub.setText(sub)
        has_update = s in ("available", "downloading", "ready")
        self.update_b.setVisible(has_update)
        self.update_b.setEnabled(s != "downloading")
        self.update_b.setText("Restart & update" if s == "ready" else "Update now")
        self.check_b.setEnabled(s not in ("dev", "checking", "downloading"))
        self.progress.setVisible(s == "downloading")
        self.progress.setValue(int((st.get("progress") or 0) * 1000))
        self.notes_title.setVisible(has_update)
        self.notes.setVisible(has_update)
        self.skip_b.setVisible(s == "available")
        if has_update:
            self.notes.setMarkdown(st.get("notes") or "No release notes.")
        while self.history.count():
            it = self.history.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        rels = st.get("releases") or []
        if not rels:
            self.history.addWidget(label("Release history appears after the first check.", "dim"))
        for r in rels[:12]:
            fr = QFrame()
            fr.setObjectName("card")
            fl = QVBoxLayout(fr)
            fl.setContentsMargins(16, 12, 16, 12)
            fl.setSpacing(6)
            tag = Badge("Installed", "live") if r["version"] == VERSION else Badge("New", "warn") if has_update and _newer(r["version"]) else None
            row = [QLabel(f"<b>Mettaloid {r['version']}</b>"), label(r["date"], "dim")]
            if r.get("prerelease"):
                row.append(Badge("Beta"))
            if tag:
                row.append(tag)
            fl.addWidget(hbox(*row, "stretch"))
            notes = QLabel()
            notes.setTextFormat(Qt.MarkdownText)
            notes.setWordWrap(True)
            notes.setOpenExternalLinks(True)
            text = (r.get("notes") or "").strip()
            notes.setText(text[:1500] + ("…" if len(text) > 1500 else "") if text else "_No notes._")
            notes.setStyleSheet(f"color: {C['text2']};")
            fl.addWidget(notes)
            self.history.addWidget(fr)


def _newer(v):
    from ...core.updater import vtuple
    return vtuple(v) > vtuple(VERSION)

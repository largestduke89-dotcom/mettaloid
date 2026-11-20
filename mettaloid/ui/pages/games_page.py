"""Games: recognized games, per-game recording profiles, custom games (port of legacy Games.jsx)."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QFileDialog, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout, QLabel, QLayout, QLineEdit, QVBoxLayout, QWidget

from .. import icons
from ..theme import C
from ..widgets import Badge, Combo, Dialog, GameIcon, Section, Toggle, btn, fmt_secs_label, label, scroll_page
from .compressor import ElideLabel
from .home import BUFFER_STEPS
from . import Page

CARD_MIN_W = 250
GAP = 12
CARD_QSS = f"""
QFrame#gamecard {{ background: {C['bg2']}; border: 1px solid {C['line']}; border-radius: 12px; }}
QFrame#gamecard:hover {{ background: {C['bg3']}; border-color: {C['line2']}; }}
QFrame#gamecard[live="true"] {{ border-color: rgba(34,197,94,0.55); }}
"""


def exe_list(g):
    e = g.get("exe") or []
    return e if isinstance(e, list) else [e]


class GameCard(QFrame):
    def __init__(self, g, on_click):
        super().__init__()
        self.g = g
        self.setObjectName("gamecard")
        self.setStyleSheet(CARD_QSS)
        self.setCursor(Qt.PointingHandCursor)
        self.on_click = on_click
        self.setFixedHeight(66)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(12)
        lay.addWidget(GameIcon(g["name"], 42))
        txt = QVBoxLayout()
        txt.setSpacing(1)
        self.name = ElideLabel(g["name"], "font-weight: 650;")
        txt.addWidget(self.name)
        self.sub = ElideLabel("", f"color: {C['text3']}; font-size: 12px;")
        txt.addWidget(self.sub)
        lay.addLayout(txt, 1)
        self.live = Badge("● Live", "live")
        lay.addWidget(self.live, 0, Qt.AlignVCenter)

    def set_state(self, count, has_profile, ignored, live):
        g = self.g
        sub = "Custom" if g.get("custom") else (g.get("category") or "Game")
        if count:
            sub += f" · {count} clip{'s' if count != 1 else ''}"
        if has_profile:
            sub += " · profile"
        if ignored:
            sub += " · ignored"
        self.sub.set_full(sub)
        self.live.setVisible(live)
        self.setProperty("live", live)
        self.style().unpolish(self)
        self.style().polish(self)
        if ignored:
            eff = QGraphicsOpacityEffect(self)
            eff.setOpacity(0.45)
            self.setGraphicsEffect(eff)
        else:
            self.setGraphicsEffect(None)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_click(self.g)
        super().mousePressEvent(e)


class GameGrid(QWidget):
    """Responsive grid: columns recomputed from the available width."""

    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(GAP)
        self.cards = []
        self.cols = 0

    def set_cards(self, cards):
        keep = set(map(id, cards))
        for c in self.cards:
            if id(c) not in keep:
                c.hide()
        self.cards = cards
        self.cols = 0
        self._layout(force=True)

    def _ncols(self):
        return max(1, (self.width() + GAP) // (CARD_MIN_W + GAP))

    def _layout(self, force=False):
        n = self._ncols()
        if n == self.cols and not force:
            return
        self.cols = n
        while self.grid.count():
            self.grid.takeAt(0)
        for i in range(self.grid.columnCount()):
            self.grid.setColumnStretch(i, 0)
        for i, c in enumerate(self.cards):
            self.grid.addWidget(c, i // n, i % n)
            c.show()
        for i in range(n):
            self.grid.setColumnStretch(i, 1)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()


class ProfileDialog(Dialog):
    def __init__(self, page, game):
        super().__init__(page, game["name"], 560)
        self.page, self.game = page, game
        s = page.backend.settings.get()
        perf = s["performance"]
        auto = perf.get("autoQuality")
        existing = s["games"]["profiles"].get(game["id"]) or {}
        self.p = {"enabled": False, "resolution": perf["maxResolution"] if auto else s["quality"]["resolution"],
                  "fps": perf["maxFps"] if auto else s["quality"]["fps"], "replaySeconds": s["recording"]["replaySeconds"],
                  "micEnabled": s["audio"]["micEnabled"], **existing}
        self.ignored = game["id"] in set(s["games"].get("disabled") or [])

        self.body.addWidget(label("Executable: " + ", ".join(exe_list(game)), "dim", wrap=True))
        sec = Section()
        sec.layout().setContentsMargins(0, 0, 0, 0)
        sec.row("Don't record this game", "Mettaloid will ignore it when it launches.", Toggle(self.ignored, self._set_ignored))
        self.enabled_t = Toggle(bool(self.p["enabled"]), self._set_enabled)
        sec.row("Use a custom recording profile", None, self.enabled_t)
        self.prof_rows = [
            sec.row("Max resolution" if auto else "Resolution", "Auto quality picks the best settings up to this." if auto else None,
                    Combo([("native", "Native"), ("2160", "2160p"), ("1440", "1440p"), ("1080", "1080p"), ("720", "720p")],
                          str(self.p["resolution"]), lambda v: self.p.update(resolution=v), width=150)),
            sec.row("Max frame rate" if auto else "Frame rate", None,
                    Combo([(f, f"{f} fps") for f in (30, 60, 120, 144, 165, 240)], self.p["fps"], lambda v: self.p.update(fps=v), width=150)),
            sec.row("Replay length", None, Combo([(x, fmt_secs_label(x)) for x in BUFFER_STEPS], self.p["replaySeconds"],
                                                 lambda v: self.p.update(replaySeconds=v), width=150)),
            sec.row("Record microphone", None, Toggle(bool(self.p["micEnabled"]), lambda v: self.p.update(micEnabled=v))),
        ]
        sec.setMinimumWidth(520)
        self.body.addWidget(sec)
        self.lay.setSizeConstraint(QLayout.SetFixedSize)
        if game.get("custom"):
            self.add_button(btn("Remove game", on_click=self._remove, danger=True), left=True)
            self.foot.insertStretch(1, 1)
        self.add_button(btn("Cancel", on_click=self.reject))
        ok = self.add_button(btn("Save", on_click=self._save, primary=True))
        ok.setDefault(True)
        self._show_rows()

    def _set_ignored(self, v):
        self.ignored = v

    def _set_enabled(self, v):
        self.p["enabled"] = v
        self._show_rows()

    def _show_rows(self):
        for r in self.prof_rows:
            r.setVisible(bool(self.p["enabled"]))
        if self.isVisible():
            QTimer.singleShot(0, self.adjustSize)

    def _save(self):
        s = self.page.backend.settings.get()
        dis = list(s["games"].get("disabled") or [])
        if self.ignored and self.game["id"] not in dis:
            dis.append(self.game["id"])
        elif not self.ignored:
            dis = [x for x in dis if x != self.game["id"]]
        profiles = {**s["games"]["profiles"], self.game["id"]: dict(self.p)}
        self.page.backend.update_settings({"games": {"profiles": profiles, "disabled": dis}})
        self.accept()

    def _remove(self):
        s = self.page.backend.settings.get()
        self.page.backend.update_settings({"games": {"custom": [g for g in s["games"]["custom"] if g["id"] != self.game["id"]]}})
        self.accept()


class GamesPage(Page):
    def __init__(self, win):
        super().__init__(win, "Games")
        self.count_lb = label("", "dim")
        self.top.addWidget(self.count_lb)
        self.top.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search games")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search", C["text3"], 14), QLineEdit.LeadingPosition)
        self.search.setFixedWidth(220)
        self.search.textChanged.connect(lambda _: self.refresh())
        self.top.addWidget(self.search)
        self.top.addWidget(btn(" Add a game", "plus", self.add_game, primary=True, sm=True, icon_size=14))
        self.data = None
        self.dialog = None

        sa, body = scroll_page()
        self.root.addWidget(sa, 1)

        self.cur = QFrame()
        self.cur.setObjectName("card")
        self.cur.setStyleSheet("QFrame#card { border-color: rgba(34,197,94,0.45); }")
        cl = QHBoxLayout(self.cur)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(14)
        self.cur_icon = GameIcon("", 48)
        cl.addWidget(self.cur_icon)
        ct = QVBoxLayout()
        ct.setSpacing(2)
        self.cur_name = label("", "h2")
        self.cur_sub = label("", "dim")
        ct.addWidget(self.cur_name)
        ct.addWidget(self.cur_sub)
        cl.addLayout(ct, 1)
        cl.addWidget(btn("Recording profile", on_click=self._edit_current))
        body.addWidget(self.cur)

        hint = label("Not listed? Click <b>Add a game</b> and pick its .exe. Games with a custom profile record with their own resolution, "
                     "frame rate, replay length and mic settings.", "dim", wrap=True)
        hint.setTextFormat(Qt.RichText)
        hint.setStyleSheet("font-size: 12.5px;")
        body.addWidget(hint)
        self.grid = GameGrid()
        body.addWidget(self.grid)
        self.empty = label("No games match your search.", "muted")
        body.addWidget(self.empty)
        body.addStretch(1)
        self.cards = {}

        b = self.backend
        b.status.connect(self._on_status)
        b.library_changed.connect(self.refresh)
        b.settings_changed.connect(lambda _s: self.load())
        self._last_game = None
        self.load()

    def on_show(self, **_):
        self.load()

    def _on_status(self, st):
        gid = (st.get("game") or {}).get("id")
        if gid != self._last_game:
            self._last_game = gid
            self.load()

    def load(self):
        self.data = self.backend.games()
        self.refresh()

    def all_games(self):
        d = self.data
        return [{**g, "custom": True, "category": g.get("category") or "Custom"} for g in d["custom"]] + list(d["builtin"])

    def refresh(self):
        if self.data is None:
            return
        s = self.backend.settings.get()
        profiles = s["games"]["profiles"]
        disabled = set(s["games"].get("disabled") or [])
        cur = self.backend.obs.status.get("game")
        cur_id = cur["id"] if cur else None
        counts = {}
        for c in self.backend.library.list():
            if c.get("game") and c["game"].get("id"):
                counts[c["game"]["id"]] = counts.get(c["game"]["id"], 0) + 1

        allg = self.all_games()
        self.count_lb.setText(f"{len(allg)} games recognized automatically")
        self.cur.setVisible(bool(cur))
        if cur:
            self.cur_icon.set_name(cur["name"])
            self.cur_name.setText(cur["name"])
            self.cur_sub.setText(f"Running now · {cur.get('exe') or ''}" + (" · custom profile active" if (profiles.get(cur["id"]) or {}).get("enabled") else ""))

        q = self.search.text().strip().lower()
        lst = [g for g in allg if not q or q in g["name"].lower()]
        new_cards = {}
        ordered = []
        for g in lst:
            key = (g["id"], bool(g.get("custom")), g["name"])
            card = self.cards.get(key)
            if card is None:
                card = GameCard(g, self.open_profile)
            card.g = g
            card.set_state(counts.get(g["id"], 0), bool((profiles.get(g["id"]) or {}).get("enabled")), g["id"] in disabled, g["id"] == cur_id)
            new_cards[key] = card
            ordered.append(card)
        # filtered-out cards stay alive (hidden) for reuse; cards of removed games are dropped
        valid = {(g["id"], bool(g.get("custom")), g["name"]) for g in allg}
        for key in [k for k in self.cards if k not in valid]:
            self.cards.pop(key).deleteLater()
        self.cards.update(new_cards)
        self.grid.set_cards(ordered)
        self.empty.setVisible(not lst)

    def _edit_current(self):
        cur = self.backend.obs.status.get("game")
        if cur:
            self.open_profile(next((g for g in self.all_games() if g["id"] == cur["id"]), cur))

    def open_profile(self, g, modal=True):
        d = ProfileDialog(self, g)
        self.dialog = d
        if modal:
            d.exec()
            self.dialog = None
            self.load()
        else:
            d.finished.connect(lambda _r: self.load())
            d.show()
        return d

    def add_game(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the game's .exe", "", "Programs (*.exe)")
        if not path:
            return
        g = self.backend.add_custom_game(path)
        self.toast(f"{g['name']} added", "It will be recorded automatically when it launches")
        self.load()


_ = (QDialog, QLabel)

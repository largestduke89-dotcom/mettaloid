"""Library: filters, search, virtualized grid, multi-select bulk actions, albums, drag & drop import."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from .. import icons
from ..bg import run_bg
from ..clipgrid import ClipGrid
from ..theme import C
from ..widgets import Combo, btn, confirm, label
from . import Page

VIEWS = [("all", "All media", "library"), ("clip", "Clips", "clip"), ("session", "Sessions", "film"), ("screenshot", "Screenshots", "image"),
         ("export", "Edited & compressed", "editor"), ("import", "Imported", "download"), ("fav", "Favorites", "star")]


class NavItem(QPushButton):
    def __init__(self, text, icon=None, count=None, on_click=None):
        super().__init__()
        self.setProperty("nav", True)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(9)
        if icon:
            ic = QLabel()
            ic.setPixmap(icons.pixmap(icon, C["text2"], 16))
            ic.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(ic)
        t = QLabel(text)
        t.setAttribute(Qt.WA_TransparentForMouseEvents)
        t.setStyleSheet(f"color: {C['text']}; font-weight: 600;")
        lay.addWidget(t, 1)
        if count:
            c = QLabel(str(count))
            c.setAttribute(Qt.WA_TransparentForMouseEvents)
            c.setStyleSheet(f"color: {C['text3']}; font-size: 11.5px;")
            lay.addWidget(c)
        self.setMinimumHeight(34)
        if on_click:
            self.clicked.connect(lambda: on_click())


class LibraryPage(Page):
    def __init__(self, win):
        super().__init__(win, "Library")
        self.view, self.album, self.game = "all", None, None
        self.sort = "new"
        self.card_w = 250
        b = self.backend
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search clips, games, tags…")
        self.search.addAction(icons.icon("search", C["text3"], 15), QLineEdit.LeadingPosition)
        self.search.setFixedWidth(320)
        self.search.textChanged.connect(lambda: self.filter_timer.start(150))
        self.top.addSpacing(14)
        self.top.addWidget(self.search)
        self.game_cb = Combo([("", "All games")], "", on_change=self._set_game, width=170)
        self.top.addWidget(self.game_cb)
        self.sort_cb = Combo([("new", "Newest"), ("old", "Oldest"), ("big", "Largest"), ("long", "Longest"), ("name", "Name")], "new",
                             on_change=self._set_sort, width=120)
        self.top.addWidget(self.sort_cb)
        self.top.addStretch(1)
        self.top.addWidget(btn("", "zoomOut", lambda: self._zoom(-40), ghost=True, sm=True, tip="Smaller"))
        self.top.addWidget(btn("", "zoomIn", lambda: self._zoom(40), ghost=True, sm=True, tip="Larger"))
        self.top.addWidget(btn("Import", "download", self.import_files, sm=True))
        self.sel_all_b = btn("Select all", None, self.toggle_select_all, sm=True)
        self.top.addWidget(self.sel_all_b)

        body = QHBoxLayout()
        body.setSpacing(0)
        self.side = QScrollArea()
        self.side.setWidgetResizable(True)
        self.side.setFixedWidth(220)
        self.side.setStyleSheet(f"QScrollArea {{ border-right: 1px solid {C['line']}; }}")
        body.addWidget(self.side)
        main = QWidget()
        ml = QVBoxLayout(main)
        ml.setContentsMargins(12, 8, 12, 0)
        ml.setSpacing(6)
        self.album_bar = QWidget()
        al = QHBoxLayout(self.album_bar)
        al.setContentsMargins(8, 6, 8, 0)
        self.album_name = QLineEdit()
        self.album_name.setStyleSheet("font-size: 18px; font-weight: 700; background: transparent; border: 1px solid transparent;")
        self.album_name.editingFinished.connect(lambda: self.album and b.library.rename_album(self.album, self.album_name.text()))
        al.addWidget(self.album_name, 1)
        al.addWidget(btn("Delete album", "trash", self.delete_album, sm=True, danger=True))
        self.album_bar.hide()
        ml.addWidget(self.album_bar)
        self.grid = ClipGrid(self.card_w)
        self.grid.opened.connect(lambda c: self.go("clip", id=c["id"]))
        self.grid.selection_changed.connect(self.on_selection)
        self.grid.context.connect(self.clip_menu)
        ml.addWidget(self.grid, 1)
        self.empty = label("Nothing here — drag & drop videos onto this window to import them.", "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        ml.addWidget(self.empty)
        body.addWidget(main, 1)
        self.root.addLayout(body, 1)

        self.bulk = QFrame(self)
        self.bulk.setStyleSheet(f"QFrame {{ background: {C['bg4']}; border: 1px solid {C['line2']}; border-radius: 14px; }}")
        bl = QHBoxLayout(self.bulk)
        bl.setContentsMargins(16, 8, 10, 8)
        self.bulk_lb = QLabel()
        self.bulk_lb.setStyleSheet("font-weight: 700; border: 0;")
        bl.addWidget(self.bulk_lb)
        for text, ic, fn, kw in (("Favorite", "star", self.bulk_fav, {}), ("Album", "album", self.bulk_album, {}), ("Montage", "merge", self.bulk_montage, {}),
                                 ("Compress", "compress", self.bulk_compress, {}), ("Find Frame scan", "target", self.bulk_scan, {}),
                                 ("Delete", "trash", self.bulk_delete, {"danger": True})):
            bl.addWidget(btn(text, ic, fn, sm=True, **kw))
        bl.addWidget(btn("", "x", lambda: self.grid.select_all(False), sm=True, ghost=True))
        self.bulk.hide()
        self.setAcceptDrops(True)
        self.filter_timer = QTimer(self, singleShot=True, timeout=self.refresh)
        b.library_changed.connect(lambda: self.filter_timer.start(100))
        self.refresh()

    # ---------------------------------------------------------------- data
    def filtered(self):
        clips = self.backend.library.list()
        if self.album:
            clips = [c for c in clips if self.album in (c.get("albums") or [])]
        elif self.view == "fav":
            clips = [c for c in clips if c.get("favorite")]
        elif self.view != "all":
            clips = [c for c in clips if c["type"] == self.view]
        if self.game:
            clips = [c for c in clips if (c["game"]["name"] if c.get("game") else "Desktop") == self.game]
        q = self.search.text().strip().lower()
        if q:
            clips = [c for c in clips if any(q in (x or "").lower() for x in [c["title"], (c.get("game") or {}).get("name"), c.get("event"), c["path"], *(c.get("tags") or [])])]
        key = {"new": (lambda c: -c["createdAt"]), "old": (lambda c: c["createdAt"]), "big": (lambda c: -(c.get("size") or 0)),
               "long": (lambda c: -(c.get("duration") or 0)), "name": (lambda c: c["title"].lower())}[self.sort]
        return sorted(clips, key=key)

    def refresh(self):
        lib = self.backend.library
        clips = lib.list()
        games = {}
        for c in clips:
            n = c["game"]["name"] if c.get("game") else "Desktop"
            games[n] = games.get(n, 0) + 1
        cur = self.game_cb.value()
        self.game_cb.set_options([("", "All games")] + [(g, g) for g in sorted(games)], cur or "")
        self._build_side(clips, games)
        items = self.filtered()
        self.grid.set_clips(items)
        self.empty.setVisible(not items)
        self.grid.setVisible(bool(items))
        a = next((x for x in lib.albums() if x["id"] == self.album), None)
        self.album_bar.setVisible(bool(a))
        if a and not self.album_name.hasFocus():
            self.album_name.setText(a["name"])

    def _build_side(self, clips, games):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 12, 10, 12)
        v.setSpacing(2)
        counts = {k: sum(1 for c in clips if c["type"] == k) for k, _, _ in VIEWS}
        counts["all"] = len(clips)
        counts["fav"] = sum(1 for c in clips if c.get("favorite"))
        for k, text, ic in VIEWS:
            it = NavItem(text, ic, counts.get(k) or None, on_click=lambda k=k: self._set_view(k))
            it.setChecked(not self.album and self.view == k)
            v.addWidget(it)
        hdr = QHBoxLayout()
        hdr.setContentsMargins(10, 14, 4, 4)
        hdr.addWidget(label("ALBUMS", "h3"), 1)
        hdr.addWidget(btn("", "plus", self.new_album, ghost=True, sm=True, tip="New album"))
        v.addLayout(hdr)
        albums = self.backend.library.albums()
        for a in albums:
            n = sum(1 for c in clips if a["id"] in (c.get("albums") or []))
            it = NavItem(a["name"], "album", n or None, on_click=lambda aid=a["id"]: self._set_album(aid))
            it.setChecked(self.album == a["id"])
            v.addWidget(it)
        if not albums:
            e = label("No albums yet", "dim")
            e.setContentsMargins(12, 2, 0, 2)
            v.addWidget(e)
        if games:
            g = label("GAMES", "h3")
            g.setContentsMargins(10, 14, 0, 4)
            v.addWidget(g)
            for name, n in sorted(games.items(), key=lambda x: -x[1]):
                it = NavItem(name, None, n, on_click=lambda name=name: self._set_game(None if self.game == name else name))
                it.setChecked(self.game == name)
                v.addWidget(it)
        v.addStretch(1)
        self.side.setWidget(w)

    def _set_view(self, k):
        self.view, self.album = k, None
        self.refresh()

    def _set_album(self, aid):
        self.album = aid
        self.refresh()

    def _set_game(self, g):
        self.game = g or None
        self.game_cb.set_value(self.game or "")
        self.refresh()

    def _set_sort(self, s):
        self.sort = s
        self.refresh()

    def _zoom(self, d):
        self.card_w = max(170, min(450, self.card_w + d))
        self.grid.set_card_width(self.card_w)

    # ---------------------------------------------------------------- selection / bulk
    def on_selection(self, sel):
        self.bulk.setVisible(bool(sel))
        self.bulk_lb.setText(f"{len(sel)} selected")
        self.sel_all_b.setText("Deselect" if sel else "Select all")
        self._place_bulk()

    def _place_bulk(self):
        self.bulk.adjustSize()
        self.bulk.move((self.width() + 220 - self.bulk.width()) // 2, self.height() - self.bulk.height() - 22)
        self.bulk.raise_()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_bulk()

    def toggle_select_all(self):
        self.grid.select_all(not self.grid.model_.selected)

    def selected(self):
        sel = self.grid.model_.selected
        return [c for c in self.backend.library.list() if c["id"] in sel]

    def bulk_fav(self):
        cs = self.selected()
        val = not all(c.get("favorite") for c in cs)
        for c in cs:
            self.backend.library.update(c["id"], {"favorite": val})

    def bulk_album(self, ids=None):
        ids = ids or [c["id"] for c in self.selected()]
        m = QMenu(self)
        for a in self.backend.library.albums():
            m.addAction(a["name"], lambda aid=a["id"]: (self.backend.library.add_to_album(aid, ids), self.toast("Added to album")))
        m.addSeparator()
        m.addAction("New album…", lambda: self._new_album_with(ids))
        m.exec(QCursor.pos())

    def _new_album_with(self, ids):
        name, ok = QInputDialog.getText(self, "New album", "Album name:")
        if ok and name:
            a = self.backend.library.create_album(name)
            self.backend.library.add_to_album(a["id"], ids)

    def bulk_montage(self):
        vids = sorted([c for c in self.selected() if c["type"] != "screenshot"], key=lambda c: c["createdAt"])
        if vids:
            self.go("editor", id="montage", ids=",".join(c["id"] for c in vids))

    def bulk_compress(self):
        ids = [c["id"] for c in self.selected() if c["type"] != "screenshot"]
        if ids:
            self.go("compressor", ids=",".join(ids))

    def bulk_scan(self):
        ff = self.backend.findframe
        finders = [f["id"] for f in ff.finders()]
        if not finders:
            return self.toast("Save a Find Frame finder first", "Open a clip, pause, press Find Frame and draw a box.", "err")
        job = ff.search(finder_ids=finders, clip_ids=[c["id"] for c in self.selected()], label="Scan selected clips")
        self.go("findframe", job=job["id"])

    def bulk_delete(self, ids=None):
        cs = [self.backend.library.get(i) for i in ids] if ids else self.selected()
        favs = sum(1 for c in cs if c.get("favorite"))
        msg = "Files in your clip folder are moved to the Recycle Bin. Imported files outside it are only removed from the library."
        if favs:
            msg += f"\n\n⚠ {favs} of these are favorites."
        if confirm(self, f"Delete {len(cs)} item(s)?", msg, "Delete", danger=True):
            run_bg(lambda: self.backend.library.remove([c["id"] for c in cs]))
            self.grid.select_all(False)

    def new_album(self):
        name, ok = QInputDialog.getText(self, "New album", "Album name:")
        if ok and name:
            a = self.backend.library.create_album(name)
            self._set_album(a["id"])

    def delete_album(self):
        if self.album and confirm(self, "Delete album?", "Clips stay in your library.", "Delete", danger=True):
            self.backend.library.delete_album(self.album)
            self.album = None
            self.refresh()

    def clip_menu(self, c, pos):
        b = self.backend
        m = QMenu(self)
        m.addAction("Open", lambda: self.go("clip", id=c["id"]))
        if c["type"] != "screenshot":
            m.addAction("Edit", lambda: self.go("editor", id=c["id"]))
            m.addAction("Compress", lambda: self.go("compressor", id=c["id"]))
        m.addAction("Unfavorite" if c.get("favorite") else "Favorite", lambda: b.library.update(c["id"], {"favorite": not c.get("favorite")}))
        m.addAction("Copy file (paste into Discord)", lambda: (b.copy_file(c["path"]), self.toast("Copied")))
        m.addAction("Show in folder", lambda: b.show_in_folder(c["path"]))
        m.addAction("Add to album…", lambda: self.bulk_album([c["id"]]))
        m.addSeparator()
        m.addAction("Delete", lambda: self.bulk_delete([c["id"]]))
        m.exec(pos)

    # ---------------------------------------------------------------- import
    def import_files(self, files=None):
        if not files:
            files, _ = QFileDialog.getOpenFileNames(self, "Import videos", "", "Media (*.mp4 *.mkv *.mov *.webm *.avi *.flv *.ts *.m4v *.png *.jpg *.jpeg)")
        if files:
            run_bg(lambda: self.backend.library.import_files(files), lambda added: self.toast(f"Imported {len(added)} file(s)"))

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.import_files([u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()])

    def on_show(self, view=None, **_):
        if view:
            self._set_view(view)


_ = time

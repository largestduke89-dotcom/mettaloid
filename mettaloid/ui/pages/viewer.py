"""Clip viewer: GPU video playback, Find Frame box tool, details/tags/albums/bookmarks."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QShortcut, QKeySequence
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import icons
from ..bg import run_bg
from ..theme import C
from ..video import FrameCanvas, Player, TransportBar
from ..widgets import FlowLayout, btn, confirm, fmt_date, fmt_dur, fmt_size, hbox, label
from . import Page


class Chip(QFrame):
    def __init__(self, text, on_remove=None):
        super().__init__()
        self.setStyleSheet(f"QFrame {{ background: {C['bg4']}; border-radius: 6px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 4, 2)
        lay.setSpacing(4)
        lay.addWidget(QLabel(text))
        if on_remove:
            x = btn("", "x", on_remove, ghost=True, sm=True)
            x.setFixedSize(18, 18)
            x.setStyleSheet("min-height: 18px; padding: 0;")
            lay.addWidget(x)


class ViewerPage(Page):
    def __init__(self, win):
        super().__init__(win, "")
        self.clip = None
        b = self.backend
        self.top.insertWidget(0, btn("Library", "back", lambda: self.go("library"), ghost=True, sm=True))
        self.top.addStretch(1)
        self.prev_b = btn("", "up", lambda: self.step(-1), ghost=True, sm=True, tip="Newer (↑)")
        self.next_b = btn("", "down", lambda: self.step(1), ghost=True, sm=True, tip="Older (↓)")
        self.top.addWidget(self.prev_b)
        self.top.addWidget(self.next_b)

        body = QHBoxLayout()
        body.setSpacing(0)
        stage = QWidget()
        stage.setStyleSheet("background: black;")
        sl = QVBoxLayout(stage)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(0)
        self.stack = QStackedWidget()
        self.video = QVideoWidget()
        self.canvas = FrameCanvas()
        self.stack.addWidget(self.video)
        self.stack.addWidget(self.canvas)
        sl.addWidget(self.stack, 1)
        self.player = Player(self)
        self.player.mp.setVideoOutput(self.video)
        self.transport = TransportBar(self.player)
        sl.addWidget(self.transport)
        self.tracks_note = label("", "dim", wrap=True)
        self.tracks_note.setStyleSheet(f"background: {C['bg2']}; padding: 4px 12px; color: {C['text3']}; font-size: 12px;")
        sl.addWidget(self.tracks_note)
        tool = QWidget()
        tool.setStyleSheet(f"background: {C['bg2']}; border-top: 1px solid {C['line']};")
        tl = QHBoxLayout(tool)
        tl.setContentsMargins(12, 8, 12, 8)
        self.ff_b = btn("Find Frame", "target", lambda: self.end_tool() if self.canvas.drawing else self.start_tool(), sm=True, tip="Pause and draw a box to find that frame/object elsewhere (B)")
        tl.addWidget(self.ff_b)
        tl.addWidget(label("Pause on a moment, draw a box around something (kill icon, banner, item…) and find every other time it appears.", "dim"), 1)
        self.tool_row = tool
        sl.addWidget(tool)
        self.canvas.box_drawn.connect(self.on_box)
        self.canvas.cancelled.connect(self.end_tool)
        body.addWidget(stage, 1)

        side_sa = QScrollArea()
        side_sa.setWidgetResizable(True)
        side_sa.setFixedWidth(350)
        side_sa.setStyleSheet(f"QScrollArea {{ border-left: 1px solid {C['line']}; }}")
        side = QWidget()
        self.sv = QVBoxLayout(side)
        self.sv.setContentsMargins(18, 18, 18, 18)
        self.sv.setSpacing(12)
        self.edit_b = btn("  Edit clip", "editor", lambda: self.go("editor", id=self.clip["id"]), primary=True, lg=True)
        self.sv.addWidget(self.edit_b)
        r1 = QHBoxLayout()
        self.comp_b = btn("Compress", "compress", lambda: self.go("compressor", id=self.clip["id"]))
        r1.addWidget(self.comp_b)
        r1.addWidget(btn("Copy file", "copy", self.copy_file))
        self.sv.addLayout(r1)
        r2 = QHBoxLayout()
        self.fav_b = btn("Favorite", "star", self.toggle_fav)
        r2.addWidget(self.fav_b, 1)
        for ic, fn, tip in (("folder", lambda: b.show_in_folder(self.clip["path"]), "Show in folder"), ("external", lambda: b.open_path(self.clip["path"]), "Open in default player"),
                            ("trash", self.delete, "Delete")):
            x = btn("", ic, fn, tip=tip, danger=ic == "trash")
            x.setFixedWidth(38)
            r2.addWidget(x)
        self.sv.addLayout(r2)
        self.sv.addWidget(label("TITLE", "h3"))
        self.title_e = QLineEdit()
        self.title_e.editingFinished.connect(lambda: self.clip and self.title_e.text() != self.clip["title"] and b.library.update(self.clip["id"], {"title": self.title_e.text()}))
        self.sv.addWidget(self.title_e)
        self.sv.addWidget(btn("Rename file on disk too", None, lambda: b.library.update(self.clip["id"], {"title": self.title_e.text(), "renameFile": True}), ghost=True, sm=True), 0, Qt.AlignLeft)
        self.kv = QGridLayout()
        self.kv.setHorizontalSpacing(14)
        self.kv.setVerticalSpacing(5)
        self.sv.addLayout(self.kv)
        self.sv.addWidget(label("TAGS", "h3"))
        self.tags_w = QWidget()
        self.tags_fl = FlowLayout(self.tags_w)
        self.sv.addWidget(self.tags_w)
        self.tag_e = QLineEdit()
        self.tag_e.setPlaceholderText("Add tag and press Enter")
        self.tag_e.returnPressed.connect(self.add_tag)
        self.sv.addWidget(self.tag_e)
        self.sv.addWidget(label("ALBUMS", "h3"))
        self.albums_w = QWidget()
        self.albums_fl = FlowLayout(self.albums_w)
        self.sv.addWidget(self.albums_w)
        self.album_cb = QComboBox()
        self.album_cb.activated.connect(self.add_album)
        self.sv.addWidget(self.album_cb)
        self.bm_title = label("BOOKMARKS", "h3")
        self.sv.addWidget(self.bm_title)
        self.bm_box = QVBoxLayout()
        self.bm_box.setSpacing(4)
        self.sv.addLayout(self.bm_box)
        self.sv.addWidget(label("Shortcuts: E edit · F favorite · B Find Frame · ↑/↓ navigate · Space play", "dim", wrap=True))
        self.sv.addStretch(1)
        side_sa.setWidget(side)
        body.addWidget(side_sa)
        self.root.addLayout(body, 1)

        for key, fn in (("E", lambda: self.clip and self.clip["type"] != "screenshot" and self.go("editor", id=self.clip["id"])),
                        ("F", self.toggle_fav), ("B", self.start_tool), ("Up", lambda: self.step(-1)), ("Down", lambda: self.step(1)),
                        ("Space", self.player.toggle), ("Escape", lambda: self.go("library"))):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        b.library_changed.connect(lambda: QTimer.singleShot(0, self.refresh_side))

    # ---------------------------------------------------------------- lifecycle
    def on_show(self, id=None, t=None, **_):
        c = self.backend.library.get(id) if id else self.clip
        if not c:
            return self.go("library")
        changed = not self.clip or self.clip["id"] != c["id"]
        self.clip = c
        self.end_tool()
        is_vid = c["type"] != "screenshot"
        self.transport.setVisible(is_vid)
        self.tool_row.setVisible(is_vid)
        self.edit_b.setVisible(is_vid)
        self.comp_b.setVisible(is_vid)
        if is_vid:
            self.stack.setCurrentWidget(self.video)
            if changed or t is not None or self.player.mp.source().isEmpty():
                self.player.load(c["path"], float(t) if t else 0.0, autoplay=not t)
        else:
            self.player.release()
            self.canvas.set_image(QImage(c["path"]))
            self.stack.setCurrentWidget(self.canvas)
        n = len(c.get("audioTracks") or [])
        self.tracks_note.setVisible(n > 1)
        self.tracks_note.setText(f"This clip has {n} audio tracks ({', '.join(x['title'] for x in c['audioTracks'])}). Preview plays the mix; balance tracks in the editor.")
        self.refresh_side()
        self.setFocus()

    def on_hide(self):
        self.player.release()  # free the decoder while not visible

    def step(self, d):
        clips = self.backend.library.list()
        i = next((k for k, x in enumerate(clips) if self.clip and x["id"] == self.clip["id"]), -1)
        j = i + d
        if 0 <= j < len(clips):
            self.on_show(id=clips[j]["id"])

    # ---------------------------------------------------------------- side panel
    def refresh_side(self):
        if not self.clip:
            return
        c = self.backend.library.get(self.clip["id"])
        if not c:
            return
        self.clip = c
        lib = self.backend.library
        self.title.setText(c["title"])
        if not self.title_e.hasFocus():
            self.title_e.setText(c["title"])
        self.fav_b.setText("Favorited" if c.get("favorite") else "Favorite")
        self.fav_b.setIcon(icons.icon("star", C["accent"] if c.get("favorite") else C["text"], 16, fill="currentColor" if c.get("favorite") else "none"))
        while self.kv.count():
            w = self.kv.takeAt(0).widget()
            w.deleteLater()
        rows = [("Game", c["game"]["name"] if c.get("game") else "Desktop"), ("Captured", fmt_date(c["createdAt"]))]
        if c["type"] != "screenshot":
            rows.append(("Duration", fmt_dur(c.get("duration"))))
        rows.append(("Resolution", f"{c.get('width')}×{c.get('height')}" + (f" @ {round(c['fps'])} fps" if c.get("fps") else "")))
        if c.get("videoCodec"):
            rows.append(("Codec", c["videoCodec"].upper()))
        br = f" · {round(c['size'] * 8 / c['duration'] / 1000)} kbps" if c.get("duration") else ""
        rows.append(("Size", fmt_size(c.get("size")) + br))
        if c["type"] != "screenshot":
            rows.append(("Audio", f"{len(c.get('audioTracks') or [])} track(s)"))
        if c.get("event"):
            rows.append(("Event", c["event"]))
        rows.append(("File", c["path"]))
        for i, (k, v) in enumerate(rows):
            a = label(k, "dim")
            bl = label(v, wrap=True, sel=True)
            bl.setStyleSheet("font-size: 12px;" + (" font-family: Consolas; font-size: 11px;" if k == "File" else ""))
            self.kv.addWidget(a, i, 0, Qt.AlignTop)
            self.kv.addWidget(bl, i, 1)
        self._clear(self.tags_fl)
        for tg in c.get("tags") or []:
            self.tags_fl.addWidget(Chip("#" + tg, lambda tg=tg: lib.update(c["id"], {"tags": [x for x in c["tags"] if x != tg]})))
        self._clear(self.albums_fl)
        albums = {a["id"]: a for a in lib.albums()}
        for aid in c.get("albums") or []:
            if aid in albums:
                self.albums_fl.addWidget(Chip(albums[aid]["name"], lambda aid=aid: lib.remove_from_album(aid, [c["id"]])))
        self.album_cb.blockSignals(True)
        self.album_cb.clear()
        self.album_cb.addItem("+ Add to album", None)
        for a in albums.values():
            if a["id"] not in (c.get("albums") or []):
                self.album_cb.addItem(a["name"], a["id"])
        self.album_cb.blockSignals(False)
        while self.bm_box.count():
            it = self.bm_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        bms = c.get("bookmarks") or []
        self.bm_title.setVisible(bool(bms))
        self.bm_title.setText(f"BOOKMARKS · {len(bms)}")
        for bm in bms[:100]:
            jump = btn(fmt_dur(bm["time"]), None, lambda t=bm["time"]: (self.player.seek(t), self.player.mp.play()), ghost=True, sm=True)
            clip_it = btn("Clip it", None, lambda t=bm["time"]: self.go("editor", id=c["id"], start=max(0, t - 20), end=min(c["duration"], t + 10)), sm=True)
            star = QLabel("★")
            star.setStyleSheet(f"color: {C['purple']};")
            lb = label(bm["label"], "dim")
            self.bm_box.addWidget(hbox(star, jump, lb, "stretch", clip_it, spacing=6))

    @staticmethod
    def _clear(fl):
        while fl.count():
            it = fl.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def add_tag(self):
        t = self.tag_e.text().strip().lstrip("#")
        if t and self.clip:
            self.backend.library.update(self.clip["id"], {"tags": sorted(set((self.clip.get("tags") or []) + [t]))})
        self.tag_e.clear()

    def add_album(self, i):
        aid = self.album_cb.itemData(i)
        if aid:
            self.backend.library.add_to_album(aid, [self.clip["id"]])

    def toggle_fav(self):
        if self.clip:
            self.backend.library.update(self.clip["id"], {"favorite": not self.clip.get("favorite")})

    def copy_file(self):
        self.backend.copy_file(self.clip["path"])
        self.toast("Copied — paste into Discord or any chat")

    def delete(self):
        c = self.clip
        msg = "This file is outside your clip folder, so it will only be removed from the library." if c.get("external") else "The file will be moved to the Recycle Bin."
        if confirm(self, "Delete this clip?", msg, "Delete", danger=True):
            clips = self.backend.library.list()
            i = next((k for k, x in enumerate(clips) if x["id"] == c["id"]), 0)
            nxt = clips[i + 1] if i + 1 < len(clips) else clips[i - 1] if i > 0 else None
            self.player.release()
            run_bg(lambda: self.backend.library.remove([c["id"]]))
            if nxt:
                self.on_show(id=nxt["id"])
            else:
                self.go("library")

    # ---------------------------------------------------------------- Find Frame tool
    def start_tool(self):
        if not self.clip or self.clip["type"] == "screenshot":
            return
        self.player.mp.pause()
        frame = self.video.videoSink().videoFrame()
        img = frame.toImage() if frame.isValid() else QImage()
        self.tool_time = self.player.pos()
        self.canvas.set_image(img)
        self.stack.setCurrentWidget(self.canvas)
        self.canvas.start_draw()
        self.ff_b.setText("Cancel")

    def end_tool(self):
        self.canvas.stop_draw()
        if self.clip and self.clip["type"] != "screenshot":
            self.stack.setCurrentWidget(self.video)
        self.ff_b.setText("Find Frame")

    def on_box(self, box):
        self.end_tool()
        from ..findframe_dialog import FindFrameDialog
        FindFrameDialog(self.win, self.clip, self.tool_time, box).exec()


_ = QFrame

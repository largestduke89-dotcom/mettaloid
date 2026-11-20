"""Compressor: shrink clips to an exact file size (port of legacy Compressor.jsx)."""
import os
import re

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIntValidator, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QDoubleSpinBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QSizePolicy,
                               QVBoxLayout, QWidget)

from ...core import ffmpeg, render
from .. import icons
from ..bg import run_bg
from ..clipgrid import THUMBS
from ..theme import C
from ..widgets import Combo, Section, Seg, Toggle, bar, btn, fmt_dur, fmt_size, hbox, label, scroll_page, set_prop
from . import Page

SIZE_PRESETS = [
    {"mb": 20, "label": "Discord / chat", "hint": "Fits most chat upload limits"},
    {"mb": 50, "label": "Discord Nitro Basic", "hint": "Good quality for short clips"},
    {"mb": 500, "label": "Large / high quality", "hint": "Near-original for long clips"},
]
VIDEO_RE = re.compile(r"\.(mp4|mkv|mov|webm|avi|flv|ts|m4v)$", re.I)
VIDEO_FILTER = "Video (*.mp4 *.mkv *.mov *.webm *.avi *.flv *.m4v *.ts)"
JOB_TITLE_RE = re.compile(r"\(\d+(\.\d+)?MB\)")
THUMB_W, THUMB_H = 160, 90

CARD_QSS = f"""
QFrame#preset {{ background: {C['bg2']}; border: 1px solid {C['line']}; border-radius: 14px; }}
QFrame#preset:hover {{ border-color: {C['line2']}; }}
QFrame#preset[on="true"] {{ border: 1px solid {C['accent']}; background: #1d1a10; }}
"""
DROP_QSS = f"""
QFrame#drop {{ background: {C['bg2']}; border: 2px dashed {C['line2']}; border-radius: 14px; }}
QFrame#drop:hover {{ border-color: #4a4f62; }}
QFrame#drop[over="true"] {{ border-color: {C['accent']}; background: #1d1a10; }}
"""


def file_from_clip(c):
    return {"path": c["path"], "title": c.get("title") or os.path.splitext(os.path.basename(c["path"]))[0], "duration": c.get("duration") or 0,
            "size": c.get("size"), "thumb": c.get("thumb"), "clipId": c["id"], "game": c.get("game"), "audioTracks": c.get("audioTracks") or [],
            "width": c.get("width"), "height": c.get("height"), "start": 0, "end": c.get("duration") or 0}


def file_from_probe(p, info):
    return {"path": p, "title": os.path.splitext(os.path.basename(p))[0], "duration": info["duration"], "size": info["size"], "thumb": None,
            "clipId": None, "game": None, "audioTracks": info["audioTracks"], "width": info["width"], "height": info["height"],
            "start": 0, "end": info["duration"]}


def project_for(f, opts, target_mb):
    """Exactly mirrors legacy projectFor()."""
    multi_track = bool(f.get("audioTracks")) and len(f["audioTracks"]) > 1
    tracks = []
    if multi_track and opts["audioMode"] == "nomic":
        tracks = [{"index": 1, "volume": 1}]
    elif multi_track and opts["audioMode"] == "mix":
        tracks = [{"index": 0, "volume": 1}]
    elif opts["audioMode"] == "none":
        tracks = [{"index": i, "muted": True} for i, _ in enumerate(f.get("audioTracks") or [])]
    return {
        "sources": [f["path"]],
        "segments": [{"src": 0, "start": f.get("start") or 0, "end": f.get("end") or f["duration"], "speed": 1}],
        "crop": None, "rotate": 0, "aspect": "source", "fit": "crop", "color": {}, "texts": [], "fadeIn": 0, "fadeOut": 0,
        "audio": {"tracks": tracks, "normalize": False, "music": None},
        "output": {
            "format": opts["format"], "codec": opts["codec"], "encoder": opts["encoder"], "twoPass": opts["twoPass"], "autoDownscale": opts["autoDownscale"],
            "audioKbps": opts["audioKbps"], "fps": opts["fps"], "resolution": opts["resolution"], "targetMB": target_mb, "quality": "medium",
        },
    }


def rounded_thumb(pm, w=THUMB_W, h=THUMB_H, radius=10):
    out = QPixmap(w * 2, h * 2)
    out.setDevicePixelRatio(2)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(0, 0, w, h, radius, radius)
    p.setClipPath(path)
    p.fillRect(QRect(0, 0, w, h), QColor(C["bg3"]))
    if pm is not None and not pm.isNull():
        s = pm.size().scaled(QSize(w, h), Qt.KeepAspectRatioByExpanding)
        p.drawPixmap(QRect((w - s.width()) // 2, (h - s.height()) // 2, s.width(), s.height()), pm)
    else:
        ic = icons.pixmap("film", C["text3"], 26)
        p.drawPixmap((w - 26) // 2, (h - 26) // 2, ic)
    p.end()
    return out


class ElideLabel(QLabel):
    def __init__(self, text="", style=""):
        super().__init__()
        self.full = text
        self.setStyleSheet(style)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)
        self.setToolTip(text)
        self._apply()

    def set_full(self, text):
        self.full = text
        self.setToolTip(text)
        self._apply()

    def _apply(self):
        self.setText(self.fontMetrics().elidedText(self.full, Qt.ElideRight, max(40, self.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply()


class PresetCard(QFrame):
    def __init__(self, on_click):
        super().__init__()
        self.setObjectName("preset")
        self.setStyleSheet(CARD_QSS)
        self.setCursor(Qt.PointingHandCursor)
        self.on_click = on_click
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(16, 14, 16, 14)
        self.lay.setSpacing(3)
        self.setMinimumHeight(104)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_click()
        super().mousePressEvent(e)

    def set_on(self, on):
        set_prop(self, "on", bool(on))


class DropZone(QFrame):
    def __init__(self, on_click):
        super().__init__()
        self.setObjectName("drop")
        self.setStyleSheet(DROP_QSS)
        self.setCursor(Qt.PointingHandCursor)
        self.on_click = on_click
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 34, 20, 34)
        lay.setSpacing(4)
        ic = QLabel()
        ic.setPixmap(icons.pixmap("compress", C["text2"], 30))
        ic.setAlignment(Qt.AlignCenter)
        lay.addWidget(ic)
        t = QLabel("Drop videos here or click to browse")
        t.setStyleSheet("font-weight: 700; margin-top: 6px;")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        d = label("You can also select clips in the Library and choose “Compress”.", "dim")
        d.setAlignment(Qt.AlignCenter)
        lay.addWidget(d)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.on_click()
        super().mousePressEvent(e)

    def set_over(self, v):
        set_prop(self, "over", bool(v))


def _spin(value, mn, mx):
    s = QDoubleSpinBox()
    s.setDecimals(1)
    s.setSingleStep(0.5)
    s.setRange(mn, mx)
    s.setValue(value)
    s.setFixedWidth(80)
    s.setKeyboardTracking(False)
    return s


class FileRow(QFrame):
    def __init__(self, page, f, first):
        super().__init__()
        self.page, self.f = page, f
        self.setObjectName("row")
        if first:
            self.setProperty("first", True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(14)
        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB_W, THUMB_H)
        lay.addWidget(self.thumb, 0, Qt.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(3)
        self.title = ElideLabel(f["title"], "font-weight: 650;")
        info.addWidget(self.title)
        dims = f"{f.get('width') or 0}×{f.get('height') or 0}"
        self.meta = label(f"{fmt_size(f.get('size'))} · {fmt_dur(f.get('duration'))} · {dims}", "dim")
        self.meta.setStyleSheet("font-size: 12.5px;")
        info.addWidget(self.meta)
        self.est = QLabel()
        self.est.setTextFormat(Qt.RichText)
        self.est.setWordWrap(True)
        self.est.setStyleSheet("font-size: 12.5px; margin-top: 4px;")
        info.addWidget(self.est)
        trim = QHBoxLayout()
        trim.setSpacing(8)
        trim.setContentsMargins(0, 4, 0, 0)
        dur = float(f.get("duration") or 0)
        trim.addWidget(label("Trim", "dim"))
        self.s_start = _spin(float(f.get("start") or 0), 0, max(0.0, dur))
        trim.addWidget(self.s_start)
        trim.addWidget(label("to", "dim"))
        self.s_end = _spin(float(f.get("end") or dur), 0, max(0.0, dur))
        trim.addWidget(self.s_end)
        trim.addWidget(label("sec", "dim"))
        trim.addStretch(1)
        info.addLayout(trim)
        lay.addLayout(info, 1)
        rm = btn("", "x", lambda: page.remove_file(f["path"]), ghost=True, sm=True, tip="Remove", icon_size=15)
        rm.setFixedWidth(30)
        lay.addWidget(rm, 0, Qt.AlignTop)
        self.s_start.valueChanged.connect(self._start_changed)
        self.s_end.valueChanged.connect(self._end_changed)
        self.update_thumb()

    def update_thumb(self):
        self.thumb.setPixmap(rounded_thumb(THUMBS.get(self.f.get("thumb"))))

    def _start_changed(self, v):
        f = self.f
        f["start"] = max(0.0, min(v, (f.get("end") or f["duration"]) - 0.5))
        if abs(f["start"] - v) > 1e-6:
            self.s_start.blockSignals(True)
            self.s_start.setValue(f["start"])
            self.s_start.blockSignals(False)
        self.page.files_changed()

    def _end_changed(self, v):
        f = self.f
        f["end"] = min(float(f["duration"]), max(v, (f.get("start") or 0) + 0.5))
        if abs(f["end"] - v) > 1e-6:
            self.s_end.blockSignals(True)
            self.s_end.setValue(f["end"])
            self.s_end.blockSignals(False)
        self.page.files_changed()

    def set_estimate(self, e, target_mb):
        f = self.f
        if not target_mb:
            self.est.setText("")
            return
        if not e:
            self.est.setText(f"<span style='color:{C['text3']}'>Estimating…</span>")
            return
        ratio = min(100, round(target_mb * 1e6 / f["size"] * 100)) if f.get("size") else None
        t = (f"→ <b>&lt; {target_mb:g} MB</b> · {e['W']}×{e['H']} @ {round(e['fps'])} fps · {e.get('videoKbps')} kbps")
        if ratio is not None and ratio < 100:
            t += f"<span style='color:{C['text3']}'> · {ratio}% of original</span>"
        if (e.get("videoKbps") or 0) < 400:
            t += f"<span style='color:{C['accent2']}'> · low quality — trim the clip or pick a bigger size</span>"
        self.est.setText(t)


class JobRow(QFrame):
    def __init__(self, page, first):
        super().__init__()
        self.page = page
        self.j = None
        self.setObjectName("row")
        if first:
            self.setProperty("first", True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 11, 16, 11)
        lay.setSpacing(12)
        txt = QVBoxLayout()
        txt.setSpacing(3)
        self.title = ElideLabel("", "font-weight: 600;")
        txt.addWidget(self.title)
        self.sub = label("", "dim")
        self.sub.setWordWrap(True)
        txt.addWidget(self.sub)
        self.bar = bar()
        txt.addWidget(self.bar)
        lay.addLayout(txt, 1)
        self.cancel_b = btn("Cancel", on_click=lambda: page.backend.cancel_job(self.j["id"]), sm=True)
        self.copy_b = btn(" Copy", "copy", self._copy, sm=True, icon_size=14)
        self.folder_b = btn("", "folder", lambda: page.backend.show_in_folder(self.j["output"]), sm=True, icon_size=14, tip="Show in folder")
        for b in (self.cancel_b, self.copy_b, self.folder_b):
            lay.addWidget(b, 0, Qt.AlignVCenter)

    def _copy(self):
        ok = False
        if self.j and os.path.exists(self.j.get("output") or ""):
            ok = self.page.backend.copy_file(self.j["output"])
        self.page.toast("Copied — paste into Discord" if ok else "Copy failed", kind="" if ok else "err")

    def set_job(self, j):
        self.j = j
        st = j["status"]
        self.title.set_full(j["title"])
        if st == "running":
            self.sub.setText(f"{j.get('phase') or ''} · {round((j.get('progress') or 0) * 100)}%")
        elif st == "done":
            self.sub.setText(f"Done · {fmt_size(j.get('size'))}")
        elif st == "error":
            self.sub.setText((j.get("error") or "Error").split("\n")[0])
        else:
            self.sub.setText(st.capitalize())
        self.bar.setVisible(st == "running")
        self.bar.setValue(int((j.get("progress") or 0) * 1000))
        self.cancel_b.setVisible(st == "running")
        self.copy_b.setVisible(st == "done")
        self.folder_b.setVisible(st == "done")


class CompressorPage(Page):
    def __init__(self, win):
        super().__init__(win, "Compressor")
        self.top.addWidget(label("Shrink clips to an exact file size", "dim"))
        self.top.addStretch(1)
        self.setAcceptDrops(True)
        b = self.backend
        c = b.settings.get()["compressor"]
        self.files = []
        self.rows = {}
        self.estimates = {}
        self.target = c["defaultTargetMB"]
        self.custom = ""
        self.opts = {"encoder": c["encoder"], "twoPass": c["twoPass"], "autoDownscale": c["autoDownscale"], "audioKbps": c["audioKbps"],
                     "fps": "source", "resolution": "source", "codec": "h264", "format": "mp4", "audioMode": "mix"}
        self._est_gen = 0
        self.est_timer = QTimer(self, singleShot=True, interval=300, timeout=self._run_estimates)

        sa, body = scroll_page(max_width=980)
        self.root.addWidget(sa, 1)

        # --- target size
        body.addWidget(label("TARGET SIZE", "h3"))
        grid = QGridLayout()
        grid.setSpacing(12)
        self.cards = {}
        for i, p in enumerate(SIZE_PRESETS):
            cd = PresetCard(lambda mb=p["mb"]: self.set_target(mb))
            big = QLabel(f"{p['mb']} MB")
            big.setStyleSheet("font-size: 20px; font-weight: 800;")
            cd.lay.addWidget(big)
            nm = QLabel(p["label"])
            nm.setStyleSheet("font-weight: 600;")
            cd.lay.addWidget(nm)
            cd.lay.addWidget(label(p["hint"], "dim", wrap=True))
            cd.lay.addStretch(1)
            grid.addWidget(cd, 0, i)
            self.cards[p["mb"]] = cd
        cd = PresetCard(self._custom_clicked)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("Custom")
        self.custom_edit.setValidator(QIntValidator(1, 100000))
        self.custom_edit.setStyleSheet("font-size: 17px; font-weight: 800; padding: 2px 8px;")
        self.custom_edit.setMinimumWidth(60)
        self.custom_edit.textEdited.connect(self._custom_edited)
        self.custom_edit.installEventFilter(self)
        row.addWidget(self.custom_edit, 1)
        mbl = QLabel("MB")
        mbl.setStyleSheet("font-size: 18px; font-weight: 800;")
        row.addWidget(mbl)
        cd.lay.addLayout(row)
        nm = QLabel("Custom")
        nm.setStyleSheet("font-weight: 600; margin-top: 2px;")
        cd.lay.addWidget(nm)
        cd.lay.addWidget(label("Any size you need", "dim"))
        cd.lay.addStretch(1)
        grid.addWidget(cd, 0, 3)
        self.cards["custom"] = cd
        for i in range(4):
            grid.setColumnStretch(i, 1)
        gw = QWidget()
        gw.setLayout(grid)
        body.addWidget(gw)

        # --- files
        body.addSpacing(12)
        self.clear_b = btn("Clear", on_click=self.clear_files, sm=True, ghost=True)
        body.addWidget(hbox(label("FILES", "h3"), "stretch", self.clear_b, btn(" Add file", "plus", self.pick_files, sm=True, icon_size=14)))
        self.drop = DropZone(self.pick_files)
        body.addWidget(self.drop)
        self.files_card = QFrame()
        self.files_card.setObjectName("setcard")
        self.files_lay = QVBoxLayout(self.files_card)
        self.files_lay.setContentsMargins(0, 0, 0, 0)
        self.files_lay.setSpacing(0)
        body.addWidget(self.files_card)

        # --- options
        body.addSpacing(12)
        o = self.opts
        sec = Section("Options")
        sec.row("Encoder", "CPU (x264 two-pass) hits the target size most precisely and gives the best quality per MB. GPU is much faster.",
                Combo([("x264", "CPU – best quality/size"), ("auto", "GPU – fastest (auto)"), ("nvenc", "NVIDIA NVENC"), ("amf", "AMD AMF"), ("qsv", "Intel QSV")],
                      o["encoder"], lambda v: self.set_opt("encoder", v)))
        sec.row("Codec", "HEVC looks better at the same size but some apps/browsers can’t play it inline.",
                Seg([("h264", "H.264"), ("hevc", "HEVC")], o["codec"], on_change=lambda v: self.set_opt("codec", v)))
        sec.row("Two-pass encoding", "Analyzes the video first for accurate size and better quality (CPU encoder).",
                Toggle(o["twoPass"], lambda v: self.set_opt("twoPass", v)))
        sec.row("Smart downscale", "Automatically lowers resolution / frame rate when the bitrate is too low for a clean picture.",
                Toggle(o["autoDownscale"], lambda v: self.set_opt("autoDownscale", v)))
        sec.row("Max resolution", None, Combo([("source", "Original"), (1440, "1440p"), (1080, "1080p"), (720, "720p"), (480, "480p")],
                                              o["resolution"], lambda v: self.set_opt("resolution", v), width=150))
        sec.row("Max frame rate", None, Combo([("source", "Original"), (60, "60 fps"), (30, "30 fps")], o["fps"], lambda v: self.set_opt("fps", v), width=150))
        sec.row("Audio", "Which audio to keep in the compressed file.",
                Combo([("mix", "Full mix"), ("nomic", "Game only (no mic)"), ("none", "No audio")], o["audioMode"], lambda v: self.set_opt("audioMode", v)),
                Combo([(k, f"{k} kbps") for k in (64, 96, 128, 160, 192)], o["audioKbps"], lambda v: self.set_opt("audioKbps", v)))
        body.addWidget(sec)

        self.go_b = btn(" Compress", "compress", self.start, primary=True, lg=True)
        body.addWidget(hbox("stretch", self.go_b))
        body.addSpacing(12)

        # --- recent jobs
        self.jobs_title = label("RECENT", "h3")
        body.addWidget(self.jobs_title)
        self.jobs_card = QFrame()
        self.jobs_card.setObjectName("setcard")
        self.jobs_lay = QVBoxLayout(self.jobs_card)
        self.jobs_lay.setContentsMargins(0, 0, 0, 0)
        self.jobs_lay.setSpacing(0)
        body.addWidget(self.jobs_card)
        self.job_rows = {}
        self.job_order = []
        body.addStretch(1)

        b.job.connect(self.on_job)
        b.library_changed.connect(self.on_library)
        THUMBS.updated.connect(self.on_thumb)
        self._sync_target()
        self.rebuild_files()
        self.on_job()

    # ---------------------------------------------------------------- target
    @property
    def target_mb(self):
        if self.target == "custom":
            try:
                return float(self.custom) if float(self.custom) > 0 else 0
            except ValueError:
                return 0
        return self.target or 0

    def set_target(self, t):
        self.target = t
        self._sync_target()
        self.files_changed()

    def _custom_clicked(self):
        self.custom_edit.setFocus()
        self.set_target("custom")

    def _custom_edited(self, text):
        self.custom = text
        self.set_target("custom")

    def eventFilter(self, obj, e):
        if obj is self.custom_edit and e.type() == QEvent.FocusIn and self.target != "custom":
            self.set_target("custom")
        return super().eventFilter(obj, e)

    def _sync_target(self):
        for k, cd in self.cards.items():
            cd.set_on(k == self.target)
        self._update_button()

    def _update_button(self):
        n = len(self.files)
        mb = self.target_mb
        self.go_b.setText(f" Compress {f'{n} files ' if n > 1 else ''}to {f'{mb:g}' if mb else '?'} MB")
        self.go_b.setEnabled(bool(n and mb))

    def set_opt(self, k, v):
        self.opts[k] = v
        self.files_changed()

    # ---------------------------------------------------------------- files
    def on_show(self, id=None, ids=None, **_):  # noqa: A002
        want = [id] if id else [x for x in (ids or "").split(",") if x]
        if not want:
            return
        lst = [self.backend.library.get(x) for x in want]
        lst = [c for c in lst if c and c.get("type") != "screenshot"]
        if lst:
            self.files = [file_from_clip(c) for c in lst]
            self.estimates = {}
            self.rebuild_files()

    def pick_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Choose video", "", VIDEO_FILTER)
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths):
        paths = [p for p in paths if VIDEO_RE.search(p)]
        if not paths:
            return
        by_path = {os.path.normcase(os.path.abspath(c["path"])): c for c in self.backend.library.list()}
        known, unknown = [], []
        for p in paths:
            c = by_path.get(os.path.normcase(os.path.abspath(p)))
            (known.append(file_from_clip(c)) if c else unknown.append(p))
        self._append(known)
        if unknown:
            def probe_all():
                out, bad = [], []
                for p in unknown:
                    try:
                        out.append(file_from_probe(p, ffmpeg.probe(p)))
                    except Exception:  # noqa: BLE001
                        bad.append(p)
                return out, bad

            def done(res):
                out, bad = res
                for p in bad:
                    self.toast("Could not read file", p, "err")
                self._append(out)
            run_bg(probe_all, done)

    def _append(self, new):
        have = {f["path"] for f in self.files}
        new = [f for f in new if f["path"] not in have]
        if new:
            self.files += new
            self.rebuild_files()

    def remove_file(self, path):
        self.files = [f for f in self.files if f["path"] != path]
        self.rebuild_files()

    def clear_files(self):
        self.files = []
        self.rebuild_files()

    def rebuild_files(self):
        while self.files_lay.count():
            it = self.files_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.rows = {}
        for i, f in enumerate(self.files):
            r = FileRow(self, f, i == 0)
            self.rows[f["path"]] = r
            self.files_lay.addWidget(r)
        has = bool(self.files)
        self.files_card.setVisible(has)
        self.drop.setVisible(not has)
        self.clear_b.setVisible(has)
        self.files_changed()

    def files_changed(self):
        self._update_button()
        for f in self.files:
            r = self.rows.get(f["path"])
            if r:
                r.set_estimate(None, self.target_mb)
        self._est_gen += 1
        if self.files and self.target_mb:
            self.est_timer.start()

    def _run_estimates(self):
        gen = self._est_gen
        mb = self.target_mb
        work = [(f["path"], project_for(f, self.opts, mb)) for f in self.files]

        def calc():
            out = {}
            for path, proj in work:
                try:
                    out[path] = render.estimate(proj)
                except Exception:  # noqa: BLE001
                    pass
            return out

        def done(est):
            if gen != self._est_gen:
                return
            self.estimates = est
            for path, r in self.rows.items():
                r.set_estimate(est.get(path), mb)
        run_bg(calc, done)

    def on_thumb(self, path):
        for r in self.rows.values():
            if r.f.get("thumb") == path:
                r.update_thumb()

    def on_library(self):
        for f in self.files:
            if f.get("clipId") and not f.get("thumb"):
                c = self.backend.library.get(f["clipId"])
                if c and c.get("thumb"):
                    f["thumb"] = c["thumb"]
                    if f["path"] in self.rows:
                        self.rows[f["path"]].update_thumb()

    # ---------------------------------------------------------------- drag & drop
    @staticmethod
    def _drop_paths(md):
        if not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile() and VIDEO_RE.search(u.toLocalFile())]

    def dragEnterEvent(self, e):
        if self._drop_paths(e.mimeData()):
            e.acceptProposedAction()
            self.drop.set_over(True)
        else:
            e.ignore()

    def dragMoveEvent(self, e):
        if self._drop_paths(e.mimeData()):
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.drop.set_over(False)

    def dropEvent(self, e):
        self.drop.set_over(False)
        paths = self._drop_paths(e.mimeData())
        if paths:
            e.acceptProposedAction()
            self.add_paths(paths)

    # ---------------------------------------------------------------- compress
    def start(self):
        mb = self.target_mb
        if not self.files or not mb:
            return
        o = self.opts
        for f in self.files:
            if f.get("size") and f["size"] <= mb * 1e6 * 0.98 and (f.get("start") or 0) == 0 and f["end"] >= f["duration"] - 0.01 \
                    and o["resolution"] == "source" and o["fps"] == "source":
                self.toast(f"{f['title']} is already under {mb:g} MB", "Compressing anyway")
            self.backend.render(project_for(f, o, mb), f"{f['title']} ({mb:g}MB)", f.get("clipId"), "compress", f.get("game"))
        self.toast(f"Compressing {len(self.files)} file(s)", "Progress is shown below and in the sidebar")

    # ---------------------------------------------------------------- jobs
    def on_job(self, _j=None):
        jobs = [j for j in self.backend.list_jobs() if j.get("kind") == "compress" or JOB_TITLE_RE.search(j["title"])][:8]
        order = [j["id"] for j in jobs]
        if order != self.job_order:
            while self.jobs_lay.count():
                it = self.jobs_lay.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            self.job_rows = {}
            for i, j in enumerate(jobs):
                r = JobRow(self, i == 0)
                self.job_rows[j["id"]] = r
                self.jobs_lay.addWidget(r)
            self.job_order = order
        for j in jobs:
            self.job_rows[j["id"]].set_job(j)
        self.jobs_title.setVisible(bool(jobs))
        self.jobs_card.setVisible(bool(jobs))

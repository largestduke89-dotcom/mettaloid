"""Settings: every option from the legacy React page, grouped into scrollable sections with a jump list."""
import os
import re

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIntValidator
from PySide6.QtWidgets import (QButtonGroup, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from ... import VERSION
from ...core import paths
from .. import bg, icons
from ..theme import C
from ..widgets import (Combo, FSlider, HotkeyEdit, Section, Seg, StatTile, Toggle, bar, btn, fmt_mb, fmt_secs_label, fmt_size, label)
from . import Page
from .home import QCOLOR, BufferControl

SECTIONS = [
    ("recording", "Recording"), ("performance", "Performance & memory"), ("hotkeys", "Hotkeys"), ("quality", "Quality"), ("audio", "Audio"),
    ("autoclip", "Auto clipping"), ("overlay", "Overlay & sounds"), ("storage", "Storage"), ("compressor", "Compressor"), ("editor", "Editor"),
    ("updates", "Updates"), ("windows", "Windows"), ("advanced", "Advanced"),
]
MEM_STEPS = [256, 384, 512, 768, 1024, 1536, 2048, 3072, 4096, 6144, 8192, 12288, 16384]
CLIP_SECS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600, 900, 1200]
PRESET_HINT = {
    "quality": "Best image per bit. Uses the most GPU encoder time.",
    "balanced": "Recommended. Great quality with low overhead.",
    "performance": "Lighter on the GPU. Good for demanding games.",
    "max-performance": "Lowest possible impact on game FPS.",
}
RES_OPTS = [("native", "Native (monitor)"), ("2160", "2160p (4K)"), ("1440", "1440p"), ("1080", "1080p"), ("720", "720p"), ("480", "480p")]
MAX_RES_OPTS = [("native", "Native (monitor)"), ("2160", "2160p"), ("1440", "1440p"), ("1080", "1080p"), ("720", "720p")]
VENDOR_NAME = {"nvenc": "NVIDIA NVENC", "amf": "AMD AMF", "qsv": "Intel Quick Sync", "x264": "x264 (CPU)"}
MONO = "font-family: 'Cascadia Mono', Consolas; font-size: 12px;"


def fmt_gb(mb):
    """Memory-limit label: 1 GB / 1.5 GB / 768 MB."""
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB" if mb % 1024 else f"{mb // 1024} GB"
    return f"{mb} MB"


def fmt_rate(k):
    return f"{k / 1000:.{0 if k >= 10000 else 1}f} Mbps" if k >= 1000 else f"{k} kbps"


def wrap_path(p):
    """Allow long paths to wrap in a word-wrapped QLabel (zero-width space after separators)."""
    return re.sub(r"([\\/])", "\\1\u200b", p or "")


def row_frame(first=False):
    fr = QFrame()
    fr.setObjectName("row")
    if first:
        fr.setProperty("first", True)
    return fr


class _Section(Section):
    """Section whose control column has no extra layout margins (keeps rows compact)."""

    def row(self, *a, **kw):
        fr = super().row(*a, **kw)
        cw = fr.layout().itemAt(fr.layout().count() - 1).widget()
        if cw is not None and cw.layout() is not None:
            cw.layout().setContentsMargins(0, 0, 0, 0)
        return fr


# ---------------------------------------------------------------- plan card (port of PlanCard.jsx)
class PlanCard(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("inset")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(18)
        left = QVBoxLayout()
        left.setSpacing(0)
        self.head = QLabel()
        self.head.setStyleSheet(f"color: {C['text3']}; font-size: 11px; font-weight: 700; letter-spacing: 0.5px;")
        self.big = QLabel()
        self.big.setTextFormat(Qt.RichText)
        self.big.setStyleSheet("font-size: 24px; font-weight: 800;")
        self.qual = QLabel()
        left.addWidget(self.head)
        left.addSpacing(2)
        left.addWidget(self.big)
        left.addWidget(self.qual)
        left.addStretch(1)
        lw = QWidget()
        lw.setLayout(left)
        lw.setMinimumWidth(150)
        top.addWidget(lw, 0, Qt.AlignTop)
        self.kv = QGridLayout()
        self.kv.setHorizontalSpacing(14)
        self.kv.setVerticalSpacing(3)
        self.vals = {}
        for i, k in enumerate(("Resolution", "Video", "Audio", "Replay", "RAM used")):
            kl = QLabel(k)
            kl.setStyleSheet(f"color: {C['text3']}; font-size: 12.5px;")
            vl = QLabel()
            vl.setStyleSheet("font-size: 12.5px;")
            vl.setWordWrap(True)
            self.kv.addWidget(kl, i, 0, Qt.AlignTop)
            self.kv.addWidget(vl, i, 1)
            self.vals[k] = vl
        self.kv.setColumnStretch(1, 1)
        top.addLayout(self.kv, 1)
        lay.addLayout(top)
        self.bar = bar()
        lay.addSpacing(12)
        lay.addWidget(self.bar)
        self.notes = QVBoxLayout()
        self.notes.setSpacing(0)
        lay.addLayout(self.notes)
        self.set_plan(None)

    def set_plan(self, p):
        while self.notes.count():
            w = self.notes.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not p:
            self.head.setText("CALCULATING…")
            self.big.setText("—")
            self.qual.setText("")
            for v in self.vals.values():
                v.setText("")
            self.bar.hide()
            return
        self.head.setText("AUTO QUALITY RESULT" if p.get("auto") else "YOUR SETTINGS")
        self.big.setText(f"{p['height']}p<span style='color:{C['accent']}'>{p['fps']}</span>")
        self.qual.setText(p["quality"])
        self.qual.setStyleSheet(f"font-weight: 700; color: {QCOLOR.get(p['quality'], C['text'])};")
        v = self.vals
        v["Resolution"].setText(f"{p['width']}×{p['height']} @ {p['fps']} fps")
        v["Video"].setText(f"{fmt_rate(p['videoKbps'])} CBR · {p['codec'].upper()}" if p.get("videoKbps") else "Constant quality (variable size)")
        v["Audio"].setText(f"{p['audioTracks']} tracks × {p['audioKbps']} kbps")
        v["Replay"].setText(fmt_secs_label(p["seconds"]))
        est, lim = p.get("estMemoryMB"), p["memoryMB"]
        v["RAM used"].setText(f"{fmt_mb(est)} of {fmt_mb(lim)} limit" if est else f"up to {fmt_mb(lim)} (limit)")
        self.bar.setVisible(bool(est))
        if est:
            self.bar.setValue(int(min(1.0, est / lim) * 1000))
            over = est > lim * 0.9 and not p.get("auto")
            self.bar.setStyleSheet(f"QProgressBar::chunk {{ background: {C['red']}; border-radius: 3px; }}" if over else "")
        for n in p.get("notes") or []:
            warn = bool(re.search(r"only the last|very low|unpredictable", n))
            col = "#fca5a5" if warn else C["text2"]
            rw = QWidget()
            rl = QHBoxLayout(rw)
            rl.setContentsMargins(0, 8, 0, 0)
            rl.setSpacing(6)
            ic = QLabel()
            ic.setPixmap(icons.pixmap("info", col, 14))
            ic.setFixedSize(14, 16)
            rl.addWidget(ic, 0, Qt.AlignTop)
            t = QLabel(n)
            t.setWordWrap(True)
            t.setStyleSheet(f"color: {col}; font-size: 12px;")
            rl.addWidget(t, 1)
            self.notes.addWidget(rw)


# ---------------------------------------------------------------- page
class SettingsPage(Page):
    def __init__(self, win):
        super().__init__(win, "Settings")
        b = self.backend
        self._sync = []          # callables(settings) that push settings → widgets
        self.anchors = {}
        self.devices = {"outputs": [], "inputs": [], "monitors": []}
        self._devices_loading = False
        self._last_obs_state = b.obs.status.get("obsState")
        self._scrolling_to = None
        self.sys = b.perf_stats()

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.root.addLayout(body, 1)

        # left jump list
        side = QFrame()
        side.setFixedWidth(200)
        side.setObjectName("setside")
        side.setStyleSheet(f"QFrame#setside {{ background: {C['bg2']}; border-right: 1px solid {C['line']}; }}")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 14, 10, 14)
        sl.setSpacing(2)
        self.nav = {}
        grp = QButtonGroup(self)
        for sid, text in SECTIONS:
            nb = QPushButton(text.replace("&", "&&"))
            nb.setProperty("nav", True)
            nb.setCheckable(True)
            nb.setCursor(Qt.PointingHandCursor)
            nb.clicked.connect(lambda _=False, s=sid: self.scroll_to(s))
            grp.addButton(nb)
            sl.addWidget(nb)
            self.nav[sid] = nb
        sl.addStretch(1)
        body.addWidget(side)

        # right scroll area
        self.sa = QScrollArea()
        self.sa.setWidgetResizable(True)
        self.sa.setFrameShape(QFrame.NoFrame)
        outer = QWidget()
        ol = QHBoxLayout(outer)
        ol.setContentsMargins(28, 22, 28, 80)
        inner = QWidget()
        inner.setMaximumWidth(860)
        self.body = QVBoxLayout(inner)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(0)
        ol.addWidget(inner, 1)
        ol.addStretch(0)
        self.sa.setWidget(outer)
        body.addWidget(self.sa, 1)
        self.anim = QPropertyAnimation(self.sa.verticalScrollBar(), b"value", self)
        self.anim.setDuration(260)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.anim.finished.connect(lambda: setattr(self, "_scrolling_to", None))
        self.sa.verticalScrollBar().valueChanged.connect(self._track_scroll)

        self._build_recording()
        self._build_performance()
        self._build_hotkeys()
        self._build_quality()
        self._build_audio()
        self._build_autoclip()
        self._build_overlay()
        self._build_storage()
        self._build_compressor()
        self._build_editor()
        self._build_updates()
        self._build_windows()
        self._build_advanced()
        self.body.addStretch(1)
        self.nav["recording"].setChecked(True)

        self.timer = QTimer(self, interval=2000, timeout=self._tick)
        b.settings_changed.connect(self._on_settings)
        b.updater.connect(self._on_updater)
        b.status.connect(self._on_status)
        b.library_changed.connect(self._refresh_storage)
        self._on_settings(b.settings.get())
        self._on_status(dict(b.obs.status))
        self._on_updater(dict(b.updater_svc.state))

    # ================================================================ generic helpers
    @property
    def s(self):
        return self.backend.settings.get()

    def _apply(self, patch):
        return self.backend.update_settings(patch)

    def _setter(self, group, key):
        return lambda v: self._apply({group: {key: v}})

    def section(self, sid, title):
        sec = _Section(title)
        self.anchors[sid] = sec
        self.body.addWidget(sec)
        return sec

    def show_if(self, w, pred):
        self._sync.append(lambda s: w.setVisible(bool(pred(s))))
        return w

    def toggle(self, sec, title, hint, group, key):
        t = Toggle(self.s[group][key], self._setter(group, key))
        self._sync.append(lambda s: t.set_value(s[group][key]))
        return sec.row(title, hint, t)

    def seg(self, sec, title, hint, group, key, options):
        w = Seg(options, self.s[group][key], on_change=self._setter(group, key))
        self._sync.append(lambda s: w.set_value(s[group][key]))
        return sec.row(title, hint, w)

    def combo(self, sec, title, hint, group, key, options, width=None):
        w = Combo(options, self.s[group][key], on_change=self._setter(group, key), width=width)
        self._sync.append(lambda s: w.set_value(s[group][key]))
        return sec.row(title, hint, w)

    def slider(self, sec, title, hint, group, key, mn, mx, step, fmt, label_width=56):
        w = FSlider(mn, mx, step, self.s[group][key], fmt, label_width, on_commit=self._setter(group, key))
        self._sync.append(lambda s: None if w.s.isSliderDown() else w.set_value(s[group][key]))
        fr = sec.row(title, hint, w, wide=True)
        fr.slider = w
        return fr

    # ================================================================ sections
    def _build_recording(self):
        sec = self.section("recording", "Recording")
        self.buffer = BufferControl(self.backend)
        sec.widget(self.buffer)
        self.toggle(sec, "Replay buffer", "Keep the last few seconds/minutes of gameplay in memory so you can clip things after they happen.", "recording", "replayEnabled")
        self.seg(sec, "Capture", "Game capture hooks the game directly (best performance). Display capture records a whole monitor.",
                 "recording", "captureMode", [("game", "Game"), ("desktop", "Display")])
        self.monitor = Combo([], None, on_change=self._setter("recording", "monitorIndex"))
        self.monitor.setMinimumWidth(200)
        self.show_if(sec.row("Monitor", None, self.monitor), lambda s: s["recording"]["captureMode"] == "desktop" or s["recording"]["recordDesktopWhenNoGame"])
        self._sync.append(lambda s: self._fill_monitors())
        self.toggle(sec, "Clip the desktop when no game is running", "Keeps the replay buffer on with display capture outside of games.", "recording", "recordDesktopWhenNoGame")
        self.toggle(sec, "Record full sessions automatically", "Start a full recording when a game launches and stop when it closes. Use the bookmark hotkey to mark moments.",
                    "recording", "autoRecordSessions")
        self.combo(sec, "File format", "MP4 is most compatible. MKV / Hybrid MP4 survive crashes during long recordings.", "recording", "fileFormat",
                   [("mp4", "MP4"), ("hybrid_mp4", "Hybrid MP4 (OBS 30.2+)"), ("mkv", "MKV")])

    def _fill_monitors(self):
        mons = self.devices["monitors"]
        if mons:
            names = [m["name"] for m in mons]
        else:
            names = []
            for i, sc in enumerate(QGuiApplication.screens()):
                g, r = sc.geometry(), sc.devicePixelRatio()
                names.append(f"Monitor {i + 1} ({round(g.width() * r)}×{round(g.height() * r)}){' · primary' if sc == QGuiApplication.primaryScreen() else ''}")
            names = names or ["Primary monitor"]
        self.monitor.set_options(list(enumerate(names)), self.s["recording"]["monitorIndex"])

    # ---------------------------------------------------------------- performance & memory
    def _mem_steps(self):
        max_mem = max(1024, int(self.sys["totalMB"] * 0.6) // 256 * 256)
        return [m for m in MEM_STEPS if m <= max_mem]

    def _mem_current(self, s=None):
        mb = (s or self.s)["performance"]["replayMemoryMB"]
        return mb if mb else self.sys.get("autoMemoryMB", 2048)

    def _build_performance(self):
        sec = self.section("performance", "Performance & memory")
        self.steps = self._mem_steps()
        self.mem_auto = Toggle(not self.s["performance"]["replayMemoryMB"], self._mem_auto_changed)
        auto_lb = label("Automatic", "dim")
        self.mem_row = sec.row("Replay memory limit", " ", auto_lb, self.mem_auto)
        self.mem_auto_info = label("", "dim", wrap=True)
        self.show_if(sec.widget(self.mem_auto_info), lambda s: not s["performance"]["replayMemoryMB"])
        self.mem_slider = FSlider(0, len(self.steps) - 1, 1, 0, lambda i: fmt_gb(self.steps[int(i)]), 70)
        self.mem_slider.lb.setStyleSheet(f"font-family: 'Cascadia Mono', Consolas; font-size: 12.5px; font-weight: 700; color: {C['accent']};")
        self.mem_slider.changed.connect(lambda i: self._refresh_plan({"performance": {"replayMemoryMB": self.steps[int(i)]}}))
        self.mem_slider.committed.connect(lambda i: self._apply({"performance": {"replayMemoryMB": self.steps[int(i)]}}))
        self.show_if(sec.row("Limit", None, self.mem_slider, wide=True), lambda s: s["performance"]["replayMemoryMB"])

        def sync_mem(s):
            auto = not s["performance"]["replayMemoryMB"]
            self.mem_auto.set_value(auto)
            cur = self._mem_current(s)
            self.mem_auto_info.setText(f"Using {fmt_gb(cur)} (about 12% of your RAM). Turn off Automatic to set your own limit.")
            if not self.mem_slider.s.isSliderDown():
                self.mem_slider.set_value(next((i for i, m in enumerate(self.steps) if m >= cur), len(self.steps) - 1))
            self._refresh_sys_hint()
        self._sync.append(sync_mem)

        self.toggle(sec, "Auto quality", "Calculates the best resolution, frame rate and bitrate that fit your replay length in the memory limit. Changes apply instantly.",
                    "performance", "autoQuality")
        auto_q = lambda s: s["performance"]["autoQuality"]  # noqa: E731
        self.show_if(self.combo(sec, "Max resolution", "Auto quality never goes above this.", "performance", "maxResolution", MAX_RES_OPTS), auto_q)
        self.show_if(self.seg(sec, "Max frame rate", None, "performance", "maxFps", [(f, str(f)) for f in (30, 60, 120, 144, 240)]), auto_q)
        self.plan_card = PlanCard()
        sec.widget(self.plan_card)
        self._sync.append(lambda s: self._refresh_plan())
        fr = self.seg(sec, "Encoder speed", " ", "performance", "encoderPreset",
                      [("quality", "Quality"), ("balanced", "Balanced"), ("performance", "Performance"), ("max-performance", "Max perf")])
        self._sync.append(lambda s: fr.hint.setText(PRESET_HINT.get(s["performance"]["encoderPreset"], "")))
        self.toggle(sec, "Adaptive performance", "If the encoder starts dropping frames during a game, automatically switch to a faster encoder speed.", "performance", "adaptive")
        self.combo(sec, "Recorder priority", "Above normal keeps recording smooth when the game maxes out your CPU.", "performance", "obsPriority",
                   [("High", "High"), ("AboveNormal", "Above normal"), ("Normal", "Normal"), ("BelowNormal", "Below normal")])
        self.toggle(sec, "Free memory in the background", "Unloads the Mettaloid window from RAM while it sits in the tray (~150–250 MB). Recording is unaffected.",
                    "performance", "freeUiWhenHidden")
        self.toggle(sec, "Gentle exports", "Exports and compression run at low priority, and with fewer CPU threads while a game is running.", "performance", "lowPriorityJobs")
        self.combo(sec, "Game detection interval", "How often to look for newly launched games. While a game runs, only it is checked, which costs almost nothing.",
                   "performance", "detectionInterval", [(n, f"{n}s") for n in (1, 3, 5, 10)])
        usage = QWidget()
        ul = QVBoxLayout(usage)
        ul.setContentsMargins(0, 0, 0, 0)
        ul.setSpacing(8)
        ul.addWidget(label("Live usage", "dim"))
        grid = QGridLayout()
        grid.setSpacing(10)
        self.tiles = [StatTile(t, compact=True) for t in ("Recorder CPU", "Recorder RAM", "App RAM", "Render time", "Encoder drops", "Render drops")]
        for i, t in enumerate(self.tiles):
            grid.addWidget(t, i // 3, i % 3)
        ul.addLayout(grid)
        sec.widget(usage)

    def _mem_auto_changed(self, v):
        self._apply({"performance": {"replayMemoryMB": 0 if v else self._mem_current()}})

    def _refresh_sys_hint(self):
        sy = self.sys
        self.mem_row.hint.setText(f"How much RAM the replay buffer may use. Your PC has {fmt_gb(sy['totalMB'])} ({fmt_gb(sy['freeMB'])} free).")

    def _refresh_plan(self, patch=None):
        try:
            self.plan_card.set_plan(self.backend.plan(patch))
        except Exception as e:  # noqa: BLE001
            print("plan preview failed:", e)
            self.plan_card.set_plan(None)

    def _tick(self):
        st = self.backend.obs.status.get("stats")
        self.sys = self.backend.perf_stats()
        a = self.sys
        vals = [f"{st['cpu']}%" if st else "—", fmt_mb(st["memoryMB"]) if st else "—", fmt_mb(a["appMB"]),
                f"{st['renderMs']} ms" if st else "—", f"{st['encoderLagPct']}%" if st else "—", f"{st['renderLagPct']}%" if st else "—"]
        warn = [False, False, False, bool(st and st["renderMs"] > 12), bool(st and st["encoderLagPct"] > 1), bool(st and st["renderLagPct"] > 2)]
        for t, v, w in zip(self.tiles, vals, warn):
            t.set(v, w)
        self._refresh_sys_hint()

    # ---------------------------------------------------------------- hotkeys
    def _build_hotkeys(self):
        sec = self.section("hotkeys", "Hotkeys")
        self.clips_box = QWidget()
        self.clips_lay = QVBoxLayout(self.clips_box)
        self.clips_lay.setContentsMargins(0, 0, 0, 0)
        self.clips_lay.setSpacing(0)
        sec.inner.addWidget(self.clips_box)
        sec.count += 1
        self._clips_rendered = None
        self._clip_edits = []
        self._sync.append(self._render_clips)

        add = row_frame()
        al = QHBoxLayout(add)
        al.setContentsMargins(16, 11, 16, 11)
        al.setSpacing(14)
        al.addWidget(btn(" Add clip hotkey", "plus", self._add_clip, sm=True, icon_size=14))
        note = label("The replay buffer automatically holds enough footage for your longest clip hotkey.", "dim", wrap=True)
        al.addWidget(note, 1)
        sec.inner.addWidget(add)
        sec.count += 1

        for title, hint, key in (("Start / stop full recording", None, "toggleRecording"),
                                 ("Bookmark", "Marks a moment while recording a session (saves a 30s clip otherwise).", "bookmark"),
                                 ("Screenshot", None, "screenshot"), ("Mute / unmute microphone", None, "toggleMic"),
                                 ("Pause / resume replay buffer", None, "toggleReplay")):
            he = self._hotkey_edit(self.s["hotkeys"][key])
            he.changed.connect(lambda v, k=key: self._apply({"hotkeys": {k: v}}))
            self._sync.append(lambda s, he=he, k=key: self._sync_hotkey(he, s["hotkeys"][k]))
            sec.row(title, hint, he)

    def _hotkey_edit(self, value):
        he = HotkeyEdit(value, on_pause=self.backend.hotkeys.pause, on_resume=self.backend.register_hotkeys)
        he.setMinimumWidth(170)
        return he

    @staticmethod
    def _sync_hotkey(he, v):
        if not he.listening and he.value != (v or ""):
            he.value = v or ""
            he.render_text()

    def _patch_clip(self, i, **kw):
        clips = [dict(c, **kw) if k == i else dict(c) for k, c in enumerate(self.s["hotkeys"]["clips"])]
        self._apply({"hotkeys": {"clips": clips}})

    def _add_clip(self):
        clips = [dict(c) for c in self.s["hotkeys"]["clips"]]
        clips.append({"accelerator": "", "seconds": 30, "label": f"Clip {len(clips) + 1}"})
        self._apply({"hotkeys": {"clips": clips}})

    def _remove_clip(self, i):
        clips = [dict(c) for k, c in enumerate(self.s["hotkeys"]["clips"]) if k != i]
        if clips:
            self._apply({"hotkeys": {"clips": clips}})

    def _render_clips(self, s):
        clips = s["hotkeys"]["clips"]
        if clips == self._clips_rendered:
            return
        # don't yank widgets away from someone typing / capturing a key
        if any(getattr(e, "listening", False) or (isinstance(e, QLineEdit) and e.hasFocus()) for e in self._clip_edits if not _deleted(e)):
            return
        self._clips_rendered = [dict(c) for c in clips]
        while self.clips_lay.count():
            w = self.clips_lay.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        self._clip_edits = []
        for i, c in enumerate(clips):
            fr = row_frame(first=(i == 0))
            lay = QHBoxLayout(fr)
            lay.setContentsMargins(16, 9, 16, 9)
            lay.setSpacing(12)
            txt = QVBoxLayout()
            txt.setSpacing(0)
            name = QLineEdit(c.get("label") or "")
            name.setPlaceholderText("Clip name")
            name.setStyleSheet(f"QLineEdit {{ font-weight: 600; background: transparent; border: 1px solid transparent; padding: 2px 4px; margin-left: -5px; }}"
                               f"QLineEdit:hover {{ border-color: {C['line2']}; }} QLineEdit:focus {{ border-color: {C['accent']}; background: {C['bg3']}; }}")
            name.editingFinished.connect(lambda i=i, w=name: w.text() != (self.s["hotkeys"]["clips"][i].get("label") or "") and self._patch_clip(i, label=w.text()))
            txt.addWidget(name)
            txt.addWidget(label(f"Saves the last {fmt_secs_label(c['seconds'])}", "dim"))
            lay.addLayout(txt, 1)
            secs = Combo([(v, fmt_secs_label(v)) for v in CLIP_SECS], c["seconds"], on_change=lambda v, i=i: self._patch_clip(i, seconds=v))
            if c["seconds"] not in CLIP_SECS:
                secs.addItem(fmt_secs_label(c["seconds"]), c["seconds"])
                secs.set_value(c["seconds"])
            secs.setFixedWidth(96)
            lay.addWidget(secs)
            he = self._hotkey_edit(c.get("accelerator") or "")
            he.changed.connect(lambda v, i=i: self._patch_clip(i, accelerator=v))
            lay.addWidget(he)
            rm = btn("", "x", lambda i=i: self._remove_clip(i), ghost=True, sm=True, tip="Remove", icon_size=14)
            rm.setFixedWidth(30)
            rm.setEnabled(len(clips) > 1)
            lay.addWidget(rm)
            fr.setMinimumHeight(54)
            self.clips_lay.addWidget(fr)
            self._clip_edits += [name, he]

    # ---------------------------------------------------------------- quality
    def _build_quality(self):
        sec = self.section("quality", "Quality")
        auto = lambda s: s["performance"]["autoQuality"]  # noqa: E731
        manual = lambda s: not s["performance"]["autoQuality"]  # noqa: E731
        self.show_if(sec.row("Resolution, frame rate and bitrate are set automatically",
                             "Auto quality picks the best settings for your memory limit. Turn it off under Performance & memory to set them by hand.",
                             btn("Open", None, lambda: self.scroll_to("performance"), sm=True)), auto)
        self.show_if(self.combo(sec, "Resolution", None, "quality", "resolution", RES_OPTS), manual)
        self.show_if(self.seg(sec, "Frame rate", None, "quality", "fps", [(f, str(f)) for f in (30, 60, 120, 144)]), manual)
        self.show_if(self.seg(sec, "Rate control", "CBR = predictable size. CQP = constant quality (size varies).", "quality", "rateControl",
                              ["CBR", "VBR", "CQP"]), manual)
        br = self.slider(sec, "Bitrate", " ", "quality", "bitrateKbps", 5000, 150000, 1000, lambda v: f"{round(v / 1000)} Mbps", 70)
        br.slider.changed.connect(lambda v: br.hint.setText(self._bitrate_hint(v)))
        self._sync.append(lambda s: br.hint.setText(self._bitrate_hint(s["quality"]["bitrateKbps"])))
        self.show_if(br, lambda s: manual(s) and s["quality"]["rateControl"] != "CQP")
        cq = self.slider(sec, "Quality level (CQ)", "Lower = better quality, bigger files. 18–23 is typical.", "quality", "cqLevel", 12, 35, 1, str, 30)
        self.show_if(cq, lambda s: manual(s) and s["quality"]["rateControl"] == "CQP")
        self.seg(sec, "Codec", "HEVC/AV1 need a recent GPU. H.264 plays everywhere.", "quality", "codec", [("h264", "H.264"), ("hevc", "HEVC"), ("av1", "AV1")])
        self.enc_row = self.combo(sec, "Encoder", " ", "quality", "encoder",
                                  [("auto", "Auto-detect"), ("nvenc", "NVIDIA NVENC"), ("amf", "AMD AMF"), ("qsv", "Intel Quick Sync"), ("x264", "x264 (CPU)")])

    @staticmethod
    def _bitrate_hint(k):
        return f"{k / 1000:.0f} Mbps ≈ {round(k * 60 / 8 / 1000)} MB per minute"

    # ---------------------------------------------------------------- audio
    def _build_audio(self):
        sec = self.section("audio", "Audio")
        self.out_combo = self._device_combo("desktopDevice")
        sec.row("Desktop / game audio device", None, self.out_combo)
        pct = lambda v: f"{round(v * 100)}%"  # noqa: E731
        self.slider(sec, "Desktop volume", None, "audio", "desktopVolume", 0, 2, 0.05, pct, 46)
        self.toggle(sec, "Record game audio only", "Captures just the game process instead of everything you hear (no music/browser). Windows 10 2004+.", "audio", "gameAudioOnly")
        self.toggle(sec, "Record microphone", None, "audio", "micEnabled")
        self.in_combo = self._device_combo("micDevice")
        sec.row("Microphone", None, self.in_combo)
        self.slider(sec, "Microphone volume", None, "audio", "micVolume", 0, 2, 0.05, pct, 46)
        self.toggle(sec, "Noise suppression", "RNNoise removes keyboard clicks, fans and background noise from your mic.", "audio", "noiseSuppression")
        self.toggle(sec, "Separate Discord track", "Records Discord voice on its own track so you can adjust it in the editor.", "audio", "separateDiscordTrack")
        self.combo(sec, "Audio bitrate", "Per track. Auto quality may lower it when memory is tight.", "audio", "audioBitrate", [(k, f"{k} kbps") for k in (128, 160, 192, 256, 320)])
        self.dev_note = label("", "dim", wrap=True)
        sec.widget(self.dev_note, (16, 10, 16, 10))
        self._sync.append(lambda s: self._fill_audio_devices())

    def _device_combo(self, key):
        cb = Combo([("default", "Default")], self.s["audio"][key], on_change=self._setter("audio", key))
        cb.setSizeAdjustPolicy(Combo.AdjustToMinimumContentsLengthWithIcon)
        cb.setMinimumContentsLength(24)
        cb.setMaximumWidth(320)
        return cb

    def _fill_audio_devices(self):
        a = self.s["audio"]
        for cb, lst, key in ((self.out_combo, self.devices["outputs"], "desktopDevice"), (self.in_combo, self.devices["inputs"], "micDevice")):
            opts = [("default", "Default")] + [(d["id"], d["name"]) for d in lst if d["id"] != "default"]
            cur = a[key] or "default"
            if cur not in [o[0] for o in opts]:
                opts.append((cur, "Saved device (not connected)" if lst else "Saved device"))
            cb.set_options(opts, cur)
            cb.setToolTip(cb.currentText())
        n = "Tracks in every recording: 1 = full mix · 2 = game/desktop · 3 = microphone · 4 = Discord"
        if self._devices_loading:
            n += "<br>Loading audio devices…"
        elif not (self.devices["outputs"] or self.devices["inputs"]):
            n += "<br>Device lists appear once the recorder is running."
        self.dev_note.setText(n)

    def _load_devices(self):
        if self._devices_loading:
            return
        self._devices_loading = True
        self._fill_audio_devices()

        def done(d):
            self._devices_loading = False
            if isinstance(d, dict):
                self.devices = {k: d.get(k) or [] for k in ("outputs", "inputs", "monitors")}
            self._fill_audio_devices()
            self._fill_monitors()

        def fail(e):
            self._devices_loading = False
            print("device list failed:", e)
            self._fill_audio_devices()
        try:
            bg.when_done(self.backend.obs.devices(), done, fail)
        except Exception as e:  # noqa: BLE001
            fail(e)

    # ---------------------------------------------------------------- auto clip
    def _build_autoclip(self):
        sec = self.section("autoclip", "Auto clipping")
        self.toggle(sec, "Auto-clip API", "Lets games, mods, scripts or a Stream Deck trigger clips on events (kills, wins…).", "autoClip", "enabled")
        on = lambda s: s["autoClip"]["enabled"]  # noqa: E731
        self.show_if(self.combo(sec, "Seconds before the event", None, "autoClip", "secondsBefore", [(v, f"{v}s") for v in (5, 10, 15, 20, 30, 45, 60)]), on)
        self.show_if(self.combo(sec, "Seconds after the event", None, "autoClip", "secondsAfter", [(v, f"{v}s") for v in (0, 2, 3, 5, 8, 10, 15)]), on)
        port = QLineEdit(str(self.s["autoClip"]["apiPort"]))
        port.setValidator(QIntValidator(1024, 65535, port))
        port.setFixedWidth(100)

        def commit_port():
            v = int(port.text()) if port.hasAcceptableInput() else self.s["autoClip"]["apiPort"]
            port.setText(str(v))
            if v != self.s["autoClip"]["apiPort"]:
                self._apply({"autoClip": {"apiPort": v}})
        port.editingFinished.connect(commit_port)
        self._sync.append(lambda s: None if port.hasFocus() else port.setText(str(s["autoClip"]["apiPort"])))
        self.show_if(sec.row("Port", None, port), on)
        snip = QWidget()
        sl = QVBoxLayout(snip)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(8)
        sl.addWidget(label("Trigger from anything on this PC (while recording a session, events become bookmarks):", "dim", wrap=True))
        code = QLabel()
        code.setTextInteractionFlags(Qt.TextSelectableByMouse)
        code.setStyleSheet(f"{MONO} background: {C['bg']}; border: 1px solid {C['line']}; border-radius: 8px; padding: 10px 12px; color: {C['text2']};")
        code.setWordWrap(True)
        sl.addWidget(code)

        def sync_code(s):
            p = s["autoClip"]["apiPort"]
            code.setText(f'curl -X POST http://127.0.0.1:{p}/clip -d "{{\\"event\\":\\"Ace\\",\\"before\\":20,\\"after\\":5}}"\n'
                         f'curl -X POST http://127.0.0.1:{p}/bookmark -d "{{\\"label\\":\\"Clutch\\"}}"\n'
                         f"curl http://127.0.0.1:{p}/status")
        self._sync.append(sync_code)
        self.show_if(sec.widget(snip), on)

    # ---------------------------------------------------------------- overlay
    def _build_overlay(self):
        sec = self.section("overlay", "Overlay & sounds")
        self.toggle(sec, "In-game notifications", "Small popup when a clip is saved (borderless/windowed games).", "overlay", "enabled")
        self.combo(sec, "Position", None, "overlay", "position",
                   [("top-right", "Top right"), ("top-left", "Top left"), ("bottom-right", "Bottom right"), ("bottom-left", "Bottom left")])
        self.toggle(sec, "Capture sounds", "Plays a sound when a clip is saved — works in every game.", "overlay", "sound")
        self.slider(sec, "Sound volume", None, "overlay", "soundVolume", 0, 1, 0.05, lambda v: f"{round(v * 100)}%", 46)

    # ---------------------------------------------------------------- storage
    def _build_storage(self):
        sec = self.section("storage", "Storage")
        folder = sec.row("Clip folder", " ", btn(" Open", "folder", lambda: self._open(self.s["general"]["clipFolder"]), sm=True, icon_size=14),
                         btn("Change…", None, self._pick_clip_folder, sm=True))
        self._sync.append(lambda s: folder.hint.setText(wrap_path(s["general"]["clipFolder"])))
        st = QWidget()
        stl = QVBoxLayout(st)
        stl.setContentsMargins(0, 0, 0, 0)
        stl.setSpacing(8)
        top = QHBoxLayout()
        self.st_used = QLabel()
        self.st_used.setStyleSheet("font-weight: 700;")
        self.st_free = label("", "dim")
        top.addWidget(self.st_used, 1)
        top.addWidget(self.st_free)
        stl.addLayout(top)
        self.st_bar = bar()
        stl.addWidget(self.st_bar)
        sec.widget(st)
        self._sync.append(lambda s: self._refresh_storage())
        self.toggle(sec, "Automatic cleanup", "Delete the oldest clips (to the Recycle Bin) when the library exceeds the limit. Favorites, locked clips and clips in albums are never deleted.",
                    "storage", "autoCleanup")
        self.show_if(self.slider(sec, "Storage limit", None, "storage", "maxGB", 5, 2000, 5, lambda v: f"{v} GB", 70), lambda s: s["storage"]["autoCleanup"])
        sec.row("Watch folders", "Automatically import recordings from OBS, ShadowPlay, etc.", btn(" Add folder", "plus", self._add_watch, sm=True, icon_size=14))
        self.watch_box = QWidget()
        self.watch_lay = QVBoxLayout(self.watch_box)
        self.watch_lay.setContentsMargins(0, 0, 0, 0)
        self.watch_lay.setSpacing(0)
        sec.inner.addWidget(self.watch_box)
        sec.count += 1
        self._watch_rendered = None
        self._sync.append(self._render_watch)
        sec.row("Rescan library", "Find files added to the clip folder manually and remove missing ones.", btn(" Rescan", "refresh", self._rescan, sm=True, icon_size=14))

    def _refresh_storage(self):
        try:
            st = self.backend.library.storage_stats()
        except Exception as e:  # noqa: BLE001
            print("storage stats failed:", e)
            return
        self.st_used.setText(f"{fmt_size(st['used']) or '0 KB'} used by {st['count']} item{'' if st['count'] == 1 else 's'}")
        self.st_free.setText(f"{fmt_size(st['free'])} free of {fmt_size(st['total'])}" if st.get("free") is not None else "")
        self.st_bar.setVisible(bool(st.get("total")))
        if st.get("total"):
            self.st_bar.setValue(int((st["total"] - st["free"]) / st["total"] * 1000))

    def _render_watch(self, s):
        folders = list(s["watchFolders"])
        if folders == self._watch_rendered:
            return
        self._watch_rendered = folders
        while self.watch_lay.count():
            w = self.watch_lay.takeAt(0).widget()
            if w:
                w.hide()
                w.deleteLater()
        for f in folders:
            fr = row_frame()
            lay = QHBoxLayout(fr)
            lay.setContentsMargins(16, 6, 16, 6)
            lay.setSpacing(10)
            ic = QLabel()
            ic.setPixmap(icons.pixmap("folder", C["text3"], 14))
            lay.addWidget(ic)
            p = QLabel(wrap_path(f))
            p.setWordWrap(True)
            p.setStyleSheet(MONO)
            p.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(p, 1)
            rm = btn("", "x", lambda f=f: self._apply({"watchFolders": [x for x in self.s["watchFolders"] if x != f]}), ghost=True, sm=True, tip="Stop watching", icon_size=14)
            rm.setFixedWidth(30)
            lay.addWidget(rm)
            fr.setMinimumHeight(40)
            self.watch_lay.addWidget(fr)

    def _pick_folder(self, title, start=""):
        f = QFileDialog.getExistingDirectory(self, title, start or "")
        return f.replace("\\", "/") if f else None

    def _pick_clip_folder(self):
        f = self._pick_folder("Choose clip folder", self.s["general"]["clipFolder"])
        if f:
            self._apply({"general": {"clipFolder": f}})

    def _add_watch(self):
        f = self._pick_folder("Folder to watch")
        if f and f not in self.s["watchFolders"]:
            self._apply({"watchFolders": [*self.s["watchFolders"], f]})

    def _rescan(self):
        self.toast("Rescanning library…")

        def done(_):
            self.toast("Library rescanned")
            self._refresh_storage()
        bg.run_bg(self.backend.library.scan, done, lambda e: self.toast("Rescan failed", str(e), "err"))

    def _open(self, p):
        try:
            if p and not os.path.exists(p):
                os.makedirs(p, exist_ok=True)
            self.backend.open_path(p)
        except Exception as e:  # noqa: BLE001
            self.toast("Could not open folder", str(e), "err")

    # ---------------------------------------------------------------- compressor / editor
    def _build_compressor(self):
        sec = self.section("compressor", "Compressor")
        self.seg(sec, "Default target size", None, "compressor", "defaultTargetMB", [(m, f"{m} MB") for m in (20, 50, 500)])
        self.combo(sec, "Default encoder", None, "compressor", "encoder", [("x264", "CPU – best quality/size"), ("auto", "GPU – fastest")])
        self.toggle(sec, "Two-pass encoding", None, "compressor", "twoPass")
        self.toggle(sec, "Smart downscale", None, "compressor", "autoDownscale")
        self.combo(sec, "Audio bitrate", None, "compressor", "audioKbps", [(k, f"{k} kbps") for k in (64, 96, 128, 160, 192)])

    def _build_editor(self):
        sec = self.section("editor", "Editor")
        fr = sec.row("Text font", " ", btn("Choose font…", None, self._pick_font, sm=True))
        self._sync.append(lambda s: fr.hint.setText(wrap_path(s["editor"]["fontPath"])))

    def _pick_font(self):
        cur = self.s["editor"]["fontPath"]
        f, _ = QFileDialog.getOpenFileName(self, "Choose font", os.path.dirname(cur) if cur else "C:/Windows/Fonts", "Fonts (*.ttf *.otf)")
        if f:
            self._apply({"editor": {"fontPath": f.replace("\\", "/")}})

    # ---------------------------------------------------------------- updates
    def _build_updates(self):
        sec = self.section("updates", "Updates")
        svc = self.backend.updater_svc
        self.upd_check = btn(" Check now", "refresh", svc.check, sm=True, icon_size=14)
        self.upd_download = btn("Update now", None, svc.update_now, primary=True, sm=True)
        self.upd_install = btn("Restart && update", None, svc.update_now, primary=True, sm=True)
        self.upd_row = sec.row(f"Mettaloid {VERSION}", " ", self.upd_check, self.upd_download, self.upd_install)
        self.upd_bar = bar()
        self.upd_bar_row = sec.widget(self.upd_bar, (16, 14, 16, 14))
        self.upd_bar_row.hide()
        self.toggle(sec, "Check for updates automatically", None, "updates", "autoCheck")
        sec.row("Updates tab", "Release notes, update history and downloads.", btn(" Open", "download", lambda: self.go("updates"), sm=True, icon_size=14))
        self.seg(sec, "Update channel", None, "updates", "channel", [("latest", "Stable"), ("beta", "Beta")])
        self._sync.append(lambda s: self._on_updater(dict(self.backend.updater_svc.state)))

    def _install_update(self):
        self.win.app.quit()  # the downloaded installer is launched on quit

    def _on_updater(self, u):
        st = (u or {}).get("status")
        prog = u.get("progress") or 0
        hint = {
            "dev": "Updates are disabled in development builds.",
            "checking": "Checking for updates…",
            "latest": "You’re up to date.",
            "available": f"Version {u.get('available')} is available — see the Updates tab for what's new.",
            "downloading": f"Downloading {u.get('available')}… {round(prog * 100)}%",
            "ready": f"Version {u.get('available')} is ready to install.",
            "error": f"Update error: {u.get('error')}",
        }.get(st, "")
        self.upd_row.hint.setText(hint)
        self.upd_row.hint.setStyleSheet("color: #fca5a5;" if st == "error" else "")
        ready = st == "ready"
        dl = st == "available"
        self.upd_install.setVisible(ready)
        self.upd_download.setVisible(dl)
        self.upd_check.setVisible(not ready and not dl)
        self.upd_check.setEnabled(st not in ("dev", "checking", "downloading"))
        self.upd_bar_row.setVisible(st == "downloading")
        self.upd_bar.setValue(int(prog * 1000))

    # ---------------------------------------------------------------- windows / advanced
    def _build_windows(self):
        sec = self.section("windows", "Windows")
        self.toggle(sec, "Launch when Windows starts", "Starts minimized in the tray so it’s ready when you play.", "general", "launchOnStartup")
        self.toggle(sec, "Start minimized", None, "general", "startMinimized")
        self.toggle(sec, "Closing the window keeps Mettaloid running in the tray", None, "general", "closeToTray")

    def _build_advanced(self):
        sec = self.section("advanced", "Advanced")
        self.engine_row = sec.row("Recording engine", " ", btn(" Restart recorder", "refresh", self._restart_recorder, sm=True, icon_size=14))
        self.obs_reset = btn("Reset", None, lambda: self._apply({"advanced": {"obsPath": ""}}), sm=True)
        obs = sec.row("Custom OBS folder", " ", btn("Choose…", None, self._pick_obs, sm=True), self.obs_reset)

        def sync_obs(s):
            p = (s.get("advanced") or {}).get("obsPath") or ""
            obs.hint.setText(wrap_path(p) if p else "Using the bundled OBS Studio")
            self.obs_reset.setVisible(bool(p))
        self._sync.append(sync_obs)
        self.hw_btn = btn("Detect", None, self._detect_hw, sm=True)
        self.hw_row = sec.row("Hardware", "Detect GPUs and hardware encoders", self.hw_btn)
        sec.row("App data", wrap_path(paths.USER_DATA), btn(" Open", "folder", lambda: self._open(paths.USER_DATA), sm=True, icon_size=14))
        sec.row("Setup wizard", "Go through the first-run questions again (presets: Minimal / Everything on / Custom).",
                btn(" Run setup again", "refresh", lambda: self.win.app.run_onboarding(self.win), sm=True, icon_size=14))
        logs_dir = os.path.join(paths.USER_DATA, "logs")
        sec.row("Logs", "Send these when reporting a problem.",
                btn(" Open", "folder", lambda: self._open(logs_dir), sm=True, icon_size=14),
                btn(" Copy log file", "copy", lambda: (self.backend.copy_file(os.path.join(logs_dir, "mettaloid.log")), self.toast("Log file copied", "Paste it into Discord or an email")), sm=True, icon_size=14))

    def _restart_recorder(self):
        try:
            self.backend.obs.restart()
            self.toast("Restarting recorder…")
        except Exception as e:  # noqa: BLE001
            self.toast("Could not restart recorder", str(e), "err")

    def _pick_obs(self):
        f = self._pick_folder("OBS Studio folder (contains bin\\64bit\\obs64.exe)")
        if f:
            self._apply({"advanced": {"obsPath": f}})

    def _detect_hw(self):
        self.hw_btn.setEnabled(False)
        self.hw_row.hint.setText("Detecting…")

        def work():
            from ...core import ffmpeg
            from ...core.obs_manager import detect_vendor
            _, gpus = detect_vendor()
            return gpus, ffmpeg.hw_encoders()

        def done(r):
            gpus, encs = r
            self.hw_btn.setEnabled(True)
            self.hw_row.hint.setText(f"GPU: {', '.join(gpus) or 'unknown'} · FFmpeg HW encoders: {', '.join(encs) or 'none'}")

        def fail(e):
            self.hw_btn.setEnabled(True)
            self.hw_row.hint.setText(f"Detection failed: {e}")
        bg.run_bg(work, done, fail)

    # ================================================================ events
    def _on_settings(self, s):
        for fn in self._sync:
            try:
                fn(s)
            except Exception as e:  # noqa: BLE001
                print("settings sync failed:", e)
        self.buffer.refresh()

    def _on_status(self, st):
        enc = st.get("encoder")
        self.enc_row.hint.setText(f"In use: {enc}" if enc else "Hardware encoders barely affect FPS.")
        parts = [f"OBS Studio {st.get('obsVersion') or '?'}", st.get("obsState") or ""]
        if enc:
            parts.append(enc)
        elif st.get("vendor"):
            parts.append(VENDOR_NAME.get(st["vendor"], st["vendor"]))
        self.engine_row.hint.setText(" · ".join(p for p in parts if p) + (f"\n{st['error']}" if st.get("error") else ""))
        state = st.get("obsState")
        if state != self._last_obs_state:
            self._last_obs_state = state
            if state == "ready" and self.isVisible():
                self._load_devices()

    def on_show(self, section=None, **_):
        self.sys = self.backend.perf_stats()
        self._on_settings(self.backend.settings.get())
        self._on_updater(dict(self.backend.updater_svc.state))
        self._tick()
        self.timer.start()
        if not (self.devices["outputs"] or self.devices["inputs"]):
            self._load_devices()
        if section:
            QTimer.singleShot(80, lambda: self.scroll_to(section, animate=False))

    def on_hide(self):
        self.timer.stop()

    # ================================================================ navigation
    def _section_y(self, sid):
        w = self.anchors.get(sid)
        return w.mapTo(self.sa.widget(), QPoint(0, 0)).y() - 14 if w else 0

    def scroll_to(self, sid, animate=True):
        if sid not in self.anchors:
            return
        self.nav[sid].setChecked(True)
        sb = self.sa.verticalScrollBar()
        y = max(0, min(sb.maximum(), self._section_y(sid)))
        self.anim.stop()
        if animate and abs(sb.value() - y) > 4:
            self._scrolling_to = sid
            self.anim.setStartValue(sb.value())
            self.anim.setEndValue(y)
            self.anim.start()
        else:
            self._scrolling_to = sid
            sb.setValue(y)
            self._scrolling_to = None

    def _track_scroll(self, v):
        if self._scrolling_to:
            return
        cur = SECTIONS[0][0]
        for sid, _ in SECTIONS:
            if self.anchors[sid].isVisible() and self._section_y(sid) <= v + 40:
                cur = sid
        if v >= self.sa.verticalScrollBar().maximum() - 2 and v > 0:
            # bottom reached: keep whatever was explicitly selected if it is on screen
            checked = next((k for k, b in self.nav.items() if b.isChecked()), cur)
            if self._section_y(checked) >= self._section_y(cur):
                cur = checked
        if not self.nav[cur].isChecked():
            self.nav[cur].setChecked(True)


def _deleted(w):
    try:
        w.objectName()
        return False
    except RuntimeError:
        return True



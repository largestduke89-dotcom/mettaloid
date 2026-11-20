"""Home: recorder status, replay length (with live auto-quality preview), live stats, recent clips."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import icons
from ..clipgrid import ClipGrid
from ..theme import C
from ..widgets import Badge, FSlider, GameIcon, StatTile, btn, fmt_dur, fmt_mb, fmt_secs_label, fmt_size, hbox, label, scroll_page
from . import Page

BUFFER_STEPS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 420, 600, 900, 1200]
QCOLOR = {"Excellent": "#4ade80", "Great": "#86efac", "Good": C["accent2"], "Fair": "#fb923c", "Low": "#f87171", "Variable": C["text2"]}


def plan_summary(p):
    if not p:
        return ""
    rate = f"{p['videoKbps'] / 1000:.0f} Mbps" if p.get("videoKbps") else "CQP"
    return f"{p['height']}p{p['fps']} · {rate} · <b style='color:{QCOLOR.get(p['quality'], C['text'])}'>{p['quality']}</b>"


class BufferControl(QFrame):
    """Replay length slider with live plan preview. Used on Home and in Settings."""

    def __init__(self, backend):
        super().__init__()
        self.backend = backend
        self.setObjectName("inset")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(1)
        t = QLabel("Replay length")
        t.setStyleSheet("font-weight: 700;")
        left.addWidget(t)
        self.ram = label("", "dim")
        left.addWidget(self.ram)
        self.plan_lb = QLabel()
        self.plan_lb.setStyleSheet("font-size: 12px;")
        left.addWidget(self.plan_lb)
        w = QWidget()
        w.setLayout(left)
        w.setFixedWidth(210)
        lay.addWidget(w)
        self.slider = FSlider(0, len(BUFFER_STEPS) - 1, 1, 0)
        lay.addWidget(self.slider, 1)
        self.val = QLabel()
        self.val.setStyleSheet(f"font-size: 20px; font-weight: 800; color: {C['accent']};")
        self.val.setFixedWidth(96)
        self.val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.val)
        self.slider.changed.connect(self._preview)
        self.slider.committed.connect(self._commit)
        self.refresh()

    def _idx(self, secs):
        return next((i for i, s in enumerate(BUFFER_STEPS) if s >= secs), len(BUFFER_STEPS) - 1)

    def refresh(self):
        s = self.backend.settings.get()
        self.slider.set_value(self._idx(s["recording"]["replaySeconds"]))
        self._preview(self.slider.value())

    def _patch(self, secs):
        s = self.backend.settings.get()
        clips = [dict(c, seconds=secs) if i == 0 else c for i, c in enumerate(s["hotkeys"]["clips"])]
        return {"recording": {"replaySeconds": secs}, "hotkeys": {"clips": clips}}

    def _preview(self, i):
        secs = BUFFER_STEPS[int(i)]
        self.val.setText(fmt_secs_label(secs))
        p = self.backend.plan(self._patch(secs))
        self.ram.setText(f"{fmt_mb(p['estMemoryMB'])} RAM" if p.get("estMemoryMB") else "")
        self.plan_lb.setText(plan_summary(p))

    def _commit(self, i):
        self.backend.update_settings(self._patch(BUFFER_STEPS[int(i)]))


class HomePage(Page):
    def __init__(self, win):
        super().__init__(win, "Home")
        sa, body = scroll_page()
        self.root.addWidget(sa, 1)
        b = self.backend

        self.err = QFrame()
        self.err.setObjectName("card")
        self.err.setStyleSheet("QFrame#card { border-color: rgba(239,68,68,0.45); }")
        el = QHBoxLayout(self.err)
        el.setContentsMargins(16, 12, 16, 12)
        self.err_lb = label("", wrap=True)
        el.addWidget(self.err_lb, 1)
        el.addWidget(btn("Restart recorder", "refresh", lambda: b.obs.restart()))
        body.addWidget(self.err)

        hero = QFrame()
        hero.setObjectName("hero")
        hl = QGridLayout(hero)
        hl.setContentsMargins(22, 22, 22, 22)
        hl.setHorizontalSpacing(22)
        hl.setVerticalSpacing(16)
        g = QHBoxLayout()
        g.setSpacing(16)
        self.gicon = GameIcon("", 64)
        g.addWidget(self.gicon, 0, Qt.AlignTop)
        info = QVBoxLayout()
        info.setSpacing(6)
        self.gtitle = QLabel()
        self.gtitle.setStyleSheet("font-size: 21px; font-weight: 750;")
        info.addWidget(self.gtitle)
        self.badges = QHBoxLayout()
        self.badges.setSpacing(6)
        self.b_rec, self.b_replay, self.b_res, self.b_enc, self.b_mic = Badge(), Badge(), Badge(), Badge(), Badge()
        for x in (self.b_rec, self.b_replay, self.b_res, self.b_enc, self.b_mic):
            self.badges.addWidget(x)
        self.badges.addStretch(1)
        info.addLayout(self.badges)
        self.hint = label("", "dim", wrap=True)
        self.hint.setTextFormat(Qt.RichText)
        info.addWidget(self.hint)
        g.addLayout(info, 1)
        hl.addLayout(g, 0, 0)
        acts = QVBoxLayout()
        acts.setSpacing(10)
        self.save_b = btn("  Save clip", "clip", lambda: b.clip(), primary=True, lg=True)
        acts.addWidget(self.save_b)
        row = QHBoxLayout()
        self.rec_b = btn("  Record", "record", b.toggle_recording)
        row.addWidget(self.rec_b, 1)
        self.bm_b = btn("", "bookmark", b.bookmark, tip="Bookmark / quick clip")
        self.shot_b = btn("", "camera", b.screenshot, tip="Screenshot")
        self.mic_b = btn("", "mic", b.toggle_mic, tip="Toggle microphone")
        for x in (self.bm_b, self.shot_b, self.mic_b):
            x.setFixedWidth(38)
            row.addWidget(x)
        acts.addLayout(row)
        self.replay_b = btn("Pause replay buffer", None, b.toggle_replay, ghost=True, sm=True)
        acts.addWidget(self.replay_b)
        aw = QWidget()
        aw.setLayout(acts)
        aw.setFixedWidth(240)
        hl.addWidget(aw, 0, 1)
        self.buffer = BufferControl(b)
        hl.addWidget(self.buffer, 1, 0, 1, 2)
        body.addWidget(hero)

        perf = QGridLayout()
        perf.setSpacing(12)
        self.p_tiles = [StatTile(t, compact=True) for t in ("Recorder CPU", "Recorder RAM", "App RAM", "Render time", "Encoder drops", "Render drops")]
        for i, t in enumerate(self.p_tiles):
            perf.addWidget(t, 0, i)
        pw = QWidget()
        pw.setLayout(perf)
        body.addWidget(pw)
        stats = QGridLayout()
        stats.setSpacing(12)
        self.s_tiles = [StatTile(t) for t in ("Clips", "Sessions", "Library size", "Free disk")]
        for i, t in enumerate(self.s_tiles):
            stats.addWidget(t, 0, i)
        sw = QWidget()
        sw.setLayout(stats)
        body.addWidget(sw)

        body.addSpacing(10)
        body.addWidget(hbox(label("Recent", "h2"), "stretch", btn("View library", None, lambda: self.go("library"), ghost=True, sm=True)))
        self.recent = ClipGrid(240, selectable=False)
        self.recent.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.recent.setFixedHeight(2 * (int(240 * 9 / 16) + 60) + 4)
        self.recent.opened.connect(lambda c: self.go("clip", id=c["id"]))
        body.addWidget(self.recent)
        self.empty = label("No clips yet — launch a game and press F8 after something cool happens.", "muted")
        body.addWidget(self.empty)
        body.addStretch(1)

        b.status.connect(self.on_status)
        b.library_changed.connect(self.refresh_lib)
        b.settings_changed.connect(lambda s: self.buffer.refresh())
        self.timer = QTimer(self, interval=1000, timeout=self.tick)
        self.on_status(dict(b.obs.status))
        self.refresh_lib()

    def on_show(self, **_):
        self.timer.start()
        self.buffer.refresh()
        self.tick()

    def on_hide(self):
        self.timer.stop()

    def tick(self):
        st = self.backend.obs.status
        s = st.get("stats")
        a = self.backend.perf_stats()
        vals = [f"{s['cpu']}%" if s else "—", fmt_mb(s["memoryMB"]) if s else "—", fmt_mb(a["appMB"]),
                f"{s['renderMs']} ms" if s else "—", f"{s['encoderLagPct']}%" if s else "—", f"{s['renderLagPct']}%" if s else "—"]
        warn = [False, False, False, bool(s and s["renderMs"] > 12), bool(s and s["encoderLagPct"] > 1), bool(s and s["renderLagPct"] > 2)]
        for t, v, w in zip(self.p_tiles, vals, warn):
            t.set(v, w)
        if st["recording"] and st.get("recordStartedAt"):
            self.b_rec.set("● REC " + fmt_dur(time.time() - st["recordStartedAt"] / 1000), "rec")

    def on_status(self, st):
        s = self.backend.settings.get()
        ready = st["obsState"] == "ready"
        g = st.get("game")
        self.err.setVisible(st["obsState"] in ("missing", "error") or bool(st.get("error")))
        self.err_lb.setText(f"<b>{'Recording engine missing' if st['obsState'] == 'missing' else 'Recorder problem'}</b><br>{st.get('error') or ''}")
        self.gicon.setVisible(bool(g))
        if g:
            self.gicon.set_name(g["name"])
        self.gtitle.setText(g["name"] if g else ("Desktop capture" if st["captureMode"] == "desktop" and st["replayActive"] else "Waiting for game…"))
        self.b_rec.setVisible(st["recording"])
        if st["replayActive"]:
            self.b_replay.set("● Replay buffer on", "live")
        else:
            self.b_replay.set("Replay buffer off" if ready else "Starting recorder…" if st["obsState"] in ("starting", "preparing", "idle") else "Recorder offline")
        p = st.get("plan")
        self.b_res.setVisible(bool(p))
        if p:
            self.b_res.set(f"{p['height']}p{p['fps']}" + (f" · {p['videoKbps'] // 1000} Mbps" if p.get("videoKbps") else ""))
        self.b_enc.setVisible(bool(st.get("vendor")))
        self.b_enc.set({"nvenc": "NVIDIA NVENC", "amf": "AMD AMF", "qsv": "Intel QSV", "x264": "x264 (CPU)"}.get(st.get("vendor"), st.get("encoder") or ""))
        self.b_mic.set("Mic muted" if st["micMuted"] else "Mic on", "rec" if st["micMuted"] else "")
        clips = s["hotkeys"]["clips"]
        if st["replayActive"] and clips:
            parts = [f"Press <b>{clips[0]['accelerator']}</b> to save the last {fmt_secs_label(clips[0]['seconds'])}"]
            parts += [f"<b>{c['accelerator']}</b> {fmt_secs_label(c['seconds'])}" for c in clips[1:] if c.get("accelerator")]
            txt = " · ".join(parts)
        else:
            txt = "The replay buffer starts automatically when a supported game launches."
        if st.get("capturingWindow"):
            txt += f"<br>Capturing: {st['capturingWindow']}"
        self.hint.setText(txt)
        self.save_b.setEnabled(st["replayActive"])
        for x in (self.rec_b, self.bm_b, self.shot_b, self.replay_b):
            x.setEnabled(ready)
        self.rec_b.setText("  Stop" if st["recording"] else "  Record")
        self.rec_b.setIcon(icons.icon("stop" if st["recording"] else "record", "white" if st["recording"] else C["text"], 16, fill="currentColor" if st["recording"] else "none"))
        self.rec_b.setProperty("rec", st["recording"])
        self.rec_b.style().unpolish(self.rec_b)
        self.rec_b.style().polish(self.rec_b)
        self.mic_b.setIcon(icons.icon("micOff" if st["micMuted"] else "mic", C["red"] if st["micMuted"] else C["text"], 16))
        self.replay_b.setText("Pause replay buffer" if st["replayActive"] else "Start replay buffer now")

    def refresh_lib(self):
        clips = self.backend.library.list()
        self.recent.set_clips(clips[:8])
        self.recent.setVisible(bool(clips))
        self.empty.setVisible(not clips)
        st = self.backend.library.storage_stats()
        self.s_tiles[0].set(str(sum(1 for c in clips if c["type"] == "clip")))
        self.s_tiles[1].set(str(sum(1 for c in clips if c["type"] == "session")))
        self.s_tiles[2].set(fmt_size(st["used"]))
        self.s_tiles[3].set(fmt_size(st["free"]) if st["free"] is not None else "—")


_ = QVBoxLayout

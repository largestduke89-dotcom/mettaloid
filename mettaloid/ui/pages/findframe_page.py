"""Find Frame: search results (live), saved finders, library scans and matching settings."""
import time

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QColorDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

from .. import icons
from ..bg import run_bg
from ..clipgrid import THUMBS
from ..findframe_dialog import ElideLabel, ImageBox, field, game_name, toggle_row
from ..theme import C
from ..widgets import Badge, Combo, Dialog, FlowLayout, FSlider, Section, Seg, Toggle, bar, btn, confirm, fmt_date, fmt_dur, fmt_time, label, scroll_page
from . import Page

TABS = [("results", "Results", "search"), ("finders", "Saved finders", "target"), ("scan", "Scan library", "scan"), ("settings", "Settings", "settings")]
METHODS = [("auto", "Auto"), ("template", "Exact look"), ("features", "Features")]
GROUP_PAGE = 40  # clip groups rendered before "Show more"


def montage_query(groups, pad):
    """Editor montage segments from matches, merging overlapping ranges per clip."""
    segs = []
    for g in groups:
        ranges = sorted([max(0.0, m["start"] - pad[0]), m["end"] + pad[1]] for m in g["matches"])
        merged = []
        for r in ranges:
            if merged and r[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], r[1])
            else:
                merged.append(list(r))
        segs += [f"{g['clipId']}:{s:.2f}:{e:.2f}" for s, e in merged]
    return ",".join(segs)


def group_matches(matches):
    out = {}
    for m in matches:
        out.setdefault(m["clipId"], []).append(m)
    return [{"clipId": k, "matches": sorted(v, key=lambda m: m["t"])} for k, v in out.items()]


def plural(n, word, suffix="s"):
    return f"{n} {word}{'' if n == 1 else suffix}"


def clear_layout(lay):
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            clear_layout(it.layout())


def empty_state(title, text="", icon="target"):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 60, 0, 60)
    lay.setSpacing(8)
    ic = QLabel()
    ic.setPixmap(icons.pixmap(icon, C["text3"], 40, stroke=1.6))
    ic.setAlignment(Qt.AlignCenter)
    lay.addWidget(ic)
    t = QLabel(title)
    t.setAlignment(Qt.AlignCenter)
    t.setStyleSheet("font-weight: 700; font-size: 14px;")
    lay.addWidget(t)
    if text:
        d = label(text, "muted", wrap=True)
        d.setTextFormat(Qt.RichText)
        d.setAlignment(Qt.AlignCenter)
        lay.addWidget(d)
    return w


# ---------------------------------------------------------------- match grid
class MatchCard(QWidget):
    """One painted match tile (thumbnail, time, score, finder, Clip it, remove)."""
    W, TH = 210, 118
    H = TH + 34

    def __init__(self, page, clip, m, removable=False):
        super().__init__()
        self.page, self.clip, self.m, self.removable = page, clip, m, removable
        self.setFixedSize(self.W, self.H)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.hover = None
        f = page.finder_info(m.get("finderId"))
        self.fname = f["name"] if f else m.get("method") or ""
        self.fcolor = (f or {}).get("color") or C["accent"]
        dur = (m.get("end") or 0) - (m.get("start") or 0)
        if dur > 0.5:
            self.fname += f" · {fmt_dur(dur)}"
        self.tip = f"{clip['title']} @ {fmt_time(m['t'])} · {round(m['score'] * 100)}%"
        self.setToolTip(self.tip)

    def thumb_paths(self):
        return self.page.thumbs.get(self.m["id"]) or self.m.get("thumb"), self.clip.get("thumb")

    def _rects(self):
        y = self.TH + 6
        x_r = QRect(self.W - 24, y, 24, 24) if self.removable else None
        right = (x_r.left() - 4) if x_r else self.W
        clip_r = QRect(right - 54, y, 54, 24)
        return clip_r, x_r

    def _area(self, pos):
        clip_r, x_r = self._rects()
        if clip_r.contains(pos):
            return "clip"
        if x_r and x_r.contains(pos):
            return "remove"
        return "card"

    def mouseMoveEvent(self, e):
        a = self._area(e.position().toPoint())
        if a != self.hover:
            self.hover = a
            self.setToolTip({"clip": "Open in the editor around this match", "remove": "Not a match"}.get(a, self.tip))
            self.update()

    def leaveEvent(self, e):
        self.hover = None
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or not self.rect().contains(e.position().toPoint()):
            return
        a = self._area(e.position().toPoint())
        if a == "clip":
            self.page.clip_it(self.clip, self.m)
        elif a == "remove":
            self.page.remove_match(self.m)
        else:
            self.page.go("clip", id=self.clip["id"], t=round(max(0.0, self.m["t"] - 1), 2))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        tr = QRectF(0.5, 0.5, self.W - 1, self.TH - 1)
        path = QPainterPath()
        path.addRoundedRect(tr, 10, 10)
        p.fillPath(path, QColor(C["bg3"]))
        p.setClipPath(path)
        mt, ct = self.thumb_paths()
        pm = THUMBS.get(mt, 420) if mt else None
        op = 1.0
        if pm is None and ct:
            pm, op = THUMBS.get(ct, 420), 0.5
        if pm and pm.width():
            s = max(tr.width() / pm.width(), tr.height() / pm.height())
            w, h = pm.width() * s, pm.height() * s
            p.setOpacity(op)
            p.drawPixmap(QRectF(tr.x() + (tr.width() - w) / 2, tr.y() + (tr.height() - h) / 2, w, h), pm, QRectF(pm.rect()))
            p.setOpacity(1)
        p.setClipping(False)
        p.setPen(QPen(QColor("#4a4f62" if self.hover == "card" else C["line"]), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(tr, 10, 10)
        f = QFont("Segoe UI", 8)
        f.setBold(True)
        p.setFont(f)
        fm = p.fontMetrics()

        def pill(text, x, y, fg, right=False):
            w = fm.horizontalAdvance(text) + 12
            rr = QRectF(x - w if right else x, y, w, 19)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 195))
            p.drawRoundedRect(rr, 6, 6)
            p.setPen(QColor(fg))
            p.drawText(rr, Qt.AlignCenter, text)
        sc = self.m.get("score") or 0
        pill(f"{round(sc * 100)}%", self.W - 7, 7, "#4ade80" if sc >= 0.9 else C["accent2"] if sc >= 0.6 else "#fb923c", right=True)
        pill(fmt_time(self.m["t"]), self.W - 7, self.TH - 26, "#ffffff", right=True)
        # bottom row
        clip_r, x_r = self._rects()
        cy = clip_r.center().y()
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self.fcolor))
        p.drawEllipse(QRectF(2, cy - 4, 8, 8))
        f2 = QFont("Segoe UI", 8)
        p.setFont(f2)
        p.setPen(QColor(C["text3"]))
        tw = clip_r.left() - 20
        p.drawText(QRect(16, clip_r.top(), tw, 24), Qt.AlignLeft | Qt.AlignVCenter, p.fontMetrics().elidedText(self.fname, Qt.ElideRight, tw))
        for r, key in ((clip_r, "clip"), (x_r, "remove")):
            if r is None:
                continue
            if self.hover == key:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(C["bg3"] if key == "clip" else "#3a1f24"))
                p.drawRoundedRect(QRectF(r), 6, 6)
            if key == "clip":
                fb = QFont("Segoe UI", 8)
                fb.setWeight(QFont.DemiBold)
                p.setFont(fb)
                p.setPen(QColor(C["text"]))
                p.drawText(r, Qt.AlignCenter, "Clip it")
            else:
                p.drawPixmap(r.x() + 5, r.y() + 5, icons.pixmap("x", "#fca5a5" if self.hover == key else C["text2"], 14))


class MatchGroup(QWidget):
    """Header (clip title, meta) + flow of match cards for one clip."""

    def __init__(self, page, clip, removable=False):
        super().__init__()
        self.page, self.clip, self.removable = page, clip, removable
        self.cards = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 14)
        lay.setSpacing(8)
        hd = QHBoxLayout()
        hd.setSpacing(10)
        t = QLabel(clip["title"])
        t.setStyleSheet("font-weight: 700;")
        t.setCursor(Qt.PointingHandCursor)
        t.mousePressEvent = lambda e: page.go("clip", id=clip["id"])
        hd.addWidget(t)
        self.meta = label("", "dim")
        hd.addWidget(self.meta)
        hd.addStretch(1)
        lay.addLayout(hd)
        self.fw = QWidget()
        self.flow = FlowLayout(self.fw, 12)
        lay.addWidget(self.fw)
        THUMBS.updated.connect(self._thumb)

    @staticmethod
    def key(m):
        return (m["id"], round(m["t"], 3))

    def add(self, m):
        k = self.key(m)
        if k in self.cards:
            return False
        card = MatchCard(self.page, self.clip, m, self.removable)
        idx = sum(1 for c in self.cards.values() if c.m["t"] <= m["t"])
        self.cards[k] = card
        self.flow.addWidget(card)
        it = self.flow.items.pop()
        self.flow.items.insert(idx, it)
        self.flow.invalidate()
        self._meta()
        return True

    def remove(self, m):
        card = self.cards.pop(self.key(m), None)
        if card:
            self.flow.removeWidget(card)
            card.deleteLater()
            self._meta()
        return len(self.cards)

    def _meta(self):
        self.meta.setText(f"{game_name(self.clip)} · {fmt_date(self.clip['createdAt'])} · {plural(len(self.cards), 'match', 'es')}")

    def refresh_thumb(self, match_id):
        for k, c in self.cards.items():
            if k[0] == match_id:
                c.update()

    def _thumb(self, path):
        for c in self.cards.values():
            if path in c.thumb_paths():
                c.update()


class MatchList(QWidget):
    """Groups of matches (by clip) with incremental adds and paging of large result sets."""

    def __init__(self, page, removable=False):
        super().__init__()
        self.page, self.removable = page, removable
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(6)
        self.empty = empty_state("No matches yet", icon="search")
        self.lay.addWidget(self.empty)
        self.more = btn("Show more", None, self._more, sm=True)
        self.groups = {}
        self.pending = []  # groups beyond the current page: (clip, [matches])
        self.lay.addWidget(self.more, 0, Qt.AlignHCenter)
        self.more.hide()

    def reset(self):
        for g in self.groups.values():
            g.setParent(None)
            g.deleteLater()
        self.groups.clear()
        self.pending = []
        self._update()

    def add_matches(self, matches):
        lib = self.page.backend.library
        changed = False
        for m in matches:
            g = self.groups.get(m["clipId"])
            if g is None:
                pend = next((x for x in self.pending if x[0]["id"] == m["clipId"]), None)
                if pend:
                    pend[1].append(m)
                    continue
                clip = lib.get(m["clipId"])
                if not clip:
                    continue
                if len(self.groups) >= GROUP_PAGE:
                    self.pending.append((clip, [m]))
                    changed = True
                    continue
                g = self._new_group(clip)
            changed = g.add(m) or changed
        if changed:
            self._update()

    def _new_group(self, clip):
        g = MatchGroup(self.page, clip, self.removable)
        self.groups[clip["id"]] = g
        self.lay.insertWidget(self.lay.count() - 1, g)
        return g

    def _more(self):
        batch, self.pending = self.pending[:GROUP_PAGE], self.pending[GROUP_PAGE:]
        for clip, ms in batch:
            g = self._new_group(clip)
            for m in ms:
                g.add(m)
        self._update()

    def remove(self, m):
        g = self.groups.get(m["clipId"])
        if g and g.remove(m) == 0:
            self.groups.pop(m["clipId"])
            g.setParent(None)
            g.deleteLater()
        self._update()

    def refresh_thumb(self, match_id):
        for g in self.groups.values():
            g.refresh_thumb(match_id)

    def _update(self):
        self.empty.setVisible(not self.groups and not self.pending)
        self.more.setVisible(bool(self.pending))
        self.more.setText(f"Show more ({plural(len(self.pending), 'clip')})")


# ---------------------------------------------------------------- results tab
class JobItem(QFrame):
    def __init__(self, tab, job):
        super().__init__()
        self.tab, self.jid = tab, job["id"]
        self.setObjectName("jobitem")
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(8)
        fs = job.get("finders") or []
        self.img = ImageBox(fs[0]["image"] if fs else None, 28, 28, decode_w=96, radius=5)
        row.addWidget(self.img)
        self.name = ElideLabel(job.get("label", ""), "font-weight: 600;")
        row.addWidget(self.name, 1)
        self.count = QLabel()
        self.count.setStyleSheet(f"background: {C['bg4']}; color: {C['text2']}; border-radius: 8px; padding: 1px 7px; font-size: 11px; font-weight: 700;")
        row.addWidget(self.count)
        lay.addLayout(row)
        self.status = label("", "dim")
        lay.addWidget(self.status)
        self.bar = bar()
        lay.addWidget(self.bar)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.tab.select(self.jid)

    def set_active(self, on):
        self.setProperty("active", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_job(self, j):
        self.count.setText(str(j.get("matchCount", 0)))
        st = j["status"]
        if st == "running":
            txt = f"{'Paused (game running)' if j.get('paused') else 'Scanning'} {j['done']}/{j['total']}"
        elif st == "done":
            txt = f"Done · {plural(j['total'], 'clip')}" + (f" ({j['skipped']} already scanned)" if j.get("skipped") else "")
        elif st == "error":
            txt = "Error"
        else:
            txt = st.capitalize()
        self.status.setText(txt)
        self.bar.setVisible(st == "running")
        self.bar.setValue(int(1000 * j["done"] / j["total"]) if j.get("total") else 0)


class ProgressCard(QFrame):
    def __init__(self, title):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.title = ElideLabel(title, "font-weight: 700; font-size: 12.5px;")
        row.addWidget(self.title, 1)
        self.time = label("", "dim")
        row.addWidget(self.time)
        self.score = label("", "dim")
        self.score.setToolTip("Live similarity of the current frame")
        row.addWidget(self.score)
        lay.addLayout(row)
        self.bar = bar()
        lay.addWidget(self.bar)

    def set(self, c):
        self.time.setText(f"{fmt_dur(c.get('t'))} / {fmt_dur(c.get('duration'))}")
        sc = c.get("scores") or {}
        best = max([0.0] + [float(v or 0) for v in sc.values()])
        self.score.setText(f"match {round(best * 100)}%")
        d = c.get("duration") or 0
        self.bar.setValue(int(1000 * min(1.0, (c.get("t") or 0) / d)) if d else 0)


class ResultsTab(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page, self.ff = page, page.backend.findframe
        self.sel = None
        self.items = {}
        self.progress = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        side = QFrame()
        side.setObjectName("ffside")
        side.setFixedWidth(280)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 14, 10, 10)
        sl.setSpacing(6)
        hd = QHBoxLayout()
        hd.setContentsMargins(6, 0, 0, 4)
        hd.addWidget(label("SEARCHES", "h3"), 1)
        self.clear_b = btn("Clear", None, self.clear_jobs, ghost=True, sm=True, tip="Remove finished searches")
        hd.addWidget(self.clear_b)
        sl.addLayout(hd)
        self.hint = label("Open a clip, pause it and press <b>Find Frame</b> (or <b>B</b>) to search. Saved finders can scan your whole library.", "dim", wrap=True)
        self.hint.setTextFormat(Qt.RichText)
        self.hint.setContentsMargins(6, 4, 6, 0)
        sl.addWidget(self.hint)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(2)
        self.list.addStretch(1)
        sa.setWidget(inner)
        sl.addWidget(sa, 1)
        lay.addWidget(side)

        main_sa, body = scroll_page(margins=(22, 18, 22, 60))
        lay.addWidget(main_sa, 1)
        self.none = empty_state("No searches yet", "Draw a box on a paused clip to find that moment everywhere.")
        body.addWidget(self.none)
        self.detail = QWidget()
        dl = QVBoxLayout(self.detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(12)
        self.imgs = QHBoxLayout()
        self.imgs.setSpacing(6)
        head.addLayout(self.imgs)
        tl = QVBoxLayout()
        tl.setSpacing(2)
        self.h_title = label("", "h2")
        self.h_title.setWordWrap(True)
        self.h_meta = label("", "dim")
        tl.addWidget(self.h_title)
        tl.addWidget(self.h_meta)
        head.addLayout(tl, 1)
        self.cancel_b = btn("Cancel", None, lambda: self.sel and self.ff.cancel(self.sel), sm=True)
        self.save_b = btn("Save as finder", "save", self.save_as_finder, sm=True, icon_size=14)
        self.montage_b = btn("Montage of matches", "merge", self.montage, sm=True, primary=True, icon_size=14)
        for b in (self.cancel_b, self.save_b, self.montage_b):
            head.addWidget(b, 0, Qt.AlignVCenter)
        dl.addLayout(head)
        self.prog = QVBoxLayout()
        self.prog.setSpacing(8)
        dl.addLayout(self.prog)
        self.errors = label("", wrap=True)
        self.errors.setStyleSheet("color: #fca5a5; font-size: 12.5px;")
        dl.addWidget(self.errors)
        self.matches = MatchList(page)
        dl.addWidget(self.matches)
        body.addWidget(self.detail)
        body.addStretch(1)
        self.setStyleSheet(f"QFrame#ffside {{ background: {C['bg2']}; border-right: 1px solid {C['line']}; }}"
                           f"QFrame#jobitem {{ border-radius: 8px; }} QFrame#jobitem:hover {{ background: {C['bg3']}; }}"
                           f"QFrame#jobitem[active=\"true\"] {{ background: {C['bg4']}; }}")
        self._shown = set()
        self.refresh_all()

    # ---------------------------------------------------------------- jobs list
    def ordered(self):
        return sorted(self.page.jobs.values(), key=lambda j: j["startedAt"], reverse=True)

    def refresh_all(self):
        """Sync the job list with page.jobs (adds/removes items) and re-render the selection."""
        jobs = self.ordered()
        ids = [j["id"] for j in jobs]
        for jid in [k for k in self.items if k not in ids]:
            it = self.items.pop(jid)
            it.setParent(None)
            it.deleteLater()
        for i, j in enumerate(jobs):
            it = self.items.get(j["id"])
            if it is None:
                it = self.items[j["id"]] = JobItem(self, j)
            if self.list.indexOf(it) != i:
                if self.list.indexOf(it) >= 0:
                    self.list.removeWidget(it)
                self.list.insertWidget(i, it)
            it.set_job(j)
        self.hint.setVisible(not jobs)
        self.clear_b.setVisible(any(j["status"] != "running" for j in jobs))
        if self.sel not in self.page.jobs:
            self.select(ids[0] if ids else None)
        else:
            self.render()

    def update_jobs(self, jids):
        if any(j not in self.items for j in jids if j in self.page.jobs):
            return self.refresh_all()
        for jid in jids:
            j = self.page.jobs.get(jid)
            if j and jid in self.items:
                self.items[jid].set_job(j)
        self.clear_b.setVisible(any(j["status"] != "running" for j in self.page.jobs.values()))
        if self.sel in jids:
            self.render()

    def select(self, jid):
        self.sel = jid
        for k, it in self.items.items():
            it.set_active(k == jid)
        clear_layout(self.imgs)
        clear_layout(self.prog)
        self.progress.clear()
        self.matches.reset()
        self._shown = set()
        j = self.page.jobs.get(jid)
        if j:
            for f in j.get("finders") or []:
                ib = ImageBox(f["image"], 96, 54, decode_w=240, border=True)
                ib.setToolTip(f["name"])
                self.imgs.addWidget(ib)
        self.render()

    def clear_jobs(self):
        self.ff.clear_jobs()
        self.page.reload_jobs()

    # ---------------------------------------------------------------- detail
    def render(self):
        j = self.page.jobs.get(self.sel)
        self.none.setVisible(j is None)
        self.detail.setVisible(j is not None)
        if not j:
            return
        running = j["status"] == "running"
        new = [m for m in j.get("matches") or [] if MatchGroup.key(m) not in self._shown]
        if new:
            self._shown.update(MatchGroup.key(m) for m in new)
            self.matches.add_matches(new)
        ngroups = len({m["clipId"] for m in j.get("matches") or []})
        meta = f"{plural(j.get('matchCount', 0), 'match', 'es')} in {plural(ngroups, 'clip')}"
        if running:
            meta += f" · {j['done']}/{j['total']} clips · {j.get('fps') or 0} frames/s"
            if j.get("paused"):
                meta += " · paused while a game is running"
        if j.get("finishedAt"):
            meta += f" · took {fmt_dur((j['finishedAt'] - j['startedAt']) / 1000)}"
        if j["status"] == "cancelled":
            meta += " · cancelled"
        if j["status"] == "error":
            meta += f" · failed: {j.get('error') or 'unknown error'}"
        self.h_title.setText(j.get("label", ""))
        self.h_meta.setText(meta)
        self.cancel_b.setVisible(running)
        self.save_b.setVisible(bool(j.get("reference")) and not running)
        self.montage_b.setVisible(bool(j.get("matches")))
        # live per-clip progress
        cur = (j.get("current") or {}) if running else {}
        for vid in [v for v in self.progress if v not in cur]:
            w = self.progress.pop(vid)
            w.setParent(None)
            w.deleteLater()
        for vid, c in cur.items():
            w = self.progress.get(vid)
            if w is None:
                clip = self.page.backend.library.get(vid)
                w = self.progress[vid] = ProgressCard(clip["title"] if clip else vid)
                self.prog.addWidget(w)
            w.set(c)
        errs = j.get("errors") or []
        self.errors.setVisible(bool(errs))
        self.errors.setText(" · ".join(str(e) for e in errs[-3:]))

    def save_as_finder(self):
        j = self.page.jobs.get(self.sel)
        if not j or not j.get("reference"):
            return
        ref = j["reference"]
        self.ff.create_finder({**ref, "name": j["label"], "settings": ref.get("settings") or {}})
        self.page.toast("Saved as finder", j["label"])

    def montage(self):
        j = self.page.jobs.get(self.sel)
        if j and j.get("matches"):
            self.page.go("editor", id="montage", seg=montage_query(group_matches(j["matches"]), self.page.pad()))


# ---------------------------------------------------------------- finders tab
class FinderCard(QFrame):
    def __init__(self, tab, f, active):
        super().__init__()
        self.tab, self.f = tab, f
        self.setObjectName("card")
        self.setProperty("accent", active)
        self.setFixedWidth(262)
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(6)
        lay.addWidget(ImageBox(f["image"], 238, 110, decode_w=480))
        row = QHBoxLayout()
        row.setSpacing(6)
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background: {f.get('color') or C['accent']}; border-radius: 5px;")
        row.addWidget(dot)
        row.addWidget(ElideLabel(f["name"], "font-weight: 700;"), 1)
        row.addWidget(Badge((f.get("settings") or {}).get("method") or "auto"))
        lay.addLayout(row)
        meta = f"{plural(f.get('matchCount', 0), 'match', 'es')} in {plural(f.get('clipCount', 0), 'clip')}" + (f" · #{f['tag']}" if f.get("tag") else "")
        lay.addWidget(label(meta, "dim"))
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(btn("Scan all", "scan", lambda: tab.scan(f["id"]), primary=True, sm=True, icon_size=13))
        row.addWidget(btn("Edit", None, lambda: tab.edit(f), sm=True))
        row.addStretch(1)
        tg = Toggle(bool(f.get("autoScan")), on_change=lambda v: tab.ff.update_finder(f["id"], {"autoScan": v}))
        tg.setToolTip("Automatically scan new clips")
        row.addWidget(tg)
        a = label("Auto", "dim")
        a.setToolTip("Automatically scan new clips")
        row.addWidget(a)
        dl = btn("", "trash", lambda: tab.delete(f), ghost=True, sm=True, tip="Delete finder", icon_size=14)
        dl.setFixedWidth(30)
        row.addWidget(dl)
        lay.addLayout(row)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.tab.view(self.f["id"])


class EditFinderDialog(Dialog):
    def __init__(self, tab, f):
        super().__init__(tab.page.win, "Edit finder", 560)
        self.tab, self.f = tab, f
        self.orig = dict(f.get("settings") or {})
        self.s = dict(self.orig)
        self.color = f.get("color") or C["accent"]
        cfg = tab.page.backend.settings.get()["findFrame"]
        row = QHBoxLayout()
        row.setSpacing(12)
        self.name = QLineEdit(f["name"])
        row.addWidget(field("Name", self.name)[0], 1)
        self.color_b = QPushButton()
        self.color_b.setFixedSize(54, 34)
        self.color_b.setCursor(Qt.PointingHandCursor)
        self.color_b.clicked.connect(self.pick_color)
        self._paint_color()
        row.addWidget(field("Color", self.color_b)[0])
        self.body.addLayout(row)
        self.tag = QLineEdit(f.get("tag") or "")
        self.tag.setPlaceholderText(f["name"])
        self.body.addWidget(field("Tag clips with", self.tag)[0])
        self.method = Seg(METHODS, self.s.get("method") or "auto", on_change=lambda v: self.s.update(method=v))
        self.body.addWidget(field("Method", self.method)[0])
        self.thr = FSlider(5, 90, 1, self.s.get("threshold", cfg["threshold"]), on_change=lambda v: self._set("threshold", v))
        w, self.thr_t, _ = field("", self.thr)
        self.body.addWidget(w)
        self.tthr = FSlider(0.5, 0.99, 0.01, self.s.get("templateThreshold", cfg["templateThreshold"]), on_change=lambda v: self._set("templateThreshold", v))
        w, self.tthr_t, _ = field("", self.tthr)
        self.body.addWidget(w)
        self.area = Seg([("anywhere", "Anywhere"), ("near", "Same spot")], self.s.get("searchArea") or "anywhere", on_change=lambda v: self.s.update(searchArea=v))
        self.body.addWidget(field("Search area", self.area)[0])
        pre = QHBoxLayout()
        pre.setSpacing(14)
        for k, text in (("boostContrast", "Boost contrast"), ("equalize", "Equalize"), ("edges", "Edges only")):
            pre.addWidget(toggle_row(text, Toggle(bool(self.s.get(k)), on_change=lambda v, k=k: self.s.update({k: v}))))
        pre.addStretch(1)
        pw = QWidget()
        pw.setLayout(pre)
        self.body.addWidget(field("Preprocessing", pw)[0])
        self.body.addWidget(label("Changing match settings marks all clips as unscanned for this finder, so the next scan rechecks them.", "dim", wrap=True))
        self._labels()
        self.add_button(btn("Delete", "trash", self.delete, danger=True, icon_size=14), left=True)
        self.add_button(btn("Cancel", None, self.reject))
        ok = self.add_button(btn("Save", None, self.save, primary=True))
        ok.setDefault(True)

    def _set(self, k, v):
        self.s[k] = v
        self._labels()

    def _labels(self):
        t = self.s.get("threshold")
        tt = self.s.get("templateThreshold")
        self.thr_t.setText(f"Feature threshold  ·  {f'{t}%' if t is not None else 'default'}")
        self.tthr_t.setText(f"Template threshold  ·  {f'{round(tt * 100)}%' if tt is not None else 'default'}")

    def _paint_color(self):
        self.color_b.setStyleSheet(f"QPushButton {{ background: {self.color}; border: 3px solid {C['bg3']}; border-radius: 8px; padding: 0; min-height: 0; }}")

    def pick_color(self):
        c = QColorDialog.getColor(QColor(self.color), self, "Finder color")
        if c.isValid():
            self.color = c.name()
            self._paint_color()

    def save(self):
        patch = {"name": self.name.text().strip() or self.f["name"], "tag": self.tag.text().strip(), "color": self.color}
        if self.s != self.orig:
            patch["settings"] = self.s
        self.tab.ff.update_finder(self.f["id"], patch)
        self.accept()

    def delete(self):
        if confirm(self, "Delete finder", f"Delete “{self.f['name']}” and its saved results?", "Delete", danger=True):
            self.tab.ff.delete_finder(self.f["id"])
            if self.tab.viewing == self.f["id"]:
                self.tab.viewing = None
            self.accept()


class FindersTab(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page, self.ff = page, page.backend.findframe
        self.viewing = None
        self._res_token = 0
        self.results = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        sa, body = scroll_page(1200)
        lay.addWidget(sa)
        self.empty = empty_state("No saved finders", "Open any clip, pause, press <b>Find Frame</b>, draw a box and choose <b>Save finder</b>.<br>"
                                 "Saved finders can scan your whole library and automatically check every new clip.")
        body.addWidget(self.empty)
        self.gw = QWidget()
        self.grid = FlowLayout(self.gw, 14)
        body.addWidget(self.gw)
        self.res = QWidget()
        rl = QVBoxLayout(self.res)
        rl.setContentsMargins(0, 14, 0, 0)
        rl.setSpacing(12)
        hd = QHBoxLayout()
        hd.setSpacing(8)
        self.res_title = label("", "h2")
        hd.addWidget(self.res_title, 1)
        self.montage_b = btn("Montage", "merge", self.montage, primary=True, sm=True, icon_size=14)
        hd.addWidget(self.montage_b)
        hd.addWidget(btn("Rescan everything", None, lambda: self.scan(self.viewing, rescan=True), sm=True))
        hd.addWidget(btn("Clear results", None, self.clear_results, danger=True, sm=True))
        rl.addLayout(hd)
        self.loading = label("Loading…", "dim")
        rl.addWidget(self.loading)
        self.matches = MatchList(page, removable=True)
        rl.addWidget(self.matches)
        body.addWidget(self.res)
        body.addStretch(1)
        self.res.hide()

    def rebuild(self):
        fs = self.page.finders
        self.empty.setVisible(not fs)
        self.gw.setVisible(bool(fs))
        if self.viewing and not any(f["id"] == self.viewing for f in fs):
            self.viewing = None
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for f in fs:
            self.grid.addWidget(FinderCard(self, f, f["id"] == self.viewing))
        self.grid.invalidate()
        self.load_results()

    def view(self, fid):
        self.viewing = None if self.viewing == fid else fid
        for i in range(self.grid.count()):
            w = self.grid.itemAt(i).widget()
            w.setProperty("accent", w.f["id"] == self.viewing)
            w.style().unpolish(w)
            w.style().polish(w)
        self.matches.reset()
        self.load_results()

    def load_results(self):
        fid = self.viewing
        self.res.setVisible(bool(fid))
        if not fid:
            return
        f = next((x for x in self.page.finders if x["id"] == fid), None)
        self.res_title.setText(f"Matches for “{f['name'] if f else ''}”")
        self._res_token += 1
        tok = self._res_token
        self.loading.setVisible(not self.matches.groups)
        run_bg(lambda: self.ff.results(fid), lambda r: self._show(tok, fid, r))

    def _show(self, tok, fid, groups):
        if tok != self._res_token or fid != self.viewing:
            return
        self.loading.hide()
        self.results = groups
        self.matches.reset()
        self.matches.add_matches([dict(m, clipId=g["clipId"]) for g in groups for m in g["matches"]])
        self.montage_b.setVisible(bool(groups))

    def scan(self, fid, rescan=False):
        if not fid:
            return
        try:
            j = self.ff.search(finder_ids=[fid], clip_ids="all", rescan=rescan)
        except Exception as e:  # noqa: BLE001
            return self.page.toast("Could not start scan", str(e), "err")
        self.page.show_job(j["id"])

    def edit(self, f):
        EditFinderDialog(self, f).exec()

    def delete(self, f):
        if confirm(self, "Delete finder", f"Delete “{f['name']}” and its saved results?", "Delete", danger=True):
            self.ff.delete_finder(f["id"])

    def clear_results(self):
        if self.viewing:
            self.ff.clear_results(self.viewing)
            self.matches.reset()
            self.page.toast("Results cleared")

    def remove_match(self, m):
        if self.viewing:
            self.matches.remove(m)
            self.results = [dict(g, matches=[x for x in g["matches"] if x["id"] != m["id"]]) for g in self.results if g["clipId"] != m["clipId"] or len(g["matches"]) > 1]
            self.ff.remove_match(self.viewing, m["clipId"], m["t"])

    def montage(self):
        if self.results:
            self.page.go("editor", id="montage", seg=montage_query(self.results, self.page.pad()))


# ---------------------------------------------------------------- scan tab
class ScanTab(QWidget):
    TYPES = [("clip", "Clips"), ("session", "Sessions"), ("export", "Edited"), ("import", "Imported")]

    def __init__(self, page):
        super().__init__()
        self.page, self.ff = page, page.backend.findframe
        self.sel = set()
        self.known = set()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        sa, body = scroll_page(900)
        lay.addWidget(sa)
        self.empty = empty_state("Save a finder first", "Draw a box on any paused clip and choose “Save finder”.", "library")
        body.addWidget(self.empty)
        self.content = QWidget()
        cl = QVBoxLayout(self.content)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.fsec_holder = QVBoxLayout()
        cl.addLayout(self.fsec_holder)
        sec = Section("Clips")
        self.game = Combo([("", "All games")], "", on_change=lambda v: self.recount(), width=220)
        sec.row("Game", None, self.game)
        tf = QFrame()
        tf.setObjectName("segbox")
        tl = QHBoxLayout(tf)
        tl.setContentsMargins(2, 2, 2, 2)
        tl.setSpacing(2)
        self.type_b = {}
        for v, text in self.TYPES:
            b = QPushButton(text)
            b.setProperty("seg", True)
            b.setProperty("accent", True)
            b.setCheckable(True)
            b.setChecked(True)
            b.setCursor(Qt.PointingHandCursor)
            b.toggled.connect(lambda _=False: self.recount())
            tl.addWidget(b)
            self.type_b[v] = b
        sec.row("Types", None, tf)
        self.since = Seg([(0, "Any time"), (1, "24 h"), (7, "7 days"), (30, "30 days")], 0, on_change=lambda v: self.recount())
        sec.row("Captured", None, self.since)
        self.fav = Toggle(False, on_change=lambda v: self.recount())
        sec.row("Favorites only", None, self.fav)
        self.rescan = Toggle(False)
        sec.row("Rescan clips already scanned", "Off = only clips a finder hasn't checked yet (much faster).", self.rescan)
        cl.addWidget(sec)
        foot = QHBoxLayout()
        foot.addStretch(1)
        self.count_lb = label("", "muted")
        foot.addWidget(self.count_lb)
        foot.addSpacing(10)
        self.start_b = btn("Scan", "scan", self.start, primary=True, lg=True)
        foot.addWidget(self.start_b)
        cl.addLayout(foot)
        body.addWidget(self.content)
        body.addStretch(1)
        self.fsec = None

    def rebuild(self):
        fs = self.page.finders
        ids = {f["id"] for f in fs}
        if not self.sel and fs and not self.known:
            self.sel = set(ids)
        self.sel |= ids - self.known  # newly created finders start selected
        self.sel &= ids
        self.known = ids
        self.empty.setVisible(not fs)
        self.content.setVisible(bool(fs))
        if self.fsec:
            self.fsec.setParent(None)
            self.fsec.deleteLater()
        self.fsec = Section("Finders to use")
        for f in fs:
            w = QWidget()
            w.setCursor(Qt.PointingHandCursor)
            hl = QHBoxLayout(w)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(14)
            tg = Toggle(f["id"] in self.sel, on_change=lambda v, fid=f["id"]: self._sel(fid, v))
            hl.addWidget(tg)
            hl.addWidget(ImageBox(f["image"], 64, 40, decode_w=160, radius=6))
            tx = QVBoxLayout()
            tx.setSpacing(1)
            n = QLabel(f["name"])
            n.setStyleSheet("font-weight: 650;")
            tx.addWidget(n)
            tx.addWidget(label(f"{f.get('matchCount', 0)} matches so far · {(f.get('settings') or {}).get('method') or 'auto'}", "dim"))
            hl.addLayout(tx, 1)
            w.mousePressEvent = lambda e, t=tg: t.toggle()
            self.fsec.widget(w, (16, 10, 16, 10))
        self.fsec_holder.addWidget(self.fsec)
        self.refresh_games()

    def _sel(self, fid, on):
        (self.sel.add if on else self.sel.discard)(fid)
        self.recount()

    def refresh_games(self):
        games = sorted({game_name(c) for c in self.page.backend.library.list()})
        cur = self.game.value() or ""
        self.game.set_options([("", "All games")] + [(g, g) for g in games], cur if cur in games else "")
        self.recount()

    def filters(self):
        days = self.since.value or 0
        return {"game": self.game.value() or None, "types": [v for v, b in self.type_b.items() if b.isChecked()],
                "since": (time.time() - days * 86400) * 1000 if days else None, "favorites": self.fav.isChecked()}

    def matching(self):
        fl = self.filters()
        return [c for c in self.page.backend.library.list() if c["type"] != "screenshot" and c["type"] in fl["types"]
                and (not fl["game"] or game_name(c) == fl["game"]) and (not fl["since"] or c["createdAt"] >= fl["since"])
                and (not fl["favorites"] or c.get("favorite"))]

    def recount(self):
        clips = self.matching()
        n = len(clips)
        mins = sum(c.get("duration") or 0 for c in clips) / 60
        self.count_lb.setText(f"{plural(n, 'clip')} · {round(mins)} min of video")
        self.start_b.setText(f"  Scan {plural(n, 'clip')}")
        self.start_b.setEnabled(bool(self.sel) and n > 0)

    def start(self):
        n = len(self.matching())
        try:
            j = self.ff.search(finder_ids=[f["id"] for f in self.page.finders if f["id"] in self.sel], clip_ids="all",
                               rescan=self.rescan.isChecked(), filters=self.filters())
        except Exception as e:  # noqa: BLE001
            return self.page.toast("Could not start scan", str(e), "err")
        self.page.toast("Library scan started", f"{plural(n, 'clip')} · {plural(len(self.sel), 'finder')}")
        self.page.show_job(j["id"])


# ---------------------------------------------------------------- settings tab
class SettingsTab(QWidget):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.ctl = {}
        c = page.backend.settings.get()["findFrame"]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        sa, body = scroll_page(860)
        lay.addWidget(sa)

        def num(k, mn, mx, step=1, fmt=str):
            s = FSlider(mn, mx, step, c[k], fmt=fmt, label_width=64, on_commit=lambda v, k=k: self.set(k, v))
            self.ctl[k] = s
            return s

        def tog(k):
            t = Toggle(bool(c[k]), on_change=lambda v, k=k: self.set(k, v))
            self.ctl[k] = t
            return t

        def seg(k, opts):
            s = Seg(opts, c[k], on_change=lambda v, k=k: self.set(k, v))
            self.ctl[k] = s
            return s

        pct = lambda v: f"{round(v * 100)}%"  # noqa: E731
        s = Section("Matching")
        s.row("Default method", "Exact look = template matching (best for HUD icons/text at a fixed size). Features = FindFrame's ORB keypoints "
              "(handles zoom/rotation/lighting). Auto picks per reference.", seg("method", METHODS))
        s.row("Feature match threshold", "% of the reference's keypoints that must match. Raise it if you get false positives.",
              num("threshold", 5, 90, 1, lambda v: f"{v}%"), wide=True)
        s.row("Template match threshold", "How closely pixels must match (correlation).", num("templateThreshold", 0.5, 0.99, 0.01, pct), wide=True)
        s.row("Keypoints per reference", "More = more robust, slower (FindFrame uses 1000).", num("nfeatures", 250, 4000, 50), wide=True)
        s.row("Ratio test", "Lowe ratio for accepting a keypoint match. Lower = stricter.", num("ratio", 0.5, 0.95, 0.01, lambda v: f"{v:.2f}"), wide=True)
        s.row("Verify geometry", "RANSAC check that matched keypoints form a consistent shape — removes most false positives.", tog("verifyGeometry"))
        s.row("Minimum inliers", None, num("minInliers", 4, 40), wide=True)
        s.row("Minimum good matches", None, num("minMatches", 4, 60), wide=True)
        s.row("Auto: keypoints needed for feature mode", "References with fewer keypoints use template matching.", num("autoMinKeypoints", 10, 300, 5), wide=True)
        s.row("Keypoint matcher", None, seg("matcher", [("bf", "Brute force (exact)"), ("flann", "FLANN (fast)")]))
        s.row("Default search area", None, seg("searchArea", [("anywhere", "Anywhere"), ("near", "Same spot")]))
        s.row("Same-spot margin", "How far from the original box to look (fraction of the screen).", num("nearMargin", 0.02, 0.5, 0.01, pct), wide=True)
        self.scales = QLineEdit(str(c["scales"]))
        self.scales.setFixedWidth(200)
        self.scales.editingFinished.connect(lambda: self.scales.text() != self.cfg()["scales"] and self.set("scales", self.scales.text().strip()))
        s.row("Template scales", "Sizes to try, relative to the box. Add more if the object changes size.", self.scales)
        body.addWidget(s)

        s = Section("Image preprocessing")
        s.row("Boost contrast", "Like FindFrame's option — helps with dark or washed-out footage.", tog("boostContrast"))
        self.contrast_row = s.row("Contrast amount", None, num("contrast", 1.2, 4, 0.1, lambda v: f"{v:.1f}x"), wide=True)
        s.row("Equalize (CLAHE)", "Normalizes lighting differences between clips.", tog("equalize"))
        s.row("Edges only", "Match outlines instead of colors — ignores background/brightness changes behind HUD elements.", tog("edges"))
        s.row("Blur", "Reduces noise/compression artifacts.", num("blur", 0, 5), wide=True)
        body.addWidget(s)

        s = Section("Scanning speed")
        s.row("Frames analysed per second", "5 catches anything on screen for 0.2 s or longer. 0 = every frame (slow, most precise).",
              num("scanFps", 0, 30, 1, lambda v: f"{v} fps" if v else "all"), wide=True)
        self.ctl["scanWidth"] = Combo([(640, "640 px (fastest)"), (960, "960 px"), (1280, "1280 px"), (1920, "1920 px"), (0, "Original")], c["scanWidth"],
                                      on_change=lambda v: self.set("scanWidth", v), width=180)
        s.row("Analysis resolution", "Frames are downscaled to this width first. Lower is faster.", self.ctl["scanWidth"])
        s.row("GPU video decoding", "Auto uses it for videos longer than 5 minutes (it costs ~1 s to start).", seg("hwDecode", [("auto", "Auto"), (True, "On"), (False, "Off")]))
        s.row("Parallel scans", "Clips scanned at the same time.", num("workers", 1, 8), wide=True)
        s.row("CPU threads per scan", "0 = all cores.", num("threadsPerWorker", 0, 16), wide=True)
        s.row("Low priority", "Scans yield CPU to games and other apps.", tog("lowPriority"))
        s.row("Pause scans while gaming", "Library scans wait until no game is running.", tog("pauseWhileGaming"))
        s.row("Skip clips already scanned", "Library scans only check clips a finder hasn't seen with its current settings.", tog("skipScanned"))
        body.addWidget(s)

        s = Section("Results")
        s.row("Merge hits closer than", "Consecutive matching frames within this gap count as one match.", num("minGapSec", 0.2, 30, 0.1, lambda v: f"{v:.1f}s"), wide=True)
        s.row("Max matches per clip", None, num("maxResultsPerClip", 1, 500), wide=True)
        s.row("Stop at the first match in each clip", "Fast “does this clip contain X?” checks.", tog("stopAfterFirst"))
        s.row("Match thumbnails", None, tog("thumbnails"))
        s.row("Seconds before a match", "Used for “Clip it” and montages.", num("padBefore", 0, 30, 1, lambda v: f"{v}s"), wide=True)
        s.row("Seconds after a match", None, num("padAfter", 0, 30, 1, lambda v: f"{v}s"), wide=True)
        body.addWidget(s)

        s = Section("Automation")
        s.row("Scan new clips automatically", "Every new clip is checked with finders that have Auto on.", tog("autoScanNewClips"))
        s.row("Tag matching clips", "Adds the finder's tag (or name) to clips with a match.", tog("autoTag"))
        s.row("Bookmark matches", "Adds a bookmark at each match — it shows on the editor timeline.", tog("addBookmarks"))
        body.addWidget(s)
        note = label("Feature matching is based on FindFrame by Gunbard (MIT License).", "dim")
        note.setAlignment(Qt.AlignCenter)
        body.addWidget(note)
        body.addStretch(1)
        self.contrast_row.setVisible(bool(c["boostContrast"]))

    def cfg(self):
        return self.page.backend.settings.get()["findFrame"]

    def set(self, k, v):
        if self.cfg().get(k) != v or type(self.cfg().get(k)) is not type(v):
            self.page.backend.update_settings({"findFrame": {k: v}})

    def refresh(self):
        c = self.cfg()
        for k, w in self.ctl.items():
            w.set_value(c[k])
        if not self.scales.hasFocus():
            self.scales.setText(str(c["scales"]))
        self.contrast_row.setVisible(bool(c["boostContrast"]))


# ---------------------------------------------------------------- page
class FindFramePage(Page):
    def __init__(self, win):
        super().__init__(win, "Find Frame")
        b = self.backend
        self.ff = b.findframe
        self.jobs = {j["id"]: j for j in self.ff.list_jobs()}
        self.finders = self.ff.finders()
        self.thumbs = {}
        self._dirty = set()
        self._finders_dirty = False
        self._lib_dirty = False

        self.tabs = Seg([(k, text) for k, text, _ in TABS], "results", on_change=self.set_tab)
        for k, _, ic in TABS:
            bt = self.tabs.buttons[repr(k)][1]
            bt.setIcon(icons.icon(ic, C["text2"], 13, active=C["text"]))
            bt.setIconSize(QSize(13, 13))
        self.top.addSpacing(10)
        self.top.addWidget(self.tabs)
        self.top.addStretch(1)

        self.stack = QStackedWidget()
        self.root.addWidget(self.stack, 1)
        self.results = ResultsTab(self)
        self.finders_tab = FindersTab(self)
        self.scan_tab = ScanTab(self)
        self.settings_tab = SettingsTab(self)
        self.pages = {"results": self.results, "finders": self.finders_tab, "scan": self.scan_tab, "settings": self.settings_tab}
        for w in self.pages.values():
            self.stack.addWidget(w)
        self.finders_tab.rebuild()
        self.scan_tab.rebuild()
        self._labels()

        self.flush_timer = QTimer(self, singleShot=True, interval=250, timeout=self._flush)
        self.finder_timer = QTimer(self, singleShot=True, interval=300, timeout=self.load_finders)
        b.ff_job.connect(self._on_job)
        b.ff_thumb.connect(self._on_thumb)
        b.ff_changed.connect(lambda: self.finder_timer.start())
        b.library_changed.connect(self._on_lib)
        b.settings_changed.connect(lambda _s: self.settings_tab.refresh())

    # ---------------------------------------------------------------- routing / helpers
    def on_show(self, job=None, tab=None, **_):
        if self._finders_dirty:
            self.load_finders()
        if self._lib_dirty:
            self._lib_dirty = False
            self.scan_tab.refresh_games()
        if job:
            self.show_job(job)
        else:
            if tab in self.pages:
                self.set_tab(tab)
            self._dirty.update(self.jobs)
            self._flush()

    def show_job(self, jid):
        self.reload_jobs()
        self.set_tab("results")
        self.results.select(jid)

    def set_tab(self, key):
        if key not in self.pages:
            return
        self.tabs.set_value(key)
        self.stack.setCurrentWidget(self.pages[key])
        if key == "scan":
            self.scan_tab.recount()

    def _labels(self):
        running = sum(1 for j in self.jobs.values() if j["status"] == "running")
        self.tabs.buttons[repr("results")][1].setText(f"Results ({running})" if running else "Results")
        self.tabs.buttons[repr("finders")][1].setText(f"Saved finders ({len(self.finders)})" if self.finders else "Saved finders")

    def pad(self):
        c = self.backend.settings.get()["findFrame"]
        return (c["padBefore"], c["padAfter"])

    def finder_info(self, fid):
        f = next((x for x in self.finders if x["id"] == fid), None)
        if f:
            return f
        for j in self.jobs.values():
            for x in j.get("finders") or []:
                if x["id"] == fid:
                    return x
        return None

    def clip_it(self, clip, m):
        b, a = self.pad()
        self.go("editor", id=clip["id"], start=round(max(0.0, m["start"] - b), 2), end=round(min(clip.get("duration") or 1e9, m["end"] + a), 2))

    def remove_match(self, m):
        self.finders_tab.remove_match(m)

    # ---------------------------------------------------------------- data
    def reload_jobs(self):
        self.jobs = {j["id"]: j for j in self.ff.list_jobs()}
        self.results.refresh_all()
        self._labels()

    def load_finders(self):
        if not self.isVisible():
            self._finders_dirty = True
            return
        self._finders_dirty = False
        self.finders = self.ff.finders()
        self.finders_tab.rebuild()
        self.scan_tab.rebuild()
        self._labels()

    def _on_job(self, j):
        prev = self.jobs.get(j["id"])
        self.jobs[j["id"]] = j
        self._dirty.add(j["id"])
        if prev and prev["status"] == "running" and j["status"] != "running":
            self.finder_timer.start()  # match counts changed
        if not self.flush_timer.isActive():
            self.flush_timer.start()

    def _flush(self):
        if not self.isVisible() or not self._dirty:
            return
        ids, self._dirty = self._dirty, set()
        self.results.update_jobs(ids)
        self._labels()

    def _on_thumb(self, match_id, path):
        self.thumbs[match_id] = path
        if len(self.thumbs) > 5000:
            self.thumbs.pop(next(iter(self.thumbs)))
        self.results.matches.refresh_thumb(match_id)
        self.finders_tab.matches.refresh_thumb(match_id)

    def _on_lib(self):
        if self.isVisible():
            self.scan_tab.refresh_games()
        else:
            self._lib_dirty = True


_ = (QSizePolicy, QRect)

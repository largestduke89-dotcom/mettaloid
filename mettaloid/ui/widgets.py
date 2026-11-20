"""Reusable widgets + formatting helpers."""
import hashlib
import time

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, QSize, Qt, QTimer, Signal, Property
from PySide6.QtGui import QColor, QFont, QKeySequence, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QDialog, QFrame, QHBoxLayout, QLabel, QLayout, QProgressBar, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QVBoxLayout, QWidget, QWidgetItem, QComboBox)

from . import icons
from .theme import C


# ---------------------------------------------------------------- formatting
def fmt_dur(s):
    s = max(0, s or 0)
    h, m, sec = int(s // 3600), int(s % 3600 // 60), int(s % 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def fmt_time(s):
    return f"{fmt_dur(s)}.{int((s % 1) * 10)}"


def fmt_size(b):
    if b is None:
        return ""
    if b >= 1e9:
        return f"{b / 1e9:.2f} GB"
    if b >= 1e6:
        return f"{b / 1e6:.1f} MB"
    return f"{max(1, round(b / 1e3))} KB"


def fmt_mb(mb):
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{round(mb)} MB"


def fmt_secs_label(s):
    s = int(s)
    if s < 60:
        return f"{s}s"
    m, r = divmod(s, 60)
    return f"{m}m {r}s" if r else f"{m} min"


def fmt_date(ms):
    t = time.localtime(ms / 1000)
    now = time.localtime()
    hm = time.strftime("%I:%M %p", t).lstrip("0")
    if t.tm_yday == now.tm_yday and t.tm_year == now.tm_year:
        return f"Today, {hm}"
    if (time.mktime(now) - time.mktime(t)) < 2 * 86400 and t.tm_yday == time.localtime(time.time() - 86400).tm_yday:
        return f"Yesterday, {hm}"
    return time.strftime("%b %d", t) + ("" if t.tm_year == now.tm_year else time.strftime(", %Y", t)) + f", {hm}"


# ---------------------------------------------------------------- factories
def btn(text="", icon=None, on_click=None, primary=False, ghost=False, sm=False, lg=False, danger=False, rec=False, tip=None, icon_size=16, checkable=False):
    b = QPushButton(text)
    for k, v in (("primary", primary), ("ghost", ghost), ("sm", sm), ("lg", lg), ("danger", danger), ("rec", rec)):
        if v:
            b.setProperty(k, True)
    if icon:
        color = C["ink"] if primary else "#fca5a5" if danger else C["text"]
        b.setIcon(icons.icon(icon, color, icon_size))
        b.setIconSize(QSize(icon_size, icon_size))
    if tip:
        b.setToolTip(tip)
    if on_click:
        b.clicked.connect(lambda *_: on_click())
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    return b


def set_prop(w, name, value):
    w.setProperty(name, value)
    w.style().unpolish(w)
    w.style().polish(w)


def label(text="", kind=None, wrap=False, sel=False):
    lb = QLabel(text)
    if kind:
        lb.setProperty(kind, True)
    lb.setWordWrap(wrap)
    if sel:
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def hbox(*items, spacing=8, margins=(0, 0, 0, 0)):
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for it in items:
        if it == "stretch":
            lay.addStretch(1)
        elif isinstance(it, int):
            lay.addSpacing(it)
        elif isinstance(it, QLayout):
            lay.addLayout(it)
        else:
            lay.addWidget(it)
    return w


def vbox(*items, spacing=8, margins=(0, 0, 0, 0)):
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for it in items:
        if it == "stretch":
            lay.addStretch(1)
        elif isinstance(it, int):
            lay.addSpacing(it)
        else:
            lay.addWidget(it)
    return w


def card(obj="card"):
    f = QFrame()
    f.setObjectName(obj)
    return f


# ---------------------------------------------------------------- toggle
class Toggle(QAbstractButton):
    def __init__(self, checked=False, on_change=None):
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(38, 22)
        self._pos = 1.0 if checked else 0.0
        self.anim = QPropertyAnimation(self, b"pos_", self)
        self.anim.setDuration(120)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate)
        if on_change:
            self.toggled.connect(on_change)

    def _animate(self, v):
        self.anim.stop()
        self.anim.setEndValue(1.0 if v else 0.0)
        self.anim.start()

    def get_pos(self):
        return self._pos

    def set_pos_(self, v):
        self._pos = v
        self.update()

    pos_ = Property(float, get_pos, set_pos_)

    def set_value(self, v):
        self.blockSignals(True)
        self.setChecked(bool(v))
        self._pos = 1.0 if v else 0.0
        self.blockSignals(False)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = QColor(C["accent"])
        off = QColor(C["bg4"])
        t = self._pos
        col = QColor(int(off.red() + (on.red() - off.red()) * t), int(off.green() + (on.green() - off.green()) * t), int(off.blue() + (on.blue() - off.blue()) * t))
        if not self.isEnabled():
            p.setOpacity(0.4)
        p.setPen(QColor(C["line2"]) if t < 0.5 else col)
        p.setBrush(col)
        p.drawRoundedRect(QRect(0, 0, 37, 21), 10.5, 10.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(C["ink"]) if t > 0.5 else QColor("#9aa0b0"))
        p.drawEllipse(QRect(int(3 + 16 * t), 3, 15, 15))

    def sizeHint(self):
        return QSize(38, 22)


# ---------------------------------------------------------------- segmented control
class Seg(QFrame):
    changed = Signal(object)

    def __init__(self, options, value=None, accent=False, on_change=None):
        super().__init__()
        self.setObjectName("segbox")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.buttons = {}
        for o in options:
            v, text = o if isinstance(o, tuple) else (o, str(o))
            b = QPushButton(text)
            b.setProperty("seg", True)
            if accent:
                b.setProperty("accent", True)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, v=v: self.set_value(v, emit=True))
            lay.addWidget(b)
            self.buttons[self._key(v)] = (v, b)
        self.value = None
        self.set_value(value)
        if on_change:
            self.changed.connect(on_change)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    @staticmethod
    def _key(v):
        return repr(v)

    def set_value(self, v, emit=False):
        self.value = v
        for key, (val, b) in self.buttons.items():
            b.setChecked(key == self._key(v))
        if emit:
            self.changed.emit(v)


class Combo(QComboBox):
    """QComboBox with (value, label) options and a value-typed signal."""
    changed = Signal(object)

    def __init__(self, options, value=None, on_change=None, width=None):
        super().__init__()
        self.set_options(options, value)
        self.currentIndexChanged.connect(lambda i: self.changed.emit(self.itemData(i)) if i >= 0 else None)
        if on_change:
            self.changed.connect(on_change)
        if width:
            self.setFixedWidth(width)

    def set_options(self, options, value=None):
        self.blockSignals(True)
        self.clear()
        for o in options:
            v, text = o if isinstance(o, tuple) else (o, str(o))
            self.addItem(text, v)
        self.set_value(value)
        self.blockSignals(False)

    def set_value(self, v):
        self.blockSignals(True)
        for i in range(self.count()):
            if self.itemData(i) == v or str(self.itemData(i)) == str(v):
                self.setCurrentIndex(i)
                break
        self.blockSignals(False)

    def value(self):
        return self.currentData()


# ---------------------------------------------------------------- float slider
class FSlider(QWidget):
    changed = Signal(float)   # while dragging
    committed = Signal(float)  # on release / key
    pressed = Signal()

    def __init__(self, mn, mx, step=1.0, value=0.0, fmt=None, label_width=56, on_commit=None, on_change=None):
        super().__init__()
        self.mn, self.mx, self.step = mn, mx, step
        self.fmt = fmt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self.s = QSlider(Qt.Horizontal)
        self.s.setRange(0, max(1, round((mx - mn) / step)))
        self.s.setCursor(Qt.PointingHandCursor)
        lay.addWidget(self.s, 1)
        self.lb = None
        if fmt:
            self.lb = QLabel()
            self.lb.setFixedWidth(label_width)
            self.lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.lb.setStyleSheet("font-family: 'Cascadia Mono', Consolas; font-size: 12px;")
            lay.addWidget(self.lb)
        self.set_value(value)
        self.s.valueChanged.connect(self._changed)
        self.s.sliderReleased.connect(lambda: self.committed.emit(self.value()))
        self.s.sliderPressed.connect(self.pressed.emit)
        self.s.actionTriggered.connect(self._action)
        if on_commit:
            self.committed.connect(on_commit)
        if on_change:
            self.changed.connect(on_change)

    def _action(self, a):
        if not self.s.isSliderDown():
            QTimer.singleShot(0, lambda: self.committed.emit(self.value()))

    def _changed(self, _):
        v = self.value()
        if self.lb:
            self.lb.setText(self.fmt(v))
        self.changed.emit(v)

    def value(self):
        v = self.mn + self.s.value() * self.step
        return round(v, 6) if isinstance(self.step, float) and self.step < 1 else (int(round(v)) if float(self.step).is_integer() and float(self.mn).is_integer() else v)

    def set_value(self, v):
        self.s.blockSignals(True)
        self.s.setValue(round(((v if v is not None else self.mn) - self.mn) / self.step))
        self.s.blockSignals(False)
        if self.lb:
            self.lb.setText(self.fmt(self.value()))


# ---------------------------------------------------------------- settings sections
class Section(QWidget):
    def __init__(self, title=None):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 18)
        lay.setSpacing(8)
        if title:
            t = label(title.upper(), "h3")
            t.setContentsMargins(2, 0, 0, 0)
            lay.addWidget(t)
        self.box = card("setcard")
        self.inner = QVBoxLayout(self.box)
        self.inner.setContentsMargins(0, 0, 0, 0)
        self.inner.setSpacing(0)
        lay.addWidget(self.box)
        self.count = 0

    def _frame(self):
        fr = QFrame()
        fr.setObjectName("row")
        if self.count == 0:
            fr.setProperty("first", True)
        self.count += 1
        return fr

    def row(self, title, hint=None, *controls, wide=False):
        fr = self._frame()
        lay = QHBoxLayout(fr)
        lay.setContentsMargins(16, 11, 16, 11)
        lay.setSpacing(18)
        txt = QVBoxLayout()
        txt.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-weight: 600;")
        t.setWordWrap(True)
        txt.addWidget(t)
        if hint:
            h = label(hint, "dim", wrap=True)
            txt.addWidget(h)
            fr.hint = h
        lay.addLayout(txt, 1)
        ctl = QHBoxLayout()
        ctl.setContentsMargins(0, 0, 0, 0)
        ctl.setSpacing(10)
        for c in controls:
            ctl.addWidget(c)
        cw = QWidget()
        cw.setLayout(ctl)
        if wide:
            cw.setFixedWidth(340)
        lay.addWidget(cw, 0, Qt.AlignRight | Qt.AlignVCenter)
        fr.setMinimumHeight(54)
        self.inner.addWidget(fr)
        return fr

    def widget(self, w, margins=(16, 12, 16, 12)):
        fr = self._frame()
        lay = QVBoxLayout(fr)
        lay.setContentsMargins(*margins)
        lay.addWidget(w)
        self.inner.addWidget(fr)
        return fr


# ---------------------------------------------------------------- misc widgets
class Badge(QLabel):
    STYLES = {"": (C["bg4"], C["text2"]), "live": ("rgba(34,197,94,0.15)", "#4ade80"), "rec": ("rgba(239,68,68,0.16)", "#f87171"),
              "warn": ("rgba(245,183,10,0.15)", C["accent2"])}

    def __init__(self, text="", kind=""):
        super().__init__(text)
        self.set(text, kind)

    def set(self, text, kind=""):
        bg, fg = self.STYLES.get(kind, self.STYLES[""])
        self.setText(text)
        self.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: 10px; padding: 2px 9px; font-size: 11.5px; font-weight: 650;")


def bar(value=0.0):
    b = QProgressBar()
    b.setRange(0, 1000)
    b.setTextVisible(False)
    b.setValue(int(value * 1000))
    return b


def game_pixmap(name, size=42):
    initials = "".join(w[0] for w in "".join(ch for ch in (name or "?") if ch.isalnum() or ch == " ").split()[:2]).upper() or "?"
    hue = int(hashlib.md5((name or "").encode()).hexdigest()[:4], 16) % 360
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor.fromHsl(hue, 110, 66))
    g.setColorAt(1, QColor.fromHsl((hue + 40) % 360, 110, 40))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, size * 0.24, size * 0.24)
    p.setPen(QColor.fromHsl(hue, 230, 190))
    f = QFont("Segoe UI", int(size * 0.3))
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRect(0, 0, size, size), Qt.AlignCenter, initials)
    p.end()
    return pm


class GameIcon(QLabel):
    def __init__(self, name="", size=42):
        super().__init__()
        self.sz = size
        self.setFixedSize(size, size)
        self.set_name(name)

    def set_name(self, name):
        self.setPixmap(game_pixmap(name, self.sz))


class StatTile(QFrame):
    def __init__(self, title, value="—", compact=False):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10 if compact else 12, 14, 10 if compact else 12)
        lay.setSpacing(2)
        t = label(title.upper(), "h3")
        t.setStyleSheet("font-size: 11px;")
        self.v = QLabel(value)
        self.v.setStyleSheet(f"font-size: {16 if compact else 20}px; font-weight: 750;")
        lay.addWidget(t)
        lay.addWidget(self.v)

    def set(self, value, warn=False):
        self.v.setText(value)
        self.v.setStyleSheet(self.v.styleSheet().split(" color")[0] + (" color: #f87171;" if warn else ""))


class HotkeyEdit(QPushButton):
    """Click, then press a key combination. Esc cancels, Backspace clears."""
    changed = Signal(str)

    def __init__(self, value="", on_pause=None, on_resume=None):
        super().__init__()
        self.value = value or ""
        self.listening = False
        self.on_pause, self.on_resume = on_pause, on_resume
        self.setMinimumWidth(150)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self.toggle)
        self.render_text()

    def render_text(self):
        self.setText("Press keys… (Esc cancels)" if self.listening else (self.value or "Not set"))
        self.setStyleSheet(f"color: {C['accent'] if self.listening else (C['text'] if self.value else C['text3'])}; font-family: 'Cascadia Mono', Consolas; font-weight: 700;")

    def toggle(self):
        self.listening = not self.listening
        if self.listening:
            self.setFocus()
            self.grabKeyboard()
            if self.on_pause:
                self.on_pause()
        else:
            self.releaseKeyboard()
            if self.on_resume:
                self.on_resume()
        self.render_text()

    def keyPressEvent(self, e):
        if not self.listening:
            return super().keyPressEvent(e)
        k = e.key()
        if k == Qt.Key_Escape:
            return self.toggle()
        if k in (Qt.Key_Backspace, Qt.Key_Delete):
            self.value = ""
            self.toggle()
            self.changed.emit("")
            return
        if k in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            return
        mods = []
        m = e.modifiers()
        if m & Qt.ControlModifier:
            mods.append("Ctrl")
        if m & Qt.AltModifier:
            mods.append("Alt")
        if m & Qt.ShiftModifier:
            mods.append("Shift")
        if m & Qt.MetaModifier:
            mods.append("Super")
        if m & Qt.KeypadModifier and Qt.Key_0 <= k <= Qt.Key_9:
            name = f"num{k - Qt.Key_0}"
        else:
            name = QKeySequence(k).toString() or ""
            name = {"PgUp": "PageUp", "PgDown": "PageDown", "Ins": "Insert", "Del": "Delete", "Print": "PrintScreen", "Return": "Enter"}.get(name, name)
        if not name:
            return
        self.value = "+".join(mods + [name])
        self.toggle()
        self.changed.emit(self.value)

    def focusOutEvent(self, e):
        if self.listening:
            self.toggle()
        super().focusOutEvent(e)


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self.items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, i):
        return self.items[i] if 0 <= i < len(self.items) else None

    def takeAt(self, i):
        return self.items.pop(i) if 0 <= i < len(self.items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, r):
        super().setGeometry(r)
        self._do(r, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self.items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do(self, r, test):
        x, y, line = r.x(), r.y(), 0
        sp = self.spacing()
        for it in self.items:
            hint = it.sizeHint()
            nx = x + hint.width() + sp
            if nx - sp > r.right() and line > 0:
                x, y = r.x(), y + line + sp
                nx = x + hint.width() + sp
                line = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x = nx
            line = max(line, hint.height())
        return y + line - r.y()


def flow(*widgets, spacing=6):
    w = QWidget()
    fl = FlowLayout(w, spacing)
    for x in widgets:
        fl.addWidget(x)
    return w


def scroll_page(max_width=None, margins=(24, 20, 24, 40)):
    """Returns (scroll_area, content_layout)."""
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QFrame.NoFrame)
    outer = QWidget()
    ol = QHBoxLayout(outer)
    ol.setContentsMargins(*margins)
    inner = QWidget()
    if max_width:
        inner.setMaximumWidth(max_width)
    il = QVBoxLayout(inner)
    il.setContentsMargins(0, 0, 0, 0)
    il.setSpacing(12)
    ol.addStretch(1) if max_width else None
    ol.addWidget(inner, 100)
    ol.addStretch(1) if max_width else None
    sa.setWidget(outer)
    return sa, il


class Dialog(QDialog):
    def __init__(self, parent, title, width=520):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(20, 18, 20, 18)
        self.lay.setSpacing(12)
        t = label(title, "h2")
        self.lay.addWidget(t)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        self.lay.addLayout(self.body)
        self.foot = QHBoxLayout()
        self.foot.addStretch(1)
        self.lay.addLayout(self.foot)

    def add_button(self, w, left=False):
        if left:
            self.foot.insertWidget(0, w)
        else:
            self.foot.addWidget(w)
        return w


def confirm(parent, title, text, ok="OK", danger=False):
    d = Dialog(parent, title, 420)
    d.body.addWidget(label(text, "muted", wrap=True))
    d.add_button(btn("Cancel", on_click=d.reject))
    ok_b = d.add_button(btn(ok, on_click=d.accept, primary=not danger, danger=danger))
    ok_b.setDefault(True)
    return d.exec() == QDialog.Accepted


class Toaster(QWidget):
    """In-window toast stack (bottom-right)."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(8)
        self.lay.addStretch(1)
        self.raise_()

    def reposition(self):
        p = self.parentWidget()
        self.setGeometry(p.width() - 340, p.height() - 400, 320, 380)
        self.raise_()

    def show_toast(self, t):
        fr = QFrame()
        border = C["red"] if t.get("kind") == "err" else C["accent"]
        fr.setStyleSheet(f"QFrame {{ background: {C['bg3']}; border: 1px solid {C['line2']}; border-left: 3px solid {border}; border-radius: 10px; }}")
        lay = QVBoxLayout(fr)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        a = QLabel(t.get("title", ""))
        a.setStyleSheet("font-weight: 700; border: 0;")
        lay.addWidget(a)
        if t.get("subtitle"):
            b = QLabel(t["subtitle"])
            b.setWordWrap(True)
            b.setStyleSheet(f"color: {C['text2']}; border: 0; font-size: 12px;")
            lay.addWidget(b)
        self.lay.addWidget(fr)
        self.reposition()
        QTimer.singleShot(4200, fr.deleteLater)


_ = QWidgetItem

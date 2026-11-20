"""First-run setup wizard.

Asks about every setting that matters, starting from a lean preset with all
optional extras (startup launch, popups, background services, extra hotkeys,
auto-scans...) switched off. Nothing records until the wizard is finished.
"""
import copy
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from ..core.planner import auto_memory_mb
from ..core.settings import deep_merge
from .theme import C
from .widgets import Combo, FSlider, HotkeyEdit, Section, Seg, Toggle, btn, fmt_secs_label, label

REPLAY_STEPS = [10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600, 900, 1200]

# Lean: only what's needed to clip. Every optional extra is off.
MINIMAL = {
    "general": {"launchOnStartup": False, "startMinimized": False, "closeToTray": True},
    "recording": {"replayEnabled": True, "replaySeconds": 30, "recordDesktopWhenNoGame": True, "autoRecordSessions": False, "captureMode": "game"},
    "performance": {"replayMemoryMB": 0, "autoQuality": True, "maxResolution": "native", "maxFps": 60, "encoderPreset": "balanced",
                    "adaptive": True, "freeUiWhenHidden": True, "lowPriorityJobs": True},
    "audio": {"micEnabled": False, "separateDiscordTrack": False, "noiseSuppression": True, "gameAudioOnly": False},
    "hotkeys": {"clips": [{"accelerator": "F8", "seconds": 30, "label": "Save clip"}], "toggleRecording": "", "bookmark": "", "screenshot": "",
                "toggleMic": "", "toggleReplay": ""},
    "overlay": {"enabled": False, "sound": True},
    "storage": {"autoCleanup": False},
    "updates": {"autoCheck": True},
    "findFrame": {"autoScanNewClips": False, "autoTag": False, "addBookmarks": False},
}

# Everything Mettaloid can do, switched on.
FULL = {
    "general": {"launchOnStartup": True, "startMinimized": True, "closeToTray": True},
    "recording": {"replayEnabled": True, "replaySeconds": 60, "recordDesktopWhenNoGame": True, "autoRecordSessions": False, "captureMode": "game"},
    "performance": {"replayMemoryMB": 0, "autoQuality": True, "maxResolution": "native", "maxFps": 60, "encoderPreset": "balanced",
                    "adaptive": True, "freeUiWhenHidden": True, "lowPriorityJobs": True},
    "audio": {"micEnabled": True, "separateDiscordTrack": True, "noiseSuppression": True, "gameAudioOnly": False},
    "hotkeys": {"clips": [{"accelerator": "F8", "seconds": 60, "label": "Save clip"}, {"accelerator": "Shift+F8", "seconds": 15, "label": "Quick clip"}],
                "toggleRecording": "F9", "bookmark": "F10", "screenshot": "F11", "toggleMic": "Alt+M", "toggleReplay": ""},
    "overlay": {"enabled": True, "sound": True},
    "storage": {"autoCleanup": True, "maxGB": 100},
    "updates": {"autoCheck": True},
    "findFrame": {"autoScanNewClips": True, "autoTag": True, "addBookmarks": True},
}

STEPS = ["Welcome", "Recording", "Hotkeys", "Quality", "Audio", "Storage", "Extras", "Done"]


class OnboardingDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Set up Mettaloid")
        self.setMinimumSize(860, 640)
        self.preset = "minimal"
        self.s = deep_merge(settings.get(), MINIMAL)
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(210)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(18, 22, 18, 22)
        mark = QLabel("M")
        mark.setFixedSize(34, 34)
        mark.setAlignment(Qt.AlignCenter)
        mark.setStyleSheet(f"background: {C['accent']}; color: {C['ink']}; border-radius: 9px; font-weight: 900; font-size: 17px;")
        sv.addWidget(mark)
        sv.addSpacing(14)
        self.step_labels = []
        for i, name in enumerate(STEPS):
            lb = QLabel(f"{i + 1}.  {name}")
            lb.setStyleSheet(f"color: {C['text3']}; font-weight: 600; padding: 6px 0;")
            sv.addWidget(lb)
            self.step_labels.append(lb)
        sv.addStretch(1)
        sv.addWidget(label("You can change everything later in Settings.", "dim", wrap=True))
        root.addWidget(side)

        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        main.addWidget(self.stack, 1)
        foot = QFrame()
        foot.setStyleSheet(f"QFrame {{ border-top: 1px solid {C['line']}; }}")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(24, 12, 24, 12)
        self.back_b = btn("Back", None, self.back)
        self.skip_b = btn("Use these settings and finish", None, self.finish, ghost=True)
        self.next_b = btn("Next", None, self.next, primary=True)
        fl.addWidget(self.back_b)
        fl.addStretch(1)
        fl.addWidget(self.skip_b)
        fl.addWidget(self.next_b)
        main.addWidget(foot)
        mw = QWidget()
        mw.setLayout(main)
        root.addWidget(mw, 1)
        self.build()
        self.go(0)

    # ---------------------------------------------------------------- helpers
    def get(self, path):
        d = self.s
        for k in path.split("."):
            d = d[k]
        return d

    def set(self, path, value):
        d = self.s
        keys = path.split(".")
        for k in keys[:-1]:
            d = d[k]
        d[keys[-1]] = value

    def toggle(self, path, on_change=None):
        return Toggle(self.get(path), lambda v: (self.set(path, v), on_change and on_change(v)))

    def page(self, title, subtitle):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(32, 28, 32, 20)
        v.setSpacing(12)
        t = QLabel(title)
        t.setStyleSheet("font-size: 22px; font-weight: 800;")
        v.addWidget(t)
        v.addWidget(label(subtitle, "muted", wrap=True))
        v.addSpacing(6)
        sa.setWidget(w)
        self.stack.addWidget(sa)
        return v

    # ---------------------------------------------------------------- pages
    def build(self):
        while self.stack.count():
            w = self.stack.widget(0)
            self.stack.removeWidget(w)
            w.deleteLater()
        self.page_welcome()
        self.page_recording()
        self.page_hotkeys()
        self.page_quality()
        self.page_audio()
        self.page_storage()
        self.page_extras()
        self.page_done()

    def page_welcome(self):
        v = self.page("Welcome to Mettaloid", "Mettaloid keeps the last few seconds of your game in memory and saves them when you press a key. "
                      "Pick a starting point — the next steps let you review every option.")
        for key, title, desc in (
            ("minimal", "Minimal  (recommended)", "Just clipping. No startup launch, no popups, no background services, one hotkey (F8), mic off. "
                                                 "Lowest impact on your PC."),
            ("full", "Everything on", "Starts with Windows, in-game popups, mic + separate Discord track, all hotkeys, automatic Find Frame scans "
                                      "and automatic cleanup."),
            ("custom", "Custom", "Start from Minimal and decide on each option in the next steps."),
        ):
            card = QPushButton()
            card.setCheckable(True)
            card.setChecked(self.preset == key)
            card.setCursor(Qt.PointingHandCursor)
            card.setFixedHeight(104)
            card.setStyleSheet(f"""QPushButton {{ text-align: left; padding: 14px 18px; border-radius: 14px; background: {C['bg3']}; border: 1px solid {C['line2']}; }}
                QPushButton:checked {{ border: 2px solid {C['accent']}; background: rgba(245,183,10,0.07); }}""")
            lay = QVBoxLayout(card)
            lay.setContentsMargins(18, 12, 18, 12)
            a = QLabel(title)
            a.setStyleSheet("font-size: 15px; font-weight: 750; background: transparent;")
            a.setAttribute(Qt.WA_TransparentForMouseEvents)
            b = label(desc, "muted", wrap=True)
            b.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(a)
            lay.addWidget(b)
            card.clicked.connect(lambda _=False, k=key: self.choose_preset(k))
            v.addWidget(card)
        v.addStretch(1)

    def choose_preset(self, key):
        self.preset = key
        self.s = deep_merge(self.settings.get(), FULL if key == "full" else MINIMAL)
        self.build()
        self.go(0)

    def page_recording(self):
        v = self.page("Recording", "How Mettaloid records your games.")
        sec = Section()
        sec.row("Instant replay", "Keep recent gameplay in memory so you can save it after something happens. This is the core feature.",
                self.toggle("recording.replayEnabled"))
        idx = min(range(len(REPLAY_STEPS)), key=lambda i: abs(REPLAY_STEPS[i] - self.get("recording.replaySeconds")))
        sl = FSlider(0, len(REPLAY_STEPS) - 1, 1, idx, fmt=lambda i: fmt_secs_label(REPLAY_STEPS[int(i)]), label_width=70)

        def set_len(i):
            secs = REPLAY_STEPS[int(i)]
            self.set("recording.replaySeconds", secs)
            clips = self.get("hotkeys.clips")
            if clips:
                clips[0]["seconds"] = secs
        sl.changed.connect(set_len)
        sec.row("Replay length", "How far back a clip goes. Longer uses more RAM; quality is adjusted automatically to fit.", sl, wide=True)
        sec.row("What to capture", "Game capture hooks the game directly (fastest). Display records a whole monitor.",
                Seg([("game", "Game"), ("desktop", "Display")], self.get("recording.captureMode"), on_change=lambda x: self.set("recording.captureMode", x)))
        sec.row("Record whole sessions automatically", "Records every game from start to finish. Uses a lot of disk space.",
                self.toggle("recording.autoRecordSessions"))
        v.addWidget(sec)
        v.addStretch(1)

    def page_hotkeys(self):
        v = self.page("Hotkeys", "Hotkeys work while you're in a game. Each one is reserved system-wide, so only turn on the ones you'll use — "
                      "click a box and press a key, or press Backspace to leave it unset.")
        sec = Section()
        clips = self.get("hotkeys.clips")
        main = clips[0] if clips else {"accelerator": "F8", "seconds": 30, "label": "Save clip"}
        he = HotkeyEdit(main["accelerator"])
        he.changed.connect(lambda x: main.__setitem__("accelerator", x))
        sec.row("Save clip", f"Saves the last {fmt_secs_label(main['seconds'])}.", he)
        if len(clips) > 1:
            q = clips[1]
            qe = HotkeyEdit(q["accelerator"])
            qe.changed.connect(lambda x: q.__setitem__("accelerator", x))
            sec.row("Quick clip", f"Saves the last {fmt_secs_label(q['seconds'])}.", qe)
        for path, title, hint in (("hotkeys.toggleRecording", "Start / stop full recording", "Record until you press it again."),
                                  ("hotkeys.bookmark", "Bookmark", "Mark a moment while recording a session."),
                                  ("hotkeys.screenshot", "Screenshot", None),
                                  ("hotkeys.toggleMic", "Mute / unmute microphone", None)):
            e = HotkeyEdit(self.get(path))
            e.changed.connect(lambda x, path=path: self.set(path, x))
            sec.row(title, hint, e)
        v.addWidget(sec)
        v.addStretch(1)

    def page_quality(self):
        v = self.page("Quality & performance", "Mettaloid picks the best quality that fits in memory. The defaults are right for most PCs.")
        sec = Section()
        auto = Toggle(self.get("performance.replayMemoryMB") == 0)
        mem = Combo([(m, f"{m // 1024} GB" if m >= 1024 else f"{m} MB") for m in (512, 1024, 2048, 3072, 4096, 6144, 8192)],
                    self.get("performance.replayMemoryMB") or 2048, on_change=lambda x: self.set("performance.replayMemoryMB", x))
        mem.setEnabled(not auto.isChecked())

        def on_auto(x):
            self.set("performance.replayMemoryMB", 0 if x else mem.value())
            mem.setEnabled(not x)
        auto.toggled.connect(on_auto)
        sec.row("Memory for the replay buffer", f"Automatic uses about 12% of your RAM ({auto_memory_mb()} MB on this PC).",
                label("Automatic", "dim"), auto, mem)
        sec.row("Max resolution", None, Combo([("native", "Native (monitor)"), ("1440", "1440p"), ("1080", "1080p"), ("720", "720p")],
                                               self.get("performance.maxResolution"), on_change=lambda x: self.set("performance.maxResolution", x)))
        sec.row("Max frame rate", None, Seg([(30, "30"), (60, "60"), (120, "120"), (144, "144")], self.get("performance.maxFps"),
                                             on_change=lambda x: self.set("performance.maxFps", x)))
        sec.row("Encoder speed", "Faster settings cost a little quality but leave more GPU for your game.",
                Seg([("quality", "Quality"), ("balanced", "Balanced"), ("performance", "Performance")], self.get("performance.encoderPreset"),
                    on_change=lambda x: self.set("performance.encoderPreset", x)))
        sec.row("Adapt automatically if frames drop", "Switches to a faster encoder speed if recording can't keep up.", self.toggle("performance.adaptive"))
        v.addWidget(sec)
        v.addStretch(1)

    def page_audio(self):
        v = self.page("Audio", "Game sound is always recorded. Choose what else goes in your clips.")
        sec = Section()
        sec.row("Record my microphone", "Off by default — turn on if you want your voice in clips.", self.toggle("audio.micEnabled"))
        sec.row("Noise suppression for the mic", "Removes keyboard clicks and background noise.", self.toggle("audio.noiseSuppression"))
        sec.row("Separate Discord track", "Records Discord voice on its own track so you can turn friends down when editing.",
                self.toggle("audio.separateDiscordTrack"))
        sec.row("Only the game's sound", "Leaves out music, browser tabs and notifications.", self.toggle("audio.gameAudioOnly"))
        v.addWidget(sec)
        v.addStretch(1)

    def page_storage(self):
        v = self.page("Storage", "Where clips are saved.")
        sec = Section()
        path = QLineEdit(self.get("general.clipFolder"))
        path.setReadOnly(True)
        path.setMinimumWidth(320)

        def pick():
            d = QFileDialog.getExistingDirectory(self, "Choose clip folder", self.get("general.clipFolder"))
            if d:
                self.set("general.clipFolder", d)
                path.setText(d)
        sec.row("Clip folder", None, path, btn("Change…", None, pick, sm=True))
        limit = Combo([(25, "25 GB"), (50, "50 GB"), (100, "100 GB"), (250, "250 GB"), (500, "500 GB")], self.get("storage.maxGB"),
                      on_change=lambda x: self.set("storage.maxGB", x))
        sec.row("Delete old clips automatically", "When the folder passes the limit, the oldest clips go to the Recycle Bin. Favorites and clips in albums are kept.",
                self.toggle("storage.autoCleanup"), limit)
        v.addWidget(sec)
        v.addStretch(1)

    def page_extras(self):
        v = self.page("Extras", "Optional features. All of these are off in the Minimal preset.")
        sec = Section()
        sec.row("Start with Windows", "Launch in the tray when you log in, so it's ready when you play.", self.toggle("general.launchOnStartup"))
        sec.row("In-game popups", "A small notification when a clip is saved (borderless/windowed games).", self.toggle("overlay.enabled"))
        sec.row("Capture sounds", "A short sound when a clip is saved — the only feedback in fullscreen games.", self.toggle("overlay.sound"))
        sec.row("Find Frame: scan new clips automatically", "Checks every new clip against your saved finders in the background.",
                self.toggle("findFrame.autoScanNewClips"))
        sec.row("Find Frame: tag and bookmark matches", "Adds tags and bookmarks to clips where a finder matched.",
                self.toggle("findFrame.autoTag", lambda x: self.set("findFrame.addBookmarks", x)))
        sec.row("Tell me about updates", "Checks GitHub every few hours and asks before installing anything (also in the Updates tab).",
                self.toggle("updates.autoCheck"))
        v.addWidget(sec)
        v.addStretch(1)

    def page_done(self):
        v = self.page("You're all set", "Here's what Mettaloid will do:")
        s = self.s
        clips = s["hotkeys"]["clips"]
        on = lambda b: "On" if b else "Off"  # noqa: E731
        rows = [
            ("Instant replay", f"{on(s['recording']['replayEnabled'])} · last {fmt_secs_label(s['recording']['replaySeconds'])}"),
            ("Save clip", clips[0]["accelerator"] if clips and clips[0]["accelerator"] else "no hotkey"),
            ("Microphone", on(s["audio"]["micEnabled"])),
            ("Start with Windows", on(s["general"]["launchOnStartup"])),
            ("In-game popups", on(s["overlay"]["enabled"])),
            ("Find Frame auto-scan", on(s["findFrame"]["autoScanNewClips"])),
            ("Updates", "ask me when one is available" if s["updates"]["autoCheck"] else "only when I check (Updates tab)"),
            ("Clips saved to", s["general"]["clipFolder"]),
        ]
        box = QFrame()
        box.setObjectName("setcard")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(18, 14, 18, 14)
        for k, val in rows:
            r = QHBoxLayout()
            a = label(k, "dim")
            a.setFixedWidth(170)
            b = label(val, wrap=True)
            r.addWidget(a)
            r.addWidget(b, 1)
            bl.addLayout(r)
        v.addWidget(box)
        v.addWidget(label("Launch a game and press your clip hotkey after something cool happens. Mettaloid lives in the tray — "
                          "closing the window keeps it running.", "muted", wrap=True))
        v.addStretch(1)

    # ---------------------------------------------------------------- navigation
    def go(self, i):
        if i == len(STEPS) - 1:
            # rebuild the summary so it reflects the latest choices
            old = self.stack.widget(i)
            self.stack.removeWidget(old)
            old.deleteLater()
            self.page_done()
        self.i = i
        self.stack.setCurrentIndex(i)
        for k, lb in enumerate(self.step_labels):
            col = C["accent"] if k == i else (C["text2"] if k < i else C["text3"])
            lb.setStyleSheet(f"color: {col}; font-weight: {750 if k == i else 600}; padding: 6px 0;")
        self.back_b.setVisible(i > 0)
        self.skip_b.setVisible(0 < i < len(STEPS) - 1)
        self.next_b.setText("Start Mettaloid" if i == len(STEPS) - 1 else ("Review options" if i == 0 else "Next"))

    def next(self):
        if self.i == len(STEPS) - 1:
            return self.finish()
        self.go(self.i + 1)

    def back(self):
        self.go(max(0, self.i - 1))

    def finish(self):
        self.result_settings = copy.deepcopy(self.s)
        self.result_settings["onboarded"] = True
        self.accept()

    def reject(self):
        # closing the window still applies the chosen (lean) preset so nothing unexpected runs
        self.finish()


_ = os

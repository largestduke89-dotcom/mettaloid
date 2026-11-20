"""Backend: owns all services and exposes Qt signals/methods to the UI."""
import copy
import os
import subprocess
import sys
import threading
import time
import uuid
import winreg

import psutil
from PySide6.QtCore import QMimeData, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication

from . import paths, render
from .api_server import ApiServer
from .findframe import FindFrame
from .games import BUILTIN, GameDetector
from .hotkeys import Hotkeys
from .library import Library, sanitize, unique_path
from .obs_manager import ObsManager
from .planner import auto_memory_mb
from .settings import Settings, deep_merge
from .updater import Updater


def fmt_secs(s):
    s = int(round(s))
    return f"{s}s" if s < 60 else f"{s // 60}:{s % 60:02d}"


class Backend(QObject):
    status = Signal(dict)
    library_changed = Signal()
    clip_new = Signal(dict)
    toast = Signal(dict)  # {title, subtitle, kind, sound}
    job = Signal(dict)
    ff_job = Signal(dict)
    ff_match = Signal(str, dict)
    ff_thumb = Signal(str, str)
    ff_changed = Signal()
    updater = Signal(dict)
    hotkeys_failed = Signal(list)
    navigate = Signal(str)
    settings_changed = Signal(dict)
    _relay = Signal(str, tuple)

    def __init__(self):
        super().__init__()
        # Every public signal is emitted on the UI thread, whichever thread the
        # event came from, so lambda slots in the UI are always safe.
        self._relay.connect(self._do_emit)
        self.settings = Settings()
        self.library = Library(self.settings)
        self.obs = ObsManager(self.settings, self.display_size, self.monitor_size)
        self.detector = GameDetector(self.settings)
        self.hotkeys = Hotkeys()
        self.updater_svc = Updater(self.settings)
        self.findframe = FindFrame(self.settings, self.library, lambda: bool(self.obs.status["game"]))
        self.api = ApiServer(self.settings, self._api_clip, self._api_bookmark, lambda: dict(self.obs.status))
        self.jobs = {}
        self._pending_patch = None
        self._pending_timer = None
        self._lib_timer = None

        self.obs.on_status.append(self.poster("status"))
        self.obs.on_replay_saved.append(self._replay_saved)
        self.obs.on_recording_finished.append(self._recording_finished)
        self.obs.on_adapt.append(self._adapt)
        self.obs.on_warn.append(lambda pct: self.notify("Your GPU is overloaded", f"{pct}% of frames skipped. Cap your game's FPS or lower the max recording resolution.", "err", None))
        self.library.on_changed.append(self._lib_changed)
        self.library.on_cleanup.append(lambda n: self.notify("Storage cleanup", f"Removed {n} old clip(s) to stay under your limit", "", "mark"))
        self.detector.on_change.append(self._game_changed)
        self.updater_svc.on_status.append(self.poster("updater"))
        self.findframe.on_job.append(self.poster("ff_job"))
        self.findframe.on_match.append(self.poster("ff_match"))
        self.findframe.on_thumb.append(self.poster("ff_thumb"))
        self.findframe.on_changed.append(self.poster("ff_changed"))

    @Slot(str, tuple)
    def _do_emit(self, name, args):
        getattr(self, name).emit(*args)

    def post(self, name, *args):
        self._relay.emit(name, args)

    def poster(self, name):
        return lambda *args: self._relay.emit(name, args)

    # ---------------------------------------------------------------- startup / shutdown
    def start(self):
        self.obs.start()
        self.detector.start()
        self.register_hotkeys()
        self.api.restart()
        self.updater_svc.schedule()
        self.library.set_watch_folders(self.settings.get()["watchFolders"])
        threading.Thread(target=self.library.scan, daemon=True).start()
        self.apply_login_item()

    def shutdown(self):
        self.hotkeys.stop()
        self.detector.stop()
        self.api.close()
        for j in self.jobs.values():
            if j["status"] == "running" and j.get("_job"):
                j["_job"].cancel()
        self.findframe.cancel_all()
        self.findframe.flush()
        self.library.flush()
        try:
            self.obs.stop().result(timeout=12)
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def display_size():
        sc = QGuiApplication.primaryScreen()
        g, r = sc.geometry(), sc.devicePixelRatio()
        return (int(round(g.width() * r / 2) * 2), int(round(g.height() * r / 2) * 2))

    @staticmethod
    def monitor_size(i):
        screens = QGuiApplication.screens()
        sc = screens[i] if i < len(screens) else QGuiApplication.primaryScreen()
        g, r = sc.geometry(), sc.devicePixelRatio()
        return (int(round(g.width() * r / 2) * 2), int(round(g.height() * r / 2) * 2))

    def notify(self, title, subtitle="", kind="", sound="clip"):
        self.post("toast", {"title": title, "subtitle": subtitle, "kind": kind, "sound": sound})

    def _lib_changed(self):
        # coalesce bursts of changes
        if self._lib_timer:
            self._lib_timer.cancel()
        self._lib_timer = threading.Timer(0.12, lambda: self.post("library_changed"))
        self._lib_timer.daemon = True
        self._lib_timer.start()

    # ---------------------------------------------------------------- recorder events
    def _replay_saved(self, info):
        try:
            clip = self.library.ingest_replay(info["path"], info.get("seconds"), info.get("bufferSeconds"), info.get("game"), info.get("source"), info.get("event"), info.get("label"))
            self.post("clip_new", clip)
        except Exception as e:  # noqa: BLE001
            self.notify("Clip processing failed", str(e), "err", "error")

    def _recording_finished(self, info):
        try:
            clip = self.library.ingest_recording(info["path"], info["bookmarks"], info["game"], info["startedAt"])
            self.post("clip_new", clip)
            self.notify("Session saved", f"{clip['title']} · {len(info['bookmarks'])} bookmark(s)", "rec", None)
        except Exception as e:  # noqa: BLE001
            self.notify("Session processing failed", str(e), "err", "error")

    def _adapt(self, frm, to, pct):
        self.settings.update({"performance": {"encoderPreset": to}})
        self.queue_recorder_update({"performance": {"encoderPreset": to}})
        self.post("settings_changed", self.settings.get())
        self.notify("Recording optimized", f"Encoder was dropping {pct}% of frames — switched {frm} → {to}", "", "mark")

    def _game_changed(self, game):
        self.obs.set_game(game)
        if game:
            s = self.settings.get()
            key = s["hotkeys"]["clips"][0]["accelerator"] if s["hotkeys"]["clips"] else "F8"
            self.notify(f"{game['name']} detected", f"Press {key} to clip" if s["recording"]["replayEnabled"] else "Replay buffer is off", "", None)

    # ---------------------------------------------------------------- actions
    def clip(self, seconds=None, label=None):
        secs = seconds or self.settings.get()["recording"]["replaySeconds"]
        try:
            fut = self.obs.save_clip(seconds=secs, label=label, source="hotkey")
            game = self.obs.status["game"]
            self.notify("Clip saved", f"{game['name'] + ' · ' if game else ''}last {fmt_secs(secs)}")
            return fut
        except Exception as e:  # noqa: BLE001
            self.notify("Could not save clip", str(e), "err", "error")

    def toggle_recording(self):
        was = self.obs.status["recording"]
        self.obs.toggle_recording()
        self.notify("Recording saved" if was else "Recording started", "Processing session…" if was else "Press again to stop", "rec", "rec")

    def bookmark(self, label="Bookmark"):
        if self.obs.status["recording"]:
            bm = self.obs.add_bookmark(label)
            self.notify("Bookmark added", f"at {fmt_secs(bm['time'])}", "mark", "mark")
            return bm
        return self.clip(30, label)

    def screenshot(self):
        game = self.obs.status["game"]
        d = os.path.join(self.library.game_dir(game), "Screenshots")
        os.makedirs(d, exist_ok=True)
        path = unique_path(os.path.join(d, f"{sanitize(game['name'] if game else 'Desktop')} {time.strftime('%Y-%m-%d %H-%M-%S')}.png"))

        def done(fut):
            try:
                fut.result()
                time.sleep(0.4)
                self.library.add(path, type="screenshot", game={"id": game["id"], "name": game["name"]} if game else None, title=f"{game['name'] if game else 'Desktop'} screenshot")
                self.notify("Screenshot saved", os.path.basename(path), "", "shot")
            except Exception as e:  # noqa: BLE001
                self.notify("Screenshot failed", str(e), "err", "error")
        self.obs.screenshot(path).add_done_callback(lambda f: threading.Thread(target=done, args=(f,), daemon=True).start())

    def toggle_mic(self):
        def done(f):
            try:
                muted = f.result()
                self.notify("Microphone muted" if muted else "Microphone live", "", "err" if muted else "", "mark")
            except Exception:  # noqa: BLE001
                pass
        self.obs.toggle_mic().add_done_callback(done)

    def toggle_replay(self):
        def done(_):
            self.notify("Replay buffer on" if self.obs.status["replayActive"] else "Replay buffer paused", "", "", "mark")
        self.obs.toggle_replay().add_done_callback(done)

    def _api_clip(self, event, before, after):
        if self.obs.status["recording"]:
            bm = self.obs.add_bookmark(event)
            self.notify(event, "Bookmarked in session", "mark", "mark")
            return {"ok": True, "bookmark": bm}
        fut = self.obs.save_clip(seconds=before + after, source="auto", event=event, label=event, delay=after)
        self.notify(f"Auto clip: {event}", self.obs.status["game"]["name"] if self.obs.status["game"] else "")
        return {"ok": True, "raw": fut.result(timeout=after + 30)}

    def _api_bookmark(self, label):
        return self.bookmark(label) or {"ok": True}

    # ---------------------------------------------------------------- hotkeys
    def register_hotkeys(self):
        h = self.settings.get()["hotkeys"]
        b = [(c["accelerator"], (lambda c=c: self.clip(int(c["seconds"]), c.get("label"))), c.get("label") or "Clip") for c in h["clips"]]
        b += [(h["toggleRecording"], self.toggle_recording, "Start/stop recording"), (h["bookmark"], self.bookmark, "Bookmark"),
              (h["screenshot"], self.screenshot, "Screenshot"), (h["toggleMic"], self.toggle_mic, "Toggle microphone"),
              (h["toggleReplay"], self.toggle_replay, "Toggle replay buffer")]
        failed = self.hotkeys.register(b)
        self.post("hotkeys_failed", failed)
        return failed

    # ---------------------------------------------------------------- settings
    def update_settings(self, patch):
        data = self.settings.update(patch)
        if "hotkeys" in patch:
            self.register_hotkeys()
        if "general" in patch and "launchOnStartup" in patch["general"]:
            self.apply_login_item()
        if "autoClip" in patch:
            self.api.restart()
        if "watchFolders" in patch:
            self.library.set_watch_folders(data["watchFolders"])
        if "storage" in patch:
            threading.Thread(target=self.library.enforce_storage, daemon=True).start()
        if "general" in patch and "clipFolder" in patch["general"]:
            os.makedirs(data["general"]["clipFolder"], exist_ok=True)
            threading.Thread(target=self.library.scan, daemon=True).start()
        self.queue_recorder_update(patch)
        self.post("settings_changed", data)
        return data

    def queue_recorder_update(self, patch):
        relevant = {"recording", "quality", "audio", "hotkeys", "autoClip", "games", "general", "advanced", "performance"}
        if not relevant & set(patch):
            return
        self._pending_patch = deep_merge(self._pending_patch or {}, patch)
        if self._pending_timer:
            self._pending_timer.cancel()

        def fire():
            p, self._pending_patch = self._pending_patch, None
            self.obs.on_settings_changed(p)
        self._pending_timer = threading.Timer(1.2, fire)
        self._pending_timer.daemon = True
        self._pending_timer.start()

    def plan(self, patch=None):
        s = self.settings.preview(patch)
        return self.obs.compute_plan(self.obs.profile_for(self.obs.status["game"], s), s)

    def apply_login_item(self):
        if not paths.FROZEN:
            return
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE) as k:
                if self.settings.get()["general"]["launchOnStartup"]:
                    winreg.SetValueEx(k, "Mettaloid", 0, winreg.REG_SZ, f'"{sys.executable}" --hidden')
                else:
                    try:
                        winreg.DeleteValue(k, "Mettaloid")
                    except FileNotFoundError:
                        pass
        except OSError:
            pass

    def games(self):
        g = self.settings.get()["games"]
        return {"builtin": BUILTIN, "custom": g["custom"], "profiles": g["profiles"], "disabled": g.get("disabled", []), "current": self.obs.status["game"]}

    def add_custom_game(self, exe_path):
        exe = os.path.basename(exe_path)
        name = os.path.splitext(exe)[0]
        gid = "custom-" + "".join(ch if ch.isalnum() else "-" for ch in name.lower())
        g = {"id": gid, "name": name, "exe": [exe], "path": exe_path}
        custom = [x for x in self.settings.get()["games"]["custom"] if x["id"] != gid] + [g]
        self.update_settings({"games": {"custom": custom}})
        return g

    # ---------------------------------------------------------------- render jobs
    def render(self, project, title="Export", parent_id=None, kind="edit", game=None):
        s = self.settings.get()
        fmt = (project.get("output") or {}).get("format") or "mp4"
        ext = {"gif": ".gif", "webm": ".webm", "mkv": ".mkv"}.get(fmt, ".mp4")
        out = unique_path(os.path.join(s["general"]["clipFolder"], "Exports", sanitize(title) + ext))
        jid = uuid.uuid4().hex
        rj = render.RenderJob()
        job = {"id": jid, "title": title, "output": out, "progress": 0.0, "phase": "Starting", "status": "running", "startedAt": time.time() * 1000, "kind": kind, "_job": rj}
        self.jobs[jid] = job

        def pub():
            self.post("job", {k: v for k, v in job.items() if not k.startswith("_")})

        last = [0.0]

        def prog(p, info):
            if p is not None:
                job["progress"] = p
            if info.get("phase"):
                job["phase"] = info["phase"]
            if info.get("speed"):
                job["speed"] = info["speed"]
            if time.time() - last[0] > 0.2:
                last[0] = time.time()
                pub()

        def run_opts():
            low = self.settings.get()["performance"]["lowPriorityJobs"]
            gaming = bool(self.obs.status["game"])
            return {"low_priority": low, "threads": max(2, (os.cpu_count() or 4) // 3) if low and gaming else 0}

        def work():
            try:
                res = render.render(copy.deepcopy(project), out, rj, prog, s["editor"]["fontPath"], run_opts)
                job.update(status="done", progress=1.0, size=res["size"])
                parent = self.library.get(parent_id) if parent_id else None
                clip = self.library.add(out, type="export", title=title, game=(parent or {}).get("game") or game, parentId=parent_id)
                job["clipId"] = clip["id"]
                pub()
                self.notify("Compression finished" if kind == "compress" else "Export finished", f"{os.path.basename(out)} · {res['size'] / 1e6:.1f} MB")
            except Exception as e:  # noqa: BLE001
                job["status"] = "cancelled" if str(e) == "Cancelled" else "error"
                job["error"] = str(e)
                try:
                    os.remove(out)
                except OSError:
                    pass
                pub()
                if job["status"] == "error":
                    self.notify("Export failed", str(e).splitlines()[0], "err", "error")
        threading.Thread(target=work, daemon=True).start()
        pub()
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def cancel_job(self, jid):
        j = self.jobs.get(jid)
        if j and j["status"] == "running":
            j["_job"].cancel()

    def list_jobs(self):
        return sorted(({k: v for k, v in j.items() if not k.startswith("_")} for j in self.jobs.values()), key=lambda j: j["startedAt"], reverse=True)

    def clear_jobs(self):
        for k in [k for k, j in self.jobs.items() if j["status"] != "running"]:
            del self.jobs[k]

    # ---------------------------------------------------------------- misc
    @staticmethod
    def hardware():
        from .ffmpeg import hw_encoders
        from .obs_manager import detect_vendor
        return {"gpus": detect_vendor()[1], "ffmpegEncoders": hw_encoders()}

    @staticmethod
    def perf_stats():
        me = psutil.Process()
        rss = me.memory_info().rss
        for c in me.children(recursive=False):
            try:
                if c.name().lower() != "obs64.exe":
                    rss += c.memory_info().rss
            except psutil.Error:
                pass
        vm = psutil.virtual_memory()
        return {"appMB": round(rss / 1048576), "appCpu": me.cpu_percent(None), "totalMB": round(vm.total / 1048576),
                "freeMB": round(vm.available / 1048576), "autoMemoryMB": auto_memory_mb(), "cores": os.cpu_count()}

    @staticmethod
    def show_in_folder(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    @staticmethod
    def open_path(path):
        os.startfile(path)  # noqa: S606

    @staticmethod
    def copy_file(path):
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(path)])
        QGuiApplication.clipboard().setMimeData(md)
        return True

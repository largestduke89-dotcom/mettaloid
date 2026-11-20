"""Drives a bundled OBS Studio (portable, hidden in tray) over obs-websocket v5.

All OBS work runs on one worker thread (serialised), so callers never block the UI.
"""
import ctypes
import json
import os
import secrets
import shutil
import socket
import subprocess
import threading
import time
from concurrent.futures import Future
from queue import Queue

import psutil

from . import paths, proc
from .obs_config import PROFILE, PROFILE_B, track_mask, write_obs_config
from .obs_ws import ObsError, ObsWs
from .planner import plan_quality

SCENE = "Mettaloid"
IN_GAME, IN_DISPLAY = "MT Game Capture", "MT Display Capture"
IN_GAME_AUDIO, IN_DISCORD = "MT Game Audio", "MT Discord"
IN_DESKTOP, IN_MIC = "MT Desktop Audio", "MT Microphone"
NOISE = "MT Noise Suppression"
MAX_BUFFER = 1200
PRESET_ORDER = ["quality", "balanced", "performance", "max-performance"]


def tracks(lst):
    return {str(i): i in lst for i in range(1, 7)}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def file_version(path):
    try:
        ver = ctypes.windll.version
        size = ver.GetFileVersionInfoSizeW(path, None)
        buf = ctypes.create_string_buffer(size)
        ver.GetFileVersionInfoW(path, 0, size, buf)
        p, n = ctypes.c_void_p(), ctypes.c_uint()
        ver.VerQueryValueW(buf, "\\", ctypes.byref(p), ctypes.byref(n))
        ms, ls = ctypes.cast(p.value + 8, ctypes.POINTER(ctypes.c_uint32))[0], ctypes.cast(p.value + 12, ctypes.POINTER(ctypes.c_uint32))[0]
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}"
    except Exception:  # noqa: BLE001
        return "31.0.0"


def detect_vendor():
    cache = os.path.join(paths.USER_DATA, "gpu.json")
    try:
        with open(cache) as f:
            gpus = json.load(f)
    except Exception:  # noqa: BLE001
        out = proc.run_quiet(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", "Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }"])
        gpus = [x.strip() for x in out.splitlines() if x.strip()]
        with open(cache, "w") as f:
            json.dump(gpus, f)
    g = " | ".join(gpus).lower()
    vendor = "nvenc" if "nvidia" in g else "amf" if ("amd" in g or "radeon" in g) else "qsv" if "intel" in g else "x264"
    return vendor, gpus


def writable(d):
    try:
        p = os.path.join(d, f".mt-write-{os.getpid()}")
        open(p, "w").close()
        os.remove(p)
        return True
    except OSError:
        return False


class ObsManager:
    def __init__(self, settings, get_display_size, get_monitor_size):
        self.settings = settings
        self.get_display_size = get_display_size
        self.get_monitor_size = get_monitor_size
        self.ws = ObsWs(on_event=self._on_event, on_close=self._on_close)
        self.p = None
        self.stopping = False
        self.crash_times = []
        self.pending_saves = []
        self.bookmarks = []
        self.session_meta = None
        self.last_game = None
        self.force_x264 = False
        self.status_lock = threading.RLock()
        self.status = {
            "obsState": "idle", "obsVersion": None, "error": None, "replayActive": False, "recording": False, "recordStartedAt": None,
            "game": None, "captureMode": "game", "micMuted": not settings.get()["audio"]["micEnabled"], "bufferSeconds": 60,
            "encoder": None, "vendor": None, "outputSize": None, "capturingWindow": None, "plan": None, "stats": None,
        }
        self.on_status = []
        self.on_replay_saved = []
        self.on_recording_finished = []
        self.on_adapt = []
        self.on_warn = []
        self.replay_state = None
        self.current_profile = None
        self.applied_sig = None
        self.pending_reconfig = False
        self.stat_samples = []
        self.last_stats = None
        self.window_retry = None
        self.q = Queue()
        threading.Thread(target=self._worker, daemon=True, name="obs-manager").start()
        threading.Thread(target=self._stats_loop, daemon=True, name="obs-stats").start()

    # ---------------------------------------------------------------- plumbing
    def _worker(self):
        while True:
            fn, args, fut = self.q.get()
            try:
                fut.set_result(fn(*args))
            except Exception as e:  # noqa: BLE001
                fut.set_exception(e)

    def submit(self, fn, *args):
        fut = Future()
        self.q.put((fn, args, fut))
        return fut

    def set_status(self, **patch):
        with self.status_lock:
            self.status.update(patch)
            snap = dict(self.status)
        for fn in self.on_status:
            fn(snap)

    def call(self, name, data=None, timeout=15):
        return self.ws.call(name, data, timeout)

    def safe(self, name, data=None):
        try:
            return self.call(name, data)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def wait_for(pred, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.1)
        return pred()

    # ---------------------------------------------------------------- planning
    def buffer_seconds(self, profile=None, s=None):
        s = s or self.settings.get()
        base = (profile or {}).get("replaySeconds") or s["recording"]["replaySeconds"]
        hk = max([int(c.get("seconds") or 0) for c in s["hotkeys"]["clips"]] + [0])
        return min(MAX_BUFFER, max(5, base, hk))

    def base_size(self, s=None):
        s = s or self.settings.get()
        if s["recording"]["captureMode"] == "desktop":
            return self.get_monitor_size(s["recording"]["monitorIndex"])
        return self.get_display_size()

    def resolve_vendor(self, s=None):
        q = (s or self.settings.get())["quality"]
        if q["encoder"] != "auto":
            return q["encoder"]
        return "x264" if self.force_x264 else getattr(self, "detected_vendor", None) or "x264"

    def profile_for(self, game, s=None):
        if not game:
            return None
        p = (s or self.settings.get())["games"]["profiles"].get(game["id"])
        return p if p and p.get("enabled") else None

    def compute_plan(self, profile=None, s=None):
        s = s or self.settings.get()
        perf = s["performance"]
        res = str(profile["resolution"]) if profile and profile.get("resolution") else None
        fps = int(profile["fps"]) if profile and profile.get("fps") else None
        return plan_quality(perf["replayMemoryMB"], self.buffer_seconds(profile, s), self.base_size(s), res or perf["maxResolution"],
                            fps or perf["maxFps"], s["quality"]["codec"], 4 if s["audio"]["separateDiscordTrack"] else 3,
                            s["audio"]["audioBitrate"], perf["autoQuality"],
                            {"resolution": res or s["quality"]["resolution"], "fps": fps or s["quality"]["fps"],
                             "bitrateKbps": s["quality"]["bitrateKbps"], "rateControl": s["quality"]["rateControl"]})

    def signature(self, plan):
        s = self.settings.get()
        return json.dumps([[plan[k] for k in ("width", "height", "fps", "videoKbps", "audioKbps", "memoryMB", "seconds", "auto")],
                           self.resolve_vendor(), s["quality"], s["recording"]["fileFormat"], track_mask(s["audio"]),
                           s["performance"]["encoderPreset"], s["general"]["clipFolder"], self.base_size()], sort_keys=True)

    def write_config(self, profile_name, plan):
        s = self.settings.get()
        self.staging = os.path.join(s["general"]["clipFolder"], ".staging")
        cfg = write_obs_config(os.path.join(self.root, "config", "obs-studio"), s, profile_name, self.staging, self.base_size(),
                               self.resolve_vendor(), self.obs_version, self.port, self.password, plan)
        self.set_status(vendor=self.resolve_vendor(), encoder=cfg["encoderId"], outputSize=cfg["outputSize"], plan=plan, bufferSeconds=plan["seconds"])

    # ---------------------------------------------------------------- locating OBS
    def candidate_roots(self):
        s = self.settings.get()
        roots = []
        if s["advanced"].get("obsPath"):
            roots.append(s["advanced"]["obsPath"])
        roots += [os.path.join(paths.VENDOR, "obs"), os.path.join(paths.USER_DATA, "obs-runtime"),
                  os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "obs-studio")]
        return roots

    def locate(self):
        return next((r for r in self.candidate_roots() if os.path.exists(os.path.join(r, "bin", "64bit", "obs64.exe"))), None)

    def prepare_runtime(self, root):
        if writable(root):
            return root
        dest = os.path.join(paths.USER_DATA, "obs-runtime")
        exe = os.path.join(root, "bin", "64bit", "obs64.exe")
        stamp = f"{exe}|{os.path.getmtime(exe)}"
        sf = os.path.join(dest, ".mt-source")
        cur = open(sf).read() if os.path.exists(sf) else ""
        if cur != stamp:
            self.set_status(obsState="preparing")
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(root, dest, ignore=lambda d, names: ["config"] if os.path.abspath(d) == os.path.abspath(root) else [])
            open(sf, "w").write(stamp)
        return dest

    def kill_orphan(self):
        pf = os.path.join(paths.USER_DATA, "obs.pid")
        try:
            pid = int(open(pf).read())
            pr = psutil.Process(pid)
            if pr.name().lower() == "obs64.exe":
                pr.kill()
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        return self.submit(self._start)

    def _start(self):
        self.stopping = False
        found = self.locate()
        if not found:
            self.set_status(obsState="missing", error="OBS Studio was not found. Run scripts/fetch_deps.py or install OBS Studio.")
            return
        try:
            self.kill_orphan()
            self.root = self.prepare_runtime(found)
            exe = os.path.join(self.root, "bin", "64bit", "obs64.exe")
            self.set_status(obsState="starting", error=None)
            self.encoder_verified = False
            self.replay_state = None
            self.obs_version = file_version(exe)
            self.detected_vendor, self.gpus = detect_vendor()
            self.port = free_port()
            self.password = secrets.token_hex(16)
            plan = self.compute_plan(self.profile_for(self.last_game))
            self.write_config(PROFILE_B, plan)
            self.write_config(PROFILE, plan)
            self.active_profile = self.current_profile = PROFILE
            self.applied_sig = self.signature(plan)
            self.set_status(obsVersion=self.obs_version)
            self.launch(exe)
            self.connect()
            self.setup_scene()
            self.apply_priority()
            self.last_stats = None
            self.set_status(obsState="ready")
            self._set_game(self.last_game)
        except Exception as e:  # noqa: BLE001
            self.set_status(obsState="error", error=str(e))

    def launch(self, exe):
        args = [exe, "--portable", "--multi", "--minimize-to-tray", "--disable-updater", "--disable-shutdown-check",
                "--disable-missing-files-check", "--profile", PROFILE, "--collection", "Mettaloid", "--scene", SCENE]
        p = proc.popen(args, cwd=os.path.dirname(exe), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.p = p
        try:
            open(os.path.join(paths.USER_DATA, "obs.pid"), "w").write(str(p.pid))
        except OSError:
            pass
        threading.Thread(target=self._watch_proc, args=(p,), daemon=True).start()
        threading.Timer(3, self.apply_priority).start()

    def _watch_proc(self, p):
        code = p.wait()
        if self.p is not p:
            return
        self.p = None
        self.set_status(replayActive=False, recording=False)
        if self.stopping:
            return
        now = time.time()
        self.crash_times = [t for t in self.crash_times if now - t < 60] + [now]
        if len(self.crash_times) <= 3:
            self.set_status(obsState="starting", error=f"OBS exited (code {code}); restarting…")
            time.sleep(1.5)
            self.start()
        else:
            self.set_status(obsState="error", error="OBS keeps crashing. Check Settings → Quality (try the x264 encoder).")

    def connect(self):
        deadline = time.time() + 45
        last = None
        while time.time() < deadline:
            if not self.p:
                raise RuntimeError("OBS exited during startup")
            try:
                self.ws.connect(f"ws://127.0.0.1:{self.port}", self.password)
                while time.time() < deadline:
                    try:
                        self.call("GetSceneList")
                        return
                    except ObsError:
                        time.sleep(0.4)
                raise RuntimeError("OBS did not become ready")
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(0.7)
        raise RuntimeError(f"Could not connect to OBS: {last}")

    def stop(self):
        return self.submit(self._stop)

    def _stop(self):
        self.stopping = True
        try:
            if self.status["recording"]:
                self.safe("StopRecord")
                self.wait_for(lambda: not self.status["recording"], 10)
        except Exception:  # noqa: BLE001
            pass
        self.ws.close()
        p = self.p
        if p:
            subprocess.run(["taskkill", "/PID", str(p.pid)], capture_output=True, creationflags=proc.CREATE_NO_WINDOW)
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        self.p = None
        self.set_status(obsState="idle", replayActive=False, recording=False)

    def restart(self):
        return self.submit(lambda: (self._stop(), self._start()))

    # ---------------------------------------------------------------- events
    def _on_close(self):
        if not self.stopping and self.status["obsState"] == "ready":
            self.set_status(obsState="starting")

    def _on_event(self, et, d):
        if et == "ReplayBufferStateChanged":
            st = d.get("outputState")
            self.replay_state = st
            if st == "OBS_WEBSOCKET_OUTPUT_STARTED":
                self.encoder_verified = True
            self.set_status(replayActive=False if st == "OBS_WEBSOCKET_OUTPUT_STOPPED" else bool(d.get("outputActive")) or st == "OBS_WEBSOCKET_OUTPUT_STOPPING")
        elif et == "ReplayBufferSaved":
            self._replay_saved(d.get("savedReplayPath"))
        elif et == "CurrentProfileChanged":
            self.current_profile = d.get("profileName")
        elif et == "RecordStateChanged":
            st = d.get("outputState")
            if st == "OBS_WEBSOCKET_OUTPUT_STARTED":
                self.bookmarks = []
                self.session_meta = {"game": self.status["game"], "startedAt": time.time() * 1000}
                self.set_status(recording=True, recordStartedAt=time.time() * 1000)
            elif st == "OBS_WEBSOCKET_OUTPUT_STOPPED":
                meta = self.session_meta or {}
                self.set_status(recording=False, recordStartedAt=None)
                if d.get("outputPath"):
                    info = {"path": d["outputPath"], "bookmarks": list(self.bookmarks), "game": meta.get("game"), "startedAt": meta.get("startedAt") or time.time() * 1000}
                    for fn in self.on_recording_finished:
                        threading.Thread(target=fn, args=(info,), daemon=True).start()
                self.bookmarks, self.session_meta = [], None
                if self.pending_reconfig:
                    self.pending_reconfig = False
                    self.set_game(self.status["game"])

    def _replay_saved(self, path):
        pend = self.pending_saves.pop(0) if self.pending_saves else None
        info = {"path": path, "game": self.status["game"], "bufferSeconds": self.status["bufferSeconds"]}
        if pend:
            info.update(pend["opts"])
            pend["fut"].set_result(path)
        for fn in self.on_replay_saved:
            threading.Thread(target=fn, args=(info,), daemon=True).start()

    # ---------------------------------------------------------------- scene / sources
    def input_names(self):
        return {i["inputName"] for i in self.call("GetInputList")["inputs"]}

    def ensure_input(self, existing, name, kind, settings, enabled=True):
        if name in existing:
            return
        self.call("CreateInput", {"sceneName": SCENE, "inputName": name, "inputKind": kind, "inputSettings": settings, "sceneItemEnabled": enabled})
        existing.add(name)

    def item_id(self, name):
        return self.call("GetSceneItemId", {"sceneName": SCENE, "sourceName": name})["sceneItemId"]

    def set_item_enabled(self, name, enabled):
        try:
            self.call("SetSceneItemEnabled", {"sceneName": SCENE, "sceneItemId": self.item_id(name), "sceneItemEnabled": enabled})
        except Exception:  # noqa: BLE001
            pass

    def fit(self, name):
        try:
            v = self.call("GetVideoSettings")
            self.call("SetSceneItemTransform", {"sceneName": SCENE, "sceneItemId": self.item_id(name), "sceneItemTransform": {
                "positionX": 0, "positionY": 0, "alignment": 5, "boundsType": "OBS_BOUNDS_SCALE_INNER", "boundsAlignment": 0,
                "boundsWidth": v["baseWidth"], "boundsHeight": v["baseHeight"]}})
        except Exception:  # noqa: BLE001
            pass

    def setup_scene(self):
        scenes = self.call("GetSceneList")["scenes"]
        if not any(s["sceneName"] == SCENE for s in scenes):
            self.call("CreateScene", {"sceneName": SCENE})
        self.call("SetCurrentProgramScene", {"sceneName": SCENE})
        ex = self.input_names()
        self.ensure_input(ex, IN_GAME, "game_capture", {"capture_mode": "any_fullscreen", "capture_cursor": False, "capture_overlays": False, "anti_cheat_hook": True, "limit_framerate": False})
        self.ensure_input(ex, IN_DISPLAY, "monitor_capture", {"capture_cursor": True}, False)
        self.ensure_input(ex, IN_GAME_AUDIO, "wasapi_process_output_capture", {"priority": 2}, False)
        self.ensure_input(ex, IN_DISCORD, "wasapi_process_output_capture", {"priority": 2}, False)
        sp = self.call("GetSpecialInputs")
        self.desktop_name = sp.get("desktop1") or IN_DESKTOP
        self.mic_name = sp.get("mic1") or IN_MIC
        if not sp.get("desktop1"):
            self.ensure_input(ex, IN_DESKTOP, "wasapi_output_capture", {"device_id": "default"})
        if not sp.get("mic1"):
            self.ensure_input(ex, IN_MIC, "wasapi_input_capture", {"device_id": "default"})
        self.fit(IN_GAME)
        self.fit(IN_DISPLAY)
        self.apply_audio()
        self.apply_monitor()

    def list_items(self, inp, prop):
        try:
            return self.call("GetInputPropertiesListPropertyItems", {"inputName": inp, "propertyName": prop}).get("propertyItems") or []
        except Exception:  # noqa: BLE001
            return []

    def devices(self):
        return self.submit(self._devices)

    def _devices(self):
        if self.status["obsState"] != "ready":
            return {"outputs": [], "inputs": [], "monitors": []}
        m = lambda arr: [{"id": i["itemValue"], "name": i["itemName"]} for i in arr]  # noqa: E731
        return {"outputs": m(self.list_items(self.desktop_name, "device_id")), "inputs": m(self.list_items(self.mic_name, "device_id")),
                "monitors": m(self.list_items(IN_DISPLAY, "monitor_id"))}

    def apply_monitor(self):
        s = self.settings.get()["recording"]
        mons = self.list_items(IN_DISPLAY, "monitor_id")
        if mons:
            m = mons[s["monitorIndex"]] if s["monitorIndex"] < len(mons) else mons[0]
            self.safe("SetInputSettings", {"inputName": IN_DISPLAY, "inputSettings": {"monitor_id": m["itemValue"], "monitor": s["monitorIndex"]}})

    def apply_audio(self, profile=None):
        a = self.settings.get()["audio"]
        mic_on = profile["micEnabled"] if profile and "micEnabled" in profile else a["micEnabled"]
        d, m = self.desktop_name, self.mic_name
        self.safe("SetInputSettings", {"inputName": d, "inputSettings": {"device_id": a["desktopDevice"] or "default"}})
        self.safe("SetInputVolume", {"inputName": d, "inputVolumeMul": a["desktopVolume"]})
        self.safe("SetInputMute", {"inputName": d, "inputMuted": a["gameAudioOnly"]})
        self.safe("SetInputAudioTracks", {"inputName": d, "inputAudioTracks": tracks([1, 2])})
        self.safe("SetInputAudioTracks", {"inputName": IN_GAME_AUDIO, "inputAudioTracks": tracks([1, 2])})
        self.safe("SetInputVolume", {"inputName": IN_GAME_AUDIO, "inputVolumeMul": a["desktopVolume"]})
        self.set_item_enabled(IN_GAME_AUDIO, a["gameAudioOnly"] and bool(self.status["game"]))
        self.safe("SetInputSettings", {"inputName": m, "inputSettings": {"device_id": a["micDevice"] or "default"}})
        self.safe("SetInputVolume", {"inputName": m, "inputVolumeMul": a["micVolume"]})
        self.safe("SetInputAudioTracks", {"inputName": m, "inputAudioTracks": tracks([1, 3])})
        self.safe("SetInputMute", {"inputName": m, "inputMuted": not mic_on})
        self.set_status(micMuted=not mic_on)
        filters = (self.safe("GetSourceFilterList", {"sourceName": m}) or {}).get("filters", [])
        if not any(f["filterName"] == NOISE for f in filters):
            self.safe("CreateSourceFilter", {"sourceName": m, "filterName": NOISE, "filterKind": "noise_suppress_filter_v2", "filterSettings": {"method": "rnnoise"}})
        self.safe("SetSourceFilterEnabled", {"sourceName": m, "filterName": NOISE, "filterEnabled": bool(a["noiseSuppression"])})
        self.safe("SetInputAudioTracks", {"inputName": IN_DISCORD, "inputAudioTracks": tracks([1, 4] if a["gameAudioOnly"] else [4])})
        if a["separateDiscordTrack"]:
            item = next((i for i in self.list_items(IN_DISCORD, "window") if i["itemValue"].lower().endswith(":discord.exe")), None)
            if item:
                self.safe("SetInputSettings", {"inputName": IN_DISCORD, "inputSettings": {"window": item["itemValue"], "priority": 2}})
            self.set_item_enabled(IN_DISCORD, bool(item))
        else:
            self.set_item_enabled(IN_DISCORD, False)

    # ---------------------------------------------------------------- capture / games
    def find_window(self, exe, inp=IN_GAME):
        e = ":" + exe.lower()
        return next((i for i in self.list_items(inp, "window") if (i.get("itemValue") or "").lower().endswith(e)), None)

    def capture_game_window(self, game):
        if self.window_retry:
            self.window_retry.set()
        self.safe("SetInputSettings", {"inputName": IN_GAME, "inputSettings": {"capture_mode": "any_fullscreen"}})

        def apply():
            w = self.find_window(game["exe"])
            if not w:
                return False
            self.safe("SetInputSettings", {"inputName": IN_GAME, "inputSettings": {"capture_mode": "window", "window": w["itemValue"], "priority": 2}})
            aw = self.find_window(game["exe"], IN_GAME_AUDIO)
            if aw:
                self.safe("SetInputSettings", {"inputName": IN_GAME_AUDIO, "inputSettings": {"window": aw["itemValue"], "priority": 2}})
            self.set_status(capturingWindow=w["itemName"])
            return True
        if apply():
            return
        stop = threading.Event()
        self.window_retry = stop

        def retry():
            for _ in range(40):
                if stop.wait(3) or not self.status["game"] or self.status["game"]["exe"] != game["exe"]:
                    return
                if self.submit(apply).result():
                    return
        threading.Thread(target=retry, daemon=True).start()

    def reconfigure(self, profile):
        plan = self.compute_plan(profile)
        sig = self.signature(plan)
        if sig == self.applied_sig:
            self.set_status(plan=plan)
            return False
        if self.status["recording"]:
            self.pending_reconfig = True
            return False
        self.stop_replay()
        nxt = PROFILE_B if self.active_profile == PROFILE else PROFILE
        self.write_config(nxt, plan)
        try:
            self.call("SetCurrentProfile", {"profileName": nxt})
            if not self.wait_for(lambda: self.current_profile == nxt, 5):
                if self.call("GetProfileList").get("currentProfileName") != nxt:
                    raise RuntimeError("profile did not switch")
            self.active_profile = nxt
            self.applied_sig = sig
            self.fit(IN_GAME)
            self.fit(IN_DISPLAY)
            return True
        except Exception as e:  # noqa: BLE001
            print("hot reconfigure failed, restarting OBS:", e)
            threading.Thread(target=lambda: self.restart(), daemon=True).start()
            return False

    def set_game(self, game):
        self.last_game = game
        return self.submit(self._set_game, game)

    def _set_game(self, game):
        self.last_game = game
        if self.status["obsState"] != "ready":
            self.set_status(game=game)
            return
        s = self.settings.get()
        prev = self.status["game"]
        self.set_status(game=game, capturingWindow=None)
        profile = self.profile_for(game)
        desktop = s["recording"]["captureMode"] == "desktop"
        if prev and (not game or prev["exe"] != game["exe"]) and getattr(self, "auto_recording", False) and self.status["recording"]:
            self.safe("StopRecord")
            self.auto_recording = False
        want = s["recording"]["replayEnabled"] and (bool(game) or desktop or s["recording"]["recordDesktopWhenNoGame"])
        use_display = desktop or not game
        try:
            self.reconfigure(profile)
        except Exception as e:  # noqa: BLE001
            print("reconfigure failed:", e)
        self.set_item_enabled(IN_GAME, not use_display)
        self.set_item_enabled(IN_DISPLAY, use_display)
        self.set_status(captureMode="desktop" if use_display else "game")
        if game and not desktop:
            self.capture_game_window(game)
        self.apply_audio(profile)
        if want and not self.status["replayActive"]:
            self.start_replay()
        if not want and self.status["replayActive"]:
            self.stop_replay()
        if game and s["recording"]["autoRecordSessions"] and not self.status["recording"] and (not prev or prev["exe"] != game["exe"]):
            if self.safe("StartRecord") is not None:
                self.auto_recording = True

    def start_replay(self):
        last = None
        for attempt in range(4):
            try:
                self.call("StartReplayBuffer")
                if not self.wait_for(lambda: self.replay_state == "OBS_WEBSOCKET_OUTPUT_STARTED" or self.status["replayActive"], 6):
                    raise RuntimeError("Replay buffer did not start")
                self.encoder_verified = True
                if self.status["error"]:
                    self.set_status(error=None)
                return
            except Exception as e:  # noqa: BLE001
                if "active" in str(e).lower() or self.status["replayActive"]:
                    return
                last = e
                time.sleep(0.8 + attempt * 0.7)
        msg = str(last) or "unknown error"
        if not self.encoder_verified and self.settings.get()["quality"]["encoder"] == "auto" and not self.force_x264 and self.status["vendor"] != "x264":
            self.force_x264 = True
            self.set_status(error=f"Hardware encoder failed ({msg}); falling back to x264.")
            threading.Thread(target=lambda: self.restart(), daemon=True).start()
            return
        self.set_status(error=f"Replay buffer failed to start: {msg}")

    def stop_replay(self):
        if not self.status["replayActive"]:
            return
        self.safe("StopReplayBuffer")
        self.wait_for(lambda: self.replay_state == "OBS_WEBSOCKET_OUTPUT_STOPPED" or not self.status["replayActive"], 8)
        time.sleep(0.3)

    def toggle_replay(self):
        return self.submit(lambda: self.stop_replay() if self.status["replayActive"] else self.start_replay())

    # ---------------------------------------------------------------- actions
    def save_clip(self, seconds=None, source="hotkey", label=None, event=None, delay=0.0):
        """Returns a Future resolving to the raw saved path."""
        if self.status["obsState"] != "ready":
            raise RuntimeError("Recorder is not ready")
        if not self.status["replayActive"]:
            raise RuntimeError("Replay buffer is not running")
        fut = Future()
        entry = {"opts": {"seconds": seconds, "source": source, "label": label, "event": event}, "fut": fut}

        def go():
            if delay:
                time.sleep(delay)
            self.pending_saves.append(entry)
            try:
                self.call("SaveReplayBuffer")
            except Exception as e:  # noqa: BLE001
                if entry in self.pending_saves:
                    self.pending_saves.remove(entry)
                fut.set_exception(e)
                return
            try:
                fut.result(timeout=20)
                return
            except Exception:  # noqa: BLE001
                pass
            if not fut.done() and entry in self.pending_saves:  # older obs-websocket without the saved event
                try:
                    self._replay_saved(self.call("GetLastReplayBufferReplay")["savedReplayPath"])
                except Exception as e:  # noqa: BLE001
                    if entry in self.pending_saves:
                        self.pending_saves.remove(entry)
                    fut.set_exception(e)
        threading.Thread(target=go, daemon=True).start()
        return fut

    def toggle_recording(self):
        def t():
            if self.status["recording"]:
                self.call("StopRecord")
            else:
                self.call("StartRecord")
                self.auto_recording = False
        return self.submit(t)

    def add_bookmark(self, label="Bookmark"):
        if not self.status["recording"] or not self.status["recordStartedAt"]:
            return None
        bm = {"time": max(0.0, (time.time() * 1000 - self.status["recordStartedAt"]) / 1000 - 2), "label": label}
        self.bookmarks.append(bm)
        self.submit(self.safe, "CreateRecordChapter", {"chapterName": label})
        return bm

    def screenshot(self, path):
        def t():
            v = self.call("GetVideoSettings")
            self.call("SaveSourceScreenshot", {"sourceName": SCENE, "imageFormat": "png", "imageFilePath": path,
                                               "imageWidth": v["outputWidth"], "imageHeight": v["outputHeight"]})
            return path
        return self.submit(t)

    def toggle_mic(self):
        def t():
            muted = not self.status["micMuted"]
            self.safe("SetInputMute", {"inputName": self.mic_name, "inputMuted": muted})
            self.set_status(micMuted=muted)
            return muted
        return self.submit(t)

    def apply_priority(self):
        if self.p:
            proc.set_priority(self.p.pid, self.settings.get()["performance"]["obsPriority"])

    def on_settings_changed(self, patch):
        def t():
            if self.status["obsState"] != "ready":
                return
            if "advanced" in patch and "obsPath" in patch["advanced"]:
                self._stop()
                self._start()
                return
            if "quality" in patch and "encoder" in patch["quality"]:
                self.force_x264 = False
            if "performance" in patch and "obsPriority" in patch["performance"]:
                self.apply_priority()
            if "audio" in patch:
                self.apply_audio(self.profile_for(self.status["game"]))
            if "recording" in patch and "monitorIndex" in patch["recording"]:
                self.apply_monitor()
            self._set_game(self.status["game"])
        return self.submit(t)

    # ---------------------------------------------------------------- stats / adaptive
    def _stats_loop(self):
        while True:
            time.sleep(3)
            if self.status["obsState"] != "ready" or not self.ws.connected:
                self.last_stats = None
                continue
            try:
                st = self.call("GetStats", timeout=5)
            except Exception:  # noqa: BLE001
                continue
            prev, self.last_stats = self.last_stats, st
            if not prev:
                continue
            d = lambda k: max(0, st[k] - prev[k])  # noqa: E731
            self.stat_samples = (self.stat_samples + [(d("outputTotalFrames"), d("outputSkippedFrames"), d("renderTotalFrames"), d("renderSkippedFrames"))])[-10:]
            out = sum(x[0] for x in self.stat_samples)
            enc = sum(x[1] for x in self.stat_samples) / out if out else 0
            ren_t = sum(x[2] for x in self.stat_samples)
            ren = sum(x[3] for x in self.stat_samples) / ren_t if ren_t else 0
            self.set_status(stats={"cpu": round(st["cpuUsage"], 1), "memoryMB": round(st["memoryUsage"]), "fps": round(st["activeFps"]),
                                   "renderMs": round(st["averageFrameRenderTime"], 2), "encoderLagPct": round(enc * 100, 1), "renderLagPct": round(ren * 100, 1)})
            perf = self.settings.get()["performance"]
            now = time.time()
            if out >= 600 and enc > 0.02 and self.status["replayActive"] and perf["adaptive"] and now - getattr(self, "last_adapt", 0) > 60:
                i = PRESET_ORDER.index(perf["encoderPreset"]) if perf["encoderPreset"] in PRESET_ORDER else -1
                if 0 <= i < len(PRESET_ORDER) - 1:
                    self.last_adapt = now
                    self.stat_samples = []
                    for fn in self.on_adapt:
                        fn(perf["encoderPreset"], PRESET_ORDER[i + 1], round(enc * 100, 1))
            if ren_t >= 600 and ren > 0.05 and now - getattr(self, "last_render_warn", 0) > 300:
                self.last_render_warn = now
                for fn in self.on_warn:
                    fn(round(ren * 100, 1))

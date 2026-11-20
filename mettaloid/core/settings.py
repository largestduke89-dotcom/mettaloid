"""Persistent settings (JSON in the user data folder), deep-merged with defaults."""
import copy
import json
import os
import threading

from . import paths

DEFAULTS = {
    "general": {"launchOnStartup": False, "startMinimized": False, "closeToTray": True, "clipFolder": ""},
    "recording": {
        "replayEnabled": True, "replaySeconds": 60, "recordDesktopWhenNoGame": False, "autoRecordSessions": False,
        "captureMode": "game", "monitorIndex": 0, "fileFormat": "mp4",
    },
    "performance": {
        "replayMemoryMB": 0, "autoQuality": True, "maxResolution": "native", "maxFps": 60,
        "encoderPreset": "balanced", "obsPriority": "AboveNormal", "adaptive": True,
        "freeUiWhenHidden": True, "lowPriorityJobs": True, "detectionInterval": 3,
        "thumbnailCacheMB": 64, "hardwareVideoDecode": True,
    },
    "quality": {"resolution": "native", "fps": 60, "bitrateKbps": 30000, "rateControl": "CBR", "cqLevel": 20, "codec": "h264", "encoder": "auto", "keyframeSec": 1},
    "audio": {
        "desktopDevice": "default", "micDevice": "default", "micEnabled": True, "micVolume": 1.0, "desktopVolume": 1.0,
        "gameAudioOnly": False, "separateDiscordTrack": True, "noiseSuppression": True, "audioBitrate": 160,
    },
    "hotkeys": {
        "clips": [{"accelerator": "F8", "seconds": 60, "label": "Save clip"}, {"accelerator": "Shift+F8", "seconds": 15, "label": "Quick clip"}],
        "toggleRecording": "F9", "bookmark": "F10", "screenshot": "F11", "toggleMic": "Alt+M", "toggleReplay": "",
    },
    "autoClip": {"enabled": True, "apiPort": 37737, "secondsBefore": 15, "secondsAfter": 5},
    "overlay": {"enabled": True, "position": "top-right", "sound": True, "soundVolume": 0.6},
    "storage": {"autoCleanup": False, "maxGB": 100, "keepFavorites": True},
    "editor": {"fontPath": "C:/Windows/Fonts/arialbd.ttf"},
    "compressor": {"defaultTargetMB": 50, "encoder": "auto", "twoPass": True, "autoDownscale": True, "audioKbps": 128},
    "updates": {"autoCheck": True, "autoDownload": True, "channel": "latest"},
    "games": {"profiles": {}, "custom": [], "disabled": []},
    "findFrame": {
        "method": "auto", "threshold": 30, "templateThreshold": 0.8, "nfeatures": 1000, "ratio": 0.9, "matcher": "bf",
        "verifyGeometry": True, "minInliers": 8, "minMatches": 10, "autoMinKeypoints": 60, "searchArea": "anywhere",
        "nearMargin": 0.15, "scales": "0.8,0.9,1,1.1,1.25", "boostContrast": False, "contrast": 2.0, "equalize": False,
        "edges": False, "blur": 0, "scanFps": 5, "scanWidth": 960, "hwDecode": "auto", "minGapSec": 2.0,
        "maxResultsPerClip": 50, "stopAfterFirst": False, "workers": 2, "threadsPerWorker": 2, "lowPriority": True,
        "pauseWhileGaming": False, "skipScanned": True, "thumbnails": True, "autoScanNewClips": True, "autoTag": True,
        "addBookmarks": True, "padBefore": 5, "padAfter": 3,
    },
    "advanced": {"obsPath": ""},
    "watchFolders": [],
}


def deep_merge(base, over):
    if not isinstance(base, dict) or not isinstance(over, dict):
        return copy.deepcopy(over if over is not None else base)
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Settings:
    def __init__(self):
        self.file = os.path.join(paths.USER_DATA, "settings.json")
        self.lock = threading.RLock()
        self.listeners = []
        saved = {}
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except Exception:  # noqa: BLE001
            pass
        self.data = deep_merge(DEFAULTS, saved)
        if not self.data["general"]["clipFolder"]:
            self.data["general"]["clipFolder"] = os.path.join(paths.videos_dir(), "Mettaloid")
        os.makedirs(self.data["general"]["clipFolder"], exist_ok=True)
        self.save()

    def get(self):
        return self.data

    def save(self):
        with self.lock:
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
            os.replace(tmp, self.file)

    def update(self, patch):
        with self.lock:
            self.data = deep_merge(self.data, patch)
            self.save()
            data = self.data
        for fn in list(self.listeners):
            try:
                fn(data, patch)
            except Exception as e:  # noqa: BLE001
                print("settings listener error:", e)
        return data

    def preview(self, patch):
        """Settings with an unsaved patch applied (for live previews)."""
        return deep_merge(self.data, patch or {})

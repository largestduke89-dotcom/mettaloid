"""Auto-updates from GitHub Releases.

Checks https://api.github.com/repos/<UPDATE_REPO>/releases, downloads the
Mettaloid-Setup-x.y.z.exe asset and runs it silently on quit (Inno Setup
relaunches Mettaloid afterwards).
"""
import json
import os
import re
import subprocess
import threading
import time
import urllib.request

from .. import UPDATE_REPO, VERSION
from . import paths, proc


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


class Updater:
    def __init__(self, settings):
        self.settings = settings
        self.state = {"status": "idle" if paths.FROZEN else "dev", "version": VERSION, "available": None, "progress": 0, "error": None, "notes": None}
        self.on_status = []
        self.installer = None
        self.asset = None

    def set(self, **kw):
        self.state.update(kw)
        for fn in self.on_status:
            fn(dict(self.state))

    def _get(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": "Mettaloid-Updater", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)

    def check(self):
        if not paths.FROZEN or "YOUR_GITHUB_USERNAME" in UPDATE_REPO:
            if paths.FROZEN:
                self.set(status="error", error="Update repository is not configured (mettaloid/__init__.py → UPDATE_REPO).")
            return self.state
        threading.Thread(target=self._check, daemon=True).start()
        return self.state

    def _check(self):
        try:
            self.set(status="checking", error=None)
            beta = self.settings.get()["updates"]["channel"] == "beta"
            rels = self._get(f"https://api.github.com/repos/{UPDATE_REPO}/releases?per_page=10")
            rels = [r for r in rels if not r.get("draft") and (beta or not r.get("prerelease"))]
            if not rels:
                return self.set(status="latest")
            rel = max(rels, key=lambda r: vtuple(r["tag_name"]))
            if vtuple(rel["tag_name"]) <= vtuple(VERSION):
                return self.set(status="latest")
            asset = next((a for a in rel["assets"] if re.match(r"Mettaloid-Setup-.*\.exe$", a["name"])), None)
            if not asset:
                return self.set(status="latest")
            self.asset = asset
            self.set(status="available", available=rel["tag_name"].lstrip("v"), notes=rel.get("body"))
            if self.settings.get()["updates"]["autoDownload"]:
                self._download()
        except Exception as e:  # noqa: BLE001
            self.set(status="error", error=str(e).splitlines()[0])

    def download(self):
        threading.Thread(target=self._download, daemon=True).start()

    def _download(self):
        a = self.asset
        if not a:
            return
        d = os.path.join(paths.USER_DATA, "updates")
        os.makedirs(d, exist_ok=True)
        out = os.path.join(d, a["name"])
        try:
            if not (os.path.exists(out) and os.path.getsize(out) == a["size"]):
                req = urllib.request.Request(a["browser_download_url"], headers={"User-Agent": "Mettaloid-Updater"})
                with urllib.request.urlopen(req, timeout=60) as r, open(out + ".part", "wb") as f:
                    got = 0
                    last = 0
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        if time.time() - last > 0.3:
                            last = time.time()
                            self.set(status="downloading", progress=got / a["size"])
                os.replace(out + ".part", out)
                if os.path.getsize(out) != a["size"]:
                    raise RuntimeError("download incomplete")
            self.installer = out
            self.set(status="ready", progress=1)
        except Exception as e:  # noqa: BLE001
            self.set(status="error", error=str(e))

    def install_on_quit(self, hidden=False):
        """Launch the downloaded installer silently (detached). Call right before exiting.
        The installer relaunches Mettaloid afterwards (in the tray when hidden=True)."""
        if self.installer and os.path.exists(self.installer):
            args = [self.installer, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"]
            if hidden:
                args.append("/mtargs=--hidden")
            subprocess.Popen(args, creationflags=0x00000008 | 0x00000200, close_fds=True)  # DETACHED_PROCESS | NEW_PROCESS_GROUP
            return True
        return False

    @staticmethod
    def cleanup():
        """Delete downloaded installers for versions we're already running."""
        d = os.path.join(paths.USER_DATA, "updates")
        if not os.path.isdir(d):
            return
        for f in os.listdir(d):
            m = re.match(r"Mettaloid-Setup-(.+)\.exe(\.part)?$", f)
            if m and vtuple(m.group(1)) <= vtuple(VERSION):
                try:
                    os.remove(os.path.join(d, f))
                except OSError:
                    pass

    def schedule(self):
        def loop():
            time.sleep(15)
            while True:
                if self.settings.get()["updates"]["autoCheck"]:
                    self._check() if paths.FROZEN and "YOUR_GITHUB_USERNAME" not in UPDATE_REPO else None
                time.sleep(4 * 3600)
        threading.Thread(target=loop, daemon=True).start()


_ = proc

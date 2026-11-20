"""Clip library: metadata DB, thumbnails, imports, watch folders, storage cleanup."""
import json
import os
import re
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import ffmpeg, paths

VIDEO_EXT = re.compile(r"\.(mp4|mkv|mov|webm|avi|flv|ts|m4v)$", re.I)
IMAGE_EXT = re.compile(r"\.(png|jpe?g|webp|bmp)$", re.I)


def sanitize(s):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(s or "Clip")).strip()[:80]
    return s or "Clip"


def stamp(t=None):
    return time.strftime("%Y-%m-%d %H-%M-%S", time.localtime(t or time.time()))


def unique_path(p):
    if not os.path.exists(p):
        return p
    base, ext = os.path.splitext(p)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def wait_unlocked(f, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with open(f, "r+b"):
                return
        except OSError:
            time.sleep(0.25)


def move(src, dst):
    try:
        os.replace(src, dst)
    except OSError:
        shutil.move(src, dst)


def recycle(path):
    """Send a file to the Recycle Bin (SHFileOperation)."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", wintypes.LPCWSTR)]
    op = SHFILEOPSTRUCTW(None, 3, os.path.abspath(path) + "\0", None, 0x0040 | 0x0010 | 0x0004 | 0x0400, False, None, None)
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0:
        os.remove(path)


class Library:
    def __init__(self, settings):
        self.settings = settings
        self.file = os.path.join(paths.USER_DATA, "library.json")
        self.thumb_dir = os.path.join(paths.USER_DATA, "thumbs")
        os.makedirs(self.thumb_dir, exist_ok=True)
        self.lock = threading.RLock()
        self.db = {"clips": [], "albums": []}
        try:
            with open(self.file, "r", encoding="utf-8") as f:
                self.db.update(json.load(f))
        except Exception:  # noqa: BLE001
            pass
        self.on_changed = []
        self.on_added = []
        self.on_cleanup = []
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lib")  # probe/thumbnail throttle
        self._save_timer = None
        self.watchers = {}

    # ---------------------------------------------------------------- persistence
    def flush(self):
        with self.lock:
            if self._save_timer:
                self._save_timer.cancel()
                self._save_timer = None
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.db, f)
            os.replace(tmp, self.file)

    def changed(self):
        with self.lock:
            if self._save_timer:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(0.4, self.flush)
            self._save_timer.daemon = True
            self._save_timer.start()
        for fn in self.on_changed:
            fn()

    @property
    def clip_folder(self):
        return self.settings.get()["general"]["clipFolder"]

    def list(self):
        with self.lock:
            return sorted(self.db["clips"], key=lambda c: c["createdAt"], reverse=True)

    def get(self, cid):
        with self.lock:
            return next((c for c in self.db["clips"] if c["id"] == cid), None)

    def albums(self):
        return self.db["albums"]

    # ---------------------------------------------------------------- add / import
    def add(self, file, **meta):
        key = os.path.normcase(os.path.abspath(file))
        with self.lock:
            existing = next((c for c in self.db["clips"] if os.path.normcase(os.path.abspath(c["path"])) == key), None)
        if existing:
            return existing
        cid = str(uuid.uuid4())
        is_img = bool(IMAGE_EXT.search(file))
        thumb = os.path.join(self.thumb_dir, cid + ".jpg")

        def work():
            info = {"duration": 0, "width": 0, "height": 0, "fps": 0, "audioTracks": [], "size": os.path.getsize(file), "videoCodec": None}
            try:
                info = ffmpeg.probe(file)
            except Exception:  # noqa: BLE001
                pass
            try:
                d = info["duration"]
                ffmpeg.thumbnail(file, thumb, 0 if is_img else min(d / 2, max(0, d - 1)))
            except Exception:  # noqa: BLE001
                pass
            return info
        info = self.pool.submit(work).result()
        clip = {
            "id": cid, "type": "screenshot" if is_img else "clip", "path": file,
            "title": os.path.splitext(os.path.basename(file))[0], "game": None, "createdAt": time.time() * 1000,
            "duration": info["duration"], "size": info["size"] or os.path.getsize(file), "width": info["width"], "height": info["height"],
            "fps": info["fps"], "videoCodec": info["videoCodec"], "audioTracks": info["audioTracks"],
            "thumb": thumb if os.path.exists(thumb) else None, "favorite": False, "tags": [], "albums": [], "bookmarks": [],
            "external": not key.startswith(os.path.normcase(os.path.abspath(self.clip_folder))),
        }
        clip.update(meta)
        with self.lock:
            self.db["clips"].append(clip)
        self.changed()
        for fn in self.on_added:
            fn(clip)
        threading.Thread(target=self.enforce_storage, daemon=True).start()
        return clip

    def import_files(self, files, copy=False):
        out = []
        for f in files:
            if not (VIDEO_EXT.search(f) or IMAGE_EXT.search(f)):
                continue
            target = f
            if copy:
                d = os.path.join(self.clip_folder, "Imported")
                os.makedirs(d, exist_ok=True)
                target = unique_path(os.path.join(d, os.path.basename(f)))
                shutil.copy2(f, target)
            out.append(self.add(target, type="screenshot" if IMAGE_EXT.search(f) else "import", createdAt=os.path.getmtime(f) * 1000))
        return out

    def game_dir(self, game):
        d = os.path.join(self.clip_folder, sanitize(game["name"] if game else "Desktop"))
        os.makedirs(d, exist_ok=True)
        return d

    def ingest_replay(self, raw, seconds=None, buffer_seconds=None, game=None, source=None, event=None, label=None):
        wait_unlocked(raw)
        ext = os.path.splitext(raw)[1]
        gname = game["name"] if game else "Desktop"
        final = unique_path(os.path.join(self.game_dir(game), f"{sanitize(gname)} {stamp()}{ext}"))
        if seconds and buffer_seconds and seconds < buffer_seconds - 0.5:
            try:
                ffmpeg.trim_tail(raw, final, seconds)
                os.remove(raw)
            except Exception:  # noqa: BLE001
                move(raw, final)
        else:
            move(raw, final)
        title = f"{label} – {gname}" if label and label not in ("Save clip", None) else f"{gname} clip"
        return self.add(final, type="clip", game={"id": game["id"], "name": gname} if game else None, source=source or "hotkey", event=event, title=title)

    def ingest_recording(self, raw, bookmarks, game, started_at):
        wait_unlocked(raw, 20)
        ext = os.path.splitext(raw)[1]
        gname = game["name"] if game else "Desktop"
        d = os.path.join(self.game_dir(game), "Sessions")
        os.makedirs(d, exist_ok=True)
        final = unique_path(os.path.join(d, f"{sanitize(gname)} session {stamp(started_at / 1000)}{ext}"))
        move(raw, final)
        return self.add(final, type="session", game={"id": game["id"], "name": gname} if game else None, bookmarks=bookmarks or [],
                        createdAt=started_at, title=f"{gname} session")

    # ---------------------------------------------------------------- edit
    def update(self, cid, patch):
        with self.lock:
            c = self.get(cid)
            if not c:
                return None
            for k in ("title", "favorite", "tags", "albums", "bookmarks", "game", "notes", "locked"):
                if k in patch:
                    c[k] = patch[k]
            if patch.get("title") and patch.get("renameFile"):
                target = unique_path(os.path.join(os.path.dirname(c["path"]), sanitize(patch["title"]) + os.path.splitext(c["path"])[1]))
                try:
                    os.replace(c["path"], target)
                    c["path"] = target
                except OSError:
                    pass
        self.changed()
        return c

    def remove(self, ids, delete_files=True):
        removed = []
        for cid in ids:
            c = self.get(cid)
            if not c:
                continue
            if delete_files and not c.get("external") and os.path.exists(c["path"]):
                try:
                    recycle(c["path"])
                except Exception:  # noqa: BLE001
                    pass
            if c.get("thumb"):
                try:
                    os.remove(c["thumb"])
                except OSError:
                    pass
            with self.lock:
                self.db["clips"] = [x for x in self.db["clips"] if x["id"] != cid]
            removed.append(cid)
        self.changed()
        return removed

    # ---------------------------------------------------------------- albums
    def create_album(self, name):
        a = {"id": str(uuid.uuid4()), "name": name or "New album", "createdAt": time.time() * 1000}
        with self.lock:
            self.db["albums"].append(a)
        self.changed()
        return a

    def rename_album(self, aid, name):
        for a in self.db["albums"]:
            if a["id"] == aid:
                a["name"] = name
        self.changed()

    def delete_album(self, aid):
        with self.lock:
            self.db["albums"] = [a for a in self.db["albums"] if a["id"] != aid]
            for c in self.db["clips"]:
                c["albums"] = [x for x in c.get("albums", []) if x != aid]
        self.changed()

    def add_to_album(self, aid, ids):
        with self.lock:
            for cid in ids:
                c = self.get(cid)
                if c and aid not in c["albums"]:
                    c["albums"].append(aid)
        self.changed()

    def remove_from_album(self, aid, ids):
        with self.lock:
            for cid in ids:
                c = self.get(cid)
                if c:
                    c["albums"] = [x for x in c["albums"] if x != aid]
        self.changed()

    # ---------------------------------------------------------------- maintenance
    def scan(self):
        dirty = False
        with self.lock:
            before = len(self.db["clips"])
            self.db["clips"] = [c for c in self.db["clips"] if os.path.exists(c["path"])]
            dirty = len(self.db["clips"]) != before
            known = {os.path.normcase(os.path.abspath(c["path"])) for c in self.db["clips"]}
        root = self.clip_folder
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            if d[len(root):].count(os.sep) > 3:
                continue
            for f in files:
                p = os.path.join(d, f)
                if not (VIDEO_EXT.search(f) or IMAGE_EXT.search(f)) or os.path.normcase(os.path.abspath(p)) in known:
                    continue
                parent = os.path.basename(d)
                game = None
                if os.path.abspath(d) != os.path.abspath(root) and parent not in ("Sessions", "Screenshots", "Exports", "Imported"):
                    game = {"id": parent.lower(), "name": parent}
                self.add(p, type="screenshot" if IMAGE_EXT.search(f) else "session" if "session" in f.lower() else "clip",
                         createdAt=os.path.getmtime(p) * 1000, game=game)
                dirty = True
        if dirty:
            self.changed()

    def storage_stats(self):
        ours = [c for c in self.db["clips"] if not c.get("external")]
        used = sum(c.get("size") or 0 for c in ours)
        try:
            u = shutil.disk_usage(self.clip_folder)
            free, total = u.free, u.total
        except OSError:
            free = total = None
        return {"used": used, "free": free, "total": total, "count": len(self.db["clips"]), "folder": self.clip_folder}

    def enforce_storage(self):
        s = self.settings.get()["storage"]
        if not s["autoCleanup"]:
            return []
        cap = s["maxGB"] * 1e9
        ours = [c for c in self.db["clips"] if not c.get("external")]
        used = sum(c.get("size") or 0 for c in ours)
        if used <= cap:
            return []
        cands = sorted([c for c in ours if not (s["keepFavorites"] and c.get("favorite")) and not c.get("locked") and not c.get("albums")],
                       key=lambda c: c["createdAt"])
        dele = []
        for c in cands:
            if used <= cap:
                break
            used -= c.get("size") or 0
            dele.append(c["id"])
        if dele:
            self.remove(dele)
            for fn in self.on_cleanup:
                fn(len(dele))
        return dele

    # ---------------------------------------------------------------- watch folders
    def set_watch_folders(self, folders):
        for d in list(self.watchers):
            if d not in folders:
                self.watchers.pop(d).set()
        for d in folders:
            if d in self.watchers or not os.path.isdir(d):
                continue
            stop = threading.Event()
            self.watchers[d] = stop
            threading.Thread(target=self._watch, args=(d, stop), daemon=True).start()

    def _watch(self, folder, stop):
        """Cheap polling watcher: new files are imported once they stop growing."""
        seen = {}
        first = True
        while not stop.wait(5):
            try:
                for d, _, files in os.walk(folder):
                    for f in files:
                        if not (VIDEO_EXT.search(f) or IMAGE_EXT.search(f)):
                            continue
                        p = os.path.join(d, f)
                        size = os.path.getsize(p)
                        prev = seen.get(p)
                        if prev == -1:
                            continue  # already known / imported
                        seen[p] = size
                        if first:
                            seen[p] = -1  # existing files are not imported
                        elif prev is not None and prev == size and prev > 0:
                            seen[p] = -1
                            self.add(p, type="import")
            except OSError:
                pass
            first = False

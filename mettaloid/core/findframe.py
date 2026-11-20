"""Find Frame service: references, saved finders, scan jobs, results, actions."""
import json
import os
import subprocess
import sys
import threading
import time
import uuid

from . import ffmpeg, paths, proc


class FindFrame:
    def __init__(self, settings, library, is_gaming):
        self.settings, self.library, self.is_gaming = settings, library, is_gaming
        self.dir = os.path.join(paths.USER_DATA, "findframe")
        self.ref_dir = os.path.join(self.dir, "refs")
        self.thumb_dir = os.path.join(self.dir, "thumbs")
        self.tmp_dir = os.path.join(self.dir, "jobs")
        for d in (self.dir, self.ref_dir, self.thumb_dir, self.tmp_dir):
            os.makedirs(d, exist_ok=True)
        self.file = os.path.join(self.dir, "finders.json")
        self.lock = threading.RLock()
        self.db = {"finders": [], "results": {}}
        try:
            with open(self.file, encoding="utf-8") as f:
                self.db.update(json.load(f))
        except Exception:  # noqa: BLE001
            pass
        self.jobs = {}
        self.on_job, self.on_match, self.on_thumb, self.on_changed = [], [], [], []
        self.thumb_lock = threading.Lock()
        self.auto_pending = set()
        self.auto_timer = None
        self._save_timer = None
        library.on_added.append(self.on_clip_added)
        self.cleanup()

    @property
    def cfg(self):
        return self.settings.get()["findFrame"]

    # ---------------------------------------------------------------- storage
    def flush(self):
        with self.lock:
            tmp = self.file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.db, f)
            os.replace(tmp, self.file)

    def save(self):
        with self.lock:
            if self._save_timer:
                self._save_timer.cancel()
            self._save_timer = threading.Timer(0.5, self.flush)
            self._save_timer.daemon = True
            self._save_timer.start()

    def changed(self):
        self.save()
        for fn in self.on_changed:
            fn()

    def cleanup(self):
        keep = {os.path.basename(p) for f in self.db["finders"] for p in (f.get("image"), f.get("frameImage")) if p}
        for f in os.listdir(self.ref_dir):
            p = os.path.join(self.ref_dir, f)
            try:
                if f not in keep and time.time() - os.path.getmtime(p) > 86400:
                    os.remove(p)
            except OSError:
                pass
        for f in os.listdir(self.tmp_dir):
            try:
                os.remove(os.path.join(self.tmp_dir, f))
            except OSError:
                pass

    # ---------------------------------------------------------------- engine
    @staticmethod
    def engine_cmd():
        if paths.FROZEN:
            return [sys.executable, "--engine"]
        return [sys.executable, paths.ENGINE_SCRIPT]

    def engine_settings(self, over=None):
        c = {**self.cfg, **(over or {})}
        keys = ["method", "threshold", "templateThreshold", "nfeatures", "ratio", "matcher", "verifyGeometry", "minInliers", "minMatches",
                "autoMinKeypoints", "scanFps", "scanWidth", "hwDecode", "boostContrast", "contrast", "equalize", "edges", "blur",
                "searchArea", "nearMargin", "scales", "minGapSec", "maxResultsPerClip", "stopAfterFirst"]
        out = {k: c[k] for k in keys if k in c}
        out["threads"] = c.get("threadsPerWorker", 2)
        out["liveScores"] = True
        return out

    def run_engine(self, job, on_event):
        jf = os.path.join(self.tmp_dir, uuid.uuid4().hex + ".json")
        with open(jf, "w", encoding="utf-8") as f:
            json.dump(job, f)
        p = proc.popen(self.engine_cmd() + [jf], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        if self.cfg["lowPriority"]:
            proc.set_priority(p.pid, "BelowNormal")
        err = []
        threading.Thread(target=lambda: err.extend(p.stderr.readlines()[-5:]), daemon=True).start()

        def wait():
            for line in p.stdout:
                line = line.strip()
                if line:
                    try:
                        on_event(json.loads(line))
                    except ValueError:
                        pass
            code = p.wait()
            try:
                os.remove(jf)
            except OSError:
                pass
            if code != 0 and not getattr(p, "killed_by_user", False):
                raise RuntimeError(f"Find Frame engine exited with code {code}: {''.join(err[-1:]).strip()}")
        return p, wait

    # ---------------------------------------------------------------- references
    def capture(self, clip_id, t, box):
        clip = self.library.get(clip_id)
        rid = uuid.uuid4().hex
        frame = os.path.join(self.ref_dir, f"{rid}-frame.jpg")
        image = os.path.join(self.ref_dir, f"{rid}.png")
        b = {"x": max(0.0, box["x"]), "y": max(0.0, box["y"])}
        b["w"] = min(1 - b["x"], box["w"])
        b["h"] = min(1 - b["y"], box["h"])
        ffmpeg.run(["-ss", f"{max(0.0, t):.3f}", "-i", clip["path"], "-frames:v", "1", "-q:v", "3", frame])
        ffmpeg.run(["-i", frame, "-vf", f"crop=trunc(iw*{b['w']}):trunc(ih*{b['h']}):trunc(iw*{b['x']}):trunc(ih*{b['y']})", "-frames:v", "1", image])
        return {"refId": rid, "image": image, "frameImage": frame, "box": b, "time": t, "clipId": clip_id, "width": clip["width"], "height": clip["height"]}

    def analyze(self, ref, over=None):
        preview = os.path.join(self.ref_dir, os.path.splitext(os.path.basename(ref["image"]))[0] + "-kp.png")
        res = {}
        _, wait = self.run_engine({"mode": "analyze", "ffmpeg": paths.FFMPEG, "preview": preview, "settings": self.engine_settings(over),
                                   "finders": [{"id": "analyze", "image": ref["image"], "box": ref["box"], "frameWidth": ref.get("width"),
                                                "frameHeight": ref.get("height"), "settings": over or {}}]},
                                  lambda e: res.update(e) if e.get("type") == "analysis" else None)
        wait()
        return res

    # ---------------------------------------------------------------- finders
    def finder(self, fid):
        return next((f for f in self.db["finders"] if f["id"] == fid), None)

    def count(self, fid):
        r = self.db["results"].get(fid) or {}
        return sum(len(v) for v in (r.get("matches") or {}).values())

    def finders(self):
        return [{**f, "matchCount": self.count(f["id"]), "clipCount": len((self.db["results"].get(f["id"]) or {}).get("matches") or {})} for f in self.db["finders"]]

    def create_finder(self, data):
        f = {"id": uuid.uuid4().hex, "name": data.get("name") or "Finder", "image": data["image"], "frameImage": data.get("frameImage"),
             "box": data["box"], "sourceClipId": data.get("clipId"), "sourceTime": data.get("time", 0), "settings": data.get("settings") or {},
             "autoScan": data.get("autoScan", True), "tag": data.get("tag", ""), "color": data.get("color", "#f5b70a"), "createdAt": time.time() * 1000}
        with self.lock:
            self.db["finders"].append(f)
        self.changed()
        return f

    def update_finder(self, fid, patch):
        f = self.finder(fid)
        if not f:
            return None
        for k in ("name", "settings", "autoScan", "tag", "color"):
            if k in patch:
                f[k] = patch[k]
        if "settings" in patch and fid in self.db["results"]:
            self.db["results"][fid]["scanned"] = {}
        self.changed()
        return f

    def delete_finder(self, fid):
        f = self.finder(fid)
        with self.lock:
            self.db["finders"] = [x for x in self.db["finders"] if x["id"] != fid]
            self.db["results"].pop(fid, None)
        if f:
            for p in (f.get("image"), f.get("frameImage")):
                try:
                    os.remove(p)
                except (OSError, TypeError):
                    pass
        self.changed()

    def results(self, fid):
        r = self.db["results"].get(fid) or {}
        out = []
        for cid, lst in (r.get("matches") or {}).items():
            c = self.library.get(cid)
            if c:
                out.append({"clipId": cid, "matches": sorted(lst, key=lambda m: m["t"]), "createdAt": c["createdAt"]})
        return sorted(out, key=lambda g: g["createdAt"], reverse=True)

    def clear_results(self, fid):
        self.db["results"].pop(fid, None)
        self.changed()

    def remove_match(self, fid, cid, t):
        r = self.db["results"].get(fid)
        if r and cid in r["matches"]:
            r["matches"][cid] = [m for m in r["matches"][cid] if abs(m["t"] - t) > 1e-3]
            if not r["matches"][cid]:
                del r["matches"][cid]
            self.changed()

    @staticmethod
    def sig(c):
        return f"{c.get('size')}|{round((c.get('duration') or 0) * 10)}"

    # ---------------------------------------------------------------- searching
    def search(self, label=None, reference=None, finder_ids=None, clip_ids="all", filters=None, rescan=False):
        jid = uuid.uuid4().hex
        finders = []
        if reference:
            finders.append({"id": f"tmp-{jid}", "name": reference.get("name") or "Search", "image": reference["image"], "frameImage": reference.get("frameImage"),
                            "box": reference["box"], "settings": reference.get("settings") or {}, "temp": True})
        for fid in finder_ids or []:
            f = self.finder(fid)
            if f:
                finders.append(f)
        if not finders:
            raise RuntimeError("Nothing to search for")
        clips = self.library.list() if clip_ids in ("all", None) else [c for c in (self.library.get(x) for x in clip_ids) if c]
        clips = [c for c in clips if c["type"] != "screenshot" and (c.get("duration") or 0) > 0 and os.path.exists(c["path"])]
        fl = filters or {}
        if fl.get("game"):
            clips = [c for c in clips if (c["game"]["name"] if c.get("game") else "Desktop") == fl["game"]]
        if fl.get("types"):
            clips = [c for c in clips if c["type"] in fl["types"]]
        if fl.get("since"):
            clips = [c for c in clips if c["createdAt"] >= fl["since"]]
        if fl.get("favorites"):
            clips = [c for c in clips if c.get("favorite")]
        job = {"id": jid, "label": label or ", ".join(f["name"] for f in finders), "kind": "search" if reference else "scan",
               "finders": [{"id": f["id"], "name": f["name"], "image": f["image"], "temp": bool(f.get("temp")), "color": f.get("color")} for f in finders],
               "reference": reference, "total": len(clips), "done": 0, "skipped": 0, "status": "running", "startedAt": time.time() * 1000,
               "matches": [], "current": {}, "errors": [], "fps": 0, "paused": False}
        job["_procs"] = []
        self.jobs[jid] = job
        self.emit_job(job)
        threading.Thread(target=self._execute, args=(job, finders, clips, rescan), daemon=True).start()
        return self.public(job)

    def _execute(self, job, finders, clips, rescan):
        try:
            c = self.cfg
            work = []
            for clip in clips:
                sg = self.sig(clip)
                todo = [f for f in finders if f.get("temp") or rescan or not c["skipScanned"] or ((self.db["results"].get(f["id"]) or {}).get("scanned") or {}).get(clip["id"]) != sg]
                if todo:
                    work.append((clip, todo, sg))
                else:
                    job["skipped"] += 1
            job["done"] = job["skipped"]
            self.emit_job(job)
            n = max(1, min(int(c["workers"]), len(work) or 1))
            lanes = [work[i::n] for i in range(n)]
            ts = [threading.Thread(target=self._lane, args=(job, lane), daemon=True) for lane in lanes if lane]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            if job["status"] == "running":
                job["status"] = "done"
        except Exception as e:  # noqa: BLE001
            if job["status"] != "cancelled":
                job["status"] = "error"
                job["error"] = str(e)
        job["finishedAt"] = time.time() * 1000
        self.flush()
        self.emit_job(job)

    def _lane(self, job, lane):
        groups = {}
        for clip, fs, sg in lane:
            groups.setdefault("|".join(f["id"] for f in fs), (fs, []))[1].append((clip, sg))
        for fs, items in groups.values():
            if job["status"] != "running":
                return
            while self.cfg["pauseWhileGaming"] and self.is_gaming() and job["status"] == "running":
                job["paused"] = True
                self.emit_job(job)
                time.sleep(5)
            job["paused"] = False
            by_video = {clip["id"]: (clip, fs, sg) for clip, sg in items}
            ej = {"ffmpeg": paths.FFMPEG, "settings": self.engine_settings(),
                  "finders": [{"id": f["id"], "image": f["image"], "box": f["box"], "settings": f.get("settings") or {}} for f in fs],
                  "videos": [{"id": c["id"], "path": c["path"], "width": c["width"], "height": c["height"], "fps": c["fps"], "duration": c["duration"]} for c, _ in items]}
            try:
                p, wait = self.run_engine(ej, lambda e: self._event(job, e, by_video))
                job["_procs"].append(p)
                wait()
            except Exception as e:  # noqa: BLE001
                job["errors"].append(str(e))

    def _event(self, job, e, by_video):
        t = e.get("type")
        if t == "progress":
            job["current"][e["video"]] = {"t": e["t"], "duration": e["duration"], "scores": e.get("scores")}
            job["fps"] = e.get("fps", 0)
            self.emit_job(job, throttle=True)
        elif t == "video-start":
            job["current"][e["video"]] = {"t": 0, "duration": (self.library.get(e["video"]) or {}).get("duration", 0)}
            self.emit_job(job, throttle=True)
        elif t == "video-done":
            job["current"].pop(e["video"], None)
            job["done"] += 1
            v = by_video.get(e["video"])
            if v:
                with self.lock:
                    for f in v[1]:
                        if not f.get("temp"):
                            r = self.db["results"].setdefault(f["id"], {"matches": {}, "scanned": {}})
                            r.setdefault("scanned", {})[e["video"]] = v[2]
                self.save()
            self.emit_job(job)
        elif t == "match":
            self._match(job, e)
        elif t == "error":
            job["errors"].append(e.get("message"))
            self.emit_job(job)

    def _match(self, job, e):
        c = self.cfg
        m = {"id": uuid.uuid4().hex, "finderId": e["finder"], "clipId": e["video"], "t": e["t"], "start": e["start"], "end": e["end"],
             "score": e["score"], "box": e["box"], "method": e["method"], "thumb": None, "foundAt": time.time() * 1000}
        job["matches"].append(m)
        f = self.finder(e["finder"])
        if f:
            with self.lock:
                r = self.db["results"].setdefault(f["id"], {"matches": {}, "scanned": {}})
                lst = r["matches"].setdefault(e["video"], [])
                dup = next((x for x in lst if abs(x["t"] - m["t"]) < max(1, c["minGapSec"])), None)
                if dup:
                    m["id"] = dup["id"]
                else:
                    lst.append(m)
            self.apply_actions(f, m)
            self.changed()  # live finder counts in the UI (UI side is debounced)
        for fn in self.on_match:
            fn(job["id"], m)
        self.emit_job(job, throttle=True)
        if c["thumbnails"]:
            threading.Thread(target=self._thumb, args=(m, f), daemon=True).start()

    def apply_actions(self, f, m):
        c = self.cfg
        clip = self.library.get(m["clipId"])
        if not clip:
            return
        patch = {}
        if c["autoTag"]:
            tag = (f.get("tag") or f["name"]).strip().lstrip("#")
            if tag and tag not in clip.get("tags", []):
                patch["tags"] = clip.get("tags", []) + [tag]
        if c["addBookmarks"]:
            label = f"Find: {f['name']}"
            bms = clip.get("bookmarks", [])
            if not any(b["label"] == label and abs(b["time"] - m["t"]) < 1.5 for b in bms):
                patch["bookmarks"] = sorted(bms + [{"time": m["t"], "label": label}], key=lambda b: b["time"])
        if patch:
            self.library.update(clip["id"], patch)

    def _thumb(self, m, f):
        with self.thumb_lock:
            clip = self.library.get(m["clipId"])
            if not clip:
                return
            out = os.path.join(self.thumb_dir, f"{m['clipId']}-{round(m['t'] * 1000)}.jpg")
            if not os.path.exists(out):
                x, y, w, h = m["box"]
                color = ((f or {}).get("color") or "#f5b70a").replace("#", "0x")
                try:
                    ffmpeg.run(["-ss", f"{m['t']:.3f}", "-i", clip["path"], "-frames:v", "1", "-vf",
                                f"scale=480:-2,drawbox=x=iw*{x}:y=ih*{y}:w=iw*{w}:h=ih*{h}:color={color}@0.95:t=3", "-q:v", "4", out], low_priority=True, threads=1)
                except Exception:  # noqa: BLE001
                    return
            m["thumb"] = out
            if f:
                r = self.db["results"].get(f["id"]) or {}
                for x in (r.get("matches") or {}).get(m["clipId"], []):
                    if x["id"] == m["id"]:
                        x["thumb"] = out
                self.save()
        for fn in self.on_thumb:
            fn(m["id"], out)

    # ---------------------------------------------------------------- jobs
    def public(self, job):
        d = {k: v for k, v in job.items() if not k.startswith("_")}
        d["matchCount"] = len(job["matches"])
        d["matches"] = list(job["matches"][-500:])
        d["current"] = dict(job["current"])
        return d

    def emit_job(self, job, throttle=False):
        now = time.time()
        if throttle and now - job.get("_last", 0) < 0.25:
            return
        job["_last"] = now
        pub = self.public(job)
        for fn in self.on_job:
            fn(pub)

    def list_jobs(self):
        return sorted((self.public(j) for j in self.jobs.values()), key=lambda j: j["startedAt"], reverse=True)

    def cancel(self, jid):
        job = self.jobs.get(jid)
        if not job or job["status"] != "running":
            return
        job["status"] = "cancelled"
        for p in job["_procs"]:
            p.killed_by_user = True
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
        self.emit_job(job)

    def clear_jobs(self):
        for jid in [k for k, j in self.jobs.items() if j["status"] != "running"]:
            del self.jobs[jid]

    def cancel_all(self):
        for jid in list(self.jobs):
            self.cancel(jid)

    # ---------------------------------------------------------------- auto scan
    def on_clip_added(self, clip):
        if not self.cfg["autoScanNewClips"] or clip["type"] == "screenshot" or not any(f.get("autoScan") for f in self.db["finders"]):
            return
        self.auto_pending.add(clip["id"])
        if self.auto_timer:
            self.auto_timer.cancel()

        def fire():
            ids = list(self.auto_pending)
            self.auto_pending.clear()
            fids = [f["id"] for f in self.db["finders"] if f.get("autoScan")]
            if ids and fids:
                try:
                    self.search(label=f"Auto-scan · {len(ids)} new clip{'s' if len(ids) > 1 else ''}", finder_ids=fids, clip_ids=ids)
                except Exception:  # noqa: BLE001
                    pass
        self.auto_timer = threading.Timer(8, fire)
        self.auto_timer.daemon = True
        self.auto_timer.start()

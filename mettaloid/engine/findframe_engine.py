"""
Mettaloid Find Frame engine.

Finds every moment in one or more videos that matches a reference image
(a box drawn on a paused frame). The feature-matching approach is ported from
FindFrame by Gunbard (MIT License, https://github.com/Gunbard/FindFrame):
ORB keypoints + Lowe ratio test, counting a frame as a match when enough of the
reference's descriptors find a good partner. On top of that this engine adds:

  * template matching (normalised cross-correlation, multi-scale) - far more
    reliable for fixed game HUD elements such as kill icons or banners
  * "auto" method selection based on how much texture the reference has
  * RANSAC homography verification to reject false feature matches
  * restricting the search to the area around where the box was drawn
  * scanning many references per decoded frame (decode once, test all)
  * merging consecutive hits into events with a peak score/time
  * frames decoded by FFmpeg (hardware accelerated, downscaled, sampled fps)

Protocol: `engine.exe <job.json>`; results are JSON lines on stdout.
The engine is UI-agnostic so a native (Qt) frontend can reuse it unchanged.
"""
import json
import math
import subprocess
import sys
import time

import cv2
import numpy as np

VERSION = "1.0.0"


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def even(n):
    n = int(round(n))
    return max(2, n - (n % 2))


# ---------------------------------------------------------------- preprocessing
class Pre:
    def __init__(self, s):
        self.contrast = float(s.get("contrast", 1.0)) if s.get("boostContrast") else 1.0
        self.equalize = bool(s.get("equalize"))
        self.edges = bool(s.get("edges"))
        self.blur = int(s.get("blur", 0))
        self.clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)) if self.equalize else None

    def __call__(self, gray):
        img = gray
        if self.contrast != 1.0:
            # same formula FindFrame uses for its "boost contrast" option
            brightness = int(round(255 * (1 - self.contrast) / 2))
            img = cv2.addWeighted(img, self.contrast, img, 0, brightness)
        if self.clahe is not None:
            img = self.clahe.apply(img)
        if self.blur > 0:
            k = self.blur * 2 + 1
            img = cv2.GaussianBlur(img, (k, k), 0)
        if self.edges:
            img = cv2.Canny(img, 60, 160)
        return img


# ---------------------------------------------------------------- reference
class Reference:
    """One saved finder, prepared for one particular video size."""

    def __init__(self, spec, W, H, settings, pre):
        self.id = spec["id"]
        self.spec = spec
        s = {**settings, **(spec.get("settings") or {})}
        self.s = s
        box = spec.get("box") or {"x": 0, "y": 0, "w": 1, "h": 1}
        self.box = box
        src = cv2.imread(spec["image"], cv2.IMREAD_GRAYSCALE)
        if src is None:
            raise ValueError("cannot read reference image %s" % spec["image"])
        tw = max(8, int(round(box["w"] * W)))
        th = max(8, int(round(box["h"] * H)))
        interp = cv2.INTER_AREA if tw < src.shape[1] else cv2.INTER_CUBIC
        self.tmpl = pre(cv2.resize(src, (tw, th), interpolation=interp))
        self.W, self.H = W, H

        # search area
        self.near = s.get("searchArea", "anywhere") == "near"
        m = float(s.get("nearMargin", 0.15))
        if self.near:
            x0 = max(0, int((box["x"] - m) * W))
            y0 = max(0, int((box["y"] - m) * H))
            x1 = min(W, int((box["x"] + box["w"] + m) * W))
            y1 = min(H, int((box["y"] + box["h"] + m) * H))
            self.region = (x0, y0, x1, y1)
        else:
            self.region = (0, 0, W, H)

        # features
        self.orb = cv2.ORB_create(nfeatures=int(s.get("nfeatures", 1000)), fastThreshold=int(s.get("fastThreshold", 20)))
        self.kp, self.des = self.orb.detectAndCompute(self.tmpl, None)
        nkp = 0 if self.des is None else len(self.des)
        self.std = float(np.std(self.tmpl))

        method = s.get("method", "auto")
        if method == "auto":
            # low-texture references (flat icons, text) → template matching
            method = "features" if nkp >= int(s.get("autoMinKeypoints", 60)) and not self.near else "template"
        if method == "features" and nkp < 8:
            method = "template"
        self.method = method

        self.thr_features = float(s.get("threshold", 30)) / 100.0
        self.thr_template = float(s.get("templateThreshold", 0.8))
        self.ratio = float(s.get("ratio", 0.9))
        self.verify = bool(s.get("verifyGeometry", True))
        self.min_inliers = int(s.get("minInliers", 8))
        self.min_matches = int(s.get("minMatches", 10))
        scales = s.get("scales") or [1.0]
        if isinstance(scales, str):
            scales = [float(x) for x in scales.replace(" ", "").split(",") if x]
        self.scales = [x for x in scales if x > 0] or [1.0]
        # flat templates break normalised correlation; use CCORR there
        self.tm_mode = cv2.TM_CCOEFF_NORMED if self.std > 4 else cv2.TM_CCORR_NORMED
        self.scaled_tmpls = []
        for sc in self.scales:
            w = max(6, int(round(self.tmpl.shape[1] * sc)))
            h = max(6, int(round(self.tmpl.shape[0] * sc)))
            self.scaled_tmpls.append((sc, cv2.resize(self.tmpl, (w, h), interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_LINEAR)))

    def info(self):
        return {"id": self.id, "method": self.method, "keypoints": 0 if self.des is None else len(self.des), "std": round(self.std, 1)}

    # -- template matching
    def _search(self, area, t):
        res = cv2.matchTemplate(area, t, self.tm_mode)
        _, maxv, _, loc = cv2.minMaxLoc(res)
        return (maxv if math.isfinite(maxv) else 0.0), loc

    def match_template(self, frame):
        x0, y0, x1, y1 = self.region
        area = frame[y0:y1, x0:x1]
        big = area.shape[0] * area.shape[1] > 12 * self.tmpl.shape[0] * self.tmpl.shape[1]
        small_area = cv2.pyrDown(area) if big else None
        best = (0.0, None)
        for sc, t in self.scaled_tmpls:
            th, tw = t.shape[:2]
            if th > area.shape[0] or tw > area.shape[1]:
                continue
            if big and th >= 16 and tw >= 16:
                # coarse-to-fine: search at half resolution, refine around the peak
                ts = cv2.resize(t, (tw // 2, th // 2), interpolation=cv2.INTER_AREA)
                coarse, cl = self._search(small_area, ts)
                if coarse < self.thr_template - 0.2:
                    best = max(best, (coarse, None), key=lambda b: b[0])
                    continue
                cx, cy = cl[0] * 2, cl[1] * 2
                wx0, wy0 = max(0, cx - 8), max(0, cy - 8)
                wx1, wy1 = min(area.shape[1], cx + tw + 8), min(area.shape[0], cy + th + 8)
                maxv, loc = self._search(area[wy0:wy1, wx0:wx1], t)
                loc = (loc[0] + wx0, loc[1] + wy0)
            else:
                maxv, loc = self._search(area, t)
            if maxv > best[0]:
                best = (maxv, (loc[0] + x0, loc[1] + y0, tw, th))
        score, rect = best
        if rect is not None and score >= self.thr_template:
            return score, rect
        return score, None

    # -- feature matching (FindFrame method + geometric verification)
    def match_features(self, kp, des, matcher):
        if des is None or self.des is None or len(des) < 2:
            return 0.0, None
        try:
            pairs = matcher.knnMatch(self.des, des, k=2)
        except cv2.error:
            return 0.0, None
        good = []
        x0, y0, x1, y1 = self.region
        for p in pairs:
            if len(p) < 2:
                continue
            m, n = p
            if m.distance < self.ratio * n.distance:
                if self.near:
                    px, py = kp[m.trainIdx].pt
                    if not (x0 <= px <= x1 and y0 <= py <= y1):
                        continue
                good.append(m)
        score = len(good) / float(len(self.des))
        if len(good) < self.min_matches or score < self.thr_features:
            return score, None
        rect = None
        if len(good) >= 4:
            src = np.float32([self.kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
            dst = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
            Hm, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
            inliers = int(mask.sum()) if mask is not None else 0
            if self.verify and (Hm is None or inliers < self.min_inliers):
                return score, None
            if Hm is not None:
                h, w = self.tmpl.shape[:2]
                corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
                pts = cv2.perspectiveTransform(corners, Hm).reshape(-1, 2)
                rx, ry, rw, rh = cv2.boundingRect(pts)
                rect = (rx, ry, rw, rh)
        elif self.verify:
            return score, None
        if rect is None:
            pts = np.float32([kp[m.trainIdx].pt for m in good])
            rect = cv2.boundingRect(pts)
        return score, rect


# ---------------------------------------------------------------- events
class EventTracker:
    def __init__(self, ref, video_id, min_gap, max_results, W, H):
        self.ref, self.vid, self.gap, self.max = ref, video_id, min_gap, max_results
        self.W, self.H = W, H
        self.cur = None
        self.count = 0

    def hit(self, t, score, rect):
        if self.cur and t - self.cur["end"] <= self.gap:
            self.cur["end"] = t
            if score > self.cur["score"]:
                self.cur.update(score=score, t=t, rect=rect)
            return
        self.flush()
        self.cur = {"start": t, "end": t, "t": t, "score": score, "rect": rect}

    def flush(self):
        if not self.cur or self.count >= self.max:
            self.cur = None
            return
        c = self.cur
        x, y, w, h = c["rect"]
        emit({
            "type": "match", "video": self.vid, "finder": self.ref.id, "method": self.ref.method,
            "t": round(c["t"], 3), "start": round(c["start"], 3), "end": round(c["end"], 3),
            "score": round(float(c["score"]), 4),
            "box": [round(x / self.W, 4), round(y / self.H, 4), round(w / self.W, 4), round(h / self.H, 4)]
        })
        self.count += 1
        self.cur = None

    @property
    def full(self):
        return self.count >= self.max


# ---------------------------------------------------------------- scanning
def scan_video(job, video, pre):
    s = job.get("settings", {})
    ffmpeg = job["ffmpeg"]
    src_w, src_h = int(video.get("width") or 1920), int(video.get("height") or 1080)
    duration = float(video.get("duration") or 0)
    start = float(video.get("start") or 0)
    end = float(video.get("end") or 0) or duration
    scan_w = int(s.get("scanWidth", 960)) or src_w
    W = even(min(scan_w, src_w))
    H = even(src_h * W / float(src_w))
    fps = float(s.get("scanFps", 5))
    src_fps = float(video.get("fps") or 60)
    eff_fps = fps if fps > 0 else src_fps

    refs = []
    for spec in job["finders"]:
        try:
            refs.append(Reference(spec, W, H, s, pre))
        except Exception as e:  # noqa: BLE001
            emit({"type": "error", "video": video["id"], "finder": spec.get("id"), "message": str(e)})
    if not refs:
        return
    emit({"type": "video-start", "video": video["id"], "width": W, "height": H, "fps": eff_fps, "finders": [r.info() for r in refs]})

    need_features = any(r.method == "features" for r in refs)
    frame_orb = cv2.ORB_create(nfeatures=int(s.get("frameFeatures", max(1500, int(s.get("nfeatures", 1000)) * 2))))
    if s.get("matcher", "bf") == "flann":
        matcher = cv2.FlannBasedMatcher(dict(algorithm=6, table_number=6, key_size=12, multi_probe_level=1), dict(checks=32))
    else:
        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)

    vf = []
    if fps > 0:
        vf.append("fps=%g" % fps)
    vf.append("scale=%d:%d:flags=area" % (W, H))
    vf.append("format=gray")
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
    hw = s.get("hwDecode", "auto")
    # GPU decode costs ~1 s to initialise; only worth it for long videos
    if hw is True or (hw == "auto" and duration > 300):
        cmd += ["-hwaccel", "auto"]
    if start > 0:
        cmd += ["-ss", "%.3f" % start]
    cmd += ["-i", video["path"]]
    if end and end < duration:
        cmd += ["-t", "%.3f" % (end - start)]
    cmd += ["-an", "-sn", "-vf", ",".join(vf), "-f", "rawvideo", "-pix_fmt", "gray", "-"]

    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags, bufsize=W * H * 4)
    trackers = {r.id: EventTracker(r, video["id"], float(s.get("minGapSec", 2)), int(s.get("maxResultsPerClip", 50)), W, H) for r in refs}
    frame_bytes = W * H
    i = 0
    t0 = time.time()
    last_progress = 0
    live = bool(s.get("liveScores", False))
    stop_first = bool(s.get("stopAfterFirst", False))
    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if not buf or len(buf) < frame_bytes:
                break
            t = start + i / eff_fps
            i += 1
            frame = pre(np.frombuffer(buf, dtype=np.uint8).reshape(H, W))
            kp = des = None
            if need_features:
                kp, des = frame_orb.detectAndCompute(frame, None)
            scores = {}
            for r in refs:
                tr = trackers[r.id]
                if tr.full:
                    continue
                if r.method == "features":
                    score, rect = r.match_features(kp, des, matcher)
                else:
                    score, rect = r.match_template(frame)
                scores[r.id] = round(float(score), 3)
                if rect is not None:
                    tr.hit(t, score, rect)
            now = time.time()
            if now - last_progress > 0.4:
                last_progress = now
                msg = {"type": "progress", "video": video["id"], "t": round(t, 2), "duration": round(end, 2), "fps": round(i / max(1e-6, now - t0), 1)}
                if live:
                    msg["scores"] = scores
                emit(msg)
            if stop_first and any(tr.count or tr.cur for tr in trackers.values()):
                break
            if all(tr.full for tr in trackers.values()):
                break
    finally:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
    for tr in trackers.values():
        tr.flush()
    emit({"type": "video-done", "video": video["id"], "frames": i, "elapsed": round(time.time() - t0, 2)})


def analyze(job):
    """Report how a reference will be matched + a keypoint preview image (like FindFrame's thumbnail)."""
    s = job.get("settings", {})
    pre = Pre(s)
    spec = job["finders"][0]
    img = cv2.imread(spec["image"], cv2.IMREAD_GRAYSCALE)
    if img is None:
        emit({"type": "error", "message": "cannot read image"})
        return
    W, H = int(spec.get("frameWidth") or img.shape[1]), int(spec.get("frameHeight") or img.shape[0])
    box = spec.get("box") or {"x": 0, "y": 0, "w": 1, "h": 1}
    ref = Reference({**spec, "box": box}, int(round(img.shape[1] / max(box["w"], 1e-6))), int(round(img.shape[0] / max(box["h"], 1e-6))), s, pre)
    out = None
    if job.get("preview"):
        vis = cv2.drawKeypoints(cv2.cvtColor(ref.tmpl, cv2.COLOR_GRAY2BGR), ref.kp or [], None, color=(10, 183, 245), flags=0)
        cv2.imwrite(job["preview"], vis)
        out = job["preview"]
    emit({"type": "analysis", **ref.info(), "preview": out, "width": int(img.shape[1]), "height": int(img.shape[0]), "frame": [W, H]})


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-v", "--version"):
        emit({"type": "version", "version": VERSION, "opencv": cv2.__version__})
        return
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        job = json.load(f)
    cv2.setNumThreads(int(job.get("settings", {}).get("threads", 0)))
    if job.get("mode") == "analyze":
        analyze(job)
        return
    pre = Pre(job.get("settings", {}))
    total = len(job["videos"])
    for n, video in enumerate(job["videos"]):
        emit({"type": "queue", "index": n, "total": total, "video": video["id"]})
        try:
            scan_video(job, video, pre)
        except Exception as e:  # noqa: BLE001
            emit({"type": "error", "video": video.get("id"), "message": str(e)})
    emit({"type": "done"})


if __name__ == "__main__":
    main()

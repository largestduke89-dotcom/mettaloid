"""FFmpeg / FFprobe helpers."""
import json
import os
import re
import subprocess
import threading

from . import paths, proc

_hw_cache = None
_hw_lock = threading.Lock()


def probe(file):
    out = subprocess.run([paths.FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", file],
                         capture_output=True, text=True, creationflags=proc.CREATE_NO_WINDOW, timeout=60)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or "ffprobe failed")
    j = json.loads(out.stdout)
    streams = j.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    fps = 0.0
    if v:
        n, _, d = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "0/1").partition("/")
        try:
            fps = round(float(n) / float(d or 1), 2)
        except (ValueError, ZeroDivisionError):
            fps = 0.0
    fmt = j.get("format", {})
    return {
        "duration": float(fmt.get("duration") or (v or {}).get("duration") or 0),
        "size": int(fmt.get("size") or 0),
        "bitrate": int(fmt.get("bit_rate") or 0),
        "width": int((v or {}).get("width") or 0),
        "height": int((v or {}).get("height") or 0),
        "fps": fps,
        "videoCodec": (v or {}).get("codec_name"),
        "audioTracks": [{"index": i, "codec": a.get("codec_name"), "channels": a.get("channels"),
                         "title": (a.get("tags") or {}).get("title") or (a.get("tags") or {}).get("handler_name") or f"Track {i + 1}"}
                        for i, a in enumerate(audio)],
    }


class Run:
    """A running ffmpeg process with progress parsing and cancellation."""

    def __init__(self, args, duration=0.0, on_progress=None, cwd=None, low_priority=False, threads=0):
        full = list(args)
        if threads and threads > 0:
            n = str(threads)
            full = ["-threads", n, "-filter_complex_threads", n] + full[:-1] + ["-threads", n, full[-1]]
        cmd = [paths.FFMPEG, "-hide_banner", "-y", "-progress", "pipe:1", "-nostats"] + full
        self.cancelled = False
        self.err_tail = ""
        self.p = proc.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, text=True, encoding="utf-8", errors="replace")
        if low_priority:
            proc.set_priority(self.p.pid, "BelowNormal")
        self.duration = duration
        self.on_progress = on_progress
        self._t = threading.Thread(target=self._read_err, daemon=True)
        self._t.start()

    def _read_err(self):
        for line in self.p.stderr:
            self.err_tail = (self.err_tail + line)[-4000:]

    def wait(self):
        for line in self.p.stdout:
            line = line.strip()
            m = re.match(r"out_time_(?:us|ms)=(\d+)", line)
            if m and self.duration > 0 and self.on_progress:
                secs = int(m.group(1)) / 1e6
                self.on_progress(min(1.0, max(0.0, secs / self.duration)), {"secs": secs})
            sp = re.match(r"speed=\s*([\d.]+)x", line)
            if sp and self.on_progress:
                self.on_progress(None, {"speed": float(sp.group(1))})
        code = self.p.wait()
        self._t.join(timeout=2)
        if self.cancelled:
            raise RuntimeError("Cancelled")
        if code != 0:
            tail = "\n".join(self.err_tail.strip().splitlines()[-6:])
            raise RuntimeError(f"ffmpeg exited with code {code}\n{tail}")

    def cancel(self):
        self.cancelled = True
        try:
            self.p.kill()
        except Exception:  # noqa: BLE001
            pass


def run(args, **kw):
    r = Run(args, **kw)
    r.wait()


def thumbnail(file, out, at=1.0, width=480):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    if re.search(r"\.(png|jpe?g|webp|bmp)$", file, re.I):
        args = ["-i", file, "-vf", f"scale={width}:-2", "-frames:v", "1", out]
    else:
        args = ["-ss", f"{max(0.0, at):.3f}", "-i", file, "-vf", f"scale={width}:-2", "-frames:v", "1", "-q:v", "4", out]
    run(args, low_priority=True, threads=2)
    return out


def trim_tail(inp, out, seconds):
    run(["-sseof", f"-{seconds}", "-i", inp, "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", out])
    return out


def fast_cut(inp, out, start, end):
    run(["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", inp, "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", out])
    return out


def hw_encoders():
    """Hardware H.264 encoders that actually work on this machine."""
    global _hw_cache
    with _hw_lock:
        if _hw_cache is not None:
            return _hw_cache
        ok = []
        for enc in ("h264_nvenc", "h264_amf", "h264_qsv"):
            try:
                r = subprocess.run([paths.FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.2",
                                    "-c:v", enc, "-f", "null", "-"], capture_output=True, timeout=10, creationflags=proc.CREATE_NO_WINDOW)
                if r.returncode == 0:
                    ok.append(enc)
            except Exception:  # noqa: BLE001
                pass
        _hw_cache = ok
        return ok

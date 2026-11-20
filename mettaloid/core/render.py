"""Non-destructive render pipeline used by the editor, compressor and montage.

project = {
  sources: [path], segments: [{src, start, end, speed}], crop: {x,y,w,h}|None (0..1),
  rotate, flipH, flipV, aspect: 'source'|'16:9'|'9:16'|..., fit: 'crop'|'pad'|'blur',
  color: {brightness, contrast, saturation, gamma, sharpen, vignette, hue}, fadeIn, fadeOut,
  texts: [{text, start, end, x, y, size, color, box, boxColor, boxOpacity, outline}],
  audio: {tracks: [{index, volume, muted}], normalize, music: {path, volume, start, loop}|None},
  output: {resolution, fps, format, codec, quality, targetMB, encoder, twoPass, autoDownscale, audioKbps, mode}
}
"""
import math
import os
import shutil
import tempfile

from . import ffmpeg


def even(n):
    return max(2, int(round(n / 2)) * 2)


def num(v, d):
    try:
        if v is None or v == "":
            return d
        f = float(v)
        return d if math.isnan(f) else f
    except (TypeError, ValueError):
        return d


def esc(s):
    return "'" + str(s).replace("\\", "/").replace(":", "\\:").replace("'", "'\\''") + "'"


def atempo_chain(speed):
    parts, s = [], speed
    while s > 2:
        parts.append("atempo=2")
        s /= 2
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    if abs(s - 1) > 1e-3:
        parts.append(f"atempo={s:.5f}")
    return parts


def parse_aspect(a):
    if not a or a == "source":
        return None
    w, _, h = a.partition(":")
    try:
        return float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return None


def output_duration(project):
    return sum(max(0.0, s["end"] - s["start"]) / (s.get("speed") or 1) for s in project["segments"])


def geometry(project, info, short_side=None):
    c = project.get("crop")
    cw, ch = info["width"], info["height"]
    if c and c.get("w", 0) > 0 and c.get("h", 0) > 0:
        cw, ch = even(info["width"] * c["w"]), even(info["height"] * c["h"])
    if project.get("rotate") in (90, 270):
        cw, ch = ch, cw
    A = parse_aspect(project.get("aspect")) or cw / ch
    fit = project.get("fit") or "crop"
    if not parse_aspect(project.get("aspect")):
        nw, nh = cw, ch
    elif fit == "crop":
        nw = min(cw, ch * A)
        nh = nw / A
    else:
        short = min(cw, ch)
        nw, nh = (short * A, short) if A >= 1 else (short, short / A)
    W, H = nw, nh
    out_res = (project.get("output") or {}).get("resolution")
    res = short_side or (float(out_res) if out_res not in (None, "", "source") else None)
    if res:
        if W >= H:
            H = min(res, nh * 4)
            W = H * A
        else:
            W = min(res, nw * 4)
            H = W / A
    return {"cw": cw, "ch": ch, "A": A, "fit": fit, "W": even(W), "H": even(H)}


def build_filters(project, infos, geo, fps, audio_on, duration, tmp_dir, font, music_input, fmt, base):
    W, H, A, fit = geo["W"], geo["H"], geo["A"], geo["fit"]
    f = []
    multi = len(project["sources"]) > 1
    segs = project["segments"]
    tracks_cfg = (project.get("audio") or {}).get("tracks") or []

    for s, _ in enumerate(project["sources"]):
        uses = sum(1 for g in segs if g["src"] == s)
        if not uses:
            continue
        f.append(f"[{s}:v]split={uses}" + "".join(f"[sv{s}_{i}]" for i in range(uses)))
        if not audio_on:
            continue
        info = infos[s]
        if not info["audioTracks"]:
            f.append(f"anullsrc=r=48000:cl=stereo[an{s}]")
            mix = f"an{s}"
        else:
            cfg = [t for t in tracks_cfg if not t.get("muted") and t["index"] < len(info["audioTracks"])] if (not multi and tracks_cfg) else [{"index": 0, "volume": 1}]
            if not cfg:
                f.append(f"anullsrc=r=48000:cl=stereo[an{s}]")
                mix = f"an{s}"
            else:
                for i, t in enumerate(cfg):
                    f.append(f"[{s}:a:{t['index']}]aresample=48000,aformat=channel_layouts=stereo,volume={num(t.get('volume'), 1):.3f}[at{s}_{i}]")
                if len(cfg) == 1:
                    mix = f"at{s}_0"
                else:
                    f.append("".join(f"[at{s}_{i}]" for i in range(len(cfg))) + f"amix=inputs={len(cfg)}:normalize=0:dropout_transition=0[am{s}]")
                    mix = f"am{s}"
        f.append(f"[{mix}]asplit={uses}" + "".join(f"[sa{s}_{i}]" for i in range(uses)))

    counters, concat_in = {}, []
    for i, g in enumerate(segs):
        k = counters.get(g["src"], 0)
        counters[g["src"]] = k + 1
        sp = num(g.get("speed"), 1)
        v = [f"trim=start={g['start']:.3f}:end={g['end']:.3f}", "setpts=PTS-STARTPTS"]
        if sp != 1:
            v.append(f"setpts=PTS/{sp}")
        if multi:
            v += [f"scale={base[0]}:{base[1]}:force_original_aspect_ratio=decrease", f"pad={base[0]}:{base[1]}:(ow-iw)/2:(oh-ih)/2", "setsar=1", f"fps={fps or 60}"]
        f.append(f"[sv{g['src']}_{k}]" + ",".join(v) + f"[v{i}]")
        concat_in.append(f"[v{i}]")
        if audio_on:
            a = [f"atrim=start={g['start']:.3f}:end={g['end']:.3f}", "asetpts=PTS-STARTPTS"] + atempo_chain(sp)
            f.append(f"[sa{g['src']}_{k}]" + ",".join(a) + f"[a{i}]")
            concat_in.append(f"[a{i}]")
    f.append("".join(concat_in) + f"concat=n={len(segs)}:v=1:a={1 if audio_on else 0}[vc]" + ("[ac]" if audio_on else ""))

    v = []
    c = project.get("crop")
    if c and c.get("w", 0) > 0 and c.get("h", 0) > 0 and not (c["w"] >= 0.999 and c["h"] >= 0.999):
        v.append(f"crop=w=trunc(iw*{c['w']}/2)*2:h=trunc(ih*{c['h']}/2)*2:x=iw*{c['x']}:y=ih*{c['y']}")
    rot = project.get("rotate") or 0
    if rot == 90:
        v.append("transpose=1")
    elif rot == 180:
        v.append("transpose=1,transpose=1")
    elif rot == 270:
        v.append("transpose=2")
    if project.get("flipH"):
        v.append("hflip")
    if project.get("flipV"):
        v.append("vflip")
    col = project.get("color") or {}
    eq = []
    if num(col.get("brightness"), 0) != 0:
        eq.append(f"brightness={num(col.get('brightness'), 0)}")
    if num(col.get("contrast"), 1) != 1:
        eq.append(f"contrast={num(col.get('contrast'), 1)}")
    if num(col.get("saturation"), 1) != 1:
        eq.append(f"saturation={num(col.get('saturation'), 1)}")
    if num(col.get("gamma"), 1) != 1:
        eq.append(f"gamma={num(col.get('gamma'), 1)}")
    if eq:
        v.append("eq=" + ":".join(eq))
    if num(col.get("hue"), 0) != 0:
        v.append(f"hue=h={num(col.get('hue'), 0)}")
    if num(col.get("sharpen"), 0) > 0:
        v.append(f"unsharp=5:5:{num(col.get('sharpen'), 0):.2f}:5:5:0")
    if num(col.get("vignette"), 0) > 0:
        v.append(f"vignette=angle={num(col.get('vignette'), 0) * 0.8:.3f}")
    vl = "vc"
    if v:
        f.append("[vc]" + ",".join(v) + "[vf]")
        vl = "vf"

    if fit == "blur" and parse_aspect(project.get("aspect")):
        f.append(f"[{vl}]split[fg][bg]")
        f.append(f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=luma_radius=30:luma_power=2[bgb]")
        f.append(f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[fgs]")
        f.append("[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[vs]")
    elif fit == "pad" and parse_aspect(project.get("aspect")):
        f.append(f"[{vl}]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[vs]")
    elif parse_aspect(project.get("aspect")):
        f.append(f"[{vl}]crop=w='min(iw,ih*{A})':h='min(ih,iw/{A})',scale={W}:{H},setsar=1[vs]")
    else:
        f.append(f"[{vl}]scale={W}:{H}:flags=lanczos,setsar=1[vs]")

    post = []
    if fps and not multi:
        post.append(f"fps={fps}")
    for i, t in enumerate(project.get("texts") or []):
        if not t.get("text"):
            continue
        tf = os.path.join(tmp_dir, f"text_{i}.txt")
        with open(tf, "w", encoding="utf-8") as fh:
            fh.write(t["text"])
        size = round(num(t.get("size"), 64) / 1080 * H)
        parts = [f"fontfile={esc(t.get('font') or font)}", f"textfile={esc(tf)}", "expansion=none", f"fontsize={size}",
                 f"fontcolor={esc(t.get('color') or '#ffffff')}", f"x=(w-text_w)*{num(t.get('x'), 0.5)}", f"y=(h-text_h)*{num(t.get('y'), 0.9)}"]
        if t.get("outline"):
            parts += [f"borderw={max(1, round(size / 18))}", "bordercolor=black"]
        if t.get("box"):
            parts += ["box=1", f"boxcolor={esc((t.get('boxColor') or '#000000') + '@' + str(num(t.get('boxOpacity'), 0.5)))}", f"boxborderw={round(size / 4)}"]
        if t.get("start") is not None and t.get("end") is not None:
            st, en = num(t.get("start"), 0), num(t.get("end"), 9999)
            parts.append("enable=" + esc(f"between(t,{st},{en})"))
        post.append("drawtext=" + ":".join(parts))
    fi, fo = num(project.get("fadeIn"), 0), num(project.get("fadeOut"), 0)
    if fi > 0:
        post.append(f"fade=t=in:st=0:d={fi}")
    if fo > 0:
        post.append(f"fade=t=out:st={max(0, duration - fo):.3f}:d={fo}")
    if fmt == "gif":
        post.append(f"fps={min(fps or 20, 20)}")
        f.append("[vs]" + ",".join(post) + ("," if post else "") + "split[g1][g2]")
        f.append("[g1]palettegen=stats_mode=diff[pal]")
        f.append("[g2][pal]paletteuse=dither=bayer:bayer_scale=4[vout]")
    else:
        post.append("format=yuv420p")
        f.append("[vs]" + ",".join(post) + "[vout]")

    if audio_on:
        a = []
        if fi > 0:
            a.append(f"afade=t=in:st=0:d={fi}")
        if fo > 0:
            a.append(f"afade=t=out:st={max(0, duration - fo):.3f}:d={fo}")
        al = "ac"
        music = (project.get("audio") or {}).get("music")
        if music and music.get("path") and music_input is not None:
            delay = round(num(music.get("start"), 0) * 1000)
            f.append(f"[{music_input}:a]aresample=48000,aformat=channel_layouts=stereo,volume={num(music.get('volume'), 0.5)},adelay={delay}|{delay}[mus]")
            f.append("[ac][mus]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[acm]")
            al = "acm"
        if (project.get("audio") or {}).get("normalize"):
            a.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        a.append(f"atrim=end={duration:.3f}")
        f.append(f"[{al}]" + ",".join(a) + "[aout]")
    return f


def pick_encoder(pref, codec):
    hw = ffmpeg.hw_encoders()
    has = lambda v: f"h264_{v}" in hw  # noqa: E731
    vendor = pref
    if pref == "auto":
        vendor = "nvenc" if has("nvenc") else "amf" if has("amf") else "qsv" if has("qsv") else "x264"
    if vendor != "x264" and not has(vendor):
        vendor = "x264"
    names = {"x264": "libx265" if codec == "hevc" else "libx264", "nvenc": f"{'hevc' if codec == 'hevc' else 'h264'}_nvenc",
             "amf": f"{'hevc' if codec == 'hevc' else 'h264'}_amf", "qsv": f"{'hevc' if codec == 'hevc' else 'h264'}_qsv"}
    return vendor, names[vendor]


QUALITY = {"high": 18, "medium": 23, "low": 28}


def video_args(vendor, name, kbps, quality, fmt):
    if fmt == "gif":
        return []
    q = QUALITY.get(quality, 23)
    if fmt == "webm":
        return (["-c:v", "libvpx-vp9", "-b:v", f"{kbps}k"] if kbps else ["-c:v", "libvpx-vp9", "-crf", str(q + 12), "-b:v", "0"]) + ["-deadline", "good", "-cpu-used", "2", "-row-mt", "1"]
    if vendor == "nvenc":
        return ["-c:v", name, "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-multipass", "fullres", "-b:v", f"{kbps}k", "-maxrate", f"{round(kbps * 1.2)}k", "-bufsize", f"{kbps * 2}k"] if kbps \
            else ["-c:v", name, "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", str(q + 1), "-b:v", "0"]
    if vendor == "amf":
        return ["-c:v", name, "-quality", "quality", "-rc", "vbr_peak", "-b:v", f"{kbps}k", "-maxrate", f"{round(kbps * 1.2)}k", "-bufsize", f"{kbps * 2}k"] if kbps \
            else ["-c:v", name, "-quality", "quality", "-rc", "cqp", "-qp_i", str(q), "-qp_p", str(q + 2)]
    if vendor == "qsv":
        return ["-c:v", name, "-preset", "slower", "-b:v", f"{kbps}k", "-maxrate", f"{round(kbps * 1.2)}k", "-bufsize", f"{kbps * 2}k"] if kbps \
            else ["-c:v", name, "-preset", "slower", "-global_quality", str(q)]
    return ["-c:v", name, "-preset", "medium", "-b:v", f"{kbps}k"] if kbps else ["-c:v", name, "-preset", "medium", "-crf", str(q)]


def plan_bitrate(target_mb, duration, geo, fps, audio_kbps, has_audio, auto_downscale):
    total = target_mb * 1e6 * 8 * 0.94 / duration / 1000
    aK = audio_kbps if has_audio else 0
    if has_audio:
        if total < 300:
            aK = min(aK, 48)
        elif total < 800:
            aK = min(aK, 64)
        elif total < 1500:
            aK = min(aK, 96)
    vK = int(total - aK)
    if vK < 50:
        vK = max(30, int(total * 0.85))
        aK = max(16, int(total - vK)) if has_audio else 0
    short_side, out_fps = None, fps
    if auto_downscale:
        thr = 0.05
        cur = min(geo["W"], geo["H"])
        los = max(geo["W"], geo["H"]) / cur
        bpp = lambda s, f: vK * 1000 / (s * s * los * f)  # noqa: E731
        cands = [cur] + [x for x in (2160, 1440, 1080, 900, 720, 540, 480, 360, 240) if x < cur]
        find = lambda f: next((s for s in cands if bpp(s, f) >= thr), cands[-1])  # noqa: E731
        floor = min(720, cur)
        s = find(out_fps)
        for f in sorted({x for x in (out_fps, 60, 30) if x <= out_fps}, reverse=True):
            sf = find(f)
            if sf >= floor:
                s, out_fps = sf, f
                break
            if sf > s:
                s, out_fps = sf, f
        if s < cur:
            short_side = s
    return {"videoKbps": vK, "audioKbps": aK, "shortSide": short_side, "fps": out_fps}


class RenderJob:
    def __init__(self):
        self.current = None
        self.cancelled = False

    def cancel(self):
        self.cancelled = True
        if self.current:
            self.current.cancel()


def render(project, output, job, on_progress=None, font="C:/Windows/Fonts/arialbd.ttf", run_opts=None):
    out = project.get("output") or {}
    fmt = out.get("format") or "mp4"
    infos = [ffmpeg.probe(s) for s in project["sources"]]
    duration = output_duration(project)
    if duration <= 0.05:
        raise RuntimeError("Nothing to render: timeline is empty")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    prog = on_progress or (lambda p, i: None)

    if out.get("mode") == "copy" and len(project["sources"]) == 1 and len(project["segments"]) == 1:
        g = project["segments"][0]
        prog(0.1, {"phase": "Trimming (lossless)"})
        ffmpeg.fast_cut(project["sources"][0], output, g["start"], g["end"])
        prog(1.0, {"phase": "Done"})
        return {"output": output, "size": os.path.getsize(output), "attempts": 1}

    tmp = tempfile.mkdtemp(prefix="mt-render-")
    try:
        src_fps = infos[0]["fps"] or 60
        fps = min(float(out["fps"]), src_fps) if out.get("fps") not in (None, "", "source") else None
        tracks = (project.get("audio") or {}).get("tracks") or []
        music = (project.get("audio") or {}).get("music")
        has_audio = fmt != "gif" and any(i["audioTracks"] for i in infos) and not (
            tracks and all(t.get("muted") for t in tracks) and len(project["sources"]) == 1 and not (music and music.get("path")))
        geo = geometry(project, infos[0])
        if fmt in ("mp4", "mkv"):
            vendor, name = pick_encoder(out.get("encoder") or "auto", out.get("codec") or "h264")
        else:
            vendor, name = "x264", "libx264"
        target = num(out.get("targetMB"), 0)
        plan = None
        if target > 0 and fmt != "gif":
            plan = plan_bitrate(target, duration, geo, fps or src_fps, num(out.get("audioKbps"), 128), has_audio, out.get("autoDownscale", True))
            if plan["shortSide"]:
                geo = geometry(project, infos[0], plan["shortSide"])
            if plan["fps"] != (fps or src_fps):
                fps = plan["fps"]
        base = (even(infos[0]["width"]), even(infos[0]["height"]))
        inputs = []
        for s in project["sources"]:
            inputs += ["-i", s]
        music_input = None
        if music and music.get("path") and has_audio:
            if music.get("loop"):
                inputs += ["-stream_loop", "-1"]
            inputs += ["-i", music["path"]]
            music_input = len(project["sources"])

        def build(kbps, pss=None, passlog=None):
            filters = build_filters(project, infos, geo, fps, has_audio and pss != 1, duration, tmp, font, music_input, fmt, base)
            script = os.path.join(tmp, f"graph_{pss or 0}.txt")
            with open(script, "w", encoding="utf-8") as fh:
                fh.write(";\n".join(filters))
            args = inputs + ["-/filter_complex", script, "-map", "[vout]"]
            if has_audio and pss != 1:
                args += ["-map", "[aout]"]
            args += video_args(vendor, name, kbps, out.get("quality") or "medium", fmt)
            if pss:
                args += ["-pass", str(pss), "-passlogfile", passlog]
            if fmt == "gif":
                args += ["-loop", "0"]
            elif pss == 1:
                return args + ["-an", "-f", "null", "NUL"]
            elif has_audio:
                aK = plan["audioKbps"] if plan else int(num(out.get("audioKbps"), 160))
                args += ["-c:a", "libopus", "-b:a", f"{aK}k"] if fmt == "webm" else ["-c:a", "aac", "-b:a", f"{aK}k", "-ac", "2"]
            if fmt == "mp4":
                args += ["-movflags", "+faststart"]
            return args + ["-t", f"{duration:.3f}", output]

        def step(args, phase, frm, span):
            if job.cancelled:
                raise RuntimeError("Cancelled")
            ro = run_opts() if run_opts else {}
            job.current = ffmpeg.Run(args, duration=duration, cwd=tmp,
                                     on_progress=lambda p, i: prog(None if p is None else frm + p * span, {"phase": phase, **i}), **ro)
            job.current.wait()

        two_pass = plan and out.get("twoPass", True) and (name == "libx264" or fmt == "webm")
        kbps = plan["videoKbps"] if plan else None
        for attempt in range(1, (4 if plan else 1) + 1):
            label = f" (retry {attempt - 1}, {kbps} kbps)" if attempt > 1 else ""
            if two_pass:
                log = os.path.join(tmp, "pass")
                step(build(kbps, 1, log), f"Analyzing (pass 1/2){label}", 0, 0.4)
                step(build(kbps, 2, log), f"Encoding (pass 2/2){label}", 0.4, 0.6)
            else:
                step(build(kbps), f"Encoding{label}", 0, 1)
            size = os.path.getsize(output)
            if not plan or size <= target * 1e6:
                prog(1.0, {"phase": "Done"})
                return {"output": output, "size": size, "attempts": attempt, "videoKbps": kbps, "W": geo["W"], "H": geo["H"], "encoder": name}
            kbps = max(30, int(kbps * (target * 1e6 / size) * 0.93))
        raise RuntimeError(f"Could not get under {target:g} MB (got {os.path.getsize(output) / 1e6:.1f} MB). Try a shorter clip or lower resolution.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def estimate(project):
    infos = [ffmpeg.probe(s) for s in project["sources"]]
    duration = output_duration(project)
    out = project.get("output") or {}
    geo = geometry(project, infos[0])
    src_fps = infos[0]["fps"] or 60
    fps = min(float(out["fps"]), src_fps) if out.get("fps") not in (None, "", "source") else src_fps
    if not out.get("targetMB"):
        return {"duration": duration, "W": geo["W"], "H": geo["H"], "fps": fps}
    plan = plan_bitrate(float(out["targetMB"]), duration, geo, fps, num(out.get("audioKbps"), 128), any(i["audioTracks"] for i in infos), out.get("autoDownscale", True))
    if plan["shortSide"]:
        geo = geometry(project, infos[0], plan["shortSide"])
    return {"duration": duration, "W": geo["W"], "H": geo["H"], "fps": plan["fps"], "videoKbps": plan["videoKbps"], "audioKbps": plan["audioKbps"]}

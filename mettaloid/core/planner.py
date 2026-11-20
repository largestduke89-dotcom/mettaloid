"""Quality planner: best resolution / fps / bitrate that fits a replay RAM budget.

The replay buffer stores encoded packets, so RAM = (video + audio kbps) x seconds.
CBR makes that deterministic.
"""
import math

import psutil

MiB = 1024 * 1024
HEADROOM = 0.9
CODEC_EFF = {"h264": 1.0, "hevc": 0.65, "av1": 0.55}
BPP_GOOD = 0.075
BPP_CEILING = 0.2
HARD_CAP = {"h264": 130000, "hevc": 130000, "av1": 100000}
MIN_VIDEO = 800
HEIGHTS = [2160, 1440, 1080, 900, 720, 540, 480, 360]
AUDIO_STEPS = [64, 96, 128, 160, 192, 256, 320]


def auto_memory_mb(total=None):
    total = total or psutil.virtual_memory().total
    mb = total / MiB * 0.12
    return int(max(512, min(6144, round(mb / 256) * 256)))


def size_for(native, h):
    if not h or h >= native[1]:
        return native
    w = round(native[0] * h / native[1])
    w += w % 4 and (4 - w % 4)  # OBS prefers multiples of 4
    return (int(w), int(h))


def label(bpp):
    if bpp >= 0.15:
        return "Excellent"
    if bpp >= 0.105:
        return "Great"
    if bpp >= 0.075:
        return "Good"
    if bpp >= 0.05:
        return "Fair"
    return "Low"


def kbps_to_mb(kbps, seconds):
    return kbps * 1000 / 8 * seconds / MiB


def plan_quality(memory_mb, seconds, native, max_resolution, max_fps, codec, audio_tracks, audio_kbps, auto, manual=None):
    memory_mb = memory_mb if memory_mb and memory_mb > 0 else auto_memory_mb()
    seconds = max(5, seconds)
    codec = codec if codec in CODEC_EFF else "h264"
    eff = CODEC_EFF[codec]
    n = max(1, audio_tracks)
    budget = int(memory_mb * MiB * 8 * HEADROOM / seconds / 1000)
    notes = []

    if not auto:
        m = manual or {}
        res = m.get("resolution", "native")
        w, h = size_for(native, None if res == "native" else int(res))
        fps = int(m.get("fps", 60))
        variable = m.get("rateControl") == "CQP"
        video = None if variable else int(m.get("bitrateKbps", 30000))
        total = None if variable else video + audio_kbps * n
        need = None if variable else kbps_to_mb(total, seconds)
        fits = None if variable else int(memory_mb * MiB * HEADROOM * 8 / (total * 1000))
        if variable:
            notes.append("Constant-quality (CQP) bitrate varies, so replay memory use is unpredictable. Use CBR to stay within the limit.")
        elif fits < seconds:
            notes.append(f"These settings need {round(need)} MB; with a {memory_mb} MB limit only the last {fits}s will be kept.")
        bpp = video * 1000 / (w * h * fps) / eff if video else None
        return dict(auto=False, memoryMB=memory_mb, seconds=seconds, codec=codec, width=w, height=h, fps=fps, videoKbps=video,
                    audioKbps=audio_kbps, audioTracks=n, totalKbps=total, estMemoryMB=need, fitsSeconds=fits,
                    quality=label(bpp) if bpp else "Variable", notes=notes, budgetKbps=budget, limitedBy="manual")

    a = audio_kbps or 160
    while a > 64 and a * n > budget * 0.12:
        i = AUDIO_STEPS.index(a) if a in AUDIO_STEPS else 1
        a = AUDIO_STEPS[max(0, i - 1)]
    vbudget = budget - a * n
    if vbudget < MIN_VIDEO:
        notes.append("Memory limit is very low for this replay length — quality will be poor. Raise the limit or shorten the replay.")
        vbudget = max(300, vbudget)

    max_h = native[1] if max_resolution in (None, "", "native") else min(int(max_resolution), native[1])
    heights = [max_h] + [x for x in HEIGHTS if x < max_h]
    max_fps = max(24, int(max_fps or 60))
    fps_list = sorted({f for f in (max_fps, 144, 120, 60, 30) if f <= max_fps}, reverse=True)
    best = None
    for hh in heights:
        w, h = size_for(native, hh)
        for f in fps_list:
            bpp = vbudget * 1000 / (w * h * f) / eff
            if bpp < BPP_GOOD:
                continue
            u = math.log2(h) + 0.7 * math.log2(f)
            if not best or u > best[3]:
                best = (w, h, f, u)
    if not best:
        w, h = size_for(native, min(max_h, 480))
        best = (w, h, min(30, max_fps), 0)
    w, h, f, _ = best
    pix = w * h * f
    ceiling = min(HARD_CAP[codec], round(BPP_CEILING * eff * pix / 1000))
    video = min(vbudget, ceiling)
    limited = "ceiling" if video == ceiling else "memory"
    video = max(300, video // 100 * 100)
    bpp = video * 1000 / pix / eff
    total = video + a * n
    if codec == "h264" and bpp < 0.11 and limited == "memory":
        notes.append("HEVC would give noticeably better quality at this memory limit (Quality → Codec).")
    if limited == "ceiling":
        notes.append("Your memory limit has room to spare — quality is already at the useful maximum for this resolution/frame rate.")
    return dict(auto=True, memoryMB=memory_mb, seconds=seconds, codec=codec, width=w, height=h, fps=f, videoKbps=video,
                audioKbps=a, audioTracks=n, totalKbps=total, estMemoryMB=kbps_to_mb(total, seconds),
                fitsSeconds=int(memory_mb * MiB * HEADROOM * 8 / (total * 1000)), quality=label(bpp), limitedBy=limited,
                notes=notes, budgetKbps=budget)

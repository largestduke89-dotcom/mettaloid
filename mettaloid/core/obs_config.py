"""Writes OBS portable-mode config (global/user ini, profile, encoder json, websocket)."""
import json
import os
import re

PROFILE = "Mettaloid"
PROFILE_B = "Mettaloid B"
COLLECTION = "Mettaloid"

PRESETS = {
    "nvenc": {
        "quality": {"preset2": "p6", "tune": "hq", "multipass": "qres", "bf": 2, "psycho_aq": True, "lookahead": False},
        "balanced": {"preset2": "p4", "tune": "hq", "multipass": "disabled", "bf": 2, "psycho_aq": True, "lookahead": False},
        "performance": {"preset2": "p2", "tune": "hq", "multipass": "disabled", "bf": 0, "psycho_aq": False, "lookahead": False},
        "max-performance": {"preset2": "p1", "tune": "ll", "multipass": "disabled", "bf": 0, "psycho_aq": False, "lookahead": False},
    },
    "amf": {"quality": {"preset": "quality"}, "balanced": {"preset": "balanced"}, "performance": {"preset": "speed"}, "max-performance": {"preset": "speed", "bf": 0}},
    "qsv": {"quality": {"target_usage": "TU2"}, "balanced": {"target_usage": "TU4"}, "performance": {"target_usage": "TU6"}, "max-performance": {"target_usage": "TU7"}},
    "x264": {"quality": {"preset": "faster"}, "balanced": {"preset": "veryfast"}, "performance": {"preset": "superfast"}, "max-performance": {"preset": "ultrafast", "tune": "zerolatency"}},
}


def major(v):
    m = re.match(r"(\d+)", str(v or ""))
    return int(m.group(1)) if m else 31


def encoder_id(vendor, codec, obs_version):
    v = major(obs_version)
    t = {
        "nvenc": {"h264": "obs_nvenc_h264_tex" if v >= 31 else "jim_nvenc", "hevc": "obs_nvenc_hevc_tex" if v >= 31 else "jim_hevc_nvenc", "av1": "obs_nvenc_av1_tex" if v >= 31 else "jim_av1_nvenc"},
        "amf": {"h264": "h264_texture_amf", "hevc": "h265_texture_amf", "av1": "av1_texture_amf"},
        "qsv": {"h264": "obs_qsv11_v2", "hevc": "obs_qsv11_hevc", "av1": "obs_qsv11_av1"},
    }
    return t.get(vendor, {}).get(codec, "obs_x264")


def encoder_settings(vendor, q, preset="balanced"):
    kbps = max(300, int(round(q["bitrateKbps"])))
    rc = q["rateControl"]
    keyint = q.get("keyframeSec") or 1
    pre = dict((PRESETS.get(vendor) or PRESETS["x264"]).get(preset) or PRESETS[vendor if vendor in PRESETS else "x264"]["balanced"])
    if rc == "CQP":
        if vendor == "x264":
            return {**pre, "rate_control": "CRF", "crf": q["cqLevel"], "keyint_sec": keyint}
        return {**pre, "rate_control": "CQP", "cqp": q["cqLevel"], "keyint_sec": keyint}
    out = {**pre, "rate_control": rc, "bitrate": kbps, "keyint_sec": keyint}
    if rc == "VBR" and vendor != "x264":
        out["max_bitrate"] = round(kbps * 1.5)
    return out


def output_size(base, resolution):
    if resolution in (None, "", "native"):
        return base
    h = int(resolution)
    if h >= base[1]:
        return base
    w = round(base[0] * h / base[1])
    return (w + (w % 2), h)


def track_mask(audio):
    return 1 | 2 | 4 | (8 if audio["separateDiscordTrack"] else 0)


def ini(sections):
    out = []
    for name, vals in sections.items():
        out.append(f"[{name}]")
        for k, v in vals.items():
            if v is None:
                continue
            if isinstance(v, bool):
                v = "true" if v else "false"
            out.append(f"{k}={v}")
        out.append("")
    return "﻿" + "\r\n".join(out)


def write_obs_config(config_root, settings, profile_name, staging_dir, base_size, vendor, obs_version, ws_port, ws_password, plan):
    s = settings
    rec, q, audio, perf = s["recording"], s["quality"], s["audio"], s["performance"]
    os.makedirs(config_root, exist_ok=True)
    os.makedirs(staging_dir, exist_ok=True)
    glob = ini({
        "General": {"FirstRun": True, "ProcessPriority": perf["obsPriority"], "MaxLogs": 3, "LastVersion": 4294967295, "EnableAutoUpdates": False,
                    "ConfirmOnExit": False, "WarnBeforeStartingRecord": False, "WarnBeforeStoppingRecord": False,
                    "Pre19Defaults": False, "Pre21Defaults": False, "Pre23Defaults": False},
        "Video": {"Renderer": "Direct3D 11"},
        "Audio": {"DisableAudioDucking": True},
        "BasicWindow": {"SysTrayEnabled": True, "SysTrayWhenStarted": True, "SysTrayMinimizeToTray": True, "ShowTransitions": False, "PreviewEnabled": False},
        "Basic": {"Profile": profile_name, "ProfileDir": profile_name, "SceneCollection": COLLECTION, "SceneCollectionFile": COLLECTION, "ConfigOnNewProfile": False},
        "OBSWebSocket": {"FirstLoad": False, "ServerEnabled": True, "ServerPort": ws_port, "AlertsEnabled": False, "AuthRequired": True, "ServerPassword": ws_password},
    })
    for name in ("global.ini", "user.ini"):
        with open(os.path.join(config_root, name), "w", encoding="utf-8") as f:
            f.write(glob)
    wsd = os.path.join(config_root, "plugin_config", "obs-websocket")
    os.makedirs(wsd, exist_ok=True)
    with open(os.path.join(wsd, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"first_load": False, "server_enabled": True, "server_port": ws_port, "alerts_enabled": False,
                   "auth_required": True, "server_password": ws_password}, f)

    if plan["auto"]:
        out, fps, akbps = (plan["width"], plan["height"]), plan["fps"], plan["audioKbps"]
        encq = {**q, "bitrateKbps": plan["videoKbps"], "rateControl": "CBR"}
    else:
        # manual quality: the planner already resolved resolution/fps (incl. per-game overrides)
        out, fps, akbps, encq = (plan["width"], plan["height"]), plan["fps"], audio["audioBitrate"], q
    enc = encoder_id(vendor, q["codec"], obs_version)
    fmt = rec["fileFormat"] if not (rec["fileFormat"] == "hybrid_mp4" and major(obs_version) < 30) else "mp4"
    pdir = os.path.join(config_root, "basic", "profiles", profile_name)
    os.makedirs(pdir, exist_ok=True)
    staging = staging_dir.replace("\\", "/")
    basic = ini({
        "General": {"Name": profile_name},
        "Output": {"Mode": "Advanced", "RecType": "Standard"},
        "AdvOut": {
            "RecType": "Standard", "RecFilePath": staging, "RecFormat": "mp4" if fmt == "hybrid_mp4" else fmt, "RecFormat2": fmt,
            "RecEncoder": enc, "RecUseRescale": False, "RecTracks": track_mask(audio), "RecAudioEncoder": "ffmpeg_aac",
            "RecRB": True, "RecRBTime": int(plan["seconds"]), "RecRBSize": int(plan["memoryMB"]), "RecRBPrefix": "Replay", "RecSplitFile": False,
            "Track1Bitrate": akbps, "Track2Bitrate": akbps, "Track3Bitrate": akbps, "Track4Bitrate": akbps,
            "Track1Name": "Mix", "Track2Name": "Game" if audio["gameAudioOnly"] else ("Game & apps" if audio["separateDiscordTrack"] else "Desktop"), "Track3Name": "Microphone", "Track4Name": "Discord",
        },
        "SimpleOutput": {"FilePath": staging, "RecRB": True, "RecRBTime": int(plan["seconds"])},
        "Video": {"BaseCX": base_size[0], "BaseCY": base_size[1], "OutputCX": out[0], "OutputCY": out[1], "FPSType": 1, "FPSInt": fps, "FPSCommon": fps,
                  "ScaleType": "bicubic", "ColorFormat": "NV12", "ColorSpace": "709", "ColorRange": "Partial"},
        "Audio": {"SampleRate": 48000, "ChannelSetup": "Stereo"},
    })
    with open(os.path.join(pdir, "basic.ini"), "w", encoding="utf-8") as f:
        f.write(basic)
    with open(os.path.join(pdir, "recordEncoder.json"), "w", encoding="utf-8") as f:
        json.dump(encoder_settings(vendor, encq, perf["encoderPreset"]), f)
    os.makedirs(os.path.join(config_root, "basic", "scenes"), exist_ok=True)
    return {"encoderId": enc, "outputSize": out, "fps": fps}

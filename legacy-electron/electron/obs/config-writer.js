// Writes OBS Studio portable-mode configuration (global/user ini, profile,
// encoder JSON, websocket config) generated from Mettaloid settings.
// Mettaloid is the source of truth: these files are regenerated before every
// OBS launch, so wiping the OBS folder (e.g. during an update) is harmless.
const fs = require('fs');
const path = require('path');

const PROFILE = 'Mettaloid';
const COLLECTION = 'Mettaloid';

function ini(sections) {
  let out = '';
  for (const [name, values] of Object.entries(sections)) {
    out += `[${name}]\r\n`;
    for (const [k, v] of Object.entries(values)) {
      if (v === undefined || v === null) continue;
      out += `${k}=${v}\r\n`;
    }
    out += '\r\n';
  }
  // OBS reads ini files as UTF-8; write a BOM like OBS does.
  return '﻿' + out;
}

function majorVersion(v) {
  const m = /^(\d+)/.exec(String(v || ''));
  return m ? parseInt(m[1], 10) : 31;
}

// Map (vendor, codec, obsVersion) -> OBS encoder id
function encoderId(vendor, codec, obsVersion) {
  const v = majorVersion(obsVersion);
  const table = {
    nvenc: {
      h264: v >= 31 ? 'obs_nvenc_h264_tex' : 'jim_nvenc',
      hevc: v >= 31 ? 'obs_nvenc_hevc_tex' : 'jim_hevc_nvenc',
      av1: v >= 31 ? 'obs_nvenc_av1_tex' : 'jim_av1_nvenc'
    },
    amf: { h264: 'h264_texture_amf', hevc: 'h265_texture_amf', av1: 'av1_texture_amf' },
    qsv: { h264: 'obs_qsv11_v2', hevc: 'obs_qsv11_hevc', av1: 'obs_qsv11_av1' },
    x264: { h264: 'obs_x264', hevc: 'obs_x264', av1: 'obs_x264' }
  };
  return (table[vendor] || table.x264)[codec] || table.x264.h264;
}

// Encoder speed presets. Faster presets cost a little quality per bit but use
// less GPU/CPU time, leaving more headroom for the game.
const PRESETS = {
  nvenc: {
    quality: { preset2: 'p6', tune: 'hq', multipass: 'qres', bf: 2, psycho_aq: true, lookahead: false },
    balanced: { preset2: 'p4', tune: 'hq', multipass: 'disabled', bf: 2, psycho_aq: true, lookahead: false },
    performance: { preset2: 'p2', tune: 'hq', multipass: 'disabled', bf: 0, psycho_aq: false, lookahead: false },
    'max-performance': { preset2: 'p1', tune: 'll', multipass: 'disabled', bf: 0, psycho_aq: false, lookahead: false }
  },
  amf: {
    quality: { preset: 'quality' }, balanced: { preset: 'balanced' }, performance: { preset: 'speed' }, 'max-performance': { preset: 'speed', bf: 0 }
  },
  qsv: {
    quality: { target_usage: 'TU2' }, balanced: { target_usage: 'TU4' }, performance: { target_usage: 'TU6' }, 'max-performance': { target_usage: 'TU7' }
  },
  x264: {
    quality: { preset: 'faster' }, balanced: { preset: 'veryfast' }, performance: { preset: 'superfast' }, 'max-performance': { preset: 'ultrafast', tune: 'zerolatency' }
  }
};

function encoderSettings(vendor, q, presetName = 'balanced') {
  const kbps = Math.max(300, Math.round(q.bitrateKbps));
  const rc = q.rateControl;
  const keyint = q.keyframeSec || 1;
  const table = PRESETS[vendor] || PRESETS.x264;
  const pre = table[presetName] || table.balanced;
  if (rc === 'CQP') {
    if (vendor === 'x264') return { ...pre, rate_control: 'CRF', crf: q.cqLevel, keyint_sec: keyint };
    return { ...pre, rate_control: 'CQP', cqp: q.cqLevel, keyint_sec: keyint };
  }
  const out = { ...pre, rate_control: rc, bitrate: kbps, keyint_sec: keyint };
  if (rc === 'VBR' && vendor !== 'x264') out.max_bitrate = Math.round(kbps * 1.5);
  return out;
}

function outputSize(base, resolution) {
  if (resolution === 'native' || !resolution) return base;
  const h = parseInt(resolution, 10);
  if (!h || h >= base.height) return base;
  let w = Math.round((base.width * h) / base.height);
  if (w % 2) w += 1;
  return { width: w, height: h };
}

function trackMask(audio) {
  // 1 = mix, 2 = game/desktop, 3 = mic, 4 = discord
  return 1 | 2 | 4 | (audio.separateDiscordTrack ? 8 : 0);
}

/**
 * @param {object} o
 * @param {string} o.configRoot   <obs root>/config/obs-studio
 * @param {object} o.settings     Mettaloid settings
 * @param {string} o.stagingDir   where OBS writes raw files
 * @param {{width:number,height:number}} o.baseSize canvas size
 * @param {string} o.vendor       nvenc|amf|qsv|x264
 * @param {string} o.obsVersion
 * @param {number} o.wsPort
 * @param {string} o.wsPassword
 */
function writeObsConfig(o) {
  const { configRoot, settings, stagingDir, baseSize, vendor, obsVersion, wsPort, wsPassword } = o;
  const profileName = o.profileName || PROFILE;
  const plan = o.plan || null;
  const perf = settings.performance || {};
  const rec = settings.recording;
  const q = settings.quality;
  const audio = settings.audio;
  fs.mkdirSync(configRoot, { recursive: true });
  fs.mkdirSync(stagingDir, { recursive: true });

  const ws = {
    FirstLoad: 'false', ServerEnabled: 'true', ServerPort: wsPort,
    AlertsEnabled: 'false', AuthRequired: 'true', ServerPassword: wsPassword
  };
  const globalIni = ini({
    General: {
      FirstRun: 'true',
      ProcessPriority: perf.obsPriority || 'AboveNormal',
      MaxLogs: 3,
      LastVersion: 4294967295,
      EnableAutoUpdates: 'false',
      ConfirmOnExit: 'false',
      WarnBeforeStartingRecord: 'false',
      WarnBeforeStoppingRecord: 'false',
      Pre19Defaults: 'false',
      Pre21Defaults: 'false',
      Pre23Defaults: 'false'
    },
    BasicWindow: {
      SysTrayEnabled: 'true',
      SysTrayWhenStarted: 'true',
      SysTrayMinimizeToTray: 'true',
      ShowTransitions: 'false',
      PreviewEnabled: 'false',
      RecordWhenStreaming: 'false',
      KeepRecordingWhenStreamStops: 'false',
      ReplayBufferWhileStreaming: 'false'
    },
    Video: { Renderer: 'Direct3D 11' },
    Audio: { DisableAudioDucking: 'true' },
    Basic: {
      Profile: profileName,
      ProfileDir: profileName,
      SceneCollection: COLLECTION,
      SceneCollectionFile: COLLECTION,
      ConfigOnNewProfile: 'false'
    },
    OBSWebSocket: ws
  });
  // OBS < 31 uses global.ini for everything, 31+ splits user settings into user.ini.
  fs.writeFileSync(path.join(configRoot, 'global.ini'), globalIni);
  fs.writeFileSync(path.join(configRoot, 'user.ini'), globalIni);

  // obs-websocket 5.3+ (OBS 30+) json config
  const wsDir = path.join(configRoot, 'plugin_config', 'obs-websocket');
  fs.mkdirSync(wsDir, { recursive: true });
  fs.writeFileSync(
    path.join(wsDir, 'config.json'),
    JSON.stringify({
      first_load: false,
      server_enabled: true,
      server_port: wsPort,
      alerts_enabled: false,
      auth_required: true,
      server_password: wsPassword
    }, null, 2)
  );

  // Auto-quality plan (from the memory budget) overrides resolution/fps/bitrate.
  const out = plan ? { width: plan.width, height: plan.height } : outputSize(baseSize, q.resolution);
  const fps = plan ? plan.fps : q.fps;
  const audioKbps = plan ? plan.audioKbps : audio.audioBitrate;
  const encQ = plan ? { ...q, bitrateKbps: plan.videoKbps, rateControl: 'CBR' } : q;
  const encId = encoderId(vendor, q.codec, obsVersion);
  const recFormat = rec.fileFormat === 'hybrid_mp4' && majorVersion(obsVersion) >= 30 ? 'hybrid_mp4' : rec.fileFormat || 'mp4';
  const profileDir = path.join(configRoot, 'basic', 'profiles', profileName);
  fs.mkdirSync(profileDir, { recursive: true });
  const replaySeconds = Math.max(5, Math.round(o.replaySeconds || rec.replaySeconds));
  const basic = ini({
    General: { Name: profileName },
    Output: { Mode: 'Advanced', RecType: 'Standard' },
    AdvOut: {
      RecType: 'Standard',
      RecFilePath: stagingDir.replace(/\\/g, '/'),
      RecFormat: recFormat === 'hybrid_mp4' ? 'mp4' : recFormat,
      RecFormat2: recFormat,
      RecEncoder: encId,
      RecUseRescale: 'false',
      RecTracks: trackMask(audio),
      RecAudioEncoder: 'ffmpeg_aac',
      RecRB: 'true',
      RecRBTime: replaySeconds,
      RecRBSize: Math.round(o.memoryMB || settings.recording.replayMaxMemoryMB || 2048),
      RecRBPrefix: 'Replay',
      RecSplitFile: 'false',
      Track1Bitrate: audioKbps,
      Track2Bitrate: audioKbps,
      Track3Bitrate: audioKbps,
      Track4Bitrate: audioKbps,
      Track1Name: 'Mix',
      Track2Name: audio.gameAudioOnly ? 'Game' : 'Desktop',
      Track3Name: 'Microphone',
      Track4Name: 'Discord'
    },
    SimpleOutput: {
      FilePath: stagingDir.replace(/\\/g, '/'),
      RecRB: 'true',
      RecRBTime: replaySeconds
    },
    Video: {
      BaseCX: baseSize.width,
      BaseCY: baseSize.height,
      OutputCX: out.width,
      OutputCY: out.height,
      FPSType: 1,
      FPSInt: fps,
      FPSCommon: fps,
      ScaleType: 'bicubic',
      ColorFormat: 'NV12',
      ColorSpace: '709',
      ColorRange: 'Partial'
    },
    Audio: {
      SampleRate: 48000,
      ChannelSetup: 'Stereo'
    },
    Hotkeys: {}
  });
  fs.writeFileSync(path.join(profileDir, 'basic.ini'), basic);
  fs.writeFileSync(path.join(profileDir, 'recordEncoder.json'), JSON.stringify(encoderSettings(vendor, encQ, perf.encoderPreset)));

  // Make sure scene collection dir exists. OBS will create a default collection
  // (with Desktop Audio + Mic/Aux global sources) if the file is missing; the
  // manager then builds our scene over websocket.
  fs.mkdirSync(path.join(configRoot, 'basic', 'scenes'), { recursive: true });

  return { encoderId: encId, outputSize: out, fps, recFormat, profileName };
}

module.exports = { writeObsConfig, encoderId, encoderSettings, outputSize, majorVersion, trackMask, PROFILE, COLLECTION, PRESETS };

// Persistent settings store (JSON file in userData). Deep-merges with defaults so
// new settings added in updates get sensible values automatically.
const fs = require('fs');
const path = require('path');
const EventEmitter = require('events');
const { app } = require('electron');

const DEFAULTS = {
  firstRun: true,
  general: {
    launchOnStartup: true,
    startMinimized: false,
    closeToTray: true,
    clipFolder: '', // filled at runtime -> Videos/Mettaloid
    theme: 'dark'
  },
  recording: {
    replayEnabled: true,
    replaySeconds: 60, // adjustable replay length (5 s .. 20 min)
    replayMaxMemoryMB: 2048,
    recordDesktopWhenNoGame: false,
    autoRecordSessions: false, // full-session recording on game launch
    captureMode: 'game', // 'game' | 'desktop'
    monitorIndex: 0,
    fileFormat: 'mp4', // mp4 | mkv | hybrid_mp4 (OBS 30.2+)
    filenamePattern: '%CCYY-%MM-%DD %hh-%mm-%ss'
  },
  performance: {
    replayMemoryMB: 0, // RAM budget for the replay buffer (0 = auto: ~12% of system RAM)
    autoQuality: true, // derive resolution / fps / bitrate from the memory budget
    maxResolution: 'native', // ceilings used by auto quality
    maxFps: 60,
    encoderPreset: 'balanced', // quality | balanced | performance | max-performance
    obsPriority: 'AboveNormal', // recorder process priority
    adaptive: true, // step encoder preset down automatically if it drops frames
    freeUiWhenHidden: true, // unload the UI while in the tray to save RAM
    lowPriorityJobs: true, // exports/compression run at low priority & fewer threads while gaming
    detectionInterval: 3 // seconds between game scans
  },
  quality: {
    resolution: 'native', // native | 2160 | 1440 | 1080 | 720 | 480
    fps: 60,
    bitrateKbps: 30000,
    rateControl: 'CBR', // CBR | VBR | CQP
    cqLevel: 20,
    codec: 'h264', // h264 | hevc | av1
    encoder: 'auto', // auto | nvenc | amf | qsv | x264
    keyframeSec: 1
  },
  audio: {
    desktopDevice: 'default',
    micDevice: 'default',
    micEnabled: true,
    micVolume: 1.0,
    desktopVolume: 1.0,
    gameAudioOnly: false, // capture only the game's process audio instead of all desktop audio
    separateDiscordTrack: true,
    noiseSuppression: true,
    audioBitrate: 192
  },
  hotkeys: {
    // Each clip hotkey saves the last N seconds. Accelerator syntax (Electron).
    clips: [
      { accelerator: 'F8', seconds: 60, label: 'Save clip' },
      { accelerator: 'Shift+F8', seconds: 15, label: 'Quick clip' }
    ],
    toggleRecording: 'F9',
    bookmark: 'F10',
    screenshot: 'F11',
    toggleMic: 'Alt+M',
    toggleReplay: ''
  },
  autoClip: {
    // Generic auto clipping: OBS can't read game state, so we expose an API
    // (local HTTP endpoint) that games/mods/scripts can call to trigger clips.
    enabled: true,
    apiPort: 37737,
    secondsBefore: 15,
    secondsAfter: 5
  },
  overlay: {
    enabled: true,
    position: 'top-right',
    sound: true,
    soundVolume: 0.6
  },
  storage: {
    autoCleanup: false,
    maxGB: 100,
    keepFavorites: true
  },
  editor: {
    defaultExportPreset: 'source',
    fontPath: 'C:/Windows/Fonts/arialbd.ttf'
  },
  compressor: {
    defaultTargetMB: 50,
    encoder: 'auto', // auto | x264 | nvenc | amf | qsv  (x264 two-pass is the most accurate)
    twoPass: true,
    autoDownscale: true,
    audioKbps: 128
  },
  updates: {
    autoCheck: true,
    autoDownload: true,
    channel: 'latest'
  },
  games: {
    // per-game profile overrides keyed by game id
    profiles: {},
    custom: [], // { id, name, exe }
    disabled: [] // game ids to ignore
  },
  findFrame: {
    // matching
    method: 'auto', // auto | features (FindFrame ORB) | template (pixel match, best for HUD icons)
    threshold: 30, // features: % of reference keypoints that must match
    templateThreshold: 0.8, // template: correlation 0..1
    nfeatures: 1000, // ORB keypoints per reference
    ratio: 0.9, // Lowe ratio test (FindFrame default)
    matcher: 'bf', // bf (brute force) | flann (LSH)
    verifyGeometry: true, // RANSAC homography check rejects false feature matches
    minInliers: 8,
    minMatches: 10,
    autoMinKeypoints: 60, // auto: fewer keypoints than this → template matching
    searchArea: 'anywhere', // anywhere | near (only around where the box was drawn)
    nearMargin: 0.15,
    scales: '0.8,0.9,1,1.1,1.25', // template sizes to try
    // preprocessing
    boostContrast: false,
    contrast: 2,
    equalize: false,
    edges: false,
    blur: 0,
    // scanning
    scanFps: 5, // frames per second analysed (0 = every frame)
    scanWidth: 960, // frames are downscaled to this width
    hwDecode: 'auto',
    minGapSec: 2, // hits closer than this merge into one match
    maxResultsPerClip: 50,
    stopAfterFirst: false,
    workers: 2, // engine processes scanning in parallel
    threadsPerWorker: 2,
    lowPriority: true,
    pauseWhileGaming: false,
    skipScanned: true, // library scans skip clips already scanned by a finder
    // results
    thumbnails: true,
    autoScanNewClips: true,
    autoTag: true, // tag clips with the finder name
    addBookmarks: true, // add a bookmark at each match (shows on the editor timeline)
    padBefore: 5, // seconds kept before/after a match for "Clip it" and montages
    padAfter: 3
  },
  advanced: {
    obsPath: '' // custom OBS Studio folder (empty = bundled)
  },
  watchFolders: []
};

function deepMerge(base, over) {
  if (Array.isArray(base) || typeof base !== 'object' || base === null) {
    return over === undefined ? base : over;
  }
  const out = { ...base };
  for (const k of Object.keys(over || {})) {
    if (base[k] && typeof base[k] === 'object' && !Array.isArray(base[k])) {
      out[k] = deepMerge(base[k], over[k]);
    } else {
      out[k] = over[k];
    }
  }
  return out;
}

class Settings extends EventEmitter {
  constructor() {
    super();
    this.file = path.join(app.getPath('userData'), 'settings.json');
    let saved = {};
    try {
      saved = JSON.parse(fs.readFileSync(this.file, 'utf8'));
    } catch {}
    this.data = deepMerge(DEFAULTS, saved);
    if (!this.data.general.clipFolder) {
      this.data.general.clipFolder = path.join(app.getPath('videos'), 'Mettaloid');
    }
    fs.mkdirSync(this.data.general.clipFolder, { recursive: true });
    this.save();
  }
  get() {
    return this.data;
  }
  save() {
    const tmp = this.file + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(this.data, null, 2));
    fs.renameSync(tmp, this.file);
  }
  // patch: partial object deep-merged into settings
  update(patch) {
    const before = this.data;
    this.data = deepMerge(this.data, patch);
    this.save();
    this.emit('change', this.data, before, patch);
    return this.data;
  }
  defaults() {
    return DEFAULTS;
  }
}

module.exports = { Settings, DEFAULTS };

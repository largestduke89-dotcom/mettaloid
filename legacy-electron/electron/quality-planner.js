// Quality planner: given a replay-buffer RAM budget and replay length, pick the
// best resolution / frame rate / bitrate that fits, or (manual mode) report how
// much memory the user's chosen settings need.
//
// The replay buffer holds *encoded* packets, so its RAM use is simply
//   (video kbps + audio kbps) × seconds.
// With CBR the encoder never exceeds its bitrate, which makes memory use
// deterministic — the planner relies on that.
const os = require('os');

const MiB = 1024 * 1024;
const HEADROOM = 0.9; // keep 10% spare for keyframe spikes / muxing overhead

// bits-per-pixel-per-frame needed for "good" quality and the point beyond which
// more bitrate is wasted, relative to H.264 hardware encoding.
const CODEC_EFF = { h264: 1.0, hevc: 0.65, av1: 0.55 };
const BPP_GOOD = 0.075;
const BPP_CEILING = 0.2;
const HARD_CAP_KBPS = { h264: 130000, hevc: 130000, av1: 100000 };
const MIN_VIDEO_KBPS = 800;

const HEIGHTS = [2160, 1440, 1080, 900, 720, 540, 480, 360];
const AUDIO_STEPS = [64, 96, 128, 160, 192, 256, 320];

function autoMemoryMB(totalBytes = os.totalmem()) {
  // ~12% of system RAM, clamped to a sane range, rounded to 256 MB
  const mb = (totalBytes / MiB) * 0.12;
  return Math.max(512, Math.min(6144, Math.round(mb / 256) * 256));
}

function sizeFor(native, h) {
  if (!h || h >= native.height) return { width: native.width, height: native.height };
  let w = Math.round((native.width * h) / native.height);
  if (w % 2) w += 1;
  return { width: w, height: h };
}

function label(bppNorm) {
  if (bppNorm >= 0.15) return 'Excellent';
  if (bppNorm >= 0.105) return 'Great';
  if (bppNorm >= 0.075) return 'Good';
  if (bppNorm >= 0.05) return 'Fair';
  return 'Low';
}

const kbpsToMB = (kbps, seconds) => (kbps * 1000 / 8) * seconds / MiB;

/**
 * @param {object} o
 * @param {number} o.memoryMB        replay RAM budget (0 = auto from system RAM)
 * @param {number} o.seconds         replay length
 * @param {{width:number,height:number}} o.native   capture/canvas size
 * @param {string|number} o.maxResolution  'native' or a height
 * @param {number} o.maxFps
 * @param {string} o.codec           h264|hevc|av1
 * @param {number} o.audioTracks     number of recorded audio tracks
 * @param {number} o.audioKbps       preferred per-track audio bitrate
 * @param {boolean} o.auto           auto quality on/off
 * @param {object}  o.manual         { resolution, fps, bitrateKbps, rateControl }
 */
function planQuality(o) {
  const memoryMB = o.memoryMB > 0 ? o.memoryMB : autoMemoryMB();
  const seconds = Math.max(5, o.seconds);
  const codec = CODEC_EFF[o.codec] ? o.codec : 'h264';
  const eff = CODEC_EFF[codec];
  const n = Math.max(1, o.audioTracks || 1);
  const budgetKbps = Math.floor((memoryMB * MiB * 8 * HEADROOM) / seconds / 1000);
  const notes = [];

  if (!o.auto) {
    const m = o.manual || {};
    const size = sizeFor(o.native, m.resolution === 'native' ? null : parseInt(m.resolution, 10));
    const fps = m.fps || 60;
    const audio = o.audioKbps * n;
    const variable = m.rateControl === 'CQP';
    const videoKbps = variable ? null : m.bitrateKbps;
    const totalKbps = variable ? null : videoKbps + audio;
    const needMB = variable ? null : kbpsToMB(totalKbps, seconds);
    const fitsSeconds = variable ? null : Math.floor((memoryMB * MiB * HEADROOM * 8) / (totalKbps * 1000));
    if (variable) notes.push('Constant-quality (CQP) bitrate varies, so replay memory use is unpredictable. Use CBR to stay within the limit.');
    else if (fitsSeconds < seconds) notes.push(`These settings need ${Math.round(needMB)} MB; with a ${memoryMB} MB limit only the last ${fitsSeconds}s will be kept.`);
    const bppNorm = videoKbps ? (videoKbps * 1000) / (size.width * size.height * fps) / eff : null;
    return {
      auto: false, memoryMB, seconds, codec, ...size, fps, videoKbps, audioKbps: o.audioKbps, audioTracks: n,
      totalKbps, estMemoryMB: needMB, fitsSeconds, quality: bppNorm ? label(bppNorm) : 'Variable', notes, budgetKbps
    };
  }

  // ---- auto: audio first (cap at ~12% of the budget, min 64 kbps/track)
  let a = o.audioKbps || 160;
  while (a > 64 && a * n > budgetKbps * 0.12) a = AUDIO_STEPS[Math.max(0, AUDIO_STEPS.indexOf(a) - 1)] || 64;
  let videoBudget = budgetKbps - a * n;
  if (videoBudget < MIN_VIDEO_KBPS) {
    notes.push('Memory limit is very low for this replay length — quality will be poor. Raise the limit or shorten the replay.');
    videoBudget = Math.max(300, videoBudget);
  }

  const maxH = o.maxResolution && o.maxResolution !== 'native' ? Math.min(parseInt(o.maxResolution, 10), o.native.height) : o.native.height;
  const heights = [...new Set([maxH, ...HEIGHTS.filter((h) => h < maxH)])];
  const maxFps = Math.max(24, o.maxFps || 60);
  const fpsList = [...new Set([maxFps, 144, 120, 60, 30].filter((f) => f <= maxFps))].sort((x, y) => y - x);

  // utility: resolution matters, frame rate matters a bit less (0.7 weight)
  let best = null;
  for (const h of heights) {
    const size = sizeFor(o.native, h);
    for (const f of fpsList) {
      const bppNorm = (videoBudget * 1000) / (size.width * size.height * f) / eff;
      if (bppNorm < BPP_GOOD) continue;
      const u = Math.log2(size.height) + 0.7 * Math.log2(f);
      if (!best || u > best.u) best = { ...size, fps: f, u };
    }
  }
  let limitedBy = 'memory';
  if (!best) {
    // nothing reaches "good": take the combo with the highest bpp at ≥30 fps
    const size = sizeFor(o.native, Math.min(maxH, 480));
    best = { ...size, fps: Math.min(30, maxFps) };
  }
  const pixRate = best.width * best.height * best.fps;
  const ceiling = Math.min(HARD_CAP_KBPS[codec], Math.round((BPP_CEILING * eff * pixRate) / 1000));
  let videoKbps = Math.min(videoBudget, ceiling);
  if (videoKbps === ceiling) limitedBy = 'ceiling';
  videoKbps = Math.max(300, Math.floor(videoKbps / 100) * 100);
  const bppNorm = (videoKbps * 1000) / pixRate / eff;
  const totalKbps = videoKbps + a * n;

  if (codec === 'h264' && bppNorm < 0.11 && limitedBy === 'memory') {
    notes.push('HEVC would give noticeably better quality at this memory limit (Quality → Codec).');
  }
  if (limitedBy === 'ceiling') {
    notes.push('Your memory limit has room to spare — quality is already at the useful maximum for this resolution/frame rate.');
  }

  return {
    auto: true, memoryMB, seconds, codec, width: best.width, height: best.height, fps: best.fps,
    videoKbps, audioKbps: a, audioTracks: n, totalKbps,
    estMemoryMB: kbpsToMB(totalKbps, seconds), fitsSeconds: Math.floor((memoryMB * MiB * HEADROOM * 8) / (totalKbps * 1000)),
    quality: label(bppNorm), limitedBy, notes, budgetKbps
  };
}

module.exports = { planQuality, autoMemoryMB };

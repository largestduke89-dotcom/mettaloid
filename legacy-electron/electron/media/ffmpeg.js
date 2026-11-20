// FFmpeg / FFprobe helpers: binary resolution (asar-unpacked), probing,
// progress-reporting runner, thumbnails and fast lossless trims.
const { spawn, execFile } = require('child_process');
const os = require('os');
const fs = require('fs');
const path = require('path');

function unpacked(p) {
  return p.replace(`app.asar${path.sep}`, `app.asar.unpacked${path.sep}`);
}

const FFMPEG = unpacked(require('ffmpeg-static'));
const FFPROBE = unpacked(require('ffprobe-static').path);

let hwCache = null;
// Which hardware H.264 encoders does this ffmpeg build + machine support?
async function hwEncoders() {
  if (hwCache) return hwCache;
  const candidates = ['h264_nvenc', 'h264_amf', 'h264_qsv'];
  const ok = [];
  for (const enc of candidates) {
    const works = await new Promise((resolve) => {
      const p = spawn(FFMPEG, ['-hide_banner', '-f', 'lavfi', '-i', 'color=c=black:s=256x144:d=0.2', '-c:v', enc, '-f', 'null', '-'], { windowsHide: true });
      const t = setTimeout(() => { try { p.kill(); } catch {} resolve(false); }, 8000);
      p.on('exit', (code) => { clearTimeout(t); resolve(code === 0); });
      p.on('error', () => { clearTimeout(t); resolve(false); });
    });
    if (works) ok.push(enc);
  }
  hwCache = ok;
  return ok;
}

function probe(file) {
  return new Promise((resolve, reject) => {
    execFile(FFPROBE, ['-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', file],
      { windowsHide: true, maxBuffer: 16 * 1024 * 1024 }, (err, stdout) => {
        if (err) return reject(err);
        try {
          const j = JSON.parse(stdout);
          const v = (j.streams || []).find((s) => s.codec_type === 'video');
          const audio = (j.streams || []).filter((s) => s.codec_type === 'audio');
          const fpsStr = v ? v.avg_frame_rate || v.r_frame_rate : '0/1';
          const [n, d] = fpsStr.split('/').map(Number);
          resolve({
            duration: parseFloat(j.format.duration) || (v && parseFloat(v.duration)) || 0,
            size: parseInt(j.format.size, 10) || 0,
            bitrate: parseInt(j.format.bit_rate, 10) || 0,
            width: v ? v.width : 0,
            height: v ? v.height : 0,
            fps: d ? Math.round((n / d) * 100) / 100 : 0,
            videoCodec: v ? v.codec_name : null,
            audioTracks: audio.map((a, i) => ({
              index: i,
              codec: a.codec_name,
              channels: a.channels,
              title: (a.tags && (a.tags.title || a.tags.handler_name)) || `Track ${i + 1}`
            }))
          });
        } catch (e) {
          reject(e);
        }
      });
  });
}

/**
 * Run ffmpeg with progress. onProgress(fraction 0..1, info)
 * Returns { promise, cancel }.
 */
function run(args, { duration = 0, onProgress, cwd, lowPriority = false, threads = 0 } = {}) {
  let proc;
  let cancelled = false;
  const promise = new Promise((resolve, reject) => {
    let full = args;
    if (threads > 0) {
      // cap decoder, filter graph and encoder threads (the last arg is the output)
      const n = String(threads);
      full = ['-threads', n, '-filter_complex_threads', n, ...args.slice(0, -1), '-threads', n, args[args.length - 1]];
    }
    proc = spawn(FFMPEG, ['-hide_banner', '-y', '-progress', 'pipe:1', '-nostats', ...full], { windowsHide: true, cwd });
    if (lowPriority) {
      // keep exports from stealing CPU from the game
      try { os.setPriority(proc.pid, os.constants.priority.PRIORITY_BELOW_NORMAL); } catch {}
    }
    let errTail = '';
    let buf = '';
    proc.stdout.on('data', (d) => {
      buf += d.toString();
      let idx;
      while ((idx = buf.indexOf('\n')) !== -1) {
        const line = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 1);
        const m = /^out_time_(?:us|ms)=(\d+)/.exec(line);
        if (m && duration > 0 && onProgress) {
          const secs = parseInt(m[1], 10) / 1e6;
          onProgress(Math.min(1, Math.max(0, secs / duration)), { secs });
        }
        const sp = /^speed=\s*([\d.]+)x/.exec(line);
        if (sp && onProgress) onProgress(null, { speed: parseFloat(sp[1]) });
      }
    });
    proc.stderr.on('data', (d) => {
      errTail = (errTail + d.toString()).slice(-4000);
    });
    proc.on('error', reject);
    proc.on('exit', (code) => {
      if (cancelled) return reject(new Error('Cancelled'));
      if (code === 0) return resolve();
      reject(new Error(`ffmpeg exited with code ${code}\n${errTail.split('\n').slice(-8).join('\n')}`));
    });
  });
  return {
    promise,
    cancel: () => {
      cancelled = true;
      try { proc && proc.kill(); } catch {}
    }
  };
}

async function thumbnail(file, out, atSec = 1) {
  fs.mkdirSync(path.dirname(out), { recursive: true });
  const isImage = /\.(png|jpe?g|webp|bmp)$/i.test(file);
  const args = isImage
    ? ['-i', file, '-vf', 'scale=480:-2', '-frames:v', '1', out]
    : ['-ss', String(Math.max(0, atSec)), '-i', file, '-vf', 'scale=480:-2', '-frames:v', '1', '-q:v', '4', out];
  await run(args, { lowPriority: true, threads: 2 }).promise;
  return out;
}

// Keep only the last `seconds` of a clip without re-encoding.
// OBS is configured with 1s keyframes, so this is accurate to ~1s.
async function trimTail(input, output, seconds) {
  await run(['-sseof', `-${seconds}`, '-i', input, '-map', '0', '-c', 'copy', '-avoid_negative_ts', 'make_zero', '-movflags', '+faststart', output]).promise;
  return output;
}

// Lossless cut of [start, end] (keyframe accurate)
async function fastCut(input, output, start, end) {
  await run(['-ss', String(start), '-to', String(end), '-i', input, '-map', '0', '-c', 'copy', '-avoid_negative_ts', 'make_zero', '-movflags', '+faststart', output]).promise;
  return output;
}

module.exports = { FFMPEG, FFPROBE, probe, run, thumbnail, trimTail, fastCut, hwEncoders };

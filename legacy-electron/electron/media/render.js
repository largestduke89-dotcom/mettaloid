// Non-destructive render pipeline. The editor, the compressor and the
// montage tool all describe their output as a "project" and render it here.
//
// project = {
//   sources: [path, ...],
//   segments: [{ src, start, end, speed }],          // kept ranges in output order
//   crop: { x, y, w, h } | null                       // normalised 0..1 of source frame
//   rotate: 0|90|180|270, flipH, flipV,
//   aspect: 'source'|'16:9'|'9:16'|'1:1'|'4:5'|'21:9', fit: 'crop'|'pad'|'blur',
//   color: { brightness, contrast, saturation, gamma, sharpen, vignette, hue },
//   fadeIn, fadeOut,                                   // seconds
//   texts: [{ text, start, end, x, y, size, color, box, boxColor, boxOpacity, font, outline }],
//   audio: { tracks: [{ index, volume, muted }], normalize, music: { path, volume, start, loop } | null },
//   output: { resolution, fps, format: 'mp4'|'webm'|'gif'|'mkv', codec: 'h264'|'hevc',
//             quality: 'high'|'medium'|'low', targetMB, encoder: 'auto'|'x264'|'nvenc'|'amf'|'qsv',
//             twoPass, autoDownscale, audioKbps, mode: 'encode'|'copy' }
// }
const fs = require('fs');
const os = require('os');
const path = require('path');
const { probe, run, fastCut, hwEncoders } = require('./ffmpeg');

const even = (n) => Math.max(2, Math.round(n / 2) * 2);
const num = (v, d) => (v === undefined || v === null || v === '' || Number.isNaN(Number(v)) ? d : Number(v));

function esc(s) {
  // value for a filter option inside single quotes
  return "'" + String(s).replace(/\\/g, '/').replace(/:/g, '\\:').replace(/'/g, "'\\''") + "'";
}

function atempoChain(speed) {
  const parts = [];
  let s = speed;
  while (s > 2) { parts.push('atempo=2'); s /= 2; }
  while (s < 0.5) { parts.push('atempo=0.5'); s /= 0.5; }
  if (Math.abs(s - 1) > 1e-3) parts.push(`atempo=${s.toFixed(5)}`);
  return parts;
}

function parseAspect(a) {
  if (!a || a === 'source') return null;
  const [w, h] = a.split(':').map(Number);
  return w && h ? w / h : null;
}

function outputDuration(project) {
  return project.segments.reduce((t, s) => t + Math.max(0, s.end - s.start) / (s.speed || 1), 0);
}

/** Work out geometry: post-crop/rotate source size and final W x H */
function geometry(project, info, shortSideOverride) {
  const c = project.crop;
  let cw = info.width;
  let ch = info.height;
  if (c && c.w > 0 && c.h > 0) {
    cw = even(info.width * c.w);
    ch = even(info.height * c.h);
  }
  if (project.rotate === 90 || project.rotate === 270) [cw, ch] = [ch, cw];
  const A = parseAspect(project.aspect) || cw / ch;
  const fit = project.fit || 'crop';
  // natural size at the requested aspect
  let natW, natH;
  if (!parseAspect(project.aspect)) {
    natW = cw; natH = ch;
  } else if (fit === 'crop') {
    natW = Math.min(cw, ch * A); natH = natW / A;
  } else {
    const short = Math.min(cw, ch);
    if (A >= 1) { natH = short; natW = short * A; } else { natW = short; natH = short / A; }
  }
  let W = natW; let H = natH;
  const res = shortSideOverride || (project.output.resolution && project.output.resolution !== 'source' ? Number(project.output.resolution) : null);
  if (res) {
    if (W >= H) { H = Math.min(res, natH * 4); W = H * A; } else { W = Math.min(res, natW * 4); H = W / A; }
  }
  return { cw, ch, A, fit, W: even(W), H: even(H) };
}

function buildFilters(project, infos, opts) {
  const { W, H, A, fit } = opts.geo;
  const fps = opts.fps;
  const f = [];
  const multi = project.sources.length > 1;
  const segs = project.segments;
  const audioOn = opts.audio;

  // --- per source: split video & build mixed audio
  project.sources.forEach((_, s) => {
    const uses = segs.filter((g) => g.src === s).length;
    if (!uses) return;
    f.push(`[${s}:v]split=${uses}${Array.from({ length: uses }, (_, i) => `[sv${s}_${i}]`).join('')}`);
    if (!audioOn) return;
    const info = infos[s];
    let mixLabel;
    if (!info.audioTracks.length) {
      f.push(`anullsrc=r=48000:cl=stereo[an${s}]`);
      mixLabel = `an${s}`;
    } else {
      const trackCfg = !multi && project.audio && project.audio.tracks && project.audio.tracks.length
        ? project.audio.tracks.filter((t) => !t.muted && t.index < info.audioTracks.length)
        : [{ index: 0, volume: 1 }];
      if (!trackCfg.length) {
        f.push(`anullsrc=r=48000:cl=stereo[an${s}]`);
        mixLabel = `an${s}`;
      } else {
        trackCfg.forEach((t, i) => f.push(`[${s}:a:${t.index}]aresample=48000,aformat=channel_layouts=stereo,volume=${num(t.volume, 1).toFixed(3)}[at${s}_${i}]`));
        if (trackCfg.length === 1) mixLabel = `at${s}_0`;
        else {
          f.push(`${trackCfg.map((_, i) => `[at${s}_${i}]`).join('')}amix=inputs=${trackCfg.length}:normalize=0:dropout_transition=0[am${s}]`);
          mixLabel = `am${s}`;
        }
      }
    }
    f.push(`[${mixLabel}]asplit=${uses}${Array.from({ length: uses }, (_, i) => `[sa${s}_${i}]`).join('')}`);
  });

  // --- per segment: trim, speed, normalise (montage)
  const counters = {};
  const concatIn = [];
  segs.forEach((g, i) => {
    const k = counters[g.src] = (counters[g.src] || 0);
    counters[g.src]++;
    const sp = num(g.speed, 1);
    const vchain = [`trim=start=${g.start.toFixed(3)}:end=${g.end.toFixed(3)}`, 'setpts=PTS-STARTPTS'];
    if (sp !== 1) vchain.push(`setpts=PTS/${sp}`);
    if (multi) {
      const base = opts.baseSize;
      vchain.push(`scale=${base.w}:${base.h}:force_original_aspect_ratio=decrease`, `pad=${base.w}:${base.h}:(ow-iw)/2:(oh-ih)/2`, 'setsar=1', `fps=${fps || 60}`);
    }
    f.push(`[sv${g.src}_${k}]${vchain.join(',')}[v${i}]`);
    concatIn.push(`[v${i}]`);
    if (audioOn) {
      const achain = [`atrim=start=${g.start.toFixed(3)}:end=${g.end.toFixed(3)}`, 'asetpts=PTS-STARTPTS', ...atempoChain(sp)];
      // anullsrc is infinite; atrim with start/end on it works because it has timestamps from 0
      f.push(`[sa${g.src}_${k}]${achain.join(',')}[a${i}]`);
      concatIn.push(`[a${i}]`);
    }
  });
  f.push(`${concatIn.join('')}concat=n=${segs.length}:v=1:a=${audioOn ? 1 : 0}[vc]${audioOn ? '[ac]' : ''}`);

  // --- global video chain
  const v = [];
  const c = project.crop;
  if (c && c.w > 0 && c.h > 0 && !(c.w >= 0.999 && c.h >= 0.999)) {
    v.push(`crop=w=trunc(iw*${c.w}/2)*2:h=trunc(ih*${c.h}/2)*2:x=iw*${c.x}:y=ih*${c.y}`);
  }
  if (project.rotate === 90) v.push('transpose=1');
  else if (project.rotate === 180) v.push('transpose=1,transpose=1');
  else if (project.rotate === 270) v.push('transpose=2');
  if (project.flipH) v.push('hflip');
  if (project.flipV) v.push('vflip');
  const col = project.color || {};
  const eq = [];
  if (num(col.brightness, 0) !== 0) eq.push(`brightness=${num(col.brightness, 0)}`);
  if (num(col.contrast, 1) !== 1) eq.push(`contrast=${num(col.contrast, 1)}`);
  if (num(col.saturation, 1) !== 1) eq.push(`saturation=${num(col.saturation, 1)}`);
  if (num(col.gamma, 1) !== 1) eq.push(`gamma=${num(col.gamma, 1)}`);
  if (eq.length) v.push(`eq=${eq.join(':')}`);
  if (num(col.hue, 0) !== 0) v.push(`hue=h=${num(col.hue, 0)}`);
  if (num(col.sharpen, 0) > 0) v.push(`unsharp=5:5:${num(col.sharpen, 0).toFixed(2)}:5:5:0`);
  if (num(col.vignette, 0) > 0) v.push(`vignette=angle=${(num(col.vignette, 0) * 0.8).toFixed(3)}`);

  let vLabel = 'vc';
  if (v.length) { f.push(`[vc]${v.join(',')}[vf]`); vLabel = 'vf'; }

  // aspect + scale
  if (fit === 'blur' && parseAspect(project.aspect)) {
    f.push(`[${vLabel}]split[fg][bg]`);
    f.push(`[bg]scale=${W}:${H}:force_original_aspect_ratio=increase,crop=${W}:${H},boxblur=luma_radius=30:luma_power=2[bgb]`);
    f.push(`[fg]scale=${W}:${H}:force_original_aspect_ratio=decrease[fgs]`);
    f.push(`[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[vs]`);
  } else if (fit === 'pad' && parseAspect(project.aspect)) {
    f.push(`[${vLabel}]scale=${W}:${H}:force_original_aspect_ratio=decrease,pad=${W}:${H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[vs]`);
  } else if (parseAspect(project.aspect)) {
    f.push(`[${vLabel}]crop=w='min(iw,ih*${A})':h='min(ih,iw/${A})',scale=${W}:${H},setsar=1[vs]`);
  } else {
    f.push(`[${vLabel}]scale=${W}:${H}:flags=lanczos,setsar=1[vs]`);
  }

  // fps, text, fades
  const post = [];
  if (fps && !multi) post.push(`fps=${fps}`);
  const font = opts.fontPath;
  (project.texts || []).forEach((t, i) => {
    if (!t.text) return;
    const tf = path.join(opts.tmpDir, `text_${i}.txt`);
    fs.writeFileSync(tf, t.text, 'utf8');
    const size = Math.round((num(t.size, 64) / 1080) * H);
    const parts = [
      `fontfile=${esc(t.font || font)}`,
      `textfile=${esc(tf)}`,
      'expansion=none',
      `fontsize=${size}`,
      `fontcolor=${esc(t.color || '#ffffff')}`,
      `x=(w-text_w)*${num(t.x, 0.5)}`,
      `y=(h-text_h)*${num(t.y, 0.9)}`
    ];
    if (t.outline) parts.push(`borderw=${Math.max(1, Math.round(size / 18))}`, 'bordercolor=black');
    if (t.box) parts.push('box=1', `boxcolor=${esc(`${t.boxColor || '#000000'}@${num(t.boxOpacity, 0.5)}`)}`, `boxborderw=${Math.round(size / 4)}`);
    if (t.start !== undefined && t.end !== undefined) parts.push(`enable=${esc(`between(t,${num(t.start, 0)},${num(t.end, 9999)})`)}`);
    post.push(`drawtext=${parts.join(':')}`);
  });
  const dur = opts.duration;
  if (num(project.fadeIn, 0) > 0) post.push(`fade=t=in:st=0:d=${num(project.fadeIn, 0)}`);
  if (num(project.fadeOut, 0) > 0) post.push(`fade=t=out:st=${Math.max(0, dur - num(project.fadeOut, 0)).toFixed(3)}:d=${num(project.fadeOut, 0)}`);

  if (opts.format === 'gif') {
    post.push(`fps=${Math.min(fps || 20, 20)}`);
    f.push(`[vs]${post.join(',')}${post.length ? ',' : ''}split[g1][g2]`);
    f.push('[g1]palettegen=stats_mode=diff[pal]');
    f.push('[g2][pal]paletteuse=dither=bayer:bayer_scale=4[vout]');
  } else {
    post.push('format=yuv420p');
    f.push(`[vs]${post.join(',')}[vout]`);
  }

  // audio post
  if (audioOn) {
    const a = [];
    if (num(project.fadeIn, 0) > 0) a.push(`afade=t=in:st=0:d=${num(project.fadeIn, 0)}`);
    if (num(project.fadeOut, 0) > 0) a.push(`afade=t=out:st=${Math.max(0, dur - num(project.fadeOut, 0)).toFixed(3)}:d=${num(project.fadeOut, 0)}`);
    let aLabel = 'ac';
    const music = project.audio && project.audio.music;
    if (music && music.path && opts.musicInput !== undefined) {
      const delay = Math.round(num(music.start, 0) * 1000);
      f.push(`[${opts.musicInput}:a]aresample=48000,aformat=channel_layouts=stereo,volume=${num(music.volume, 0.5)},adelay=${delay}|${delay}[mus]`);
      f.push(`[ac][mus]amix=inputs=2:duration=first:normalize=0:dropout_transition=0[acm]`);
      aLabel = 'acm';
    }
    if (project.audio && project.audio.normalize) a.push('loudnorm=I=-16:TP=-1.5:LRA=11');
    a.push(`atrim=end=${dur.toFixed(3)}`);
    f.push(`[${aLabel}]${a.join(',')}[aout]`);
  }
  return f;
}

async function pickEncoder(pref, codec) {
  const hw = await hwEncoders();
  const has = (v) => hw.includes(`h264_${v}`);
  let vendor = pref;
  if (pref === 'auto') vendor = has('nvenc') ? 'nvenc' : has('amf') ? 'amf' : has('qsv') ? 'qsv' : 'x264';
  if (vendor !== 'x264' && !has(vendor)) vendor = 'x264';
  const names = {
    x264: codec === 'hevc' ? 'libx265' : 'libx264',
    nvenc: codec === 'hevc' ? 'hevc_nvenc' : 'h264_nvenc',
    amf: codec === 'hevc' ? 'hevc_amf' : 'h264_amf',
    qsv: codec === 'hevc' ? 'hevc_qsv' : 'h264_qsv'
  };
  return { vendor, name: names[vendor] };
}

const QUALITY = { high: 18, medium: 23, low: 28 };

function videoEncodeArgs(enc, { kbps, quality, format }) {
  if (format === 'gif') return [];
  if (format === 'webm') {
    return kbps
      ? ['-c:v', 'libvpx-vp9', '-b:v', `${kbps}k`, '-deadline', 'good', '-cpu-used', '2', '-row-mt', '1']
      : ['-c:v', 'libvpx-vp9', '-crf', String(QUALITY[quality] + 12 || 33), '-b:v', '0', '-deadline', 'good', '-cpu-used', '2', '-row-mt', '1'];
  }
  const q = typeof quality === 'number' ? quality : QUALITY[quality] || 23;
  switch (enc.vendor) {
    case 'nvenc':
      return kbps
        ? ['-c:v', enc.name, '-preset', 'p6', '-tune', 'hq', '-rc', 'vbr', '-multipass', 'fullres', '-b:v', `${kbps}k`, '-maxrate', `${Math.round(kbps * 1.2)}k`, '-bufsize', `${kbps * 2}k`]
        : ['-c:v', enc.name, '-preset', 'p6', '-tune', 'hq', '-rc', 'vbr', '-cq', String(q + 1), '-b:v', '0'];
    case 'amf':
      return kbps
        ? ['-c:v', enc.name, '-quality', 'quality', '-rc', 'vbr_peak', '-b:v', `${kbps}k`, '-maxrate', `${Math.round(kbps * 1.2)}k`, '-bufsize', `${kbps * 2}k`]
        : ['-c:v', enc.name, '-quality', 'quality', '-rc', 'cqp', '-qp_i', String(q), '-qp_p', String(q + 2)];
    case 'qsv':
      return kbps
        ? ['-c:v', enc.name, '-preset', 'slower', '-b:v', `${kbps}k`, '-maxrate', `${Math.round(kbps * 1.2)}k`, '-bufsize', `${kbps * 2}k`]
        : ['-c:v', enc.name, '-preset', 'slower', '-global_quality', String(q)];
    default:
      return kbps
        ? ['-c:v', enc.name, '-preset', 'medium', '-b:v', `${kbps}k`]
        : ['-c:v', enc.name, '-preset', 'medium', '-crf', String(q)];
  }
}

/**
 * Size-targeted bitrate plan. Returns { videoKbps, audioKbps, shortSide, fps }.
 * Guarantees total stays under targetMB (decimal MB) with a safety margin.
 */
function planBitrate({ targetMB, duration, geo, fps, audioKbps, hasAudio, autoDownscale }) {
  const totalKbps = (targetMB * 1e6 * 8 * 0.94) / duration / 1000; // 6% margin for muxing overhead
  let aK = hasAudio ? audioKbps : 0;
  if (hasAudio) {
    if (totalKbps < 300) aK = Math.min(aK, 48);
    else if (totalKbps < 800) aK = Math.min(aK, 64);
    else if (totalKbps < 1500) aK = Math.min(aK, 96);
  }
  let vK = Math.floor(totalKbps - aK);
  if (vK < 50) {
    vK = Math.max(30, Math.floor(totalKbps * 0.85));
    aK = hasAudio ? Math.max(16, Math.floor(totalKbps - vK)) : 0;
  }
  let shortSide = null;
  let outFps = fps;
  if (autoDownscale) {
    // keep bits-per-pixel-per-frame above ~0.05 for watchable H.264;
    // prefer dropping to 30 fps over going below 720p
    const THR = 0.05;
    const steps = [2160, 1440, 1080, 900, 720, 540, 480, 360, 240];
    const curShort = Math.min(geo.W, geo.H);
    const longOverShort = Math.max(geo.W, geo.H) / curShort;
    const bpp = (s, f) => (vK * 1000) / (s * s * longOverShort * f);
    const candidates = [curShort, ...steps.filter((st) => st < curShort)];
    const find = (f) => candidates.find((st) => bpp(st, f) >= THR) || candidates[candidates.length - 1];
    const floor = Math.min(720, curShort);
    const fpsOptions = [...new Set([outFps, 60, 30].filter((f) => f <= outFps))];
    let s = find(outFps);
    for (const f of fpsOptions) {
      const sf = find(f);
      if (sf >= floor) { s = sf; outFps = f; break; }
      if (sf > s) { s = sf; outFps = f; }
    }
    if (s < curShort) shortSide = s;
  }
  return { videoKbps: vK, audioKbps: aK, shortSide, fps: outFps };
}

/**
 * Render a project.
 * @returns {{ promise: Promise<{output:string,size:number,attempts:number}>, cancel: () => void }}
 */
function render(project, output, { onProgress, fontPath, tmpRoot, getRunOpts } = {}) {
  let current = null;
  let cancelled = false;
  const promise = (async () => {
    const out = project.output || {};
    const format = out.format || 'mp4';
    const infos = await Promise.all(project.sources.map((s) => probe(s)));
    const duration = outputDuration(project);
    if (duration <= 0.05) throw new Error('Nothing to render: timeline is empty');
    fs.mkdirSync(path.dirname(output), { recursive: true });

    // Fast path: lossless stream-copy trim
    if (out.mode === 'copy' && project.sources.length === 1 && project.segments.length === 1) {
      const g = project.segments[0];
      onProgress && onProgress(0.1, { phase: 'Trimming (lossless)' });
      await fastCut(project.sources[0], output, g.start, g.end);
      onProgress && onProgress(1, { phase: 'Done' });
      return { output, size: fs.statSync(output).size, attempts: 1 };
    }

    const tmpDir = fs.mkdtempSync(path.join(tmpRoot || os.tmpdir(), 'mt-render-'));
    try {
      const srcFps = infos[0].fps || 60;
      let fps = out.fps && out.fps !== 'source' ? Math.min(Number(out.fps), srcFps) : null;
      const hasAudio = format !== 'gif' && infos.some((i) => i.audioTracks.length) &&
        !(project.audio && project.audio.tracks && project.audio.tracks.length && project.audio.tracks.every((t) => t.muted) && project.sources.length === 1 && !(project.audio.music && project.audio.music.path));
      let geo = geometry(project, infos[0]);
      const enc = format === 'mp4' || format === 'mkv' ? await pickEncoder(out.encoder || 'auto', out.codec || 'h264') : { vendor: 'x264', name: 'libx264' };
      const targetMB = num(out.targetMB, 0);
      let plan = null;
      if (targetMB > 0 && format !== 'gif') {
        plan = planBitrate({ targetMB, duration, geo, fps: fps || srcFps, audioKbps: num(out.audioKbps, 128), hasAudio, autoDownscale: out.autoDownscale !== false });
        if (plan.shortSide) geo = geometry(project, infos[0], plan.shortSide);
        if (plan.fps !== (fps || srcFps)) fps = plan.fps;
      }
      const baseSize = { w: even(infos[0].width), h: even(infos[0].height) };

      const inputs = [];
      project.sources.forEach((s) => inputs.push('-i', s));
      let musicInput;
      const music = project.audio && project.audio.music;
      if (music && music.path && hasAudio) {
        if (music.loop) inputs.push('-stream_loop', '-1');
        inputs.push('-i', music.path);
        musicInput = project.sources.length;
      }

      const buildArgs = (videoKbps, pass, passlog) => {
        const filters = buildFilters(project, infos, { geo, fps, audio: hasAudio && pass !== 1, duration, tmpDir, fontPath, musicInput, format, baseSize });
        const script = path.join(tmpDir, `graph_${pass || 0}.txt`);
        fs.writeFileSync(script, filters.join(';\n'), 'utf8');
        const args = [...inputs, '-filter_complex_script', script, '-map', '[vout]'];
        if (hasAudio && pass !== 1) args.push('-map', '[aout]');
        args.push(...videoEncodeArgs(enc, { kbps: videoKbps, quality: out.quality || 'medium', format }));
        if (pass) args.push('-pass', String(pass), '-passlogfile', passlog);
        if (format === 'gif') {
          args.push('-loop', '0');
        } else if (pass === 1) {
          args.push('-an', '-f', 'null', 'NUL');
          return args;
        } else if (hasAudio) {
          const aK = plan ? plan.audioKbps : num(out.audioKbps, 160);
          args.push(...(format === 'webm' ? ['-c:a', 'libopus', '-b:a', `${aK}k`] : ['-c:a', 'aac', '-b:a', `${aK}k`, '-ac', '2']));
        }
        if (format === 'mp4') args.push('-movflags', '+faststart');
        args.push('-t', duration.toFixed(3), output);
        return args;
      };

      const runStep = async (args, phase, from, span) => {
        if (cancelled) throw new Error('Cancelled');
        current = run(args, {
          ...(getRunOpts ? getRunOpts() : {}),
          duration,
          cwd: tmpDir,
          onProgress: (p, info) => onProgress && onProgress(p === null ? null : from + p * span, { phase, ...info })
        });
        await current.promise;
      };

      const twoPass = plan && out.twoPass !== false && (enc.name === 'libx264' || format === 'webm');
      let videoKbps = plan ? plan.videoKbps : null;
      let attempts = 0;
      const maxAttempts = plan ? 4 : 1;
      while (attempts < maxAttempts) {
        attempts++;
        const label = attempts > 1 ? ` (retry ${attempts - 1}, ${videoKbps} kbps)` : '';
        if (twoPass) {
          const passlog = path.join(tmpDir, 'pass');
          await runStep(buildArgs(videoKbps, 1, passlog), `Analyzing (pass 1/2)${label}`, 0, 0.4);
          await runStep(buildArgs(videoKbps, 2, passlog), `Encoding (pass 2/2)${label}`, 0.4, 0.6);
        } else {
          await runStep(buildArgs(videoKbps), `Encoding${label}`, 0, 1);
        }
        const size = fs.statSync(output).size;
        if (!plan || size <= targetMB * 1e6) {
          onProgress && onProgress(1, { phase: 'Done' });
          return { output, size, attempts, videoKbps, geo: { W: geo.W, H: geo.H }, encoder: enc.name };
        }
        // Overshot: scale bitrate down proportionally with extra margin and retry
        videoKbps = Math.max(30, Math.floor(videoKbps * ((targetMB * 1e6) / size) * 0.93));
      }
      const size = fs.statSync(output).size;
      throw new Error(`Could not get under ${targetMB} MB (got ${(size / 1e6).toFixed(1)} MB). Try a shorter clip or lower resolution.`);
    } finally {
      fs.rmSync(tmpDir, { recursive: true, force: true });
    }
  })();
  return {
    promise,
    cancel: () => {
      cancelled = true;
      current && current.cancel();
    }
  };
}

/** Estimate result for UI preview before compressing */
async function estimate(project) {
  const infos = await Promise.all(project.sources.map((s) => probe(s)));
  const duration = outputDuration(project);
  const out = project.output || {};
  let geo = geometry(project, infos[0]);
  const srcFps = infos[0].fps || 60;
  const fps = out.fps && out.fps !== 'source' ? Math.min(Number(out.fps), srcFps) : srcFps;
  if (!out.targetMB) return { duration, W: geo.W, H: geo.H, fps };
  const plan = planBitrate({ targetMB: Number(out.targetMB), duration, geo, fps, audioKbps: num(out.audioKbps, 128), hasAudio: infos.some((i) => i.audioTracks.length), autoDownscale: out.autoDownscale !== false });
  if (plan.shortSide) geo = geometry(project, infos[0], plan.shortSide);
  return { duration, W: geo.W, H: geo.H, fps: plan.fps, videoKbps: plan.videoKbps, audioKbps: plan.audioKbps };
}

module.exports = { render, estimate, outputDuration, planBitrate, geometry };

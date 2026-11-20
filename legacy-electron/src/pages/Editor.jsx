import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useApp } from '../App';
import { invoke, mediaUrl, fmtTime, fmtDur, fmtSize } from '../api';
import { Icon, Slider, Seg, Select, Toggle } from '../components/ui';
import { SIZE_PRESETS } from './Compressor';

const ASPECTS = [
  { value: 'source', label: 'Original' }, { value: '16:9', label: '16:9' }, { value: '9:16', label: '9:16 (TikTok/Shorts)' },
  { value: '1:1', label: '1:1' }, { value: '4:5', label: '4:5' }, { value: '21:9', label: '21:9' }
];
const SPEEDS = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];
const TABS = [
  { id: 'trim', label: 'Trim', icon: 'scissors' },
  { id: 'frame', label: 'Frame', icon: 'crop' },
  { id: 'color', label: 'Look', icon: 'palette' },
  { id: 'text', label: 'Text', icon: 'text' },
  { id: 'audio', label: 'Audio', icon: 'volume' },
  { id: 'export', label: 'Export', icon: 'export' }
];
const DEFAULT_COLOR = { brightness: 0, contrast: 1, saturation: 1, gamma: 1, sharpen: 0, vignette: 0, hue: 0 };
const LOOKS = [
  { name: 'None', c: DEFAULT_COLOR },
  { name: 'Vivid', c: { ...DEFAULT_COLOR, contrast: 1.12, saturation: 1.35, sharpen: 0.4 } },
  { name: 'Punchy', c: { ...DEFAULT_COLOR, contrast: 1.25, saturation: 1.15, brightness: 0.02, sharpen: 0.6, vignette: 0.4 } },
  { name: 'Cinematic', c: { ...DEFAULT_COLOR, contrast: 1.1, saturation: 0.85, gamma: 0.95, vignette: 0.6 } },
  { name: 'Bright', c: { ...DEFAULT_COLOR, brightness: 0.07, contrast: 1.05, saturation: 1.1 } },
  { name: 'B&W', c: { ...DEFAULT_COLOR, saturation: 0, contrast: 1.2 } },
  { name: 'Warm', c: { ...DEFAULT_COLOR, hue: -8, saturation: 1.15 } },
  { name: 'Cool', c: { ...DEFAULT_COLOR, hue: 10, saturation: 1.05 } }
];

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const segDur = (s) => (s.end - s.start) / (s.speed || 1);

function offsets(segs) {
  let t = 0;
  return segs.map((s) => { const o = { outStart: t, outEnd: t + segDur(s) }; t = o.outEnd; return o; });
}
function locate(segs, offs, t) {
  for (let i = 0; i < segs.length; i++) {
    if (t < offs[i].outEnd - 1e-6 || i === segs.length - 1) {
      const local = clamp(t - offs[i].outStart, 0, segDur(segs[i]));
      return { i, st: segs[i].start + local * (segs[i].speed || 1) };
    }
  }
  return { i: 0, st: 0 };
}

function defaultProject(clips, query) {
  const single = clips.length === 1;
  const c0 = clips[0];
  const tracks = single && c0.audioTracks ? c0.audioTracks : [];
  return {
    sources: clips.map((c) => c.path),
    segments: query.get('seg') ? query.get('seg').split(',').map((x) => {
      const [cid, a, b] = x.split(':');
      const src = clips.findIndex((c) => c.id === cid);
      return src < 0 ? null : { src, start: Math.max(0, Number(a)), end: Math.min(clips[src].duration, Number(b)), speed: 1 };
    }).filter((g) => g && g.end - g.start > 0.1) : clips.map((c, i) => ({
      src: i,
      start: single && query.get('start') ? Number(query.get('start')) : 0,
      end: single && query.get('end') ? Number(query.get('end')) : c.duration,
      speed: 1
    })),
    crop: null, rotate: 0, flipH: false, flipV: false, aspect: 'source', fit: 'crop',
    color: { ...DEFAULT_COLOR }, fadeIn: 0, fadeOut: 0, texts: [],
    audio: {
      // OBS tracks: 1 = mix, 2 = game/desktop, 3 = mic, 4 = discord (already inside desktop)
      tracks: tracks.map((t, i) => ({ index: i, title: t.title, volume: 1, muted: tracks.length > 1 ? i === 0 || i >= 3 : false })),
      normalize: false, music: null
    },
    output: { resolution: 'source', fps: 'source', format: 'mp4', codec: 'h264', quality: 'high', targetMB: 0, encoder: 'auto', mode: 'encode', audioKbps: 160, autoDownscale: true, twoPass: true }
  };
}

export default function Editor({ id }) {
  const { lib, go, toast, route } = useApp();
  // montage from library selection (?ids=a,b) or from Find Frame matches (?seg=id:start:end,...)
  const segParam = route.query.get('seg');
  const montageIds = id !== 'montage' ? null : segParam
    ? [...new Set(segParam.split(',').map((x) => x.split(':')[0]).filter(Boolean))]
    : (route.query.get('ids') || '').split(',').filter(Boolean);
  const clips = useMemo(() => (montageIds ? montageIds.map((x) => lib.clips.find((c) => c.id === x)).filter(Boolean) : [lib.clips.find((c) => c.id === id)].filter(Boolean)), [lib.clips, id, route.query.get('ids')]);
  const draftKey = `mt-draft-${montageIds ? segParam || montageIds.join(',') : id}`;
  const [project, setProject] = useState(null);
  const hist = useRef({ past: [], future: [] });
  const [tab, setTab] = useState('trim');
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selSeg, setSelSeg] = useState(0);
  const [selText, setSelText] = useState(null);
  const [cropMode, setCropMode] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [stageSize, setStageSize] = useState({ w: 800, h: 450 });
  const [trackFiles, setTrackFiles] = useState([]);
  const [film, setFilm] = useState({});
  const [masterVol, setMasterVol] = useState(1);
  const vids = useRef([]);
  const auds = useRef([]);
  const musicRef = useRef();
  const blurCanvas = useRef();
  const stageRef = useRef();
  const cur = useRef(0);
  const timeRef = useRef(0);

  // ---------- init ----------
  useEffect(() => {
    if (!clips.length || project) return;
    let p = null;
    try { p = JSON.parse(localStorage.getItem(draftKey) || 'null'); } catch {}
    const fromQuery = route.query.get('start') !== null || !!segParam;
    const valid = p && !fromQuery && Array.isArray(p.sources) && p.sources.length === clips.length && p.sources.every((x, i) => x === clips[i].path);
    setProject(valid ? p : defaultProject(clips, route.query));
  }, [clips.length]);

  useEffect(() => {
    if (!project) return;
    try { localStorage.setItem(draftKey, JSON.stringify(project)); } catch {}
  }, [project]);

  useEffect(() => {
    if (clips.length !== 1) return;
    const c = clips[0];
    if (c.audioTracks && c.audioTracks.length > 1) invoke('media:extractTracks', c.path).then(setTrackFiles).catch(() => setTrackFiles([]));
  }, [clips.length && clips[0].path]);

  useEffect(() => {
    clips.forEach((c) => invoke('media:filmstrip', c.path, c.duration).then((f) => setFilm((x) => ({ ...x, [c.path]: f }))).catch(() => {}));
  }, [clips.map((c) => c.path).join('|')]);

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setStageSize({ w: el.clientWidth - 32, h: el.clientHeight - 32 }));
    ro.observe(el);
    return () => ro.disconnect();
  }, [project !== null]);

  const commit = useCallback((fn) => {
    setProject((p) => {
      hist.current.past.push(JSON.stringify(p));
      if (hist.current.past.length > 200) hist.current.past.shift();
      hist.current.future = [];
      return typeof fn === 'function' ? fn(p) : { ...p, ...fn };
    });
  }, []);
  const live = useCallback((fn) => setProject((p) => (typeof fn === 'function' ? fn(p) : { ...p, ...fn })), []);
  const snapshot = useCallback(() => setProject((p) => { hist.current.past.push(JSON.stringify(p)); hist.current.future = []; return p; }), []);
  const undo = () => { const h = hist.current; if (!h.past.length) return; h.future.push(JSON.stringify(project)); setProject(JSON.parse(h.past.pop())); };
  const redo = () => { const h = hist.current; if (!h.future.length) return; h.past.push(JSON.stringify(project)); setProject(JSON.parse(h.future.pop())); };

  const segs = project ? project.segments : [];
  const offs = useMemo(() => offsets(segs), [segs]);
  const total = offs.length ? offs[offs.length - 1].outEnd : 0;

  // ---------- playback engine ----------
  const activeVideo = () => vids.current[segs[cur.current]?.src];
  const syncAux = useCallback((force) => {
    const v = activeVideo();
    if (!v) return;
    auds.current.forEach((a) => {
      if (!a) return;
      a.playbackRate = v.playbackRate;
      if (force || Math.abs(a.currentTime - v.currentTime) > 0.12) a.currentTime = v.currentTime;
      if (!v.paused && a.paused) a.play().catch(() => {});
      if (v.paused && !a.paused) a.pause();
    });
    const m = musicRef.current;
    const music = project && project.audio.music;
    if (m && music) {
      const mt = timeRef.current - (music.start || 0);
      if (mt < 0 || v.paused) { if (!m.paused) m.pause(); }
      else {
        const want = m.duration ? (music.loop ? mt % m.duration : mt) : mt;
        if (force || Math.abs(m.currentTime - want) > 0.2) m.currentTime = want;
        if (m.paused && (!m.duration || music.loop || mt < m.duration)) m.play().catch(() => {});
      }
    }
  }, [project, segs]);

  const seek = useCallback((t) => {
    if (!segs.length) return;
    t = clamp(t, 0, total);
    const { i, st } = locate(segs, offs, t);
    const prevV = activeVideo();
    cur.current = i;
    const v = vids.current[segs[i].src];
    if (prevV && prevV !== v) prevV.pause();
    if (v) {
      v.currentTime = st;
      v.playbackRate = segs[i].speed || 1;
      if (playing) v.play().catch(() => {});
    }
    timeRef.current = t;
    setTime(t);
    syncAux(true);
  }, [segs, offs, total, playing, syncAux]);

  const play = useCallback(() => {
    if (!segs.length) return;
    if (timeRef.current >= total - 0.05) seek(0);
    const v = activeVideo();
    if (!v) return;
    v.playbackRate = segs[cur.current].speed || 1;
    v.play().catch(() => {});
    setPlaying(true);
  }, [segs, total, seek]);
  const pause = useCallback(() => {
    vids.current.forEach((v) => v && v.pause());
    auds.current.forEach((a) => a && a.pause());
    musicRef.current && musicRef.current.pause();
    setPlaying(false);
  }, []);

  useEffect(() => {
    let raf;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const seg = segs[cur.current];
      const v = activeVideo();
      if (!seg || !v) return;
      if (playing) {
        if (v.currentTime >= seg.end - 0.03 || v.ended) {
          const nxt = cur.current + 1;
          if (nxt >= segs.length) { pause(); timeRef.current = total; setTime(total); return; }
          cur.current = nxt;
          const v2 = vids.current[segs[nxt].src];
          if (v2 !== v) v.pause();
          v2.currentTime = segs[nxt].start;
          v2.playbackRate = segs[nxt].speed || 1;
          v2.play().catch(() => {});
          syncAux(true);
          return;
        }
        if (v.currentTime < seg.start - 0.25) v.currentTime = seg.start;
        const t = offs[cur.current].outStart + (v.currentTime - seg.start) / (seg.speed || 1);
        timeRef.current = t;
        setTime(t);
        syncAux(false);
      }
      // blurred background preview
      const cv = blurCanvas.current;
      if (cv && v.readyState >= 2) {
        const ctx = cv.getContext('2d');
        ctx.drawImage(v, 0, 0, cv.width, cv.height);
      }
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [segs, offs, playing, total, pause, syncAux]);

  // volumes
  useEffect(() => {
    if (!project) return;
    const useTracks = trackFiles.length > 1;
    vids.current.forEach((v) => { if (v) { v.muted = useTracks; v.volume = clamp(masterVol, 0, 1); } });
    auds.current.forEach((a, i) => {
      if (!a) return;
      const t = project.audio.tracks[i];
      a.volume = t && !t.muted ? clamp(t.volume * masterVol, 0, 1) : 0;
    });
    if (musicRef.current && project.audio.music) musicRef.current.volume = clamp((project.audio.music.volume ?? 0.5) * masterVol, 0, 1);
  }, [project && project.audio, trackFiles, masterVol]);

  // keep current segment valid after edits
  useEffect(() => {
    if (!project) return;
    if (selSeg >= segs.length) setSelSeg(Math.max(0, segs.length - 1));
    if (timeRef.current > total) seek(total);
  }, [segs.length, total]);

  // ---------- edit ops ----------
  const split = () => {
    const { i, st } = locate(segs, offs, timeRef.current);
    const s = segs[i];
    if (st - s.start < 0.1 || s.end - st < 0.1) return;
    commit((p) => {
      const n = p.segments.slice();
      n.splice(i, 1, { ...s, end: st }, { ...s, start: st });
      return { ...p, segments: n };
    });
    setSelSeg(i + 1);
  };
  const delSeg = (i = selSeg) => {
    if (segs.length <= 1) return toast({ title: 'Can’t delete the only segment', subtitle: 'Trim it instead', kind: 'err' });
    commit((p) => ({ ...p, segments: p.segments.filter((_, k) => k !== i) }));
  };
  const dupSeg = () => commit((p) => { const n = p.segments.slice(); n.splice(selSeg + 1, 0, { ...n[selSeg] }); return { ...p, segments: n }; });
  const moveSeg = (d) => {
    const j = selSeg + d;
    if (j < 0 || j >= segs.length) return;
    commit((p) => { const n = p.segments.slice(); [n[selSeg], n[j]] = [n[j], n[selSeg]]; return { ...p, segments: n }; });
    setSelSeg(j);
  };
  const setSeg = (i, patch, isLive) => (isLive ? live : commit)((p) => ({ ...p, segments: p.segments.map((s, k) => (k === i ? { ...s, ...patch } : s)) }));
  const setIn = () => { const { i, st } = locate(segs, offs, timeRef.current); if (st < segs[i].end - 0.1) setSeg(i, { start: st }); };
  const setOut = () => { const { i, st } = locate(segs, offs, timeRef.current); if (st > segs[i].start + 0.1) setSeg(i, { end: st }); };
  const resetAll = () => { commit(defaultProject(clips, new URLSearchParams())); setSelText(null); seek(0); };

  // ---------- keyboard ----------
  useEffect(() => {
    const k = (e) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
      const fps = clips[0]?.fps || 60;
      if (e.key === ' ') { e.preventDefault(); playing ? pause() : play(); }
      else if (e.key === 's' || e.key === 'S') split();
      else if ((e.key === 'Delete' || e.key === 'Backspace') && selText !== null) commit((p) => ({ ...p, texts: p.texts.filter((_, i) => i !== selText) })) || setSelText(null);
      else if (e.key === 'Delete' || e.key === 'Backspace') delSeg();
      else if (e.key === 'i' || e.key === 'I') setIn();
      else if (e.key === 'o' || e.key === 'O') setOut();
      else if (e.key === 'ArrowLeft') seek(timeRef.current - (e.shiftKey ? 5 : 1 / fps));
      else if (e.key === 'ArrowRight') seek(timeRef.current + (e.shiftKey ? 5 : 1 / fps));
      else if (e.key === 'Home') seek(0);
      else if (e.key === 'End') seek(total);
      else if (e.ctrlKey && (e.key === 'z' || e.key === 'Z')) { e.preventDefault(); e.shiftKey ? redo() : undo(); }
      else if (e.ctrlKey && (e.key === 'y' || e.key === 'Y')) { e.preventDefault(); redo(); }
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  });

  if (!clips.length) return <div className="content"><div className="empty">Clip not found.</div></div>;
  if (!project) return null;

  const c0 = clips[0];
  const srcW = c0.width || 1920;
  const srcH = c0.height || 1080;
  const selected = segs[selSeg];
  const multi = clips.length > 1;

  // ---------- preview geometry ----------
  const crop = project.crop && !cropMode ? project.crop : { x: 0, y: 0, w: 1, h: 1 };
  const pw = srcW * crop.w;
  const ph = srcH * crop.h;
  const rot = cropMode ? 0 : project.rotate;
  const [rw, rh] = rot === 90 || rot === 270 ? [ph, pw] : [pw, ph];
  const aspectRatio = (() => {
    if (cropMode || project.aspect === 'source') return rw / rh;
    const [a, b] = project.aspect.split(':').map(Number);
    return a / b;
  })();
  let FW = stageSize.w;
  let FH = FW / aspectRatio;
  if (FH > stageSize.h) { FH = stageSize.h; FW = FH * aspectRatio; }
  const fit = cropMode ? 'crop' : project.aspect === 'source' ? 'crop' : project.fit;
  const s = fit === 'crop' ? Math.max(FW / rw, FH / rh) : Math.min(FW / rw, FH / rh);
  const boxW = rw * s; const boxH = rh * s;
  const innerW = pw * s; const innerH = ph * s;
  const col = cropMode ? DEFAULT_COLOR : project.color;
  const cssFilter = `brightness(${1 + col.brightness * 1.4}) contrast(${col.contrast}) saturate(${col.saturation}) hue-rotate(${col.hue}deg)`;
  const fadeOpacity = (() => {
    const t = time;
    if (project.fadeIn > 0 && t < project.fadeIn) return 1 - t / project.fadeIn;
    if (project.fadeOut > 0 && t > total - project.fadeOut) return (t - (total - project.fadeOut)) / project.fadeOut;
    return 0;
  })();

  const exportProject = () => {
    const out = { ...project };
    out.audio = { ...project.audio, tracks: multi ? [] : project.audio.tracks };
    return out;
  };

  return (
    <>
      <div className="topbar">
        <button className="btn ghost sm" onClick={() => go(multi ? '/library' : '/clip/' + c0.id)}><Icon name="back" size={15} /> Back</button>
        <h1 style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{multi ? `Montage · ${clips.length} clips` : c0.title}</h1>
        <span className="grow" />
        <button className="btn ghost sm icon" title="Undo (Ctrl+Z)" disabled={!hist.current.past.length} onClick={undo}><Icon name="undo" size={15} /></button>
        <button className="btn ghost sm icon" title="Redo (Ctrl+Y)" disabled={!hist.current.future.length} onClick={redo}><Icon name="redo" size={15} /></button>
        <button className="btn sm" onClick={resetAll}>Reset</button>
        <button className="btn primary sm" onClick={() => setTab('export')}><Icon name="export" size={14} /> Export</button>
      </div>
      <div className="content flush">
        <div className="editor">
          <div className="ed-stage">
            <div className="ed-canvas" ref={stageRef}>
              <div className="ed-frame" style={{ width: FW, height: FH }} onMouseDown={() => setSelText(null)}>
                {fit === 'blur' && <canvas ref={blurCanvas} width={160} height={90} className="blur-bg" style={{ width: FW + 80, height: FH + 80, filter: `blur(24px) brightness(.75) ${cssFilter}` }} />}
                <div style={{ position: 'absolute', left: (FW - boxW) / 2, top: (FH - boxH) / 2, width: boxW, height: boxH, filter: cssFilter }}>
                  <div style={{
                    position: 'absolute', width: innerW, height: innerH, left: (boxW - innerW) / 2, top: (boxH - innerH) / 2, overflow: 'hidden',
                    transform: `scale(${project.flipH && !cropMode ? -1 : 1}, ${project.flipV && !cropMode ? -1 : 1}) rotate(${rot}deg)`
                  }}>
                    {clips.map((c, i) => (
                      <video key={c.path} ref={(el) => (vids.current[i] = el)} src={mediaUrl(c.path)} preload="auto" playsInline
                        onLoadedMetadata={(e) => { if (i === segs[0]?.src) e.target.currentTime = segs[0].start; }}
                        style={{
                          width: multi ? innerW : srcW * s, height: multi ? innerH : srcH * s,
                          left: multi ? 0 : -crop.x * srcW * s, top: multi ? 0 : -crop.y * srcH * s,
                          objectFit: 'contain', visibility: segs[cur.current]?.src === i ? 'visible' : 'hidden'
                        }} />
                    ))}
                  </div>
                  {!cropMode && col.vignette > 0 && <div style={{ position: 'absolute', inset: 0, background: `radial-gradient(ellipse at center, transparent 45%, rgba(0,0,0,${col.vignette * 0.8}) 100%)` }} />}
                </div>
                {!cropMode && project.texts.map((t, i) => (time >= t.start && time <= t.end) || selText === i ? (
                  <TextLayer key={i} t={t} FW={FW} FH={FH} sel={selText === i}
                    onSelect={() => { setSelText(i); setTab('text'); }}
                    onDragStart={snapshot}
                    onMove={(x, y) => live((p) => ({ ...p, texts: p.texts.map((tt, k) => (k === i ? { ...tt, x, y } : tt)) }))} />
                ) : null)}
                {!cropMode && fadeOpacity > 0 && <div style={{ position: 'absolute', inset: 0, background: '#000', opacity: fadeOpacity, pointerEvents: 'none' }} />}
                {cropMode && <CropBox crop={project.crop || { x: 0, y: 0, w: 1, h: 1 }} FW={FW} FH={FH} srcAspect={srcW / srcH} lockAspect={project.aspect !== 'source' && project.fit === 'crop' ? project.aspect : null}
                  onStart={snapshot} onChange={(c) => live({ crop: c })} />}
              </div>
              {trackFiles.length > 1 && trackFiles.map((f, i) => <audio key={f} ref={(el) => (auds.current[i] = el)} src={mediaUrl(f)} preload="auto" />)}
              {project.audio.music && project.audio.music.path && <audio ref={musicRef} src={mediaUrl(project.audio.music.path)} preload="auto" />}
            </div>
            <div className="ed-controls">
              <button className="btn icon" onClick={() => (playing ? pause() : play())} title="Play/Pause (Space)"><Icon name={playing ? 'pause' : 'play'} fill="currentColor" size={16} /></button>
              <div className="ed-time">{fmtTime(time)} / {fmtTime(total)}</div>
              <button className="btn sm" onClick={split} title="Split at playhead (S)"><Icon name="scissors" size={14} /> Split</button>
              <button className="btn sm" onClick={setIn} title="Trim start of segment to playhead (I)">Set in</button>
              <button className="btn sm" onClick={setOut} title="Trim end of segment to playhead (O)">Set out</button>
              <span className="grow" />
              {cropMode && <span className="badge warn">Cropping — drag the box</span>}
              <Icon name="volume" size={15} className="dim" />
              <div style={{ width: 90 }}><Slider min={0} max={1} step={0.05} value={masterVol} onChange={setMasterVol} /></div>
            </div>
          </div>

          <Timeline project={project} segs={segs} offs={offs} total={total} time={time} zoom={zoom} setZoom={setZoom}
            selSeg={selSeg} setSelSeg={setSelSeg} selText={selText} setSelText={(i) => { setSelText(i); setTab('text'); }}
            seek={seek} setSeg={setSeg} snapshot={snapshot} film={film} clips={clips}
            bookmarks={multi ? [] : (c0.bookmarks || [])} split={split} delSeg={delSeg} />

          <div className="ed-panel">
            <div className="ed-tabs" style={{ gridTemplateColumns: 'repeat(6, 1fr)' }}>
              {TABS.map((t) => <button key={t.id} className={`ed-tab ${tab === t.id ? 'on' : ''}`} onClick={() => { setTab(t.id); if (t.id !== 'frame') setCropMode(false); }}><Icon name={t.icon} size={17} />{t.label}</button>)}
            </div>
            <div className="ed-body">
              {tab === 'trim' && selected && (
                <>
                  <div className="h3">Segment {selSeg + 1} of {segs.length}</div>
                  <div className="row">
                    <div className="field grow"><label>Start</label><input type="number" step="0.1" value={selected.start.toFixed(2)} onChange={(e) => setSeg(selSeg, { start: clamp(Number(e.target.value), 0, selected.end - 0.1) })} /></div>
                    <div className="field grow"><label>End</label><input type="number" step="0.1" value={selected.end.toFixed(2)} onChange={(e) => setSeg(selSeg, { end: clamp(Number(e.target.value), selected.start + 0.1, clips[selected.src].duration) })} /></div>
                  </div>
                  <div className="dim" style={{ fontSize: 12 }}>Source: {multi ? clips[selected.src].title : 'original clip'} · {fmtDur(selected.end - selected.start)} → {fmtTime(segDur(selected))} on timeline</div>
                  <div className="field">
                    <label>Speed <em>{selected.speed}x</em></label>
                    <div className="seg" style={{ flexWrap: 'wrap' }}>
                      {SPEEDS.map((sp) => <button key={sp} className={selected.speed === sp ? 'on accent' : ''} onClick={() => setSeg(selSeg, { speed: sp })}>{sp}x</button>)}
                    </div>
                    <div className="dim" style={{ fontSize: 12 }}>Tip: split around a highlight and set it to 0.5x or 0.25x for slow motion.</div>
                  </div>
                  <div className="row wrap">
                    <button className="btn sm" onClick={split}><Icon name="scissors" size={14} /> Split</button>
                    <button className="btn sm" onClick={dupSeg}><Icon name="copy" size={14} /> Duplicate</button>
                    <button className="btn sm" onClick={() => moveSeg(-1)} disabled={selSeg === 0}>← Move</button>
                    <button className="btn sm" onClick={() => moveSeg(1)} disabled={selSeg >= segs.length - 1}>Move →</button>
                    <button className="btn sm danger" onClick={() => delSeg()}><Icon name="trash" size={14} /> Delete</button>
                  </div>
                  <div className="h3" style={{ marginTop: 8 }}>Quick trim</div>
                  <div className="row wrap">
                    {[5, 10, 15, 30, 60].map((n) => (
                      <button key={n} className="btn sm" onClick={() => commit((p) => {
                        const src = clips[0].duration;
                        return { ...p, segments: [{ src: 0, start: Math.max(0, src - n), end: src, speed: 1 }] };
                      })}>Last {n}s</button>
                    ))}
                  </div>
                  <div className="dim" style={{ fontSize: 12, lineHeight: 1.6 }}>
                    <kbd>Space</kbd> play · <kbd>S</kbd> split · <kbd>I</kbd>/<kbd>O</kbd> set in/out · <kbd>Del</kbd> delete segment · <kbd>←</kbd>/<kbd>→</kbd> frame step (<kbd>Shift</kbd> 5s) · <kbd>Ctrl+Z</kbd> undo
                  </div>
                </>
              )}

              {tab === 'frame' && (
                <>
                  <div className="field"><label>Aspect ratio</label><Select value={project.aspect} options={ASPECTS} onChange={(v) => commit({ aspect: v })} /></div>
                  {project.aspect !== 'source' && (
                    <div className="field"><label>Fit</label>
                      <Seg value={project.fit} onChange={(v) => commit({ fit: v })} options={[{ value: 'crop', label: 'Fill (crop)' }, { value: 'blur', label: 'Blur bars' }, { value: 'pad', label: 'Black bars' }]} />
                    </div>
                  )}
                  <div className="field"><label>Crop {project.crop && <em>{Math.round(project.crop.w * srcW)}×{Math.round(project.crop.h * srcH)}</em>}</label>
                    <div className="row">
                      <button className={`btn sm ${cropMode ? 'primary' : ''}`} disabled={multi} onClick={() => { if (!cropMode && !project.crop) commit({ crop: { x: 0.1, y: 0.1, w: 0.8, h: 0.8 } }); setCropMode(!cropMode); }}><Icon name="crop" size={14} /> {cropMode ? 'Done cropping' : 'Edit crop'}</button>
                      {project.crop && <button className="btn sm" onClick={() => { commit({ crop: null }); setCropMode(false); }}>Remove crop</button>}
                    </div>
                    {multi && <div className="dim" style={{ fontSize: 12 }}>Crop is available for single clips.</div>}
                    <div className="row wrap" style={{ marginTop: 4 }}>
                      {[['Center 9:16', 9 / 16], ['Center 1:1', 1], ['Center 4:5', 4 / 5]].map(([l, a]) => (
                        <button key={l} className="btn sm ghost" disabled={multi} onClick={() => { const w = Math.min(1, (a * srcH) / srcW); commit({ crop: { x: (1 - w) / 2, y: 0, w, h: 1 } }); }}>{l}</button>
                      ))}
                      {[['Zoom 1.25x', 0.8], ['Zoom 1.5x', 0.6667], ['Zoom 2x', 0.5]].map(([l, z]) => (
                        <button key={l} className="btn sm ghost" disabled={multi} onClick={() => commit({ crop: { x: (1 - z) / 2, y: (1 - z) / 2, w: z, h: z } })}>{l}</button>
                      ))}
                    </div>
                  </div>
                  <div className="field"><label>Rotate</label>
                    <Seg value={project.rotate} onChange={(v) => commit({ rotate: v })} options={[{ value: 0, label: '0°' }, { value: 90, label: '90°' }, { value: 180, label: '180°' }, { value: 270, label: '270°' }]} />
                  </div>
                  <div className="row">
                    <label className="row grow" style={{ cursor: 'pointer' }}><Toggle checked={project.flipH} onChange={(v) => commit({ flipH: v })} /> Flip horizontal</label>
                    <label className="row grow" style={{ cursor: 'pointer' }}><Toggle checked={project.flipV} onChange={(v) => commit({ flipV: v })} /> Flip vertical</label>
                  </div>
                </>
              )}

              {tab === 'color' && (
                <>
                  <div className="field"><label>Looks</label>
                    <div className="row wrap" style={{ gap: 6 }}>
                      {LOOKS.map((l) => <button key={l.name} className="btn sm" onClick={() => commit({ color: { ...l.c } })}>{l.name}</button>)}
                    </div>
                  </div>
                  {[
                    ['brightness', 'Brightness', -0.3, 0.3, 0.01],
                    ['contrast', 'Contrast', 0.5, 1.8, 0.01],
                    ['saturation', 'Saturation', 0, 2.5, 0.01],
                    ['gamma', 'Gamma (export only)', 0.5, 2, 0.01],
                    ['hue', 'Hue shift', -60, 60, 1],
                    ['sharpen', 'Sharpen (export only)', 0, 1.5, 0.05],
                    ['vignette', 'Vignette', 0, 1, 0.05]
                  ].map(([k, l, mn, mx, st]) => (
                    <div className="field" key={k}>
                      <label>{l} <em onDoubleClick={() => commit((p) => ({ ...p, color: { ...p.color, [k]: DEFAULT_COLOR[k] } }))} title="Double-click to reset">{Number(project.color[k]).toFixed(2)}</em></label>
                      <Slider min={mn} max={mx} step={st} value={project.color[k]} onChange={(v) => live((p) => ({ ...p, color: { ...p.color, [k]: v } }))} onStart={snapshot} />
                    </div>
                  ))}
                  <div className="h3">Transitions</div>
                  <div className="field"><label>Fade in <em>{project.fadeIn.toFixed(1)}s</em></label><Slider min={0} max={3} step={0.1} value={project.fadeIn} onChange={(v) => live({ fadeIn: v })} onStart={snapshot} /></div>
                  <div className="field"><label>Fade out <em>{project.fadeOut.toFixed(1)}s</em></label><Slider min={0} max={3} step={0.1} value={project.fadeOut} onChange={(v) => live({ fadeOut: v })} onStart={snapshot} /></div>
                </>
              )}

              {tab === 'text' && <TextPanel project={project} commit={commit} live={live} snapshot={snapshot} selText={selText} setSelText={setSelText} time={time} total={total} />}

              {tab === 'audio' && <AudioPanel project={project} commit={commit} live={live} snapshot={snapshot} multi={multi} hasTrackPreview={trackFiles.length > 1} />}

              {tab === 'export' && <ExportPanel project={project} commit={commit} clips={clips} total={total} exportProject={exportProject} multi={multi} />}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

// ---------------- timeline ----------------
function Timeline({ project, segs, offs, total, time, zoom, setZoom, selSeg, setSelSeg, selText, setSelText, seek, setSeg, snapshot, film, clips, bookmarks, split, delSeg }) {
  const wrap = useRef();
  const [w, setW] = useState(800);
  useEffect(() => {
    const ro = new ResizeObserver(() => setW(wrap.current.clientWidth));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);
  const width = Math.max(w, w * zoom);
  const pps = total > 0 ? width / total : 1;
  const step = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800].find((s) => s * pps >= 70) || 3600;
  const ticks = [];
  for (let t = 0; t <= total + 1e-6; t += step) ticks.push(t);

  const scrub = (e) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const move = (ev) => seek((ev.clientX - rect.left) / pps);
    move(e);
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  const dragHandle = (e, i, side) => {
    e.stopPropagation();
    e.preventDefault();
    snapshot();
    const s0 = { ...segs[i] };
    const x0 = e.clientX;
    const srcDur = clips[s0.src].duration;
    const move = (ev) => {
      const ds = ((ev.clientX - x0) / pps) * (s0.speed || 1);
      if (side === 'l') setSeg(i, { start: clamp(s0.start + ds, 0, s0.end - 0.1) }, true);
      else setSeg(i, { end: clamp(s0.end + ds, s0.start + 0.1, srcDur) }, true);
    };
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };

  // bookmarks (source time) → output time
  const marks = [];
  for (const b of bookmarks) {
    segs.forEach((s, i) => {
      if (s.src === 0 && b.time >= s.start && b.time <= s.end) marks.push({ ...b, t: offs[i].outStart + (b.time - s.start) / (s.speed || 1) });
    });
  }

  return (
    <div className="timeline">
      <div className="tl-tools">
        <span className="dim" style={{ fontSize: 12 }}>{segs.length} segment{segs.length > 1 ? 's' : ''} · {fmtDur(total)}</span>
        <span className="grow" />
        <button className="btn ghost sm" onClick={split}><Icon name="scissors" size={14} /> Split</button>
        <button className="btn ghost sm" onClick={() => delSeg()}><Icon name="trash" size={14} /> Delete</button>
        <button className="btn ghost sm icon" onClick={() => setZoom(Math.max(1, zoom / 1.5))}><Icon name="zoomOut" size={15} /></button>
        <div style={{ width: 90 }}><Slider min={1} max={20} step={0.1} value={zoom} onChange={setZoom} /></div>
        <button className="btn ghost sm icon" onClick={() => setZoom(Math.min(20, zoom * 1.5))}><Icon name="zoomIn" size={15} /></button>
      </div>
      <div className="tl-wrap" ref={wrap}>
        <div className="tl" style={{ width }}>
          <div className="tl-ruler" onMouseDown={scrub}>
            {ticks.map((t) => <React.Fragment key={t}><span style={{ left: t * pps }}>{fmtDur(t)}</span><i style={{ left: t * pps }} /></React.Fragment>)}
          </div>
          <div className="tl-track" onMouseDown={scrub}>
            {segs.map((s, i) => {
              const f = film[clips[s.src].path] || [];
              const dur = clips[s.src].duration || 1;
              const thumbs = f.filter((_, k) => { const tt = (k / Math.max(1, f.length)) * dur; return tt >= s.start - dur / f.length && tt <= s.end; });
              return (
                <div key={i} className={`tl-seg ${selSeg === i ? 'sel' : ''}`} style={{ left: offs[i].outStart * pps, width: Math.max(4, segDur(s) * pps - 2) }}
                  onMouseDown={(e) => { e.stopPropagation(); setSelSeg(i); scrub(e); }}>
                  <div className="tl-filmstrip">{thumbs.slice(0, Math.max(1, Math.ceil((segDur(s) * pps) / 60))).map((th) => <img key={th} src={mediaUrl(th)} alt="" />)}</div>
                  <span className="lbl">{s.speed !== 1 ? `${s.speed}x · ` : ''}{fmtDur(segDur(s))}{clips.length > 1 ? ` · ${clips[s.src].title}` : ''}</span>
                  <div className="tl-handle l" onMouseDown={(e) => dragHandle(e, i, 'l')} />
                  <div className="tl-handle r" onMouseDown={(e) => dragHandle(e, i, 'r')} />
                </div>
              );
            })}
          </div>
          <div className="tl-texts">
            {project.texts.map((t, i) => (
              <div key={i} className={`tl-text ${selText === i ? 'sel' : ''}`} style={{ left: t.start * pps, width: Math.max(10, (t.end - t.start) * pps) }} onMouseDown={(e) => { e.stopPropagation(); setSelText(i); }}>T {t.text}</div>
            ))}
          </div>
          {marks.map((m, i) => <div key={i} className="tl-mark" style={{ left: m.t * pps }} title={m.label} onMouseDown={() => seek(m.t)} />)}
          <div className="tl-head" style={{ left: time * pps }} />
        </div>
      </div>
    </div>
  );
}

// ---------------- crop box ----------------
function CropBox({ crop, FW, FH, onChange, onStart, lockAspect }) {
  const ar = lockAspect ? (() => { const [a, b] = lockAspect.split(':').map(Number); return a / b; })() : null;
  const drag = (e, mode) => {
    e.stopPropagation();
    e.preventDefault();
    onStart();
    const c0 = { ...crop };
    const x0 = e.clientX; const y0 = e.clientY;
    const move = (ev) => {
      const dx = (ev.clientX - x0) / FW;
      const dy = (ev.clientY - y0) / FH;
      let { x, y, w, h } = c0;
      if (mode === 'move') { x = clamp(x + dx, 0, 1 - w); y = clamp(y + dy, 0, 1 - h); }
      else {
        if (mode.includes('l')) { const nx = clamp(x + dx, 0, x + w - 0.05); w += x - nx; x = nx; }
        if (mode.includes('r')) w = clamp(w + dx, 0.05, 1 - x);
        if (mode.includes('t')) { const ny = clamp(y + dy, 0, y + h - 0.05); h += y - ny; y = ny; }
        if (mode.includes('b')) h = clamp(h + dy, 0.05, 1 - y);
        if (ar) {
          // keep pixel aspect: (w*FW)/(h*FH) = ar
          h = clamp((w * FW) / ar / FH, 0.05, 1 - y);
          w = (h * FH * ar) / FW;
        }
      }
      onChange({ x, y, w, h });
    };
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };
  const hs = { tl: { left: -6, top: -6, cursor: 'nwse-resize' }, tr: { right: -6, top: -6, cursor: 'nesw-resize' }, bl: { left: -6, bottom: -6, cursor: 'nesw-resize' }, br: { right: -6, bottom: -6, cursor: 'nwse-resize' } };
  return (
    <div className="crop-box" style={{ left: crop.x * FW, top: crop.y * FH, width: crop.w * FW, height: crop.h * FH }} onMouseDown={(e) => drag(e, 'move')}>
      {Object.entries(hs).map(([k, st]) => <i key={k} style={st} onMouseDown={(e) => drag(e, k)} />)}
    </div>
  );
}

// ---------------- text ----------------
function TextLayer({ t, FW, FH, sel, onSelect, onMove, onDragStart }) {
  const ref = useRef();
  const size = (t.size / 1080) * FH;
  const down = (e) => {
    e.stopPropagation();
    onSelect();
    onDragStart();
    const r = ref.current.getBoundingClientRect();
    const x0 = e.clientX; const y0 = e.clientY;
    const tx = t.x; const ty = t.y;
    const move = (ev) => {
      const nx = clamp(tx + (ev.clientX - x0) / Math.max(1, FW - r.width), 0, 1);
      const ny = clamp(ty + (ev.clientY - y0) / Math.max(1, FH - r.height), 0, 1);
      onMove(nx, ny);
    };
    const up = () => { window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };
  const hexA = (hex, a) => { const n = parseInt((hex || '#000000').slice(1), 16); return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`; };
  return (
    <div ref={ref} className={`ed-text ${sel ? 'sel' : ''}`} onMouseDown={down}
      style={{
        left: `${t.x * 100}%`, top: `${t.y * 100}%`, transform: `translate(-${t.x * 100}%, -${t.y * 100}%)`,
        fontSize: size, color: t.color,
        background: t.box ? hexA(t.boxColor, t.boxOpacity) : 'transparent', padding: t.box ? size / 4 : 0,
        WebkitTextStroke: t.outline ? `${Math.max(1, size / 18)}px #000` : undefined, paintOrder: 'stroke fill'
      }}>{t.text}</div>
  );
}

function TextPanel({ project, commit, live, snapshot, selText, setSelText, time, total }) {
  const t = selText !== null ? project.texts[selText] : null;
  const set = (patch, isLive) => (isLive ? live : commit)((p) => ({ ...p, texts: p.texts.map((x, i) => (i === selText ? { ...x, ...patch } : x)) }));
  const add = (preset) => {
    const base = { text: 'Your text', start: time, end: Math.min(total, time + 3), x: 0.5, y: 0.85, size: 72, color: '#ffffff', box: false, boxColor: '#000000', boxOpacity: 0.55, outline: true };
    const n = { ...base, ...preset };
    commit((p) => ({ ...p, texts: [...p.texts, n] }));
    setSelText(project.texts.length);
  };
  return (
    <>
      <div className="field"><label>Add text</label>
        <div className="row wrap" style={{ gap: 6 }}>
          <button className="btn sm" onClick={() => add({})}><Icon name="plus" size={14} /> Caption</button>
          <button className="btn sm" onClick={() => add({ text: 'TITLE', y: 0.12, size: 110, start: 0, end: Math.min(total, 3) })}>Title</button>
          <button className="btn sm" onClick={() => add({ text: 'Label', box: true, outline: false, size: 54, y: 0.08, x: 0.04, color: '#ffffff' })}>Boxed label</button>
          <button className="btn sm" onClick={() => add({ text: '@yourname', size: 40, x: 0.97, y: 0.95, start: 0, end: total, outline: true })}>Watermark</button>
        </div>
      </div>
      {project.texts.length > 0 && (
        <div className="field"><label>Layers</label>
          <div className="card">
            {project.texts.map((x, i) => (
              <div key={i} className="row" style={{ padding: '7px 10px', borderTop: i ? '1px solid var(--line)' : 0, background: selText === i ? 'var(--bg-4)' : undefined, cursor: 'pointer' }} onClick={() => setSelText(i)}>
                <Icon name="text" size={14} /><span className="grow" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{x.text}</span><span className="dim" style={{ fontSize: 11 }}>{fmtDur(x.start)}–{fmtDur(x.end)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      {t && (
        <>
          <div className="field"><label>Text</label><textarea rows={2} value={t.text} onFocus={snapshot} onChange={(e) => set({ text: e.target.value }, true)} /></div>
          <div className="row">
            <div className="field grow"><label>Start</label><input type="number" step="0.1" value={t.start.toFixed(1)} onChange={(e) => set({ start: clamp(Number(e.target.value), 0, t.end - 0.1) })} /></div>
            <div className="field grow"><label>End</label><input type="number" step="0.1" value={t.end.toFixed(1)} onChange={(e) => set({ end: clamp(Number(e.target.value), t.start + 0.1, total) })} /></div>
          </div>
          <div className="row">
            <button className="btn sm grow" onClick={() => set({ start: time })}>Start at playhead</button>
            <button className="btn sm grow" onClick={() => set({ end: Math.max(t.start + 0.1, time) })}>End at playhead</button>
          </div>
          <button className="btn sm" onClick={() => set({ start: 0, end: total })}>Show for whole video</button>
          <div className="field"><label>Size <em>{t.size}</em></label><Slider min={16} max={220} value={t.size} onChange={(v) => set({ size: v }, true)} onStart={snapshot} /></div>
          <div className="row">
            <div className="field"><label>Color</label><input type="color" value={t.color} onChange={(e) => set({ color: e.target.value }, true)} style={{ width: 54, height: 32, padding: 2, background: 'var(--bg-3)', border: '1px solid var(--line-2)', borderRadius: 8 }} /></div>
            <label className="row grow" style={{ cursor: 'pointer', marginTop: 18 }}><Toggle checked={t.outline} onChange={(v) => set({ outline: v })} /> Outline</label>
            <label className="row grow" style={{ cursor: 'pointer', marginTop: 18 }}><Toggle checked={t.box} onChange={(v) => set({ box: v })} /> Box</label>
          </div>
          {t.box && (
            <div className="row">
              <input type="color" value={t.boxColor} onChange={(e) => set({ boxColor: e.target.value }, true)} style={{ width: 54, height: 32, padding: 2, background: 'var(--bg-3)', border: '1px solid var(--line-2)', borderRadius: 8 }} />
              <div className="grow"><Slider min={0} max={1} step={0.05} value={t.boxOpacity} onChange={(v) => set({ boxOpacity: v }, true)} onStart={snapshot} /></div>
            </div>
          )}
          <div className="field"><label>Position</label>
            <div className="row wrap" style={{ gap: 6 }}>
              {[['Top', 0.5, 0.06], ['Center', 0.5, 0.5], ['Bottom', 0.5, 0.92], ['Top left', 0.04, 0.06], ['Top right', 0.96, 0.06], ['Bottom right', 0.96, 0.94]].map(([l, x, y]) => <button key={l} className="btn sm ghost" onClick={() => set({ x, y })}>{l}</button>)}
            </div>
            <div className="dim" style={{ fontSize: 12 }}>Or drag the text in the preview.</div>
          </div>
          <button className="btn sm danger" onClick={() => { commit((p) => ({ ...p, texts: p.texts.filter((_, i) => i !== selText) })); setSelText(null); }}><Icon name="trash" size={14} /> Delete text</button>
        </>
      )}
    </>
  );
}

// ---------------- audio ----------------
function AudioPanel({ project, commit, live, snapshot, multi, hasTrackPreview }) {
  const a = project.audio;
  const setTrack = (i, patch, isLive) => (isLive ? live : commit)((p) => ({ ...p, audio: { ...p.audio, tracks: p.audio.tracks.map((t, k) => (k === i ? { ...t, ...patch } : t)) } }));
  const setMusic = (patch, isLive) => (isLive ? live : commit)((p) => ({ ...p, audio: { ...p.audio, music: p.audio.music ? { ...p.audio.music, ...patch } : null } }));
  const NAMES = ['Mix (all audio)', 'Game / desktop', 'Microphone', 'Discord'];
  return (
    <>
      {multi ? <div className="muted" style={{ fontSize: 12.5 }}>Montages use each clip’s mixed audio track.</div> : a.tracks.length === 0 ? <div className="muted">This clip has no audio.</div> : (
        <>
          <div className="h3">Tracks</div>
          {a.tracks.length > 1 && <div className="dim" style={{ fontSize: 12 }}>Mettaloid records separate tracks so you can balance them. Track 1 is the combined mix — keep it muted when using the separate tracks to avoid doubled audio.</div>}
          {a.tracks.map((t, i) => (
            <div key={i} className="card" style={{ padding: 12 }}>
              <div className="row" style={{ marginBottom: 8 }}>
                <b className="grow">{a.tracks.length > 1 ? NAMES[i] || t.title : 'Audio'}</b>
                <button className={`btn sm icon ${t.muted ? 'danger' : ''}`} onClick={() => setTrack(i, { muted: !t.muted })} title={t.muted ? 'Unmute' : 'Mute'}><Icon name={t.muted ? 'micOff' : 'volume'} size={14} /></button>
              </div>
              <div className="row"><div className="grow"><Slider min={0} max={2} step={0.05} value={t.volume} onChange={(v) => setTrack(i, { volume: v }, true)} onStart={snapshot} /></div><span className="dim mono" style={{ width: 44, textAlign: 'right' }}>{Math.round(t.volume * 100)}%</span></div>
            </div>
          ))}
          {!hasTrackPreview && a.tracks.length > 1 && <div className="dim" style={{ fontSize: 12 }}>Preparing per-track preview…</div>}
        </>
      )}
      <label className="row" style={{ cursor: 'pointer' }}><Toggle checked={a.normalize} onChange={(v) => commit((p) => ({ ...p, audio: { ...p.audio, normalize: v } }))} /> Normalize loudness (export)</label>
      <div className="h3" style={{ marginTop: 6 }}>Music</div>
      {!a.music ? (
        <button className="btn sm" onClick={async () => {
          const f = await invoke('dialog:pickFile', { title: 'Choose music', filters: [{ name: 'Audio', extensions: ['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac'] }] });
          if (f) commit((p) => ({ ...p, audio: { ...p.audio, music: { path: f, volume: 0.4, start: 0, loop: true } } }));
        }}><Icon name="plus" size={14} /> Add background music</button>
      ) : (
        <div className="card" style={{ padding: 12 }}>
          <div className="row"><b className="grow" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 12.5 }}>{a.music.path.split(/[\\/]/).pop()}</b><button className="btn sm danger icon" onClick={() => commit((p) => ({ ...p, audio: { ...p.audio, music: null } }))}><Icon name="trash" size={14} /></button></div>
          <div className="field" style={{ marginTop: 8 }}><label>Volume <em>{Math.round(a.music.volume * 100)}%</em></label><Slider min={0} max={1.5} step={0.05} value={a.music.volume} onChange={(v) => setMusic({ volume: v }, true)} onStart={snapshot} /></div>
          <div className="row" style={{ marginTop: 8 }}>
            <div className="field grow"><label>Starts at (s)</label><input type="number" step="0.5" min="0" value={a.music.start} onChange={(e) => setMusic({ start: Math.max(0, Number(e.target.value)) })} /></div>
            <label className="row" style={{ cursor: 'pointer', marginTop: 18 }}><Toggle checked={a.music.loop} onChange={(v) => setMusic({ loop: v })} /> Loop</label>
          </div>
        </div>
      )}
    </>
  );
}

// ---------------- export ----------------
function ExportPanel({ project, commit, clips, total, exportProject, multi }) {
  const { toast, go } = useApp();
  const o = project.output;
  const set = (patch) => commit((p) => ({ ...p, output: { ...p.output, ...patch } }));
  const [title, setTitle] = useState(multi ? `Montage ${new Date().toLocaleDateString()}` : `${clips[0].title} (edit)`);
  const [est, setEst] = useState(null);
  const canCopy = !multi && project.segments.length === 1 && project.segments[0].speed === 1 && !project.crop && project.rotate === 0 && !project.flipH && !project.flipV &&
    project.aspect === 'source' && project.texts.length === 0 && !project.fadeIn && !project.fadeOut && JSON.stringify(project.color) === JSON.stringify(DEFAULT_COLOR) &&
    !project.audio.music && !project.audio.normalize && o.resolution === 'source' && o.fps === 'source' && o.format === 'mp4' && !o.targetMB;
  useEffect(() => {
    let alive = true;
    invoke('media:estimate', exportProject()).then((e) => alive && setEst(e)).catch(() => {});
    return () => { alive = false; };
  }, [JSON.stringify(project)]);

  const doExport = async () => {
    const p = exportProject();
    if (o.mode === 'copy' && !canCopy) p.output = { ...p.output, mode: 'encode' };
    const job = await invoke('media:render', p, { meta: { title, parentId: multi ? null : clips[0].id, kind: 'edit' } });
    toast({ title: 'Export started', subtitle: job.title });
  };

  return (
    <>
      <div className="field"><label>File name</label><input type="text" value={title} onChange={(e) => setTitle(e.target.value)} /></div>
      <div className="field"><label>Format</label>
        <Seg value={o.format} onChange={(v) => set({ format: v })} options={[{ value: 'mp4', label: 'MP4' }, { value: 'webm', label: 'WebM' }, { value: 'mkv', label: 'MKV' }, { value: 'gif', label: 'GIF' }]} />
      </div>
      <div className="row">
        <div className="field grow"><label>Resolution</label>
          <Select value={o.resolution} onChange={(v) => set({ resolution: v })} options={[{ value: 'source', label: 'Original' }, { value: 2160, label: '2160p (4K)' }, { value: 1440, label: '1440p' }, { value: 1080, label: '1080p' }, { value: 720, label: '720p' }, { value: 480, label: '480p' }, { value: 360, label: '360p' }]} />
        </div>
        <div className="field grow"><label>Frame rate</label>
          <Select value={o.fps} onChange={(v) => set({ fps: v })} options={[{ value: 'source', label: 'Original' }, { value: 144, label: '144' }, { value: 120, label: '120' }, { value: 60, label: '60' }, { value: 30, label: '30' }, { value: 24, label: '24' }]} />
        </div>
      </div>
      {o.format !== 'gif' && (
        <div className="field"><label>File size limit</label>
          <div className="seg" style={{ flexWrap: 'wrap' }}>
            <button className={!o.targetMB ? 'on accent' : ''} onClick={() => set({ targetMB: 0 })}>None</button>
            {SIZE_PRESETS.map((p) => <button key={p.mb} className={o.targetMB === p.mb ? 'on accent' : ''} onClick={() => set({ targetMB: p.mb })}>{p.mb} MB</button>)}
          </div>
          <div className="row"><span className="dim" style={{ fontSize: 12 }}>Custom:</span><input type="number" min="1" style={{ width: 90 }} value={o.targetMB || ''} placeholder="MB" onChange={(e) => set({ targetMB: Math.max(0, Number(e.target.value)) })} /></div>
        </div>
      )}
      {!o.targetMB && o.format !== 'gif' && (
        <div className="field"><label>Quality</label><Seg value={o.quality} onChange={(v) => set({ quality: v })} options={[{ value: 'high', label: 'High' }, { value: 'medium', label: 'Medium' }, { value: 'low', label: 'Small' }]} /></div>
      )}
      {(o.format === 'mp4' || o.format === 'mkv') && (
        <div className="row">
          <div className="field grow"><label>Encoder</label><Select value={o.encoder} onChange={(v) => set({ encoder: v })} options={[{ value: 'auto', label: 'Auto (GPU)' }, { value: 'x264', label: 'CPU (best size accuracy)' }, { value: 'nvenc', label: 'NVIDIA' }, { value: 'amf', label: 'AMD' }, { value: 'qsv', label: 'Intel' }]} /></div>
          <div className="field grow"><label>Codec</label><Select value={o.codec} onChange={(v) => set({ codec: v })} options={[{ value: 'h264', label: 'H.264 (compatible)' }, { value: 'hevc', label: 'HEVC (smaller)' }]} /></div>
        </div>
      )}
      {canCopy && (
        <label className="row" style={{ cursor: 'pointer' }}><Toggle checked={o.mode === 'copy'} onChange={(v) => set({ mode: v ? 'copy' : 'encode' })} /> <span>Lossless fast trim <span className="dim">(no re-encode, cuts on keyframes)</span></span></label>
      )}
      {est && (
        <div className="card" style={{ padding: 12, fontSize: 12.5 }}>
          <div className="kv">
            <span>Duration</span><span>{fmtDur(est.duration)}</span>
            <span>Output</span><span>{est.W}×{est.H} @ {Math.round(est.fps)} fps</span>
            {est.videoKbps && <><span>Bitrate</span><span>{est.videoKbps} kbps video + {est.audioKbps} kbps audio</span></>}
            {o.targetMB > 0 && <><span>Target</span><span>&lt; {o.targetMB} MB</span></>}
          </div>
        </div>
      )}
      <button className="btn primary lg" onClick={doExport} disabled={total <= 0.05}><Icon name="export" /> Export {o.format.toUpperCase()}</button>
      <div className="dim" style={{ fontSize: 12 }}>Exports are saved to <span className="mono">Exports</span> in your clip folder and added to the library. Your original clip is never modified.</div>
    </>
  );
}

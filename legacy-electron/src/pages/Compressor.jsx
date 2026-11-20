import React, { useEffect, useMemo, useState } from 'react';
import { useApp } from '../App';
import { invoke, mediaUrl, pathForFile, fmtDur, fmtSize, useSettings } from '../api';
import { Icon, Row, Section, Select, Toggle, Seg, Bar } from '../components/ui';

export const SIZE_PRESETS = [
  { mb: 20, label: 'Discord / chat', hint: 'Fits most chat upload limits' },
  { mb: 50, label: 'Discord Nitro Basic', hint: 'Good quality for short clips' },
  { mb: 500, label: 'Large / high quality', hint: 'Near-original for long clips' }
];

export default function Compressor({ id }) {
  const { lib, route, toast, jobs } = useApp();
  const [settings] = useSettings();
  const [files, setFiles] = useState([]);
  const [target, setTarget] = useState(null);
  const [custom, setCustom] = useState('');
  const [opts, setOpts] = useState(null);
  const [over, setOver] = useState(false);
  const [estimates, setEstimates] = useState({});

  useEffect(() => {
    if (!settings || opts) return;
    const c = settings.compressor;
    setTarget(c.defaultTargetMB);
    setOpts({ encoder: c.encoder, twoPass: c.twoPass, autoDownscale: c.autoDownscale, audioKbps: c.audioKbps, fps: 'source', resolution: 'source', codec: 'h264', format: 'mp4', audioMode: 'mix' });
  }, [settings]);

  // preload from route
  useEffect(() => {
    const ids = id ? [id] : (route.query.get('ids') || '').split(',').filter(Boolean);
    const list = ids.map((x) => lib.clips.find((c) => c.id === x)).filter((c) => c && c.type !== 'screenshot');
    if (list.length) setFiles(list.map((c) => ({ path: c.path, title: c.title, duration: c.duration, size: c.size, thumb: c.thumb, clipId: c.id, game: c.game, audioTracks: c.audioTracks, width: c.width, height: c.height, start: 0, end: c.duration })));
  }, [id, route.query.get('ids'), lib.clips.length]);

  const addPaths = async (paths) => {
    const out = [];
    for (const p of paths) {
      if (!/\.(mp4|mkv|mov|webm|avi|flv|ts|m4v)$/i.test(p)) continue;
      const known = lib.clips.find((c) => c.path.toLowerCase() === p.toLowerCase());
      if (known) { out.push({ path: known.path, title: known.title, duration: known.duration, size: known.size, thumb: known.thumb, clipId: known.id, game: known.game, audioTracks: known.audioTracks, width: known.width, height: known.height, start: 0, end: known.duration }); continue; }
      try {
        const info = await invoke('media:probe', p);
        out.push({ path: p, title: p.split(/[\\/]/).pop().replace(/\.[^.]+$/, ''), duration: info.duration, size: info.size, thumb: null, clipId: null, audioTracks: info.audioTracks, width: info.width, height: info.height, start: 0, end: info.duration });
      } catch {
        toast({ title: 'Could not read file', subtitle: p, kind: 'err' });
      }
    }
    setFiles((f) => [...f, ...out.filter((o) => !f.some((x) => x.path === o.path))]);
  };

  const targetMB = target === 'custom' ? Number(custom) || 0 : target;

  const projectFor = (f) => {
    const multiTrack = f.audioTracks && f.audioTracks.length > 1;
    let tracks = [];
    if (multiTrack && opts.audioMode === 'nomic') tracks = [{ index: 1, volume: 1 }];
    else if (multiTrack && opts.audioMode === 'mix') tracks = [{ index: 0, volume: 1 }];
    else if (opts.audioMode === 'none') tracks = (f.audioTracks || []).map((_, i) => ({ index: i, muted: true }));
    return {
      sources: [f.path],
      segments: [{ src: 0, start: f.start || 0, end: f.end || f.duration, speed: 1 }],
      crop: null, rotate: 0, aspect: 'source', fit: 'crop', color: {}, texts: [], fadeIn: 0, fadeOut: 0,
      audio: { tracks, normalize: false, music: null },
      output: {
        format: opts.format, codec: opts.codec, encoder: opts.encoder, twoPass: opts.twoPass, autoDownscale: opts.autoDownscale,
        audioKbps: opts.audioKbps, fps: opts.fps, resolution: opts.resolution, targetMB, quality: 'medium'
      }
    };
  };

  useEffect(() => {
    if (!opts || !targetMB) return;
    let alive = true;
    (async () => {
      const e = {};
      for (const f of files) {
        try { e[f.path] = await invoke('media:estimate', projectFor(f)); } catch {}
      }
      if (alive) setEstimates(e);
    })();
    return () => { alive = false; };
  }, [files, opts, targetMB]);

  const start = async () => {
    for (const f of files) {
      if (f.size && f.size <= targetMB * 1e6 * 0.98 && (f.start || 0) === 0 && f.end >= f.duration - 0.01 && opts.resolution === 'source' && opts.fps === 'source') {
        toast({ title: `${f.title} is already under ${targetMB} MB`, subtitle: 'Compressing anyway' });
      }
      await invoke('media:render', projectFor(f), { meta: { title: `${f.title} (${targetMB}MB)`, parentId: f.clipId, game: f.game || null, kind: 'compress' } });
    }
    toast({ title: `Compressing ${files.length} file(s)`, subtitle: 'Progress is shown in the sidebar' });
  };

  const myJobs = useMemo(() => jobs.filter((j) => /\(\d+(\.\d+)?MB\)/.test(j.title)).slice(0, 8), [jobs]);
  if (!opts) return null;

  return (
    <>
      <div className="topbar"><h1>Compressor</h1><span className="dim">Shrink clips to an exact file size</span></div>
      <div className="content" onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); addPaths([...e.dataTransfer.files].map(pathForFile)); }}>
        <div style={{ maxWidth: 980, margin: '0 auto' }}>
          <div className="h3" style={{ marginBottom: 10 }}>Target size</div>
          <div className="size-presets">
            {SIZE_PRESETS.map((p) => (
              <button key={p.mb} className={`size-preset ${target === p.mb ? 'on' : ''}`} onClick={() => setTarget(p.mb)}>
                <b>{p.mb} MB</b><div style={{ fontWeight: 600 }}>{p.label}</div><small>{p.hint}</small>
              </button>
            ))}
            <div className={`size-preset ${target === 'custom' ? 'on' : ''}`} onClick={() => setTarget('custom')}>
              <div className="row"><input type="number" min="1" placeholder="Custom" value={custom} onChange={(e) => { setCustom(e.target.value); setTarget('custom'); }} style={{ width: 120, fontSize: 18, fontWeight: 800 }} /><b style={{ fontSize: 18 }}>MB</b></div>
              <div style={{ fontWeight: 600, marginTop: 6 }}>Custom</div><small>Any size you need</small>
            </div>
          </div>

          <div className="row" style={{ margin: '24px 0 10px' }}>
            <div className="h3 grow">Files</div>
            {files.length > 0 && <button className="btn sm ghost" onClick={() => setFiles([])}>Clear</button>}
            <button className="btn sm" onClick={async () => { const p = await invoke('dialog:pickFile', { title: 'Choose video', filters: [{ name: 'Video', extensions: ['mp4', 'mkv', 'mov', 'webm', 'avi', 'flv', 'm4v', 'ts'] }] }); if (p) addPaths([p]); }}><Icon name="plus" size={14} /> Add file</button>
          </div>
          {files.length === 0 ? (
            <div className={`drop ${over ? 'over' : ''}`} onClick={async () => { const p = await invoke('dialog:pickFile', { title: 'Choose video', filters: [{ name: 'Video', extensions: ['mp4', 'mkv', 'mov', 'webm', 'avi', 'flv', 'm4v', 'ts'] }] }); if (p) addPaths([p]); }}>
              <Icon name="compress" size={30} /><div style={{ fontWeight: 700, marginTop: 8 }}>Drop videos here or click to browse</div>
              <div className="dim" style={{ marginTop: 4 }}>You can also select clips in the Library and choose “Compress”.</div>
            </div>
          ) : (
            <div className="card">
              {files.map((f, i) => {
                const e = estimates[f.path];
                const ratio = f.size && targetMB ? Math.min(100, Math.round((targetMB * 1e6 / f.size) * 100)) : null;
                return (
                  <div key={f.path} className="cmp-file" style={{ padding: 14, borderTop: i ? '1px solid var(--line)' : 0 }}>
                    {f.thumb ? <img src={mediaUrl(f.thumb)} alt="" /> : <div style={{ width: 180, aspectRatio: '16/9', borderRadius: 10, background: 'var(--bg-3)', display: 'grid', placeItems: 'center' }}><Icon name="film" /></div>}
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontWeight: 650, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.title}</div>
                      <div className="dim" style={{ fontSize: 12.5 }}>{fmtSize(f.size)} · {fmtDur(f.duration)} · {f.width}×{f.height}</div>
                      {targetMB > 0 && e && (
                        <div style={{ fontSize: 12.5, marginTop: 6 }}>
                          → <b>&lt; {targetMB} MB</b> · {e.W}×{e.H} @ {Math.round(e.fps)} fps · {e.videoKbps} kbps
                          {ratio !== null && ratio < 100 && <span className="dim"> · {ratio}% of original</span>}
                          {e.videoKbps < 400 && <span style={{ color: 'var(--accent-2)' }}> · low quality — trim the clip or pick a bigger size</span>}
                        </div>
                      )}
                      <div className="row" style={{ marginTop: 8, fontSize: 12 }}>
                        <span className="dim">Trim</span>
                        <input type="number" step="0.5" min="0" style={{ width: 80, padding: '3px 6px' }} value={Number(f.start || 0).toFixed(1)} onChange={(ev) => setFiles((fs) => fs.map((x, k) => (k === i ? { ...x, start: Math.max(0, Math.min(Number(ev.target.value), x.end - 0.5)) } : x)))} />
                        <span className="dim">to</span>
                        <input type="number" step="0.5" style={{ width: 80, padding: '3px 6px' }} value={Number(f.end || f.duration).toFixed(1)} onChange={(ev) => setFiles((fs) => fs.map((x, k) => (k === i ? { ...x, end: Math.min(x.duration, Math.max(Number(ev.target.value), x.start + 0.5)) } : x)))} />
                        <span className="dim">sec</span>
                      </div>
                    </div>
                    <button className="btn ghost sm icon" onClick={() => setFiles(files.filter((_, k) => k !== i))}><Icon name="x" size={15} /></button>
                  </div>
                );
              })}
            </div>
          )}

          <div style={{ marginTop: 24 }}>
            <Section title="Options">
              <Row label="Encoder" hint="CPU (x264 two-pass) hits the target size most precisely and gives the best quality per MB. GPU is much faster.">
                <Select value={opts.encoder} onChange={(v) => setOpts({ ...opts, encoder: v })} options={[{ value: 'x264', label: 'CPU – best quality/size' }, { value: 'auto', label: 'GPU – fastest (auto)' }, { value: 'nvenc', label: 'NVIDIA NVENC' }, { value: 'amf', label: 'AMD AMF' }, { value: 'qsv', label: 'Intel QSV' }]} />
              </Row>
              <Row label="Codec" hint="HEVC looks better at the same size but some apps/browsers can’t play it inline.">
                <Seg value={opts.codec} onChange={(v) => setOpts({ ...opts, codec: v })} options={[{ value: 'h264', label: 'H.264' }, { value: 'hevc', label: 'HEVC' }]} />
              </Row>
              <Row label="Two-pass encoding" hint="Analyzes the video first for accurate size and better quality (CPU encoder).">
                <Toggle checked={opts.twoPass} onChange={(v) => setOpts({ ...opts, twoPass: v })} />
              </Row>
              <Row label="Smart downscale" hint="Automatically lowers resolution / frame rate when the bitrate is too low for a clean picture.">
                <Toggle checked={opts.autoDownscale} onChange={(v) => setOpts({ ...opts, autoDownscale: v })} />
              </Row>
              <Row label="Max resolution">
                <Select value={opts.resolution} onChange={(v) => setOpts({ ...opts, resolution: v })} options={[{ value: 'source', label: 'Original' }, { value: 1440, label: '1440p' }, { value: 1080, label: '1080p' }, { value: 720, label: '720p' }, { value: 480, label: '480p' }]} />
              </Row>
              <Row label="Max frame rate">
                <Select value={opts.fps} onChange={(v) => setOpts({ ...opts, fps: v })} options={[{ value: 'source', label: 'Original' }, { value: 60, label: '60 fps' }, { value: 30, label: '30 fps' }]} />
              </Row>
              <Row label="Audio" hint="Which audio to keep in the compressed file.">
                <Select value={opts.audioMode} onChange={(v) => setOpts({ ...opts, audioMode: v })} options={[{ value: 'mix', label: 'Full mix' }, { value: 'nomic', label: 'Game only (no mic)' }, { value: 'none', label: 'No audio' }]} />
                <Select value={opts.audioKbps} onChange={(v) => setOpts({ ...opts, audioKbps: v })} options={[64, 96, 128, 160, 192].map((k) => ({ value: k, label: `${k} kbps` }))} />
              </Row>
            </Section>
          </div>

          <div className="row" style={{ justifyContent: 'flex-end', marginBottom: 24 }}>
            <button className="btn primary lg" disabled={!files.length || !targetMB} onClick={start}><Icon name="compress" /> Compress {files.length > 1 ? `${files.length} files` : ''} to {targetMB || '?'} MB</button>
          </div>

          {myJobs.length > 0 && (
            <Section title="Recent">
              {myJobs.map((j) => (
                <div className="job" key={j.id}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>{j.title}</div>
                    <div className="dim" style={{ fontSize: 12 }}>{j.status === 'running' ? `${j.phase} · ${Math.round(j.progress * 100)}%` : j.status === 'done' ? `Done · ${fmtSize(j.size)}` : j.status === 'error' ? j.error.split('\n')[0] : j.status}</div>
                    {j.status === 'running' && <div style={{ marginTop: 6 }}><Bar value={j.progress} /></div>}
                  </div>
                  <div className="row">
                    {j.status === 'running' && <button className="btn sm" onClick={() => invoke('media:cancel', j.id)}>Cancel</button>}
                    {j.status === 'done' && <>
                      <button className="btn sm" onClick={async () => { const c = lib.clips.find((x) => x.id === j.clipId); if (c) { const ok = await invoke('library:copyToClipboard', c.id); toast({ title: ok ? 'Copied — paste into Discord' : 'Copy failed' }); } }}><Icon name="copy" size={14} /> Copy</button>
                      <button className="btn sm" onClick={() => invoke('app:showItem', j.output)}><Icon name="folder" size={14} /></button>
                    </>}
                  </div>
                </div>
              ))}
            </Section>
          )}
        </div>
      </div>
    </>
  );
}

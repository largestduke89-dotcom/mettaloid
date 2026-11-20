import React, { useEffect, useMemo, useState } from 'react';
import { invoke, mediaUrl, fmtTime } from '../api';
import { useApp } from '../App';
import { Icon, Modal, Seg, Slider, Toggle } from './ui';

/** Rectangle actually covered by the picture inside an object-fit:contain video. */
export function contentRect(video) {
  const r = video.getBoundingClientRect();
  const vw = video.videoWidth || 16;
  const vh = video.videoHeight || 9;
  const s = Math.min(r.width / vw, r.height / vh);
  const w = vw * s;
  const h = vh * s;
  return { left: r.left + (r.width - w) / 2, top: r.top + (r.height - h) / 2, width: w, height: h };
}

/** Crosshair overlay: drag a box over the paused frame. */
export function BoxDrawOverlay({ videoRef, onBox, onCancel }) {
  const [drag, setDrag] = useState(null);
  useEffect(() => {
    const k = (e) => e.key === 'Escape' && onCancel();
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [onCancel]);
  const down = (e) => {
    const v = videoRef.current;
    if (!v) return;
    const cr = contentRect(v);
    const host = e.currentTarget.getBoundingClientRect();
    const clampX = (x) => Math.max(cr.left, Math.min(cr.left + cr.width, x));
    const clampY = (y) => Math.max(cr.top, Math.min(cr.top + cr.height, y));
    const x0 = clampX(e.clientX);
    const y0 = clampY(e.clientY);
    const move = (ev) => setDrag({ x0, y0, x1: clampX(ev.clientX), y1: clampY(ev.clientY), host });
    const up = (ev) => {
      window.removeEventListener('mousemove', move);
      window.removeEventListener('mouseup', up);
      const x1 = clampX(ev.clientX);
      const y1 = clampY(ev.clientY);
      setDrag(null);
      if (Math.abs(x1 - x0) < 8 || Math.abs(y1 - y0) < 8) return;
      onBox({
        x: (Math.min(x0, x1) - cr.left) / cr.width,
        y: (Math.min(y0, y1) - cr.top) / cr.height,
        w: Math.abs(x1 - x0) / cr.width,
        h: Math.abs(y1 - y0) / cr.height
      });
    };
    setDrag({ x0, y0, x1: x0, y1: y0, host });
    window.addEventListener('mousemove', move);
    window.addEventListener('mouseup', up);
  };
  return (
    <div onMouseDown={down} style={{ position: 'absolute', inset: 0, cursor: 'crosshair', zIndex: 5, background: drag ? 'transparent' : 'rgba(0,0,0,.15)' }}>
      {!drag && (
        <div style={{ position: 'absolute', top: 14, left: '50%', transform: 'translateX(-50%)', background: 'rgba(16,17,22,.92)', border: '1px solid var(--line-2)', borderRadius: 10, padding: '8px 14px', fontWeight: 600, pointerEvents: 'none' }}>
          <Icon name="boxSelect" size={15} style={{ verticalAlign: -3, color: 'var(--accent)' }} /> Draw a box around what you want to find · <kbd>Esc</kbd> to cancel
        </div>
      )}
      {drag && (
        <div style={{
          position: 'absolute', left: Math.min(drag.x0, drag.x1) - drag.host.left, top: Math.min(drag.y0, drag.y1) - drag.host.top,
          width: Math.abs(drag.x1 - drag.x0), height: Math.abs(drag.y1 - drag.y0),
          border: '2px solid var(--accent)', boxShadow: '0 0 0 9999px rgba(0,0,0,.5)', pointerEvents: 'none'
        }} />
      )}
    </div>
  );
}

/** Dialog shown after drawing a box: preview, quick settings, search / save. */
export function FindFrameDialog({ clip, time, box, onClose }) {
  const { go, toast, lib } = useApp();
  const [ref, setRef] = useState(null);
  const [analysis, setAnalysis] = useState(null);
  const [error, setError] = useState(null);
  const [showKp, setShowKp] = useState(false);
  const [name, setName] = useState(`${clip.game ? clip.game.name : 'Clip'} · ${fmtTime(time, false)}`);
  const [method, setMethod] = useState('auto');
  const [threshold, setThreshold] = useState(30);
  const [tThreshold, setTThreshold] = useState(0.8);
  const [area, setArea] = useState('anywhere');
  const [scope, setScope] = useState('clip');
  const [autoScan, setAutoScan] = useState(true);
  const [tag, setTag] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    invoke('ff:capture', { clipId: clip.id, time, box })
      .then(async (r) => {
        if (!alive) return;
        setRef(r);
        const a = await invoke('ff:analyze', r, {});
        if (alive) setAnalysis(a);
      })
      .catch((e) => alive && setError(e.message));
    return () => { alive = false; };
  }, []);

  const settings = useMemo(() => {
    const s = { method, searchArea: area };
    if (method !== 'template') s.threshold = threshold;
    if (method !== 'features') s.templateThreshold = tThreshold;
    return s;
  }, [method, area, threshold, tThreshold]);

  const clipIds = () => {
    if (scope === 'clip') return [clip.id];
    if (scope === 'game') return lib.clips.filter((c) => (c.game ? c.game.name : 'Desktop') === (clip.game ? clip.game.name : 'Desktop')).map((c) => c.id);
    return 'all';
  };

  const search = async () => {
    setBusy(true);
    const job = await invoke('ff:search', { label: name, reference: { ...ref, name, settings }, clipIds: clipIds() });
    onClose();
    go(`/findframe?job=${job.id}`);
  };
  const saveFinder = async (scan) => {
    setBusy(true);
    const f = await invoke('ff:createFinder', { ...ref, name, settings, autoScan, tag });
    toast({ title: 'Finder saved', subtitle: name });
    if (scan) {
      const job = await invoke('ff:search', { finderIds: [f.id], clipIds: clipIds() });
      onClose();
      go(`/findframe?job=${job.id}`);
    } else {
      onClose();
    }
  };

  const recommended = analysis && analysis.method;
  return (
    <Modal title={<span><Icon name="target" size={17} style={{ verticalAlign: -3, color: 'var(--accent)' }} /> Find Frame</span>} onClose={onClose} width={640}
      footer={<>
        <button className="btn" disabled={!ref || busy} onClick={() => saveFinder(false)}><Icon name="save" size={14} /> Save finder</button>
        <button className="btn" disabled={!ref || busy} onClick={() => saveFinder(true)}>Save & scan</button>
        <button className="btn primary" disabled={!ref || busy} onClick={search}><Icon name="search" size={14} /> Search</button>
      </>}>
      {error && <div style={{ color: '#fca5a5' }}>{error}</div>}
      <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 18 }}>
        <div className="col" style={{ gap: 8 }}>
          <div style={{ background: '#000', borderRadius: 10, border: '1px solid var(--line-2)', aspectRatio: '1', display: 'grid', placeItems: 'center', overflow: 'hidden', cursor: analysis?.preview ? 'pointer' : 'default' }}
            onClick={() => analysis?.preview && setShowKp(!showKp)} title="Click to toggle detected keypoints">
            {ref ? <img src={mediaUrl(showKp && analysis?.preview ? analysis.preview : ref.image)} style={{ width: '100%', height: '100%', objectFit: 'contain' }} alt="" /> : <span className="dim">Capturing…</span>}
          </div>
          {analysis ? (
            <div className="dim" style={{ fontSize: 12 }}>
              {analysis.keypoints} keypoints · {analysis.width}×{analysis.height}px<br />
              Auto will use <b style={{ color: 'var(--text)' }}>{recommended === 'features' ? 'feature matching' : 'template matching'}</b>
              {analysis.keypoints < 60 && <><br />Low detail: best for icons/HUD at a fixed size.</>}
            </div>
          ) : ref && <div className="dim" style={{ fontSize: 12 }}>Analyzing…</div>}
        </div>
        <div className="col" style={{ gap: 12 }}>
          <div className="field"><label>Name</label><input type="text" value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div className="field"><label>Method</label>
            <Seg value={method} onChange={setMethod} options={[{ value: 'auto', label: 'Auto' }, { value: 'template', label: 'Exact look' }, { value: 'features', label: 'Features' }]} />
            <span className="dim" style={{ fontSize: 11.5 }}>{method === 'template' ? 'Pixel-accurate: kill icons, banners, HUD text.' : method === 'features' ? 'FindFrame keypoints: survives zoom, rotation, lighting.' : 'Picks the best method from the image detail.'}</span>
          </div>
          {method !== 'template' && <div className="field"><label>Feature threshold <em>{threshold}%</em></label><Slider min={5} max={90} value={threshold} onChange={setThreshold} /></div>}
          {method !== 'features' && <div className="field"><label>Template threshold <em>{Math.round(tThreshold * 100)}%</em></label><Slider min={0.5} max={0.99} step={0.01} value={tThreshold} onChange={setTThreshold} /></div>}
          <div className="field"><label>Look for it</label>
            <Seg value={area} onChange={setArea} options={[{ value: 'anywhere', label: 'Anywhere on screen' }, { value: 'near', label: 'Same spot (faster)' }]} />
          </div>
          <div className="field"><label>Search in</label>
            <Seg value={scope} onChange={setScope} options={[{ value: 'clip', label: 'This clip' }, { value: 'game', label: clip.game ? clip.game.name : 'Desktop clips' }, { value: 'all', label: 'All clips' }]} />
          </div>
          <div className="row">
            <label className="row grow" style={{ cursor: 'pointer', fontSize: 12.5 }}><Toggle checked={autoScan} onChange={setAutoScan} /> Auto-scan new clips</label>
            <input type="text" placeholder="Tag matches (optional)" value={tag} onChange={(e) => setTag(e.target.value)} style={{ width: 170 }} />
          </div>
        </div>
      </div>
    </Modal>
  );
}

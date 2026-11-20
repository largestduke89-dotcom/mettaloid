import React, { useEffect, useRef, useState } from 'react';
import { invoke, fmtSecsLabel, useInterval } from '../api';
import { Icon } from './ui';

const QCOLOR = { Excellent: '#4ade80', Great: '#86efac', Good: 'var(--accent-2)', Fair: '#fb923c', Low: '#f87171', Variable: 'var(--text-2)' };
const fmtMB = (mb) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`);
const fmtRate = (k) => (k >= 1000 ? `${(k / 1000).toFixed(k >= 10000 ? 0 : 1)} Mbps` : `${k} kbps`);

/** Live preview of what the recorder will use. `patch` = unsaved settings to preview. */
export function usePlan(patch, deps) {
  const [plan, setPlan] = useState(null);
  const seq = useRef(0);
  useEffect(() => {
    const n = ++seq.current;
    const t = setTimeout(() => invoke('quality:plan', patch || null).then((p) => n === seq.current && setPlan(p)).catch(() => {}), 60);
    return () => clearTimeout(t);
  }, deps);
  return plan;
}

export function PlanSummary({ plan }) {
  if (!plan) return null;
  return (
    <span>
      {plan.height}p{plan.fps} · {plan.videoKbps ? fmtRate(plan.videoKbps) : 'CQP'} ·{' '}
      <b style={{ color: QCOLOR[plan.quality] }}>{plan.quality}</b>
    </span>
  );
}

export default function PlanCard({ plan }) {
  if (!plan) return <div className="set-row dim">Calculating…</div>;
  const usedPct = plan.estMemoryMB ? Math.min(100, (plan.estMemoryMB / plan.memoryMB) * 100) : 0;
  const over = plan.estMemoryMB && plan.estMemoryMB > plan.memoryMB * 0.9;
  return (
    <div className="set-row" style={{ display: 'block', background: 'var(--bg-3)' }}>
      <div className="row" style={{ alignItems: 'flex-start', gap: 18 }}>
        <div style={{ minWidth: 150 }}>
          <div className="dim" style={{ fontSize: 11.5, fontWeight: 700, letterSpacing: 0.5, textTransform: 'uppercase' }}>{plan.auto ? 'Auto quality result' : 'Your settings'}</div>
          <div style={{ fontSize: 24, fontWeight: 800, lineHeight: 1.2, marginTop: 2 }}>{plan.height}p<span style={{ color: 'var(--accent)' }}>{plan.fps}</span></div>
          <div style={{ fontWeight: 700, color: QCOLOR[plan.quality] }}>{plan.quality}</div>
        </div>
        <div className="kv grow" style={{ fontSize: 12.5 }}>
          <span>Resolution</span><span>{plan.width}×{plan.height} @ {plan.fps} fps</span>
          <span>Video</span><span>{plan.videoKbps ? `${fmtRate(plan.videoKbps)} CBR · ${plan.codec.toUpperCase()}` : 'Constant quality (variable size)'}</span>
          <span>Audio</span><span>{plan.audioTracks} tracks × {plan.audioKbps} kbps</span>
          <span>Replay</span><span>{fmtSecsLabel(plan.seconds)}</span>
          <span>RAM used</span><span>{plan.estMemoryMB ? `${fmtMB(plan.estMemoryMB)} of ${fmtMB(plan.memoryMB)} limit` : `up to ${fmtMB(plan.memoryMB)} (limit)`}</span>
        </div>
      </div>
      {plan.estMemoryMB > 0 && (
        <div className="bar" style={{ marginTop: 12 }}><i style={{ width: `${usedPct}%`, background: over && !plan.auto ? 'var(--red)' : undefined }} /></div>
      )}
      {plan.notes.map((n, i) => (
        <div key={i} className="row" style={{ marginTop: 8, fontSize: 12, color: /only the last|very low|unpredictable/.test(n) ? '#fca5a5' : 'var(--text-2)', alignItems: 'flex-start' }}>
          <Icon name="info" size={14} style={{ flex: 'none', marginTop: 1 }} /> <span>{n}</span>
        </div>
      ))}
    </div>
  );
}

/** Live CPU / RAM / dropped-frame readout for the recorder and the app. */
export function PerfStats({ status, compact }) {
  const [app, setApp] = useState(null);
  const load = () => invoke('perf:stats').then(setApp).catch(() => {});
  useEffect(() => { load(); }, []);
  useInterval(load, 3000);
  const st = status && status.stats;
  const cell = (label, value, warn) => (
    <div className="stat" style={{ padding: compact ? '10px 12px' : undefined }}>
      <span>{label}</span><b style={{ fontSize: compact ? 16 : undefined, color: warn ? '#f87171' : undefined }}>{value}</b>
    </div>
  );
  return (
    <div className="stat-grid" style={{ gridTemplateColumns: `repeat(${compact ? 6 : 3}, 1fr)` }}>
      {cell('Recorder CPU', st ? `${st.cpu}%` : '—')}
      {cell('Recorder RAM', st ? fmtMB(st.memoryMB) : '—')}
      {cell('App RAM', app ? fmtMB(app.appMB) : '—')}
      {cell('Render time', st ? `${st.renderMs} ms` : '—', st && st.renderMs > 12)}
      {cell('Encoder drops', st ? `${st.encoderLagPct}%` : '—', st && st.encoderLagPct > 1)}
      {cell('Render drops', st ? `${st.renderLagPct}%` : '—', st && st.renderLagPct > 2)}
    </div>
  );
}

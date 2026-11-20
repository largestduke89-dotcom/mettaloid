import React, { useEffect, useState } from 'react';
import { useApp } from '../App';
import { invoke, useSettings, fmtSecsLabel, fmtSize, fmtDur, useInterval } from '../api';
import { Icon, Slider, GameIcon } from '../components/ui';
import ClipCard from '../components/ClipCard';
import { usePlan, PlanSummary, PerfStats } from '../components/PlanCard';

export const BUFFER_STEPS = [5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 420, 600, 900, 1200];

export function BufferControl({ settings, update, compact }) {
  const secs = settings.recording.replaySeconds;
  const idx = Math.max(0, BUFFER_STEPS.findIndex((s) => s >= secs));
  const [local, setLocal] = useState(idx);
  useEffect(() => setLocal(idx), [idx]);
  const val = BUFFER_STEPS[local];
  const commit = (i) => {
    const s = BUFFER_STEPS[i];
    // keep the main clip hotkey in sync with the replay length
    const clips = settings.hotkeys.clips.map((c, n) => (n === 0 ? { ...c, seconds: s } : c));
    update({ recording: { replaySeconds: s }, hotkeys: { clips } });
  };
  // live preview of the quality the recorder will use at this length
  const clipsPreview = settings.hotkeys.clips.map((c, n) => (n === 0 ? { ...c, seconds: val } : c));
  const plan = usePlan({ recording: { replaySeconds: val }, hotkeys: { clips: clipsPreview } }, [val, JSON.stringify(settings)]);
  const mb = plan && plan.estMemoryMB ? plan.estMemoryMB : 0;
  return (
    <div className="buffer-box" style={compact ? { marginTop: 0 } : undefined}>
      <div>
        <div style={{ fontWeight: 700 }}>Replay length</div>
        <div className="dim" style={{ fontSize: 12 }}>{mb ? `${mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`} RAM` : ''}</div>
        <div style={{ fontSize: 12, marginTop: 2 }}><PlanSummary plan={plan} /></div>
      </div>
      <Slider min={0} max={BUFFER_STEPS.length - 1} value={local} onChange={setLocal} onCommit={commit} />
      <div className="buffer-val">{fmtSecsLabel(val)}</div>
    </div>
  );
}

export default function Home() {
  const { status, lib, go } = useApp();
  const [settings, update] = useSettings();
  const [stats, setStats] = useState(null);
  const [, tick] = useState(0);
  useEffect(() => { invoke('library:stats').then(setStats); }, [lib.clips.length]);
  useInterval(() => tick((x) => x + 1), status?.recording ? 1000 : null);

  if (!status || !settings) return null;
  const clipKey = settings.hotkeys.clips[0]?.accelerator || 'F8';
  const recent = lib.clips.slice(0, 8);
  const ready = status.obsState === 'ready';
  const elapsed = status.recordStartedAt ? (Date.now() - status.recordStartedAt) / 1000 : 0;

  return (
    <>
      <div className="topbar"><h1>Home</h1></div>
      <div className="content">
        {(status.obsState === 'missing' || status.obsState === 'error' || status.error) && (
          <div className="card card-pad" style={{ borderColor: 'rgba(239,68,68,.4)', marginBottom: 16, display: 'flex', gap: 14, alignItems: 'center' }}>
            <Icon name="info" style={{ color: 'var(--red)' }} />
            <div className="grow">
              <b>{status.obsState === 'missing' ? 'Recording engine missing' : 'Recorder problem'}</b>
              <div className="muted" style={{ fontSize: 12.5 }}>{status.error}</div>
            </div>
            <button className="btn" onClick={() => invoke('recorder:restart')}><Icon name="refresh" size={15} /> Restart recorder</button>
          </div>
        )}

        <div className="rec-hero">
          <div className="rec-game">
            {status.game ? <GameIcon name={status.game.name} size={64} /> : <div className="rec-cover"><Icon name="games" size={28} /></div>}
            <div>
              <div className="rec-title">{status.game ? status.game.name : status.captureMode === 'desktop' && status.replayActive ? 'Desktop capture' : 'Waiting for game…'}</div>
              <div className="row wrap" style={{ gap: 6 }}>
                {status.recording && <span className="badge rec"><span className="dot pulse" />REC {fmtDur(elapsed)}</span>}
                {status.replayActive ? <span className="badge live"><span className="dot pulse" />Replay buffer on</span> : <span className="badge">{ready ? 'Replay buffer off' : status.obsState === 'starting' || status.obsState === 'preparing' ? 'Starting recorder…' : 'Recorder offline'}</span>}
                {status.plan && <span className="badge" title={status.plan.auto ? 'Auto quality' : 'Manual quality'}>{status.plan.height}p{status.plan.fps}{status.plan.videoKbps ? ` · ${Math.round(status.plan.videoKbps / 1000)} Mbps` : ''}</span>}
                {status.encoder && <span className="badge" title={status.encoder}>{({ nvenc: 'NVIDIA NVENC', amf: 'AMD AMF', qsv: 'Intel QSV', x264: 'x264 (CPU)' })[status.vendor] || status.encoder}</span>}
                <span className={`badge ${status.micMuted ? 'rec' : ''}`}><Icon name={status.micMuted ? 'micOff' : 'mic'} size={12} /> {status.micMuted ? 'Mic muted' : 'Mic on'}</span>
              </div>
              <div className="dim" style={{ marginTop: 10, fontSize: 12.5 }}>
                {status.replayActive ? <>Press <kbd>{clipKey}</kbd> to save the last {fmtSecsLabel(settings.hotkeys.clips[0]?.seconds || settings.recording.replaySeconds)}{settings.hotkeys.clips.slice(1).map((c) => <span key={c.accelerator}> · <kbd>{c.accelerator}</kbd> {fmtSecsLabel(c.seconds)}</span>)}</> : 'The replay buffer starts automatically when a supported game launches.'}
                {status.capturingWindow && <div style={{ marginTop: 4 }}>Capturing: {status.capturingWindow}</div>}
              </div>
            </div>
          </div>
          <div className="rec-actions">
            <button className="btn primary lg" disabled={!status.replayActive} onClick={() => invoke('recorder:clip')}><Icon name="clip" /> Save clip</button>
            <div className="row">
              <button className={`btn grow ${status.recording ? 'rec' : ''}`} disabled={!ready} onClick={() => invoke('recorder:toggleRecording')}>
                <Icon name={status.recording ? 'stop' : 'record'} size={15} fill={status.recording ? 'currentColor' : 'none'} /> {status.recording ? 'Stop' : 'Record'}
              </button>
              <button className="btn icon" title="Bookmark / quick clip" disabled={!ready} onClick={() => invoke('recorder:bookmark')}><Icon name="bookmark" size={15} /></button>
              <button className="btn icon" title="Screenshot" disabled={!ready} onClick={() => invoke('recorder:screenshot')}><Icon name="camera" size={15} /></button>
              <button className="btn icon" title="Toggle mic" onClick={() => invoke('recorder:toggleMic')}><Icon name={status.micMuted ? 'micOff' : 'mic'} size={15} /></button>
            </div>
            <button className="btn ghost sm" disabled={!ready} onClick={() => invoke('recorder:toggleReplay')}>{status.replayActive ? 'Pause replay buffer' : 'Start replay buffer now'}</button>
          </div>
          <BufferControl settings={settings} update={update} />
        </div>

        {ready && <div style={{ marginTop: 16 }}><PerfStats status={status} compact /></div>}

        <div className="stat-grid" style={{ marginTop: 16 }}>
          <div className="stat"><span>Clips</span><b>{lib.clips.filter((c) => c.type === 'clip').length}</b></div>
          <div className="stat"><span>Sessions</span><b>{lib.clips.filter((c) => c.type === 'session').length}</b></div>
          <div className="stat"><span>Library size</span><b>{stats ? fmtSize(stats.used) : '—'}</b></div>
          <div className="stat"><span>Free disk</span><b>{stats && stats.free != null ? fmtSize(stats.free) : '—'}</b></div>
        </div>

        <div className="row" style={{ margin: '26px 0 12px' }}>
          <div className="h2 grow">Recent</div>
          <button className="btn sm ghost" onClick={() => go('/library')}>View library <Icon name="chevron" size={14} /></button>
        </div>
        {recent.length === 0 ? (
          <div className="card empty">
            <div className="big">🎮</div>
            <b>No clips yet</b>
            <div style={{ marginTop: 4 }}>Launch a game and press <kbd>{clipKey}</kbd> after something cool happens.</div>
          </div>
        ) : (
          <div className="grid" style={{ '--card-w': '240px' }}>
            {recent.map((c) => <ClipCard key={c.id} clip={c} onClick={() => go('/clip/' + c.id)} onToggleSelect={() => go('/clip/' + c.id)} />)}
          </div>
        )}
      </div>
    </>
  );
}

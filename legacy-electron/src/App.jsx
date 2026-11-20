import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { invoke, on, useJobs, useLibrary, useStatus, fmtSize } from './api';
import { Icon, Bar, Modal } from './components/ui';
import Home from './pages/Home';
import Library from './pages/Library';
import Viewer from './pages/Viewer';
import Editor from './pages/Editor';
import Compressor from './pages/Compressor';
import Games from './pages/Games';
import Settings from './pages/Settings';
import FindFrame from './pages/FindFrame';

export const AppCtx = createContext(null);
export const useApp = () => useContext(AppCtx);

function parseRoute(hash) {
  const h = (hash || '').replace(/^#/, '') || '/home';
  const [path, query] = h.split('?');
  const parts = path.split('/').filter(Boolean);
  return { name: parts[0] || 'home', id: parts[1] ? decodeURIComponent(parts[1]) : null, query: new URLSearchParams(query || '') };
}

export default function App() {
  const [route, setRoute] = useState(parseRoute(location.hash));
  const status = useStatus();
  const lib = useLibrary();
  const [jobs, setJobs] = useJobs();
  const [toasts, setToasts] = useState([]);
  const [showJobs, setShowJobs] = useState(false);
  const [failedHotkeys, setFailedHotkeys] = useState([]);

  const go = useCallback((r) => { location.hash = r; }, []);
  useEffect(() => {
    const f = () => setRoute(parseRoute(location.hash));
    window.addEventListener('hashchange', f);
    return () => window.removeEventListener('hashchange', f);
  }, []);
  useEffect(() => on('navigate', (r) => go(r)), [go]);

  const toast = useCallback((t) => {
    const id = Math.random();
    setToasts((x) => [...x, { ...t, id }]);
    setTimeout(() => setToasts((x) => x.filter((y) => y.id !== id)), 4200);
  }, []);
  useEffect(() => on('toast', toast), [toast]);
  useEffect(() => {
    invoke('hotkeys:failed').then(setFailedHotkeys);
    return on('hotkeys:failed', setFailedHotkeys);
  }, []);

  const running = jobs.filter((j) => j.status === 'running');
  const counts = {
    all: lib.clips.length
  };

  const nav = [
    { id: 'home', label: 'Home', icon: 'home' },
    { id: 'library', label: 'Library', icon: 'library', count: counts.all },
    { id: 'compressor', label: 'Compressor', icon: 'compress' },
    { id: 'findframe', label: 'Find Frame', icon: 'target' },
    { id: 'games', label: 'Games', icon: 'games' },
    { id: 'settings', label: 'Settings', icon: 'settings' }
  ];
  const active = route.name === 'clip' ? 'library' : route.name === 'editor' ? 'library' : route.name === 'compress' ? 'compressor' : route.name;

  let page;
  switch (route.name) {
    case 'library': page = <Library />; break;
    case 'clip': page = <Viewer id={route.id} />; break;
    case 'editor': page = <Editor id={route.id} key={route.id} />; break;
    case 'compressor':
    case 'compress': page = <Compressor id={route.id} />; break;
    case 'games': page = <Games />; break;
    case 'findframe': page = <FindFrame key={route.query.get('job') || 'ff'} />; break;
    case 'settings': page = <Settings section={route.id} />; break;
    default: page = <Home />;
  }

  const recState = !status ? null : status.recording ? 'rec' : status.replayActive ? 'live' : status.obsState === 'ready' ? 'idle' : status.obsState;

  return (
    <AppCtx.Provider value={{ status, lib, jobs, setJobs, toast, go, route }}>
      <div className="app">
        <aside className="sidebar">
          <div className="brand"><div className="brand-mark">M</div><div className="brand-name">Mettaloid</div></div>
          {nav.map((n) => (
            <button key={n.id} className={`nav-item ${active === n.id ? 'active' : ''}`} onClick={() => go('/' + n.id)}>
              <Icon name={n.icon} /> {n.label}
              {n.count ? <span className="count">{n.count}</span> : null}
            </button>
          ))}
          <div className="sidebar-foot">
            {running.length > 0 && (
              <div className="jobs-mini" onClick={() => setShowJobs(true)}>
                <div className="row" style={{ marginBottom: 6 }}><b className="grow">{running.length} job{running.length > 1 ? 's' : ''} running</b><span className="dim">{Math.round(running[0].progress * 100)}%</span></div>
                <Bar value={running[0].progress} />
              </div>
            )}
            {running.length === 0 && jobs.length > 0 && (
              <button className="nav-item" onClick={() => setShowJobs(true)}><Icon name="export" /> Exports <span className="count">{jobs.length}</span></button>
            )}
            <div className="jobs-mini" onClick={() => go('/home')} style={{ cursor: 'pointer' }}>
              <div className="row">
                {recState === 'rec' && <span className="badge rec"><span className="dot pulse" />REC</span>}
                {recState === 'live' && <span className="badge live"><span className="dot pulse" />Replay on</span>}
                {recState === 'idle' && <span className="badge">Standby</span>}
                {['starting', 'preparing'].includes(recState) && <span className="badge warn">Starting OBS…</span>}
                {['error', 'missing'].includes(recState) && <span className="badge rec">Recorder error</span>}
                <span className="grow" />
                {status && <Icon name={status.micMuted ? 'micOff' : 'mic'} size={15} style={{ color: status.micMuted ? 'var(--red)' : 'var(--text-2)' }} />}
              </div>
              <div className="dim" style={{ marginTop: 6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {status?.game ? status.game.name : 'Waiting for game'}
              </div>
            </div>
          </div>
        </aside>
        <main className="main">
          {failedHotkeys.length > 0 && (
            <div style={{ background: 'rgba(239,68,68,.12)', borderBottom: '1px solid rgba(239,68,68,.3)', padding: '8px 22px', paddingRight: 160, fontSize: 12.5 }}>
              Hotkey{failedHotkeys.length > 1 ? 's' : ''} {failedHotkeys.map((f) => f.accelerator).join(', ')} could not be registered (in use by another app). <a href="#/settings/hotkeys">Change hotkeys</a>
            </div>
          )}
          {page}
        </main>
      </div>
      {showJobs && <JobsModal jobs={jobs} onClose={() => setShowJobs(false)} />}
      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind === 'err' ? 'err' : ''}`}><b>{t.title}</b>{t.subtitle && <small>{t.subtitle}</small>}</div>
        ))}
      </div>
    </AppCtx.Provider>
  );
}

function JobsModal({ jobs, onClose }) {
  const { go } = useApp();
  return (
    <Modal title="Exports & compression" onClose={onClose} width={620}
      footer={<><button className="btn" onClick={() => invoke('media:clearJobs').then(() => invoke('media:jobs')).then(() => onClose())}>Clear finished</button><button className="btn primary" onClick={onClose}>Close</button></>}>
      <div className="card">
        {jobs.length === 0 && <div className="empty">No jobs yet</div>}
        {jobs.map((j) => (
          <div className="job" key={j.id}>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{j.title}</div>
              <div className="dim" style={{ fontSize: 12 }}>
                {j.status === 'running' && `${j.phase} · ${Math.round(j.progress * 100)}%${j.speed ? ` · ${j.speed.toFixed(1)}x` : ''}`}
                {j.status === 'done' && `Done · ${fmtSize(j.size)}`}
                {j.status === 'error' && <span style={{ color: '#fca5a5' }}>{(j.error || '').split('\n')[0]}</span>}
                {j.status === 'cancelled' && 'Cancelled'}
              </div>
              {j.status === 'running' && <div style={{ marginTop: 6 }}><Bar value={j.progress} /></div>}
            </div>
            <div className="row">
              {j.status === 'running' && <button className="btn sm" onClick={() => invoke('media:cancel', j.id)}>Cancel</button>}
              {j.status === 'done' && <>
                <button className="btn sm" onClick={() => invoke('app:showItem', j.output)}><Icon name="folder" size={14} /></button>
                {j.clipId && <button className="btn sm" onClick={() => { onClose(); go('/clip/' + j.clipId); }}>Open</button>}
              </>}
            </div>
          </div>
        ))}
      </div>
    </Modal>
  );
}

import { useEffect, useState, useCallback, useRef } from 'react';

const mt = window.mt;
export const invoke = (ch, ...a) => mt.invoke(ch, ...a);
export const on = (ch, fn) => mt.on(ch, fn);
export const mediaUrl = (p) => mt.mediaUrl(p);
export const pathForFile = (f) => mt.pathForFile(f);

export function useStatus() {
  const [st, setSt] = useState(null);
  useEffect(() => {
    invoke('recorder:status').then(setSt);
    return on('recorder:status', setSt);
  }, []);
  return st;
}

export function useSettings() {
  const [s, setS] = useState(null);
  useEffect(() => { invoke('settings:get').then(setS); }, []);
  const update = useCallback(async (patch) => {
    const next = await invoke('settings:update', patch);
    setS(next);
    return next;
  }, []);
  return [s, update];
}

export function useLibrary() {
  const [clips, setClips] = useState([]);
  const [albums, setAlbums] = useState([]);
  const refresh = useCallback(async () => {
    const [c, a] = await Promise.all([invoke('library:list'), invoke('albums:list')]);
    setClips(c);
    setAlbums(a);
  }, []);
  useEffect(() => {
    refresh();
    const off = on('library:changed', () => {
      clearTimeout(useLibrary.t);
      useLibrary.t = setTimeout(refresh, 120);
    });
    return off;
  }, [refresh]);
  return { clips, albums, refresh };
}

export function useJobs() {
  const [jobs, setJobs] = useState([]);
  useEffect(() => {
    invoke('media:jobs').then(setJobs);
    return on('job:update', (j) => setJobs((prev) => {
      const i = prev.findIndex((x) => x.id === j.id);
      if (i === -1) return [j, ...prev];
      const n = prev.slice();
      n[i] = j;
      return n;
    }));
  }, []);
  return [jobs, setJobs];
}

export function useInterval(fn, ms) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!ms) return;
    const t = setInterval(() => ref.current(), ms);
    return () => clearInterval(t);
  }, [ms]);
}

export const fmtDur = (s) => {
  if (!s && s !== 0) return '';
  s = Math.max(0, s);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`;
};
export const fmtTime = (s, frac = true) => {
  const base = fmtDur(s);
  if (!frac) return base;
  return `${base}.${String(Math.floor((s % 1) * 10))}`;
};
export const fmtSize = (b) => {
  if (!b && b !== 0) return '';
  if (b >= 1e9) return `${(b / 1e9).toFixed(2)} GB`;
  if (b >= 1e6) return `${(b / 1e6).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(b / 1e3))} KB`;
};
export const fmtSecsLabel = (s) => {
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m ${r}s` : `${m} min`;
};
export const fmtDate = (t) => {
  const d = new Date(t);
  const today = new Date();
  const y = new Date(); y.setDate(today.getDate() - 1);
  const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  if (d.toDateString() === today.toDateString()) return `Today, ${time}`;
  if (d.toDateString() === y.toDateString()) return `Yesterday, ${time}`;
  return d.toLocaleDateString([], { month: 'short', day: 'numeric', year: d.getFullYear() === today.getFullYear() ? undefined : 'numeric' }) + `, ${time}`;
};

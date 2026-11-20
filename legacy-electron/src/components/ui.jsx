import React, { useEffect, useRef, useState } from 'react';
import { invoke } from '../api';

// ---- icons (lucide-style strokes) ----
const P = {
  home: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  library: 'M4 4h6v16H4zM14 4l6 1.5-3.5 15L11 19',
  editor: 'M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4',
  compress: 'M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7',
  games: 'M6 12h4M8 10v4M15 13h.01M18 11h.01M17.3 5H6.7a4 4 0 0 0-4 3.6L2 15.4A3 3 0 0 0 7.2 18l1.6-2h6.4l1.6 2a3 3 0 0 0 5.2-2.6l-.7-6.8A4 4 0 0 0 17.3 5z',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  play: 'M7 4v16l13-8z',
  pause: 'M7 4h4v16H7zM14 4h4v16h-4z',
  scissors: 'M6 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM20 4 8.1 15.9M14.5 14.5 20 20M8.1 8.1 12 12',
  trash: 'M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6',
  star: 'M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3 6.5 20.2l1-6.2L3 9.6l6.2-.9z',
  folder: 'M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  plus: 'M12 5v14M5 12h14',
  x: 'M6 6l12 12M18 6 6 18',
  check: 'M5 12.5l4.5 4.5L19 7',
  mic: 'M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM19 11a7 7 0 0 1-14 0M12 18v3',
  micOff: 'M3 3l18 18M9 9v3a3 3 0 0 0 5.1 2.1M15 9.3V6a3 3 0 0 0-5.9-.8M19 11a7 7 0 0 1-1.2 3.9M5 11a7 7 0 0 0 10.6 6M12 18v3',
  record: 'M12 12m-6 0a6 6 0 1 0 12 0a6 6 0 1 0-12 0',
  stop: 'M6 6h12v12H6z',
  camera: 'M4 8h3l2-3h6l2 3h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V9a1 1 0 0 1 1-1zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  bookmark: 'M6 3h12v18l-6-4-6 4z',
  clip: 'M4 6h16v12H4zM9 9.5v5l4.5-2.5z',
  upload: 'M12 16V4M7 9l5-5 5 5M4 20h16',
  copy: 'M9 9h11v11H9zM5 15H4V4h11v1',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-5-5',
  album: 'M4 5h16v14H4zM4 9h16',
  film: 'M4 4h16v16H4zM8 4v16M16 4v16M4 8h4M4 12h4M4 16h4M16 8h4M16 12h4M16 16h4',
  image: 'M4 5h16v14H4zM8.5 11a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM20 15l-5-5-9 9',
  text: 'M5 6V4h14v2M12 4v16M9 20h6',
  crop: 'M6 2v14a2 2 0 0 0 2 2h14M2 6h14a2 2 0 0 1 2 2v14',
  palette: 'M12 3a9 9 0 1 0 0 18c1 0 1.5-.8 1.5-1.5 0-.4-.2-.8-.4-1.1-.3-.3-.4-.7-.4-1.1 0-.8.7-1.5 1.5-1.5H16a5 5 0 0 0 5-5c0-4.4-4-7.8-9-7.8zM7.5 11.5h.01M10 7.5h.01M15 7.5h.01',
  volume: 'M11 5 6 9H3v6h3l5 4zM15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13',
  sparkles: 'M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8zM19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z',
  export: 'M12 3v12M7 10l5 5 5-5M5 21h14',
  undo: 'M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3',
  redo: 'M15 14l5-5-5-5M20 9H9a5 5 0 0 0 0 10h3',
  speed: 'M12 14l4-4M3.3 19a10 10 0 1 1 17.4 0',
  info: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01',
  refresh: 'M21 12a9 9 0 0 1-15.4 6.4L3 16M3 12a9 9 0 0 1 15.4-6.4L21 8M21 3v5h-5M3 21v-5h5',
  download: 'M12 4v12M7 11l5 5 5-5M4 20h16',
  chevron: 'M9 6l6 6-6 6',
  back: 'M15 6l-6 6 6 6',
  merge: 'M6 3v6a6 6 0 0 0 6 6h6M15 12l3 3-3 3M6 21v-3',
  keyboard: 'M3 6h18v12H3zM7 10h.01M11 10h.01M15 10h.01M7 14h10',
  zoomIn: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-5-5M11 8v6M8 11h6',
  zoomOut: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM21 21l-5-5M8 11h6',
  split: 'M12 3v18M5 8l-3 4 3 4M19 8l3 4-3 4',
  bolt: 'M13 2 4 14h7l-1 8 9-12h-7z',
  hdd: 'M3 13h18v6H3zM5 13l2.5-8h9L19 13M7 16h.01',
  external: 'M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5',
  target: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10zM12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2z',
  boxSelect: 'M4 8V4h4M16 4h4v4M20 16v4h-4M8 20H4v-4M9 9h6v6H9z',
  scan: 'M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2M7 12h10',
  save: 'M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM7 3v5h8V3M7 21v-7h10v7'
};

export function Icon({ name, size = 18, stroke = 2, fill = 'none', style, className }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke="currentColor" strokeWidth={stroke}
      strokeLinecap="round" strokeLinejoin="round" style={style} className={className} aria-hidden="true">
      <path d={P[name] || P.info} />
    </svg>
  );
}

export function Toggle({ checked, onChange, disabled }) {
  return (
    <label className="toggle" style={disabled ? { opacity: 0.4, pointerEvents: 'none' } : undefined}>
      <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} />
      <span />
    </label>
  );
}

export function Slider({ value, min, max, step = 1, onChange, onCommit, onStart, style }) {
  const p = ((Number(value) - min) / (max - min)) * 100;
  return (
    <input type="range" min={min} max={max} step={step} value={value} style={{ '--p': `${Math.max(0, Math.min(100, p))}%`, ...style }}
      onPointerDown={() => onStart && onStart()}
      onChange={(e) => onChange(Number(e.target.value))}
      onMouseUp={(e) => onCommit && onCommit(Number(e.target.value))}
      onKeyUp={(e) => onCommit && onCommit(Number(e.target.value))} />
  );
}

export function Seg({ value, options, onChange, accent }) {
  return (
    <div className="seg">
      {options.map((o) => {
        const v = typeof o === 'object' ? o.value : o;
        const l = typeof o === 'object' ? o.label : o;
        return (
          <button key={String(v)} className={`${value === v ? 'on' : ''} ${accent ? 'accent' : ''}`} onClick={() => onChange(v)}>{l}</button>
        );
      })}
    </div>
  );
}

export function Select({ value, options, onChange, style }) {
  return (
    <select value={value} onChange={(e) => {
      const o = options.find((x) => String(typeof x === 'object' ? x.value : x) === e.target.value);
      onChange(typeof o === 'object' ? o.value : o);
    }} style={style}>
      {options.map((o) => {
        const v = typeof o === 'object' ? o.value : o;
        const l = typeof o === 'object' ? o.label : o;
        return <option key={String(v)} value={String(v)}>{l}</option>;
      })}
    </select>
  );
}

export function Row({ label, hint, children, wide }) {
  return (
    <div className="set-row">
      <div className="label"><b>{label}</b>{hint && <small>{hint}</small>}</div>
      <div className={`ctl ${wide ? 'wide' : ''}`}>{children}</div>
    </div>
  );
}

export function Section({ title, children, id }) {
  return (
    <div className="set-section" id={id}>
      {title && <div className="h3">{title}</div>}
      <div className="set-card">{children}</div>
    </div>
  );
}

export function Modal({ title, onClose, children, footer, width }) {
  useEffect(() => {
    const k = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [onClose]);
  return (
    <div className="modal-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={width ? { width } : undefined}>
        <div className="modal-head"><div className="h2 grow">{title}</div><button className="btn ghost sm icon" onClick={onClose}><Icon name="x" size={16} /></button></div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

// Converts a keydown into an Electron accelerator string.
function toAccelerator(e) {
  const mods = [];
  if (e.ctrlKey) mods.push('Ctrl');
  if (e.altKey) mods.push('Alt');
  if (e.shiftKey) mods.push('Shift');
  if (e.metaKey) mods.push('Super');
  let k = e.key;
  if (['Control', 'Alt', 'Shift', 'Meta'].includes(k)) return null;
  const map = { ' ': 'Space', ArrowUp: 'Up', ArrowDown: 'Down', ArrowLeft: 'Left', ArrowRight: 'Right', Escape: 'Esc', '+': 'Plus' };
  if (e.code.startsWith('Numpad')) {
    const n = e.code.slice(6);
    k = /^\d$/.test(n) ? `num${n}` : { Add: 'numadd', Subtract: 'numsub', Multiply: 'nummult', Divide: 'numdiv', Decimal: 'numdec', Enter: 'Enter' }[n] || k;
  } else if (map[k]) k = map[k];
  else if (k.length === 1) k = k.toUpperCase();
  return [...mods, k].join('+');
}

export function HotkeyInput({ value, onChange }) {
  const [listening, setListening] = useState(false);
  const ref = useRef();
  useEffect(() => {
    if (!listening) return;
    invoke('hotkeys:pause');
    const onKey = (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === 'Escape') { setListening(false); return; }
      if (e.key === 'Backspace' || e.key === 'Delete') { onChange(''); setListening(false); return; }
      const acc = toAccelerator(e);
      if (acc) { onChange(acc); setListening(false); }
    };
    window.addEventListener('keydown', onKey, true);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      invoke('hotkeys:resume');
    };
  }, [listening, onChange]);
  return (
    <button ref={ref} className={`btn hotkey-input ${listening ? 'listening' : ''}`} onClick={() => setListening(!listening)} onBlur={() => setListening(false)}>
      {listening ? 'Press keys… (Esc cancel, ⌫ clear)' : value ? value.split('+').map((k, i) => <kbd key={i}>{k}</kbd>) : <span className="dim">Not set</span>}
    </button>
  );
}

export function Bar({ value }) {
  return <div className="bar"><i style={{ width: `${Math.round((value || 0) * 100)}%` }} /></div>;
}

export function GameIcon({ name, size = 42 }) {
  const initials = (name || '?').replace(/[^A-Za-z0-9 ]/g, '').split(' ').filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase();
  let hash = 0;
  for (const ch of name || '') hash = (hash * 31 + ch.charCodeAt(0)) | 0;
  const hue = Math.abs(hash) % 360;
  return (
    <div className="game-ico" style={{ width: size, height: size, background: `linear-gradient(140deg, hsl(${hue} 45% 26%), hsl(${(hue + 40) % 360} 45% 16%))`, color: `hsl(${hue} 90% 75%)`, fontSize: size * 0.36 }}>
      {initials || '?'}
    </div>
  );
}

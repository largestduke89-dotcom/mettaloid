import React, { useRef, useState } from 'react';
import { mediaUrl, fmtDur, fmtDate, fmtSize } from '../api';
import { Icon } from './ui';

const TYPE_LABEL = { session: 'Session', screenshot: 'Screenshot', import: 'Imported', export: 'Edited', clip: null };

export default function ClipCard({ clip, selected, onClick, onToggleSelect, selecting }) {
  const [hover, setHover] = useState(false);
  const timer = useRef();
  const isVideo = clip.type !== 'screenshot';
  return (
    <div className={`clip-card ${selected ? 'selected' : ''}`}
      onMouseEnter={() => { timer.current = setTimeout(() => setHover(true), 350); }}
      onMouseLeave={() => { clearTimeout(timer.current); setHover(false); }}
      onClick={(e) => (selecting || e.ctrlKey || e.shiftKey ? onToggleSelect(e) : onClick())}>
      <div className="clip-thumb">
        {clip.thumb ? <img src={mediaUrl(clip.thumb)} loading="lazy" alt="" draggable={false} /> : <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center' }} className="dim"><Icon name={isVideo ? 'film' : 'image'} size={28} /></div>}
        {hover && isVideo && <video src={mediaUrl(clip.path)} muted autoPlay loop playsInline onLoadedMetadata={(e) => { e.target.currentTime = Math.max(0, (clip.duration || 0) / 2 - 3); }} />}
        {clip.duration > 0 && <span className="clip-dur">{fmtDur(clip.duration)}</span>}
        {TYPE_LABEL[clip.type] && <span className="clip-type badge" style={{ background: 'rgba(0,0,0,.7)', color: '#fff' }}>{TYPE_LABEL[clip.type]}</span>}
        {clip.event && <span className="clip-type badge warn" style={{ background: 'rgba(0,0,0,.7)' }}>⚡ {clip.event}</span>}
        <div className="clip-check" onClick={(e) => { e.stopPropagation(); onToggleSelect(e); }}>{selected && <Icon name="check" size={14} stroke={3} />}</div>
        {clip.favorite && <span className="clip-fav"><Icon name="star" size={16} fill="currentColor" /></span>}
      </div>
      <div className="clip-meta">
        <div className="t" title={clip.title}>{clip.title}</div>
        <div className="s"><span>{clip.game ? clip.game.name : 'Desktop'}</span>·<span>{fmtDate(clip.createdAt)}</span><span className="grow" /><span>{fmtSize(clip.size)}</span></div>
      </div>
    </div>
  );
}

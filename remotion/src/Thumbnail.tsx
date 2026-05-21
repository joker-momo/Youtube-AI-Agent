import React from 'react';
import {AbsoluteFill, Img, OffthreadVideo} from 'remotion';
import {mediaSrc, RenderProps} from './render-props';
import {fitHeadline} from './styles';

export const Thumbnail: React.FC<RenderProps> = (props) => {
  const palette = props.style.palette;
  const leadScene = props.scenes[0];
  const seoBg = (props.seo.thumbnail_path ?? '').trim();
  const sceneBg = leadScene?.asset_refs?.background ?? '';
  const bg = seoBg || sceneBg;
  const bgPathForType = bg.split('?')[0].toLowerCase();
  const isVideo = bgPathForType.endsWith('.mp4');

  // thumbnail_text: short ALL-CAPS hook from SEO (e.g. "DUERME MEJOR HOY")
  // Fallback: use title up to first colon or first 5 words.
  const rawHook = props.seo.thumbnail_text
    ?? (props.seo.title.includes(':')
        ? props.seo.title.split(':')[0]
        : props.seo.title.split(' ').slice(0, 5).join(' '));
  const hook = rawHook.toUpperCase();

  const channelName = props.channel.name;
  // Cap at 96px — keeps text within bottom third even for 4-5 word hooks.
  const hookSize = fitHeadline(hook, 96, 60);

  return (
    <AbsoluteFill style={{overflow: 'hidden', fontFamily: 'Inter, Arial, sans-serif'}}>

      {/* Background priority: seo.thumbnail_path -> scene-01 background -> gradient fallback */}
      {bg ? (
        isVideo ? (
          <OffthreadVideo
            src={mediaSrc(bg)}
            muted
            style={{position: 'absolute', width: '100%', height: '100%', objectFit: 'cover'}}
          />
        ) : (
          <Img
            src={mediaSrc(bg)}
            style={{position: 'absolute', width: '100%', height: '100%', objectFit: 'cover'}}
          />
        )
      ) : (
        <div style={{position: 'absolute', inset: 0, background: `linear-gradient(135deg, ${palette.primary} 0%, ${palette.secondary} 100%)`}} />
      )}

      {/* Dark gradient overlay — bottom heavy for text readability */}
      <div style={{
        position: 'absolute', inset: 0,
        background: 'linear-gradient(to top, rgba(0,0,0,0.88) 0%, rgba(0,0,0,0.55) 45%, rgba(0,0,0,0.18) 100%)',
      }} />

      {/* Left vertical accent bar */}
      <div style={{
        position: 'absolute', left: 0, top: 0, bottom: 0,
        width: 14, backgroundColor: palette.accent,
      }} />

      {/* Channel name — top left */}
      <div style={{
        position: 'absolute', top: 38, left: 42,
        display: 'flex', alignItems: 'center', gap: 12,
        backgroundColor: 'rgba(0,0,0,0.52)',
        padding: '10px 18px',
        borderRadius: 4,
      }}>
        <div style={{width: 14, height: 14, borderRadius: 7, backgroundColor: palette.accent, flexShrink: 0}} />
        <span style={{fontSize: 26, fontWeight: 800, color: '#fff', letterSpacing: 1.5, textTransform: 'uppercase'}}>
          {channelName}
        </span>
      </div>

      {/* Main hook text — bottom area, left-aligned, large */}
      <div style={{
        position: 'absolute', left: 42, right: 42, bottom: 52,
      }}>
        {/* Accent top line */}
        <div style={{width: 60, height: 7, backgroundColor: palette.accent, borderRadius: 4, marginBottom: 20}} />

        {/* Hook — the big clickbait text */}
        <div style={{
          fontSize: hookSize,
          fontWeight: 950,
          lineHeight: 1.0,
          color: '#FFFFFF',
          textShadow: '0 2px 24px rgba(0,0,0,0.95), 0 1px 6px rgba(0,0,0,0.9)',
          letterSpacing: -1,
          maxWidth: 980,
        }}>
          {hook}
        </div>
      </div>

    </AbsoluteFill>
  );
};

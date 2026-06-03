/**
 * Reusable text primitives for vertical Shorts (spec §4 + §6.1).
 * Calm modern entrance: fade + slight rise + tiny scale-in.
 */
import React from 'react';
import {interpolate, useCurrentFrame} from 'remotion';
import {
  HOOK_FONT_FAMILY,
  BODY_FONT_FAMILY,
  CAPTION_FONT_FAMILY,
  TEXT_ENTRANCE,
} from './ShortLayoutConstants';

export function parseHighlights(text: React.ReactNode, highlightColor: string = '#F5C24B'): React.ReactNode {
  if (typeof text !== 'string') {
    return text;
  }
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <span key={index} style={{color: highlightColor}}>
          {part.slice(2, -2)}
        </span>
      );
    }
    return part;
  });
}

function useEntrance(): {opacity: number; transform: string} {
  const frame = useCurrentFrame();
  const t = interpolate(frame, [0, TEXT_ENTRANCE.durationFrames], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const y = (1 - t) * TEXT_ENTRANCE.translateYStartPx;
  const scale = TEXT_ENTRANCE.scaleStart + t * (TEXT_ENTRANCE.scaleEnd - TEXT_ENTRANCE.scaleStart);
  return {opacity: t, transform: `translateY(${y}px) scale(${scale})`};
}

export const HookTitle: React.FC<{children: React.ReactNode; fontSize?: number; accentColor?: string}> = ({
  children,
  fontSize = 96,
  accentColor,
}) => {
  const anim = useEntrance();
  return (
    <div
      style={{
        ...anim,
        fontFamily: HOOK_FONT_FAMILY,
        fontWeight: 900,
        fontSize,
        lineHeight: 0.96,
        letterSpacing: -0.6,
        textTransform: 'uppercase',
        color: '#FFFFFF',
        textShadow:
          '0 6px 18px rgba(0,0,0,0.55), 0 2px 4px rgba(0,0,0,0.55), 0 0 2px rgba(0,0,0,0.6)',
        WebkitTextStroke: '0.6px rgba(0,0,0,0.55)',
        textAlign: 'center',
      }}
    >
      {parseHighlights(children, accentColor)}
    </div>
  );
};

export const BodyText: React.FC<{children: React.ReactNode; fontSize?: number; accentColor?: string}> = ({
  children,
  fontSize = 54,
  accentColor,
}) => {
  const anim = useEntrance();
  return (
    <div
      style={{
        ...anim,
        fontFamily: BODY_FONT_FAMILY,
        fontWeight: 700,
        fontSize,
        lineHeight: 1.12,
        letterSpacing: 0,
        color: '#FFFFFF',
        textShadow:
          '0 4px 14px rgba(0,0,0,0.55), 0 1px 3px rgba(0,0,0,0.5), 0 0 2px rgba(0,0,0,0.55)',
        WebkitTextStroke: '0.4px rgba(0,0,0,0.5)',
        textAlign: 'center',
      }}
    >
      {parseHighlights(children, accentColor)}
    </div>
  );
};

export const CaptionText: React.FC<{children: React.ReactNode; fontSize?: number; accentColor?: string}> = ({
  children,
  fontSize = 42,
  accentColor,
}) => {
  const anim = useEntrance();
  return (
    <div
      style={{
        ...anim,
        fontFamily: CAPTION_FONT_FAMILY,
        fontWeight: 700,
        fontSize,
        lineHeight: 1.18,
        color: '#F7F7F2',
        textShadow:
          '0 3px 12px rgba(0,0,0,0.6), 0 1px 2px rgba(0,0,0,0.5), 0 0 2px rgba(0,0,0,0.55)',
        WebkitTextStroke: '0.4px rgba(0,0,0,0.5)',
        textAlign: 'center',
      }}
    >
      {parseHighlights(children, accentColor)}
    </div>
  );
};

export const BulletPill: React.FC<{n: number; children: React.ReactNode; accentColor?: string}> = ({
  n,
  children,
  accentColor,
}) => (
  <div
    style={{
      display: 'flex',
      alignItems: 'center',
      gap: 20,
      padding: '14px 24px',
      borderRadius: 36,
      background: 'rgba(0,0,0,0.55)',
      maxWidth: 920,
    }}
  >
    <div
      style={{
        width: 56,
        height: 56,
        borderRadius: 28,
        background: accentColor || '#F5C24B',
        color: '#1A1207',
        fontFamily: HOOK_FONT_FAMILY,
        fontWeight: 900,
        fontSize: 30,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flex: '0 0 auto',
      }}
    >
      {n}
    </div>
    <div
      style={{
        fontFamily: BODY_FONT_FAMILY,
        fontWeight: 800,
        fontSize: 44,
        color: '#FFFFFF',
        lineHeight: 1.1,
      }}
    >
      {parseHighlights(children, accentColor)}
    </div>
  </div>
);

export const CtaText: React.FC<{children: React.ReactNode; fontSize?: number; accentColor?: string}> = ({
  children,
  fontSize = 52,
  accentColor,
}) => {
  const anim = useEntrance();
  return (
    <div
      style={{
        ...anim,
        fontFamily: BODY_FONT_FAMILY,
        fontWeight: 800,
        fontSize,
        lineHeight: 1.08,
        color: '#FFFFFF',
        textShadow:
          '0 5px 16px rgba(0,0,0,0.55), 0 1px 3px rgba(0,0,0,0.5), 0 0 2px rgba(0,0,0,0.55)',
        WebkitTextStroke: '0.5px rgba(0,0,0,0.55)',
        textAlign: 'center',
        textTransform: 'uppercase',
        letterSpacing: 0,
      }}
    >
      {parseHighlights(children, accentColor)}
    </div>
  );
};

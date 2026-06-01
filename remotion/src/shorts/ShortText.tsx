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

export const HookTitle: React.FC<{children: React.ReactNode; fontSize?: number}> = ({
  children,
  fontSize = 96,
}) => {
  const anim = useEntrance();
  return (
    <div
      style={{
        ...anim,
        fontFamily: HOOK_FONT_FAMILY,
        fontWeight: 900,
        fontSize,
        lineHeight: 0.94,
        letterSpacing: -1.6,
        textTransform: 'uppercase',
        color: '#FFFFFF',
        textShadow: '0 8px 22px rgba(0,0,0,0.72)',
        WebkitTextStroke: '2px rgba(0,0,0,0.75)',
        textAlign: 'center',
      }}
    >
      {children}
    </div>
  );
};

export const BodyText: React.FC<{children: React.ReactNode; fontSize?: number}> = ({
  children,
  fontSize = 54,
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
        letterSpacing: -0.3,
        color: '#FFFFFF',
        textShadow: '0 6px 18px rgba(0,0,0,0.68)',
        textAlign: 'center',
      }}
    >
      {children}
    </div>
  );
};

export const CaptionText: React.FC<{children: React.ReactNode; fontSize?: number}> = ({
  children,
  fontSize = 42,
}) => {
  const anim = useEntrance();
  return (
    <div
      style={{
        ...anim,
        fontFamily: CAPTION_FONT_FAMILY,
        fontWeight: 800,
        fontSize,
        lineHeight: 1.16,
        color: '#F7F7F2',
        textShadow: '0 4px 14px rgba(0,0,0,0.72)',
        textAlign: 'center',
      }}
    >
      {children}
    </div>
  );
};

export const BulletPill: React.FC<{n: number; children: React.ReactNode}> = ({n, children}) => (
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
        background: '#F5C24B',
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
      {children}
    </div>
  </div>
);

export const CtaText: React.FC<{children: React.ReactNode; fontSize?: number}> = ({
  children,
  fontSize = 52,
}) => {
  const anim = useEntrance();
  return (
    <div
      style={{
        ...anim,
        fontFamily: BODY_FONT_FAMILY,
        fontWeight: 900,
        fontSize,
        lineHeight: 1.06,
        color: '#FFFFFF',
        textShadow: '0 6px 18px rgba(0,0,0,0.7)',
        textAlign: 'center',
        textTransform: 'uppercase',
        letterSpacing: -0.5,
      }}
    >
      {children}
    </div>
  );
};

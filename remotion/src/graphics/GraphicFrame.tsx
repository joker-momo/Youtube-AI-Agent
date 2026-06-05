/**
 * Shared frame + reveal helpers for graphic components (spec v7 §10, §13).
 *
 * GraphicFrame anchors a title row and an optional footer inside the Shorts
 * safe area, leaving the centre band for the graphic body. ``useReveal`` gives
 * each element a calm staggered fade/slide — soft motion, no bouncy effects.
 */
import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {graphicTheme} from './graphic-theme';

const {colors, font, fontSize, spacing} = graphicTheme;

/** Calm fade-up reveal that starts at ``delaySec`` into the scene. */
export function useReveal(delaySec: number): React.CSSProperties {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const delayFrames = Math.round(delaySec * fps);
  const progress = spring({
    frame: frame - delayFrames,
    fps,
    config: {damping: 200, mass: 0.7},
    durationInFrames: Math.round(fps * 0.5),
  });
  const opacity = interpolate(progress, [0, 1], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const translateY = interpolate(progress, [0, 1], [26, 0]);
  return {opacity, transform: `translateY(${translateY}px)`};
}

export const GraphicTitle: React.FC<{children: React.ReactNode; delaySec?: number}> = ({
  children,
  delaySec = 0,
}) => {
  const reveal = useReveal(delaySec);
  return (
    <div
      style={{
        ...reveal,
        fontFamily: font.family,
        fontSize: fontSize.title,
        fontWeight: 800,
        lineHeight: 1.05,
        letterSpacing: '0.5px',
        color: colors.oliveDark,
        textTransform: 'uppercase',
        textAlign: 'center',
        maxWidth: 1080 - spacing.safeX * 2,
      }}
    >
      {children}
    </div>
  );
};

export const GraphicFooter: React.FC<{children?: React.ReactNode; delaySec?: number}> = ({
  children,
  delaySec = 0,
}) => {
  const reveal = useReveal(delaySec);
  if (!children) return null;
  return (
    <div
      style={{
        ...reveal,
        position: 'absolute',
        bottom: spacing.safeBottom,
        left: spacing.safeX,
        right: spacing.safeX,
        fontFamily: font.family,
        fontSize: fontSize.footer,
        fontWeight: 600,
        lineHeight: 1.25,
        color: colors.mutedText,
        textAlign: 'center',
      }}
    >
      {children}
    </div>
  );
};

/**
 * Centre-band container. Title sits near the top of the safe area; ``children``
 * (the graphic body) fill the band between title and footer.
 */
export const GraphicFrame: React.FC<{
  title: string;
  footer?: string;
  children: React.ReactNode;
}> = ({title, footer, children}) => {
  return (
    <AbsoluteFill
      style={{
        paddingTop: spacing.safeTop,
        paddingLeft: spacing.safeX,
        paddingRight: spacing.safeX,
        paddingBottom: spacing.safeBottom,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
      }}
    >
      <GraphicTitle delaySec={0}>{title}</GraphicTitle>

      <div
        style={{
          flex: 1,
          width: '100%',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        {children}
      </div>

      <GraphicFooter delaySec={0.6}>{footer}</GraphicFooter>
    </AbsoluteFill>
  );
};

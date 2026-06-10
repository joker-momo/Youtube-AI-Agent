/**
 * Shared frame + reveal helpers for graphic components (spec v7 §10, §13).
 *
 * GraphicFrame anchors a title row and an optional footer inside the Shorts
 * safe area, leaving the centre band for the graphic body. ``useReveal`` gives
 * each element a calm staggered fade/slide — soft motion, no bouncy effects.
 */
import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {
  getGraphicColors,
  graphicLayout,
  graphicMotion,
  graphicTheme,
  type GraphicColorRoles,
  type GraphicSurfaceStyle,
  type GraphicVariant,
} from './graphic-theme';

const {font, fontSize, spacing} = graphicTheme;

/**
 * Sentence-case a (often all-caps) title for a calmer, less shouty feel.
 * Spanish sentence case = capitalise the first letter only. Render-only, no
 * payload/schema change.
 */
export function toSentenceCase(text: string): string {
  const t = (text || '').trim();
  if (!t) return t;
  const lower = t.toLocaleLowerCase('es');
  return lower.charAt(0).toLocaleUpperCase('es') + lower.slice(1);
}

/** Calm fade-up reveal with a faint depth settle, starting at ``delaySec``. */
export function useReveal(delaySec: number): React.CSSProperties {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const delayFrames = Math.round(delaySec * fps);
  const progress = spring({
    frame: frame - delayFrames,
    fps,
    config: graphicMotion.spring,
    durationInFrames: Math.round(fps * 0.42),
  });
  const opacity = interpolate(progress, [0, 1], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const translateY = interpolate(progress, [0, 1], [18, 0]);
  // Faint scale settle adds depth without any bounce.
  const scale = interpolate(progress, [0, 1], [0.992, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return {opacity, transform: `translateY(${translateY}px) scale(${scale})`};
}

/**
 * Hero reveal for the scene title: a gentle settle (scale 1.03 -> 1.0) that
 * lands within the first ~0.5s so the dominant idea reads immediately. No
 * punchy/TikTok motion — a soft spring only.
 */
export function useHeroReveal(): React.CSSProperties {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const progress = spring({
    frame: frame - Math.round(graphicMotion.heroDelaySec * fps),
    fps,
    config: graphicMotion.settleSpring,
    durationInFrames: Math.round(fps * 0.5),
  });
  const opacity = interpolate(progress, [0, 1], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const translateY = interpolate(progress, [0, 1], [14, 0]);
  const scale = interpolate(progress, [0, 1], [graphicMotion.heroSettleScale, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return {opacity, transform: `translateY(${translateY}px) scale(${scale})`, transformOrigin: 'center top'};
}

export const GraphicTitle: React.FC<{
  children: React.ReactNode;
  colors?: GraphicColorRoles;
  delaySec?: number;
}> = ({
  children,
  colors = graphicTheme.colors,
}) => {
  const reveal = useHeroReveal();
  // Sentence-case the title when it arrives as a plain string (the common case).
  const content = typeof children === 'string' ? toSentenceCase(children) : children;
  return (
    <div
      style={{
        ...reveal,
        fontFamily: font.family,
        fontSize: fontSize.title,
        fontWeight: 700,
        lineHeight: 1.08,
        letterSpacing: -0.5,
        color: colors.titleStrong,
        textTransform: 'none',
        textAlign: 'center',
        maxWidth: 1080 - spacing.safeX * 2,
      }}
    >
      {content}
    </div>
  );
};

export const GraphicFooter: React.FC<{
  children?: React.ReactNode;
  colors?: GraphicColorRoles;
  delaySec?: number;
}> = ({
  children,
  colors = graphicTheme.colors,
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

function surfaceStyleFor(colors: GraphicColorRoles, surfaceStyle: GraphicSurfaceStyle): React.CSSProperties {
  if (surfaceStyle === 'none' || surfaceStyle === 'plate_focus') {
    return {};
  }

  if (surfaceStyle === 'editorial') {
    return {
      width: '100%',
      padding: '28px 30px',
      background: colors.surfaceElevated,
      borderTop: `1px solid ${colors.line}`,
      borderBottom: `1px solid ${colors.line}`,
      boxShadow: graphicLayout.shadow.soft,
      borderRadius: graphicLayout.radius.md,
    };
  }

  return {
    width: '100%',
    padding: '30px',
    background: colors.surfaceElevated,
    border: `1px solid ${colors.line}`,
    boxShadow: graphicLayout.shadow.card,
    borderRadius: graphicLayout.radius.lg,
  };
}

/**
 * Centre-band container. Title sits near the top of the safe area; ``children``
 * (the graphic body) fill the band between title and footer.
 */
export const GraphicFrame: React.FC<{
  title: string;
  footer?: string;
  variant?: GraphicVariant;
  surfaceStyle?: GraphicSurfaceStyle;
  children: React.ReactNode;
}> = ({title, footer, variant = 'brand_default', surfaceStyle = 'none', children}) => {
  const colors = getGraphicColors(variant);
  const surfaceStyles = surfaceStyleFor(colors, surfaceStyle);
  const hasSurface = surfaceStyle !== 'none' && surfaceStyle !== 'plate_focus';
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
      <GraphicTitle colors={colors} delaySec={0}>{title}</GraphicTitle>

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
        <div style={hasSurface ? surfaceStyles : {width: '100%'}}>{children}</div>
      </div>

      <GraphicFooter colors={colors} delaySec={0.6}>{footer}</GraphicFooter>
    </AbsoluteFill>
  );
};

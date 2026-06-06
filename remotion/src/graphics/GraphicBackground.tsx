/**
 * Warm readable background for graphic scenes (spec v7 §14).
 *
 * Priority: blurred/dimmed stock background if available, else a warm cream
 * generated background. Always readable with no video. No blobs/orbs.
 */
import React from 'react';
import {AbsoluteFill, Img, OffthreadVideo, staticFile} from 'remotion';
import {
  getGraphicColors,
  graphicVariants,
  type GraphicBackgroundMode,
  type GraphicColorRoles,
  type GraphicVariant,
} from './graphic-theme';

const VIDEO_EXT = /\.(mp4|mov|webm|m4v)(\?|#|$)/i;

function resolveSrc(src?: string): string | undefined {
  if (!src) return undefined;
  if (/^(https?|data|file):/.test(src)) return src;
  try {
    return staticFile(src.replace(/^\/+/, ''));
  } catch {
    return src;
  }
}

const WarmWash: React.FC<{colors: GraphicColorRoles; mode: GraphicBackgroundMode}> = ({colors, mode}) => {
  const background =
    mode === 'clean'
      ? `linear-gradient(180deg, ${colors.surface} 0%, ${colors.background} 100%)`
      : `radial-gradient(120% 90% at 50% 38%, ${colors.surface} 0%, ${colors.background} 55%, ${colors.surfaceAlt} 100%)`;
  return (
    <AbsoluteFill
      style={{
        background,
      }}
    />
  );
};

const PaperTexture: React.FC = () => (
  <AbsoluteFill
    style={{
      opacity: 0.035,
      backgroundImage: 'radial-gradient(rgba(47,42,36,0.35) 0.6px, transparent 0.6px)',
      backgroundSize: '7px 7px',
      mixBlendMode: 'multiply',
      pointerEvents: 'none',
    }}
  />
);

/** Soft vignette to focus the eye centre, no harsh edges. */
const Vignette: React.FC<{colors: GraphicColorRoles}> = ({colors}) => (
  <AbsoluteFill
    style={{
      background:
        `radial-gradient(130% 100% at 50% 45%, rgba(0,0,0,0) 58%, ${colors.shadow} 100%)`,
      pointerEvents: 'none',
    }}
  />
);

export const GraphicBackground: React.FC<{
  variant?: GraphicVariant;
  backgroundMode?: GraphicBackgroundMode;
  backgroundSrc?: string;
  src?: string;
  children?: React.ReactNode;
}> = ({variant = 'brand_default', backgroundMode = 'radial', backgroundSrc, src, children}) => {
  const colors = getGraphicColors(variant);
  const mediaSrc = backgroundSrc ?? src;
  const resolved = resolveSrc(mediaSrc);
  const isVideo = resolved ? VIDEO_EXT.test(resolved) : false;
  const shouldShowMedia = Boolean(resolved && backgroundMode === 'video_blur');
  const resolvedMedia = shouldShowMedia ? resolved : undefined;

  return (
    <AbsoluteFill style={{backgroundColor: colors.background}}>
      <WarmWash colors={colors} mode={backgroundMode} />

      {resolvedMedia && (
        <AbsoluteFill style={{filter: 'blur(28px) saturate(0.9)', transform: 'scale(1.12)'}}>
          {isVideo ? (
            <OffthreadVideo
              src={resolvedMedia}
              muted
              style={{width: '100%', height: '100%', objectFit: 'cover'}}
            />
          ) : (
            <Img src={resolvedMedia} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
          )}
        </AbsoluteFill>
      )}

      {/* Warm cream scrim so the graphic stays readable over any footage. */}
      {resolvedMedia && <AbsoluteFill style={{backgroundColor: colors.overlay}} />}

      {backgroundMode === 'paper' && <PaperTexture />}

      {backgroundMode !== 'clean' && <Vignette colors={colors} />}

      {children}
    </AbsoluteFill>
  );
};

export const defaultGraphicBackgroundColors = graphicVariants.brand_default;

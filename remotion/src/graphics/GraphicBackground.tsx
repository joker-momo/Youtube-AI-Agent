/**
 * Warm readable background for graphic scenes (spec v7 §14).
 *
 * Priority: blurred/dimmed stock background if available, else a warm cream
 * generated background. Always readable with no video. No blobs/orbs.
 */
import React from 'react';
import {AbsoluteFill, Img, OffthreadVideo, staticFile} from 'remotion';
import {graphicTheme} from './graphic-theme';

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

const WarmWash: React.FC = () => (
  <AbsoluteFill
    style={{
      background: `radial-gradient(120% 90% at 50% 38%, ${graphicTheme.colors.paper} 0%, ${graphicTheme.colors.cream} 55%, #EFE5D4 100%)`,
    }}
  />
);

/** Soft vignette to focus the eye centre, no harsh edges. */
const Vignette: React.FC = () => (
  <AbsoluteFill
    style={{
      background:
        'radial-gradient(130% 100% at 50% 45%, rgba(0,0,0,0) 58%, rgba(47,42,36,0.16) 100%)',
    }}
  />
);

export const GraphicBackground: React.FC<{
  src?: string;
  children?: React.ReactNode;
}> = ({src, children}) => {
  const resolved = resolveSrc(src);
  const isVideo = resolved ? VIDEO_EXT.test(resolved) : false;

  return (
    <AbsoluteFill style={{backgroundColor: graphicTheme.colors.cream}}>
      <WarmWash />

      {resolved && (
        <AbsoluteFill style={{filter: 'blur(28px) saturate(0.9)', transform: 'scale(1.12)'}}>
          {isVideo ? (
            <OffthreadVideo
              src={resolved}
              muted
              style={{width: '100%', height: '100%', objectFit: 'cover'}}
            />
          ) : (
            <Img src={resolved} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
          )}
        </AbsoluteFill>
      )}

      {/* Warm cream scrim so the graphic stays readable over any footage. */}
      {resolved && <AbsoluteFill style={{backgroundColor: graphicTheme.colors.overlay}} />}

      <Vignette />

      {children}
    </AbsoluteFill>
  );
};

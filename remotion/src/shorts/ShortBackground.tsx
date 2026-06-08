/**
 * Background media + smart fit + readability overlays (spec §3).
 *
 * Renders the scene background as cover-fit video/image with a blurred
 * duplicate fill behind, plus three independently controllable overlays
 * (full darken + bottom gradient + center text scrim).
 */
import React from 'react';
import {AbsoluteFill, Img, OffthreadVideo, staticFile} from 'remotion';
import {SHORT_OVERLAYS, OverlayKey} from './ShortLayoutConstants';

export type ShortBackgroundProps = {
  src?: string;
  /** "video" | "image". Inferred from src extension when omitted. */
  kind?: 'video' | 'image';
  /** Spec §3.2 — picks the overlay preset for this scene mood. */
  overlay?: OverlayKey;
};

function inferKind(src: string | undefined): 'video' | 'image' {
  if (!src) return 'image';
  const lower = src.toLowerCase();
  if (lower.endsWith('.mp4') || lower.endsWith('.mov') || lower.endsWith('.webm')) return 'video';
  return 'image';
}

function resolveSrc(src: string | undefined): string | undefined {
  if (!src) return undefined;
  if (src.startsWith('http://') || src.startsWith('https://') || src.startsWith('data:')) return src;
  try {
    return staticFile(src);
  } catch {
    return src;
  }
}

export const ShortBackground: React.FC<ShortBackgroundProps> = ({src, kind, overlay = 'default'}) => {
  const resolved = resolveSrc(src);
  const actualKind = kind ?? inferKind(src);
  const ov = SHORT_OVERLAYS[overlay] ?? SHORT_OVERLAYS.default;

  return (
    <AbsoluteFill style={{backgroundColor: '#0b1020'}}>
      {/* Blur-fill duplicate behind for horizontal sources. */}
      {resolved ? (
        <AbsoluteFill style={{filter: 'blur(40px) brightness(0.55)', transform: 'scale(1.15)'}}>
          {actualKind === 'video' ? (
            <OffthreadVideo src={resolved} style={{width: '100%', height: '100%', objectFit: 'cover'}} muted />
          ) : (
            <Img src={resolved} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
          )}
        </AbsoluteFill>
      ) : null}

      {/* Primary cover-fit media. */}
      {resolved ? (
        <AbsoluteFill>
          {actualKind === 'video' ? (
            <OffthreadVideo src={resolved} style={{width: '100%', height: '100%', objectFit: 'cover'}} muted />
          ) : (
            <Img src={resolved} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
          )}
        </AbsoluteFill>
      ) : null}

      {/* Spec §3.2 — three-layer readability overlay. */}
      <AbsoluteFill style={{backgroundColor: `rgba(0,0,0,${ov.fullDarkenOpacity})`}} />
      <AbsoluteFill
        style={{
          background: `linear-gradient(180deg, rgba(0,0,0,0) 55%, rgba(0,0,0,${ov.bottomGradientOpacity}) 100%)`,
        }}
      />
      <AbsoluteFill
        style={{
          background: `radial-gradient(ellipse at center, rgba(0,0,0,${ov.centerTextScrimOpacity}) 0%, rgba(0,0,0,0) 65%)`,
        }}
      />
    </AbsoluteFill>
  );
};

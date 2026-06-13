/**
 * Background media + smart fit + readability overlays (spec §3).
 *
 * Renders the scene background as cover-fit video/image with a blurred
 * duplicate fill behind, plus three independently controllable overlays
 * (full darken + bottom gradient + center text scrim).
 *
 * The primary media also carries a subtle per-scene Ken-Burns motion driven by
 * ``scene.motion`` (spec: push_in / object_reveal / face_cut / text_pop /
 * crop_shift). Even "none"/"static" gets a barely-perceptible drift so an
 * image-backed scene never reads as a frozen slide — the slide-deck feel the
 * Shorts prompt explicitly forbids and that tanks 45+ retention.
 */
import React from 'react';
import {AbsoluteFill, Img, OffthreadVideo, staticFile, useCurrentFrame, interpolate, Easing} from 'remotion';
import {SHORT_OVERLAYS, OverlayKey} from './ShortLayoutConstants';
import type {CropPlan} from '../render-props';

export type ShortBackgroundProps = {
  src?: string;
  /** "video" | "image". Inferred from src extension when omitted. */
  kind?: 'video' | 'image';
  /** Spec §3.2 — picks the overlay preset for this scene mood. */
  overlay?: OverlayKey;
  /** Spec §3 — per-scene camera motion intent (LLM-planned, QA-enforced). */
  motion?: string;
  cropPlan?: CropPlan;
  /** Scene length in frames; normalizes the motion progress 0→1. */
  durationInFrames?: number;
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

const lerp = (a: number, b: number, p: number) => a + (b - a) * p;

/**
 * Maps a scene's motion intent to a {scale, x%} pair animated by progress p∈[0,1].
 *
 * Constraints that keep edges hidden (media is object-fit:cover):
 *  - scale never drops below 1.0 (start of object_reveal is the only zoom-out,
 *    and it ENDS at exactly 1.0 — never under).
 *  - translateX is kept within the headroom the active scale provides
 *    (scale 1.06 → ±3% safe; we cap at ±2.2%).
 */
function shortMotion(motion: string | undefined, p: number): {scale: number; x: number} {
  switch ((motion || '').toLowerCase()) {
    // Hook punch-in — noticeable slow zoom toward the subject.
    case 'push_in':
    case 'slow_push':
    case 'slow_zoom':
      return {scale: lerp(1.0, 1.18, p), x: 0};
    // Pull back noticeably to reveal context.
    case 'object_reveal':
      return {scale: lerp(1.22, 1.0, p), x: 0};
    // Dramatic, tighter punch-in on a face/subject.
    case 'face_cut':
      return {scale: lerp(1.05, 1.25, p), x: 0};
    // Text is the star — keep the background relatively calm but alive.
    case 'text_pop':
      return {scale: lerp(1.0, 1.06, p), x: 0};
    // Noticeable lateral drift; scale 1.15 provides ±7.5% headroom.
    case 'crop_shift':
    case 'pan_right':
      return {scale: 1.15, x: lerp(0, -6.0, p)};
    case 'pan_left':
      return {scale: 1.15, x: lerp(0, 6.0, p)};
    // Never truly frozen — drift defeats the slide-deck look.
    case 'none':
    case 'static':
    case '':
      return {scale: lerp(1.0, 1.05, p), x: 0};
    default:
      return {scale: lerp(1.0, 1.08, p), x: 0};
  }
}

function cropTransform(cropPlan: CropPlan | undefined): {scale: number; x: number; y: number} {
  if (!cropPlan) return {scale: 1, x: 0, y: 0};
  const scale = Math.max(1, cropPlan.scale ?? 1);
  switch ((cropPlan.anchor || '').toLowerCase()) {
    case 'center-left':
    case 'left':
      return {scale, x: 4, y: 0};
    case 'center-right':
    case 'right':
      return {scale, x: -4, y: 0};
    case 'top':
      return {scale, x: 0, y: 3};
    case 'bottom':
      return {scale, x: 0, y: -3};
    default:
      return {scale, x: 0, y: 0};
  }
}

export const ShortBackground: React.FC<ShortBackgroundProps> = ({
  src,
  kind,
  overlay = 'default',
  motion,
  cropPlan,
  durationInFrames,
}) => {
  const resolved = resolveSrc(src);
  const actualKind = kind ?? inferKind(src);
  const ov = SHORT_OVERLAYS[overlay] ?? SHORT_OVERLAYS.default;

  const frame = useCurrentFrame();
  const span = durationInFrames && durationInFrames > 1 ? durationInFrames - 1 : 1;
  const progress = interpolate(frame, [0, span], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.ease),
  });
  const m = shortMotion(motion, progress);
  const c = cropTransform(cropPlan);
  const motionTransform = `translateX(${m.x}%) scale(${m.scale})`;
  const mediaTransform = cropPlan ? `translate(${c.x + m.x}%, ${c.y}%) scale(${c.scale * m.scale})` : motionTransform;

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

      {/* Primary cover-fit media — carries the per-scene Ken-Burns motion. */}
      {resolved ? (
        <AbsoluteFill style={{transform: mediaTransform, transformOrigin: 'center center'}}>
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

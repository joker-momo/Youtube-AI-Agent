import React from 'react';
import {AbsoluteFill, Img, staticFile, useCurrentFrame, interpolate, Easing} from 'remotion';
import {Video as MediaVideo} from '@remotion/media';
import type {CompiledVisualTrack, CropPlan} from '../render-props';

export type ShortMediaLayerProps = {
  src?: string;
  kind?: 'video' | 'image';
  motion?: string;
  motionPlan?: CompiledVisualTrack['motion_plan'];
  cropPlan?: CropPlan;
  durationInFrames?: number;
  trimBeforeInFrames?: number;
  trimEndInFrames?: number | null;
  trimTimebaseFps?: number;
  compositionFps?: number;
  playbackRate?: number;
  legacyStaticDrift?: boolean;
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

function shortMotion(motion: string | undefined, p: number, legacyStaticDrift: boolean): {scale: number; x: number} {
  switch ((motion || '').toLowerCase()) {
    case 'push_in':
    case 'slow_push':
    case 'slow_zoom':
      return {scale: lerp(1.0, 1.18, p), x: 0};
    case 'object_reveal':
      return {scale: lerp(1.22, 1.0, p), x: 0};
    case 'face_cut':
      return {scale: lerp(1.05, 1.25, p), x: 0};
    case 'text_pop':
      return {scale: lerp(1.0, 1.06, p), x: 0};
    case 'crop_shift':
    case 'pan_right':
      return {scale: 1.15, x: lerp(0, -6.0, p)};
    case 'pan_left':
      return {scale: 1.15, x: lerp(0, 6.0, p)};
    case 'none':
    case 'static':
    case '':
      return legacyStaticDrift ? {scale: lerp(1.0, 1.05, p), x: 0} : {scale: 1.0, x: 0};
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

function normalizedTrimFrame(value: number | null | undefined, trimTimebaseFps: number | undefined, compositionFps: number | undefined): number | undefined {
  if (!value) return undefined;
  const timebase = trimTimebaseFps && trimTimebaseFps > 0 ? trimTimebaseFps : compositionFps;
  if (!timebase || !compositionFps) return value;
  return Math.round((value * compositionFps) / timebase);
}

export const ShortMediaLayer: React.FC<ShortMediaLayerProps> = ({
  src,
  kind,
  motion,
  motionPlan,
  cropPlan,
  durationInFrames,
  trimBeforeInFrames,
  trimEndInFrames,
  trimTimebaseFps,
  compositionFps,
  playbackRate = 1.0,
  legacyStaticDrift = false,
}) => {
  const resolved = resolveSrc(src);
  const actualKind = kind ?? inferKind(src);
  const frame = useCurrentFrame();
  const span = durationInFrames && durationInFrames > 1 ? durationInFrames - 1 : 1;
  const progress = interpolate(frame, [0, span], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: Easing.inOut(Easing.ease),
  });
  const plannedMotion = motionPlan?.name ?? motion;
  const m = shortMotion(plannedMotion, progress, legacyStaticDrift);
  const c = cropTransform(cropPlan);
  const mediaTransform = cropPlan ? `translate(${c.x + m.x}%, ${c.y}%) scale(${c.scale * m.scale})` : `translateX(${m.x}%) scale(${m.scale})`;
  const trimBefore = normalizedTrimFrame(trimBeforeInFrames, trimTimebaseFps, compositionFps);
  const normalizedTrimEnd = normalizedTrimFrame(trimEndInFrames, trimTimebaseFps, compositionFps);
  const trimAfter =
    normalizedTrimEnd && (!trimBefore || normalizedTrimEnd > trimBefore) ? normalizedTrimEnd : undefined;

  return (
    <AbsoluteFill style={{backgroundColor: '#0b1020'}}>
      {resolved ? (
        <AbsoluteFill style={{filter: 'blur(40px) brightness(0.55)', transform: 'scale(1.15)'}}>
          {actualKind === 'video' ? (
            <MediaVideo src={resolved} style={{width: '100%', height: '100%', objectFit: 'cover'}} muted trimBefore={trimBefore} trimAfter={trimAfter} playbackRate={playbackRate} />
          ) : (
            <Img src={resolved} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
          )}
        </AbsoluteFill>
      ) : null}
      {resolved ? (
        <AbsoluteFill style={{transform: mediaTransform, transformOrigin: 'center center'}}>
          {actualKind === 'video' ? (
            <MediaVideo src={resolved} style={{width: '100%', height: '100%', objectFit: 'cover'}} muted trimBefore={trimBefore} trimAfter={trimAfter} playbackRate={playbackRate} />
          ) : (
            <Img src={resolved} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
          )}
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};

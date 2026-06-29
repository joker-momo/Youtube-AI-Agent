import React from 'react';
import {AbsoluteFill, interpolate, Loop, OffthreadVideo, Sequence, useCurrentFrame} from 'remotion';
import type {ElenaCue, ElenaCuesDoc} from './render-props';
import {mediaSrc} from './render-props';

// Treatment sizes for a 1920x1080 composition (spec §6.3).
const SIZE_PX: Record<string, number> = {circle: 240, large: 384};
const MARGIN_RIGHT = 72;
const MARGIN_BOTTOM = 120;
// Both Elena clips are ~10.0s @ 24fps → ~300 frames in the 30fps comp; ~270 remain
// after the ~1s entry trim. A cue now spans its FULL (longer) scene, so loop a
// safely-shorter 264-frame (~8.8s) window of the talking clip to keep Elena
// on-screen edge-to-edge with no frozen tail and no mid-sentence cut.
const ELENA_LOOP_FRAMES = 264;
// Cross-dissolve window at each loop seam so the talking clip never hard-cuts.
const ELENA_XFADE = 14;

// One looped copy of the talking clip (muted; 24fps source plays at natural rate).
const ElenaLoop: React.FC<{src: string; trimBefore?: number}> = ({src, trimBefore}) => (
  <Loop durationInFrames={ELENA_LOOP_FRAMES}>
    <OffthreadVideo
      src={src}
      muted
      trimBefore={trimBefore}
      // Crop to face + upper chest (face ~65-75% of the frame).
      style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center 30%'}}
    />
  </Loop>
);

/**
 * One Elena appearance. Always muted (hard rule). The clip keeps its original
 * background — only a circular / rounded mask + border + soft shadow are applied.
 * Bottom-right, clear of the bottom-center subtitle band.
 *
 * Seamless loop: the ~8.8s clip is shorter than its (full-scene) cue, so it loops.
 * A raw `<Loop>` hard-cuts at each seam (the pose visibly jumps). To smooth it, two
 * phase-offset copies cross-dissolve: copy A loops normally; copy B is half a period
 * ahead and only fades in to COVER A's seam (where B is mid-clip and smooth). The
 * opacities sum to 1, and B's own seam falls where B is fully hidden — so there is
 * never a visible cut.
 */
const ElenaClip: React.FC<{cue: ElenaCue}> = ({cue}) => {
  const frame = useCurrentFrame();
  if (cue.mode !== 'talking' || !cue.asset_ref) return null;
  const treatment = cue.treatment ?? 'circle';
  const size = SIZE_PX[treatment] ?? SIZE_PX.circle;
  const isCircle = treatment === 'circle';
  const src = mediaSrc(cue.asset_ref);
  const trimBefore = cue.source_trim_frames ? Math.max(0, Math.round(cue.source_trim_frames)) : undefined;

  const P = ELENA_LOOP_FRAMES;
  const s = ((frame % P) + P) % P;
  const distToSeam = Math.min(s, P - s); // frames to the nearest copy-A loop seam
  const aOpacity = interpolate(distToSeam, [0, ELENA_XFADE], [0, 1], {extrapolateRight: 'clamp'});
  const bOpacity = 1 - aOpacity;

  return (
    <div
      style={{
        position: 'absolute',
        right: MARGIN_RIGHT,
        bottom: MARGIN_BOTTOM,
        width: size,
        height: size,
        borderRadius: isCircle ? '50%' : 24,
        overflow: 'hidden',
        border: '5px solid rgba(255,255,255,0.92)',
        boxShadow: '0 12px 34px rgba(0,0,0,0.38)',
      }}
    >
      <div style={{position: 'absolute', inset: 0, opacity: aOpacity}}>
        <ElenaLoop src={src} trimBefore={trimBefore} />
      </div>
      <div style={{position: 'absolute', inset: 0, opacity: bOpacity}}>
        {/* Half-period-ahead copy: covers copy A's seam with smooth mid-clip frames. */}
        <Sequence from={-Math.floor(P / 2)} layout="none">
          <ElenaLoop src={src} trimBefore={trimBefore} />
        </Sequence>
      </div>
    </div>
  );
};

/**
 * Elena presenter overlay. Renders each talking cue as an independent
 * `<Sequence>` so an appearance mounts and unmounts on its own frame window;
 * `hidden` stretches (and the gaps between cues) simply render nothing. Sits above
 * the B-roll/graphic layer and never overlaps the subtitle band.
 */
export const ChannelElenaPresenter: React.FC<{elena: ElenaCuesDoc}> = ({elena}) => {
  if (!elena || !Array.isArray(elena.cues)) return null;
  return (
    <AbsoluteFill>
      {elena.cues.map((cue, i) =>
        cue.mode === 'talking' ? (
          <Sequence key={i} from={cue.start_frame} durationInFrames={cue.duration_frames} name={`elena-${i}`}>
            <ElenaClip cue={cue} />
          </Sequence>
        ) : null,
      )}
    </AbsoluteFill>
  );
};

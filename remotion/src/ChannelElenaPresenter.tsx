import React from 'react';
import {AbsoluteFill, OffthreadVideo, Sequence} from 'remotion';
import type {ElenaCue, ElenaCuesDoc} from './render-props';
import {mediaSrc} from './render-props';

// Treatment sizes for a 1920x1080 composition (spec §6.3).
const SIZE_PX: Record<string, number> = {circle: 240, large: 384};
const MARGIN_RIGHT = 72;
const MARGIN_BOTTOM = 120;

/**
 * One Elena appearance. Always muted (hard rule). The 24fps source plays at its
 * natural rate inside the 30fps composition (no time-stretch). The clip keeps its
 * original background — only a circular / rounded mask + border + soft shadow are
 * applied. Bottom-right, clear of the bottom-center subtitle band.
 */
const ElenaClip: React.FC<{cue: ElenaCue}> = ({cue}) => {
  if (cue.mode !== 'talking' || !cue.asset_ref) return null;
  const treatment = cue.treatment ?? 'circle';
  const size = SIZE_PX[treatment] ?? SIZE_PX.circle;
  const isCircle = treatment === 'circle';
  const trimBefore = cue.source_trim_frames ? Math.max(0, Math.round(cue.source_trim_frames)) : undefined;
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
      <OffthreadVideo
        src={mediaSrc(cue.asset_ref)}
        muted
        trimBefore={trimBefore}
        // Crop to face + upper chest (face ~65-75% of the frame).
        style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'center 30%'}}
      />
    </div>
  );
};

/**
 * Elena presenter overlay. Renders each talking cue as an independent
 * `<Sequence>` so a 5-10s appearance mounts and unmounts on its own frame window;
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

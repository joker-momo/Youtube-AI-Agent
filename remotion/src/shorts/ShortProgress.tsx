/**
 * Optional thin top progress bar (spec §16.1).
 *
 * Subtle accent line that fills left→right over the Short's duration so the
 * viewer has a non-verbal sense of how much is left. Opacity 0.65 so it
 * never competes with hook/body text.
 */
import React from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate} from 'remotion';

export const ShortProgress: React.FC<{accent?: string}> = ({accent = '#F5C24B'}) => {
  const frame = useCurrentFrame();
  const {durationInFrames} = useVideoConfig();
  const pct = interpolate(frame, [0, Math.max(1, durationInFrames)], [0, 100], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <AbsoluteFill style={{pointerEvents: 'none'}}>
      <div
        style={{
          position: 'absolute',
          top: 0,
          left: 0,
          height: 4,
          width: `${pct}%`,
          background: accent,
          opacity: 0.65,
        }}
      />
    </AbsoluteFill>
  );
};

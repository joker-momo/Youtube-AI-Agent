import React from 'react';
import {AbsoluteFill, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';

// YouTube-overlay-safe region for a 1080x1920 Short (spec §Feature A): left of
// the action rail, above the bottom title/description band. Named constants so
// the safe area is reviewable instead of hidden across nested style objects.
const SAFE_LEFT = 120;
const SAFE_RIGHT = 840;
const SAFE_TOP = 1050;
const SAFE_BOTTOM = 1500;

export type EndEngagementCueProps = {
  channelName?: string;
};

/**
 * Deterministic final-3s Like/Subscribe cue. Mounted by InfographicShort inside
 * a <Sequence> covering only the last round(3*fps) frames, so local frame 0 is
 * the start of the cue. All motion is Remotion frame math (spring/interpolate);
 * no runtime randomness, no remote assets, no sound effects.
 *
 * Phase timeline (seconds within the cue):
 *   0.0-0.6  panel slides/fades in, poster dims by max 18%
 *   0.6-1.3  finger presses the Like control -> thumb bounces, "ME GUSTA" active
 *   1.3-2.1  finger presses the red "SUSCRÍBETE" button
 *   2.1-3.0  hold "✓ SUSCRITO", bell wiggles, channel name visible
 */
export const EndEngagementCue: React.FC<EndEngagementCueProps> = ({channelName}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const t = frame / fps; // seconds inside the cue

  // --- entrance: dim + slide-in (0.0-0.6s) ---------------------------------
  const dim = interpolate(t, [0, 0.6], [0, 0.18], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const enter = spring({frame, fps, config: {damping: 200, stiffness: 120}});
  const panelY = interpolate(enter, [0, 1], [60, 0]);
  const panelOpacity = interpolate(t, [0, 0.4], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  // --- like press (0.6-1.3s) ------------------------------------------------
  const likePressFrame = Math.round(0.75 * fps);
  const liked = frame >= likePressFrame;
  const likeBounce = spring({
    frame: Math.max(0, frame - likePressFrame),
    fps,
    config: {damping: 9, stiffness: 220, mass: 0.6},
  });
  const likeScale = liked ? 1 + 0.25 * (1 - Math.abs(likeBounce - 1)) : 1;

  // --- subscribe press (1.3-2.1s): the RED button stays visible through the
  // whole press phase; the state flips to SUSCRITO only at 2.1s (spec timeline).
  const subscribePressFrame = Math.round(1.6 * fps);
  const subscribedFrame = Math.round(2.1 * fps);
  const subscribed = frame >= subscribedFrame;
  const subscribeBounce = spring({
    frame: Math.max(0, frame - subscribePressFrame),
    fps,
    config: {damping: 10, stiffness: 200, mass: 0.7},
  });
  const subscribePressed = frame >= subscribePressFrame;
  const subscribeScale = subscribePressed ? 1 + 0.12 * (1 - Math.abs(subscribeBounce - 1)) : 1;

  // --- bell wiggle after subscribing (2.1-2.8s) ------------------------------
  const bellRotation = interpolate(
    t,
    [2.1, 2.25, 2.4, 2.55, 2.7, 2.8],
    [0, -18, 14, -9, 5, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  // --- pressing finger: moves from Like to Subscribe, then leaves ------------
  const fingerX = interpolate(t, [0.4, 0.75, 1.25, 1.6, 2.1], [430, 240, 240, 520, 620], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const fingerY = interpolate(t, [0.4, 0.75, 1.25, 1.6, 2.1], [SAFE_BOTTOM, 170, 170, 300, 420], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const fingerOpacity = interpolate(t, [0.4, 0.6, 1.9, 2.2], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const pressPulse =
    (frame === likePressFrame || frame === subscribePressFrame) ? 0.85 : 1;

  const likeColor = liked ? '#1B66C9' : '#4B5563';

  return (
    <AbsoluteFill>
      {/* Poster dim — capped at 18% per spec. */}
      <AbsoluteFill style={{backgroundColor: `rgba(0,0,0,${dim})`}} />
      <div
        style={{
          position: 'absolute',
          left: SAFE_LEFT,
          top: SAFE_TOP,
          width: SAFE_RIGHT - SAFE_LEFT,
          maxHeight: SAFE_BOTTOM - SAFE_TOP,
          transform: `translateY(${panelY}px)`,
          opacity: panelOpacity,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 28,
          background: 'rgba(255,255,255,0.96)',
          borderRadius: 28,
          padding: '36px 32px',
          boxShadow: '0 12px 40px rgba(0,0,0,0.35)',
          fontFamily: 'Montserrat, "Helvetica Neue", Arial, sans-serif',
        }}
      >
        {/* Like row */}
        <div style={{display: 'flex', alignItems: 'center', gap: 24}}>
          <svg
            width={96}
            height={96}
            viewBox="0 0 24 24"
            style={{transform: `scale(${likeScale})`}}
          >
            <path
              d="M2 21h4V9H2v12zM22 10c0-1.1-.9-2-2-2h-6.3l1-4.6.03-.32c0-.41-.17-.79-.44-1.06L13.2 1 6.6 7.6C6.2 7.9 6 8.4 6 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"
              fill={likeColor}
            />
          </svg>
          <span style={{fontSize: 58, fontWeight: 800, color: likeColor, letterSpacing: 1}}>
            ME GUSTA
          </span>
        </div>

        {/* Subscribe button */}
        <div
          style={{
            transform: `scale(${subscribeScale})`,
            background: subscribed ? '#3F3F46' : '#E11D2A',
            color: '#fff',
            fontSize: 56,
            fontWeight: 800,
            letterSpacing: 1,
            padding: '20px 56px',
            borderRadius: 14,
          }}
        >
          {subscribed ? '✓ SUSCRITO' : 'SUSCRÍBETE'}
        </div>

        {/* Bell + channel name — the hold state: visible ONLY from 2.1s (spec
            timeline), never during the entrance or press phases. */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 18,
            minHeight: 64,
            opacity: subscribed ? 1 : 0,
          }}
        >
          <svg
            width={56}
            height={56}
            viewBox="0 0 24 24"
            style={{
              transform: `rotate(${bellRotation}deg)`,
              transformOrigin: '50% 10%',
            }}
          >
            {/* bell icon */}
            <path
              d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5S10.5 3.17 10.5 4v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2z"
              fill="#1F2937"
            />
          </svg>
          {channelName ? (
            <span style={{fontSize: 40, fontWeight: 700, color: '#1F2937'}}>{channelName}</span>
          ) : null}
        </div>
      </div>

      {/* Pressing finger (simple deterministic cursor) */}
      <div
        style={{
          position: 'absolute',
          left: SAFE_LEFT + fingerX,
          top: SAFE_TOP + fingerY,
          opacity: fingerOpacity,
          transform: `scale(${pressPulse})`,
          width: 64,
          height: 64,
          borderRadius: '50%',
          background: 'rgba(31,41,55,0.85)',
          border: '4px solid rgba(255,255,255,0.9)',
          boxShadow: '0 6px 18px rgba(0,0,0,0.4)',
        }}
      />
    </AbsoluteFill>
  );
};

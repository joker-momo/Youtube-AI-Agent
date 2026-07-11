import React from 'react';
import {AbsoluteFill, Audio, interpolate, Sequence, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';

// YouTube-overlay-safe region for a 1080x1920 Short: keep the panel clear of
// the action rail and the bottom title/description band. Named constants so
// the safe area is reviewable rather than hidden in nested style objects.
const SAFE_LEFT = 120;
const SAFE_RIGHT = 840;
const SAFE_TOP = 1050;
const SAFE_BOTTOM = 1500;

// Fixed internal layout (px, relative to the panel's top-left) so the pressing
// finger can be keyframed onto EXACT button coordinates — the press animation
// and the button reaction must land on the same frame at the same spot.
const PANEL_WIDTH = 600;
const PANEL_PAD = 36;
const LIKE_ROW_TOP = PANEL_PAD;              // like row: 36..132
const LIKE_ROW_HEIGHT = 96;
const SUBSCRIBE_TOP = LIKE_ROW_TOP + LIKE_ROW_HEIGHT + 28; // 160..268
const SUBSCRIBE_HEIGHT = 108;
const BELL_ROW_TOP = SUBSCRIBE_TOP + SUBSCRIBE_HEIGHT + 24; // 292..356
const PANEL_HEIGHT = BELL_ROW_TOP + 64 + PANEL_PAD;

// Finger press targets (panel-relative): center of the thumb icon, center of
// the subscribe button.
const LIKE_TARGET = {x: 190, y: LIKE_ROW_TOP + LIKE_ROW_HEIGHT / 2};
const SUB_TARGET = {x: PANEL_WIDTH / 2, y: SUBSCRIBE_TOP + SUBSCRIBE_HEIGHT / 2};

export type EndEngagementCueProps = {
  channelName?: string;
};

/**
 * Deterministic final-3s Like/Subscribe cue. Mounted by InfographicShort inside
 * a <Sequence> covering only the last round(3*fps) frames, so local frame 0 is
 * the start of the cue. All motion is Remotion frame math (spring/interpolate).
 *
 * Phase timeline (seconds within the cue):
 *   0.0-0.6  panel slides/fades in centered, poster dims by max 18%
 *   0.75     finger presses Like (pop SFX) -> thumb bounces, ME GUSTA active
 *   1.6      finger presses the red SUSCRÍBETE (bell SFX)
 *   2.1-3.0  hold ✓ SUSCRITO, bell wiggles, channel name visible
 */
export const EndEngagementCue: React.FC<EndEngagementCueProps> = ({channelName}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const t = frame / fps; // seconds inside the cue

  // Panel centered HORIZONTALLY on the video; SAFE_LEFT/RIGHT clamp it if a
  // composition ever gets narrower than the panel + margins.
  const panelLeft = Math.max(
    SAFE_LEFT,
    Math.min((width - PANEL_WIDTH) / 2, SAFE_RIGHT - PANEL_WIDTH),
  );
  const panelTop = SAFE_TOP;

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

  // --- like press (finger arrives 0.70s, presses 0.75s) ---------------------
  const likePressFrame = Math.round(0.75 * fps);
  const liked = frame >= likePressFrame;
  const likeBounce = spring({
    frame: Math.max(0, frame - likePressFrame),
    fps,
    config: {damping: 9, stiffness: 220, mass: 0.6},
  });
  const likeScale = liked ? 1 + 0.25 * (1 - Math.abs(likeBounce - 1)) : 1;

  // --- subscribe press (finger arrives 1.55s, presses 1.6s) -----------------
  // The RED button stays through the whole 1.3-2.1s press phase; the state
  // flips to SUSCRITO only at 2.1s (spec timeline).
  const subscribePressFrame = Math.round(1.6 * fps);
  const subscribedFrame = Math.round(2.1 * fps);
  const subscribed = frame >= subscribedFrame;
  const subscribePressed = frame >= subscribePressFrame;
  const subscribeBounce = spring({
    frame: Math.max(0, frame - subscribePressFrame),
    fps,
    config: {damping: 10, stiffness: 200, mass: 0.7},
  });
  const subscribeScale = subscribePressed ? 1 + 0.12 * (1 - Math.abs(subscribeBounce - 1)) : 1;

  // --- bell wiggle after subscribing (2.1-2.8s) ------------------------------
  const bellRotation = interpolate(
    t,
    [2.1, 2.25, 2.4, 2.55, 2.7, 2.8],
    [0, -18, 14, -9, 5, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );

  // --- pressing finger: keyframed onto the EXACT button targets so the press
  // and the button reaction share the same frame and the same coordinates.
  const fingerX = interpolate(
    t,
    [0.45, 0.7, 1.3, 1.55, 2.05, 2.3],
    [PANEL_WIDTH / 2, LIKE_TARGET.x, LIKE_TARGET.x, SUB_TARGET.x, SUB_TARGET.x, PANEL_WIDTH + 80],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );
  const fingerY = interpolate(
    t,
    [0.45, 0.7, 1.3, 1.55, 2.05, 2.3],
    [PANEL_HEIGHT + 120, LIKE_TARGET.y, LIKE_TARGET.y, SUB_TARGET.y, SUB_TARGET.y, PANEL_HEIGHT + 160],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'},
  );
  const fingerOpacity = interpolate(t, [0.45, 0.6, 2.05, 2.3], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  // Quick dip exactly on each press frame (4-frame squeeze) — synced with the
  // button springs keyed on the same frames.
  const pressDip = (pressFrame: number) =>
    interpolate(frame, [pressFrame - 2, pressFrame, pressFrame + 2], [1, 0.8, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
  const fingerScale = pressDip(likePressFrame) * pressDip(subscribePressFrame);

  const likeColor = liked ? '#1B66C9' : '#4B5563';

  return (
    <AbsoluteFill>
      {/* Press sound effects — self-synthesized (ffmpeg sine synthesis), no
          third-party audio. Fired exactly on the press frames. */}
      <Sequence from={likePressFrame} name="LikePopSfx">
        <Audio src={staticFile('sfx/like_pop.wav')} volume={0.7} />
      </Sequence>
      <Sequence from={subscribePressFrame} name="BellSfx">
        <Audio src={staticFile('sfx/bell_ding.wav')} volume={0.6} />
      </Sequence>

      {/* Poster dim — capped at 18% per spec. */}
      <AbsoluteFill style={{backgroundColor: `rgba(0,0,0,${dim})`}} />
      <div
        style={{
          position: 'absolute',
          left: panelLeft,
          top: panelTop,
          width: PANEL_WIDTH,
          height: PANEL_HEIGHT,
          transform: `translateY(${panelY}px)`,
          opacity: panelOpacity,
          background: 'rgba(255,255,255,0.96)',
          borderRadius: 28,
          boxShadow: '0 12px 40px rgba(0,0,0,0.35)',
          fontFamily: 'Montserrat, "Helvetica Neue", Arial, sans-serif',
        }}
      >
        {/* Like row — fixed position so the finger target is exact. */}
        <div
          style={{
            position: 'absolute',
            top: LIKE_ROW_TOP,
            left: 0,
            width: PANEL_WIDTH,
            height: LIKE_ROW_HEIGHT,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 24,
          }}
        >
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
          <span style={{fontSize: 56, fontWeight: 800, color: likeColor, letterSpacing: 1}}>
            ME GUSTA
          </span>
        </div>

        {/* Subscribe button — fixed position, centered. */}
        <div
          style={{
            position: 'absolute',
            top: SUBSCRIBE_TOP,
            left: 0,
            width: PANEL_WIDTH,
            height: SUBSCRIBE_HEIGHT,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <div
            style={{
              transform: `scale(${subscribeScale})`,
              background: subscribed ? '#3F3F46' : '#E11D2A',
              color: '#fff',
              fontSize: 54,
              fontWeight: 800,
              letterSpacing: 1,
              padding: '18px 52px',
              borderRadius: 14,
            }}
          >
            {subscribed ? '✓ SUSCRITO' : 'SUSCRÍBETE'}
          </div>
        </div>

        {/* Bell + channel name — the hold state: visible ONLY from 2.1s. */}
        <div
          style={{
            position: 'absolute',
            top: BELL_ROW_TOP,
            left: 0,
            width: PANEL_WIDTH,
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 18,
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
            <span style={{fontSize: 38, fontWeight: 700, color: '#1F2937'}}>{channelName}</span>
          ) : null}
        </div>
      </div>

      {/* Pressing finger (deterministic cursor, lands exactly on the targets) */}
      <div
        style={{
          position: 'absolute',
          left: panelLeft + fingerX - 32,
          top: panelTop + fingerY - 32,
          opacity: fingerOpacity,
          transform: `scale(${fingerScale})`,
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

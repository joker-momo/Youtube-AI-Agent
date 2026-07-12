import React from 'react';
import {AbsoluteFill, Audio, interpolate, Sequence, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {
  BELL_ROW_TOP,
  LIKE_ICON_BOX,
  LIKE_TEXT_LEFT,
  PANEL_HEIGHT,
  PANEL_WIDTH,
  panelLeftFor,
  panelTopFor,
  pointerOpacityAt,
  pointerPositionAt,
  pressFrames,
  SFX_FILES,
  sfxFrames,
  SUBSCRIBE_HEIGHT,
  SUBSCRIBE_TOP,
} from './endEngagementCueTiming';

export type EndEngagementCueProps = {
  channelName?: string;
};

/**
 * Deterministic final-3s Like/Subscribe cue. Mounted by InfographicShort inside
 * a <Sequence> covering only the last cue frames, so local frame 0 is the cue
 * start. All timing/geometry comes from endEngagementCueTiming.ts (pure math,
 * executable in tests); this file only renders it with Remotion frame motion.
 *
 * Phase timeline (seconds within the cue):
 *   0.0-0.6  panel slides/fades in centered, poster dims by max 18%
 *   0.75     pointer presses Like (pop SFX) -> thumb bounces, ME GUSTA active
 *   1.6      pointer presses the red SUSCRÍBETE (bell SFX)
 *   2.1-3.0  hold ✓ SUSCRITO, bell wiggles, channel name visible
 */
export const EndEngagementCue: React.FC<EndEngagementCueProps> = ({channelName}) => {
  const frame = useCurrentFrame();
  const {fps, width} = useVideoConfig();
  const t = frame / fps; // seconds inside the cue
  const presses = pressFrames(fps);

  // Panel centered HORIZONTALLY on the video (exact math lives in the timing
  // module so tests execute it: at 1080 wide, panelLeft + PANEL_WIDTH/2 == 540).
  const panelLeft = panelLeftFor(width);
  const panelTop = panelTopFor();
  const sfx = sfxFrames(fps);

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

  // --- like press ------------------------------------------------------------
  const liked = frame >= presses.like;
  const likeBounce = spring({
    frame: Math.max(0, frame - presses.like),
    fps,
    config: {damping: 9, stiffness: 220, mass: 0.6},
  });
  const likeScale = liked ? 1 + 0.25 * (1 - Math.abs(likeBounce - 1)) : 1;

  // --- subscribe press: the RED button stays through the whole press phase;
  // the state flips to SUSCRITO only at the subscribed frame (2.1s).
  const subscribed = frame >= presses.subscribed;
  const subscribePressed = frame >= presses.subscribe;
  const subscribeBounce = spring({
    frame: Math.max(0, frame - presses.subscribe),
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

  // --- pointer: geometry lives in endEngagementCueTiming.pointerPositionAt so
  // tests can execute the exact same math the render uses.
  const pointer = pointerPositionAt(frame, fps);
  // Opacity comes from the timing module too: fully invisible before the
  // subscribed frame, so the pointer can never cover the bell/channel row.
  const pointerOpacity = pointerOpacityAt(frame, fps);
  // Quick dip exactly on each press frame (4-frame squeeze) — synced with the
  // button springs keyed on the same frames.
  const pressDip = (pressFrame: number) =>
    interpolate(frame, [pressFrame - 2, pressFrame, pressFrame + 2], [1, 0.8, 1], {
      extrapolateLeft: 'clamp',
      extrapolateRight: 'clamp',
    });
  const pointerScale = pressDip(presses.like) * pressDip(presses.subscribe);

  const likeColor = liked ? '#1B66C9' : '#4B5563';

  return (
    <AbsoluteFill>
      {/* Press sound effects — self-synthesized (ffmpeg sine synthesis), no
          third-party audio. Fired exactly on the press frames. */}
      <Sequence from={sfx.likePop} name="LikePopSfx">
        <Audio src={staticFile(SFX_FILES.likePop)} volume={0.7} />
      </Sequence>
      <Sequence from={sfx.bellDing} name="BellSfx">
        <Audio src={staticFile(SFX_FILES.bellDing)} volume={0.6} />
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
        {/* Like thumb at its EXPLICIT box (LIKE_ICON_BOX) — LIKE_TARGET is
            derived from this exact geometry, so the pointer press lands
            centered inside the thumb, never on the text. */}
        <svg
          width={LIKE_ICON_BOX.size}
          height={LIKE_ICON_BOX.size}
          viewBox="0 0 24 24"
          style={{
            position: 'absolute',
            left: LIKE_ICON_BOX.left,
            top: LIKE_ICON_BOX.top,
            transform: `scale(${likeScale})`,
          }}
        >
          <path
            d="M2 21h4V9H2v12zM22 10c0-1.1-.9-2-2-2h-6.3l1-4.6.03-.32c0-.41-.17-.79-.44-1.06L13.2 1 6.6 7.6C6.2 7.9 6 8.4 6 9v10c0 1.1.9 2 2 2h9c.83 0 1.54-.5 1.84-1.22l3.02-7.05c.09-.23.14-.47.14-.73v-2z"
            fill={likeColor}
          />
        </svg>
        <span
          style={{
            position: 'absolute',
            left: LIKE_TEXT_LEFT,
            top: LIKE_ICON_BOX.top,
            height: LIKE_ICON_BOX.size,
            display: 'flex',
            alignItems: 'center',
            fontSize: 56,
            fontWeight: 800,
            color: likeColor,
            letterSpacing: 1,
          }}
        >
          ME GUSTA
        </span>

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

      {/* Pressing pointer (deterministic cursor, lands exactly on the targets) */}
      <div
        style={{
          position: 'absolute',
          left: panelLeft + pointer.x - 32,
          top: panelTop + pointer.y - 32,
          opacity: pointerOpacity,
          transform: `scale(${pointerScale})`,
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

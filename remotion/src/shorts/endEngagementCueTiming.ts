/**
 * Pure timing/geometry contract for the final-3s engagement cue.
 *
 * No Remotion imports: everything here is plain math so tests can execute it
 * directly (compile with tsc, run under node) instead of trusting source-string
 * greps. EndEngagementCue.tsx consumes these as its single source of truth.
 */

// YouTube-overlay-safe region for a 1080x1920 Short.
export const SAFE_LEFT = 120;
export const SAFE_RIGHT = 840;
export const SAFE_TOP = 1050;
export const SAFE_BOTTOM = 1500;

// Fixed internal layout (px, relative to the panel's top-left) so the pressing
// pointer can be keyframed onto EXACT control coordinates.
export const PANEL_WIDTH = 600;
export const PANEL_PAD = 36;
export const LIKE_ROW_TOP = PANEL_PAD;
export const LIKE_ROW_HEIGHT = 96;
export const SUBSCRIBE_TOP = LIKE_ROW_TOP + LIKE_ROW_HEIGHT + 28;
export const SUBSCRIBE_HEIGHT = 108;
export const BELL_ROW_TOP = SUBSCRIBE_TOP + SUBSCRIBE_HEIGHT + 24;
export const PANEL_HEIGHT = BELL_ROW_TOP + 64 + PANEL_PAD;

// The Like thumb icon is rendered at this EXACT box (absolute-positioned in
// the component, no flex guessing) — LIKE_TARGET is derived from it, so the
// pointer lands centered inside the thumb, never on the text.
export const LIKE_ICON_BOX = {left: 120, top: LIKE_ROW_TOP, size: 96} as const;
export const LIKE_TEXT_LEFT = LIKE_ICON_BOX.left + LIKE_ICON_BOX.size + 24;

export const LIKE_TARGET = {
  x: LIKE_ICON_BOX.left + LIKE_ICON_BOX.size / 2,
  y: LIKE_ICON_BOX.top + LIKE_ICON_BOX.size / 2,
} as const;
export const SUB_TARGET = {x: PANEL_WIDTH / 2, y: SUBSCRIBE_TOP + SUBSCRIBE_HEIGHT / 2} as const;

// Phase timeline (seconds within the cue). Paced for a 45-75 viewer
// (2026-07-12 operator feedback: the old 3s cue pressed Like 0.15s after the
// panel settled and reached the bell at 2.1s — it read as rushed): the panel
// settles, THEN the pointer approaches; every press gets read time; the final
// bell/channel state holds a full second.
export const CUE_TIMELINE_SEC = {
  enterStart: 0.0,
  enterEnd: 0.7,
  likePress: 1.2,
  subscribePress: 2.4,
  subscribed: 3.0,
} as const;

// Total cue length. MUST equal build.py's _ENGAGEMENT_CUE_SEC (the reserved
// audio tail) or the choreography gets cut mid-press; a test pins the pair.
export const CUE_TOTAL_SEC = 4.0;

export const pressFrames = (fps: number) => ({
  like: Math.round(CUE_TIMELINE_SEC.likePress * fps),
  subscribe: Math.round(CUE_TIMELINE_SEC.subscribePress * fps),
  subscribed: Math.round(CUE_TIMELINE_SEC.subscribed * fps),
});

// Sound effects fire on the SAME shared frame constants as the visual presses:
// pop on the Like press, bell ding on the Subscribe press (user contract).
export const SFX_FILES = {
  likePop: 'sfx/like_pop.wav',
  bellDing: 'sfx/bell_ding.wav',
} as const;

export const sfxFrames = (fps: number) => {
  const f = pressFrames(fps);
  return {likePop: f.like, bellDing: f.subscribe};
};

/** Horizontally centered panel-left, clamped to the safe area. At 1080 wide:
 * (1080-600)/2 = 240, so panelLeft + PANEL_WIDTH/2 == 540 with equal margins. */
export const panelLeftFor = (videoWidth: number): number =>
  Math.max(SAFE_LEFT, Math.min((videoWidth - PANEL_WIDTH) / 2, SAFE_RIGHT - PANEL_WIDTH));

export const panelTopFor = (): number => Math.min(SAFE_TOP, SAFE_BOTTOM - PANEL_HEIGHT);

// Pointer path keyframes (seconds -> panel-relative x/y). The pointer must SIT
// on LIKE_TARGET at the like press, on SUB_TARGET at the subscribe press, and
// be fully OUTSIDE the panel (and invisible) by the subscribed frame so it can
// never cover the bell/channel-name hold state.
const POINTER_PATH: ReadonlyArray<{t: number; x: number; y: number}> = [
  {t: 0.55, x: PANEL_WIDTH / 2, y: PANEL_HEIGHT + 120},
  {t: 1.05, x: LIKE_TARGET.x, y: LIKE_TARGET.y},
  {t: 2.0, x: LIKE_TARGET.x, y: LIKE_TARGET.y},
  {t: 2.3, x: SUB_TARGET.x, y: SUB_TARGET.y},
  {t: 2.75, x: SUB_TARGET.x, y: SUB_TARGET.y},
  {t: 2.95, x: PANEL_WIDTH + 140, y: PANEL_HEIGHT + 160},
];

// Pointer opacity keyframes (seconds -> alpha): fully invisible before the
// subscribed frame (3.0s).
const POINTER_OPACITY: ReadonlyArray<{t: number; a: number}> = [
  {t: 0.55, a: 0},
  {t: 0.75, a: 1},
  {t: 2.75, a: 1},
  {t: 2.95, a: 0},
];

const piecewise = (
  t: number,
  path: ReadonlyArray<{t: number} & Record<string, number>>,
  dim: string,
): number => {
  if (t <= path[0].t) return path[0][dim];
  for (let i = 1; i < path.length; i++) {
    if (t <= path[i].t) {
      const a = path[i - 1];
      const b = path[i];
      const f = (t - a.t) / (b.t - a.t);
      return a[dim] + f * (b[dim] - a[dim]);
    }
  }
  return path[path.length - 1][dim];
};

/** Panel-relative pointer center at a cue-local frame. */
export const pointerPositionAt = (frame: number, fps: number) => {
  const t = frame / fps;
  return {x: piecewise(t, POINTER_PATH, 'x'), y: piecewise(t, POINTER_PATH, 'y')};
};

/** Pointer opacity at a cue-local frame (0 at/after the subscribed frame). */
export const pointerOpacityAt = (frame: number, fps: number): number =>
  piecewise(frame / fps, POINTER_OPACITY, 'a');

/** Visible interaction state at a cue-local frame. */
export const cueStateAt = (frame: number, fps: number) => {
  const f = pressFrames(fps);
  return {
    liked: frame >= f.like,
    subscribePressed: frame >= f.subscribe,
    subscribed: frame >= f.subscribed,
  };
};

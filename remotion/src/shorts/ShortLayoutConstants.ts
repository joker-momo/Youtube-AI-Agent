/**
 * Universal Short Video template constants (spec §2).
 *
 * Pixel-accurate vertical layout for the YouTube Shorts canvas (1080x1920).
 * Numbers come directly from the spec; do not tweak without re-reading it.
 */

export const SHORT_LAYOUT = {
  width: 1080,
  height: 1920,

  // Hard safe zones — never render readable text outside these.
  safeTop: 170,
  safeBottom: 280,
  safeX: 108,

  // Logo / watermark slot (top-right).
  logoTop: 62,
  logoRight: 62,

  // Primary text zones (y px, top-down).
  hookZone: {yMin: 360, yMax: 850, width: 864},
  bodyZone: {yMin: 860, yMax: 1250, width: 820},
  captionZone: {yMin: 1180, yMax: 1460, width: 800},
  ctaZone: {yMin: 1180, yMax: 1420, width: 800},

  // Absolute hard floor — anything readable below this risks YouTube UI.
  dangerBottomY: 1500,
} as const;

/** Three overlay presets per spec §3.2. */
export const SHORT_OVERLAYS = {
  default: {fullDarkenOpacity: 0.20, bottomGradientOpacity: 0.52, centerTextScrimOpacity: 0.28},
  dark: {fullDarkenOpacity: 0.12, bottomGradientOpacity: 0.44, centerTextScrimOpacity: 0.24},
  bright: {fullDarkenOpacity: 0.24, bottomGradientOpacity: 0.58, centerTextScrimOpacity: 0.34},
} as const;

export type OverlayKey = keyof typeof SHORT_OVERLAYS;

/** Spec §4.1 font stacks. */
export const HOOK_FONT_FAMILY = 'Montserrat, Manrope, "Segoe UI", Arial, sans-serif';
export const BODY_FONT_FAMILY = 'Montserrat, Manrope, "Segoe UI", Arial, sans-serif';
export const CAPTION_FONT_FAMILY = 'Montserrat, Manrope, "Segoe UI", Arial, sans-serif';

/** Spec §4.3 text length limits. */
export const TEXT_LIMITS = {
  hookWordsMax: 5,
  hookCharsPerLineSoft: 16,
  bodyWordsMax: 9,
  captionLinesMax: 2,
  captionCharsMax: 70,
  ctaWordsMax: 8,
} as const;

/** Animation timing (spec §6.1). */
export const TEXT_ENTRANCE = {
  durationFrames: 8, // 8 frames @ 30fps ≈ 270ms (in the 160-220ms band, slightly longer).
  translateYStartPx: 22,
  scaleStart: 0.97,
  scaleEnd: 1.0,
} as const;

/** Allowed short_* layout names (spec v6 §10). */
export const SHORT_LAYOUTS = [
  'short_hook', 'short_pain', 'short_tip', 'short_checklist',
  'short_myth', 'short_quote', 'short_cta',
] as const;

export type ShortLayoutName = (typeof SHORT_LAYOUTS)[number];

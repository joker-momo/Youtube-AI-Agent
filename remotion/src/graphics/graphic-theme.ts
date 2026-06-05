/**
 * Vida Plena 45+ Graphic Kit — shared design tokens (spec v7 §10).
 *
 * Style: clean warm wellness, Spain-first, 45+ readable, large text, soft
 * motion. No blobs/orbs/neon. Muted olive + warm orange on cream.
 */
export const graphicTheme = {
  colors: {
    cream: '#F6EFE4',
    paper: '#FFF9EF',
    text: '#2F2A24',
    mutedText: '#6C6257',
    olive: '#7C8A4A',
    oliveDark: '#596735',
    warmOrange: '#D99A4E',
    softTerracotta: '#C9795B',
    // Plate-segment accents.
    protein: '#D99A4E',
    vegetables: '#7C8A4A',
    carbs: '#C9A36A',
    line: 'rgba(47,42,36,0.18)',
    shadow: 'rgba(47,42,36,0.18)',
    overlay: 'rgba(246,239,228,0.82)',
  },
  font: {
    family: 'Inter, Arial, sans-serif',
  },
  fontSize: {
    title: 72,
    item: 50,
    small: 34,
    footer: 38,
  },
  radius: {
    panel: 28,
    pill: 999,
  },
  // 1080x1920 Shorts safe area. Keep essential text out of the bottom
  // 240-280px (subtitles + platform UI).
  spacing: {
    safeX: 80,
    safeTop: 170,
    safeBottom: 260,
  },
} as const;

export type GraphicTheme = typeof graphicTheme;

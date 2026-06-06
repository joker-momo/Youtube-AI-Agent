/**
 * Vida Plena 45+ Graphic Kit shared visual system.
 *
 * Keep the legacy token paths alive while adding semantic variants, because
 * the current graphics already use graphicTheme.colors.* extensively.
 */
export type GraphicVariant =
  | 'brand_default'
  | 'warm_olive'
  | 'soft_clay'
  | 'cream_focus'
  | 'evening_calm';

export type GraphicVisualTone = 'calm' | 'focus' | 'warning_soft' | 'positive' | 'evening';

export type GraphicBackgroundMode = 'clean' | 'radial' | 'paper' | 'video_blur';

export type GraphicSurfaceStyle = 'none' | 'soft_card' | 'editorial' | 'plate_focus';

export type GraphicColorRoles = {
  background: string;
  surface: string;
  surfaceAlt: string;
  surfaceElevated: string;

  textPrimary: string;
  textMuted: string;
  textInverse: string;
  titleStrong: string;

  accent: string;
  accentSoft: string;

  positive: string;
  positiveSoft: string;

  caution: string;
  cautionSoft: string;

  info: string;
  infoSoft: string;

  line: string;
  lineStrong: string;

  shadow: string;
  shadowSoft: string;
  overlay: string;

  paper: string;
  cream: string;
  text: string;
  mutedText: string;
  olive: string;
  oliveDark: string;
  warmOrange: string;
  protein: string;
  vegetables: string;
  carbs: string;
  softTerracotta: string;
};

type SemanticGraphicColorRoles = Omit<
  GraphicColorRoles,
  | 'paper'
  | 'cream'
  | 'text'
  | 'mutedText'
  | 'olive'
  | 'oliveDark'
  | 'warmOrange'
  | 'protein'
  | 'vegetables'
  | 'carbs'
  | 'softTerracotta'
>;

function withLegacyAliases(roles: SemanticGraphicColorRoles): GraphicColorRoles {
  return {
    ...roles,
    paper: roles.surface,
    cream: roles.background,
    text: roles.textPrimary,
    mutedText: roles.textMuted,
    olive: roles.positive,
    oliveDark: roles.titleStrong,
    warmOrange: roles.accent,
    protein: roles.caution,
    vegetables: roles.positive,
    carbs: roles.accentSoft,
    softTerracotta: roles.cautionSoft,
    overlay: roles.overlay,
  };
}

export const graphicVariants: Record<GraphicVariant, GraphicColorRoles> = {
  brand_default: withLegacyAliases({
    background: '#F6EFE4',
    surface: '#FFF8EE',
    surfaceAlt: '#EFE5D4',
    surfaceElevated: 'rgba(255, 248, 238, 0.92)',
    textPrimary: '#2F2A24',
    titleStrong: '#4F5D30',
    textMuted: '#6E6255',
    textInverse: '#FFF8EE',
    accent: '#D99A4E',
    accentSoft: '#F2DEC3',
    positive: '#7C8A4A',
    positiveSoft: '#E3E8D0',
    caution: '#D99A4E',
    cautionSoft: '#F2DEC3',
    info: '#8C7355',
    infoSoft: '#E8D9C4',
    line: 'rgba(47, 42, 36, 0.12)',
    lineStrong: 'rgba(47, 42, 36, 0.22)',
    shadow: 'rgba(47, 42, 36, 0.18)',
    shadowSoft: 'rgba(47, 42, 36, 0.10)',
    overlay: 'rgba(246, 239, 228, 0.82)',
  }),

  warm_olive: withLegacyAliases({
    background: '#F6EFE4',
    surface: '#FFF8EE',
    surfaceAlt: '#EDF0DA',
    surfaceElevated: 'rgba(255, 248, 238, 0.92)',
    textPrimary: '#2F2A24',
    titleStrong: '#4F5D30',
    textMuted: '#6E6255',
    textInverse: '#FFF8EE',
    accent: '#7C8A4A',
    accentSoft: '#E3E8D0',
    positive: '#7C8A4A',
    positiveSoft: '#E3E8D0',
    caution: '#D99A4E',
    cautionSoft: '#F2DEC3',
    info: '#8C7355',
    infoSoft: '#E8D9C4',
    line: 'rgba(47, 42, 36, 0.12)',
    lineStrong: 'rgba(47, 42, 36, 0.22)',
    shadow: 'rgba(47, 42, 36, 0.18)',
    shadowSoft: 'rgba(47, 42, 36, 0.10)',
    overlay: 'rgba(246, 239, 228, 0.82)',
  }),

  soft_clay: withLegacyAliases({
    background: '#F6EFE4',
    surface: '#FFF5E8',
    surfaceAlt: '#F0DCC2',
    surfaceElevated: 'rgba(255, 245, 232, 0.92)',
    textPrimary: '#302821',
    titleStrong: '#6F4B2A',
    textMuted: '#6F5F50',
    textInverse: '#FFF8EE',
    accent: '#D99A4E',
    accentSoft: '#F2DEC3',
    positive: '#7C8A4A',
    positiveSoft: '#E3E8D0',
    caution: '#C9823F',
    cautionSoft: '#F0D4B4',
    info: '#8C7355',
    infoSoft: '#E8D9C4',
    line: 'rgba(48, 40, 33, 0.12)',
    lineStrong: 'rgba(48, 40, 33, 0.22)',
    shadow: 'rgba(48, 40, 33, 0.18)',
    shadowSoft: 'rgba(48, 40, 33, 0.10)',
    overlay: 'rgba(246, 239, 228, 0.82)',
  }),

  cream_focus: withLegacyAliases({
    background: '#F6EFE4',
    surface: '#FFF9F0',
    surfaceAlt: '#EFE5D4',
    surfaceElevated: 'rgba(255, 249, 240, 0.92)',
    textPrimary: '#2F2A24',
    titleStrong: '#4F5D30',
    textMuted: '#6E6255',
    textInverse: '#FFF8EE',
    accent: '#D99A4E',
    accentSoft: '#F2DEC3',
    positive: '#7C8A4A',
    positiveSoft: '#E3E8D0',
    caution: '#D99A4E',
    cautionSoft: '#F2DEC3',
    info: '#8C7355',
    infoSoft: '#E8D9C4',
    line: 'rgba(47, 42, 36, 0.12)',
    lineStrong: 'rgba(47, 42, 36, 0.22)',
    shadow: 'rgba(47, 42, 36, 0.18)',
    shadowSoft: 'rgba(47, 42, 36, 0.10)',
    overlay: 'rgba(246, 239, 228, 0.82)',
  }),

  evening_calm: withLegacyAliases({
    background: '#F0E8DC',
    surface: '#FFF6EA',
    surfaceAlt: '#E4DACB',
    surfaceElevated: 'rgba(255, 246, 234, 0.92)',
    textPrimary: '#2E2A25',
    titleStrong: '#4F5B35',
    textMuted: '#6A6258',
    textInverse: '#FFF8EE',
    accent: '#6F7D47',
    accentSoft: '#DEE5CA',
    positive: '#6F7D47',
    positiveSoft: '#DEE5CA',
    caution: '#C98F4A',
    cautionSoft: '#EFD7B8',
    info: '#7D6B58',
    infoSoft: '#E3D6C6',
    line: 'rgba(46, 42, 37, 0.12)',
    lineStrong: 'rgba(46, 42, 37, 0.22)',
    shadow: 'rgba(46, 42, 37, 0.18)',
    shadowSoft: 'rgba(46, 42, 37, 0.10)',
    overlay: 'rgba(240, 232, 220, 0.84)',
  }),
};

export const toneToVariant: Record<GraphicVisualTone, GraphicVariant> = {
  calm: 'brand_default',
  focus: 'cream_focus',
  warning_soft: 'soft_clay',
  positive: 'warm_olive',
  evening: 'evening_calm',
};

export function hashString(input: string): number {
  let hash = 0;
  for (let i = 0; i < input.length; i++) {
    hash = (hash * 31 + input.charCodeAt(i)) >>> 0;
  }
  return hash;
}

export function pickGraphicVariant(args: {
  layout?: string;
  sceneId?: string;
  explicitVariant?: GraphicVariant;
  visualTone?: GraphicVisualTone;
  layoutDefault?: GraphicVariant;
  allowHashFallback?: boolean;
}): GraphicVariant {
  if (args.explicitVariant) return args.explicitVariant;
  if (args.visualTone) return toneToVariant[args.visualTone];
  if (args.layoutDefault) return args.layoutDefault;

  if (args.allowHashFallback) {
    const variants: GraphicVariant[] = [
      'brand_default',
      'warm_olive',
      'soft_clay',
      'cream_focus',
      'evening_calm',
    ];
    const key = `${args.sceneId || ''}:${args.layout || ''}`;
    return variants[hashString(key) % variants.length] ?? 'brand_default';
  }

  return 'brand_default';
}

export const defaultVariantByLayout: Record<string, GraphicVariant> = {
  graphic_plate_ratio: 'warm_olive',
  graphic_checklist: 'evening_calm',
  graphic_step_list: 'cream_focus',
  graphic_label_callout: 'cream_focus',
  graphic_comparison: 'soft_clay',
  graphic_routine_split: 'evening_calm',
};

export const defaultSurfaceByLayout: Record<string, GraphicSurfaceStyle> = {
  graphic_plate_ratio: 'plate_focus',
  graphic_checklist: 'soft_card',
  graphic_step_list: 'editorial',
  graphic_label_callout: 'soft_card',
  graphic_comparison: 'soft_card',
  graphic_routine_split: 'editorial',
};

export const defaultBackgroundByLayout: Record<string, GraphicBackgroundMode> = {
  graphic_plate_ratio: 'radial',
  graphic_checklist: 'paper',
  graphic_step_list: 'radial',
  graphic_label_callout: 'paper',
  graphic_comparison: 'radial',
  graphic_routine_split: 'paper',
};

export const colors = graphicVariants.brand_default;

export const font = {
  family: 'Inter, Arial, sans-serif',
};

export const fontSize = {
  title: 72,
  item: 50,
  footer: 38,
  body: 42,
  small: 34,
  number: 120,
};

export const graphicTypography = {
  title: {
    fontSize: fontSize.title,
    lineHeight: 1.04,
    fontWeight: 800,
  },
  item: {
    fontSize: fontSize.item,
    lineHeight: 1.12,
    fontWeight: 700,
  },
  footer: {
    fontSize: fontSize.footer,
    lineHeight: 1.16,
    fontWeight: 600,
  },
  body: {
    fontSize: fontSize.body,
    lineHeight: 1.15,
    fontWeight: 650,
  },
  small: {
    fontSize: fontSize.small,
    lineHeight: 1.18,
    fontWeight: 600,
  },
  number: {
    fontSize: fontSize.number,
    lineHeight: 0.9,
    fontWeight: 850,
  },
};

export const graphicLayout = {
  radius: {
    sm: 16,
    md: 24,
    lg: 32,
    panel: 28,
    pill: 999,
  },
  shadow: {
    soft: '0 14px 36px rgba(47, 42, 36, 0.10)',
    card: '0 24px 70px rgba(47, 42, 36, 0.14)',
    plate: '0 28px 80px rgba(47, 42, 36, 0.16)',
  },
};

export const radius = {
  panel: graphicLayout.radius.panel,
  pill: graphicLayout.radius.pill,
};

export const spacing = {
  safeX: 80,
  safeTop: 170,
  safeBottom: 260,
  xs: 8,
  sm: 14,
  md: 24,
  lg: 36,
  xl: 52,
};

export const graphicMotion = {
  spring: {
    damping: 200,
    mass: 0.7,
  },
  revealFrames: {
    fast: 10,
    normal: 14,
    slow: 20,
  },
};

export function getGraphicColors(variant?: GraphicVariant): GraphicColorRoles {
  return graphicVariants[variant ?? 'brand_default'] ?? graphicVariants.brand_default;
}

export const graphicTheme = {
  colors,
  font,
  fontSize,
  radius,
  spacing,
  typography: graphicTypography,
  layout: graphicLayout,
  motion: graphicMotion,
} as const;

export type GraphicTheme = typeof graphicTheme;

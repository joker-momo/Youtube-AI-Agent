/**
 * Graphic payload types + runtime validation (spec v7 §8).
 *
 * Payloads are produced by the Python/LLM scene planner, so they are validated
 * at render time with Zod. Unsupported layouts throw clearly — never silently
 * fall back to stock rendering.
 */
import {z} from 'zod';
import type {
  GraphicBackgroundMode,
  GraphicSurfaceStyle,
  GraphicVariant,
  GraphicVisualTone,
} from './graphic-theme';

export const PLATE_RATIO_TOTAL = 100;
export const PLATE_RATIO_EPSILON = 0.01;
export const MAX_GRAPHIC_ITEMS = 5;

// --- TypeScript payload types ---------------------------------------------

export type BaseGraphicStyleFields = {
  variant?: GraphicVariant;
  visual_tone?: GraphicVisualTone;
  background_mode?: GraphicBackgroundMode;
  surface_style?: GraphicSurfaceStyle;
};

export type GraphicChecklistPayload = {
  title: string;
  items: string[];
  footer?: string;
} & BaseGraphicStyleFields;

export type GraphicStepListPayload = {
  title: string;
  steps: Array<{
    label: string;
    text: string;
  }>;
  footer?: string;
} & BaseGraphicStyleFields;

export type GraphicPlateSegment = {
  label: string;
  value: number;
};

export type GraphicPlateRatioPayload = {
  title: string;
  segments: GraphicPlateSegment[];
  footer?: string;
} & BaseGraphicStyleFields;

export type GraphicLabelCalloutPayload = {
  title: string;
  productLabel?: string;
  callouts: Array<{
    label: string;
    value: string;
    note?: string;
  }>;
  footer?: string;
} & BaseGraphicStyleFields;

export type GraphicComparisonPayload = {
  title: string;
  left: {
    heading: string;
    text: string;
    badge?: string;
  };
  right: {
    heading: string;
    text: string;
    badge?: string;
  };
  footer?: string;
} & BaseGraphicStyleFields;

export type GraphicRoutineSplitPayload = {
  title: string;
  totalLabel?: string;
  blocks: Array<{
    time: string;
    text: string;
    icon?: string;
  }>;
  footer?: string;
} & BaseGraphicStyleFields;

/** Union of every supported graphic payload. */
export type GraphicAnyPayload =
  | GraphicChecklistPayload
  | GraphicStepListPayload
  | GraphicPlateRatioPayload
  | GraphicLabelCalloutPayload
  | GraphicComparisonPayload
  | GraphicRoutineSplitPayload;

// --- Zod schemas -----------------------------------------------------------

const GraphicVariantSchema = z.enum([
  'brand_default',
  'warm_olive',
  'soft_clay',
  'cream_focus',
  'evening_calm',
]);

const GraphicVisualToneSchema = z.enum(['calm', 'focus', 'warning_soft', 'positive', 'evening']);

const GraphicBackgroundModeSchema = z.enum(['clean', 'radial', 'paper', 'video_blur']);

const GraphicSurfaceStyleSchema = z.enum(['none', 'soft_card', 'editorial', 'plate_focus']);

const BaseGraphicStyleFieldsSchema = {
  variant: GraphicVariantSchema.optional(),
  visual_tone: GraphicVisualToneSchema.optional(),
  background_mode: GraphicBackgroundModeSchema.optional(),
  surface_style: GraphicSurfaceStyleSchema.optional(),
};

export const GraphicChecklistPayloadSchema = z.object({
  ...BaseGraphicStyleFieldsSchema,
  title: z.string().min(1).max(48),
  items: z.array(z.string().min(1).max(48)).min(2).max(MAX_GRAPHIC_ITEMS),
  footer: z.string().max(72).optional(),
});

export const GraphicStepListPayloadSchema = z.object({
  ...BaseGraphicStyleFieldsSchema,
  title: z.string().min(1).max(48),
  steps: z
    .array(
      z.object({
        label: z.string().min(1).max(12),
        text: z.string().min(1).max(56),
      }),
    )
    .min(2)
    .max(4),
  footer: z.string().max(72).optional(),
});

export const GraphicPlateRatioPayloadSchema = z
  .object({
    ...BaseGraphicStyleFieldsSchema,
    title: z.string().min(1).max(48),
    segments: z
      .array(
        z.object({
          label: z.string().min(1).max(48),
          value: z.number().positive().max(100),
        }),
      )
      .min(2)
      .max(4),
    footer: z.string().max(72).optional(),
  })
  .superRefine((payload, ctx) => {
    const total = payload.segments.reduce((sum, segment) => sum + segment.value, 0);
    if (Math.abs(total - PLATE_RATIO_TOTAL) > PLATE_RATIO_EPSILON) {
      ctx.addIssue({
        code: 'custom',
        message: `Plate ratio segments must sum to ${PLATE_RATIO_TOTAL}, got ${total}`,
        path: ['segments'],
      });
    }
  });

export const GraphicLabelCalloutPayloadSchema = z.object({
  ...BaseGraphicStyleFieldsSchema,
  title: z.string().min(1).max(60),
  productLabel: z.string().max(36).optional(),
  callouts: z
    .array(
      z.object({
        label: z.string().min(1).max(22),
        value: z.string().min(1).max(26),
        note: z.string().max(48).optional(),
      }),
    )
    .min(2)
    .max(4),
  footer: z.string().max(90).optional(),
});

const comparisonText = (max: number, min = 0) =>
  z.string().min(min).max(max).superRefine((value, ctx) => {
    const forbidden = ['veneno', 'prohibido', 'nunca', 'milagro', 'cura', 'doctores no quieren'];
    const lower = value.toLowerCase();
    for (const word of forbidden) {
      if (lower.includes(word)) {
        ctx.addIssue({
          code: 'custom',
          message: `Comparison text contains forbidden health-marketing word: ${word}`,
        });
      }
    }
  });

const GraphicComparisonSideSchema = z.object({
  heading: comparisonText(24, 1),
  text: comparisonText(68, 1),
  badge: comparisonText(28).optional(),
});

export const GraphicComparisonPayloadSchema = z.object({
  ...BaseGraphicStyleFieldsSchema,
  title: comparisonText(60, 1),
  left: GraphicComparisonSideSchema,
  right: GraphicComparisonSideSchema,
  footer: comparisonText(90).optional(),
});

export const GraphicRoutineSplitPayloadSchema = z.object({
  ...BaseGraphicStyleFieldsSchema,
  title: z.string().min(1).max(60),
  totalLabel: z.string().max(16).optional(),
  blocks: z
    .array(
      z.object({
        time: z.string().min(1).max(16),
        text: z.string().min(1).max(52),
        icon: z.string().max(24).optional(),
      }),
    )
    .min(2)
    .max(4),
  footer: z.string().max(90).optional(),
});

// --- Parser ----------------------------------------------------------------

export const SUPPORTED_GRAPHIC_LAYOUTS = [
  'graphic_checklist',
  'graphic_step_list',
  'graphic_plate_ratio',
  'graphic_label_callout',
  'graphic_comparison',
  'graphic_routine_split',
] as const;

/**
 * Validate a raw payload against the schema for ``layout``. Throws (Zod or a
 * plain Error) for unsupported layouts or malformed payloads. This clear
 * failure is intentional — bad graphic scenes must not reach the screen.
 */
export function parseGraphicPayload(layout: string, payload: unknown): GraphicAnyPayload {
  switch (layout) {
    case 'graphic_checklist':
      return GraphicChecklistPayloadSchema.parse(payload);
    case 'graphic_step_list':
      return GraphicStepListPayloadSchema.parse(payload);
    case 'graphic_plate_ratio':
      return GraphicPlateRatioPayloadSchema.parse(payload);
    case 'graphic_label_callout':
      return GraphicLabelCalloutPayloadSchema.parse(payload);
    case 'graphic_comparison':
      return GraphicComparisonPayloadSchema.parse(payload);
    case 'graphic_routine_split':
      return GraphicRoutineSplitPayloadSchema.parse(payload);
    default:
      throw new Error(
        `Unsupported graphic layout: ${layout}. Supported layouts: ${SUPPORTED_GRAPHIC_LAYOUTS.join(', ')}.`,
      );
  }
}

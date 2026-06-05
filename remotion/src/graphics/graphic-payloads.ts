/**
 * Graphic payload types + runtime validation (spec v7 §8).
 *
 * Payloads are produced by the Python/LLM scene planner, so they are validated
 * at render time with Zod. Unsupported layouts throw clearly — never silently
 * fall back to stock rendering.
 */
import {z} from 'zod';

export const PLATE_RATIO_TOTAL = 100;
export const PLATE_RATIO_EPSILON = 0.01;
export const MAX_GRAPHIC_ITEMS = 5;

// --- TypeScript payload types ---------------------------------------------

export type GraphicChecklistPayload = {
  title: string;
  items: string[];
  footer?: string;
};

export type GraphicStepListPayload = {
  title: string;
  steps: Array<{
    label: string;
    text: string;
  }>;
  footer?: string;
};

export type GraphicPlateSegment = {
  label: string;
  value: number;
};

export type GraphicPlateRatioPayload = {
  title: string;
  segments: GraphicPlateSegment[];
  footer?: string;
};

/** Union of every supported graphic payload. */
export type GraphicAnyPayload =
  | GraphicChecklistPayload
  | GraphicStepListPayload
  | GraphicPlateRatioPayload;

// --- Zod schemas -----------------------------------------------------------

export const GraphicChecklistPayloadSchema = z.object({
  title: z.string().min(1).max(48),
  items: z.array(z.string().min(1).max(48)).min(2).max(MAX_GRAPHIC_ITEMS),
  footer: z.string().max(72).optional(),
});

export const GraphicStepListPayloadSchema = z.object({
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

// --- Parser ----------------------------------------------------------------

export const SUPPORTED_GRAPHIC_LAYOUTS = [
  'graphic_checklist',
  'graphic_step_list',
  'graphic_plate_ratio',
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
    default:
      throw new Error(
        `Unsupported graphic layout: ${layout}. Supported MVP layouts: ${SUPPORTED_GRAPHIC_LAYOUTS.join(', ')}.`,
      );
  }
}

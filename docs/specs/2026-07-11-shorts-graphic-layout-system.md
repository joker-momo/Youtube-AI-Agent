# Shorts Graphic Layout System — Content-First, Mobile-Readable

Status: implementation specification  
Date: 2026-07-11  
Owner: Shorts pipeline  
Decision: adopt the content-first graphic system described below

## Problem

Shorts graphic images currently combine information semantics and art direction
inside one growing `graphic_*` layout menu. The planner can choose 13 layouts,
including several overlapping shapes. Every generated graphic also receives a
strict channel palette and soft wellness-card treatment. This produces three
quality risks:

1. repeated cream/green cards flatten scene-specific ideas;
2. overlapping layout names increase planner mistakes and validator churn;
3. poster-like lists can exceed what a viewer can understand during a 2.5–5.0
   second graphic scene on a phone.

The reference Shorts show useful visual devices — large food photography,
strong numbered bands and binary contrast — but their static poster density must
not be copied into narrated moving scenes.

## Goal

Increase first-frame attention and mobile comprehension while preserving the
existing quality invariants: Spain-first wellness content for adults 45+, no
alarmist health claims, no more than two graphic scenes in a normal Short, and
no loss of narrated or source-mapped ideas.

## Core model: semantics and surface are separate

`layout` answers **what information relationship is being taught**.

`layout_payload.surface_style` answers **how that relationship is presented**.

`numbered_photo_bands` is therefore a surface style, not a new semantic
`graphic_*` layout. This keeps the planner decision surface small and lets the
same list semantics use an appropriate visual treatment without duplicating
validators.

## Planner-facing semantic layouts

The scene planner may emit exactly these 10 layouts:

1. `graphic_stat` — one memorable number or evidence fact plus short context.
2. `graphic_do_dont` — one clearly worse and one clearly better choice.
3. `graphic_comparison` — two neutral choices with equal visual weight.
4. `graphic_myth` — misconception and correction.
5. `graphic_checklist` — 2–4 short actions under one instruction.
6. `graphic_warning` — 2–4 calm caution items.
7. `graphic_step_list` — 2–4 ordered steps; a step may carry an optional `time`.
8. `graphic_label_callout` — 2–3 callouts on a real label/product.
9. `graphic_plate_ratio` — 2–3 labelled portions whose values sum to 100.
10. `graphic_recipe_snapshot` — 2–3 labelled real-food tiles.

Planner migrations:

- `graphic_evidence_nugget` becomes `graphic_stat`.
- `graphic_routine_split` becomes `graphic_step_list` with optional `time` on
  each step.
- `graphic_quote_portrait` is not planner-selectable. It remains an internal
  compatibility/repair layout for a one-item degenerate list.

Existing stored scenes using the three legacy layouts must remain valid. The
change is a planner contract migration, not destructive removal of historical
artifact support.

## Surface styles

New planner-preferred values:

- `hero_stat`
- `binary_split`
- `numbered_photo_bands`
- `annotated_object`
- `photo_tiles`

Existing surface-style values remain accepted for stored-scene compatibility.

`numbered_photo_bands` is allowed only for `graphic_checklist`,
`graphic_warning`, and `graphic_step_list`. It requires 2–4 horizontal,
edge-to-edge photo bands. Each band has one large circular numbered badge and a
label of at most four words. Do not add paragraphs, footnotes, invented claims,
or a separate card around every band.

Recommended defaults:

| Semantic layout | Preferred surface |
| --- | --- |
| `graphic_stat` | `hero_stat` |
| `graphic_do_dont`, `graphic_comparison`, `graphic_myth` | `binary_split` |
| `graphic_checklist`, `graphic_warning`, `graphic_step_list` | `numbered_photo_bands` when the subject has concrete photos |
| `graphic_label_callout`, `graphic_plate_ratio` | `annotated_object` |
| `graphic_recipe_snapshot` | `photo_tiles` |

## Art direction

Use **content-first, brand-as-accent**:

- The scene subject, action and teaching idea choose the background, lighting,
  materials, camera angle and dominant colours.
- Real, appetizing, consistently lit subject photography should dominate when
  the content is concrete.
- Channel palette colours are accents only: numbered badge, marker, underline
  or small channel pill. They must not lock the whole frame or every panel.
- Keep the existing typography, Spanish spelling, anatomy, contrast and mobile
  safe-margin guards.
- Never force a recurring beige/cream/green wellness card, repeated soft panel,
  generic icon grid or stock-photo collage.
- Two graphic scenes in the same normal Short should use different semantic or
  surface families so the visual rhythm does not repeat.

## Density and duration contract

| Layout family | Density | Target duration |
| --- | --- | --- |
| Stat/evidence | one fact + label up to 6 words | 2.5–3.5 s, hard max 4.0 s |
| Binary | exactly 2 cells, up to 5 words per cell | 3.0–4.5 s, hard max 5.0 s |
| Checklist/warning/steps | 2–4 entries, up to 4 words per entry | 3.0–4.5 s; up to 5.0 s for photo bands |
| Label callout | 2–3 callouts | 3.5–5.0 s |
| Plate ratio | 2–3 segments | 3.0–4.5 s |
| Recipe snapshot | 2–3 tiles | 3.0–4.5 s |

A five-item checklist is invalid. The pipeline must request a simpler/split
scene rather than silently dropping the fifth idea. Existing idea-preservation
rules remain authoritative.

## Acceptance criteria

- AC1: the scene-generation prompt exposes exactly the 10 planner-facing
  semantic layouts above.
- AC2: the prompt instructs the three planner migrations and does not offer the
  legacy layouts as choices.
- AC3: validators still accept stored legacy `graphic_evidence_nugget`,
  `graphic_routine_split`, and repair-generated `graphic_quote_portrait` scenes.
- AC4: `graphic_step_list.steps[*].time` is optional and, when present, must be
  a short non-empty string.
- AC5: checklist density is hard-limited to 2–4 items without silent truncation.
- AC6: the five new surface styles validate; `numbered_photo_bands` is rejected
  on incompatible semantic layouts.
- AC7: the generated ChatGPT prompt describes content-first art direction,
  brand colours as accents only, and the numbered-band visual contract.
- AC8: the prompt does not contain `Use ONLY this brand palette`, mandatory soft
  panel/card language, or mandatory wellness-magazine template language.
- AC9: current maximum graphic-count and duration gates remain unchanged.
- AC10: targeted tests, the broader Shorts build suite and static/type checks
  for touched surfaces pass.

## Non-goals

- Do not change render concurrency.
- Do not change thumbnail prompts.
- Do not change the standalone static infographic-poster pipeline.
- Do not add a new renderer-native graphic layout; these remain ChatGPT image
  generation contracts.
- Do not weaken health-claim, age-fit, safe-area, spelling or anatomy guards.


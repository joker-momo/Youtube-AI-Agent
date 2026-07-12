# Spec: Long-form Thumbnail Copy for Vida Plena 45+

## Status

Acceptance source for the Claude implementation and Codex verification loop.

## Objective

Improve long-form thumbnail click appeal by replacing vague curiosity fragments
with short, self-contained Spanish micro-promises that a Spain-first viewer aged
45–75 can understand in about one second.

The viewer should immediately understand:

1. what familiar problem or object the video concerns;
2. what practical benefit, action, or decision the video offers; and
3. that the message respects their autonomy rather than portraying them as
   frail, frightened, or medically helpless.

Success is not maximal sensationalism. Success is clear relevance, practical
hope, trustworthy curiosity, and mobile readability.

## Audience Contract

Primary audience:

- Spanish-speaking adults aged 45–75, Spain-first language;
- concerned about sleep, energy, food, muscle, joints, memory, and maintaining
  everyday independence;
- wants simple actions and realistic improvement;
- dislikes fake authority, miracle claims, medical fear, age stereotypes, and
  context-free clickbait.

The psychological promise is:

> I understand the change you are noticing; here is a practical action or
> explanation that helps you stay in control.

## Scope

In scope:

- the long-form SEO prompt that generates `title_variants[].thumbnail_text`;
- deterministic scoring/ranking of those variants;
- normalization/fallback only as needed to preserve valid output;
- tests using real channel topic families.

Out of scope:

- thumbnail image composition, presenter identity, colors, typography, or
  image-generation provider;
- long-form title/chapter logic except title-to-thumbnail semantic alignment;
- Shorts SEO, Shorts covers, CTA, music, render configuration, and thumbnails
  already published on YouTube;
- changing `render.concurrency` from `auto`.

## Copy Contract

### C1 — Standalone micro-promise

`thumbnail_text` must make useful sense without the YouTube title. It must not
require the viewer to infer the missing object after reading words such as
`ESTO`, `DESPUÉS`, `LA HORA`, `CLAVE`, or `SECRETO`.

### C2 — Minimum semantic payload

Each candidate must communicate at least two of these signal classes:

- concrete object/topic: `CAFÉ`, `PARTIDO`, `ACEITE DE OLIVA`, `ALIMENTOS`;
- familiar pain/problem: `SUEÑO`, `CANSANCIO`, `DOLOR`, `PÉRDIDA MUSCULAR`;
- practical outcome/benefit: `DORMIR MEJOR`, `CUIDAR TUS MÚSCULOS`;
- actionable decision: `CUÁNDO TOMARLO`, `QUÉ ELEGIR`, `EVITA`;
- honest specificity: number, timing, or relevant age frame.

Age or a number may strengthen a candidate, but neither is mandatory when the
topic and value are already explicit.

### C3 — Mobile-readable length

- Target 4–7 spoken/display words.
- Three words are allowed only when they already carry concrete topic and value.
- Never reward a vague phrase merely because it is shorter.
- Preserve Spanish accents and natural Spain wording.

### C4 — Complementary but complete

The thumbnail must not copy the full title or merely paraphrase it. It should
select the title's strongest pain/action/outcome angle, while remaining
self-contained.

### C5 — Three meaningfully different variants

Generate:

1. pain-led clarity;
2. outcome-led practical hope;
3. action/decision-led specificity.

They must not be three minor rewrites of the same hook. Do not force an
imperative+age template when it makes the copy generic or unnatural.

### C6 — Honest, dignified persuasion

Never use:

- fake credentials or authority;
- cure, miracle, guaranteed, death, or catastrophe claims;
- degrading age labels (`ancianos`, `tercera edad`, helpless/frail framing);
- unsupported certainty such as `ARRUINA TU SALUD` when the content only says
  something may influence sleep or wellbeing.

Prefer practical agency: `CÓMO`, `CUÁNDO`, `QUÉ ELEGIR`, `PARA CUIDAR`,
`PUEDE AFECTAR`.

## Required Real-topic Outcomes

The scorer must rank the right-hand candidate materially above the vague
left-hand candidate for the same title:

| Topic | Reject/deprioritize | Prefer |
| --- | --- | --- |
| Mundial and sleep | `5 GESTOS CLAVE` | `DUERME MEJOR TRAS EL PARTIDO` |
| Coffee and sleep | `¿DUERMES PEOR DESPUÉS?` | `¿TU CAFÉ EMPEORA EL SUEÑO?` |
| Food after 60 | `TU SEMANA TIENE HUECOS` | `5 ALIMENTOS PARA CUIDAR TUS MÚSCULOS` |
| Olive oil | `NO ES POR LA HORA` | `ACEITE DE OLIVA: CUÁNDO TOMARLO` |

These are acceptance fixtures, not phrases the generator must copy verbatim.

## Deterministic Scoring Contract

`score_variant()` remains backward compatible and returns a score in `[0,100]`.
Its thumbnail detail must expose auditable components:

- `standalone_value_score` — reward concrete topic plus pain/outcome/action;
- `audience_fit_score` — reward practical, dignified agency appropriate to 45+;
- `vagueness_penalty` — penalize context-free/deictic hooks;
- `trust_penalty` — penalize fear, miracle, fake authority, or unsupported
  certainty.

Semantic quality must have enough weight that a vague all-caps 3–5-word phrase
cannot beat a clear 4–7-word phrase only because it is shorter.

The implementation may use normalized Spanish token/phrase sets and the paired
title for topic alignment. It must remain deterministic, offline, fast, and
must not add an LLM call.

## Acceptance Criteria

- **AC1:** SEO prompt explicitly requires a `STANDALONE MICRO-PROMISE`.
- **AC2:** SEO prompt requires at least two semantic signal classes.
- **AC3:** SEO prompt bans context-free vague fragments and includes bad/good
  examples from the real channel topics.
- **AC4:** Prompt defines pain-led, outcome-led, and action/decision-led variants.
- **AC5:** Prompt preserves honest CTR, Spain locale, dignity, and autonomy.
- **AC6:** All four real-topic score comparisons prefer the clear candidate by
  a meaningful margin.
- **AC7:** Score breakdown exposes standalone value, audience fit, vagueness,
  and trust components.
- **AC8:** Unsupported fear copy scores below a clear, proportionate alternative.
- **AC9:** Existing valid title/thumbnail scoring APIs and range remain compatible.
- **AC10:** Planner still renders the selected wording exactly; no image-layout
  behavior changes.
- **AC11:** Relevant SEO, title scorer, thumbnail planner/image-stage, operator
  prompt, and workflow suites pass without skips/xfails/test weakening.
- **AC12:** Ruff and compile checks show no new violations in touched files.

## Testing Strategy

Primary acceptance test:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_thumbnail_copy_audience_fit.py \
  tests/test_operator_prompts.py \
  tests/test_title_ctr_formula.py \
  tests/test_title_scorer.py
```

Regression sweep:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/test_seo_variants.py \
  tests/test_thumbnail_planner.py \
  tests/test_thumbnail_image_stage.py \
  tests/test_thumbnail_stage_v13.py \
  tests/test_operator_workflow.py
```

Quality gates:

```bash
.venv/bin/ruff check <touched-files>
.venv/bin/python -m compileall -q src/video_agent
```

## Boundaries

Always:

- preserve title/topic alignment and honest claims;
- keep scoring deterministic and explainable;
- test real Vida Plena topic families;
- preserve `render.concurrency: auto`.

Never:

- solve by increasing font size or changing thumbnail image composition;
- reward vague text solely for being short/all-caps;
- hardcode one final phrase per topic;
- use external/network/LLM calls in the scorer;
- touch Shorts, chapters, CTA, music, or unrelated dirty files;
- delete, loosen, skip, or xfail acceptance tests to make the suite green.

## Implementation Tasks

- [ ] Update the SEO generation prompt to the new audience/copy contract.
- [ ] Add deterministic semantic and trust scoring to `title_scorer.py`.
- [ ] Keep `score_variant()` output/range backward compatible.
- [ ] Verify sorting selects the clearer variants on all four real topics.
- [ ] Run focused and regression gates and report evidence to Codex.


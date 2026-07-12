# Spec: Parent-job uniqueness for Shorts SEO titles

## Objective

Prevent two Shorts produced from the same long-form parent job from publishing
the same or cosmetically different SEO title. The title must still be short,
honest, topical, idiomatic es-ES, and aligned with the Short's own hook and
idea contract.

Live reproduction from the Vida Plena salt job:

- idea-01: `Si tienes más de 45, revisa tu sal`
- idea-07: `Si tienes más de 45, revisa tu sal`
- idea-09: `Si tienes más de 45, revisa la sal`

idea-07 is a self-test and idea-09 is a six-error dinner warning. Treating
`tu` versus `la` as a distinct title is not acceptable audience-facing
variation.

## Scope and assumptions

- Uniqueness is scoped to one parent long-form job, not the entire channel.
- Existing `build_short_seo(...) -> dict` callers remain compatible.
- Existing title quality, topic, fidelity, length, description, hashtag, and
  engagement gates remain active.
- Both narrated and infographic Shorts use the same uniqueness policy.
- A rerender excludes its own current artifact from sibling comparisons.
- No database or new external dependency is required.
- Existing rendered videos are not re-rendered by this change. Metadata repair
  for already-published videos is a separate operator action.

## Contract

### Canonical comparison

Comparison must be accent-insensitive, case-insensitive, punctuation-insensitive,
and robust to formula boilerplate and low-information determiners/pronouns.
For example, these are duplicates:

- `Si tienes más de 45, revisa tu sal`
- `Si tienes más de 45, revisa la sal`

Formula boilerplate such as `si tienes más de 45` must not inflate similarity.
Meaningful action or object differences must remain distinct; for example,
`reduce la sal` must not be rejected merely because another title says
`revisa la sal`.

The comparator must expose a deterministic offline result suitable for tests
and audit. Exact canonical equality is always a duplicate. Near-duplicate
classification must use a documented stable threshold and return the matched
sibling title in its issue/evidence.

### Sibling-title discovery

Read completed/current sibling SEO artifacts under the same
`<parent>/shorts/<short_id>/json/short_seo.json` contract, including compatible
legacy resolution through existing path helpers where applicable. Ignore:

- another parent job;
- missing or malformed artifacts;
- empty titles;
- the current `short_id` during rerender.

Discovery order must be deterministic.

### Generation and retry

Before every SEO generation attempt:

1. Load or refresh sibling titles for the parent job.
2. Include the used-title list in the LLM prompt as titles that must not be
   repeated or paraphrased cosmetically.
3. Run the existing per-title gates.
4. Run the uniqueness gate against sibling titles.
5. If duplicate, retry with actionable feedback naming the collision.

Before persistence, re-read sibling titles and run the uniqueness gate again.
This final check closes the stale-snapshot window. Parent-level locking may be
used to serialize the final check/write, but must be bounded and must not alter
render concurrency.

If retries and deterministic fallback cannot produce a valid unique title,
fail loudly. Never publish a duplicate, append an arbitrary number, or weaken
existing title-quality gates.

### Infographic idea preservation

`build_infographic_seo` must receive the selected source idea, not only the
poster plan. It must preserve at least:

- original idea title/topic;
- original format (`warning_list`, `checklist_score`, etc.);
- viewer pain and practical payoff;
- count contract derived from `key_points` when the idea promises a fixed list;
- `idea_id` for audit.

The poster title/hook remains useful context, but must not erase the source
idea contract. For the live idea-09, SEO must know it is a six-error warning,
not a generic infographic about salt.

## Acceptance criteria

- **AC1** Canonical normalization treats the live `tu sal`/`la sal` titles as
  equivalent.
- **AC2** Similarity is deterministic and does not reject meaningful action
  differences such as `revisa` versus `reduce` by itself.
- **AC3** Sibling discovery is parent-scoped, deterministic, ignores malformed
  files, and excludes the current Short.
- **AC4** The prompt includes existing sibling titles and explicit
  no-cosmetic-paraphrase guidance.
- **AC5** A duplicate first LLM response is rejected and a unique second
  response is persisted.
- **AC6** A stubborn duplicate cannot survive retry exhaustion or fallback.
- **AC7** A final refresh/check occurs before atomic persistence, preventing a
  stale sibling snapshot from silently publishing a collision.
- **AC8** Narrated and infographic paths share the same uniqueness gate.
- **AC9** Infographic SEO receives and preserves the original idea format,
  title, pain/payoff, idea ID, and fixed-count contract.
- **AC10** The real infographic orchestrator passes its selected source idea to
  the SEO adapter.
- **AC11** Existing public function calls remain backward compatible; an empty
  sibling set behaves as before.
- **AC12** Existing title length/formula/topic/fidelity and hashtag/description
  rules remain unchanged and green.
- **AC13** No tests are removed, skipped, xfailed, or loosened; no hardcoding of
  the live three titles as a special-case implementation.
- **AC14** `render.concurrency` remains `auto`; no Shorts render, CTA, music,
  poster, long-form title, or thumbnail behavior is changed.

## Testing strategy

Acceptance tests live in:

- `tests/test_shorts_seo_title_uniqueness.py`
- `tests/shorts_build/infographic/test_infographic_seo_source_contract.py`

Required verification:

```bash
PYTHONPATH=src /Volumes/DATA/YBT-Studio/Youtube-AI-Agent/.venv/bin/python -m pytest -q \
  tests/test_shorts_seo_title_uniqueness.py \
  tests/shorts_build/infographic/test_infographic_seo_source_contract.py \
  tests/shorts_build/test_seo.py \
  tests/shorts_build/test_seo_topic_keyword.py \
  tests/test_shorts_seo_context_leak.py \
  tests/test_shorts_idea_preservation.py \
  tests/shorts_build/infographic/test_infographic_seo.py \
  tests/shorts_build/infographic/test_build.py
```

Also run Ruff on touched Python files and `compileall` on touched source.

## Boundaries

- Always: preserve title quality and idea fidelity; use atomic writes; provide
  collision evidence; keep behavior deterministic and offline-testable.
- Ask first: channel-wide uniqueness, metadata migration on published YouTube
  videos, new persistence services, or changing the four title formulas.
- Never: weaken existing tests, special-case idea-01/07/09, silently accept a
  duplicate, mutate dirty root files, or change `render.concurrency`.


# Visual Batch Audit - 2026-05-19

Scope: four end-to-end Docker renders for `vida-plena-45` using stock API visuals and the Phase 2A asset library.

## Summary

| Topic | Job | Video size | Visual QA | Source mix |
| --- | --- | ---: | --- | --- |
| Habitos nocturnos para dormir mejor despues de los 45 | `20260518-193144-vida-plena-45-habitos-nocturnos-para-dormir-mejor-despues-de-l` | 15.6 MB | PASS | 5 Pexels assets |
| Desayuno equilibrado para tener energia despues de los 45 | `20260518-193434-vida-plena-45-desayuno-equilibrado-para-tener-energia-despues-` | 22.7 MB | PASS | 5 Pexels assets |
| Caminar cada dia sin cansarse demasiado despues de los 45 | `20260518-193728-vida-plena-45-caminar-cada-dia-sin-cansarse-demasiado-despues-` | 36.2 MB | PASS | 5 Pexels assets |
| Como recordar tomar agua durante el dia despues de los 45 | `20260518-194019-vida-plena-45-como-recordar-tomar-agua-durante-el-dia-despues-` | 20.7 MB | PASS | 5 Pexels assets |

## Findings

- All four videos rendered successfully in Docker with Remotion `Concurrency 1x`.
- `visual_review.json` passed for all jobs: no placeholders, no missing provider metadata, no duplicate assets inside a job, and no low-score warnings.
- The batch used Pexels for all 20 scenes. This is acceptable for the current provider ranking, but it means Pixabay fallback/diversity was not exercised in this run.
- Selection scores ranged from 58 to 80. The QA gate did not warn because the current low-score threshold is below 40, but several scenes are still close enough to deserve visual review by a human.
- Each Docker render downloaded Chrome Headless Shell again. This keeps the container self-contained, but it adds repeated startup time.

## Job Outputs

- `jobs/20260518-193144-vida-plena-45-habitos-nocturnos-para-dormir-mejor-despues-de-l/`
- `jobs/20260518-193434-vida-plena-45-desayuno-equilibrado-para-tener-energia-despues-/`
- `jobs/20260518-193728-vida-plena-45-caminar-cada-dia-sin-cansarse-demasiado-despues-/`
- `jobs/20260518-194019-vida-plena-45-como-recordar-tomar-agua-durante-el-dia-despues-/`

## Next Fix Candidates

1. Add a lightweight batch runner so 3-5 ideas can be rendered and audited with one Docker command.
2. Add provider diversity controls or an audit metric so Pixabay fallback is tested deliberately.
3. Cache or preinstall Remotion's Chrome Headless Shell in the Docker image to remove repeated download time.
4. Add visual review screenshots/contact sheet per job so humans can inspect selected images faster than opening each video.

# Phase 7: evaluation and hardening

Status: **complete (2026-10-12).** All 14 steps of the [MVP definition of done](PRODUCT.md#definition-of-mvp-done) have evidence below. The only item not done is outside Phase 7: live Nebius Serverless Jobs runs (Phase 6), which wait on the owner's Nebius account.

Phase 7 had two halves in the original plan: the workflow evaluation ([METRICS §3](METRICS.md#3-workflow-evaluation-protocol-to-be-run-in-phase-7)) and the hardening items carried forward from [PHASE3.md](PHASE3.md).

## Evaluation

The owner dropped the formal user study on 2026-10-09. Kinesis therefore claims only measured, objective results: slip, collateral motion, time and cost. It makes no productivity claim.

| Item | Status | Where |
|---|---|---|
| Scene suite: canonical fixture, 3 variants, 1 external CC0 walk with a hand-made slide | done | [BENCHMARKS.md](BENCHMARKS.md), `uv run task bench` |
| External rig: Quaternius *Universal Animation Library* walk (65 bones, UE-style names) | done; one rig bug found and fixed (toe leaf bones were counted as collateral motion) | [`blender/fixtures/build_ual_walk.py`](../blender/fixtures/build_ual_walk.py), `uv run task ual-scene` |
| Scripted end-to-end demo exported as `docs/evaluation/demo_run.json` (METRICS §4) | done | `uv run task demo` |
| Resume-ready spec sheet generated from measured numbers only | done | [SPECS.md](SPECS.md), `uv run task specs` |
| Vision-model bake-off | done | [BENCHMARKS.md](BENCHMARKS.md#vision-model-bake-off) |
| Formal user study (≥ 3 animators) | dropped by the owner | n/a |

### Finding: the recommendation preferred a failing candidate

The first 5-scene benchmark recommended B in every model-planned scene, including scenes where B left 1.3-2.3 cm of slip and A left none. Smoothness and the visual review outweighed the residual slip. Since 2026-10-12 a candidate that leaves 1 cm or more is recommended only when no ungated candidate gets below 1 cm. In the re-run, every recommended candidate in every pass is under 1 cm.

### Finding: the model planned jerky locks

With prompt `planner/2`, Nemotron chose 2-frame blends for candidate A. The planted foot's jerk ratio was 2.4-2.8, against 1.6-1.8 for the presets and a golden limit of 2.0. An offline sweep on the fixture showed blend length is the cause and costs no slip: 2 frames gave 2.75, 3 frames 1.79, 4 frames 1.35, 6 frames 1.10, all with 0.00 cm left. Since 2026-10-12 the plan gate rejects blends shorter than 3 frames, and prompt `planner/3` asks for 4-6 frames on A and 6-10 on B. In the re-run, all 5 model plans passed the gate (A 5 frames, B 8). Model-planned A's jerk ratio fell to 0.73-1.17, still with 0.00 cm of slip in every scene.

## Hardening

| Item | Status |
|---|---|
| Retention sweeper on a schedule: finished jobs' files and old uploads are deleted after `KINESIS_RETENTION_DAYS` (7). Records, events and decisions are kept. Running and awaiting-decision jobs are never touched | done |
| Streaming uploads: the multipart body is parsed as it arrives, so an oversized upload is cut off at the cap instead of being spooled to disk first. Over real HTTP, a 50 MB chunked upload against a 5 MB cap got 413 after about 6 MB | done |
| Branch protection: PRs, five required checks, squash only | done (ruleset active 2026-10-12) |

## Definition of done: evidence

The demo row values come from `uv run task demo` on 2026-10-12, commit `0c3f658`, recorded in [`evaluation/demo_run.json`](evaluation/demo_run.json). It used the canonical fixture, host Blender 4.5.14, and Token Factory.

| # | Step | Evidence |
|---|---|---|
| 1 | Open the canonical fixture | `blender/fixtures/foot_slide_v1.blend`, built by `uv run task fixture` |
| 2 | Select the left leg and the slide frames | demo selection: `foot.L`, frames 45-90 |
| 3 | Measure the defect (about 10 cm) | demo: 10.00 cm on frames 39-91, MAJOR |
| 4 | Submit the repair | demo: the job is created through `JobService.create_job`, the call behind `POST /v1/jobs`, and ends `COMPLETED` |
| 5 | A and B run concurrently | `tests/fault/test_fault_injection.py::test_candidates_run_concurrently`, `tests/unit/test_engine.py::test_independent_nodes_run_concurrently` |
| 6 | The original animation stays byte-for-byte unchanged | demo: the original action's F-curve hash is identical at 8 checkpoints (extract, before and after each candidate, export); `test_blender_worker.py::test_original_action_fcurves_hash_unchanged` |
| 7 | Previews and deterministic metrics for both | demo: both candidates rendered and scored; golden tests (`tests/golden`) |
| 8 | At least one candidate removes ≥ 80% of the slide | demo: A 98.0% (10.00 → 0.20 cm), B 78.8%; benchmark: A 0.00 cm in 5/5 scenes ([BENCHMARKS.md](BENCHMARKS.md)) |
| 9 | Models evaluate through Token Factory | demo: Nemotron 3 Super plan (898 prompt + 822 completion tokens) and two GLM-5.3-Flash visual reviews; $0.0031 in all. The vision model is not Nemotron: see the note on step 9 in PRODUCT.md |
| 10 | The Kotlin client shows Original / A / B | [PHASE5.md](PHASE5.md), screenshot `docs/img/review-client.png`; 17 Kotlin tests pass |
| 11 | The user selects a result | demo: decision A (the recommendation); `POST /v1/jobs/{id}/decision` tests in `tests/integration/test_api_jobs.py` |
| 12 | Applied non-destructively as an NLA layer in an output `.blend` | demo: `OUTPUT_BLEND` artifact (1.48 MB); `test_blender_worker.py::test_export_writes_output_blend_with_selected_track` |
| 13 | Metrics and logs show latency, model usage, improvement and collateral | demo: upload to review 21.1 s with per-node times, token counts, slip before/after, collateral 0.0000 cm; Prometheus names in [METRICS.md](METRICS.md) |
| 14 | The automated suite passes | 2026-10-12, locally: 428 offline, 14 Blender, 1 container parity and 17 Kotlin tests pass; CI green on PRs #5-#9 with the five required checks |

## Open (outside Phase 7)

- **Nebius Serverless Jobs live runs** wait on the owner's Nebius AI Cloud account ([PHASE6.md](PHASE6.md)).

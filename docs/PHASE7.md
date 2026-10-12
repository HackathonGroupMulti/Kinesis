# Phase 7: evaluation and hardening

Status: **in progress** (2026-10-12).

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

## Open

- **Nebius Serverless Jobs live runs** wait on the owner's Nebius AI Cloud account ([PHASE6.md](PHASE6.md)).

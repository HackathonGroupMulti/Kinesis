# Kinesis: progress report

**As of 2026-10-19, `main` at `bcce3f5`.** The MVP (Phases 0–7) is complete. Since then, the post-MVP work below has been merged. The one open MVP item is the Nebius Serverless Jobs live run, which needs a Nebius account.

## Where things stand

| Area | Status | Details |
|---|---|---|
| Phases 0–5: design, analysis, repair, orchestration, models, review client | Complete | [PHASE0](PHASE0.md) to [PHASE5](PHASE5.md) |
| Phase 6: serverless | Implemented and contract-tested; **live runs pending** | [PHASE6.md](PHASE6.md) |
| Phase 7: evaluation and hardening | Complete; all 14 definition-of-done steps have evidence | [PHASE7.md](PHASE7.md) |
| Blender add-on: Measure & Repair, Import Selected Repair | Complete (#11) | [PHASE7.md](PHASE7.md#after-the-wrap-up-the-blender-add-on-concern-c4) |
| Resuming jobs after a server restart | Complete (#15) | [FAILURE_MODES #26](FAILURE_MODES.md), [API.md](API.md) |

## Headline results

From the 5-scene benchmark ([BENCHMARKS.md](BENCHMARKS.md), run 2026-10-12):

- **Repair:** median planted foot slip goes from 7.73 cm to 0.00 cm (candidate A), with 0.0000 cm of collateral motion in every scene, including a 65-bone game rig.
- **Speed:** upload to A/B review takes a median 18.8 s with host Blender (model calls included) and 20.3 s in the worker container on 4 CPUs (deterministic presets).
- **Cost:** model calls cost a median $0.0034 per repair (Nemotron 3 Super plan plus GLM-5.3-Flash visual review).

## Merged since Phase 7

| PR | What changed |
|---|---|
| #11 | The Blender add-on works end to end from the animator's open file and imports the chosen repair as a `Kinesis:<label>` NLA track |
| #12 | Stale docs fixed: the ADR 0009 status, the vision model (GLM-5.3-Flash, not MiniCPM), and the S5 status |
| #13 | The unused 501 stub and the scene routes' 501 responses removed from the API contract |
| #14 | `uv run task setup` reports the Java version Gradle will start with, and `task client` stops early on a missing or too-old Java (Gradle 9 needs Java 17+). The S5 questions in [infra/nebius/README.md](../infra/nebius/README.md) now separate what is settled from what needs a live run |
| #15 | Jobs interrupted by a restart are resumed instead of failed: up to 2 resumes per job, with a check that the job's scene copy is complete |

## Test suite on `main`

| Suite | Tests | Runs |
|---|---|---|
| unit | 318 | every PR |
| contract | 53 | every PR |
| fault | 28 | every PR |
| golden | 13 | every PR |
| integration (offline) | 37 | every PR |
| Blender | 16 | PRs that touch Blender paths (`blender-gate`), pushes to `main`, nightly |
| container parity | 1 | nightly |
| live (gated) | 4 | manually, costs money |
| Kotlin client | 17 | every PR |

449 offline tests pass on `main`, and lint (ruff, `mypy --strict`) is clean. The test inventory in BENCHMARKS.md and SPECS.md is from the 2026-10-12 benchmark run; it changes the next time `uv run task bench` runs.

## Open

| Item | Blocked on | Next step |
|---|---|---|
| Nebius Serverless Jobs live runs (concern C2) | A Nebius AI Cloud account | Follow "To switch it on" in [PHASE6.md](PHASE6.md#to-switch-it-on-owner) |
| Vision model is not NVIDIA (concern C1) | Owner decision | Decide whether GLM-5.3-Flash is acceptable; switching is a one-line config change if an image-capable Nemotron appears |
| Worker processes that outlive a hard kill of the API | Nothing | Follow-up to #15: record worker PIDs and stop survivors before resuming a job |
| Rigs without a thigh → shin → foot chain (concern C6) | Post-MVP | A rig-mapping config |

## Next

The roadmap is in [PRODUCT.md](PRODUCT.md#future-milestones-not-part-of-the-mvp). The likely next milestone is a second defect class. **Joint jitter cleanup** is the best fit: it needs no contact surface, and detection and repair are pure numpy, so it reuses the whole pipeline (A/B candidates, NLA layering, collateral gates and review).

# Nebius deployment (Phase 6)

This document set out the boundaries before any cloud code was written. Both Nebius runners are now implemented and contract-tested; see [docs/PHASE6.md](../../docs/PHASE6.md) for the design, the API contract and how to switch it on. Live runs wait on a Nebius account (concern C2).

## Components

| Component | Nebius service | Notes |
|---|---|---|
| Kinesis API | Compute VM or container service | Single instance for the MVP; persistent disk for SQLite and artifacts |
| Redis | Managed Redis, or co-located | Cache only; it can be flushed safely |
| Blender worker | **Serverless Jobs** | `infra/docker/blender-worker.Dockerfile` (Phase 6). Runs one worker command per job |
| Models | **Token Factory** | OpenAI-compatible API; model ids come from configuration |
| Artifact exchange | Object Storage | Input prefix: `spec.json` + `scene.blend`. Output prefix: `result.json` + frames |

## Worker isolation

- The worker has no access to the API's database or to other jobs' prefixes. Each job gets credentials scoped to its own prefix, if Nebius supports that (verify in S5). If it doesn't, use pre-signed URLs.
- The container filesystem is read-only, apart from `/work`, which holds one job directory.
- The argv is fixed, the same as in `LocalJobRunner`, and the command comes from the `WorkerCommand` enum.

## S5 questions

Answers come from the Nebius docs (checked 2026-10-09, [PHASE6.md](../../docs/PHASE6.md#the-api-contract-followed-docsnebiuscom-verified-2026-10-09)). Anything that needs a live run is still open.

| # | Question | Answer |
|---|---|---|
| 1 | Submission API, authentication, quota limits | **REST:** `POST https://api.nebius.cloud/ai/v1/jobs`, poll `GET /ai/v1/jobs/{id}`, cancel `POST /ai/v1/jobs/{id}:cancel`. **Auth:** a service-account key signs an RS256 JWT, exchanged (RFC 8693) for a 12 h bearer token. **Quotas:** open; a quota `ERROR` is surfaced with the platform's reason (FAILURE_MODES #19) |
| 2 | Start-up latency for the ~2 GB worker image; image caching | **Open (live run).** Caching is not documented. The session runner (design B) pays start-up once per Kinesis job if it dominates |
| 3 | Polling or webhooks | **Polling only.** No webhook or event delivery is documented |
| 4 | CPU vs GPU shapes and prices | **Partly open.** The runner defaults to `cpu-d3` / `4vcpu-16gb`; prices are not yet recorded (`NEBIUS_PRICE_PER_HOUR_USD`). Local measurements show CPU rendering is the main cost, so a GPU shape would recover most of the time ([PHASE6.md](../../docs/PHASE6.md#local-versus-container-workstation-2026-10-09)) |
| 5 | Object Storage client | **boto3**, in the optional `nebius` extra only. The job's bucket prefix is mounted into the container at `/work`, so the worker needs no client; only the API side uploads and downloads |

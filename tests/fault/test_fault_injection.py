"""Fault-injection scenarios (docs/FAILURE_MODES.md).

Every scenario drives the real ``JobService`` and repair DAG with a ``FakeJobRunner`` and a
``MockProvider``. Each asserts the retry behaviour, the isolation scope and the error code the
user sees. Backoff sleeps are recorded, not slept.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest
from conftest import FAKE_BLEND, chunks, job_request, run_job

from kinesis.errors import ProviderRateLimited, ProviderServerError, ProviderTimeout
from kinesis.orchestration.breaker import BreakerState
from kinesis.orchestration.caches import RedisCache
from kinesis.providers.mock import MockProvider
from kinesis.schemas import (
    CandidateLabel,
    CandidateStatus,
    DecisionChoice,
    DecisionRequest,
    EvaluatorStatus,
    JobEvent,
    JobEventType,
    JobStatus,
    PlanSource,
    RepairJob,
)
from kinesis.schemas.common import ErrorCode
from kinesis.schemas.worker import WorkerCommand
from kinesis.testing.fake_runner import FakeJobRunner, Fault
from kinesis.testing.synthetic import foot_slide_v1

pytestmark = pytest.mark.fault


def candidate(job: RepairJob, label: str) -> Any:
    return next(c for c in job.candidates if c.label is CandidateLabel(label))


async def events(service: Any, job: RepairJob) -> list[JobEvent]:
    return list(await service.store.list_events(job.job_id, after_seq=0, limit=500))


# ------------------------------------------------------------------ provider faults (#1-#6)


async def test_model_timeout_retried_then_fallback_plan(make_service: Any) -> None:
    provider = MockProvider(plans=[ProviderTimeout("read timeout")] * 3)
    service = make_service(provider=provider)
    job = await run_job(service)
    assert provider.plan_calls == 3
    assert job.plan is not None
    assert job.plan.plan_source is PlanSource.FALLBACK
    assert "PROVIDER_TIMEOUT" in (job.plan.fallback_reason or "")
    rejected = [e for e in await events(service, job) if e.type is JobEventType.PLAN_REJECTED]
    assert rejected
    assert rejected[0].data["code"] == "PROVIDER_TIMEOUT"
    assert job.status is JobStatus.AWAITING_DECISION


async def test_model_429_respects_retry_after(make_service: Any) -> None:
    provider = MockProvider(plans=[ProviderRateLimited("429", retry_after_s=12.0)])
    service = make_service(provider=provider)
    job = await run_job(service)
    assert provider.plan_calls == 2
    assert 12.0 in service.sleep_recorder.calls
    assert job.plan is not None
    assert job.plan.plan_source is PlanSource.MODEL


async def test_breaker_opens_after_threshold_and_fails_fast(make_service: Any) -> None:
    provider = MockProvider(plans=[ProviderServerError("503")] * 100)
    service = make_service(provider=provider)
    first = await run_job(service, key="key-00000001")
    second = await run_job(service, key="key-00000002")
    assert provider.plan_calls == 5  # 3 + 2, then the breaker opened mid-retry
    assert service.planner_breaker.state is BreakerState.OPEN
    third = await run_job(service, key="key-00000003")
    assert provider.plan_calls == 5  # failed fast, no call made
    for job in (first, second, third):
        assert job.plan is not None
        assert job.plan.plan_source is PlanSource.FALLBACK
        assert job.status is JobStatus.AWAITING_DECISION
    assert third.plan is not None
    assert "circuit open" in (third.plan.fallback_reason or "")


async def test_malformed_model_plan_uses_fallback(make_service: Any) -> None:
    provider = MockProvider(plans=["{not json"])
    service = make_service(provider=provider)
    job = await run_job(service)
    assert job.plan is not None
    assert job.plan.plan_source is PlanSource.FALLBACK
    rejected = [e for e in await events(service, job) if e.type is JobEventType.PLAN_REJECTED]
    assert rejected[0].data["code"] == "PLAN_INVALID"


async def test_visual_evaluation_failure_degrades(make_service: Any) -> None:
    provider = MockProvider(evaluations=[ProviderServerError("503")] * 3)
    service = make_service(provider=provider)
    job = await run_job(service)
    assert job.evaluation is not None
    assert job.evaluation.evaluator_status is EvaluatorStatus.DEGRADED
    assert len(job.evaluation.visual) == 1  # the other candidate was judged
    assert job.evaluation.objective_ranking
    assert provider.eval_frame_counts
    assert all(n <= 10 for n in provider.eval_frame_counts)  # S4: at most 10 images per call


# ------------------------------------------------------------------ worker faults (#8-#14)


async def test_blender_subprocess_crash_retried_once(make_service: Any) -> None:
    runner = FakeJobRunner({"apply_a": [Fault.CRASH], "extract_scope": [Fault.CRASH] * 2})
    service = make_service(runner=runner)
    job = await run_job(service)
    assert job.status is JobStatus.AWAITING_DECISION
    assert candidate(job, "A").status is CandidateStatus.SUCCEEDED
    retries = [e for e in await events(service, job) if e.type is JobEventType.NODE_RETRY]
    assert {e.node for e in retries} == {"apply_render_A", "extract_scope"}


async def test_extract_crash_exhausted_fails_job(make_service: Any) -> None:
    service = make_service(runner=FakeJobRunner({"extract_scope": [Fault.CRASH] * 3}))
    job = await run_job(service)
    assert job.status is JobStatus.FAILED
    assert job.error is not None
    assert job.error.code is ErrorCode.WORKER_CRASHED
    assert job.error.node == "extract_scope"


async def test_candidate_a_fails_candidate_b_succeeds_awaiting_decision(make_service: Any) -> None:
    service = make_service(runner=FakeJobRunner({"apply_a": [Fault.CRASH] * 2}))
    job = await run_job(service)
    assert job.status is JobStatus.AWAITING_DECISION
    a, b = candidate(job, "A"), candidate(job, "B")
    assert a.status is CandidateStatus.FAILED
    assert a.error is not None
    assert a.error.code is ErrorCode.WORKER_CRASHED
    assert "apply_render_A" in a.error.message
    assert "(attempt 2/2)" in a.error.message
    assert "candidate B unaffected" in a.error.message
    assert b.status is CandidateStatus.SUCCEEDED
    assert job.evaluation is not None
    assert [r.label for r in job.evaluation.objective_ranking] == [CandidateLabel.B]


async def test_both_candidates_fail_job_failed_no_viable_candidate(make_service: Any) -> None:
    runner = FakeJobRunner({"apply_a": [Fault.CRASH] * 2, "apply_b": [Fault.CRASH] * 2})
    service = make_service(runner=runner)
    job = await run_job(service)
    assert job.status is JobStatus.FAILED
    assert job.error is not None
    assert job.error.code is ErrorCode.NO_VIABLE_CANDIDATE


async def test_render_timeout_isolated_to_candidate(make_service: Any) -> None:
    service = make_service(runner=FakeJobRunner({"apply_b": [Fault.TIMEOUT] * 2}))
    job = await run_job(service)
    assert job.status is JobStatus.AWAITING_DECISION
    b = candidate(job, "B")
    assert b.status is CandidateStatus.FAILED
    assert b.error is not None
    assert b.error.code is ErrorCode.WORKER_TIMEOUT
    assert candidate(job, "A").status is CandidateStatus.SUCCEEDED


async def test_malformed_worker_result_not_retried(make_service: Any) -> None:
    runner = FakeJobRunner({"apply_a": [Fault.MALFORMED]})
    service = make_service(runner=runner)
    job = await run_job(service)
    a = candidate(job, "A")
    assert a.status is CandidateStatus.FAILED
    assert a.error is not None
    assert a.error.code is ErrorCode.WORKER_OUTPUT_INVALID
    assert sum(1 for c in runner.calls if c.node == "apply_a") == 1


async def test_original_hash_change_is_a_bug_alarm(
    make_service: Any, caplog: pytest.LogCaptureFixture
) -> None:
    service = make_service(runner=FakeJobRunner({"apply_b": [Fault.HASH_MISMATCH]}))
    with caplog.at_level("ERROR", logger="kinesis.pipeline"):
        job = await run_job(service)
    b = candidate(job, "B")
    assert b.status is CandidateStatus.FAILED
    assert b.error is not None
    assert b.error.code is ErrorCode.WORKER_OUTPUT_INVALID
    assert any(r.message == "bug_alarm.original_action_modified" for r in caplog.records)


async def test_serverless_job_failure_surfaces_error(make_service: Any, tmp_path: Any) -> None:
    """FAILURE_MODES #19: a Nebius job that ends ERROR fails the node with the platform's reason."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from kinesis.jobs.nebius.api import NebiusJobsClient
    from kinesis.jobs.nebius.auth import NebiusTokenProvider, ServiceAccountKey
    from kinesis.jobs.nebius.config import NebiusJobConfig, SpendGuard
    from kinesis.jobs.nebius.runner import NebiusJobRunner
    from kinesis.testing.fake_nebius import FakeNebius, Outcome

    pem = tmp_path / "k.pem"
    pem.write_bytes(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    fake = FakeNebius(
        outcomes=[Outcome.NORMAL] + [Outcome.ERROR] * 3
    )  # inspect ok, extract x3 fail
    transport = fake.transport()
    tokens = NebiusTokenProvider(ServiceAccountKey("sa", "kid", pem), transport=transport)

    async def no_sleep(_: float) -> None:
        return None

    runner = NebiusJobRunner(
        NebiusJobsClient(tokens, transport=transport),
        fake.store,
        NebiusJobConfig(project_id="p", image="img", bucket=fake.store.bucket),
        SpendGuard(tmp_path / "spend.json", 5.0, None),
        sleep=no_sleep,
    )
    job = await run_job(make_service(runner=runner))
    assert job.status is JobStatus.FAILED
    assert job.error is not None
    assert job.error.code is ErrorCode.WORKER_CRASHED
    assert job.error.node == "extract_scope"
    assert "quota exceeded" in job.error.message


# ------------------------------------------------------------------ infrastructure (#16, #20, #25)


class BrokenRedis:
    async def get(self, *_: Any, **__: Any) -> None:
        from redis.exceptions import ConnectionError as RedisConnectionError

        raise RedisConnectionError("down")

    set = delete = get


async def test_redis_unavailable_degrades_to_memory_cache(make_service: Any) -> None:
    cache = RedisCache(BrokenRedis())
    service = make_service(cache=cache)
    job = await run_job(service)
    assert job.status is JobStatus.AWAITING_DECISION
    assert cache.degraded_calls > 0


async def test_extract_is_cached_across_jobs(make_service: Any) -> None:
    from kinesis.orchestration.caches import MemoryCache

    runner = FakeJobRunner()
    service = make_service(runner=runner, cache=MemoryCache())
    scene = await service.upload_scene(chunks(FAKE_BLEND))
    for key in ("key-00000001", "key-00000002"):
        await service.create_job(job_request(scene_id=scene.scene_id), key)
        await service.wait_idle()
    assert sum(1 for c in runner.calls if c.command is WorkerCommand.EXTRACT) == 1


async def test_duplicate_status_event_deduplicated(make_service: Any) -> None:
    service = make_service()
    job = await run_job(service)
    before = job.last_event_seq
    event = JobEvent(seq=1, job_id=job.job_id, ts=job.updated_at, type=JobEventType.NODE_SUCCEEDED)
    first = await service.store.append_event(event, dedupe_key="callback-42")
    second = await service.store.append_event(event, dedupe_key="callback-42")
    assert first.seq == second.seq == before + 1
    assert (await service.get_job(job.job_id)).last_event_seq == before + 1


async def test_cancel_mid_run_cancels_child_tasks(make_service: Any) -> None:
    runner = FakeJobRunner({"extract_scope": [Fault.HANG]})
    service = make_service(runner=runner)
    scene = await service.upload_scene(chunks(FAKE_BLEND))
    job, _ = await service.create_job(job_request(scene_id=scene.scene_id), "key-00000001")
    for _ in range(200):
        if runner.in_flight:
            break
        await asyncio.sleep(0.005)
    cancelled = await service.cancel(job.job_id)
    assert cancelled.status is JobStatus.CANCELLED
    assert runner.cancelled == ["extract_scope"]
    assert runner.in_flight == 0


async def test_event_callback_exception_does_not_abort_dag(make_service: Any) -> None:
    service = make_service()
    original = service.store.append_event

    async def flaky(event: JobEvent, **kw: Any) -> JobEvent:
        if event.type is JobEventType.NODE_STARTED:
            raise RuntimeError("event store hiccup")
        return await original(event, **kw)

    service.store.append_event = flaky
    job = await run_job(service)
    assert job.status is JobStatus.AWAITING_DECISION


# ------------------------------------------------------------------ pipeline behaviour


async def test_candidates_run_concurrently(make_service: Any) -> None:
    runner = FakeJobRunner(delay_s=0.05)
    service = make_service(runner=runner)
    job = await run_job(service)
    assert job.status is JobStatus.AWAITING_DECISION
    assert runner.max_concurrent(WorkerCommand.APPLY_RENDER) >= 2


async def test_happy_path_records_everything(make_service: Any) -> None:
    service = make_service(provider=MockProvider())
    job = await run_job(service)
    assert job.status is JobStatus.AWAITING_DECISION
    assert job.defect is not None
    assert job.defect.worst is not None
    assert 9.5 <= job.defect.worst.planted_displacement_cm <= 10.5
    assert job.plan is not None
    assert job.plan.plan_source is PlanSource.MODEL
    for c in job.candidates:
        assert c.status is CandidateStatus.SUCCEEDED
        assert c.metrics is not None
        assert c.metrics.slip_reduction_pct >= 80
        kinds = {a.kind.value for a in c.artifacts}
        assert kinds == {"CANDIDATE_BLEND", "PREVIEW_FRAMES", "CONTEXT_FRAMES", "METRICS_JSON"}
    assert {a.kind.value for a in job.original_artifacts} == {"PREVIEW_FRAMES", "CONTEXT_FRAMES"}
    assert job.evaluation is not None
    assert job.evaluation.evaluator_status is EvaluatorStatus.OK
    assert job.evaluation.recommended is CandidateLabel.A  # B leaves 1.2 cm (acceptance line)
    seqs = [e.seq for e in await events(service, job)]
    assert seqs == list(range(1, len(seqs) + 1))


async def test_null_provider_skips_visual_evaluation(make_service: Any) -> None:
    job = await run_job(make_service())
    assert job.evaluation is not None
    assert job.evaluation.evaluator_status is EvaluatorStatus.SKIPPED
    assert job.plan is not None
    assert job.plan.fallback_reason == "no model provider configured"


async def test_no_defect_completes_without_candidates(make_service: Any) -> None:
    service = make_service(runner=FakeJobRunner(scene=foot_slide_v1(defect=False)))
    job = await run_job(service)
    assert job.status is JobStatus.COMPLETED
    assert job.defect is not None
    assert job.defect.severity.value == "NONE"
    assert job.candidates == ()


async def _wait_for(predicate: Any, what: str) -> None:
    """Poll ``predicate`` (plain or async) until it is true."""
    for _ in range(2000):  # up to 10 s; CI runners can be slow
        value = predicate()
        if inspect.isawaitable(value):
            value = await value
        if value:
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for {what}")


async def _die(service: Any) -> None:
    """Simulate the process dying: every background task stops where it is."""
    for task in list(service._tasks.values()):
        task.cancel()
    await service.wait_idle()


def _restarted(service: Any, runner: Any = None) -> Any:
    """A new process on the same database and job directories."""
    from kinesis.jobs.service import JobService

    return JobService(
        service.store,
        service.artifacts,
        runner if runner is not None else FakeJobRunner(),
        service.provider,
        service.config,
        sleep=service.sleep,
        jitter=service.jitter,
    )


async def _start_hung_job(make_service: Any, node: str) -> tuple[Any, Any, str]:
    runner = FakeJobRunner({node: [Fault.HANG]})
    service = make_service(runner=runner)
    scene = await service.upload_scene(chunks(FAKE_BLEND))
    job, _ = await service.create_job(job_request(scene_id=scene.scene_id), "key-00000001")
    return service, runner, job.job_id


async def _resume_events(service: Any, job_id: str) -> list[Any]:
    events = await service.store.list_events(job_id, after_seq=0, limit=10_000)
    return [e for e in events if "resume" in e.data]


async def test_restart_resumes_a_running_job(make_service: Any) -> None:
    """FAILURE_MODES #26: a job interrupted mid-DAG runs again and reaches review."""
    service, runner, job_id = await _start_hung_job(make_service, "apply_a")

    async def partly_done() -> bool:
        job = await service.get_job(job_id)
        return runner.in_flight > 0 and job.defect is not None and len(job.candidates) == 2

    await _wait_for(partly_done, "the DAG to reach apply_a")
    await _die(service)
    interrupted = await service.get_job(job_id)
    assert interrupted.status is JobStatus.RUNNING

    restarted = _restarted(service)
    report = await restarted.recover()
    assert (report.resumed, report.failed) == (1, 0)
    await restarted.wait_idle()

    job = await restarted.get_job(job_id)
    assert job.status is JobStatus.AWAITING_DECISION
    assert job.error is None
    assert sorted(c.label.value for c in job.candidates) == ["A", "B"]  # no leftovers
    assert all(c.status is CandidateStatus.SUCCEEDED for c in job.candidates)
    assert job.evaluation is not None
    [event] = await _resume_events(restarted, job_id)
    assert event.message == "resumed after a server restart (1/2)"


async def test_restart_resumes_an_applying_job(make_service: Any) -> None:
    """A decision recorded before the restart is exported after it."""
    service, runner, job_id = await _start_hung_job(make_service, "export")
    await service.wait_idle()
    job = await service.get_job(job_id)
    assert job.status is JobStatus.AWAITING_DECISION
    await service.decide(job_id, DecisionRequest(choice=DecisionChoice.A))
    await _wait_for(lambda: runner.in_flight > 0, "the export to start")
    await _die(service)

    restarted = _restarted(service)
    report = await restarted.recover()
    assert (report.resumed, report.failed) == (1, 0)
    await restarted.wait_idle()

    done = await restarted.get_job(job_id)
    assert done.status is JobStatus.COMPLETED
    assert done.output is not None
    assert done.decision is not None
    assert done.decision.choice is DecisionChoice.A


async def test_app_startup_resumes_interrupted_jobs(make_service: Any) -> None:
    """The API's startup hook runs recovery, so a restarted server resumes the job."""
    from kinesis.main import create_app

    service, runner, job_id = await _start_hung_job(make_service, "extract_scope")
    await _wait_for(lambda: runner.in_flight > 0, "extract_scope to start")
    await _die(service)

    restarted = _restarted(service)
    app = create_app(restarted)
    async with app.router.lifespan_context(app):
        await restarted.wait_idle()
    job = await restarted.get_job(job_id)
    assert job.status is JobStatus.AWAITING_DECISION
    assert len(await _resume_events(restarted, job_id)) == 1


async def test_restart_gives_up_after_max_resumes(make_service: Any) -> None:
    """A job that keeps dying with the process is FAILED instead of looping."""
    from kinesis.jobs.service import MAX_RESUMES

    service, runner, job_id = await _start_hung_job(make_service, "extract_scope")
    await _wait_for(lambda: runner.in_flight > 0, "extract_scope to start")
    await _die(service)
    current = service
    for _ in range(MAX_RESUMES):
        runner = FakeJobRunner({"extract_scope": [Fault.HANG]})
        current = _restarted(current, runner)
        assert (await current.recover()).resumed == 1
        await _wait_for(lambda r=runner: r.in_flight > 0, "the resumed run")
        await _die(current)

    final = _restarted(current)
    report = await final.recover()
    assert (report.resumed, report.failed) == (0, 1)
    job = await final.get_job(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error is not None
    assert job.error.code is ErrorCode.INTERNAL
    assert f"not resumed again after {MAX_RESUMES} resumes" in job.error.message
    assert len(await _resume_events(final, job_id)) == MAX_RESUMES


async def test_restart_fails_a_job_whose_scene_copy_is_gone(make_service: Any) -> None:
    service, runner, job_id = await _start_hung_job(make_service, "extract_scope")
    await _wait_for(lambda: runner.in_flight > 0, "extract_scope to start")
    await _die(service)
    (service.artifacts.job_dir(job_id) / "input" / "scene.blend").unlink()

    restarted = _restarted(service)
    report = await restarted.recover()
    assert (report.resumed, report.failed) == (0, 1)
    job = await restarted.get_job(job_id)
    assert job.status is JobStatus.FAILED
    assert job.error is not None
    assert "restart" in job.error.message
    assert "missing or incomplete" in job.error.message


async def test_original_and_candidates_share_one_crop_camera(make_service: Any) -> None:
    """A/B fairness: every pane (and the vision model) sees the same camera."""
    import json as _json

    service = make_service()
    job = await run_job(service)
    job_dir = service.artifacts.job_dir(job.job_id)
    renders = [
        _json.loads((job_dir / "work" / f"{node}.spec.json").read_text(encoding="utf-8"))["render"]
        for node in ("apply_a", "apply_b", "render_original")
    ]
    assert renders[0] == renders[1] == renders[2]

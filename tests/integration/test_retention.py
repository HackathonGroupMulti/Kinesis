"""Retention sweeper (ARCHITECTURE §8): finished workspaces and old uploads are deleted."""

from __future__ import annotations

import os
import time
from typing import Any

import pytest
from conftest import FAKE_BLEND, chunks, job_request, run_job

from kinesis.jobs.service import JobService, RequestError
from kinesis.schemas import DecisionChoice, DecisionRequest, JobStatus

DAY = 86400.0


async def finished_job(service: JobService) -> Any:
    job = await run_job(service)
    assert job.status is JobStatus.AWAITING_DECISION
    await service.decide(job.job_id, DecisionRequest(choice=DecisionChoice.A))
    await service.wait_idle()
    done = await service.get_job(job.job_id)
    assert done.status is JobStatus.COMPLETED
    return done


async def test_recent_work_is_kept(make_service: Any) -> None:
    service = make_service()
    job = await finished_job(service)
    report = await service.sweep(7 * DAY)
    assert (report.jobs, report.scenes) == (0, 0)
    assert service.artifacts.job_dir(job.job_id).exists()


async def test_finished_jobs_lose_their_files_but_keep_their_record(make_service: Any) -> None:
    service = make_service()
    job = await finished_job(service)
    report = await service.sweep(-1)  # everything is "older" than a negative retention
    assert report.jobs == 1
    assert report.scenes == 1
    assert not service.artifacts.job_dir(job.job_id).exists()
    kept = await service.get_job(job.job_id)
    assert kept.status is JobStatus.COMPLETED
    assert kept.decision is not None
    with pytest.raises(FileNotFoundError):
        await service.artifacts.resolve(kept.output.artifact_id)
    assert (await service.sweep(-1)).jobs == 0  # nothing left to do


async def test_jobs_awaiting_a_decision_are_never_swept(make_service: Any) -> None:
    service = make_service()
    job = await run_job(service)
    assert job.status is JobStatus.AWAITING_DECISION
    assert (await service.sweep(-1)).jobs == 0
    assert service.artifacts.job_dir(job.job_id).exists()
    await service.decide(job.job_id, DecisionRequest(choice=DecisionChoice.B))
    await service.wait_idle()
    assert (await service.get_job(job.job_id)).status is JobStatus.COMPLETED  # still works


async def test_swept_scenes_are_gone_for_new_jobs(make_service: Any) -> None:
    service = make_service()
    scene = await service.upload_scene(chunks(FAKE_BLEND))
    assert (await service.sweep(-1)).scenes == 1
    assert not service.scene_dir(scene.scene_id).exists()
    with pytest.raises(RequestError) as err:
        await service.create_job(job_request(scene_id=scene.scene_id), "key-00000009")
    assert err.value.status == 404


async def test_orphan_upload_dirs_are_swept_by_age(make_service: Any) -> None:
    service = make_service()
    orphan = service.config.scenes_root / "scn_crashed"
    orphan.mkdir(parents=True)
    assert (await service.sweep(DAY)).scenes == 0  # fresh: maybe an upload in progress
    old = time.time() - 2 * DAY
    os.utime(orphan, (old, old))
    assert (await service.sweep(DAY)).scenes == 1
    assert not orphan.exists()


async def test_the_sweeper_loop_survives_a_failed_sweep() -> None:
    import asyncio

    from kinesis.jobs.service import SweepReport
    from kinesis.main import run_sweeper

    calls: list[float] = []
    third = asyncio.Event()

    class Flaky:
        async def sweep(self, retention_s: float) -> SweepReport:
            calls.append(retention_s)
            if len(calls) == 3:
                third.set()
            if len(calls) == 1:
                raise OSError("disk hiccup")
            return SweepReport(jobs=1, scenes=0)

    task = asyncio.create_task(run_sweeper(Flaky(), 7 * DAY, 0.01))  # type: ignore[arg-type]
    await asyncio.wait_for(third.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls[:3] == [7 * DAY] * 3

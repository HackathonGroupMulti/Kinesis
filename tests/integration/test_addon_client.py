"""The Blender add-on's HTTP client against the real API, served by uvicorn (no Blender).

``blender/addon/kinesis_addon/client.py`` is standard library only, so it runs here exactly as
it runs inside Blender's bundled Python.
"""

from __future__ import annotations

import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from conftest import FAKE_BLEND

from kinesis.main import create_app
from kinesis.providers.mock import MockProvider

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "blender" / "addon"))
from kinesis_addon.client import (
    KinesisClient,
    KinesisClientError,
    action_name,
    job_body,
    track_name,
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve(service: Any) -> Iterator[str]:
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(service), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture
def server(make_service: Any) -> Iterator[str]:
    yield from _serve(make_service(provider=MockProvider()))


def _wait(client: KinesisClient, job_id: str, status: str) -> dict[str, Any]:
    deadline = time.monotonic() + 20
    while True:
        job = client.get_job(job_id)
        if job["status"] == status:
            return job
        assert job["status"] not in ("FAILED", "CANCELLED"), job
        assert time.monotonic() < deadline, job["status"]
        time.sleep(0.05)


def test_full_round_trip(server: str, tmp_path: Path) -> None:
    client = KinesisClient(server)
    blend = tmp_path / "scene.blend"
    blend.write_bytes(FAKE_BLEND)
    scene = client.upload_scene(blend)
    assert scene["armatures"][0]["name"] == "Rig"
    body = job_body(scene["scene_id"], "Rig", ["foot.L"], 45, 90, "  keep the heel planted ")
    assert body["selection"]["instruction"] == "keep the heel planted"
    job = client.create_job(body, "addon-key-0001")
    assert client.create_job(body, "addon-key-0001")["job_id"] == job["job_id"]  # idempotent
    ready = _wait(client, job["job_id"], "AWAITING_DECISION")
    assert ready["evaluation"]["recommended"] in ("A", "B")
    client.decide(job["job_id"], "A")
    done = _wait(client, job["job_id"], "COMPLETED")
    out = client.download(done["output"]["uri"], tmp_path / "repaired.blend")
    assert out.read_bytes() == b"BLENDER-fake-export"
    assert action_name(job["job_id"], "A") == f"KIN_{job['job_id']}_A"
    assert track_name("A") == "Kinesis:A"


def test_problems_become_readable_errors(make_service: Any, tmp_path: Path) -> None:
    for url in _serve(make_service(provider=MockProvider(), max_upload_bytes=16)):
        client = KinesisClient(url)
        blend = tmp_path / "scene.blend"
        blend.write_bytes(FAKE_BLEND)
        with pytest.raises(KinesisClientError) as err:
            client.upload_scene(blend)
        assert (err.value.status, err.value.code) == (413, "FILE_TOO_LARGE")
        with pytest.raises(KinesisClientError) as err:
            client.get_job("job_missing0001")
        assert err.value.status == 404


def test_unreachable_server_is_reported() -> None:
    client = KinesisClient(f"http://127.0.0.1:{_free_port()}", timeout_s=2)
    with pytest.raises(KinesisClientError, match="cannot reach Kinesis"):
        client.get_job("job_x")


@pytest.mark.parametrize(
    ("bones", "start", "end", "match"),
    [([], 1, 10, "select the foot"), (["foot.L"], 10, 1, "empty")],
)
def test_job_body_validates_the_selection(
    bones: list[str], start: int, end: int, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        job_body("scn_x", "Rig", bones, start, end)

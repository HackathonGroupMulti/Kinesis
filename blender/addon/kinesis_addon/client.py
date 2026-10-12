"""A small Kinesis API client for the add-on: standard library only, no bpy.

Blender ships its own Python without third-party packages, so this uses ``urllib``. Every
method blocks; the add-on calls them from a worker thread, never from Blender's UI thread.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any
from urllib import error, request

DEFAULT_URL = "http://127.0.0.1:8000"
CHUNK = 1 << 20


class KinesisClientError(Exception):
    """An API Problem (RFC 7807) or a transport failure, in one readable message."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def _problem(exc: error.HTTPError) -> KinesisClientError:
    try:
        body = json.loads(exc.read().decode("utf-8"))
    except (ValueError, OSError):
        body = {}
    code = body.get("code")
    detail = body.get("detail") or body.get("title") or exc.reason
    return KinesisClientError(f"{exc.code} {code or ''}: {detail}".strip(), status=exc.code, code=code)


class KinesisClient:
    def __init__(self, base_url: str = DEFAULT_URL, *, timeout_s: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _send(self, req: request.Request) -> Any:
        try:
            with request.urlopen(req, timeout=self.timeout_s) as resp:  # noqa: S310 - http(s) URL from prefs
                data = resp.read()
        except error.HTTPError as exc:
            raise _problem(exc) from exc
        except (error.URLError, OSError) as exc:
            raise KinesisClientError(f"cannot reach Kinesis at {self.base_url}: {exc}") from exc
        return json.loads(data.decode("utf-8")) if data else None

    def _json(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = request.Request(f"{self.base_url}{path}", data=data, method=method)  # noqa: S310
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        return self._send(req)

    def upload_scene(self, blend: Path) -> dict[str, Any]:
        """``POST /v1/scenes`` with the .blend as the multipart ``file`` field."""
        boundary = f"kinesis-{uuid.uuid4().hex}"
        head = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="scene.blend"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        tail = f"\r\n--{boundary}--\r\n".encode()
        size = len(head) + os.path.getsize(blend) + len(tail)

        def body() -> Any:
            yield head
            with open(blend, "rb") as fh:
                while chunk := fh.read(CHUNK):
                    yield chunk
            yield tail

        req = request.Request(f"{self.base_url}/v1/scenes", data=body(), method="POST")  # noqa: S310
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("Content-Length", str(size))
        req.add_header("Accept", "application/json")
        result: dict[str, Any] = self._send(req)
        return result

    def create_job(self, body: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        result: dict[str, Any] = self._json(
            "POST", "/v1/jobs", body, {"Idempotency-Key": idempotency_key}
        )
        return result

    def get_job(self, job_id: str) -> dict[str, Any]:
        result: dict[str, Any] = self._json("GET", f"/v1/jobs/{job_id}")
        return result

    def decide(self, job_id: str, choice: str) -> dict[str, Any]:
        result: dict[str, Any] = self._json(
            "POST", f"/v1/jobs/{job_id}/decision", {"choice": choice}
        )
        return result

    def download(self, uri: str, dest: Path) -> Path:
        """Stream an artifact (``/v1/artifacts/...``) to ``dest``."""
        req = request.Request(f"{self.base_url}{uri}", method="GET")  # noqa: S310
        try:
            with request.urlopen(req, timeout=self.timeout_s) as resp, open(dest, "wb") as out:  # noqa: S310
                while chunk := resp.read(CHUNK):
                    out.write(chunk)
        except error.HTTPError as exc:
            raise _problem(exc) from exc
        except (error.URLError, OSError) as exc:
            raise KinesisClientError(f"download failed: {exc}") from exc
        return dest


def job_body(
    scene_id: str,
    armature: str,
    bones: list[str],
    frame_start: int,
    frame_end: int,
    instruction: str = "",
) -> dict[str, Any]:
    """The ``POST /v1/jobs`` body for the add-on's selection."""
    if not bones:
        raise ValueError("select the foot bone to repair (pose mode)")
    if frame_end < frame_start:
        raise ValueError("the frame range is empty")
    selection: dict[str, Any] = {
        "scene_id": scene_id,
        "armature": armature,
        "target_bones": bones,
        "temporal": {"frame_start": frame_start, "frame_end": frame_end},
    }
    if instruction.strip():
        selection["instruction"] = instruction.strip()
    return {"selection": selection}


def action_name(job_id: str, label: str) -> str:
    """The candidate Action's name in the output .blend (``kinesis.jobs.specs.action_name``)."""
    return f"KIN_{job_id}_{label}"[:63]


def track_name(label: str) -> str:
    return f"Kinesis:{label}"


__all__ = [
    "DEFAULT_URL",
    "KinesisClient",
    "KinesisClientError",
    "action_name",
    "job_body",
    "track_name",
]

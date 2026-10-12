"""Streaming multipart upload (Phase 7 hardening; PHASE3.md finding 9).

FastAPI's ``UploadFile`` makes Starlette spool the whole multipart body to a temporary file
before the route runs, so an oversized upload filled the disk before the size check fired.
Here the request body is parsed as it arrives: the ``file`` part's bytes go straight to
``JobService.upload_scene``, which enforces the size cap and the magic check on the fly and
stops reading as soon as either fails. A declared ``Content-Length`` far over the cap is
refused before a single body byte is read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from python_multipart.multipart import MultipartParser, parse_options_header

from kinesis.jobs.service import RequestError
from kinesis.schemas.common import ErrorCode

FIELD = b"file"
MULTIPART_OVERHEAD = 64 * 1024
"""Allowance for boundaries and part headers when comparing Content-Length with the cap."""

# The request body as OpenAPI shows it: identical to what ``UploadFile = File()`` produced,
# so the published contract (and the generated Kotlin client) did not change.
BODY_SCHEMA_NAME = "Body_upload_scene_v1_scenes_post"
BODY_SCHEMA = {
    "properties": {
        "file": {"contentMediaType": "application/octet-stream", "title": "File", "type": "string"}
    },
    "required": ["file"],
    "title": BODY_SCHEMA_NAME,
    "type": "object",
}
REQUEST_BODY = {
    "content": {
        "multipart/form-data": {"schema": {"$ref": f"#/components/schemas/{BODY_SCHEMA_NAME}"}}
    },
    "required": True,
}


def _invalid(detail: str) -> RequestError:
    return RequestError(422, ErrorCode.VALIDATION_ERROR, "Request validation failed", detail)


def check_declared_size(request: Request, max_bytes: int) -> None:
    declared = request.headers.get("content-length")
    if (
        declared is not None
        and declared.isdigit()
        and int(declared) > max_bytes + MULTIPART_OVERHEAD
    ):
        raise RequestError(
            413, ErrorCode.FILE_TOO_LARGE, "File too large", f"the limit is {max_bytes} bytes"
        )


async def file_part(request: Request) -> AsyncIterator[bytes]:
    """Yield the bytes of the ``file`` form field as the body streams in."""
    content_type, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if content_type != b"multipart/form-data" or not boundary:
        raise _invalid("expected multipart/form-data with a `file` field")

    pending: list[bytes] = []
    state: dict[str, object] = {"name": None, "in_file": False, "seen": False}
    header_field = bytearray()
    header_value = bytearray()

    def on_part_begin() -> None:
        state["name"] = None

    def on_header_field(data: bytes, start: int, end: int) -> None:
        header_field.extend(data[start:end])

    def on_header_value(data: bytes, start: int, end: int) -> None:
        header_value.extend(data[start:end])

    def on_header_end() -> None:
        if bytes(header_field).lower() == b"content-disposition":
            _, disp = parse_options_header(bytes(header_value))
            state["name"] = disp.get(b"name")
        header_field.clear()
        header_value.clear()

    def on_headers_finished() -> None:
        state["in_file"] = state["name"] == FIELD and not state["seen"]
        if state["in_file"]:
            state["seen"] = True

    def on_part_data(data: bytes, start: int, end: int) -> None:
        if state["in_file"]:
            pending.append(bytes(data[start:end]))

    def on_part_end() -> None:
        state["in_file"] = False

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": on_part_begin,
            "on_header_field": on_header_field,
            "on_header_value": on_header_value,
            "on_header_end": on_header_end,
            "on_headers_finished": on_headers_finished,
            "on_part_data": on_part_data,
            "on_part_end": on_part_end,
        },
    )
    async for chunk in request.stream():
        if not chunk:
            continue
        parser.write(chunk)
        if pending:
            out = b"".join(pending)
            pending.clear()
            yield out
    parser.finalize()
    if pending:
        yield b"".join(pending)
    if not state["seen"]:
        raise _invalid("the multipart body has no `file` field")


__all__ = ["BODY_SCHEMA", "BODY_SCHEMA_NAME", "REQUEST_BODY", "check_declared_size", "file_part"]

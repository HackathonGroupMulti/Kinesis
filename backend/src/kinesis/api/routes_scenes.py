"""Scene upload and inspection."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request, status

from kinesis.api.deps import get_service
from kinesis.api.errors import problem_responses
from kinesis.api.upload import REQUEST_BODY, check_declared_size, file_part
from kinesis.jobs.service import JobService
from kinesis.schemas import SceneRef

router = APIRouter(prefix="/v1/scenes", tags=["scenes"])

SceneIdPath = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9_-]{5,63}$")]
Service = Annotated[JobService, Depends(get_service)]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SceneRef,
    responses=problem_responses(413, 422),
    summary="Upload a .blend copy and inspect it",
    description=(
        "Accepts a single `.blend` file (at most KINESIS_MAX_UPLOAD_MB; it must start with "
        "the `BLENDER` magic bytes). The client filename is ignored. The file is stored as "
        "`input/scene.blend` in a new scene directory and inspected headlessly with "
        "auto-run scripts disabled. The body is streamed: an oversized upload is refused "
        "as soon as it crosses the limit (or up front, from its Content-Length)."
    ),
    openapi_extra={"requestBody": REQUEST_BODY},
)
async def upload_scene(request: Request, service: Service) -> SceneRef:
    check_declared_size(request, service.config.max_upload_bytes)
    return await service.upload_scene(file_part(request))


@router.get(
    "/{scene_id}",
    response_model=SceneRef,
    responses=problem_responses(404),
    summary="Get an uploaded scene's inventory",
)
async def get_scene(scene_id: SceneIdPath, service: Service) -> SceneRef:
    return await service.get_scene(scene_id)

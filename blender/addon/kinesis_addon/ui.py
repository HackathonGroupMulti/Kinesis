"""Panel, operators and the background worker of the add-on.

Network calls run on one worker thread; Blender data is only touched on the main thread, by
a timer that drains the worker's results. The UI never blocks on the network.
"""

from __future__ import annotations

import os
import queue
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]

from .client import DEFAULT_URL, KinesisClient, KinesisClientError, action_name, job_body, track_name
from .nla import ImportFailed, import_repair

ADDON = __package__ or "kinesis_addon"
POLL_S = 2.0
# AWAITING_DECISION too: the animator decides in the review client, and the panel must notice.
ACTIVE = {"PENDING", "RUNNING", "AWAITING_DECISION", "APPLYING"}

_results: queue.Queue[Callable[[], None]] = queue.Queue()


def _client() -> KinesisClient:
    addon = bpy.context.preferences.addons.get(ADDON)
    url = addon.preferences.server_url if addon else os.environ.get("KINESIS_URL", DEFAULT_URL)
    return KinesisClient(url)


def _props() -> Any:
    return bpy.context.scene.kinesis


def _redraw() -> None:
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _background(work: Callable[[], Callable[[], None]]) -> None:
    """Run ``work`` on a thread; the callback it returns runs later on the main thread."""

    def run() -> None:
        try:
            _results.put(work())
        except KinesisClientError as exc:
            message = str(exc)
            _results.put(lambda: _fail(message))
        except Exception as exc:  # noqa: BLE001 - surfaced in the panel
            message = f"{type(exc).__name__}: {exc}"
            _results.put(lambda: _fail(message))

    threading.Thread(target=run, daemon=True).start()


def _fail(message: str) -> None:
    props = _props()
    props.busy = False
    props.message = message[:500]


def _drain() -> float:
    """Main-thread timer: apply worker results, and poll an active job."""
    while True:
        try:
            _results.get_nowait()()
        except queue.Empty:
            break
    props = _props()
    if props.job_id and props.status in ACTIVE and not props.busy and props.next_poll <= time.time():
        props.busy = True
        props.next_poll = time.time() + POLL_S
        client, job_id = _client(), props.job_id
        _background(lambda: _apply_job(client.get_job(job_id)))
    _redraw()
    return 0.5


def _apply_job(job: dict[str, Any]) -> Callable[[], None]:
    def apply() -> None:
        props = _props()
        props.busy = False
        props.status = job["status"]
        evaluation = job.get("evaluation") or {}
        props.recommended = evaluation.get("recommended") or ""
        decision = job.get("decision") or {}
        props.decision = decision.get("choice") or ""
        output = job.get("output") or {}
        props.output_uri = output.get("uri") or ""
        if job["status"] == "AWAITING_DECISION":
            props.message = "Ready: compare Original / A / B in the Kinesis review client and choose."
        elif job["status"] == "COMPLETED" and props.output_uri:
            props.message = f"Repair {props.decision} exported. Import it to add it as an NLA layer."
        elif job["status"] in ("FAILED", "CANCELLED", "REJECTED"):
            error = job.get("error") or {}
            props.message = f"{job['status']}: {error.get('message') or 'no repair to import'}"
        else:
            props.message = f"{job['status']}..."

    return apply


class KinesisPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON

    server_url: bpy.props.StringProperty(name="Kinesis server", default=DEFAULT_URL)  # type: ignore[valid-type]

    def draw(self, context: Any) -> None:
        self.layout.prop(self, "server_url")


class KinesisSceneProps(bpy.types.PropertyGroup):
    instruction: bpy.props.StringProperty(name="Instruction", description="Optional style hint, e.g. keep the heel planted")  # type: ignore[valid-type]
    use_preview_range: bpy.props.BoolProperty(name="Use preview range", default=True)  # type: ignore[valid-type]
    job_id: bpy.props.StringProperty()  # type: ignore[valid-type]
    status: bpy.props.StringProperty()  # type: ignore[valid-type]
    recommended: bpy.props.StringProperty()  # type: ignore[valid-type]
    decision: bpy.props.StringProperty()  # type: ignore[valid-type]
    output_uri: bpy.props.StringProperty()  # type: ignore[valid-type]
    message: bpy.props.StringProperty()  # type: ignore[valid-type]
    busy: bpy.props.BoolProperty()  # type: ignore[valid-type]
    next_poll: bpy.props.FloatProperty()  # type: ignore[valid-type]


def frame_range(scene: Any, use_preview: bool) -> tuple[int, int]:
    if use_preview and scene.use_preview_range:
        return scene.frame_preview_start, scene.frame_preview_end
    return scene.frame_start, scene.frame_end


class KINESIS_OT_submit(bpy.types.Operator):
    """Save a copy of this file, upload it and start a repair of the selected foot"""

    bl_idname = "kinesis.submit"
    bl_label = "Measure & Repair"

    @classmethod
    def poll(cls, context: Any) -> bool:
        obj = context.active_object
        return bool(obj and obj.type == "ARMATURE" and context.mode == "POSE" and not _props().busy)

    def execute(self, context: Any) -> set[str]:
        obj = context.active_object
        props = _props()
        bones = [pb.name for pb in (context.selected_pose_bones or []) if pb.id_data == obj]
        start, end = frame_range(context.scene, props.use_preview_range)
        try:
            job_body("scn_check", obj.name, bones, start, end)  # validate before uploading
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        copy = Path(tempfile.mkdtemp(prefix="kinesis-")) / "scene.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(copy), copy=True, compress=False)
        client, armature, instruction = _client(), obj.name, props.instruction
        key = f"blender-{uuid.uuid4().hex}"
        props.busy, props.job_id, props.status, props.output_uri = True, "", "", ""
        props.message = "Uploading..."

        def work() -> Callable[[], None]:
            scene = client.upload_scene(copy)
            job = client.create_job(
                job_body(scene["scene_id"], armature, bones, start, end, instruction), key
            )
            copy.unlink(missing_ok=True)

            def started() -> None:
                props = _props()
                props.job_id = job["job_id"]
                props.next_poll = 0.0
                _apply_job(job)()

            return started

        _background(work)
        return {"FINISHED"}


class KINESIS_OT_import(bpy.types.Operator):
    """Download the chosen repair and add it to the armature as a Kinesis NLA layer"""

    bl_idname = "kinesis.import_repair"
    bl_label = "Import Selected Repair"

    @classmethod
    def poll(cls, context: Any) -> bool:
        props = _props()
        obj = context.active_object
        return bool(
            props.status == "COMPLETED" and props.output_uri and props.decision
            and obj and obj.type == "ARMATURE" and not props.busy
        )

    def execute(self, context: Any) -> set[str]:
        props = _props()
        client, uri = _client(), props.output_uri
        job_id, label, armature = props.job_id, props.decision, context.active_object.name
        dest = Path(tempfile.mkdtemp(prefix="kinesis-")) / "repaired.blend"
        props.busy = True
        props.message = "Downloading the repair..."

        def work() -> Callable[[], None]:
            client.download(uri, dest)

            def finish() -> None:
                props = _props()
                props.busy = False
                obj = bpy.data.objects.get(armature)
                try:
                    import_repair(obj, dest, action_name(job_id, label), track_name(label))
                except ImportFailed as exc:
                    props.message = str(exc)
                    return
                props.message = f"Added {track_name(label)}. Mute that track to compare with the original."

            return finish

        _background(work)
        return {"FINISHED"}


class KINESIS_PT_panel(bpy.types.Panel):
    bl_label = "Kinesis"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Kinesis"

    def draw(self, context: Any) -> None:
        layout, props = self.layout, _props()
        obj = context.active_object
        col = layout.column()
        if obj is None or obj.type != "ARMATURE":
            col.label(text="Select an armature", icon="INFO")
        else:
            bones = [pb.name for pb in (context.selected_pose_bones or []) if pb.id_data == obj]
            col.label(text=f"Rig: {obj.name}", icon="ARMATURE_DATA")
            col.label(text=f"Foot: {', '.join(bones) or '(select in pose mode)'}", icon="BONE_DATA")
            start, end = frame_range(context.scene, props.use_preview_range)
            col.label(text=f"Frames: {start}-{end}", icon="TIME")
        col.prop(props, "use_preview_range")
        col.prop(props, "instruction", text="")
        col.operator(KINESIS_OT_submit.bl_idname, icon="PLAY")
        if props.job_id:
            box = layout.box()
            box.label(text=f"Job {props.job_id[-8:]}: {props.status or '...'}")
            if props.recommended:
                box.label(text=f"Recommended: {props.recommended}")
            if props.decision:
                box.label(text=f"Chosen: {props.decision}")
            box.operator(KINESIS_OT_import.bl_idname, icon="IMPORT")
        if props.message:
            for line in _wrap(props.message, 40):
                layout.label(text=line)


def _wrap(text: str, width: int) -> list[str]:
    lines, current = [], ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    return [*lines, current] if current else lines


CLASSES = (KinesisPreferences, KinesisSceneProps, KINESIS_OT_submit, KINESIS_OT_import, KINESIS_PT_panel)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.kinesis = bpy.props.PointerProperty(type=KinesisSceneProps)
    if not bpy.app.background:
        bpy.app.timers.register(_drain, first_interval=0.5, persistent=True)


def unregister() -> None:
    if bpy.app.timers.is_registered(_drain):
        bpy.app.timers.unregister(_drain)
    del bpy.types.Scene.kinesis
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)

"""The add-on end to end in headless Blender against a real Kinesis server.

Blender opens the fixture as the animator's file, selects ``foot.L`` in pose mode and presses
**Measure & Repair**; a real server (uvicorn + host Blender worker, no AI) runs the job; the
decision is made through the API, as the review client would; then **Import Selected Repair**
adds the chosen candidate as a ``Kinesis:A`` NLA track in the open file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from test_addon_client import _serve

from kinesis.jobs.local import LocalJobRunner

pytestmark = [
    pytest.mark.blender,
    pytest.mark.skipif(not os.environ.get("KINESIS_BLENDER_BIN"), reason="KINESIS_BLENDER_BIN"),
]

REPO = Path(__file__).resolve().parents[2]
FIXTURE_BLEND = REPO / "blender" / "fixtures" / "foot_slide_v1.blend"

SCRIPT = """
import bpy, json, os, sys, time
sys.path.insert(0, sys.argv[sys.argv.index("--") + 1])
import kinesis_addon
from kinesis_addon import ui
from kinesis_addon.client import KinesisClient
kinesis_addon.register()
sc = bpy.context.scene
props = sc.kinesis
rig = bpy.data.objects["Rig"]
bpy.context.view_layer.objects.active = rig
rig.select_set(True)
bpy.ops.object.mode_set(mode="POSE")
for bone in rig.data.bones:
    bone.select = bone.name == "foot.L"
sc.use_preview_range = True
sc.frame_preview_start, sc.frame_preview_end = 45, 90

def pump(done, what, timeout=300):
    t0 = time.time()
    while not done():
        ui._drain()
        time.sleep(0.2)
        if time.time() - t0 > timeout:
            raise SystemExit(f"timeout waiting for {what}: {props.status} {props.message}")

assert bpy.ops.kinesis.submit() == {"FINISHED"}
pump(lambda: props.status == "AWAITING_DECISION", "the A/B candidates")
seen = {"recommended": props.recommended, "message": props.message}
KinesisClient(os.environ["KINESIS_URL"]).decide(props.job_id, "A")
pump(lambda: props.status == "COMPLETED" and props.output_uri, "the export")
assert bpy.ops.kinesis.import_repair() == {"FINISHED"}
pump(lambda: not props.busy and not props.message.startswith("Downloading"), "the import")
ad = rig.animation_data
seen.update(
    final=props.message, decision=props.decision,
    tracks=[t.name for t in ad.nla_tracks], active=ad.action.name if ad.action else None,
)
print("E2E" + json.dumps(seen))
"""


def test_measure_repair_decide_import(make_service: Any, tmp_path: Path) -> None:
    blender = Path(os.environ["KINESIS_BLENDER_BIN"])
    service = make_service(runner=LocalJobRunner(blender))
    session = tmp_path / "animator.blend"
    shutil.copyfile(FIXTURE_BLEND, session)
    for url in _serve(service):
        proc = subprocess.run(  # noqa: S603 - fixed argv, test-only
            [
                str(blender),
                *("--background", "--factory-startup", "-noaudio", str(session)),
                *("--python-exit-code", "3", "--python-expr", SCRIPT, "--"),
                str(REPO / "blender" / "addon"),
            ],
            capture_output=True,
            timeout=600,
            check=False,
            env={**os.environ, "KINESIS_URL": url},
        )
        text = proc.stdout.decode(errors="replace")
        assert proc.returncode == 0, text[-3000:] + proc.stderr.decode(errors="replace")[-2000:]
        out = json.loads(next(x for x in text.splitlines() if x.startswith("E2E"))[3:])
        assert out["recommended"] in ("A", "B")
        assert "review client" in out["message"]
        assert out["decision"] == "A"
        assert out["final"].startswith("Added Kinesis:A"), out["final"]
        assert out["tracks"] == ["Kinesis:Original", "Kinesis:A"]
        assert out["active"] is None

"""Kinesis Blender add-on: View3D > Sidebar > Kinesis.

- **Measure & Repair** saves a copy of the open file, uploads it to the Kinesis server and
  starts a repair of the selected foot bone over the preview range (or the scene range). The
  panel follows the job; the animator compares Original / A / B in the review client.
- **Import Selected Repair** downloads the exported repair and adds the chosen candidate as a
  ``Kinesis:<label>`` NLA track above the original (``nla.py``). It never edits the active
  action.

The add-on does no repair math and makes no AI calls (ADR 0003). ``client.py`` is standard
library only, so it also runs (and is tested) outside Blender.
"""

bl_info = {
    "name": "Kinesis",
    "author": "Kinesis contributors",
    "version": (0, 2, 0),
    "blender": (4, 5, 0),
    "location": "View3D > Sidebar > Kinesis",
    "description": "Localized animation repair: measure, propose, compare, apply",
    "category": "Animation",
}


def register() -> None:
    from . import ui

    ui.register()


def unregister() -> None:
    from . import ui

    ui.unregister()

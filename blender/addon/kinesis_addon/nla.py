"""Import a chosen repair into the animator's open session (concern C4, ADR 0004).

The repair arrives as the output .blend that Kinesis exported. Its candidate Action is
appended into the current file and layered exactly as the worker layers it:

- the animator's action moves to a bottom ``Kinesis:Original`` strip (Replace, Hold), and the
  active action is cleared, because in Blender 4.5 the active action evaluates *above* the
  NLA stack (spike S1);
- the candidate goes on a ``Kinesis:<label>`` track above it (Replace, Nothing), so it only
  overrides the repaired bones inside the repaired frames.

Only AnimData references change. The animator's Action datablock is never edited, and muting
or deleting the Kinesis track restores the original motion exactly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import bpy  # type: ignore[import-not-found]

ORIGINAL_TRACK = "Kinesis:Original"


class ImportFailed(Exception):
    pass


def _original_action(obj: Any) -> Any:
    ad = obj.animation_data
    if ad is None:
        raise ImportFailed(f"{obj.name} has no animation")
    if ad.action is not None:
        return ad.action
    track = ad.nla_tracks.get(ORIGINAL_TRACK)
    if track is not None and len(track.strips):
        return track.strips[0].action
    raise ImportFailed(f"{obj.name} has no active action")


def push_original_down(obj: Any) -> Any:
    ad = obj.animation_data
    original = _original_action(obj)
    if ad.nla_tracks.get(ORIGINAL_TRACK) is None:
        slot = getattr(ad, "action_slot", None)
        track = ad.nla_tracks.new()
        track.name = ORIGINAL_TRACK
        strip = track.strips.new("ORIGINAL", int(original.frame_range[0]), original)
        strip.blend_type = "REPLACE"
        strip.extrapolation = "HOLD"
        if slot is not None and hasattr(strip, "action_slot"):
            strip.action_slot = slot
        ad.action = None
    return original


def append_action(blend: Path, name: str) -> Any:
    """Append one Action by name from ``blend``. A clash with an existing Action is an error,
    so a repair is never imported twice by accident."""
    if name in bpy.data.actions:
        raise ImportFailed(f"action {name!r} is already in this file")
    with bpy.data.libraries.load(str(blend), link=False) as (src, dst):
        if name not in src.actions:
            raise ImportFailed(f"{blend.name} has no action {name!r}")
        dst.actions = [name]
    action = bpy.data.actions.get(name)
    if action is None:
        raise ImportFailed(f"appending {name!r} failed")
    return action


def import_repair(obj: Any, blend: Path, action: str, track: str) -> Any:
    """Append ``action`` from ``blend`` and layer it on ``obj`` as ``track``."""
    if obj is None or obj.type != "ARMATURE":
        raise ImportFailed("select the armature the repair was made for")
    if obj.animation_data is not None and obj.animation_data.nla_tracks.get(track) is not None:
        raise ImportFailed(f"{obj.name} already has a {track!r} track")
    _original_action(obj)  # fail before appending anything
    candidate = append_action(blend, action)
    push_original_down(obj)
    nla_track = obj.animation_data.nla_tracks.new()
    nla_track.name = track
    strip = nla_track.strips.new(candidate.name, int(candidate.frame_range[0]), candidate)
    strip.blend_type = "REPLACE"
    strip.influence = 1.0
    strip.extrapolation = "NOTHING"
    strip.blend_in = strip.blend_out = 0.0
    if hasattr(strip, "action_slot") and len(candidate.slots):
        strip.action_slot = candidate.slots[0]
    return nla_track


__all__ = ["ORIGINAL_TRACK", "ImportFailed", "append_action", "import_repair", "push_original_down"]

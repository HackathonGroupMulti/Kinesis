"""The task runner's Java check for the Kotlin client (Gradle 9 needs Java 17+ to start)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import tasks  # noqa: E402


@pytest.mark.parametrize(
    ("output", "major"),
    [
        ('java version "1.8.0_503"\nJava(TM) SE Runtime Environment (build 1.8.0_503-b09)', 8),
        ('openjdk version "17.0.12" 2024-07-16\nOpenJDK Runtime Environment', 17),
        ('openjdk version "21.0.12.1" 2026-08-18 LTS', 21),
        ('openjdk version "25-ea" 2025-09-16', 25),
        ('Picked up JAVA_TOOL_OPTIONS: -Dfile.encoding=UTF-8\nopenjdk version "21.0.1"', 21),
        ("Error: could not find java.dll", None),
        ("", None),
    ],
)
def test_parse_java_major(output: str, major: int | None) -> None:
    assert tasks.parse_java_major(output) == major


@pytest.mark.parametrize(
    ("found", "blocking"),
    [
        ((None, None), True),  # no java at all
        (("java", 8), True),  # known too old
        (("java", None), False),  # unrecognised: reported, left for Gradle to judge
        (("java", 17), False),
        (("java", 21), False),
    ],
)
def test_java_report_blocks_only_on_known_failures(
    monkeypatch: pytest.MonkeyPatch, found: tuple[str | None, int | None], blocking: bool
) -> None:
    monkeypatch.setattr(tasks, "gradle_java", lambda: found)
    message, blocks = tasks.java_report()
    assert blocks is blocking
    assert message

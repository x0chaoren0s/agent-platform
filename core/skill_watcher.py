"""
Watchdog-based SKILL.md change detector.

Monitors system-level, project-level, and extra skill root directories
for SKILL.md file modifications and deletions.  Calls back with the skill
name and event type so active agent sessions can be notified.

Usage (in main.py):
    watcher = SkillWatcher(roots, callback)
    watcher.start()
    ...
    watcher.stop()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

_EVENT_MODIFIED = "modified"
_EVENT_DELETED = "deleted"


class _SkillChangeHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[str, str], None]) -> None:
        self._callback = callback

    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.name == "SKILL.md":
            skill_name = path.parent.name
            logger.info("SKILL.md modified: %s (%s)", skill_name, path)
            self._callback(skill_name, _EVENT_MODIFIED)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.name == "SKILL.md":
            skill_name = path.parent.name
            logger.info("SKILL.md deleted: %s (%s)", skill_name, path)
            self._callback(skill_name, _EVENT_DELETED)


class SkillWatcher:
    """Watch multiple skill root directories for SKILL.md changes."""

    def __init__(
        self, roots: list[Path], callback: Callable[[str, str], None]
    ) -> None:
        self._observer = Observer()
        handler = _SkillChangeHandler(callback)
        for root in roots:
            if root.exists():
                self._observer.schedule(handler, str(root), recursive=True)
                logger.info("Watching skills dir: %s", root)
            else:
                logger.debug("Skipping non-existent skills dir: %s", root)

    def start(self) -> None:
        self._observer.start()

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()

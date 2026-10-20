"""File helpers: safe output names and watching a folder for new exports."""

from __future__ import annotations

import os
import re
from pathlib import Path


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w\- .]", "_", name).strip(" .")
    return cleaned or "sample"


def unique_path(folder: Path, stem: str, suffix: str, keep: Path | None = None) -> Path:
    """Return folder/stem+suffix, or "stem 2", "stem 3", ... if that file exists.

    `keep` is a path we already own; it is returned as-is if it is the first choice.
    """
    candidate = folder / f"{stem}{suffix}"
    n = 2
    while candidate.exists() and candidate != keep:
        candidate = folder / f"{stem} {n}{suffix}"
        n += 1
    return candidate


class FolderScanner:
    """Reports files in a folder that are new or changed since the last poll.

    Files present when the scanner is created are ignored. A file is only
    reported once its size and modification time are the same on two polls in
    a row, so files still being written aren't picked up half-finished.
    """

    def __init__(self, folder: Path, suffix: str = ".y3s"):
        self.folder = Path(folder)
        self.suffix = suffix.lower()
        self._seen = self._snapshot()
        self._pending: dict[Path, tuple[int, int]] = {}

    def _snapshot(self) -> dict[Path, tuple[int, int]]:
        found = {}
        try:
            entries = list(os.scandir(self.folder))
        except OSError:
            return found
        for entry in entries:
            if not entry.name.lower().endswith(self.suffix):
                continue
            try:
                if entry.is_file():
                    st = entry.stat()
                    found[Path(entry.path)] = (st.st_size, st.st_mtime_ns)
            except OSError:
                continue
        return found

    def poll(self) -> list[Path]:
        current = self._snapshot()
        ready = []
        for path, sig in current.items():
            if self._seen.get(path) == sig:
                continue
            if self._pending.get(path) == sig:
                ready.append(path)
                self._seen[path] = sig
                del self._pending[path]
            else:
                self._pending[path] = sig
        self._seen = {p: s for p, s in self._seen.items() if p in current}
        self._pending = {p: s for p, s in self._pending.items() if p in current}
        return sorted(ready)

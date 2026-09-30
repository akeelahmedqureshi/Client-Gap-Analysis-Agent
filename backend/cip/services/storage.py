"""Object storage abstraction (local filesystem implementation)."""

from __future__ import annotations

import re
from pathlib import Path

from cip.config import get_settings

_SAFE = re.compile(r"^[A-Za-z0-9_\-./]+$")


class LocalObjectStore:
    def __init__(self, base_dir: str | None = None) -> None:
        self.base = Path(base_dir or get_settings().storage_dir).resolve()

    def _path(self, key: str) -> Path:
        if not _SAFE.match(key) or ".." in key:
            raise ValueError(f"Invalid storage key: {key}")
        p = (self.base / key).resolve()
        if self.base not in p.parents:
            raise ValueError(f"Invalid storage key: {key}")
        return p

    def put(self, key: str, data: bytes) -> str:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

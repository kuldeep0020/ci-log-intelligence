from __future__ import annotations

import tempfile
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Iterator


class StorageBackend(ABC):
    name: str

    @abstractmethod
    def write_text(self, content: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def iter_lines(self, reference: str) -> Iterator[str]:
        raise NotImplementedError

    @abstractmethod
    def delete(self, reference: str) -> None:
        raise NotImplementedError


class InMemoryStorage(StorageBackend):
    name = "memory"

    def __init__(self) -> None:
        self._store: Dict[str, str] = {}

    def write_text(self, content: str) -> str:
        reference = str(uuid.uuid4())
        self._store[reference] = content
        return reference

    def iter_lines(self, reference: str) -> Iterator[str]:
        content = self._store[reference]
        for line in content.splitlines():
            yield line

    def delete(self, reference: str) -> None:
        self._store.pop(reference, None)


class DiskSpillStorage(StorageBackend):
    name = "disk"

    def __init__(self) -> None:
        self._paths: Dict[str, Path] = {}

    def write_text(self, content: str) -> str:
        handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        try:
            handle.write(content)
            handle.flush()
            reference = str(uuid.uuid4())
            self._paths[reference] = Path(handle.name)
            return reference
        finally:
            handle.close()

    def iter_lines(self, reference: str) -> Iterator[str]:
        path = self._paths[reference]
        with path.open("r", encoding="utf-8") as fh:
            for raw_line in fh:
                yield raw_line.rstrip("\n")

    def delete(self, reference: str) -> None:
        path = self._paths.pop(reference, None)
        if path and path.exists():
            path.unlink()


def create_storage_backend(byte_size: int, spill_threshold_bytes: int) -> StorageBackend:
    if byte_size >= spill_threshold_bytes:
        return DiskSpillStorage()
    return InMemoryStorage()

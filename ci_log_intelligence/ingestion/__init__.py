from __future__ import annotations

from ..models import StoredLog
from ..storage import StorageBackend


def ingest_log(log: str, storage_backend: StorageBackend) -> StoredLog:
    reference = storage_backend.write_text(log)
    return StoredLog(
        reference=reference,
        byte_size=len(log.encode("utf-8")),
        backend_name=storage_backend.name,
    )

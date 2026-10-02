from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import time
from typing import Any, List, Optional, Tuple, Union

try:
    import aiosqlite
except ImportError:  # pragma: no cover
    aiosqlite = None  # type: ignore[assignment]

from fastapi_idempotency_key.backends.base import BaseIdempotencyBackend
from fastapi_idempotency_key.exceptions import IdempotencyStorageError
from fastapi_idempotency_key.status import IdempotencyRecord, IdempotencyStatus


class SQLiteBackend(BaseIdempotencyBackend):
    """SQLite-backed idempotency storage using aiosqlite."""

    def __init__(
        self,
        db_path: Union[str, Path] = ":memory:",
        table_name: str = "idempotency_records",
        *,
        database_path: Optional[Union[str, Path]] = None,
    ) -> None:
        if aiosqlite is None:
            raise ImportError(
                "The 'aiosqlite' package is required to use SQLiteBackend. "
                "Install it with: pip install 'fastapi-idempotency-key[sqlite]'"
            )
        resolved_path = database_path if database_path is not None else db_path
        self.db_path = str(resolved_path)
        self.table_name = table_name
        self._conn: Optional[aiosqlite.Connection] = None
        self._lock = asyncio.Lock()

    async def _get_conn(self) -> aiosqlite.Connection:
        """Returns or opens the persistent SQLite connection."""
        if self._conn is None:
            try:
                self._conn = await aiosqlite.connect(self.db_path)
                self._conn.row_factory = aiosqlite.Row
                if self.db_path != ":memory:":
                    await self._conn.execute("PRAGMA journal_mode=WAL")
                await self._init_db(self._conn)
            except Exception as exc:
                raise IdempotencyStorageError(
                    f"Failed to connect to SQLite: {exc}"
                ) from exc
        return self._conn

    async def _init_db(self, conn: aiosqlite.Connection) -> None:
        """Initializes table and indexes if they do not exist."""
        sql = f"""
        CREATE TABLE IF NOT EXISTS {self.table_name} (
            key TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            status TEXT NOT NULL,
            status_code INTEGER,
            headers TEXT NOT NULL,
            body BLOB NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_{self.table_name}_expires_at ON {self.table_name}(expires_at);
        """
        await conn.executescript(sql)
        await conn.commit()

    def _row_to_record(self, row: Any) -> IdempotencyRecord:
        """Converts an aiosqlite Row to an IdempotencyRecord."""
        raw_headers = json.loads(row["headers"])
        headers = [(str(k), str(v)) for k, v in raw_headers]
        body = row["body"]
        if not isinstance(body, bytes):
            body = bytes(body)

        return IdempotencyRecord(
            key=str(row["key"]),
            fingerprint=str(row["fingerprint"]),
            status=IdempotencyStatus(row["status"]),
            status_code=row["status_code"],
            headers=headers,
            body=body,
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
        )

    async def _try_lock_once(
        self, key: str, fingerprint: str, ttl: int
    ) -> Tuple[bool, Optional[IdempotencyRecord]]:
        now = time.time()
        expires_at = now + ttl if ttl > 0 else 0.0

        async with self._lock:
            conn = await self._get_conn()
            # Clean up expired record for this key if present
            await conn.execute(
                f"DELETE FROM {self.table_name} WHERE key = ? AND expires_at > 0.0 AND expires_at <= ?",
                (key, now),
            )
            await conn.commit()

            try:
                await conn.execute(
                    f"INSERT INTO {self.table_name} "
                    f"(key, fingerprint, status, status_code, headers, body, created_at, expires_at) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        key,
                        fingerprint,
                        IdempotencyStatus.IN_PROGRESS.value,
                        None,
                        "[]",
                        b"",
                        now,
                        expires_at,
                    ),
                )
                await conn.commit()
                return True, None
            except sqlite3.IntegrityError:
                # Key already exists
                cursor = await conn.execute(
                    f"SELECT key, fingerprint, status, status_code, headers, body, created_at, expires_at "
                    f"FROM {self.table_name} WHERE key = ?",
                    (key,),
                )
                row = await cursor.fetchone()
                if row is None:
                    # Key was deleted between insert and select, retry
                    return await self._try_lock_once(key, fingerprint, ttl)

                record = self._row_to_record(row)
                if record.is_expired():
                    await conn.execute(
                        f"DELETE FROM {self.table_name} WHERE key = ?", (key,)
                    )
                    await conn.commit()
                    return await self._try_lock_once(key, fingerprint, ttl)

                return False, record

    async def store_response(
        self,
        key: str,
        status_code: int,
        headers: List[Tuple[str, str]],
        body: bytes,
        ttl: int,
    ) -> None:
        now = time.time()
        expires_at = now + ttl if ttl > 0 else 0.0
        headers_json = json.dumps(headers)

        async with self._lock:
            conn = await self._get_conn()
            await conn.execute(
                f"UPDATE {self.table_name} "
                f"SET status = ?, status_code = ?, headers = ?, body = ?, expires_at = ? "
                f"WHERE key = ?",
                (
                    IdempotencyStatus.COMPLETED.value,
                    status_code,
                    headers_json,
                    body,
                    expires_at,
                    key,
                ),
            )
            await conn.commit()

    async def release_lock(self, key: str) -> None:
        async with self._lock:
            conn = await self._get_conn()
            await conn.execute(f"DELETE FROM {self.table_name} WHERE key = ?", (key,))
            await conn.commit()

    async def get_record(self, key: str) -> Optional[IdempotencyRecord]:
        async with self._lock:
            conn = await self._get_conn()
            cursor = await conn.execute(
                f"SELECT key, fingerprint, status, status_code, headers, body, created_at, expires_at "
                f"FROM {self.table_name} WHERE key = ?",
                (key,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None

            record = self._row_to_record(row)
            if record.is_expired():
                await conn.execute(
                    f"DELETE FROM {self.table_name} WHERE key = ?", (key,)
                )
                await conn.commit()
                return None
            return record

    async def clear(self) -> None:
        async with self._lock:
            conn = await self._get_conn()
            await conn.execute(f"DELETE FROM {self.table_name}")
            await conn.commit()

    async def close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await self._conn.close()
                self._conn = None

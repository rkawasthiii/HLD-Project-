"""Durable primary data store backed by SQLite.

SQLite is a real, file-based database in the Python standard library, so the
project runs locally with no server to install. It holds the authoritative
query -> count mapping. The in-memory Trie is rebuilt from this table at startup.

The batch writer is the ONLY component that writes here, and it does so in
aggregated transactions (one transaction per flush, not per search) which is the
whole point of the batching requirement. Every physical read/write is counted so
we can report DB write reduction.
"""
import sqlite3
import time
from threading import Lock
from typing import Dict, Iterable, List, Tuple

from . import config


class PrimaryStore:
    def __init__(self, db_path=config.DB_FILE):
        self.db_path = str(db_path)
        self._lock = Lock()
        # check_same_thread=False: we guard every access with our own lock.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS query_counts (
                query TEXT PRIMARY KEY,
                count INTEGER NOT NULL,
                last_searched REAL
            )
            """
        )
        self._conn.commit()
        # observability counters
        self.read_ops = 0     # number of physical read statements
        self.write_ops = 0    # number of physical write transactions (flushes)
        self.rows_written = 0  # number of rows upserted across all flushes

    def is_empty(self) -> bool:
        with self._lock:
            self.read_ops += 1
            cur = self._conn.execute("SELECT 1 FROM query_counts LIMIT 1")
            return cur.fetchone() is None

    def count_rows(self) -> int:
        with self._lock:
            self.read_ops += 1
            cur = self._conn.execute("SELECT COUNT(*) FROM query_counts")
            return int(cur.fetchone()[0])

    def load_all(self) -> List[Tuple[str, int]]:
        """Read every (query, count) row — used to build the Trie at startup."""
        with self._lock:
            self.read_ops += 1
            cur = self._conn.execute("SELECT query, count FROM query_counts")
            return cur.fetchall()

    def bulk_insert(self, rows: Iterable[Tuple[str, int]]) -> int:
        """Initial dataset ingestion. Returns number of rows inserted."""
        now = time.time()
        data = [(q, c, now) for q, c in rows]
        with self._lock:
            self.write_ops += 1
            self.rows_written += len(data)
            self._conn.executemany(
                "INSERT OR REPLACE INTO query_counts(query, count, last_searched) "
                "VALUES (?, ?, ?)",
                data,
            )
            self._conn.commit()
        return len(data)

    def flush_deltas(self, deltas: Dict[str, int]) -> List[Tuple[str, int]]:
        """Apply aggregated count increments in a SINGLE transaction.

        Returns the list of (query, new_count) so callers can refresh the Trie.
        This is one physical write transaction regardless of how many individual
        search submissions were aggregated into `deltas`.
        """
        if not deltas:
            return []
        now = time.time()
        items = list(deltas.items())
        with self._lock:
            self.write_ops += 1
            self.rows_written += len(items)
            # upsert: increment existing, insert new
            self._conn.executemany(
                """
                INSERT INTO query_counts(query, count, last_searched)
                VALUES (?, ?, ?)
                ON CONFLICT(query) DO UPDATE SET
                    count = count + excluded.count,
                    last_searched = excluded.last_searched
                """,
                [(q, d, now) for q, d in items],
            )
            self._conn.commit()
            # read back the authoritative new counts for the affected queries
            self.read_ops += 1
            placeholders = ",".join("?" for _ in items)
            cur = self._conn.execute(
                f"SELECT query, count FROM query_counts WHERE query IN ({placeholders})",
                [q for q, _ in items],
            )
            return cur.fetchall()

    def stats(self) -> Dict[str, int]:
        return {
            "read_ops": self.read_ops,
            "write_ops": self.write_ops,
            "rows_written": self.rows_written,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()

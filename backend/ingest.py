"""Dataset ingestion.

Reads a "query<TAB>count" (or "query,count") text file and loads it into the
SQLite primary store, then the Trie is built from the store. Runs once at startup
if the database is empty; re-running is idempotent (INSERT OR REPLACE).
"""
import time
from pathlib import Path
from typing import Iterator, Tuple

from . import config


def parse_dataset(path: Path) -> Iterator[Tuple[str, int]]:
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if "\t" in line:
                query, _, count = line.partition("\t")
            elif "," in line:
                query, _, count = line.rpartition(",")
            else:
                continue
            query = query.strip().lower()
            try:
                c = int(count.strip())
            except ValueError:
                continue
            if query:
                yield query, c


def ingest_if_needed(store, dataset_file: Path = config.DATASET_FILE) -> int:
    """Load the dataset into the store if it's empty. Returns rows ingested."""
    if not store.is_empty():
        return 0
    if not dataset_file.exists():
        raise FileNotFoundError(
            f"Dataset not found at {dataset_file}. "
            f"Run: python scripts/generate_dataset.py"
        )
    print(f"[ingest] loading dataset from {dataset_file} ...")
    t0 = time.time()
    total = 0
    batch = []
    for row in parse_dataset(dataset_file):
        batch.append(row)
        if len(batch) >= 10_000:
            total += store.bulk_insert(batch)
            batch = []
    if batch:
        total += store.bulk_insert(batch)
    print(f"[ingest] inserted {total:,} rows in {time.time() - t0:.2f}s")
    return total


def build_trie(store, trie) -> int:
    """Populate the Trie from every row in the store. Returns rows loaded."""
    print("[ingest] building trie from primary store ...")
    t0 = time.time()
    rows = store.load_all()
    trie.bulk_load(rows)
    print(f"[ingest] trie built with {len(rows):,} queries in "
          f"{time.time() - t0:.2f}s")
    return len(rows)

"""Central configuration for the Search Typeahead system.

Every tunable knob lives here so it is easy to explain and adjust in a viva.
All values are plain Python so the project runs with zero dependencies.
"""
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
FRONTEND_DIR = ROOT_DIR / "frontend"
DATASET_FILE = DATA_DIR / "queries.txt"     # "query<TAB>count" per line
DB_FILE = DATA_DIR / "typeahead.db"          # durable primary store (SQLite)

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
HOST = "127.0.0.1"
PORT = 8000

# ---------------------------------------------------------------------------
# Suggestions / Trie
# ---------------------------------------------------------------------------
SUGGEST_LIMIT = 10        # max suggestions returned to the UI
TRIE_CAP = 10             # top-K cached per trie node (== SUGGEST_LIMIT)

# ---------------------------------------------------------------------------
# Distributed cache + consistent hashing
# ---------------------------------------------------------------------------
CACHE_NODES = ["cache-a", "cache-b", "cache-c", "cache-d"]  # logical cache nodes
VNODES_PER_NODE = 150     # virtual nodes per physical node -> smooth distribution
CACHE_TTL_SECONDS = 30.0  # suggestion results expire after this long
CACHE_MAX_KEYS = 50_000   # per-node soft cap (simple LRU-ish eviction)

# ---------------------------------------------------------------------------
# Batch writes
# ---------------------------------------------------------------------------
BATCH_MAX_SIZE = 500      # flush when this many buffered submissions accumulate
BATCH_FLUSH_INTERVAL = 2.0  # ...or at least every N seconds (whichever first)

# ---------------------------------------------------------------------------
# Trending (recency-aware ranking)
# ---------------------------------------------------------------------------
# trending_score = count + RECENCY_WEIGHT * decayed_recent_activity
# `recent` is an exponential moving sum that decays with time constant TAU,
# so a short-lived popularity spike fades automatically (no permanent boost).
RECENCY_TAU_SECONDS = 300.0   # ~5 min decay constant
RECENCY_WEIGHT = 20_000.0     # how strongly recent activity can lift a query
RECENCY_PRUNE_EPS = 0.01      # drop hot-set entries once decayed below this

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
LATENCY_WINDOW = 2000     # number of recent latency samples kept per endpoint

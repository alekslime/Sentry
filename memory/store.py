"""SQLite-backed conversation history (Milestone 9, Part A).

`ConversationStore` persists each query/response turn to a local SQLite
database so a record of past conversations survives across app restarts.

Scope note: this is storage only. Nothing here feeds past turns back into
the LLM prompt -- that's Milestone 9, Part B (retrieval for follow-up
context), a separate piece of work. `get_recent_turns` exists for
inspection/testing, not for prompt construction.

Threading: `save_turn`/`get_recent_turns` each open and close their own
short-lived `sqlite3` connection rather than sharing one across threads.
`main.py` calls `save_turn` from the same worker thread that runs LLM
generation (a new thread per query) -- a shared connection would need
either `check_same_thread=False` plus a lock, or one connection per
thread. A connection per call is simpler and safe: SQLite handles
concurrent short connections to the same file via its own locking, and
turn volume is far too low for per-call connection overhead to matter.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import TypedDict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    query TEXT NOT NULL,
    response TEXT NOT NULL
);
"""


class StoredTurn(TypedDict):
    """One row of the `turns` table, as returned by `get_recent_turns`."""

    id: int
    timestamp: float
    query: str
    response: str


class ConversationStore:
    """Persists query/response turns to a SQLite database at `db_path`.

    The database file (and its parent directory) is created on first use
    if it doesn't already exist.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            # Connect once up front purely to create the schema / fail
            # fast if the path is unwritable -- matches the other
            # engines' pattern of raising during construction rather than
            # on first real use.
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(_SCHEMA)
        except (OSError, sqlite3.Error) as exc:
            raise RuntimeError(
                f"Could not open/initialize conversation database at {self.db_path}: {exc}"
            ) from exc

    def save_turn(self, query: str, response: str, timestamp: float | None = None) -> int:
        """Persist one query/response turn. Returns the new row's id."""
        ts = timestamp if timestamp is not None else time.time()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO turns (timestamp, query, response) VALUES (?, ?, ?)",
                (ts, query, response),
            )
            conn.commit()
            row_id = cursor.lastrowid
            # Unreachable after a successful INSERT on a rowid table, but
            # `lastrowid` is typed `int | None`, so say what a None would
            # mean instead of feeding it to int().
            if row_id is None:
                raise RuntimeError("INSERT into turns returned no row id.")
            return row_id

    def get_recent_turns(self, limit: int = 20) -> list[StoredTurn]:
        """Return up to `limit` most recent turns, newest first.

        For inspection/debugging only -- not used anywhere in the
        generation path yet (see module docstring).
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, timestamp, query, response FROM turns ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            # Built field-by-field (not `dict(row)`) so the static type
            # matches the _SCHEMA this module owns.
            return [
                StoredTurn(
                    id=row["id"],
                    timestamp=row["timestamp"],
                    query=row["query"],
                    response=row["response"],
                )
                for row in rows
            ]

    def count_turns(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            (count,) = conn.execute("SELECT COUNT(*) FROM turns").fetchone()
            return int(count)

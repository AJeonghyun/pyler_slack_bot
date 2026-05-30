from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class VoteDatabase:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        parent = Path(db_path).expanduser().resolve().parent
        parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    case_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    root_ts TEXT NOT NULL,
                    vote_message_ts TEXT,
                    category TEXT,
                    status TEXT NOT NULL,
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    closed_at TEXT,
                    UNIQUE(channel_id, root_ts)
                );
                """
            )
            self._ensure_column(conn, "cases", "category", "TEXT")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS votes (
                    case_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    voted_at TEXT NOT NULL,
                    PRIMARY KEY (case_id, user_id)
                );
                """
            )

    def create_case_if_absent(
        self,
        channel_id: str,
        root_ts: str,
        created_by: str | None,
    ) -> tuple[dict[str, Any], bool]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM cases WHERE channel_id = ? AND root_ts = ?",
                (channel_id, root_ts),
            ).fetchone()
            if existing:
                return dict(existing), False

            case_id = self._next_case_id(conn)
            conn.execute(
                """
                INSERT INTO cases (
                    case_id, channel_id, root_ts, vote_message_ts, category, status,
                    created_by, created_at, closed_at
                )
                VALUES (?, ?, ?, NULL, NULL, 'categorizing', ?, ?, NULL)
                """,
                (case_id, channel_id, root_ts, created_by, utc_now()),
            )
            case = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            return dict(case), True

    def _next_case_id(self, conn: sqlite3.Connection) -> str:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        prefix = f"CASE-{today}-"
        row = conn.execute(
            """
            SELECT case_id
            FROM cases
            WHERE case_id LIKE ?
            ORDER BY case_id DESC
            LIMIT 1
            """,
            (f"{prefix}%",),
        ).fetchone()
        next_sequence = 1
        if row:
            next_sequence = int(row["case_id"].rsplit("-", 1)[1]) + 1
        return f"{prefix}{next_sequence:04d}"

    def set_vote_message_ts(self, case_id: str, vote_message_ts: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE cases SET vote_message_ts = ? WHERE case_id = ?",
                (vote_message_ts, case_id),
            )

    def set_category(self, case_id: str, category: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            if not existing:
                return None
            if existing["status"] == "closed":
                return dict(existing)

            conn.execute(
                """
                UPDATE cases
                SET category = ?,
                    status = 'voting'
                WHERE case_id = ?
                """,
                (category, case_id),
            )
            row = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_case(self, case_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            return dict(row) if row else None

    def _ensure_column(
        self,
        conn: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_type: str,
    ) -> None:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        if any(row["name"] == column_name for row in rows):
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")

    def upsert_vote(self, case_id: str, user_id: str, score: int) -> None:
        if score < 0 or score > 5:
            raise ValueError("score must be between 0 and 5")

        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO votes (case_id, user_id, score, voted_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(case_id, user_id)
                DO UPDATE SET score = excluded.score, voted_at = excluded.voted_at
                """,
                (case_id, user_id, score, utc_now()),
            )

    def close_case(self, case_id: str) -> tuple[dict[str, Any] | None, bool]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            if not existing:
                return None, False
            if existing["status"] == "closed":
                return dict(existing), False

            conn.execute(
                """
                UPDATE cases
                SET status = 'closed',
                    closed_at = COALESCE(closed_at, ?)
                WHERE case_id = ?
                """,
                (utc_now(), case_id),
            )
            row = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?",
                (case_id,),
            ).fetchone()
            return (dict(row) if row else None), True

    def get_vote_counts(self, case_id: str) -> dict[int, int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT score, COUNT(*) AS count
                FROM votes
                WHERE case_id = ?
                GROUP BY score
                """,
                (case_id,),
            ).fetchall()
            counts = {score: 0 for score in range(6)}
            for row in rows:
                counts[int(row["score"])] = int(row["count"])
            return counts

    def get_votes_by_score(self, case_id: str) -> dict[int, list[str]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, score
                FROM votes
                WHERE case_id = ?
                ORDER BY voted_at ASC
                """,
                (case_id,),
            ).fetchall()
            votes_by_score: dict[int, list[str]] = {score: [] for score in range(6)}
            for row in rows:
                votes_by_score[int(row["score"])].append(str(row["user_id"]))
            return votes_by_score

    def get_vote_stats(self, case_id: str) -> dict[str, Any]:
        counts = self.get_vote_counts(case_id)
        votes_by_score = self.get_votes_by_score(case_id)
        total_voters = sum(counts.values())
        total_score = sum(score * count for score, count in counts.items())
        average = total_score / total_voters if total_voters else 0.0
        max_count = max(counts.values()) if total_voters else 0
        modes = [
            score for score, count in counts.items() if total_voters and count == max_count
        ]
        return {
            "counts": counts,
            "votes_by_score": votes_by_score,
            "total_voters": total_voters,
            "average": average,
            "modes": modes,
        }

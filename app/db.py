import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class Database:
    def __init__(self, path: str):
        self.path = str(Path(path).resolve())

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def migrate(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
        # executescript commits implicitly: execute individual statements instead.
        # Our versioned migrations contain simple DDL and seed INSERTs, no triggers.
        with self.transaction() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version=?", (path.name,)).fetchone():
                    continue
                for statement in path.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (path.name,))

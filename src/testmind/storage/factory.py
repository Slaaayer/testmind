from pathlib import Path

from testmind.storage.base import Store


def open_store(db: str | None = None) -> Store:
    """Return the appropriate Store based on the connection string.

    - Strings starting with ``postgresql://`` or ``postgres://`` → PostgresStore
    - Anything else (file path or None) → SQLiteStore at that path
    """
    raw = db or ""

    if raw.startswith(("postgresql://", "postgres://")):
        from testmind.storage.postgres_store import PostgresStore
        return PostgresStore(raw)

    path = Path(raw) if raw else Path.home() / ".testmind" / "testmind.db"
    path.parent.mkdir(parents=True, exist_ok=True)

    from testmind.storage.sqlite_store import SQLiteStore
    return SQLiteStore(path)
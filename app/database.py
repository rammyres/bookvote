import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATA_DIR = os.environ.get("BOOKVOTE_DATA_DIR", "./data")
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR}/bookvote.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_column(table: str, column: str, ddl_type: str) -> None:
    """Adds a column to an existing SQLite table if it's missing.

    This project has no migration framework (overkill for its size), so
    additive, nullable columns are patched in at startup instead of
    forcing a DB wipe on every schema change. Only handles simple ADD
    COLUMN cases — anything more involved (renames, NOT NULL backfills)
    still needs a manual/managed migration.
    """
    with engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")

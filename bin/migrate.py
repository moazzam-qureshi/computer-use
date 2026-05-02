"""Apply migrations against the configured DATABASE_URL. Run: uv run bin/migrate.py"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path when invoked as `uv run bin/migrate.py`.
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from scheduler.config import Settings
from storage.connection import Database
from storage.migrate import apply_migrations, applied_versions

MIGRATIONS_DIR = Path(__file__).parent.parent / "storage" / "migrations"


def main() -> int:
    settings = Settings.from_env()
    db = Database(settings.database_url)
    try:
        already = applied_versions(db)
        print(f"Already applied: {already}")
        applied = apply_migrations(db, MIGRATIONS_DIR)
        if applied:
            print(f"Applied this run: {applied}")
        else:
            print("Nothing to apply.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

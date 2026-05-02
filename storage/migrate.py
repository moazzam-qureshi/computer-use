"""Migration runner. Plain-SQL files in storage/migrations/, numbered 001_*, 002_*, ..."""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

from storage.connection import Database


_MIG_RE = re.compile(r"^(\d{3})_.*\.sql$")


def _discover(migrations_dir: Path) -> List[tuple[int, Path]]:
    out = []
    for p in sorted(migrations_dir.iterdir()):
        m = _MIG_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), p))
    return out


def applied_versions(db: Database) -> List[int]:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'schema_migrations'
            """)
            if cur.fetchone() is None:
                return []
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            return [r[0] for r in cur.fetchall()]


def apply_migrations(db: Database, migrations_dir: Path) -> List[int]:
    """Apply any not-yet-applied migrations in order. Returns versions applied this call."""
    already = set(applied_versions(db))
    applied_now: List[int] = []
    for version, path in _discover(migrations_dir):
        if version in already:
            continue
        sql = path.read_text(encoding="utf-8")
        with db.transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
        applied_now.append(version)
    return applied_now

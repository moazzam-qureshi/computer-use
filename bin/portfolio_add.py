"""CLI: add a single portfolio item.

Usage:
    uv run bin/portfolio_add.py \\
        --name "Project Name" \\
        --client-context "..." \\
        --outcome "..." \\
        [--tech "fastapi,langchain"] \\
        [--tags "ai-agents,rag"] \\
        [--year 2025] \\
        [--summary "..."]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from scheduler.config import Settings
from storage.connection import Database
from storage.portfolio import PortfolioStore


def _split_csv(s: str | None) -> list[str]:
    if not s:
        return []
    return [item.strip() for item in s.split(",") if item.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Add a single portfolio item.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--client-context", required=True)
    parser.add_argument("--outcome", required=True)
    parser.add_argument("--tech", default="", help="Comma-separated tech list.")
    parser.add_argument("--tags", default="", help="Comma-separated relevance tags.")
    parser.add_argument("--summary", default=None)
    parser.add_argument("--year", type=int, default=None, dest="year_completed")
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    db = Database(settings.database_url)
    store = PortfolioStore(db)

    portfolio_id = store.add(
        name=args.name,
        summary=args.summary,
        client_context=args.client_context,
        outcome=args.outcome,
        tech=_split_csv(args.tech),
        relevance_tags=_split_csv(args.tags),
        year_completed=args.year_completed,
    )
    print(f"Inserted portfolio item id={portfolio_id} name={args.name!r}.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

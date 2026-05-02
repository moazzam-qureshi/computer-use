"""One-time: portfolio.json -> portfolio_items table. Idempotent (skips by name)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from scheduler.config import Settings
from storage.connection import Database
from storage.portfolio import PortfolioStore


def main() -> int:
    settings = Settings.from_env()
    db = Database(settings.database_url)
    store = PortfolioStore(db)

    portfolio_path = Path("portfolio.json")
    data = json.loads(portfolio_path.read_text(encoding="utf-8"))
    existing = {p["name"] for p in store.list_all()}
    inserted = 0
    for project in data["projects"]:
        if project["name"] in existing:
            continue
        tech_field = project.get("tech")
        tech_array = [tech_field] if isinstance(tech_field, str) else (tech_field or [])
        store.add(
            name=project["name"],
            summary=None,
            client_context=project["client_context"],
            outcome=project["outcome"],
            tech=tech_array,
            relevance_tags=project.get("relevance_tags", []),
            year_completed=None,
        )
        inserted += 1
    print(f"Inserted {inserted} portfolio items.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""One-time: insert the manual seed setup so the Bidder has something to match against in Phase 1.

Wide-net setup: matches on any strike-zone skill, requires payment-verified clients and budget at least $1500.
Tier=normal, escalation=quiet, auto_apply=disabled.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv()

from scheduler.config import Settings
from storage.connection import Database
from storage.setups import SetupStore
from domain.types import FilterDsl, Setup


SEED_SETUP_NAME = "wide-net-ai-strike-zone"

SEED_FILTER = {
    "all_of": [
        {"skill_in": [
            "ai-agents", "ai agents", "agents",
            "rag", "agentic-rag", "agentic rag",
            "langchain", "langgraph",
            "openai", "claude", "anthropic", "gemini",
            "mcp", "model context protocol",
            "voice", "voice-agent", "voice agent", "elevenlabs",
            "multi-tenant", "multi-tenancy",
            "document-intelligence", "document intelligence",
            "vector-search", "vector search", "pinecone", "qdrant", "opensearch",
            "fastapi", "next.js", "nextjs",
            "ai", "llm", "large language model",
        ]},
        {"client_payment_verified": True},
        {"budget_min_at_least": 1500},
    ]
}

SEED_PROSE = (
    "We bid on production AI engineering work for technical buyers: agents, "
    "RAG/document-intelligence systems, voice agents, MCP servers, multi-tenant AI SaaS. "
    "Strike zone is $1500+ fixed-price OR hourly retainer with payment-verified clients. "
    "We avoid: pure prompt-engineering tasks, 'build me ChatGPT' vague briefs, "
    "no-budget tire-kickers, sub-$1500 throwaways."
)


def main() -> int:
    settings = Settings.from_env()
    db = Database(settings.database_url)
    store = SetupStore(db)

    existing = next((s for s in store.list_active() if s.name == SEED_SETUP_NAME), None)
    if existing is not None:
        print(f"Seed setup {SEED_SETUP_NAME} already exists (id={existing.setup_id}).")
        return 0

    setup = Setup(
        setup_id=None,
        name=SEED_SETUP_NAME,
        status="active",
        tier="normal",
        filter_dsl=FilterDsl(SEED_FILTER),
        prose_definition=SEED_PROSE,
        pitch_template_id=None,
        cover_letter_template_id=None,
        auto_apply_enabled=False,
        escalation_config={"initial": 0, "repings": [], "deadline": None, "on_deadline": "queue"},
    )
    setup_id = store.create(setup)
    print(f"Created seed setup id={setup_id}.")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

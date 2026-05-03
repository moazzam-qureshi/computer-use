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
    "We bid on ANY AI-related engineering work, broadly defined. Strike zone "
    "(be generous on TOPIC, strict on BUDGET): "
    "LLM-based applications using GPT / Claude / Gemini / Anthropic / OpenAI APIs, "
    "AI agents (single-agent or multi-agent), agentic-RAG and document-intelligence systems, "
    "voice agents, MCP servers, AI assistants and chatbots, OpenAI Assistants API integrations, "
    "AI SaaS products, automation workflows where LLMs are the engine (not just a buzzword sprinkle), "
    "prompt-engineering layered into a real product, fine-tuning when it is plumbing inside a larger LLM app. "
    "NON-NEGOTIABLE BUDGET FLOOR — REJECT immediately if ANY of these apply, regardless of how on-topic the work sounds: "
    "(a) Fixed-price budget under 500 USD. "
    "(b) Hourly rate ceiling under 25 USD/hr. "
    "(c) Phrases like 'small initial task', 'test task', 'trial project', 'quick task', "
    "'evaluate skills', 'to start', 'paid trial' — these are tire-kicker funnels regardless of stated budget. "
    "(d) Posts that say 'potential for long-term collaboration' as the main pitch with a tiny up-front "
    "budget — the small budget IS the deal, the long-term promise is bait. "
    "The non-negotiable floor exists because tiny-budget AI jobs cost the same Doc + cover-letter "
    "generation as serious ones and dilute our signal. "
    "Above the floor, default RELEVANT for anything in this orbit even when the brief is rough — "
    "the human reviews every signal in Discord. "
    "Other hard rejects: "
    "(1) pure machine learning work where the deliverable IS the model — training classical ML models, "
    "scikit-learn / xgboost / regression / clustering pipelines, MLOps for traditional ML, "
    "recommender systems built from scratch with no LLM in the loop. "
    "(2) pure deep learning work where the deliverable IS the network — training computer vision models "
    "from scratch, custom CNN / RNN / transformer architectures, image segmentation / object detection "
    "model R&D, audio model training, deep learning research papers. "
    "(3) data-labeling or annotation work. "
    "If a job mixes ML/DL with LLM orchestration (e.g. 'we have a CV model and want an LLM agent to "
    "query it'), that IS in scope provided the budget floor is met — the LLM layer is what we are bidding on."
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

"""
Upwork job scanner — collects relevant job listings from the 'Most Recent' tab
and saves them to a markdown file. NO applying, NO submitting, NO clicking
anything that costs Connects.

Usage:
    uv run upwork_scan.py
    uv run upwork_scan.py --max-jobs 15
    uv run upwork_scan.py --output jobs/2026-05-01.md

Workflow:
  1. Focus Upwork window (must already be on the Find Work page).
  2. Click 'Most Recent' tab.
  3. Read the visible job listings.
  4. For each job that matches CRITERIA: open it, capture title/budget/url/tags,
     save to file, go back. Skip non-matches.
  5. Stop after MAX_JOBS evaluated, or when hourly pacing budget is exhausted.

Edit CRITERIA below to describe what you want.
"""
from __future__ import annotations

import argparse
import io
import sys

from dotenv import load_dotenv
from langchain.agents import create_agent

# UTF-8 console output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import pacing
import tools as tools_mod
from tools import ALL_TOOLS

# ============================================================================
# EDIT ME — what makes a job relevant?
# ============================================================================
CRITERIA = """\
The user is a Senior AI Engineer (6+ yrs) who builds production AI systems end-to-end:
multi-tenant AI SaaS, AI agents (LangGraph/LangChain/Mastra/CrewAI/Pydantic AI), custom
MCP servers, RAG pipelines (Pinecone/Qdrant/pgvector/OpenSearch with BM25 + vectors),
voice agents (ElevenLabs), and integrations across OpenAI/Claude/Gemini/Ollama. Backend
in Python (FastAPI) or .NET 8 / C# (Clean Architecture, CQRS).

He works on long retainers (6+ months), HOURLY only, NOT fixed-price, NOT under 4 weeks.

RELEVANT jobs:
  - AI agents / multi-agent systems / agentic workflows (LangGraph, LangChain, Mastra,
    CrewAI, Pydantic AI, AutoGen), especially with tool-use, memory, human-in-the-loop
  - Custom MCP servers, MCP integrations, MCP tool development
  - RAG / agentic RAG / enterprise document intelligence (hybrid search, reranking,
    VLM-based parsing, OneDrive/SharePoint/Google Drive sync)
  - Voice agents (ElevenLabs, Vapi, Retell), conversational AI with backend integration
  - LLM-powered SaaS features: AI copilots, AI workflows, automated comms over
    email/SMS/WhatsApp, classification pipelines
  - Multi-tenant AI architecture, tenant-scoped isolation, RBAC for AI features
  - Production AI engineering: scaling prototypes to production, observability
    (LangSmith, Langfuse), evaluation, guardrails
  - Senior / architect / lead AI engineer roles on serious products
  - Backend roles requiring AI integration: FastAPI + LLMs, .NET + AI features

Strong signals (boost relevance):
  - Hourly rate $50+/hr OR enterprise/SaaS company
  - "Long-term", "ongoing", "retainer", "6 months+", "team lead"
  - Payment verified, client spend > $5k, rating 4.5+
  - Mentions specific technical stack from above (LangGraph, MCP, pgvector, etc.)
  - PropTech / FinTech / HealthTech SaaS adding AI features

NOT RELEVANT — skip these:
  - Fixed-price contracts of any size — user does hourly only
  - Projects under 4 weeks duration ("quick task", "1-week job", "small project")
  - Pure prompt engineering ("write me a ChatGPT prompt", "tune my GPT")
  - "Build me a chatbot" with no backend/integration depth
  - No-code AI tools / Bubble / Make.com / Zapier-only work
  - ChatGPT wrappers with no real engineering (pure UI on top of OpenAI)
  - Content writing, blog posts, SEO, social media management
  - Generic web dev / WordPress / Shopify with no AI
  - Junior roles, "entry-level", "$5/hr", "fresher friendly"
  - No payment verification, no prior spend, no client history
  - Anything explicitly anti-USA or with red flags (fake Western names + suspicious budgets)

When uncertain, ERR ON THE SIDE OF SAVING — the user reviews the file manually
and prefers a few extra entries over missed opportunities.
"""
# ============================================================================

# Hard safety: never click anything that could cost Connects or commit to a job.
# Substring match (case-insensitive). The agent's click tool refuses these.
CLICK_BLOCKS = [
    "apply now",
    "submit proposal",
    "submit a proposal",
    "send proposal",
    "boost your profile",
    "buy connects",
    "promote",
    "subscribe",
    "upgrade",
    "claim 1 free month",
]


SYSTEM_PROMPT = f"""\
You are an Upwork job scanner. You read the user's job feed and save jobs that
match the user's relevance criteria to a file. You DO NOT apply to jobs. You
DO NOT click 'Apply Now', 'Submit Proposal', or anything that costs Connects.
Those are blocked at the tool level — if a click is refused, you've hit the
safety limit; never try to bypass it.

The user's relevance criteria:
{CRITERIA}

UPWORK-SPECIFIC FACTS YOU MUST KNOW:
- Clicking a job from the Most Recent feed opens a DETAIL PANEL on the same
  URL — the browser URL does NOT change. Do NOT read the address bar.
- To capture the canonical job URL, the RELIABLE pattern is:
    1. find_or_scroll_to("Copy link", role="button") — this is critical because
       the Copy link button is usually below the fold; this tool scrolls until
       it finds it, then stops. Don't use scroll_page + view_screen separately;
       it over-shoots past the button into the reviews section below.
    2. click(<that button id>) — clicking it copies the URL to the clipboard.
    3. read_clipboard() — returns the full URL.
  Do NOT scroll past the button into the reviews section — once you have the
  URL, save and move on.
- The detail panel also contains:
    - Title (top of panel, large hyperlink)
    - "Posted N hours/minutes ago" (top-left, small text)
    - Budget area showing hourly range, weekly hours, duration, experience
      level, OR a fixed-price amount
    - "About the client" with payment verified status, prior spend, hire rate
    - "Apply now" button (DO NOT CLICK — blocked anyway)

Workflow:
1. set_target_window("Upwork").
2. enable_rich_text() — turn on long-text capture so descriptions surface in views.
3. view_screen() to see the page. The user has the Find Work page open.
4. Click the "Most Recent" tab if it's not already active.
5. For each job listing visible in the [main] region:
     a. Read the title and short snippet from view_screen.
     b. Decide: relevant or not, based on CRITERIA. Skim, don't agonize.
        Hard-skip fixed-price jobs (the user does hourly only).
     c. If relevant: click the job's title link to open the modal.
     d. view_screen() once to see the panel layout.
     e. Read the description: find a long `text` element starting with
        "Description" or scan [main] for the longest text element, then
        read_full_text(its_id).
     f. Capture the URL in TWO STEPS:
        - find("Copy") or find("Copy to clipboard") to locate the button
        - click(that_button_id), then read_clipboard() — that's the URL.
     g. save_to_file with the markdown entry (title, URL, budget,
        posted-time, tags, why-relevant).
     h. Close the panel by ALWAYS pressing press_key("escape").
        Do NOT click the "Go Back" arrow — it moves the cursor far from the
        feed and disorients the next action. Escape stays in place and
        dismisses the panel cleanly.
     i. Move on to the NEXT job. Do not keep scrolling the same job's panel
        looking for more info — once you have title, URL, budget, tags, save
        and move on.
6. To reveal more jobs in the feed, scroll_page("down", 5) THEN view_screen().
7. Stop when:
     - You've evaluated {{max_jobs}} jobs total (relevant or not), OR
     - pacing_status shows actions used >= 80% of budget, OR
     - You've made a full pass without finding any new relevant jobs, OR
     - No new job titles appear after a scroll.
8. End by stating how many jobs you saved and to which file.

Strict rules:
- NEVER click 'Apply', 'Submit Proposal', or anything ending the proposal flow.
- Save jobs as you find them, not all at once at the end. If we crash, we keep what's saved.
- One markdown entry per relevant job. Format:

  ## <Job title>
  - **URL**: <full URL>
  - **Budget**: <fixed/hourly amount or "Not specified">
  - **Posted**: <e.g. "49 minutes ago">
  - **Tags**: <comma-separated>
  - **Why relevant**: <one sentence>

  ---

- Be terse in your reasoning text. Don't over-explain. Just take the next action.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-jobs", type=int, default=15, help="max jobs to evaluate")
    ap.add_argument("--output", default="relevant_jobs.md", help="markdown file to append jobs to")
    ap.add_argument("--model", default="openai:gpt-4o-mini")
    ap.add_argument("--max-steps", type=int, default=80, help="max LLM steps before forced stop")
    ap.add_argument("--actions-per-hour", type=int, default=40, help="pacing rate limit")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    load_dotenv()

    # Configure pacing for this session
    pacing.configure(pacing.PacingConfig(max_actions_per_hour=args.actions_per_hour))

    # Activate the click blocklist
    tools_mod.set_click_blocklist(CLICK_BLOCKS)

    sys_prompt = SYSTEM_PROMPT.format(max_jobs=args.max_jobs)
    agent = create_agent(
        model=args.model,
        tools=ALL_TOOLS,
        system_prompt=sys_prompt,
    )

    user_prompt = (
        f"Scan up to {args.max_jobs} jobs from the Most Recent tab on Upwork. "
        f"Save relevant ones to {args.output!r}. Skip non-relevant. "
        f"Stop early if pacing budget is exhausted. "
        f"Today's date should appear at the top of the file as a header if the file is new."
    )

    print(f"Goal: {user_prompt}\nOutput file: {args.output}\nModel: {args.model}\n", file=sys.stderr)

    result = agent.invoke(
        {"messages": [{"role": "user", "content": user_prompt}]},
        config={"recursion_limit": args.max_steps * 2},
    )

    messages = result["messages"]
    if args.verbose:
        for m in messages:
            role = getattr(m, "type", "?")
            content = getattr(m, "content", "")
            tool_calls = getattr(m, "tool_calls", None)
            print(f"\n--- {role} ---", file=sys.stderr)
            if tool_calls:
                for tc in tool_calls:
                    print(f"  TOOL CALL: {tc.get('name')}({tc.get('args')})", file=sys.stderr)
            if content:
                if isinstance(content, str):
                    print(content[:2000], file=sys.stderr)
                else:
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            print(block.get("text", "")[:2000], file=sys.stderr)

    final = messages[-1]
    final_content = getattr(final, "content", "")
    if isinstance(final_content, list):
        text_parts = [b.get("text", "") for b in final_content if isinstance(b, dict) and b.get("type") == "text"]
        final_content = "\n".join(text_parts)
    print("\n=== FINAL ===")
    print(final_content)


if __name__ == "__main__":
    main()

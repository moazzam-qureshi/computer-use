"""
Proposal generator for Upwork relevant jobs.

Produces two outputs per job:
  1. cover_letter — <=35 words, "above the fold" Upwork preview text.
  2. full_proposal — longer body using the locked-in formula.

Formula (verbatim opener mandatory, no LLM creativity on the structure):
  Hey [Name],

  I spent some time going over your job description. Here's the thing —
  <Insight: sharp observation about their actual problem>

  Here's how I can help you with this problem:
  <Real, specific solutions — concrete enough that it looks like we
   actually researched, not generic LLM filler>

  Happy to jump on a call and discuss this further!

If no name is found in the job post, we drop the name and use just "Hey,".
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

from openai import OpenAI

# Default model — gpt-4o-mini per user preference
DEFAULT_MODEL = "gpt-4o-mini"


@dataclass
class Proposal:
    client_name: str        # "" if not detected
    cover_letter: str       # <=35 words, includes opener
    full_proposal: str      # full body using the formula
    raw_insight: str = ""   # the "Here's the thing — ..." line, useful for alerts
    doc_markdown: str = ""  # markdown for Google Doc proposal body
    doc_url: str = ""       # populated after Google Doc is created
    diagram_url: str = ""   # mermaid.ink PNG URL, inserted at placeholder by gdocs


def _strip_em_dashes(text: str) -> str:
    """Remove em-dashes (em-dash, en-dash, double-hyphen) which are LLM tells.

    Replaces with a comma + space for inline dashes, or just a space if the
    surrounding context already has spaces.
    """
    if not text:
        return text
    # em-dash, en-dash, horizontal-bar, and the double-hyphen pattern
    out = text
    out = out.replace(" — ", ", ")  # spaced em-dash → comma
    out = out.replace(" – ", ", ")  # spaced en-dash → comma
    out = out.replace("—", ", ")    # bare em-dash anywhere
    out = out.replace("–", ", ")    # bare en-dash
    out = out.replace(" -- ", ", ") # double-hyphen
    return out


_NAME_BLOCKLIST = {
    # Sign-off words themselves
    "Thanks", "Thank", "Best", "Cheers", "Regards", "Sincerely",
    "Hi", "Hello", "Hey", "Greetings",
    # Job/budget terms
    "Hourly", "Fixed", "Expert", "Intermediate", "Entry", "Beginner", "Senior",
    "Less", "More", "About", "Posted", "Available", "Connects",
    # Common business / project words that the old regex would mistakenly grab
    "Settings", "Investment", "Permission", "Permissions", "Allowed", "Required",
    "Project", "Task", "Job", "Work", "Position", "Role", "Description", "Summary",
    "Phase", "Stage", "Milestone", "Deliverable", "Deliverables",
    "Budget", "Timeline", "Duration", "Scope",
    "Apply", "Submit", "Save", "Send",
    "Proposals", "Proposal", "Reviews", "Rating",
    "Open", "Close", "Closed", "Active", "Inactive",
    "Tech", "Stack", "Tools", "Skills", "Skill",
    "Phone", "Email", "Address", "Number",
    "First", "Last", "Final", "Initial",
    "United", "States", "USA", "America", "Europe", "Asia",
    # Common product/service names
    "Salesforce", "HubSpot", "Slack", "Zoom", "Stripe", "AWS", "Azure",
    "OpenAI", "Anthropic", "Claude", "GPT", "Gemini",
    # Common tech words capitalized
    "API", "REST", "MCP", "RAG", "LLM", "ML", "AI",
    "Python", "JavaScript", "TypeScript", "React", "Next", "Node",
}


def _looks_like_first_name(s: str) -> bool:
    """Heuristic: does this token look like an actual first name?

    Real first names: capitalized, 2-15 chars, mostly letters, not in blocklist.
    """
    if not s or s in _NAME_BLOCKLIST:
        return False
    if not (2 <= len(s) <= 15):
        return False
    if not s[0].isupper():
        return False
    # Must be mostly letters (allow apostrophe for names like "O'Brien")
    if not all(c.isalpha() or c == "'" for c in s):
        return False
    # Mostly lower-case body (i.e. capitalised name, not acronym)
    if sum(1 for c in s[1:] if c.islower()) < len(s) - 2:
        return False
    return True


def _extract_name_from_description(description: str) -> str:
    """Best-effort: extract a client first name from sign-off lines.

    Only trusts strong patterns. Better to return "" than the wrong name.
    """
    if not description:
        return ""

    # Pattern 1: trailing sign-off — the description ends with "Thanks,\nSarah"
    # or "Best regards,\nMike" or similar. These are reliable name signals.
    # We look at the last ~200 chars of the description.
    tail = description[-300:]
    signoff_re = re.compile(
        r"(?:^|\n)\s*(?:Thanks|Thank you|Best|Best regards|Cheers|Regards|Sincerely|Cheerio)\s*[,!.\-—–]*\s*\n+\s*([A-Z][a-zA-Z']{1,14})\b",
        re.MULTILINE,
    )
    m = signoff_re.search(tail)
    if m:
        cand = m.group(1)
        if _looks_like_first_name(cand):
            return cand

    # Pattern 2: explicit self-introduction "I'm Sarah" / "My name is Sarah"
    # ONLY in the first 500 chars (intros happen at the start, not buried in scope).
    head = description[:500]
    intro_re = re.compile(
        r"\b(?:I am|I'?m|My name is|This is)\s+([A-Z][a-zA-Z']{1,14})\b"
    )
    m = intro_re.search(head)
    if m:
        cand = m.group(1)
        if _looks_like_first_name(cand):
            return cand

    return ""


import json as _json
from pathlib import Path as _Path

_PORTFOLIO_PATH = _Path(__file__).resolve().parent / "portfolio.json"


def _load_portfolio() -> dict:
    if not _PORTFOLIO_PATH.exists():
        return {}
    try:
        return _json.loads(_PORTFOLIO_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


ABOUT_ME_SYSTEM = """\
You write a tailored "About me" section for a senior AI engineer responding to
an Upwork job post. You are given:
  1. The freelancer's portfolio as structured JSON: personal info + projects,
     each tagged with relevance keywords.
  2. The job description and metadata.

Pick the 2-3 projects whose relevance_tags best match this specific job and
write a 100-150 word section that makes the reader think "this person has
done exactly the kind of thing I need".

HARD RULES:
- NO em-dashes anywhere. Use commas, periods, or restructure.
- NO emojis.
- NO marketing language. Banned phrases: "I'm passionate about", "I have N
  years of experience", "I align well with your needs", "extensive expertise",
  "robust solution", "leveraging cutting-edge".
- First-person, conversational, direct. Like a senior engineer messaging
  a peer.
- Skip projects whose tags don't match. Don't list everything you've done.
- Lead with the project that maps closest to this job.
- Mention specific tech only when it overlaps with the job description.
- Keep under 150 words.

Output ONLY the section content as plain markdown (a few short paragraphs or
short bulleted blocks of text describing each chosen project, lightly).
NO headings (the doc adds the heading itself). NO preamble. NO markdown fences.
"""


SYSTEM_PROMPT = """\
You are a writing assistant that drafts Upwork proposals. The user will give you
a job description and metadata. You produce TWO outputs:

1) An "INSIGHT" — a single sharp paragraph (2-4 sentences) that:
   - Reflects you actually read the post
   - Names a specific gotcha, hidden cost, smarter framing, or technical detail
     only an experienced engineer would notice
   - Is NOT generic ("I have years of experience"). NOT a sales pitch.
   - Sounds like a senior engineer talking, not a marketer

2) A "SOLUTIONS" block — concrete, specific solutions to the problem (3-6
   short bullets or 1-2 short paragraphs). Must be:
   - Real and grounded in the job description
   - Technical when the job is technical
   - Researched-sounding — name specific tech/approach choices
   - NOT vague ("I'll build a robust scalable system") — name the actual moves

Hard rules:
  - NO emojis anywhere in the output. Plain text only.
  - Don't mention LangChain or specific frameworks unless they're named in the
    job post or naturally fit the problem.
  - Don't write "I'm passionate about" / "I align well with your needs" /
    "I have N years of experience". These are auto-disqualifiers.
  - Don't start with greetings — that's added by the template, not by you.
  - First-person, conversational, direct. Like a senior engineer messaging
    a peer, not a marketer pitching.

Return ONLY a JSON object with two string keys: {"insight": "...", "solutions": "..."}
Nothing before or after the JSON.
"""

USER_TEMPLATE = """\
Job title: {title}
Detected client name (use this in the greeting if non-empty, otherwise just "Hey,"): {client_name}
Budget / type: {budget}
Posted: {posted}
Client: {client}

Job description:
{description}
"""


COVER_LETTER_SYSTEM = """\
You write a single-line opener compressed to fit Upwork's 35-word "above the fold"
preview. NO emojis anywhere. The exact required structure is:

"Hey [Name], I spent some time going over your job description. Here's the thing — <one-line insight>. Happy to jump on a call to walk you through how I'd solve it."

If no name is provided, use "Hey, I spent some time..." (drop the name+comma, lower-case 'i' becomes capital 'I' after the comma).

Hard rules:
- The opener "I spent some time going over your job description. Here's the thing —" is mandatory verbatim.
- The closing "Happy to jump on a call ..." is mandatory.
- The middle <one-line insight> must be 8-15 words, specific to this job, and the same insight as the longer proposal's first sentence (compressed).
- Total word count must be 35 or fewer.
- Output the cover letter ONLY. No quotes, no preamble, no explanation.
"""


def _extract_one_line_insight(insight: str) -> str:
    """Compress the multi-sentence insight to one short sentence for the cover letter."""
    # Take the first sentence; strip trailing 'Here's the thing — ' if present.
    s = insight.strip()
    s = re.sub(r"^Here['’]s the thing\s*[—–-]\s*", "", s, flags=re.IGNORECASE)
    # First sentence (period or em-dash)
    m = re.split(r"(?<=[.!?])\s+", s, maxsplit=1)
    return m[0].strip() if m else s[:200]


DOC_PROPOSAL_SYSTEM = """\
You write a Google Doc proposal for a senior AI engineer responding to an
Upwork job post. Output a JSON object with these keys:

{
  "title": "<6-12 word outcome-line. Reframes the client's problem as the
            target outcome. Examples:
              'Cutting your Anthropic bill from $70k to $35k without breaking the product'
              'Permit intake to engineer-grade PDF, end to end, in 6 weeks'
              'Voice agent for outbound booking confirmations with sub-second latency'
            Not the raw job title. Not 'Proposal for X'. The outcome.>",

  "opener": "<3-5 sentences. The Doc's opening hook. MUST start with the
             greeting line: 'Hey [client_name],' if a name is given, otherwise
             just 'Hey,'. Then 'spent some time digging into your post.'
             Then a sharp, SPECIFIC observation about something IN the job post,
             not a generic restatement. Pick a detail others would miss: a
             hidden gotcha, a subtle technical implication, a smarter framing,
             a thing the client got right that proves you know the space.
             Transition to a single sentence pivoting to the plan, like
             'Here's how I'd build it.' or 'Here's what I'd actually do.'

             BAD example (do not write like this): 'Handling inbound calls for
             a real estate broker requires not only speed but also accurate
             lead qualification and seamless CRM integration. Your need for a
             voice agent that can perform these tasks efficiently is essential
             for maximizing conversions and client satisfaction.'
             That's marketing slop, restating their post back, ZERO insight.

             GOOD example: 'Hey Sarah, spent some time digging into your post.
             The interesting bit is the sub-second CRM round-trip. Most voice
             agent setups go through the Salesforce REST API and stall at
             1-2 seconds. The way around that is a direct backend tool layer,
             usually a custom MCP server, which is exactly what I built for a
             similar PropTech client last year. Here is how I would do it for you.'
             Notice it's specific, names the actual technical issue, and
             references real past work without buzzword stuffing.>",

  "approach": "<3-5 phases as a markdown list. Each phase: bold the phase
              name, then 1-2 sentences describing what happens, ending with
              the concrete deliverable. Specific not generic. Example:
                '- **Phase 1: Discovery + audit.** I'd review your current
                 prompt library and tool schemas, profile token usage by
                 endpoint. Output: spend breakdown by call type and a
                 prioritised optimization list.'>",

  "deliverables": "<3-6 bullet points. Concrete things the client will
                  have at the end. Not 'a robust system' but 'a deployed
                  RAG service handling X queries with Y latency'.>",

  "timeline": "<3-5 bullets, week-by-week or phase-by-phase. Real estimates.
              No 'depends on requirements' weasel.>",

  "questions": "<2-3 sharp clarifying questions. Things only a senior
                engineer would ask. Not 'what's your deadline'. Try to
                surface a hidden requirement or constraint.>",

  "mermaid_diagram": "<Mermaid flowchart describing the proposed architecture.
                     Use 'flowchart TD' (top-down). Match the diagram shape to
                     the actual problem; do not default to a linear chain.

                     Pick the shape that fits:

                     - LINEAR (3-4 nodes): only when the system genuinely is a
                       sequential pipeline with no branching. Rare.
                       Example (a webhook ingestion):
                         flowchart TD
                           A[Webhook event] --> B[Validate + dedupe]
                           B --> C[Queue]
                           C --> D[Worker]

                     - BRANCHING (5-7 nodes): a node feeds multiple downstream
                       nodes that run in parallel or handle different cases.
                       Most AI-engineering jobs are this shape.
                       Example (agentic RAG with parallel retrievers):
                         flowchart TD
                           Q[User query] --> R[Router agent]
                           R --> BM[BM25 retriever]
                           R --> VS[Vector retriever]
                           BM --> RR[Reranker]
                           VS --> RR
                           RR --> LLM[LLM with context]
                           LLM --> A[Streamed answer]

                     - WITH CALLBACK / FEEDBACK (5-7 nodes): includes a loop
                       or human-in-the-loop step.
                       Example (agent with HITL approval):
                         flowchart TD
                           U[User goal] --> P[Planner agent]
                           P --> T[Tool calls]
                           T --> A{{Write action?}}
                           A -- yes --> H[Human approval]
                           A -- no --> X[Execute]
                           H --> X
                           X --> R[Result + memory update]

                     - SUBSYSTEMS (with subgraph): multiple bounded contexts.
                       Use when the job describes distinct services.
                       Example:
                         flowchart TD
                           subgraph Frontend
                             U[User] --> APP[Next.js app]
                           end
                           subgraph Backend
                             APP --> API[FastAPI]
                             API --> DB[(Postgres)]
                             API --> AG[Agent worker]
                           end
                           AG --> LLM[LLM]

                     Cap at 7 nodes total. Keep node labels short (1-3 words).
                     If the job is genuinely simple, a 3-node linear flow is
                     fine. If the job has any of: routing, parallel retrieval,
                     human-in-the-loop, multiple data sources, agents, or
                     async workers, use one of the branching shapes.>"
}

HARD RULES — violations make the proposal look like LLM output:
1. NO em-dashes anywhere ( the long dash character that an LLM loves to use).
   Use commas, periods, parentheses, or restructure the sentence. Em-dashes
   are an instant tell.
2. NO emojis anywhere.
3. NO greeting words ('Hi', 'Hello', 'Hey'). The cover letter handles greetings;
   the doc opener is paragraph-style.
4. NO marketing fluff. Banned phrases: "I'm passionate about", "I have N years
   of experience", "I align well with your needs", "I am writing to express",
   "robust solution", "scalable architecture", "leveraging cutting-edge",
   "best practices", "synergy".
5. Don't mention frameworks unless the job names them or they obviously fit
   the problem.
6. Be specific. "I'd start with the auth layer" beats "I'd build a robust
   solution".
7. First-person, conversational, direct. Like a senior engineer messaging
   a peer.

Output ONLY the JSON. No preamble. No markdown fences.
"""


ANSWER_SYSTEM = """\
You are answering a screening question on an Upwork job application, written
in the voice of a senior software engineer responding to a client.

Voice rules (HARD):
- First-person, conversational, direct.
- NO em-dashes, NO en-dashes, NO double-hyphens. Use commas.
- NO emojis.
- NO marketing fluff. Banned phrases include: "I'm passionate about",
  "robust solution", "leveraging cutting-edge", "I align well with your needs",
  "I have N years of experience".
- Don't repeat the cover letter content verbatim. The client will read both.
- Don't mention frameworks/tools unless the job names them or they obviously fit.
- Don't invent projects, clients, companies, or numbers. Only reference past
  work that appears in the provided portfolio data.

Length: 60-150 words. Not so short it looks lazy, not so long it looks padded.

Output: just the answer text. No preamble, no quotes, no labels."""


def _generate_tailored_about_me(
    job_title: str,
    description: str,
    client: OpenAI,
    model: str,
) -> str:
    """Use the portfolio JSON + job description to produce a tailored About-me.

    Returns plain markdown content (no heading). Falls back to a static
    summary if the LLM call fails or the portfolio is missing.
    """
    portfolio = _load_portfolio()
    if not portfolio:
        return (
            "Senior AI engineer with six years of experience building "
            "production AI systems including agents, RAG, and voice. "
            "Long-term retainers, weekly updates, daily timezone-overlap responsiveness."
        )

    user_prompt = (
        "PORTFOLIO (JSON):\n"
        f"{_json.dumps(portfolio, indent=2)}\n\n"
        "JOB:\n"
        f"Title: {job_title}\n\n"
        f"Description:\n{(description or '')[:5000]}\n\n"
        "Write the tailored About-me section now."
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": ABOUT_ME_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            max_tokens=400,
        )
        text = (resp.choices[0].message.content or "").strip()
        return _strip_em_dashes(text) or (
            "Senior AI engineer focused on production AI systems."
        )
    except Exception:
        return (
            "Senior AI engineer focused on production AI systems including "
            "agents, agentic RAG, voice agents, and multi-tenant SaaS."
        )


def generate_doc_proposal(
    job_title: str,
    description: str,
    budget: str = "",
    posted: str = "",
    client_summary: str = "",
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> Proposal:
    """Generate a structured Doc proposal + matching cover letter.

    Output:
      Proposal.client_name      — best-effort name extracted from description
      Proposal.doc_markdown     — markdown body for the Google Doc
      Proposal.cover_letter     — short Upwork cover letter (the doc_url
                                  is a placeholder; the caller fills it in
                                  after creating the Doc)
      Proposal.raw_insight      — for Discord alerts
    """
    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
    name = _extract_name_from_description(description)

    user_prompt = USER_TEMPLATE.format(
        title=job_title,
        client_name=name or "(no name detected, use plain 'Hey,')",
        budget=budget or "(not specified)",
        posted=posted or "(unknown)",
        client=client_summary or "(unknown)",
        description=(description or "")[:5000],
    )

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": DOC_PROPOSAL_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
        response_format={"type": "json_object"},
        max_tokens=1200,
    )
    raw = (resp.choices[0].message.content or "").strip()

    import json
    try:
        parsed = json.loads(raw)
    except Exception:
        parsed = {}

    def _coerce(value, default: str) -> str:
        """Accept either a string or a list of bullets/strings; return clean markdown."""
        if isinstance(value, list):
            # Each item: keep "-" prefix if it has one, else add it.
            lines = []
            for item in value:
                s = str(item).strip()
                if not s:
                    continue
                if not s.startswith(("-", "*")):
                    s = f"- {s}"
                lines.append(s)
            text = "\n".join(lines)
        else:
            text = str(value or "").strip()
        text = _strip_em_dashes(text)
        return text or default

    title = _strip_em_dashes(str(parsed.get("title") or "").strip()) or job_title
    opener = _coerce(parsed.get("opener"), (
        "Most teams will treat this as a standard build. The interesting work is "
        "in the data flow between your existing systems, which is where the leverage is."
    ))
    approach = _coerce(parsed.get("approach"), (
        "- **Phase 1: Discovery.** Review the current setup, profile what works and what doesn't. Output: an architecture map and a prioritised work list.\n"
        "- **Phase 2: Core build.** Ship the smallest version that delivers real value. Output: a working system.\n"
        "- **Phase 3: Iterate.** Tune based on actual usage."
    ))
    deliverables = _coerce(parsed.get("deliverables"), (
        "- Working system in production\n"
        "- Documentation and clean handover\n"
        "- One sync per week with progress demos"
    ))
    timeline = _coerce(parsed.get("timeline"), (
        "- Week 1-2: Discovery and architecture\n"
        "- Week 3-4: Core build\n"
        "- Week 5+: Iterate based on real usage"
    ))
    questions = _coerce(parsed.get("questions"), (
        "- What does success look like for this in 90 days?\n"
        "- Any constraints I should know about (compliance, existing infra, team size)?"
    ))
    mermaid_src = (parsed.get("mermaid_diagram") or "").strip()

    # Build the diagram image URL (mermaid.ink). Skip if generation failed
    # or the URL would be too long for Composio's 2KB image-insert limit.
    # Build the diagram URL. The image is NOT embedded in the markdown
    # because CREATE_DOCUMENT_MARKDOWN's image rendering is inconsistent.
    # Instead, gdocs.create_proposal_doc inserts it at end-of-doc via
    # INSERT_INLINE_IMAGE after fetching the real end-index.
    diagram_url = ""
    if mermaid_src:
        try:
            import mermaid as mermaid_mod
            url = mermaid_mod.diagram_to_url(mermaid_src)
            if mermaid_mod.is_safe_url_size(url):
                diagram_url = url
        except Exception:
            diagram_url = ""

    # Tailor the About-me section to this specific job using the portfolio JSON.
    about_me = _generate_tailored_about_me(
        job_title=job_title,
        description=description,
        client=client,
        model=model,
    )

    # Compose the Doc body. Opener is a plain paragraph (no "Here's the thing" heading).
    doc_md_parts = [
        f"# {title}",
        "",
        opener,
        "",
        "## How I'd approach it",
        approach,
        "",
        "## What you'd get",
        deliverables,
        "",
        "## Timeline",
        timeline,
        "",
        "## A bit about me",
        about_me,
        "",
        "## Questions I'd want to clarify",
        questions,
    ]
    # If we have a diagram, end the doc with "How the pieces fit together".
    # The image is inserted by gdocs.py just under this heading, making it the
    # closing visual. The CTA + signoff live in the cover letter, not the doc.
    if diagram_url:
        doc_md_parts += [
            "",
            "## How the pieces fit together",
            "",
        ]
    doc_markdown = "\n".join(doc_md_parts)

    # Cover letter: locked formula. doc_url is filled in by the caller after
    # the Google Doc is created. The CTA + signoff live here (not the doc)
    # so the doc ends cleanly on the diagram.
    greeting = f"Hey {name}," if name else "Hey,"
    cover_letter_template = (
        f"{greeting} I spent some time going over your job description.\n"
        f"Here's how I would approach it: {{DOC_URL}}\n"
        f"Reply with a good time and we can hop on a 20-minute call.\n"
        f"- Moazzam"
    )

    return Proposal(
        client_name=name,
        cover_letter=cover_letter_template,
        full_proposal=doc_markdown,
        raw_insight=opener[:300],
        doc_markdown=doc_markdown,
        doc_url="",
        diagram_url=diagram_url,
    )


def generate_proposal(
    job_title: str,
    description: str,
    budget: str = "",
    posted: str = "",
    client_summary: str = "",
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
) -> Proposal:
    """Generate a Proposal for a job.

    Two LLM calls:
      1. Insight + solutions (the body content).
      2. Cover letter compression (35-word version).
    """
    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    # 1. Insight + solutions
    user_prompt = USER_TEMPLATE.format(
        title=job_title,
        client_name=_extract_name_from_description(description) or "(no name detected)",
        budget=budget or "(not specified)",
        posted=posted or "(unknown)",
        client=client_summary or "(unknown)",
        description=(description or "")[:5000],
    )

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.4,
        response_format={"type": "json_object"},
        max_tokens=900,
    )
    raw = (resp.choices[0].message.content or "").strip()

    import json
    try:
        parsed = json.loads(raw)
        insight = (parsed.get("insight") or "").strip()
        solutions = (parsed.get("solutions") or "").strip()
    except Exception:
        # Fallback: dump the whole thing into solutions
        insight = raw[:400]
        solutions = raw

    if not insight:
        insight = "Most people will treat this as a generic AI build, but the real lever here is the data-flow design between your existing systems."
    if not solutions:
        solutions = "I'd start with a quick architecture review, then ship a focused MVP that proves the core value before expanding."

    # 2. Build the full proposal from the locked formula
    name = _extract_name_from_description(description)
    greeting = f"Hey {name}," if name else "Hey,"

    full_proposal = (
        f"{greeting}\n\n"
        f"I spent some time going over your job description. Here's the thing — "
        f"{insight}\n\n"
        f"Here's how I can help you with this problem:\n"
        f"{solutions}\n\n"
        f"Happy to jump on a call and discuss this further!"
    )

    # 3. 35-word cover letter
    one_line = _extract_one_line_insight(insight)

    cl_user = f"Name: {name or '(none)'}\nOne-line insight: {one_line}"
    cl_resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": COVER_LETTER_SYSTEM},
            {"role": "user", "content": cl_user},
        ],
        temperature=0.2,
        max_tokens=120,
    )
    cover_letter = (cl_resp.choices[0].message.content or "").strip().strip('"')

    # Sanity-check word count; if over 35, build a fallback by trimming
    if len(cover_letter.split()) > 35:
        if name:
            cover_letter = (
                f"Hey {name}, I spent some time going over your job description. "
                f"Here's the thing — {one_line[:60]}. Happy to jump on a call to walk you through how I'd solve it."
            )
        else:
            cover_letter = (
                f"Hey, I spent some time going over your job description. "
                f"Here's the thing — {one_line[:60]}. Happy to jump on a call to walk you through how I'd solve it."
            )

    return Proposal(
        client_name=name,
        cover_letter=cover_letter,
        full_proposal=full_proposal,
        raw_insight=insight,
    )


def generate_screening_answer(
    question: str,
    job_description: str,
    cover_letter: str,
    portfolio_json: str,
    model: str = DEFAULT_MODEL,
) -> str:
    """One LLM call to answer a single Upwork screening question."""
    user_prompt = f"""\
QUESTION FROM CLIENT:
{question}

JOB DESCRIPTION (for context):
{job_description[:2000]}

COVER LETTER ALREADY SUBMITTED (do NOT repeat its content verbatim):
{cover_letter}

MY PORTFOLIO (raw JSON, pull only relevant past work):
{portfolio_json}

Write the answer now."""

    client = OpenAI()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=400,
    )
    text = (resp.choices[0].message.content or "").strip()
    return _strip_em_dashes(text)

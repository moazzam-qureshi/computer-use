# Computer-Use System — Architecture & Operations

## What this project does

End-to-end automated Upwork job scanner + proposal drafter. Runs 24/7 on a Windows laptop, scans the "Most Recent" tab of the user's Upwork feed every 15-20 minutes, judges each new job for relevance using gpt-4o-mini, and for relevant jobs:

1. Generates a tailored Google Doc proposal (with a Mermaid architecture diagram)
2. Generates a 35-word cover letter referencing the Doc URL
3. Posts both to Discord via webhook so the user can review and apply

The system controls a real Chrome browser via OS-level UI Automation reads + SendInput — undetectable by browser fingerprinting, no Playwright, no CDP.

## Core architectural principle

**Substrate first, agent second.** We built a deterministic computer-use substrate (UI Automation tree reads + SendInput writes) and proved each primitive manually with `dump.py` and `poke.py` before adding any LLM. Then for high-frequency tasks (Upwork scanning), we use a **deterministic Python driver** with the LLM only invoked for *judgment* (1-2 calls per relevant job). For one-off exploration, we keep a general-purpose LangChain agent (`agent.py`).

This split is intentional: agents are great for novel tasks (one-off exploration), bad for production loops (recursion limits, hallucinations, runaway costs).

## File map

### Substrate (the foundation)
- `observe.py` — walks Windows UI Automation tree of any window, returns filtered Element list. Supports `include_unnamed` (for editable surfaces with no name) and `include_text` (for paragraphs / labels). Handles Chrome's renderer-accessibility wake-up.
- `act.py` — input primitives: `click(Element)`, `type_text`, `key(combo)`, `navigate(url)`, `scroll(amount, method='key'|'wheel')`, `focus_window`. All routed through `pacing` for human-paced timing.
- `launch_chrome.py` — kills any existing Chrome, launches a fresh instance with `--force-renderer-accessibility` flag and a chosen profile. Required for UIA to expose web content.
- `pacing.py` — session budget (40 actions/hr default), bimodal delays (80% fast, 15% medium, 5% long), click-jitter, reading-time pauses. Configurable per-session via `PacingConfig`.
- `dump.py` — CLI tool: dumps the foreground (or `--window`) UIA tree to `last_dump.json`. Diagnostic. Used to validate selectors before automating.
- `poke.py` — CLI tool: clicks/types/finds against `last_dump.json` or live observation. Diagnostic.

### General-purpose LLM agent (LangChain)
- `view.py` — token-efficient view layer over `observe.py`. Region detection (top/left/main/right/modal), stable hash IDs, `[SUBMIT]` tag lifting, chrome-shell filtering, `find_or_scroll_to`.
- `tools.py` — 15 `@tool` functions wrapping observe + view + act for LangChain. Wraps each in `@_with_com` to handle COM init on LangChain's worker thread. Includes a `CLICK_BLOCKLIST` safety mechanism to prevent destructive clicks (Apply / Submit Proposal etc.) during scanning tasks.
- `agent.py` — `langchain.agents.create_agent` with `gpt-4o-mini`, the 15 tools, and a system prompt teaching the spatial-region model. CLI: `uv run agent.py --verbose "<goal>"`.
- `post_linkedin.py` — hardcoded LinkedIn-post recipe (proves the substrate end-to-end without an agent).

### Upwork-specific deterministic driver
- `upwork_driver.py` — the production scanner. Top-of-feed scan, panel-open per card, parses panel structure deterministically, judges via one LLM call, captures URL via clipboard click on "Copy to clipboard" button, optionally generates Doc proposal, pings Discord. Persists processed URLs to `seen_urls.txt` for cross-run dedup.
- `scheduler.py` — infinite loop. Every 15-20 min (random jitter), invokes `upwork_driver.py --reload-first` as a subprocess. Survives Ctrl+C cleanly.
- `proposal.py` — generates the structured Doc proposal markdown + cover letter. Two LLM calls per relevant job: (1) `DOC_PROPOSAL_SYSTEM` produces title/opener/approach/deliverables/timeline/questions/mermaid diagram; (2) `ABOUT_ME_SYSTEM` produces a tailored "About me" section using `portfolio.json` as raw data.
- `gdocs.py` — Composio wrapper. Creates the Doc from markdown, downloads the rendered Mermaid PNG from mermaid.ink, resizes it via Pillow (max 720px), uploads to Drive, makes public, inserts at end-of-doc via INSERT_INLINE_IMAGE.
- `mermaid.py` — diagram-to-URL helper. Pako-compressed base64 encoding for short URLs. Fetches PNG bytes when needed.
- `notify.py` — Discord webhook. `send_alert` (terse alert with title, budget, client, why-relevant, URL) + `send_proposal` (cover letter + Doc URL).
- `portfolio.json` — user's portfolio raw data. Edit this anytime; the LLM never modifies it. Used to generate tailored "About me" per job.

### Diagnostics
- `inspect_composer.py` — diagnostic for LinkedIn composer (uses diff strategy to find dynamically-rendered modal content).
- `debug_feed.py`, `debug_slices.py` — diagnostics for Upwork feed parsing.
- `NOTES.md` — deferred improvements.

## How the Upwork scanner runs end-to-end

1. **scheduler.py** wakes up (random 15-20 min interval).
2. Spawns `upwork_driver.py --reload-first --max-jobs 10` as a subprocess.
3. Driver focuses Chrome (must be launched via `launch_chrome.py` so renderer-accessibility is on), presses Ctrl+R to refresh feed, clicks "Most Recent" tab, presses Ctrl+Home to scroll to top.
4. For each job at top of feed:
   - **Click title** → panel opens (right-side detail panel; URL doesn't change).
   - **Two-phase observe**: scroll panel to top, then PageDown 4× to surface description / skills / About-the-client / Job-link section. Merge all observed elements.
   - **Parse panel** by structural anchors: title, posted-time (regex), budget (Hourly/Fixed-price + chips), client trust (Payment verified, rating, spend, etc.), skills (after "Skills and Expertise"), description (longest text element).
   - **Judge** via gpt-4o-mini. Skip rules: only skip if BOTH (a) Fixed-price AND (b) duration is "Less than 1 month / less than 1 week / quick task". Anything else → RELEVANT.
   - If RELEVANT and URL not yet in `seen_urls.txt`:
     - `find_or_scroll_to` "Copy to clipboard" button → click it → `read_clipboard` → URL captured.
     - Generate proposal (two more LLM calls): doc body + tailored about-me.
     - mermaid.ink renders the architecture diagram → fetched as PNG → resized to 720px via Pillow → uploaded to Drive via Composio → made public → inserted into Doc at end-of-doc via INSERT_INLINE_IMAGE.
     - Doc made public, URL embedded in cover letter.
     - Discord webhook fires twice: alert + cover-letter-and-doc-url.
     - URL appended to `seen_urls.txt`.
   - Press Esc to close panel.
5. Driver exits. Scheduler sleeps until next cycle.

## Cover letter formula (LOCKED)

```
Hey [Name], I spent some time going over your job description.
Here's how I would approach it: <Doc URL>
Reply with a good time and we can hop on a 20-minute call.
- Moazzam
```

If client name not detected, drops to `Hey,` (no comma + name). Name detection only fires on strict sign-off patterns ("Thanks,\nSarah") or explicit intros ("I'm Mike") to avoid hallucinating names from random capitalized words.

## Doc structure (LOCKED)

```
# [Outcome-line title — 6-12 words, NOT the raw job title]
[2-3 sentence opener: "Hey [Name], spent some time digging into your post.
 [sharp specific insight from job post]. Here's how I'd build it."]

## How I'd approach it
[3-5 phases, each: bold name + 1-2 sentences + concrete deliverable]

## What you'd get
[3-6 concrete deliverables]

## Timeline
[3-5 week-by-week or phase-by-phase bullets, no weasel]

## A bit about me
[Tailored to job via portfolio.json relevance_tags, 100-150 words,
 picks 2-3 most relevant past projects]

## Questions I'd want to clarify
[2-3 sharp clarifying questions only a senior would ask]

## How the pieces fit together
[DIAGRAM — Mermaid → mermaid.ink PNG → Drive → embedded inline image]
```

## Hard rules (encoded in prompts)

- NO em-dashes anywhere (replaced with comma + space; LLM tells)
- NO emojis anywhere
- NO marketing fluff: banned phrases include "I'm passionate about", "robust solution", "leveraging cutting-edge", "I align well with your needs", "I have N years of experience"
- Don't mention frameworks unless the job names them or they obviously fit
- First-person, conversational, direct — like a senior engineer messaging a peer

## Mermaid diagram rules

- ALWAYS produce a diagram, never skip
- 3-7 nodes, short labels (1-3 words)
- Pick shape based on the job:
  - **Linear**: rare, only true sequential pipelines
  - **Branching**: parallel retrievers, routers, multiple downstreams (most AI jobs)
  - **Feedback/HITL**: loops, decision diamonds, human-in-the-loop
  - **Subsystems**: subgraphs when there are multiple bounded contexts
- Default to one of the branching shapes for any job involving routing, parallel retrieval, HITL, multiple data sources, agents, or async workers

## Critical operational facts

### Chrome launch (must do every time before scheduler starts)
```
uv run launch_chrome.py --profile "Moazzam" --url "https://www.upwork.com/nx/find-work/" --kill-existing
```
The `--force-renderer-accessibility` flag is mandatory; without it Chrome's content tree is not exposed and parsing fails entirely. Re-launching kills any existing Chrome and creates a clean accessibility-enabled instance.

### Panel parse anchors (don't break these without re-dumping)
- Posted: text element matching `^\d+\s*(minute|hour|day|week|month)s?\s*ago$`
- Budget chips: text "Fixed-price" / "Hourly" / "Hourly: $X-$Y" / "Less than X hrs/week" / experience level
- Job URL: clicking the "Copy to clipboard" button (exact name match), then read clipboard. Element bounds must be in the right column (x ≥ 1200) and not too close to panel top (y ≥ 200) — else likely the "Open job in a new window" link, abort.
- Description: longest text element (≥100 chars) in the panel.

### Why we click panel content vs reading from feed
The Upwork feed exposes job titles cleanly but truncates / mangles other fields. The panel has consistent structure. We sacrifice ~5 seconds per job to open the panel and get reliable data, in exchange for never hallucinating budgets / SKIP reasons.

### Copy-to-clipboard click hardening
Click uses dead-center `click_xy` (no jitter) because the button is small (~76×26 px). Bounds are validated before click: x ≥ 1200 (right column), y ≥ 200 (not panel top). If validation fails, abort URL capture and skip rather than risk clicking "Open in new window" which navigates the tab away.

### Pacing self-protection
40 actions/hr default. Scanner stops mid-cycle if budget hits 85%. Wheel-scroll is preferred for cards (precise advance per scroll); PageDown is too aggressive (skips cards). Inside panel: PageDown is fine (panel is the focused element).

### Composio versions (PINNED)
- googledocs: `20260501_01`
- googledrive: `20260429_00`

These are pinned in `gdocs.py`. "latest" is not accepted by Composio's manual-execution layer. Update these strings if Composio releases a new version that breaks behavior.

### Composio actions used
- `GOOGLEDOCS_CREATE_DOCUMENT_MARKDOWN` — body creation (markdown image syntax is unreliable, do not use)
- `GOOGLEDOCS_GET_DOCUMENT_BY_ID` — fetch real doc.body.content[-1].endIndex (param: `id`, NOT `documentId`; the SDK accepts both with retry)
- `GOOGLEDOCS_INSERT_INLINE_IMAGE` — at end-of-doc (param: `documentId`, also accepts `id`). URI must be a direct image URL; Drive `https://drive.google.com/uc?export=view&id=<id>` works
- `GOOGLEDRIVE_UPLOAD_FILE` — for the Mermaid PNG (Composio auto-stages local paths when `dangerously_allow_auto_upload_download_files=True` and the file is in `file_upload_dirs`; we whitelist `tempfile.gettempdir()`)
- `GOOGLEDRIVE_CREATE_PERMISSION` — `type="anyone", role="reader"` for both the Doc and the diagram image

## Things that DON'T work (lessons)

- **Playwright / Puppeteer** — detected via navigator.webdriver, CDP signals
- **Markdown image syntax `![alt](url)`** in Composio's CREATE_DOCUMENT_MARKDOWN — renders as plain text `[alt: text]` inconsistently
- **Mid-doc image insertion** with INSERT_INLINE_IMAGE at a computed mid-doc index — Google Docs does fancy paragraph-level rendering that makes precise mid-doc indices brittle. End-of-doc is reliable.
- **mermaid.ink hot-link** as the URI passed to INSERT_INLINE_IMAGE — Google Docs sometimes can't fetch the URL (timing, redirects). Solved by downloading PNG → uploading to user's own Drive → using Drive URL.
- **`objectSize` parameter** on INSERT_INLINE_IMAGE — Composio's wrapper doesn't expose it. Sizing controlled solely by source PNG dimensions (we resize via Pillow before upload).
- **The general-purpose agent for production scanning** — gpt-4o-mini hits 160-call recursion limits on its own loop. Deterministic driver is the right tool for this.
- **Feed-only judgment** — Upwork feed truncates / inconsistently exposes budget/client info. Always open the panel for ground-truth data.
- **Feeding the LLM noisy budget strings** like "$17.13" (client avg hourly rate paid) without filtering — the LLM treats it as the job rate. Budget extractor strips client-history bits before passing to the LLM.

## .env required

```
OPENAI_API_KEY=sk-...
COMPOSIO_API_KEY=...
COMPOSIO_USER_ID=XzwQgfAYOe8NWSTRDNl9842dDa12VEEy
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

## Operational commands

```bash
# Install deps
uv sync

# Launch Chrome (do once at start)
uv run launch_chrome.py --profile "Moazzam" --url "https://www.upwork.com/nx/find-work/" --kill-existing

# One-off scan (testing)
uv run upwork_driver.py --reload-first --max-jobs 5

# Production scheduler (24/7)
uv run scheduler.py

# Diagnostic substrate tools
uv run dump.py --window "Upwork" --text
uv run poke.py --live --find "Copy to clipboard"
```

## Next milestone (in progress)

**Job-link auto-applier.** Take a job URL → navigate → fill in the proposal cover letter (auto-paste from drafted version) → set bid amount → click Submit. Will reuse the substrate (act.py + observe.py) and add a new driver that knows the Apply form's structure. The hard parts:
- Reliably navigating to the apply panel from a job URL
- Filling Connects / bid / cover-letter fields
- Detecting and surfacing any "answer these questions" client-custom fields for human review
- Hard safety: NEVER click final Submit without human confirmation (or with explicit `--really-submit` flag)

## Stylistic / behavioral preferences (encoded across prompts)

- The user's voice: senior engineer, conversational, direct, blunt when needed
- Hook formula: "Hey [Name], I spent some time going over your job description. Here's the thing..."
- Doc opener: "Hey [Name], spent some time digging into your post. [insight]. Here's how I'd build it."
- Cover letter: short, doc-link forward, ends with call + signoff
- Diagrams: real architecture, not lazy linear flows
- About-me: tailored per job, not one-size-fits-all dump

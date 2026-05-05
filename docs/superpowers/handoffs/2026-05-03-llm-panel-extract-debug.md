# Handoff: Debug LLM Panel Extraction End-to-End

**Created:** 2026-05-03
**Branch:** `phase1-foundation` (do NOT merge to main)
**Working dir:** `d:\Personal\Projects\computer-use`

## TL;DR

Phase 1 architecture is operational end-to-end EXCEPT the field-extraction
quality is broken in production despite working in isolation. The Discord
embed shows `Budget: None $0`, `Posted: recent`, `Client: ?` even though we
just replaced the regex parser with an LLM-based extractor and verified it
returns correct values on synthetic input. Something between
`ai/panel_extract.extract_panel()` and `Discord embed render` is dropping
fields, AND the underlying jobs row is also persisted with all null fields,
so the loss is upstream of storage.

Fresh session needs to:

1. Run one cycle with **the raw element dump printed** so we see what the LLM
   is actually being fed on a real panel
2. Confirm whether the LLM is returning empty fields, or returning correct
   fields that then get dropped before `JobStore.upsert`
3. Fix whichever layer is the culprit

## What's working (do NOT regress)

- Branch `phase1-foundation`, 7 operational commits past the original Phase 1 ship
- Bidder loop: 10/10 URLs captured per cycle, navigates Upwork via fresh-tab pattern
- Apply executor: end-to-end verified, navigates `/nx/proposals/job/~ID/apply/`,
  pastes cover letter, sets rate-increase to Never, stops short of submit
- Discord bot: connects, slash commands work, signal embeds post
- UI lock prevents bidder/applier race conditions
- Click-y safety cap prevents Windows-taskbar mis-clicks
- Login-required detection in `bidder.scan_cycle` raises `LoginExpired`,
  bidder_loop converts to `@owner` Discord ping
- `gpt-4o` -> `gpt-4o-mini` (key didn't have gpt-4o access)
- Full-scan-only (no humanizer cycle-type variation, per operator preference)
- "Hungry freelancer" diurnal envelope (95-100% during waking hours)
- First-cycle-immediate flag in `bidder_loop` for fast feedback on restart
- Setup is LLM-only-gated (`filter_dsl={}`, rich `prose_definition` describes
  strike zone)
- Relevance LLM uses relaxed prompt (default-relevant, only reject clearly bad
  fits)
- Proposal generation uses raw `response_format={"type": "json_object"}` mode
  with `_coerce_to_string_list` / `_coerce_to_string` helpers (gpt-4o-mini
  occasionally returns dicts where strings are expected)
- Proposal `max_tokens=6000` (was 1500, which truncated mermaid_diagram field)
- Generic mermaid default removed (no more `User -> System -> Result`); empty
  mermaid skips the diagram heading entirely
- `Job.posted_text` field added; `_to_job` carries it through

## What's broken

**Symptom**: After running scheduler.main against a fresh feed:

```sql
SELECT job_id, substring(title, 1, 35), budget_kind, budget_min_usd, posted_text, client_country
FROM jobs ORDER BY scraped_first_at DESC LIMIT 3;

 022050907811848079631 | Setup Autonomous AI Agents |   |   |   |
```

All fields except title are NULL. Discord embed shows:
- Budget: `None $0`
- Posted: `recent`
- Client: `?`
- Why matched: (empty)

**But** in isolation, calling `ai.panel_extract.extract_panel()` with a
synthetic element dump returns ALL fields correctly:

```
title: 'Setup Autonomous AI Agents'
posted_text: '17 minutes ago'
budget_kind: hourly
budget_min/max: 15.0 20.0
duration: '1 to 3 months'
experience_level: 'Intermediate'
hours_per_week: 'Less than 30 hrs/week'
skills: ['AI Agent Development', 'Claude', 'OpenRouter', 'Workflow Automation']
client_country: United States
client_payment_verified: True
client_rating: 5.0
client_total_spent_usd: 14000.0
client_hires: 58
```

So the extractor itself works. The loss is happening either:
1. The LLM returns nothing useful when fed the REAL element dump (maybe the
   real dump is too noisy / different shape from synthetic), OR
2. `extract_panel` returns correct values but they get dropped between there
   and the DB (unlikely - we instrumented the print line in `scan_cycle.py`)

## Files in flight (UNCOMMITTED)

```
M  ai/prompts/relevance.py        (relaxed system prompt, default-relevant)
M  ai/proposal_gen.py             (raw JSON + coercion helpers, max_tokens=6000)
M  ai/schemas.py                  (added PanelExtraction; removed mermaid default; ProposalDraft has field_validators for dict->str coercion)
M  bidder/draft_pipeline.py       (job_title passed to gdocs; doc_url placeholder substitution)
M  bidder/scan_cycle.py           (uses extract_panel from ai/panel_extract; LoginExpired raise; full-scan only; immediate-first-cycle skipped here)
M  bidder/signal_pipeline.py      (LLM relevance check runs for EVERY active setup, not gated by rule match)
M  domain/scoring.py              (empty filter_dsl now treated as 'always pass')
M  domain/types.py                (Job.posted_text added)
M  external/gdocs.py              (_proposal_to_markdown fixed to use approach_phases/questions; create_doc_with_diagram takes job_title; skips diagram heading when no diagram)
M  scheduler/main.py              (immediate first cycle, ui_lock, LoginExpired -> @owner Discord ping, posted_text wired through)
M  storage/jobs.py                (upsert COALESCEs all client/posted fields; get reads posted_text)
M  upwork/panel.py                (added budget_diag print -- can be removed; legacy parse_panel still exists but is no longer called from scan_cycle)
?? ai/panel_extract.py                              (NEW: LLM-based panel extractor)
?? storage/migrations/002_add_jobs_posted_text.sql  (NEW: ALTER TABLE jobs ADD COLUMN posted_text TEXT)
```

Migration 002 has been APPLIED to the dev DB (`bin/migrate.py` reports
"Applied this run: [2]"). 39/39 unit tests pass.

## Resume instructions for fresh session

```
We're mid-debug on the LLM panel extraction in the Phase 1 Upwork bot.

Branch: phase1-foundation (do NOT merge to main; do NOT commit until the
        extraction bug is fixed)
Working dir: d:/Personal/Projects/computer-use
Handoff doc: docs/superpowers/handoffs/2026-05-03-llm-panel-extract-debug.md
Postgres is running in Docker. Verify with `docker compose ps`.

The architecture is sound; only field extraction is failing in production
despite working in isolation. Read the handoff doc first, then:

1. Add a debug print in ai/panel_extract.extract_panel() that dumps the
   first ~3000 chars of the raw element text passed to the LLM, AND the
   raw JSON response from the LLM, AND the parsed Pydantic object.
2. Reset DB: `docker compose exec postgres psql -U upwork -d upwork -c
   "DELETE FROM orders WHERE status != 'submitted'; DELETE FROM signals;
   DELETE FROM scrape_runs; DELETE FROM agent_runs; DELETE FROM job_skills;
   DELETE FROM job_enrichments; DELETE FROM jobs;"`
3. Have the operator launch Chrome on Upwork and run scheduler.main.
4. Observe what the extractor sees on a REAL panel and what it returns.
5. Diagnose: is the dump too noisy? is the LLM returning nulls? is there a
   field-name mismatch in storage?
6. Fix whichever layer is the culprit. Re-test. Verify a real signal posts
   to Discord with correct Budget / Posted / Client fields.

Do NOT touch the working layers (URL capture, apply executor, login
detection, ui_lock, gpt-4o-mini model, json_object response format) unless
the bug specifically traces to one of them.
```

## DB state at handoff time

```
1 job stored:
  job_id=022050907811848079631
  title='Setup Autonomous AI Agents'
  budget_kind=NULL, budget_min_usd=NULL
  posted_text=NULL
  client_country=NULL
0 enrichments
0 signals
0 orders (the Discord embed shown to operator was from an earlier order
  that pre-dated the LLM extractor wiring; no new orders persisted with
  the extractor in place)
```

## Hypotheses to test (in order of likelihood)

1. **Real element dump is HUGE and noisy** -- 30k char cap may truncate
   the body content; LLM may give up and return mostly nulls. Verify by
   printing `len(raw_text)` and the first/last 1000 chars.
2. **LLM is not actually being called for some reason** -- maybe an
   exception in extract_panel is being silently caught somewhere. Check
   `agent_runs` table after a cycle: is there a `panel_extract` row?
3. **Pydantic validation is failing on real responses** and the second-pass
   `cleaned` dict ends up empty. Add print in the except branch.
4. **`extract_panel` is being called with the wrong `elements` argument**
   -- verify `panel.capture_panel` returns what we think it does.

## Decisions already made (do not re-litigate)

- Use LLM-based extraction (no regex). Operator approved this approach
  explicitly before we built it.
- Empty `mermaid_diagram` -> no diagram + no diagram heading. Generic
  `User -> System -> Result` placeholder is unacceptable.
- `gpt-4o-mini` is the only available model on operator's API key.
- Setup gating is LLM-only (empty `filter_dsl`, prose-driven). Rules layer
  is optional pre-filter, never the gate.
- Cover letter is hardcoded template (legacy formula); only the doc URL is
  substituted. No LLM call for cover letter.
- Cycle-type variation (skim/no-op/panel-skim) is REMOVED -- always
  full_scan. Operator preference.
- Diurnal envelope is "hungry freelancer" pattern (95-100% awake, 5-25%
  sleep window 02:00-11:00 PKT = 21:00-06:00 UTC).

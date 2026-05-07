-- Researcher v1: findings produced by the autonomous research pass.
-- See docs/superpowers/specs/2026-05-05-researcher-design.md §5.3.
--
-- Each row is a specific actionable finding tied to ≥2 evidence jobs.
-- The Researcher loop produces these from ai/agents/researcher.py and
-- the nudge engine (researcher/nudge.py) decides which DM the operator.

CREATE TABLE researcher_findings (
    finding_id        bigserial PRIMARY KEY,
    detected_at       timestamptz NOT NULL DEFAULT now(),

    -- Finding shape (mirrors ai.schemas.JobForensicFinding).
    finding_type      text NOT NULL CHECK (finding_type IN (
        'emerging_template',
        'failure_mode_pattern',
        'tech_combo_emergence',
        'specific_stack_demand',
        'budget_anomaly',
        'geographic_cluster'
    )),
    headline          text NOT NULL,
    why_specific      text NOT NULL,
    portfolio_tie     text NOT NULL,
    suggested_action  text NOT NULL,
    urgency           text NOT NULL CHECK (urgency IN (
        'this_week', 'this_month', 'monitor'
    )),
    evidence_job_ids  text[] NOT NULL,

    -- Audit trail: the raw LLM response, for debugging shallow findings.
    raw_llm_response  jsonb,

    -- Lifecycle:
    --   new -> nudged | dismissed | snoozed
    --   snoozed_until promotes back to 'new' after the date passes.
    -- The status column reflects the CURRENT lifecycle phase; the *_at
    -- columns are timestamps of the last transition into that phase.
    status            text NOT NULL DEFAULT 'new' CHECK (status IN (
        'new', 'nudged', 'dismissed', 'snoozed'
    )),
    nudged_at         timestamptz,
    dismissed_at      timestamptz,
    dismissed_reason  text,
    snoozed_until     timestamptz,

    -- Dedup: same finding (same type + same evidence jobs sorted) must
    -- not fire twice across consecutive Researcher passes. Computed in
    -- application code as sha256(finding_type || sorted(evidence_jobs)).
    -- UNIQUE constraint makes the dedup contract a DB invariant rather
    -- than a soft check, so a race between two passes can't double-insert.
    dedup_key         text NOT NULL UNIQUE
);

-- Operator-surface tools (list_findings) typically want recent rows of
-- specific statuses — index on (status, detected_at desc) covers that.
CREATE INDEX idx_findings_status_detected ON researcher_findings (
    status, detected_at DESC
);

-- Snooze auto-promotion needs a fast scan of snoozed rows whose
-- snoozed_until has passed. Partial index keeps it cheap.
CREATE INDEX idx_findings_snooze_due ON researcher_findings (snoozed_until)
    WHERE status = 'snoozed';

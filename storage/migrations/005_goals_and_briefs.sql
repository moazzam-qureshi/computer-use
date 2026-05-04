-- Phase 2.A: operator goal + briefed scans.

-- Single active goal at a time, enforced via partial unique index.
-- Older goals stay in the table as history with is_active=false.
CREATE TABLE goals (
    goal_id            bigserial PRIMARY KEY,
    is_active          boolean NOT NULL DEFAULT true,
    prose              text NOT NULL,
    target_metric      text,
    target_value       numeric,
    horizon            text,
    min_hourly         numeric,
    min_budget         numeric,
    preferred_country  text,
    notes              text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    deactivated_at     timestamptz
);

-- "At most one active goal" enforced at the DB level.
CREATE UNIQUE INDEX goals_one_active ON goals (is_active) WHERE is_active = true;

-- One row per briefed scan. Bidder consumes pending rows in order; brief-watcher
-- summarizes status='done' rows where notified_at IS NULL.
CREATE TABLE scan_briefs (
    brief_id                       bigserial PRIMARY KEY,
    prose                          text NOT NULL,
    filter_dsl                     jsonb NOT NULL,
    requested_by_conversation_id   bigint REFERENCES assistant_conversations(conversation_id),
    requested_at                   timestamptz NOT NULL DEFAULT now(),
    consumed_at                    timestamptz,
    finished_at                    timestamptz,
    status                         text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'failed')),
    cycle_notes                    text,
    notified_at                    timestamptz,
    result_summary                 jsonb,
    setup_id                       integer REFERENCES setups(setup_id)
);

-- Bidder polls this index path on every iteration.
CREATE INDEX scan_briefs_pending ON scan_briefs (requested_at)
    WHERE status = 'pending';

-- Watcher polls this path every 5 seconds.
CREATE INDEX scan_briefs_to_notify ON scan_briefs (finished_at)
    WHERE status IN ('done', 'failed') AND notified_at IS NULL;

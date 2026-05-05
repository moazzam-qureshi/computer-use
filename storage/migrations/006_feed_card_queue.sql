-- Phase 2.B: feed_card_queue. Pass 1 (detection) inserts queued rows by title;
-- Pass 2 (processing) claims oldest queued, captures URL via panel walk, updates
-- the row, runs goal-relevance + drafting.
--
-- Idempotency:
--   * job_url is nullable at detection time and populated by Pass 2.
--   * Partial unique index on job_title WHERE status IN ('queued','processing')
--     blocks duplicate-title insertion while a row is in flight.
--   * UNIQUE on job_url (when non-null) dedups against future detection passes
--     after the row has been processed.

CREATE TABLE feed_card_queue (
    queue_id           bigserial PRIMARY KEY,
    job_url            text UNIQUE,                    -- nullable; populated by Pass 2
    job_title          text NOT NULL,
    posted_text        text,
    detected_at        timestamptz NOT NULL DEFAULT now(),
    triage_reasoning   text,
    status             text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'processed', 'failed', 'skipped')),
    claimed_at         timestamptz,
    finished_at        timestamptz,
    error_text         text,
    attempt_count      integer NOT NULL DEFAULT 0,
    order_id           bigint REFERENCES orders(order_id),
    goal_id_at_detect  bigint REFERENCES goals(goal_id)
);

-- Pass 2 claim_next reads this index path.
CREATE INDEX feed_card_queue_pending ON feed_card_queue (detected_at)
    WHERE status = 'queued';

-- Block duplicate-title queueing while a row is in flight.
CREATE UNIQUE INDEX feed_card_queue_inflight_title
    ON feed_card_queue (job_title)
    WHERE status IN ('queued', 'processing');

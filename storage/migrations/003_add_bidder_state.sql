-- Single-row table for runtime bidder control. The bidder loop reads this
-- at the top of every iteration and skips its cycle when paused=true. Discord
-- slash commands (/bidder pause, /bidder resume, /bidder run-now) write to
-- this table; the loop polls. No subprocess restart, no IPC, just Postgres.
--
-- Why a table instead of an in-memory flag: the bot and the bidder are in the
-- same process today, but if we ever split them (or run the bot in Docker
-- against a Windows-host bidder), Postgres is the only thing both sides agree
-- on. Build the boring, durable thing now.

CREATE TABLE bidder_state (
  -- Singleton row. The CHECK constraint keeps anyone from inserting a second.
  id integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),

  -- Runtime control flags.
  paused boolean NOT NULL DEFAULT false,
  paused_reason text,
  paused_at timestamptz,

  -- "Run a cycle right now" trigger. The bidder loop checks this and clears
  -- it after starting a forced cycle. Set by /bidder run-now.
  force_run_requested boolean NOT NULL DEFAULT false,
  force_run_requested_at timestamptz,

  -- Diagnostics surfaced by /bidder status.
  last_cycle_started_at timestamptz,
  last_cycle_finished_at timestamptz,
  last_cycle_status text, -- 'succeeded' | 'failed' | 'login_required' | 'paused' | null
  last_cycle_notes text   -- short error message or scrape_run notes
);

-- Seed the singleton row at migration time so the bidder loop can always
-- read state without a "first run" branch.
INSERT INTO bidder_state (id) VALUES (1);

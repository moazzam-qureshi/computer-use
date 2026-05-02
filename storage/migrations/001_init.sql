-- 001_init.sql — initial schema
-- Created: 2026-05-02

CREATE TABLE IF NOT EXISTS schema_migrations (
  version integer PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);

-- ============ CORPUS ============

CREATE TABLE jobs (
  job_id text PRIMARY KEY,                 -- canonical Upwork job id (from URL)
  url text NOT NULL UNIQUE,
  title text NOT NULL,
  description text,
  budget_kind text,                        -- 'fixed' | 'hourly' | NULL if unknown
  budget_min_usd numeric,
  budget_max_usd numeric,
  budget_raw_text text,
  duration text,
  experience_level text,
  hours_per_week text,
  posted_at timestamptz,
  scraped_first_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL,                    -- 'feed' | 'search:<query_term>'
  client_country text,
  client_city text,
  client_member_since text,
  client_payment_verified boolean,
  client_rating numeric,
  client_hires integer,
  client_total_spent_usd numeric,
  client_avg_hourly_paid numeric,
  proposals_count_at_first_scrape integer,
  raw_panel_json jsonb
);

CREATE INDEX idx_jobs_scraped_first_at ON jobs (scraped_first_at DESC);
CREATE INDEX idx_jobs_source ON jobs (source);

CREATE TABLE scrape_events (
  id bigserial PRIMARY KEY,
  job_id text NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  scraped_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL,
  proposals_count integer,
  notes text
);

CREATE INDEX idx_scrape_events_job_id ON scrape_events (job_id);

CREATE TABLE scrape_runs (
  run_id bigserial PRIMARY KEY,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  source text NOT NULL,
  query_id integer,                        -- FK added below
  jobs_seen integer NOT NULL DEFAULT 0,
  jobs_new integer NOT NULL DEFAULT 0,
  jobs_signaled integer NOT NULL DEFAULT 0,
  notes text
);

CREATE TABLE queries (
  query_id serial PRIMARY KEY,
  raw_text text NOT NULL,
  query_term text NOT NULL,
  filters_json jsonb,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE scrape_runs
  ADD CONSTRAINT fk_scrape_runs_query FOREIGN KEY (query_id) REFERENCES queries(query_id);

CREATE TABLE skills (
  skill_id serial PRIMARY KEY,
  name text NOT NULL UNIQUE,
  category text,
  alias_of integer REFERENCES skills(skill_id),
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE job_skills (
  job_id text NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  skill_id integer NOT NULL REFERENCES skills(skill_id),
  source text NOT NULL,                    -- 'upwork_chip' | 'description_extracted' | 'enriched'
  PRIMARY KEY (job_id, skill_id, source)
);

CREATE INDEX idx_job_skills_skill ON job_skills (skill_id);

CREATE TABLE job_enrichments (
  job_id text PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
  prompt_version text NOT NULL,
  enriched_at timestamptz NOT NULL DEFAULT now(),
  extracted_tech text[] NOT NULL DEFAULT '{}',
  pain_points text[] NOT NULL DEFAULT '{}',
  red_flags text[] NOT NULL DEFAULT '{}',
  green_flags text[] NOT NULL DEFAULT '{}',
  project_shape text,
  buyer_sophistication text,
  raw_llm_response jsonb
);

-- ============ STRATEGY ============

CREATE TABLE pitch_templates (
  template_id serial PRIMARY KEY,
  name text NOT NULL,
  kind text NOT NULL,                      -- 'doc' | 'cover_letter' | 'screening_answer'
  prompt_version text NOT NULL,
  body_template text NOT NULL,
  variables jsonb NOT NULL DEFAULT '{}'::jsonb,
  parent_template_id integer REFERENCES pitch_templates(template_id),
  created_at timestamptz NOT NULL DEFAULT now(),
  retired_at timestamptz
);

CREATE TABLE setups (
  setup_id serial PRIMARY KEY,
  name text NOT NULL UNIQUE,
  status text NOT NULL CHECK (status IN ('proposed','active','disabled','retired')),
  tier text NOT NULL CHECK (tier IN ('quiet','normal','critical')),
  filter_dsl jsonb NOT NULL,
  prose_definition text,
  pitch_template_id integer REFERENCES pitch_templates(template_id),
  cover_letter_template_id integer REFERENCES pitch_templates(template_id),
  auto_apply_enabled boolean NOT NULL DEFAULT false,
  escalation_config jsonb NOT NULL DEFAULT '{"initial":0,"repings":[],"deadline":null,"on_deadline":"queue"}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  activated_at timestamptz,
  retired_at timestamptz,
  retired_reason text
);

CREATE TABLE setup_proposals (
  proposal_id serial PRIMARY KEY,
  proposed_at timestamptz NOT NULL DEFAULT now(),
  agent_run_id bigint,                     -- FK added later (after agent_runs exists)
  action text NOT NULL CHECK (action IN ('create','retire','upgrade','enable_auto_apply','update_filter')),
  setup_id integer REFERENCES setups(setup_id),
  draft jsonb NOT NULL,
  reasoning text,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  reviewed_at timestamptz,
  reviewed_action text,
  reviewed_note text
);

CREATE TABLE pitch_proposals (
  proposal_id serial PRIMARY KEY,
  proposed_at timestamptz NOT NULL DEFAULT now(),
  agent_run_id bigint,
  parent_template_id integer NOT NULL REFERENCES pitch_templates(template_id),
  draft_template_body text NOT NULL,
  reasoning text,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
  reviewed_at timestamptz,
  reviewed_action text
);

-- ============ EXECUTION ============

CREATE TABLE signals (
  signal_id bigserial PRIMARY KEY,
  job_id text NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
  fired_at timestamptz NOT NULL DEFAULT now(),
  matched_setups jsonb NOT NULL,           -- [{setup_id, match_reason: 'rule'|'llm', score}]
  primary_setup_id integer NOT NULL REFERENCES setups(setup_id),
  market_state jsonb NOT NULL              -- proposals_at_signal, time_since_post_minutes, ...
);

CREATE INDEX idx_signals_job ON signals (job_id);
CREATE INDEX idx_signals_setup ON signals (primary_setup_id);

CREATE TABLE orders (
  order_id bigserial PRIMARY KEY,
  signal_id bigint NOT NULL REFERENCES signals(signal_id),
  job_id text NOT NULL REFERENCES jobs(job_id),
  setup_id integer NOT NULL REFERENCES setups(setup_id),
  status text NOT NULL CHECK (status IN ('drafting','awaiting_approval','approved','staging','attempting','submitted','cancelled','failed')),
  bid_amount_usd numeric,
  connects_spent integer,
  cover_letter_body text,
  doc_url text,
  screening_answers_json jsonb,
  drafted_at timestamptz,
  approved_at timestamptz,
  submitted_at timestamptz,
  failed_reason text,
  idempotency_key text NOT NULL UNIQUE
);

CREATE INDEX idx_orders_status ON orders (status);
CREATE INDEX idx_orders_setup ON orders (setup_id);
CREATE INDEX idx_orders_submitted_at ON orders (submitted_at DESC);

CREATE TABLE outcome_events (
  id bigserial PRIMARY KEY,
  order_id bigint NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
  event_type text NOT NULL CHECK (event_type IN ('submitted','viewed','replied','interviewed','hired','declined','ghosted')),
  observed_at timestamptz NOT NULL DEFAULT now(),
  source text NOT NULL CHECK (source IN ('discord_manual','upwork_my_proposals_scrape','self_reported')),
  notes text
);

CREATE INDEX idx_outcome_events_order ON outcome_events (order_id);

CREATE TABLE connects_ledger (
  id bigserial PRIMARY KEY,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  delta integer NOT NULL,
  reason text NOT NULL,
  balance_after integer,
  order_id bigint REFERENCES orders(order_id)
);

CREATE INDEX idx_connects_ledger_occurred_at ON connects_ledger (occurred_at DESC);

-- ============ PORTFOLIO ============

CREATE TABLE portfolio_items (
  portfolio_id serial PRIMARY KEY,
  name text NOT NULL,
  summary text,
  client_context text,
  outcome text,
  tech text[] NOT NULL DEFAULT '{}',
  relevance_tags text[] NOT NULL DEFAULT '{}',
  year_completed integer,
  added_at timestamptz NOT NULL DEFAULT now(),
  last_used_at timestamptz
);

-- ============ AUDIT ============

CREATE TABLE agent_runs (
  run_id bigserial PRIMARY KEY,
  agent_name text NOT NULL,
  started_at timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  status text NOT NULL DEFAULT 'running' CHECK (status IN ('running','succeeded','failed')),
  parent_run_id bigint REFERENCES agent_runs(run_id),
  total_tokens integer,
  total_cost_usd numeric,
  trigger text NOT NULL CHECK (trigger IN ('scheduled','discord_question','manual','per_job')),
  trigger_context jsonb,
  steps jsonb NOT NULL DEFAULT '[]'::jsonb,
  output_summary text
);

CREATE INDEX idx_agent_runs_started ON agent_runs (started_at DESC);
CREATE INDEX idx_agent_runs_agent ON agent_runs (agent_name);

ALTER TABLE setup_proposals
  ADD CONSTRAINT fk_setup_proposals_run FOREIGN KEY (agent_run_id) REFERENCES agent_runs(run_id);

ALTER TABLE pitch_proposals
  ADD CONSTRAINT fk_pitch_proposals_run FOREIGN KEY (agent_run_id) REFERENCES agent_runs(run_id);

-- Researcher v1: extend agent_runs.trigger CHECK to allow 'researcher_pass'.
--
-- The original CHECK in 001_init.sql allowed only:
--   'scheduled' | 'discord_question' | 'manual' | 'per_job'
--
-- The Researcher's forensic LLM call is a fifth category — it fires
-- inside an autonomous research pass, distinct from a scheduled bidder
-- cycle. Tagging it correctly matters for cost attribution and audit
-- queries ("show me how much LLM cost the Researcher used last week").

ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS agent_runs_trigger_check;

ALTER TABLE agent_runs ADD CONSTRAINT agent_runs_trigger_check
    CHECK (trigger IN (
        'scheduled',
        'discord_question',
        'manual',
        'per_job',
        'researcher_pass'
    ));

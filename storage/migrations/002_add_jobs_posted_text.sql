-- Add human-readable posted_text to jobs (e.g. '17 minutes ago').
-- The numeric posted_at column stays for future canonical-time use; posted_text
-- is what the LLM extractor returns and what the Discord embed renders.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS posted_text TEXT;

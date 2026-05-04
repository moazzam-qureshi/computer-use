-- Assistant module: conversation memory, audit log, runtime flags.

-- Per-operator conversation state.
CREATE TABLE assistant_conversations (
    conversation_id  bigserial PRIMARY KEY,
    discord_user_id  text NOT NULL UNIQUE,
    summary          text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Append-only message log. archived_at lets summarization soft-delete
-- old messages without losing the audit trail.
CREATE TABLE assistant_messages (
    message_id       bigserial PRIMARY KEY,
    conversation_id  bigint NOT NULL REFERENCES assistant_conversations(conversation_id),
    role             text NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content          text NOT NULL,
    tool_call_id     text,
    tool_name        text,
    archived_at      timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX assistant_messages_conv_recent
    ON assistant_messages (conversation_id, message_id DESC)
    WHERE archived_at IS NULL;

-- Write-tool history with before/after snapshots. revert_last_change reads this.
CREATE TABLE assistant_audit_log (
    audit_id         bigserial PRIMARY KEY,
    conversation_id  bigint REFERENCES assistant_conversations(conversation_id),
    tool_name        text NOT NULL,
    arguments        jsonb NOT NULL,
    result           jsonb NOT NULL,
    before_state     jsonb,
    after_state      jsonb,
    created_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX assistant_audit_conv_recent
    ON assistant_audit_log (conversation_id, audit_id DESC);

-- Global runtime flags. Currently used keys: bidder_paused (bool),
-- connects_daily_cap (int), connects_weekly_cap (int).
CREATE TABLE system_config (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Per-setup additions.
ALTER TABLE setups ADD COLUMN ignored_clients text[] NOT NULL DEFAULT '{}';
ALTER TABLE setups ADD COLUMN tone_override text;

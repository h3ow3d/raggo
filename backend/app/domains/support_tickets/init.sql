-- Support tickets domain schema for raggo
-- Demonstrates a second pluggable domain beyond flights

CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- support_tickets
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS support_tickets (
    id                SERIAL PRIMARY KEY,
    subject           TEXT        NOT NULL,
    priority          TEXT        NOT NULL,  -- low, medium, high, critical
    status            TEXT        NOT NULL,  -- open, in_progress, resolved, closed
    customer          TEXT        NOT NULL,
    opened_at         TIMESTAMPTZ NOT NULL,
    closed_at         TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_support_tickets_priority ON support_tickets (priority);
CREATE INDEX IF NOT EXISTS idx_support_tickets_status   ON support_tickets (status);
CREATE INDEX IF NOT EXISTS idx_support_tickets_customer ON support_tickets (customer);
CREATE INDEX IF NOT EXISTS idx_support_tickets_opened_at ON support_tickets (opened_at);

-- ---------------------------------------------------------------------------
-- ticket_messages
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ticket_messages (
    id                  SERIAL PRIMARY KEY,
    ticket_id           INTEGER     NOT NULL REFERENCES support_tickets (id) ON DELETE CASCADE,
    author              TEXT        NOT NULL,  -- customer, agent, system
    body                TEXT        NOT NULL,
    embedding           vector(384),
    embedding_model     TEXT,
    embedding_dim       INTEGER,
    embedded_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket_id ON ticket_messages (ticket_id);
CREATE INDEX IF NOT EXISTS idx_ticket_messages_author    ON ticket_messages (author);

-- IVFFlat index for vector similarity search
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'idx_ticket_messages_embedding'
    ) THEN
        EXECUTE 'CREATE INDEX idx_ticket_messages_embedding
                 ON ticket_messages USING ivfflat (embedding vector_cosine_ops)
                 WITH (lists = 100)';
    END IF;
END $$;

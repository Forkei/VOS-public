-- VOS SDK Compatible Database Schema
-- This schema supports both the original VOS system and the new SDK requirements

-- Drop existing tables if they exist (for clean rebuild)
DROP TABLE IF EXISTS subscription_evaluations CASCADE;
DROP TABLE IF EXISTS calendar_conflicts CASCADE;
DROP TABLE IF EXISTS notification_subscriptions CASCADE;
DROP TABLE IF EXISTS active_timers CASCADE;
DROP TABLE IF EXISTS reminders CASCADE;
DROP TABLE IF EXISTS calendar_events CASCADE;
DROP TABLE IF EXISTS conversation_messages CASCADE;
DROP TABLE IF EXISTS documents CASCADE;
DROP TABLE IF EXISTS message_history CASCADE;
DROP TABLE IF EXISTS task_assignees CASCADE;
DROP TABLE IF EXISTS tasks CASCADE;
DROP TABLE IF EXISTS agent_state CASCADE;
DROP TABLE IF EXISTS agents CASCADE;
DROP TABLE IF EXISTS pending_notifications CASCADE;
DROP TABLE IF EXISTS users CASCADE;

-- Drop existing types if they exist
DROP TYPE IF EXISTS subscription_status CASCADE;
DROP TYPE IF EXISTS subscription_type CASCADE;
DROP TYPE IF EXISTS timer_status CASCADE;
DROP TYPE IF EXISTS timer_type CASCADE;
DROP TYPE IF EXISTS reminder_status CASCADE;
DROP TYPE IF EXISTS reminder_type CASCADE;
DROP TYPE IF EXISTS task_status CASCADE;
DROP TYPE IF EXISTS task_priority CASCADE;
DROP TYPE IF EXISTS agent_status CASCADE;
DROP TYPE IF EXISTS processing_state CASCADE;
DROP TYPE IF EXISTS message_role CASCADE;
DROP TYPE IF EXISTS message CASCADE;

-- Create ENUM types (matching SDK and API Gateway expectations)
CREATE TYPE task_status AS ENUM ('pending', 'in_progress', 'completed', 'archived');
CREATE TYPE task_priority AS ENUM ('low', 'medium', 'high', 'urgent');
CREATE TYPE agent_status AS ENUM ('active', 'sleeping', 'off'); -- SDK expects these exact values
CREATE TYPE processing_state AS ENUM ('idle', 'thinking', 'executing_tools'); -- SDK expects these exact values
CREATE TYPE message_role AS ENUM ('system', 'user', 'assistant'); -- SDK MessageRole enum values

-- Calendar/Scheduler ENUM types
-- Calendar/Reminder enum types removed - no longer needed

-- ============================================================================
-- USERS TABLE (Authentication)
-- ============================================================================
CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(255),
    is_active BOOLEAN DEFAULT TRUE,
    is_admin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    last_login_at TIMESTAMPTZ,
    failed_login_attempts INTEGER DEFAULT 0,
    locked_until TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'::jsonb,

    -- Constraints
    CONSTRAINT username_min_length CHECK (char_length(username) >= 3),
    CONSTRAINT username_max_length CHECK (char_length(username) <= 255)
);

-- Create indexes for users
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email) WHERE email IS NOT NULL;
CREATE INDEX idx_users_is_active ON users(is_active);
CREATE INDEX idx_users_created_at ON users(created_at);
CREATE INDEX idx_users_last_login_at ON users(last_login_at);

-- Create trigger for updating users timestamp
CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMENT ON TABLE users IS 'User accounts for authentication and authorization';
COMMENT ON COLUMN users.password_hash IS 'Bcrypt hashed password';
COMMENT ON COLUMN users.failed_login_attempts IS 'Counter for rate limiting and account lockout';
COMMENT ON COLUMN users.locked_until IS 'Account locked until this timestamp (NULL if not locked)';

-- Create agent_state table (supports SDK requirements)
CREATE TABLE agent_state (
    agent_id VARCHAR(255) PRIMARY KEY,
    status agent_status DEFAULT 'off',
    processing_state processing_state DEFAULT 'idle',
    total_messages INTEGER DEFAULT 0,
    last_updated TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_error TEXT,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Create indexes for agent_state
CREATE INDEX idx_agent_status ON agent_state(status);
CREATE INDEX idx_processing_state ON agent_state(processing_state);
CREATE INDEX idx_agent_last_updated ON agent_state(last_updated);

-- Create tasks table
CREATE TABLE tasks (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    status task_status DEFAULT 'pending',
    priority task_priority DEFAULT 'medium',
    tags TEXT[],
    metadata JSONB DEFAULT '{}'::jsonb,
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    due_date TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    parent_task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    assigned_agent VARCHAR(255),
    broadcast_updates BOOLEAN DEFAULT false,
    CONSTRAINT fk_assigned_agent FOREIGN KEY (assigned_agent)
        REFERENCES agent_state(agent_id) ON DELETE SET NULL
);

-- Create indexes for tasks
CREATE INDEX idx_task_status ON tasks(status);
CREATE INDEX idx_task_priority ON tasks(priority);
CREATE INDEX idx_task_created_at ON tasks(created_at);
CREATE INDEX idx_task_due_date ON tasks(due_date);
CREATE INDEX idx_task_assigned_agent ON tasks(assigned_agent);
CREATE INDEX idx_task_tags ON tasks USING gin(tags);

-- Create task_assignees junction table (for multiple assignees)
CREATE TABLE task_assignees (
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    agent_id VARCHAR(255) REFERENCES agent_state(agent_id) ON DELETE CASCADE,
    assigned_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (task_id, agent_id)
);

-- Create message_history table
CREATE TABLE message_history (
    id SERIAL PRIMARY KEY,
    agent_id VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL, -- 'user', 'assistant', 'system', 'tool'
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    message_type VARCHAR(100),
    correlation_id VARCHAR(255),
    CONSTRAINT fk_message_agent FOREIGN KEY (agent_id)
        REFERENCES agent_state(agent_id) ON DELETE CASCADE
);

-- Create indexes for message_history
CREATE INDEX idx_message_agent ON message_history(agent_id);
CREATE INDEX idx_message_timestamp ON message_history(timestamp);
CREATE INDEX idx_message_role ON message_history(role);
CREATE INDEX idx_message_correlation ON message_history(correlation_id);

-- Create documents table (for vector store references)
CREATE TABLE documents (
    id SERIAL PRIMARY KEY,
    document_id VARCHAR(255) UNIQUE NOT NULL,
    title VARCHAR(500),
    content TEXT,
    vector_store_id VARCHAR(255),
    creator_agent_id VARCHAR(255),
    tags TEXT[],
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT fk_creator_agent FOREIGN KEY (creator_agent_id)
        REFERENCES agent_state(agent_id) ON DELETE CASCADE
);

-- Create indexes for documents
CREATE INDEX idx_doc_vector_store ON documents(vector_store_id);
CREATE INDEX idx_doc_creator ON documents(creator_agent_id);
CREATE INDEX idx_doc_created_at ON documents(created_at);
CREATE INDEX idx_doc_tags ON documents USING gin(tags);

-- Create voice_messages table first (for foreign key reference)
CREATE TABLE voice_messages (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) NOT NULL,
    audio_file_path VARCHAR(500),
    transcript TEXT NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'agent')),
    duration_ms INTEGER,
    audio_size_bytes INTEGER,
    audio_format VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb,
    CONSTRAINT voice_messages_audio_file_unique UNIQUE(audio_file_path)
);

-- Create indexes for voice_messages
CREATE INDEX idx_voice_messages_session_id ON voice_messages(session_id);
CREATE INDEX idx_voice_messages_role ON voice_messages(role);
CREATE INDEX idx_voice_messages_created_at ON voice_messages(created_at DESC);

-- Create conversation_messages table (for user-agent conversations)
CREATE TABLE conversation_messages (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,
    sender_type VARCHAR(20) NOT NULL,  -- 'user' or 'agent'
    sender_id VARCHAR(255),  -- NULL for user messages, agent_id for agent messages
    content TEXT NOT NULL,
    timestamp TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb,
    input_mode VARCHAR(20) DEFAULT 'text' CHECK (input_mode IN ('text', 'voice')),
    voice_metadata JSONB DEFAULT '{}'::jsonb,
    voice_message_id INTEGER REFERENCES voice_messages(id) ON DELETE SET NULL,
    CONSTRAINT valid_sender_type CHECK (sender_type IN ('user', 'agent'))
);

-- Create indexes for conversation_messages
CREATE INDEX idx_conversation_session ON conversation_messages(session_id);
CREATE INDEX idx_conversation_timestamp ON conversation_messages(timestamp);
CREATE INDEX idx_conversation_sender ON conversation_messages(sender_id);
CREATE INDEX idx_conversation_session_timestamp ON conversation_messages(session_id, timestamp);
CREATE INDEX idx_conversation_messages_input_mode ON conversation_messages(input_mode);
CREATE INDEX idx_conversation_messages_voice_message_id ON conversation_messages(voice_message_id);

-- Create function to update timestamps
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    -- Check which column exists and update accordingly
    IF TG_TABLE_NAME = 'agent_state' THEN
        NEW.last_updated = NOW();
    ELSE
        NEW.updated_at = NOW();
    END IF;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create triggers for updating timestamps
CREATE TRIGGER update_agent_state_updated_at BEFORE UPDATE ON agent_state
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_tasks_updated_at BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_documents_updated_at BEFORE UPDATE ON documents
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Create pending_notifications table for WebSocket delivery tracking
CREATE TABLE IF NOT EXISTS pending_notifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id VARCHAR(255) NOT NULL,
    notification_id UUID NOT NULL,
    notification_type VARCHAR(50) NOT NULL,
    notification_payload JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    delivered_at TIMESTAMPTZ,
    delivery_attempts INTEGER DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,

    -- For deduplication and efficient queries
    CONSTRAINT unique_notification UNIQUE (notification_id)
);

-- Create indexes for efficient pending notification queries
CREATE INDEX IF NOT EXISTS idx_pending_session ON pending_notifications(session_id) WHERE delivered_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pending_created ON pending_notifications(created_at) WHERE delivered_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_pending_delivery_attempts ON pending_notifications(delivery_attempts) WHERE delivered_at IS NULL;

-- Create function to clean up old delivered notifications (older than 7 days)
CREATE OR REPLACE FUNCTION cleanup_old_notifications()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM pending_notifications
    WHERE delivered_at IS NOT NULL
    AND delivered_at < NOW() - INTERVAL '7 days';

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON TABLE pending_notifications IS 'Tracks WebSocket notifications for guaranteed delivery to disconnected clients';
COMMENT ON COLUMN pending_notifications.notification_id IS 'Unique notification ID for deduplication';
COMMENT ON COLUMN pending_notifications.session_id IS 'Target session for routing';
COMMENT ON COLUMN pending_notifications.delivery_attempts IS 'Number of failed delivery attempts';

-- ============================================================================
-- CALENDAR EVENTS
-- ============================================================================
CREATE TABLE calendar_events (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    all_day BOOLEAN DEFAULT false,
    location VARCHAR(255),

    -- Virtual recurrence (max 100 instances)
    recurrence_rule TEXT,  -- iCalendar RRULE format
    exception_dates JSONB DEFAULT '[]'::jsonb,  -- Array of ISO date strings to skip

    -- Auto-reminders (minutes before event start)
    auto_reminders JSONB DEFAULT '[]'::jsonb,  -- e.g., [15, 60, 1440]

    -- Metadata
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_calendar_start_time ON calendar_events(start_time);
CREATE INDEX idx_calendar_end_time ON calendar_events(end_time);
CREATE INDEX idx_calendar_recurrence ON calendar_events(recurrence_rule) WHERE recurrence_rule IS NOT NULL;

CREATE TRIGGER update_calendar_events_updated_at BEFORE UPDATE ON calendar_events
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- REMINDERS
-- ============================================================================
CREATE TABLE reminders (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255),  -- Optional
    description TEXT,    -- Optional
    trigger_time TIMESTAMPTZ NOT NULL,

    -- Event attachment (optional, for event-attached reminders)
    event_id INTEGER REFERENCES calendar_events(id) ON DELETE CASCADE,

    -- Recurrence (for standalone reminders only)
    recurrence_rule TEXT,  -- iCalendar RRULE format
    exception_dates JSONB DEFAULT '[]'::jsonb,

    -- Notification targets
    target_agents TEXT[] DEFAULT ARRAY['primary_agent'],

    -- Metadata
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_reminders_trigger_time ON reminders(trigger_time);
CREATE INDEX idx_reminders_event ON reminders(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX idx_reminders_recurrence ON reminders(recurrence_rule) WHERE recurrence_rule IS NOT NULL;

-- ============================================================================
-- NOTES TABLES
-- ============================================================================

CREATE TABLE notes (
    id SERIAL PRIMARY KEY,
    title VARCHAR(500) NOT NULL,
    content TEXT,
    tags TEXT[],
    folder VARCHAR(255),

    -- GCS storage reference (if content is stored in GCS)
    gcs_bucket VARCHAR(255),
    gcs_path VARCHAR(1000),

    -- Content metadata
    content_type VARCHAR(50) DEFAULT 'text/plain',  -- text/plain, text/markdown, text/html
    content_length INTEGER DEFAULT 0,

    -- Search and organization
    is_pinned BOOLEAN DEFAULT false,
    is_archived BOOLEAN DEFAULT false,
    color VARCHAR(20),  -- For UI color coding

    -- Metadata
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_notes_created_by ON notes(created_by);
CREATE INDEX idx_notes_folder ON notes(folder);
CREATE INDEX idx_notes_tags ON notes USING GIN(tags);
CREATE INDEX idx_notes_created_at ON notes(created_at DESC);
CREATE INDEX idx_notes_updated_at ON notes(updated_at DESC);
CREATE INDEX idx_notes_pinned ON notes(is_pinned) WHERE is_pinned = true;
CREATE INDEX idx_notes_archived ON notes(is_archived) WHERE is_archived = false;
CREATE INDEX idx_notes_title_search ON notes USING GIN(to_tsvector('english', title));
CREATE INDEX idx_notes_content_search ON notes USING GIN(to_tsvector('english', content));

CREATE TRIGGER update_notes_updated_at BEFORE UPDATE ON notes
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================================================
-- ATTACHMENTS TABLE (Images and file uploads)
-- ============================================================================

CREATE TABLE attachments (
    id SERIAL PRIMARY KEY,
    attachment_id VARCHAR(255) UNIQUE NOT NULL,  -- UUID for external reference
    session_id VARCHAR(255) NOT NULL,

    -- File info
    attachment_type VARCHAR(50) NOT NULL,  -- 'image', 'file'
    original_filename VARCHAR(500),
    content_type VARCHAR(100) NOT NULL,  -- image/png, image/jpeg, etc.
    file_size_bytes INTEGER NOT NULL,

    -- Storage
    storage_path VARCHAR(1000) NOT NULL,  -- GCS or local path

    -- Image-specific metadata
    width INTEGER,
    height INTEGER,

    -- Ownership
    created_by VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_attachments_attachment_id ON attachments(attachment_id);
CREATE INDEX idx_attachments_session_id ON attachments(session_id);
CREATE INDEX idx_attachments_created_at ON attachments(created_at DESC);
CREATE INDEX idx_attachments_type ON attachments(attachment_type);

COMMENT ON TABLE attachments IS 'File attachments for messages (images, documents for vision analysis)';

-- ============================================================================
-- ENHANCE DOCUMENTS TABLE (Add missing columns for document system)
-- ============================================================================

-- Add columns if they don't exist (idempotent migrations)
DO $$
BEGIN
    -- Content storage columns
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='original_filename') THEN
        ALTER TABLE documents ADD COLUMN original_filename VARCHAR(500);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='content_type') THEN
        ALTER TABLE documents ADD COLUMN content_type VARCHAR(100) DEFAULT 'text/plain';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='file_size_bytes') THEN
        ALTER TABLE documents ADD COLUMN file_size_bytes INTEGER;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='gcs_bucket') THEN
        ALTER TABLE documents ADD COLUMN gcs_bucket VARCHAR(255);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='gcs_path') THEN
        ALTER TABLE documents ADD COLUMN gcs_path VARCHAR(1000);
    END IF;

    -- Source tracking columns
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='source_type') THEN
        ALTER TABLE documents ADD COLUMN source_type VARCHAR(50);  -- 'agent', 'user_upload', 'tool_result', 'note_export'
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='source_agent_id') THEN
        ALTER TABLE documents ADD COLUMN source_agent_id VARCHAR(255);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='source_tool') THEN
        ALTER TABLE documents ADD COLUMN source_tool VARCHAR(255);
    END IF;

    -- Session association
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='documents' AND column_name='session_id') THEN
        ALTER TABLE documents ADD COLUMN session_id VARCHAR(255);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(session_id);
CREATE INDEX IF NOT EXISTS idx_documents_source_type ON documents(source_type);
CREATE INDEX IF NOT EXISTS idx_documents_source_agent ON documents(source_agent_id);

COMMENT ON TABLE documents IS 'Lightweight document references for efficient data piping between agents';

-- ============================================================================

-- Insert default agent states
INSERT INTO agent_state (agent_id, status, processing_state) VALUES
    ('primary_agent', 'off', 'idle'),
    ('weather_agent', 'off', 'idle'),
    ('search_agent', 'off', 'idle'),
    ('calendar_agent', 'off', 'idle'),
    ('notes_agent', 'off', 'idle')
ON CONFLICT (agent_id) DO NOTHING;

-- Insert default users (passwords will be hashed by migration script)
-- Note: These are placeholder entries that should be replaced by the migration script
-- Default password for 'admin' is 'admin123'
-- Default password for 'user1' is 'password1'
INSERT INTO users (username, email, password_hash, full_name, is_admin) VALUES
    ('admin', 'admin@vos.local', '$2b$12$placeholder_will_be_replaced_by_migration', 'VOS Administrator', true),
    ('user1', 'user1@vos.local', '$2b$12$placeholder_will_be_replaced_by_migration', 'VOS User', false)
ON CONFLICT (username) DO NOTHING;

-- Grant permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO vos_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO vos_user;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO vos_user;
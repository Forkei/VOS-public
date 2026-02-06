-- Call System Schema
-- Voice call functionality for VOS agents

-- ============================================================================
-- ENUM TYPES
-- ============================================================================

-- Drop existing types if they exist (for clean rebuild)
DROP TYPE IF EXISTS call_status CASCADE;
DROP TYPE IF EXISTS call_end_reason CASCADE;
DROP TYPE IF EXISTS call_participant_role CASCADE;

-- Call lifecycle states
CREATE TYPE call_status AS ENUM (
    'ringing_outbound',   -- User calling agent, waiting for answer
    'ringing_inbound',    -- Agent calling user, waiting for accept
    'connected',          -- Active call, bidirectional audio
    'on_hold',            -- Call paused (e.g., app backgrounded)
    'transferring',       -- Handoff to another agent in progress
    'ended'               -- Call terminated
);

-- Reasons for call termination
CREATE TYPE call_end_reason AS ENUM (
    'user_hangup',        -- User ended the call
    'agent_hangup',       -- Agent ended the call
    'user_declined',      -- User declined incoming call
    'agent_declined',     -- Agent declined incoming call
    'transfer_complete',  -- Call transferred to another agent
    'timeout',            -- Ringing timeout, no answer
    'error',              -- Technical error
    'disconnected'        -- Connection lost
);

-- Role of participant in call
CREATE TYPE call_participant_role AS ENUM (
    'initiator',          -- Started the call
    'receiver',           -- Received the call
    'transferred',        -- Received via transfer
    'conferenced'         -- Added to conference (future)
);

-- ============================================================================
-- CALLS TABLE
-- ============================================================================

CREATE TABLE calls (
    call_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id VARCHAR(255) NOT NULL,

    -- Call initiation
    initiated_by VARCHAR(255) NOT NULL,  -- 'user' or agent_id
    initial_target VARCHAR(255) NOT NULL,  -- Target agent (e.g., 'primary_agent')

    -- Current state
    current_agent_id VARCHAR(255) NOT NULL,  -- Agent currently handling the call
    call_status call_status NOT NULL DEFAULT 'ringing_outbound',

    -- Timestamps
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ringing_at TIMESTAMPTZ DEFAULT NOW(),
    connected_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,

    -- Termination info
    end_reason call_end_reason,
    ended_by VARCHAR(255),  -- 'user' or agent_id

    -- Call metadata
    metadata JSONB DEFAULT '{}'::jsonb,

    -- Indexes will be created separately
    CONSTRAINT valid_initiator CHECK (initiated_by = 'user' OR initiated_by ~ '^[a-z_]+_agent$')
);

-- Create indexes for calls
CREATE INDEX idx_calls_session ON calls(session_id);
CREATE INDEX idx_calls_status ON calls(call_status) WHERE call_status NOT IN ('ended');
CREATE INDEX idx_calls_current_agent ON calls(current_agent_id) WHERE call_status NOT IN ('ended');
CREATE INDEX idx_calls_started_at ON calls(started_at DESC);
CREATE INDEX idx_calls_active ON calls(session_id, call_status) WHERE call_status NOT IN ('ended');

COMMENT ON TABLE calls IS 'Voice calls between users and agents';
COMMENT ON COLUMN calls.session_id IS 'User session for the call';
COMMENT ON COLUMN calls.current_agent_id IS 'Agent currently handling the call (changes on transfer)';

-- ============================================================================
-- CALL PARTICIPANTS TABLE (Tracks agent involvement history)
-- ============================================================================

CREATE TABLE call_participants (
    id SERIAL PRIMARY KEY,
    call_id UUID NOT NULL REFERENCES calls(call_id) ON DELETE CASCADE,
    agent_id VARCHAR(255) NOT NULL,

    -- Participation details
    role call_participant_role NOT NULL,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    left_at TIMESTAMPTZ,

    -- Transfer metadata
    transferred_from VARCHAR(255),  -- Agent who transferred the call
    transfer_reason TEXT,           -- Why the transfer happened

    -- Participation stats
    messages_sent INTEGER DEFAULT 0,
    speak_count INTEGER DEFAULT 0,  -- Number of times agent used speak tool

    metadata JSONB DEFAULT '{}'::jsonb
);

-- Create indexes for call_participants
CREATE INDEX idx_call_participants_call ON call_participants(call_id);
CREATE INDEX idx_call_participants_agent ON call_participants(agent_id);
CREATE INDEX idx_call_participants_active ON call_participants(call_id, agent_id) WHERE left_at IS NULL;

COMMENT ON TABLE call_participants IS 'History of agents participating in each call';
COMMENT ON COLUMN call_participants.role IS 'How the agent joined the call';

-- ============================================================================
-- CALL EVENTS TABLE (Audit log of call state changes)
-- ============================================================================

CREATE TABLE call_events (
    id SERIAL PRIMARY KEY,
    call_id UUID NOT NULL REFERENCES calls(call_id) ON DELETE CASCADE,

    -- Event details
    event_type VARCHAR(50) NOT NULL,  -- 'ringing', 'connected', 'hold', 'transfer', 'ended', 'speak', etc.
    event_data JSONB DEFAULT '{}'::jsonb,

    -- Actor
    triggered_by VARCHAR(255),  -- 'user', 'system', or agent_id

    -- Timestamp
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Create indexes for call_events
CREATE INDEX idx_call_events_call ON call_events(call_id);
CREATE INDEX idx_call_events_type ON call_events(event_type);
CREATE INDEX idx_call_events_created ON call_events(created_at DESC);

COMMENT ON TABLE call_events IS 'Audit log of all call state changes and actions';

-- ============================================================================
-- CALL TRANSCRIPTS TABLE (For call history and search)
-- ============================================================================

CREATE TABLE call_transcripts (
    id SERIAL PRIMARY KEY,
    call_id UUID NOT NULL REFERENCES calls(call_id) ON DELETE CASCADE,

    -- Speaker info
    speaker_type VARCHAR(20) NOT NULL,  -- 'user' or 'agent'
    speaker_id VARCHAR(255),            -- NULL for user, agent_id for agent

    -- Content
    content TEXT NOT NULL,

    -- Audio reference (optional)
    audio_file_path VARCHAR(500),
    audio_duration_ms INTEGER,

    -- Timing
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- STT metadata for user speech
    stt_confidence FLOAT,

    metadata JSONB DEFAULT '{}'::jsonb
);

-- Create indexes for call_transcripts
CREATE INDEX idx_call_transcripts_call ON call_transcripts(call_id);
CREATE INDEX idx_call_transcripts_timestamp ON call_transcripts(call_id, timestamp);
CREATE INDEX idx_call_transcripts_speaker ON call_transcripts(speaker_type, speaker_id);
CREATE INDEX idx_call_transcripts_search ON call_transcripts USING GIN(to_tsvector('english', content));

COMMENT ON TABLE call_transcripts IS 'Transcription of speech during calls';

-- ============================================================================
-- HELPER FUNCTIONS
-- ============================================================================

-- Function to get active call for a session
CREATE OR REPLACE FUNCTION get_active_call(p_session_id VARCHAR)
RETURNS TABLE (
    call_id UUID,
    current_agent_id VARCHAR,
    call_status call_status,
    started_at TIMESTAMPTZ,
    connected_at TIMESTAMPTZ
) AS $$
BEGIN
    RETURN QUERY
    SELECT c.call_id, c.current_agent_id, c.call_status, c.started_at, c.connected_at
    FROM calls c
    WHERE c.session_id = p_session_id
    AND c.call_status NOT IN ('ended')
    ORDER BY c.started_at DESC
    LIMIT 1;
END;
$$ LANGUAGE plpgsql;

-- Function to calculate call duration
CREATE OR REPLACE FUNCTION get_call_duration(p_call_id UUID)
RETURNS INTEGER AS $$
DECLARE
    v_connected_at TIMESTAMPTZ;
    v_ended_at TIMESTAMPTZ;
BEGIN
    SELECT connected_at, ended_at INTO v_connected_at, v_ended_at
    FROM calls WHERE call_id = p_call_id;

    IF v_connected_at IS NULL THEN
        RETURN 0;
    END IF;

    IF v_ended_at IS NULL THEN
        v_ended_at := NOW();
    END IF;

    RETURN EXTRACT(EPOCH FROM (v_ended_at - v_connected_at))::INTEGER;
END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- GRANT PERMISSIONS
-- ============================================================================

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO vos_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO vos_user;
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO vos_user;

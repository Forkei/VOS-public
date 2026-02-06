-- Voice Mode Database Schema
-- Simplified voice messages architecture

-- Update existing conversation_messages table to support voice
ALTER TABLE conversation_messages
ADD COLUMN IF NOT EXISTS input_mode VARCHAR(20) DEFAULT 'text'
    CHECK (input_mode IN ('text', 'voice'));

ALTER TABLE conversation_messages
ADD COLUMN IF NOT EXISTS voice_metadata JSONB DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_conversation_messages_input_mode
ON conversation_messages(input_mode);

-- Create voice_messages table for storing all audio messages
CREATE TABLE IF NOT EXISTS voice_messages (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(100) NOT NULL,
    audio_file_path VARCHAR(500),           -- e.g., "agent_responses/session_123/vm_456.mp3" (NULL until file saved)
    transcript TEXT NOT NULL,               -- Text content of the voice message
    role VARCHAR(20) NOT NULL               -- 'user' or 'agent'
        CHECK (role IN ('user', 'agent')),
    duration_ms INTEGER,                    -- Audio duration in milliseconds
    audio_size_bytes INTEGER,               -- File size in bytes
    audio_format VARCHAR(20),               -- 'mp3', 'webm', etc.
    created_at TIMESTAMP DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb,     -- Additional audio metadata (confidence, voice_id, etc.)

    -- Indexes
    CONSTRAINT voice_messages_audio_file_unique UNIQUE(audio_file_path)  -- NULL values allowed (don't violate unique)
);

-- Create indexes for voice_messages
CREATE INDEX IF NOT EXISTS idx_voice_messages_session_id
ON voice_messages(session_id);

CREATE INDEX IF NOT EXISTS idx_voice_messages_role
ON voice_messages(role);

CREATE INDEX IF NOT EXISTS idx_voice_messages_created_at
ON voice_messages(created_at DESC);

-- Add voice_message_id to conversation_messages
ALTER TABLE conversation_messages
ADD COLUMN IF NOT EXISTS voice_message_id INTEGER REFERENCES voice_messages(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_conversation_messages_voice_message_id
ON conversation_messages(voice_message_id);

-- Grant permissions
GRANT ALL PRIVILEGES ON TABLE voice_messages TO postgres;
GRANT ALL PRIVILEGES ON SEQUENCE voice_messages_id_seq TO postgres;

-- Comments for documentation
COMMENT ON TABLE voice_messages IS 'Stores all voice/audio messages with their audio files and transcripts';
COMMENT ON COLUMN conversation_messages.input_mode IS 'Input mode: text (keyboard) or voice (speech)';
COMMENT ON COLUMN conversation_messages.voice_metadata IS 'Voice-specific metadata (transcription confidence, audio duration, etc.)';
COMMENT ON COLUMN conversation_messages.voice_message_id IS 'Reference to voice_messages table if this message has audio';

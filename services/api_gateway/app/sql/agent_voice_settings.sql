-- Agent Voice Settings Database Schema
--
-- Stores per-user, per-agent voice preferences for TTS.
-- Allows each agent to have a unique voice when speaking during calls.
--
-- Created: 2026-01-03

-- =============================================================================
-- Agent Voice Settings
-- =============================================================================
-- Per-user voice preferences for each agent.
-- Each user can customize which voice each agent uses.

CREATE TABLE IF NOT EXISTS agent_voice_settings (
    id SERIAL PRIMARY KEY,

    -- User this setting belongs to (from sessions table)
    user_id VARCHAR(255) NOT NULL,

    -- Agent this setting applies to
    agent_id VARCHAR(255) NOT NULL,

    -- TTS provider: 'elevenlabs' or 'cartesia'
    tts_provider VARCHAR(50) NOT NULL DEFAULT 'elevenlabs',

    -- Voice ID for the TTS provider
    voice_id VARCHAR(255) NOT NULL,

    -- Display name for the voice (for UI)
    voice_name VARCHAR(255),

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Ensure unique setting per user per agent
    UNIQUE(user_id, agent_id)
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_agent_voice_settings_user ON agent_voice_settings(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_voice_settings_agent ON agent_voice_settings(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_voice_settings_user_agent ON agent_voice_settings(user_id, agent_id);


-- =============================================================================
-- Default Agent Voices
-- =============================================================================
-- System-wide default voices for each agent.
-- Used when user hasn't set a custom voice preference.

CREATE TABLE IF NOT EXISTS agent_default_voices (
    id SERIAL PRIMARY KEY,

    -- Agent this default applies to
    agent_id VARCHAR(255) UNIQUE NOT NULL,

    -- TTS provider
    tts_provider VARCHAR(50) NOT NULL DEFAULT 'elevenlabs',

    -- Default voice ID
    voice_id VARCHAR(255) NOT NULL,

    -- Display name for the voice
    voice_name VARCHAR(255),

    -- Description of why this voice was chosen for this agent
    description TEXT,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create index
CREATE INDEX IF NOT EXISTS idx_agent_default_voices_agent ON agent_default_voices(agent_id);


-- =============================================================================
-- Helper Functions
-- =============================================================================

-- Function to automatically update updated_at timestamp
CREATE OR REPLACE FUNCTION update_agent_voice_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for agent_voice_settings
DROP TRIGGER IF EXISTS update_agent_voice_settings_updated_at ON agent_voice_settings;
CREATE TRIGGER update_agent_voice_settings_updated_at
    BEFORE UPDATE ON agent_voice_settings
    FOR EACH ROW
    EXECUTE FUNCTION update_agent_voice_updated_at();

-- Trigger for agent_default_voices
DROP TRIGGER IF EXISTS update_agent_default_voices_updated_at ON agent_default_voices;
CREATE TRIGGER update_agent_default_voices_updated_at
    BEFORE UPDATE ON agent_default_voices
    FOR EACH ROW
    EXECUTE FUNCTION update_agent_voice_updated_at();


-- =============================================================================
-- Initial Data: Default Agent Voices
-- =============================================================================
-- Set up distinct default voices for each agent
-- Using ElevenLabs voices as defaults

INSERT INTO agent_default_voices (agent_id, tts_provider, voice_id, voice_name, description)
VALUES
    -- Primary agent - friendly, conversational female voice
    ('primary_agent', 'elevenlabs', '21m00Tcm4TlvDq8ikWAM', 'Rachel',
     'Warm and friendly voice for the main assistant'),

    -- Weather agent - clear, professional male voice
    ('weather_agent', 'elevenlabs', 'ErXwobaYiN019PkySvjV', 'Antoni',
     'Clear and professional for weather reports'),

    -- Calendar agent - organized, efficient female voice
    ('calendar_agent', 'elevenlabs', 'EXAVITQu4vr4xnSDxMaL', 'Sarah',
     'Efficient and organized for scheduling'),

    -- Notes agent - calm, thoughtful female voice
    ('notes_agent', 'elevenlabs', 'MF3mGyEYCl7XYWbV9V6O', 'Elli',
     'Calm and thoughtful for note-taking'),

    -- Calculator agent - precise, analytical male voice
    ('calculator_agent', 'elevenlabs', 'VR6AewLTigWG4xSOukaG', 'Arnold',
     'Precise and clear for calculations'),

    -- Search agent - knowledgeable, informative male voice
    ('search_agent', 'elevenlabs', 'pNInz6obpgDQGcFmaJgB', 'Adam',
     'Knowledgeable and informative for research'),

    -- Browser agent - tech-savvy, modern male voice
    ('browser_agent', 'elevenlabs', 'yoZ06aMxZJJ28mfd3POQ', 'Sam',
     'Tech-savvy voice for web browsing')

ON CONFLICT (agent_id) DO UPDATE SET
    tts_provider = EXCLUDED.tts_provider,
    voice_id = EXCLUDED.voice_id,
    voice_name = EXCLUDED.voice_name,
    description = EXCLUDED.description,
    updated_at = NOW();


-- =============================================================================
-- View for Easy Voice Lookup
-- =============================================================================
-- Gets the effective voice for a user+agent combination
-- Falls back to default if no user preference exists

CREATE OR REPLACE VIEW effective_agent_voices AS
SELECT
    COALESCE(avs.user_id, 'default') as user_id,
    COALESCE(avs.agent_id, adv.agent_id) as agent_id,
    COALESCE(avs.tts_provider, adv.tts_provider) as tts_provider,
    COALESCE(avs.voice_id, adv.voice_id) as voice_id,
    COALESCE(avs.voice_name, adv.voice_name) as voice_name,
    CASE WHEN avs.id IS NOT NULL THEN true ELSE false END as is_custom
FROM agent_default_voices adv
LEFT JOIN agent_voice_settings avs ON adv.agent_id = avs.agent_id;


-- Helpful comments
COMMENT ON TABLE agent_voice_settings IS 'Per-user voice preferences for each agent';
COMMENT ON TABLE agent_default_voices IS 'System-wide default voices for each agent';
COMMENT ON VIEW effective_agent_voices IS 'Effective voice for each agent (user preference or default)';

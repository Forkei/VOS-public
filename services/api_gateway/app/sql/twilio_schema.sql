-- Twilio Phone Integration Schema
-- Adds phone call support via Twilio to the VOS call system

-- ============================================================================
-- ALLOWED PHONE NUMBERS TABLE (Whitelist)
-- ============================================================================

CREATE TABLE IF NOT EXISTS allowed_phone_numbers (
    id SERIAL PRIMARY KEY,

    -- Phone number in E.164 format (+1234567890)
    phone_number VARCHAR(20) UNIQUE NOT NULL,

    -- Optional display name for the caller
    display_name VARCHAR(100),

    -- Optional link to VOS user account (references users.id)
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,

    -- Active/inactive flag (soft delete)
    is_active BOOLEAN DEFAULT true,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),

    -- Additional metadata (e.g., notes, preferences)
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Create indexes for allowed_phone_numbers
CREATE INDEX IF NOT EXISTS idx_allowed_phone_numbers_phone ON allowed_phone_numbers(phone_number);
CREATE INDEX IF NOT EXISTS idx_allowed_phone_numbers_user ON allowed_phone_numbers(user_id) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_allowed_phone_numbers_active ON allowed_phone_numbers(is_active) WHERE is_active = true;

COMMENT ON TABLE allowed_phone_numbers IS 'Whitelist of phone numbers allowed to call the VOS Twilio number';
COMMENT ON COLUMN allowed_phone_numbers.phone_number IS 'E.164 format phone number (+1234567890)';
COMMENT ON COLUMN allowed_phone_numbers.user_id IS 'Optional link to VOS user for personalization';

-- ============================================================================
-- EXTEND CALLS TABLE FOR TWILIO
-- ============================================================================

-- Add Twilio-specific columns to calls table
ALTER TABLE calls ADD COLUMN IF NOT EXISTS twilio_call_sid VARCHAR(50);
ALTER TABLE calls ADD COLUMN IF NOT EXISTS caller_phone_number VARCHAR(20);
ALTER TABLE calls ADD COLUMN IF NOT EXISTS call_source VARCHAR(20) DEFAULT 'web';

-- Add comment for new columns
COMMENT ON COLUMN calls.twilio_call_sid IS 'Twilio Call SID for phone calls';
COMMENT ON COLUMN calls.caller_phone_number IS 'Phone number of caller (for Twilio calls)';
COMMENT ON COLUMN calls.call_source IS 'Source of call: web, twilio_inbound, twilio_outbound';

-- Create index for Twilio call SID lookups
CREATE INDEX IF NOT EXISTS idx_calls_twilio_sid ON calls(twilio_call_sid) WHERE twilio_call_sid IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_calls_call_source ON calls(call_source);

-- ============================================================================
-- CALL SOURCE VALIDATION
-- ============================================================================

-- Add constraint to validate call_source values
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'valid_call_source'
    ) THEN
        ALTER TABLE calls ADD CONSTRAINT valid_call_source
            CHECK (call_source IN ('web', 'twilio_inbound', 'twilio_outbound'));
    END IF;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ============================================================================
-- HELPER FUNCTIONS FOR TWILIO
-- ============================================================================

-- Function to check if a phone number is allowed
CREATE OR REPLACE FUNCTION is_phone_allowed(p_phone_number VARCHAR)
RETURNS BOOLEAN AS $$
BEGIN
    RETURN EXISTS(
        SELECT 1 FROM allowed_phone_numbers
        WHERE phone_number = p_phone_number
        AND is_active = true
    );
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION is_phone_allowed IS 'Check if a phone number is in the whitelist';

-- Function to get call by Twilio SID
CREATE OR REPLACE FUNCTION get_call_by_twilio_sid(p_twilio_call_sid VARCHAR)
RETURNS TABLE (
    call_id UUID,
    session_id VARCHAR,
    current_agent_id VARCHAR,
    call_status call_status,
    caller_phone_number VARCHAR,
    call_source VARCHAR
) AS $$
BEGIN
    RETURN QUERY
    SELECT c.call_id, c.session_id, c.current_agent_id, c.call_status,
           c.caller_phone_number, c.call_source
    FROM calls c
    WHERE c.twilio_call_sid = p_twilio_call_sid;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_call_by_twilio_sid IS 'Look up a call by its Twilio Call SID';

-- Function to get caller info from whitelist
CREATE OR REPLACE FUNCTION get_allowed_number_info(p_phone_number VARCHAR)
RETURNS TABLE (
    id INTEGER,
    phone_number VARCHAR,
    display_name VARCHAR,
    user_id INTEGER,
    metadata JSONB
) AS $$
BEGIN
    RETURN QUERY
    SELECT a.id, a.phone_number::VARCHAR, a.display_name::VARCHAR,
           a.user_id, a.metadata
    FROM allowed_phone_numbers a
    WHERE a.phone_number = p_phone_number
    AND a.is_active = true;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION get_allowed_number_info IS 'Get details about an allowed phone number';

-- ============================================================================
-- TRIGGER FOR UPDATED_AT
-- ============================================================================

-- Trigger to auto-update updated_at on allowed_phone_numbers
CREATE OR REPLACE FUNCTION update_allowed_phone_numbers_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS allowed_phone_numbers_updated_at ON allowed_phone_numbers;
CREATE TRIGGER allowed_phone_numbers_updated_at
    BEFORE UPDATE ON allowed_phone_numbers
    FOR EACH ROW
    EXECUTE FUNCTION update_allowed_phone_numbers_timestamp();

-- ============================================================================
-- SAMPLE DATA (for testing - can be removed in production)
-- ============================================================================

-- Insert a sample allowed number for testing (disabled by default)
-- UNCOMMENT TO ADD TEST DATA:
-- INSERT INTO allowed_phone_numbers (phone_number, display_name, metadata)
-- VALUES ('+15551234567', 'Test User', '{"notes": "Test number for development"}')
-- ON CONFLICT (phone_number) DO NOTHING;

-- ============================================================================
-- GRANT PERMISSIONS
-- ============================================================================

GRANT ALL PRIVILEGES ON TABLE allowed_phone_numbers TO vos_user;
GRANT USAGE, SELECT ON SEQUENCE allowed_phone_numbers_id_seq TO vos_user;
GRANT EXECUTE ON FUNCTION is_phone_allowed TO vos_user;
GRANT EXECUTE ON FUNCTION get_call_by_twilio_sid TO vos_user;
GRANT EXECUTE ON FUNCTION get_allowed_number_info TO vos_user;

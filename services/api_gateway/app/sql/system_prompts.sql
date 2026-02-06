-- System Prompts Database Schema
--
-- Provides database-first system prompt management with:
-- - Reusable prompt sections (identity, guidelines, context, etc.)
-- - Per-agent prompts with version tracking
-- - Rollback capability
-- - API-editable prompts for future web UI
--
-- Created: 2026-01-01

-- =============================================================================
-- Prompt Sections
-- =============================================================================
-- Reusable prompt sections that can be included in multiple system prompts.
-- Sections can be global (available to all agents) or agent-specific.

CREATE TABLE IF NOT EXISTS prompt_sections (
    id SERIAL PRIMARY KEY,

    -- Unique identifier for referencing in prompts
    section_id VARCHAR(100) UNIQUE NOT NULL,

    -- Section type for categorization and UI grouping
    -- Common types: identity, guidelines, context, tools, memory, constraints
    section_type VARCHAR(50) NOT NULL,

    -- Display name for UI
    name VARCHAR(255) NOT NULL,

    -- Actual prompt content (markdown supported)
    content TEXT NOT NULL,

    -- Order in which sections appear (lower = earlier)
    display_order INTEGER DEFAULT 0,

    -- If true, available to all agents; if false, must be explicitly assigned
    is_global BOOLEAN DEFAULT false,

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_prompt_sections_type ON prompt_sections(section_type);
CREATE INDEX IF NOT EXISTS idx_prompt_sections_global ON prompt_sections(is_global);
CREATE INDEX IF NOT EXISTS idx_prompt_sections_order ON prompt_sections(display_order);


-- =============================================================================
-- System Prompts
-- =============================================================================
-- Per-agent system prompts with version tracking.
-- Only one prompt per agent can be active at a time.

CREATE TABLE IF NOT EXISTS system_prompts (
    id SERIAL PRIMARY KEY,

    -- Agent this prompt belongs to
    agent_id VARCHAR(255) NOT NULL,

    -- Version number (auto-incremented on updates)
    version INTEGER NOT NULL DEFAULT 1,

    -- Display name for this prompt configuration
    name VARCHAR(255) NOT NULL,

    -- Full prompt content (can include inline content + section references)
    content TEXT NOT NULL,

    -- Array of section_ids to include (in order)
    -- These are prepended to the content
    section_ids JSONB DEFAULT '[]',

    -- Only one prompt per agent can be active
    is_active BOOLEAN DEFAULT false,

    -- Where to insert the tools section: 'start', 'end', or 'none'
    tools_position VARCHAR(20) DEFAULT 'end',

    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),

    -- Ensure unique version per agent
    UNIQUE(agent_id, version)
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_system_prompts_agent ON system_prompts(agent_id);
CREATE INDEX IF NOT EXISTS idx_system_prompts_active ON system_prompts(agent_id, is_active) WHERE is_active = true;


-- =============================================================================
-- Prompt Versions (Audit Trail)
-- =============================================================================
-- Stores historical versions for rollback capability.

CREATE TABLE IF NOT EXISTS prompt_versions (
    id SERIAL PRIMARY KEY,

    -- Reference to the prompt
    prompt_id INTEGER REFERENCES system_prompts(id) ON DELETE CASCADE,

    -- Version number at the time of this snapshot
    version INTEGER NOT NULL,

    -- Full content at this version
    content TEXT NOT NULL,

    -- Section IDs at this version
    section_ids JSONB DEFAULT '[]',

    -- Why this version was created
    change_reason TEXT,

    -- Who made the change (for audit)
    changed_by VARCHAR(255),

    -- When this version was created
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prompt_versions_prompt ON prompt_versions(prompt_id);
CREATE INDEX IF NOT EXISTS idx_prompt_versions_version ON prompt_versions(prompt_id, version);


-- =============================================================================
-- Helper Functions
-- =============================================================================

-- Function to automatically update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for prompt_sections
DROP TRIGGER IF EXISTS update_prompt_sections_updated_at ON prompt_sections;
CREATE TRIGGER update_prompt_sections_updated_at
    BEFORE UPDATE ON prompt_sections
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();


-- Function to ensure only one active prompt per agent
CREATE OR REPLACE FUNCTION ensure_single_active_prompt()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_active = true THEN
        -- Deactivate all other prompts for this agent
        UPDATE system_prompts
        SET is_active = false
        WHERE agent_id = NEW.agent_id AND id != NEW.id;
    END IF;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for single active prompt
DROP TRIGGER IF EXISTS ensure_single_active_prompt_trigger ON system_prompts;
CREATE TRIGGER ensure_single_active_prompt_trigger
    BEFORE INSERT OR UPDATE ON system_prompts
    FOR EACH ROW
    WHEN (NEW.is_active = true)
    EXECUTE FUNCTION ensure_single_active_prompt();


-- Function to auto-create version history on prompt update
CREATE OR REPLACE FUNCTION create_prompt_version()
RETURNS TRIGGER AS $$
BEGIN
    -- Only create version if content changed
    IF OLD.content IS DISTINCT FROM NEW.content OR OLD.section_ids IS DISTINCT FROM NEW.section_ids THEN
        -- Increment version
        NEW.version = OLD.version + 1;

        -- Store the old version
        INSERT INTO prompt_versions (prompt_id, version, content, section_ids, change_reason)
        VALUES (OLD.id, OLD.version, OLD.content, OLD.section_ids, 'Auto-versioned on update');
    END IF;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Trigger for auto-versioning
DROP TRIGGER IF EXISTS auto_version_prompt_trigger ON system_prompts;
CREATE TRIGGER auto_version_prompt_trigger
    BEFORE UPDATE ON system_prompts
    FOR EACH ROW
    EXECUTE FUNCTION create_prompt_version();


-- =============================================================================
-- Initial Data: Common Sections
-- =============================================================================

-- Insert default sections (only if they don't exist)
INSERT INTO prompt_sections (section_id, section_type, name, content, display_order, is_global)
VALUES
    ('core_identity', 'identity', 'Core Identity',
     'You are VOS (Virtual Operating System), an AI assistant designed to help users with various tasks. You are helpful, harmless, and honest.',
     0, true),

    ('response_guidelines', 'guidelines', 'Response Guidelines',
     '## Response Guidelines

- Be concise and direct
- Use markdown formatting when appropriate
- Always think step-by-step for complex problems
- Ask clarifying questions when requirements are ambiguous
- Acknowledge limitations and uncertainties',
     10, true),

    ('safety_constraints', 'constraints', 'Safety Constraints',
     '## Safety Constraints

- Never generate harmful, illegal, or unethical content
- Protect user privacy and confidentiality
- Do not attempt to circumvent security measures
- Always be transparent about your capabilities and limitations',
     20, true)

ON CONFLICT (section_id) DO NOTHING;


-- =============================================================================
-- Views for Easy Querying
-- =============================================================================

-- View to get the active prompt with expanded sections
CREATE OR REPLACE VIEW active_prompts_with_sections AS
SELECT
    sp.id,
    sp.agent_id,
    sp.version,
    sp.name,
    sp.content,
    sp.section_ids,
    sp.tools_position,
    sp.created_at,
    -- Aggregate section content
    COALESCE(
        (SELECT string_agg(ps.content, E'\n\n' ORDER BY ps.display_order)
         FROM prompt_sections ps
         WHERE ps.section_id = ANY(ARRAY(SELECT jsonb_array_elements_text(sp.section_ids)))
        ), ''
    ) AS sections_content
FROM system_prompts sp
WHERE sp.is_active = true;


-- Helpful comments
COMMENT ON TABLE prompt_sections IS 'Reusable prompt sections that can be included in system prompts';
COMMENT ON TABLE system_prompts IS 'Per-agent system prompts with versioning';
COMMENT ON TABLE prompt_versions IS 'Historical versions of prompts for rollback';
COMMENT ON VIEW active_prompts_with_sections IS 'Active prompts with expanded section content';

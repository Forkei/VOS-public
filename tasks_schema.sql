-- VOS Tasks Table Schema
-- Shared task list for multi-agent orchestration

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    assigner_agent_id TEXT NOT NULL,
    assignee_agent_id TEXT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'assigned', 'in_progress', 'completed', 'failed')),
    description TEXT NOT NULL,
    result JSONB NULL,
    
    -- Indexes for performance
    INDEX idx_tasks_status (status),
    INDEX idx_tasks_assignee (assignee_agent_id),
    INDEX idx_tasks_created_at (created_at)
);

-- Add comments for documentation
COMMENT ON TABLE tasks IS 'Shared task queue for VOS multi-agent system';
COMMENT ON COLUMN tasks.id IS 'Unique task identifier (UUID)';
COMMENT ON COLUMN tasks.created_at IS 'Task creation timestamp';
COMMENT ON COLUMN tasks.assigner_agent_id IS 'Agent that created/assigned the task';
COMMENT ON COLUMN tasks.assignee_agent_id IS 'Agent assigned to execute the task (null if unassigned)';
COMMENT ON COLUMN tasks.status IS 'Task status: pending, assigned, in_progress, completed, failed';
COMMENT ON COLUMN tasks.description IS 'Task description/prompt for the agent';
COMMENT ON COLUMN tasks.result IS 'Task execution result stored as JSON';
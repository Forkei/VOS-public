"""
System Prompts API Router

Provides REST API endpoints for managing system prompts:
- CRUD operations for prompt sections
- Per-agent prompt management
- Version history and rollback
- Preview with tools injection

This enables a future web UI for editing system prompts without code changes.
"""

import json
import logging
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/system-prompts", tags=["system-prompts"])


# =============================================================================
# Pydantic Models
# =============================================================================

class PromptSectionBase(BaseModel):
    """Base model for prompt sections"""
    section_id: str = Field(..., description="Unique identifier for the section")
    section_type: str = Field(..., description="Type: identity, guidelines, context, tools, etc.")
    name: str = Field(..., description="Display name")
    content: str = Field(..., description="Prompt content (markdown supported)")
    display_order: int = Field(default=0, description="Order in which sections appear")
    is_global: bool = Field(default=False, description="If true, available to all agents")


class PromptSectionCreate(PromptSectionBase):
    """Model for creating a new section"""
    pass


class PromptSectionUpdate(BaseModel):
    """Model for updating a section"""
    name: Optional[str] = None
    content: Optional[str] = None
    display_order: Optional[int] = None
    is_global: Optional[bool] = None


class PromptSection(PromptSectionBase):
    """Full section model with database fields"""
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SystemPromptBase(BaseModel):
    """Base model for system prompts"""
    name: str = Field(..., description="Display name for this prompt configuration")
    content: str = Field(..., description="Main prompt content")
    section_ids: List[str] = Field(default=[], description="Section IDs to include")
    tools_position: str = Field(default="end", description="Where to insert tools: start, end, none")


class SystemPromptCreate(SystemPromptBase):
    """Model for creating a new prompt"""
    is_active: bool = Field(default=False, description="Set as active prompt")


class SystemPromptUpdate(BaseModel):
    """Model for updating a prompt"""
    name: Optional[str] = None
    content: Optional[str] = None
    section_ids: Optional[List[str]] = None
    tools_position: Optional[str] = None


class SystemPrompt(SystemPromptBase):
    """Full prompt model with database fields"""
    id: int
    agent_id: str
    version: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class PromptVersion(BaseModel):
    """Model for version history"""
    id: int
    prompt_id: int
    version: int
    content: str
    section_ids: List[str]
    change_reason: Optional[str]
    changed_by: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class PromptPreview(BaseModel):
    """Model for prompt preview with tools"""
    agent_id: str
    version: int
    full_prompt: str
    sections_content: str
    main_content: str
    tools_section: str
    total_length: int


# =============================================================================
# Database Helper
# =============================================================================

def get_db():
    """Get database client from main app"""
    from app.main import db_client
    if not db_client:
        raise HTTPException(status_code=503, detail="Database not available")
    return db_client


# =============================================================================
# Section Endpoints
# =============================================================================

@router.get("/sections", response_model=List[PromptSection])
async def list_sections(
    section_type: Optional[str] = Query(None, description="Filter by section type"),
    is_global: Optional[bool] = Query(None, description="Filter by global flag")
):
    """
    List all prompt sections.

    Optionally filter by type or global flag.
    """
    db = get_db()

    query = "SELECT * FROM prompt_sections WHERE 1=1"
    params = []

    if section_type:
        query += " AND section_type = %s"
        params.append(section_type)

    if is_global is not None:
        query += " AND is_global = %s"
        params.append(is_global)

    query += " ORDER BY display_order, name"

    try:
        results = db.execute_query_dict(query, tuple(params) if params else None)
        return results or []
    except Exception as e:
        logger.error(f"Error listing sections: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/sections/{section_id}", response_model=PromptSection)
async def get_section(section_id: str):
    """Get a specific section by ID."""
    db = get_db()

    try:
        results = db.execute_query_dict(
            "SELECT * FROM prompt_sections WHERE section_id = %s",
            (section_id,)
        )
        if not results:
            raise HTTPException(status_code=404, detail="Section not found")
        return results[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting section: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sections", response_model=PromptSection)
async def create_section(section: PromptSectionCreate):
    """Create a new prompt section."""
    db = get_db()

    try:
        query = """
        INSERT INTO prompt_sections (section_id, section_type, name, content, display_order, is_global)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """
        results = db.execute_query_dict(query, (
            section.section_id,
            section.section_type,
            section.name,
            section.content,
            section.display_order,
            section.is_global
        ))
        return results[0]
    except Exception as e:
        logger.error(f"Error creating section: {e}")
        if "duplicate key" in str(e).lower():
            raise HTTPException(status_code=409, detail="Section ID already exists")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/sections/{section_id}", response_model=PromptSection)
async def update_section(section_id: str, update: PromptSectionUpdate):
    """Update an existing section."""
    db = get_db()

    # Build dynamic update query
    updates = []
    params = []

    if update.name is not None:
        updates.append("name = %s")
        params.append(update.name)
    if update.content is not None:
        updates.append("content = %s")
        params.append(update.content)
    if update.display_order is not None:
        updates.append("display_order = %s")
        params.append(update.display_order)
    if update.is_global is not None:
        updates.append("is_global = %s")
        params.append(update.is_global)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(section_id)

    try:
        query = f"UPDATE prompt_sections SET {', '.join(updates)} WHERE section_id = %s RETURNING *"
        results = db.execute_query_dict(query, tuple(params))
        if not results:
            raise HTTPException(status_code=404, detail="Section not found")
        return results[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating section: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/sections/{section_id}")
async def delete_section(section_id: str):
    """Delete a section."""
    db = get_db()

    try:
        result = db.execute_query_dict(
            "DELETE FROM prompt_sections WHERE section_id = %s RETURNING id",
            (section_id,)
        )
        if not result:
            raise HTTPException(status_code=404, detail="Section not found")
        return {"success": True, "deleted": section_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting section: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Agent Prompt Endpoints
# =============================================================================

@router.get("/agents/{agent_id}", response_model=List[SystemPrompt])
async def list_agent_prompts(agent_id: str):
    """List all prompts for an agent."""
    db = get_db()

    try:
        results = db.execute_query_dict(
            "SELECT * FROM system_prompts WHERE agent_id = %s ORDER BY version DESC",
            (agent_id,)
        )
        return results or []
    except Exception as e:
        logger.error(f"Error listing agent prompts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/agents/{agent_id}/active", response_model=SystemPrompt)
async def get_active_prompt(agent_id: str):
    """Get the active prompt for an agent."""
    db = get_db()

    try:
        results = db.execute_query_dict(
            "SELECT * FROM system_prompts WHERE agent_id = %s AND is_active = true",
            (agent_id,)
        )
        if not results:
            raise HTTPException(status_code=404, detail="No active prompt found")
        return results[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting active prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agents/{agent_id}", response_model=SystemPrompt)
async def create_agent_prompt(agent_id: str, prompt: SystemPromptCreate):
    """Create a new prompt for an agent."""
    db = get_db()

    try:
        import json
        query = """
        INSERT INTO system_prompts (agent_id, name, content, section_ids, tools_position, is_active)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """
        results = db.execute_query_dict(query, (
            agent_id,
            prompt.name,
            prompt.content,
            json.dumps(prompt.section_ids),
            prompt.tools_position,
            prompt.is_active
        ))
        return results[0]
    except Exception as e:
        logger.error(f"Error creating prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Prompt Management Endpoints
# =============================================================================

@router.get("/{prompt_id}", response_model=SystemPrompt)
async def get_prompt(prompt_id: int):
    """Get a specific prompt by ID."""
    db = get_db()

    try:
        results = db.execute_query_dict(
            "SELECT * FROM system_prompts WHERE id = %s",
            (prompt_id,)
        )
        if not results:
            raise HTTPException(status_code=404, detail="Prompt not found")
        return results[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{prompt_id}")
async def delete_prompt(prompt_id: int):
    """Delete a prompt by ID."""
    db = get_db()

    try:
        # First delete version history
        db.execute_query_dict(
            "DELETE FROM prompt_versions WHERE prompt_id = %s",
            (prompt_id,)
        )
        # Then delete the prompt
        results = db.execute_query_dict(
            "DELETE FROM system_prompts WHERE id = %s RETURNING id",
            (prompt_id,)
        )
        if not results:
            raise HTTPException(status_code=404, detail="Prompt not found")
        return {"deleted": True, "id": prompt_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{prompt_id}", response_model=SystemPrompt)
async def update_prompt(prompt_id: int, update: SystemPromptUpdate):
    """
    Update a prompt.

    This automatically creates a version history entry.
    """
    db = get_db()

    # Build dynamic update query
    updates = []
    params = []

    if update.name is not None:
        updates.append("name = %s")
        params.append(update.name)
    if update.content is not None:
        updates.append("content = %s")
        params.append(update.content)
    if update.section_ids is not None:
        import json
        updates.append("section_ids = %s")
        params.append(json.dumps(update.section_ids))
    if update.tools_position is not None:
        updates.append("tools_position = %s")
        params.append(update.tools_position)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    params.append(prompt_id)

    try:
        query = f"UPDATE system_prompts SET {', '.join(updates)} WHERE id = %s RETURNING *"
        results = db.execute_query_dict(query, tuple(params))
        if not results:
            raise HTTPException(status_code=404, detail="Prompt not found")
        return results[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{prompt_id}/activate", response_model=SystemPrompt)
async def activate_prompt(prompt_id: int):
    """
    Activate a prompt.

    This automatically deactivates any other active prompt for the same agent.
    """
    db = get_db()

    try:
        results = db.execute_query_dict(
            "UPDATE system_prompts SET is_active = true WHERE id = %s RETURNING *",
            (prompt_id,)
        )
        if not results:
            raise HTTPException(status_code=404, detail="Prompt not found")
        return results[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error activating prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{prompt_id}/versions", response_model=List[PromptVersion])
async def list_prompt_versions(prompt_id: int):
    """List all versions of a prompt."""
    db = get_db()

    try:
        results = db.execute_query_dict(
            "SELECT * FROM prompt_versions WHERE prompt_id = %s ORDER BY version DESC",
            (prompt_id,)
        )
        return results or []
    except Exception as e:
        logger.error(f"Error listing versions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{prompt_id}/rollback/{version}", response_model=SystemPrompt)
async def rollback_prompt(prompt_id: int, version: int):
    """
    Rollback a prompt to a specific version.

    This creates a new version with the content from the specified version.
    """
    db = get_db()

    try:
        # Get the version to rollback to
        version_results = db.execute_query_dict(
            "SELECT * FROM prompt_versions WHERE prompt_id = %s AND version = %s",
            (prompt_id, version)
        )
        if not version_results:
            raise HTTPException(status_code=404, detail="Version not found")

        old_version = version_results[0]

        # Convert section_ids to JSON string for JSONB column
        section_ids_json = json.dumps(old_version['section_ids']) if old_version['section_ids'] else '[]'

        # Update the prompt with the old content (triggers auto-versioning)
        results = db.execute_query_dict(
            """
            UPDATE system_prompts
            SET content = %s, section_ids = %s::jsonb
            WHERE id = %s
            RETURNING *
            """,
            (old_version['content'], section_ids_json, prompt_id)
        )

        if not results:
            raise HTTPException(status_code=404, detail="Prompt not found")

        return results[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error rolling back prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{prompt_id}/preview", response_model=PromptPreview)
async def preview_prompt(
    prompt_id: int,
    include_tools: bool = Query(True, description="Include tools section in preview")
):
    """
    Preview a prompt with all sections expanded and tools injected.

    Useful for seeing the final prompt as the agent will see it.
    """
    db = get_db()

    try:
        # Get the prompt
        prompt_results = db.execute_query_dict(
            "SELECT * FROM system_prompts WHERE id = %s",
            (prompt_id,)
        )
        if not prompt_results:
            raise HTTPException(status_code=404, detail="Prompt not found")

        prompt = prompt_results[0]

        # Get sections content
        import json
        section_ids = prompt.get('section_ids', [])
        if isinstance(section_ids, str):
            section_ids = json.loads(section_ids)

        sections_content = ""
        if section_ids:
            # Build query for sections
            placeholders = ','.join(['%s'] * len(section_ids))
            sections_results = db.execute_query_dict(
                f"SELECT content FROM prompt_sections WHERE section_id IN ({placeholders}) ORDER BY display_order",
                tuple(section_ids)
            )
            if sections_results:
                sections_content = "\n\n".join(s['content'] for s in sections_results)

        # Build tools section (placeholder - actual tools would come from agent)
        tools_section = ""
        if include_tools:
            tools_section = "## Available Tools\n\n[Tools would be injected here by the agent]"

        # Build full prompt
        parts = []
        if sections_content:
            parts.append(sections_content)

        main_content = prompt['content']
        tools_pos = prompt.get('tools_position', 'end')

        if tools_pos == 'start' and tools_section:
            parts.append(tools_section)
        parts.append(main_content)
        if tools_pos == 'end' and tools_section:
            parts.append(tools_section)

        full_prompt = "\n\n".join(parts)

        return PromptPreview(
            agent_id=prompt['agent_id'],
            version=prompt['version'],
            full_prompt=full_prompt,
            sections_content=sections_content,
            main_content=main_content,
            tools_section=tools_section,
            total_length=len(full_prompt)
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error previewing prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))

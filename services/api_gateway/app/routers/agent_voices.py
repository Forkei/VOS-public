"""
Agent Voice Settings API Router

Provides REST API endpoints for managing per-agent voice preferences:
- Get/set user's voice preferences for each agent
- Get default voices for all agents
- Get effective voice for a specific agent (user preference or default)

This enables users to customize which voice each agent uses during calls.
"""

import logging
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent-voices", tags=["agent-voices"])


# =============================================================================
# Pydantic Models
# =============================================================================

class AgentVoiceBase(BaseModel):
    """Base model for agent voice settings"""
    agent_id: str = Field(..., description="Agent identifier")
    tts_provider: str = Field(default="elevenlabs", description="TTS provider: elevenlabs or cartesia")
    voice_id: str = Field(..., description="Voice ID for the TTS provider")
    voice_name: Optional[str] = Field(None, description="Display name for the voice")


class AgentVoiceCreate(AgentVoiceBase):
    """Model for creating/updating agent voice setting"""
    pass


class AgentVoiceSetting(AgentVoiceBase):
    """Full agent voice setting model"""
    id: int
    user_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AgentDefaultVoice(AgentVoiceBase):
    """Default voice for an agent"""
    id: int
    description: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EffectiveAgentVoice(BaseModel):
    """Effective voice for an agent (user preference or default)"""
    agent_id: str
    tts_provider: str
    voice_id: str
    voice_name: Optional[str]
    is_custom: bool = Field(..., description="True if this is a user preference, false if default")


class BulkVoiceUpdate(BaseModel):
    """Model for bulk updating multiple agent voices"""
    settings: List[AgentVoiceCreate]


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
# Default Voice Endpoints
# =============================================================================

@router.get("/defaults", response_model=List[AgentDefaultVoice])
async def list_default_voices():
    """
    List default voices for all agents.

    These are the system-wide defaults used when a user hasn't set a custom preference.
    """
    db = get_db()

    try:
        results = db.execute_query_dict(
            "SELECT * FROM agent_default_voices ORDER BY agent_id"
        )
        return results or []
    except Exception as e:
        logger.error(f"Error listing default voices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/defaults/{agent_id}", response_model=AgentDefaultVoice)
async def get_default_voice(agent_id: str):
    """Get the default voice for a specific agent."""
    db = get_db()

    try:
        results = db.execute_query_dict(
            "SELECT * FROM agent_default_voices WHERE agent_id = %s",
            (agent_id,)
        )
        if not results:
            raise HTTPException(status_code=404, detail="Default voice not found for agent")
        return results[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting default voice: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# User Voice Preference Endpoints
# =============================================================================

@router.get("/user/{user_id}", response_model=List[AgentVoiceSetting])
async def list_user_voice_settings(user_id: str):
    """
    List all voice preferences for a user.

    Returns only the agents where the user has set a custom voice preference.
    """
    db = get_db()

    try:
        results = db.execute_query_dict(
            "SELECT * FROM agent_voice_settings WHERE user_id = %s ORDER BY agent_id",
            (user_id,)
        )
        return results or []
    except Exception as e:
        logger.error(f"Error listing user voice settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/{user_id}/{agent_id}", response_model=AgentVoiceSetting)
async def get_user_voice_setting(user_id: str, agent_id: str):
    """Get user's voice preference for a specific agent."""
    db = get_db()

    try:
        results = db.execute_query_dict(
            "SELECT * FROM agent_voice_settings WHERE user_id = %s AND agent_id = %s",
            (user_id, agent_id)
        )
        if not results:
            raise HTTPException(status_code=404, detail="Voice setting not found")
        return results[0]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user voice setting: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/user/{user_id}/{agent_id}", response_model=AgentVoiceSetting)
async def set_user_voice_setting(user_id: str, agent_id: str, voice: AgentVoiceCreate):
    """
    Set user's voice preference for an agent.

    Creates or updates the voice preference.
    """
    db = get_db()

    try:
        query = """
        INSERT INTO agent_voice_settings (user_id, agent_id, tts_provider, voice_id, voice_name)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (user_id, agent_id) DO UPDATE SET
            tts_provider = EXCLUDED.tts_provider,
            voice_id = EXCLUDED.voice_id,
            voice_name = EXCLUDED.voice_name,
            updated_at = NOW()
        RETURNING *
        """
        results = db.execute_query_dict(query, (
            user_id,
            agent_id,
            voice.tts_provider,
            voice.voice_id,
            voice.voice_name
        ))
        return results[0]
    except Exception as e:
        logger.error(f"Error setting voice preference: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/user/{user_id}/{agent_id}")
async def delete_user_voice_setting(user_id: str, agent_id: str):
    """
    Delete user's voice preference for an agent.

    The agent will revert to using the default voice.
    """
    db = get_db()

    try:
        result = db.execute_query_dict(
            "DELETE FROM agent_voice_settings WHERE user_id = %s AND agent_id = %s RETURNING id",
            (user_id, agent_id)
        )
        if not result:
            raise HTTPException(status_code=404, detail="Voice setting not found")
        return {"success": True, "message": f"Voice preference deleted for {agent_id}"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting voice preference: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/user/{user_id}/bulk", response_model=List[AgentVoiceSetting])
async def bulk_update_voice_settings(user_id: str, bulk: BulkVoiceUpdate):
    """
    Bulk update voice preferences for multiple agents.

    Useful for applying voice settings for all agents at once.
    """
    db = get_db()
    results = []

    try:
        for voice in bulk.settings:
            query = """
            INSERT INTO agent_voice_settings (user_id, agent_id, tts_provider, voice_id, voice_name)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id, agent_id) DO UPDATE SET
                tts_provider = EXCLUDED.tts_provider,
                voice_id = EXCLUDED.voice_id,
                voice_name = EXCLUDED.voice_name,
                updated_at = NOW()
            RETURNING *
            """
            result = db.execute_query_dict(query, (
                user_id,
                voice.agent_id,
                voice.tts_provider,
                voice.voice_id,
                voice.voice_name
            ))
            if result:
                results.append(result[0])

        return results
    except Exception as e:
        logger.error(f"Error bulk updating voice settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Effective Voice Endpoints
# =============================================================================

@router.get("/effective/{user_id}", response_model=List[EffectiveAgentVoice])
async def list_effective_voices(user_id: str):
    """
    List effective voices for all agents for a user.

    Returns user preference if set, otherwise the default voice.
    Useful for displaying the current voice configuration in the UI.
    """
    db = get_db()

    try:
        # Get all default voices and left join with user settings
        query = """
        SELECT
            adv.agent_id,
            COALESCE(avs.tts_provider, adv.tts_provider) as tts_provider,
            COALESCE(avs.voice_id, adv.voice_id) as voice_id,
            COALESCE(avs.voice_name, adv.voice_name) as voice_name,
            CASE WHEN avs.id IS NOT NULL THEN true ELSE false END as is_custom
        FROM agent_default_voices adv
        LEFT JOIN agent_voice_settings avs
            ON adv.agent_id = avs.agent_id AND avs.user_id = %s
        ORDER BY adv.agent_id
        """
        results = db.execute_query_dict(query, (user_id,))
        return results or []
    except Exception as e:
        logger.error(f"Error listing effective voices: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/effective/{user_id}/{agent_id}", response_model=EffectiveAgentVoice)
async def get_effective_voice(user_id: str, agent_id: str):
    """
    Get the effective voice for a specific agent for a user.

    Returns user preference if set, otherwise the default voice.
    This is the primary endpoint used by the voice gateway to determine
    which voice to use when an agent speaks.
    """
    db = get_db()

    try:
        # Try user preference first
        user_results = db.execute_query_dict(
            "SELECT * FROM agent_voice_settings WHERE user_id = %s AND agent_id = %s",
            (user_id, agent_id)
        )

        if user_results:
            setting = user_results[0]
            return EffectiveAgentVoice(
                agent_id=setting['agent_id'],
                tts_provider=setting['tts_provider'],
                voice_id=setting['voice_id'],
                voice_name=setting['voice_name'],
                is_custom=True
            )

        # Fall back to default
        default_results = db.execute_query_dict(
            "SELECT * FROM agent_default_voices WHERE agent_id = %s",
            (agent_id,)
        )

        if default_results:
            default = default_results[0]
            return EffectiveAgentVoice(
                agent_id=default['agent_id'],
                tts_provider=default['tts_provider'],
                voice_id=default['voice_id'],
                voice_name=default['voice_name'],
                is_custom=False
            )

        # If no default exists, return a fallback
        raise HTTPException(
            status_code=404,
            detail=f"No voice configuration found for agent {agent_id}"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting effective voice: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# Reset Endpoints
# =============================================================================

@router.delete("/user/{user_id}/all")
async def reset_all_user_voices(user_id: str):
    """
    Reset all voice preferences for a user.

    All agents will revert to using their default voices.
    """
    db = get_db()

    try:
        result = db.execute_query_dict(
            "DELETE FROM agent_voice_settings WHERE user_id = %s RETURNING id",
            (user_id,)
        )
        count = len(result) if result else 0
        return {"success": True, "message": f"Reset {count} voice preference(s) to defaults"}
    except Exception as e:
        logger.error(f"Error resetting voice preferences: {e}")
        raise HTTPException(status_code=500, detail=str(e))

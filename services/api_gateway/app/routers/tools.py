"""
Tools execution router for API Gateway.

Allows frontend to directly execute agent tools.
"""

import json
import logging
import sys
import os
import importlib.util
import re
from pathlib import Path
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from dateutil import parser as date_parser

logger = logging.getLogger(__name__)


def parse_rrule_to_dict(rrule_string: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Parse an RRULE string into a dictionary format expected by the frontend.

    Example input: "RRULE:FREQ=DAILY;COUNT=10;INTERVAL=2"
    Example output: {"freq": "DAILY", "count": 10, "interval": 2}
    """
    if not rrule_string:
        return None

    # Remove RRULE: prefix if present
    rule_str = rrule_string.replace('RRULE:', '').strip()
    if not rule_str:
        return None

    result = {}
    parts = rule_str.split(';')

    for part in parts:
        if '=' not in part:
            continue
        key, value = part.split('=', 1)
        key = key.strip().lower()
        value = value.strip()

        if key == 'freq':
            result['freq'] = value.upper()
        elif key == 'interval':
            try:
                result['interval'] = int(value)
            except ValueError:
                pass
        elif key == 'count':
            try:
                result['count'] = int(value)
            except ValueError:
                pass
        elif key == 'until':
            try:
                result['until'] = date_parser.isoparse(value).isoformat()
            except Exception:
                result['until'] = value
        elif key == 'byday':
            result['by_weekday'] = [d.strip() for d in value.split(',')]
        elif key == 'bymonthday':
            try:
                result['by_monthday'] = [int(d.strip()) for d in value.split(',')]
            except ValueError:
                pass
        elif key == 'bymonth':
            try:
                result['by_month'] = [int(m.strip()) for m in value.split(',')]
            except ValueError:
                pass

    return result if result else None

router = APIRouter(tags=["tools"])

# Global notes Weaviate client instance
_notes_weaviate_client = None
_NotesWeaviateClient = None

def _load_notes_weaviate_module():
    """Load the notes Weaviate client module using importlib to avoid vos_sdk issues."""
    global _NotesWeaviateClient
    if _NotesWeaviateClient is None:
        try:
            tools_path = Path("/app/tools")

            # First, ensure embedding_service is loaded (dependency)
            embedding_spec = importlib.util.spec_from_file_location(
                "embedding_service",
                tools_path / "memory" / "embedding_service.py"
            )
            embedding_module = importlib.util.module_from_spec(embedding_spec)
            sys.modules["memory.embedding_service"] = embedding_module
            embedding_spec.loader.exec_module(embedding_module)

            # Now load the notes Weaviate client
            spec = importlib.util.spec_from_file_location(
                "notes_weaviate_client",
                tools_path / "notes" / "notes_weaviate_client.py"
            )
            notes_weaviate_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(notes_weaviate_module)

            _NotesWeaviateClient = notes_weaviate_module.NotesWeaviateClient
            logger.info("Notes Weaviate module loaded successfully")
        except Exception as e:
            logger.error(f"Failed to load notes Weaviate module: {e}")
            raise
    return _NotesWeaviateClient

def get_notes_weaviate_client():
    """Get or create the notes Weaviate client singleton."""
    global _notes_weaviate_client
    if _notes_weaviate_client is None:
        try:
            NotesWeaviateClient = _load_notes_weaviate_module()
            _notes_weaviate_client = NotesWeaviateClient()
            _notes_weaviate_client.connect()
            logger.info("Notes Weaviate client initialized")
        except Exception as e:
            logger.warning(f"Failed to initialize Notes Weaviate client: {e}")
            return None
    return _notes_weaviate_client


class ToolExecutionRequest(BaseModel):
    """Request to execute a tool"""
    agent_id: str
    tool_name: str
    parameters: Dict[str, Any]


class ToolExecutionResponse(BaseModel):
    """Response from tool execution"""
    status: str  # "success", "error", or "queued"
    message: str | None = None
    result: Dict[str, Any] | None = None


@router.post("/tools/execute", response_model=ToolExecutionResponse)
async def execute_tool(request: ToolExecutionRequest):
    """
    Execute a tool on a specific agent.

    This endpoint executes tools directly for calendar_agent (synchronous),
    or routes to other agents via RabbitMQ (asynchronous).

    Args:
        request: Tool execution request containing agent_id, tool_name, and parameters

    Returns:
        Tool execution response with result or error
    """
    try:
        logger.info(f"Tool execution request: {request.agent_id}.{request.tool_name}")

        # For calendar_agent and notes_agent, execute tools directly with database queries
        if request.agent_id in ["calendar_agent", "notes_agent"]:
            try:
                from app.main import db_client

                if not db_client:
                    raise HTTPException(status_code=500, detail="Database client not initialized")

                # Execute the requested tool with direct database queries
                if request.tool_name == "list_calendar_events":
                    # Query calendar events
                    start_date = request.parameters.get('start_date')
                    end_date = request.parameters.get('end_date')
                    limit = request.parameters.get('limit', 100)

                    # Handle case where dates might be None - return all events if no date range
                    if start_date and end_date:
                        query = """
                            SELECT id, title, description, start_time, end_time,
                                   location, all_day, recurrence_rule, exception_dates,
                                   auto_reminders, created_at, updated_at
                            FROM calendar_events
                            WHERE (start_time BETWEEN %s AND %s) OR (end_time BETWEEN %s AND %s)
                            ORDER BY start_time ASC
                            LIMIT %s
                        """
                        rows = db_client.execute_query(query, (start_date, end_date, start_date, end_date, limit))
                    else:
                        # No date range - return recent events
                        query = """
                            SELECT id, title, description, start_time, end_time,
                                   location, all_day, recurrence_rule, exception_dates,
                                   auto_reminders, created_at, updated_at
                            FROM calendar_events
                            ORDER BY start_time ASC
                            LIMIT %s
                        """
                        rows = db_client.execute_query(query, (limit,))

                    events = []
                    for row in rows:
                        # Parse recurrence_rule string into object format
                        recurrence_rule = parse_rrule_to_dict(row[7]) if row[7] else None

                        # Parse exception_dates - could be JSON string or array
                        exception_dates = row[8]
                        if isinstance(exception_dates, str):
                            try:
                                exception_dates = json.loads(exception_dates)
                            except (json.JSONDecodeError, TypeError):
                                exception_dates = None

                        # Parse auto_reminders - could be JSON string or array
                        auto_reminders = row[9]
                        if isinstance(auto_reminders, str):
                            try:
                                auto_reminders = json.loads(auto_reminders)
                            except (json.JSONDecodeError, TypeError):
                                auto_reminders = None

                        events.append({
                            'id': str(row[0]),
                            'title': row[1],
                            'description': row[2],
                            'start_time': row[3].isoformat() if row[3] else None,
                            'end_time': row[4].isoformat() if row[4] else None,
                            'location': row[5],
                            'all_day': row[6] if row[6] is not None else False,
                            'recurrence_rule': recurrence_rule,
                            'exception_dates': exception_dates,
                            'auto_reminders': auto_reminders,
                            'created_at': row[10].isoformat() if row[10] else None,
                            'updated_at': row[11].isoformat() if row[11] else None
                        })

                    result = {'events': events, 'has_conflicts': False}

                elif request.tool_name == "list_reminders":
                    # Query reminders
                    start_date = request.parameters.get('start_date')
                    end_date = request.parameters.get('end_date')
                    limit = request.parameters.get('limit', 100)

                    # Handle case where dates might be None
                    if start_date and end_date:
                        query = """
                            SELECT id, title, description, trigger_time, recurrence_rule,
                                   target_agents, event_id, created_at
                            FROM reminders
                            WHERE trigger_time BETWEEN %s AND %s
                            ORDER BY trigger_time ASC
                            LIMIT %s
                        """
                        rows = db_client.execute_query(query, (start_date, end_date, limit))
                    else:
                        # No date range - return all reminders
                        query = """
                            SELECT id, title, description, trigger_time, recurrence_rule,
                                   target_agents, event_id, created_at
                            FROM reminders
                            ORDER BY trigger_time ASC
                            LIMIT %s
                        """
                        rows = db_client.execute_query(query, (limit,))

                    reminders = []
                    for row in rows:
                        # Parse recurrence_rule string into object format
                        recurrence_rule = parse_rrule_to_dict(row[4]) if row[4] else None

                        reminders.append({
                            'id': str(row[0]),
                            'title': row[1],
                            'description': row[2],
                            'trigger_time': row[3].isoformat() if row[3] else None,
                            'recurrence_rule': recurrence_rule,
                            'target_agents': row[5],
                            'event_id': str(row[6]) if row[6] else None,
                            'created_at': row[7].isoformat() if row[7] else None
                        })

                    result = {'reminders': reminders}

                elif request.tool_name == "create_reminder":
                    # Create a reminder
                    from datetime import datetime as dt
                    from dateutil import parser as date_parser

                    trigger_time_str = request.parameters.get('triggerTime') or request.parameters.get('trigger_time')
                    title = request.parameters.get('title')
                    description = request.parameters.get('description')
                    recurrence_rule = request.parameters.get('recurrenceRule') or request.parameters.get('recurrence_rule')
                    target_agents = request.parameters.get('targetAgents') or request.parameters.get('target_agents', ['primary_agent'])
                    event_id = request.parameters.get('eventId') or request.parameters.get('event_id')

                    # Validate required fields
                    if not trigger_time_str:
                        return ToolExecutionResponse(
                            status="error",
                            message="trigger_time is required",
                            result={}
                        )

                    # Parse trigger time
                    try:
                        trigger_time = date_parser.isoparse(trigger_time_str)
                    except Exception as e:
                        return ToolExecutionResponse(
                            status="error",
                            message=f"Invalid datetime format: {e}",
                            result={}
                        )

                    # Ensure target_agents is a list
                    if not isinstance(target_agents, list):
                        target_agents = [target_agents]

                    # Insert reminder
                    insert_query = """
                        INSERT INTO reminders
                            (title, description, trigger_time, event_id, recurrence_rule,
                             target_agents, created_by)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """
                    params = (
                        title, description, trigger_time, event_id, recurrence_rule,
                        target_agents, 'calendar_agent'
                    )

                    rows = db_client.execute_query(insert_query, params)
                    reminder_id = rows[0][0] if rows else None

                    result = {
                        'success': True,
                        'reminder_id': reminder_id,
                        'title': title,
                        'description': description,
                        'trigger_time': trigger_time.isoformat(),
                        'recurrence_rule': recurrence_rule,
                        'target_agents': target_agents
                    }

                    # Publish app interaction notification to calendar agent
                    try:
                        from app.app_interaction_manager import AppInteractionManager
                        app_interaction_manager = AppInteractionManager.get_instance()
                        if app_interaction_manager:
                            import asyncio
                            asyncio.create_task(app_interaction_manager.publish_notification(
                                agent_id='calendar_agent',
                                app_name='reminders_app',
                                action='reminder_created',
                                result=result
                            ))
                            logger.info(f"📱 Published reminder_created notification to calendar_agent")
                    except Exception as e:
                        logger.warning(f"Failed to publish app interaction notification: {e}")

                elif request.tool_name == "edit_reminder":
                    # Edit a reminder
                    from dateutil import parser as date_parser
                    import json

                    reminder_id = request.parameters.get('reminderId') or request.parameters.get('reminder_id')

                    logger.info(f"Edit reminder request parameters: {request.parameters}")

                    if not reminder_id:
                        logger.error("reminder_id is missing from edit request")
                        return ToolExecutionResponse(
                            status="error",
                            message="reminder_id is required",
                            result={}
                        )

                    # Build update query
                    update_fields = []
                    update_values = []

                    if 'title' in request.parameters:
                        update_fields.append('title = %s')
                        update_values.append(request.parameters['title'])
                    if 'description' in request.parameters:
                        update_fields.append('description = %s')
                        update_values.append(request.parameters['description'])
                    if 'triggerTime' in request.parameters or 'trigger_time' in request.parameters:
                        trigger_time_str = request.parameters.get('triggerTime') or request.parameters.get('trigger_time')
                        try:
                            update_fields.append('trigger_time = %s')
                            update_values.append(date_parser.isoparse(trigger_time_str))
                        except Exception as e:
                            logger.error(f"Failed to parse trigger_time: {e}")
                            return ToolExecutionResponse(
                                status="error",
                                message=f"Invalid trigger_time format: {e}",
                                result={}
                            )
                    if 'recurrenceRule' in request.parameters or 'recurrence_rule' in request.parameters:
                        recurrence_data = request.parameters.get('recurrenceRule') or request.parameters.get('recurrence_rule')
                        # If it's a dict/object, convert to RRULE string format
                        if isinstance(recurrence_data, dict):
                            # Convert dict to RRULE string (simplified - you may need more complex logic)
                            # For now, store as JSON string if it's an object
                            logger.warning(f"recurrence_rule received as dict, storing as JSON: {recurrence_data}")
                            recurrence_str = json.dumps(recurrence_data)
                        else:
                            recurrence_str = recurrence_data
                        update_fields.append('recurrence_rule = %s')
                        update_values.append(recurrence_str)
                    if 'targetAgents' in request.parameters or 'target_agents' in request.parameters:
                        target_agents = request.parameters.get('targetAgents') or request.parameters.get('target_agents')
                        if not isinstance(target_agents, list):
                            target_agents = [target_agents]
                        update_fields.append('target_agents = %s')
                        update_values.append(target_agents)

                    if not update_fields:
                        logger.error("No fields to update in edit request")
                        return ToolExecutionResponse(
                            status="error",
                            message="No fields to update",
                            result={}
                        )

                    update_values.append(reminder_id)
                    update_query = f"UPDATE reminders SET {', '.join(update_fields)} WHERE id = %s"

                    logger.info(f"Executing update query: {update_query} with values: {update_values}")
                    db_client.execute_query(update_query, tuple(update_values))

                    result = {'success': True, 'reminder_id': reminder_id}
                    logger.info(f"Successfully updated reminder {reminder_id}")

                    # Publish app interaction notification to calendar agent
                    try:
                        from app.app_interaction_manager import AppInteractionManager
                        app_interaction_manager = AppInteractionManager.get_instance()
                        if app_interaction_manager:
                            import asyncio
                            asyncio.create_task(app_interaction_manager.publish_notification(
                                agent_id='calendar_agent',
                                app_name='reminders_app',
                                action='reminder_updated',
                                result=result
                            ))
                            logger.info(f"📱 Published reminder_updated notification to calendar_agent")
                    except Exception as e:
                        logger.warning(f"Failed to publish app interaction notification: {e}")

                elif request.tool_name == "delete_reminder":
                    # Delete a reminder
                    reminder_id = request.parameters.get('reminderId') or request.parameters.get('reminder_id')

                    if not reminder_id:
                        return ToolExecutionResponse(
                            status="error",
                            message="reminder_id is required",
                            result={}
                        )

                    # Delete reminder
                    delete_query = "DELETE FROM reminders WHERE id = %s"
                    db_client.execute_query(delete_query, (reminder_id,))

                    result = {'success': True, 'reminder_id': reminder_id, 'message': f'Deleted reminder {reminder_id}'}

                    # Publish app interaction notification to calendar agent
                    try:
                        from app.app_interaction_manager import AppInteractionManager
                        app_interaction_manager = AppInteractionManager.get_instance()
                        if app_interaction_manager:
                            import asyncio
                            asyncio.create_task(app_interaction_manager.publish_notification(
                                agent_id='calendar_agent',
                                app_name='reminders_app',
                                action='reminder_deleted',
                                result=result
                            ))
                            logger.info(f"📱 Published reminder_deleted notification to calendar_agent")
                    except Exception as e:
                        logger.warning(f"Failed to publish app interaction notification: {e}")

                elif request.tool_name == "create_calendar_event":
                    # Create a calendar event
                    from datetime import datetime as dt
                    from dateutil import parser as date_parser

                    title = request.parameters.get('title')
                    description = request.parameters.get('description')
                    start_time_str = request.parameters.get('startTime') or request.parameters.get('start_time')
                    end_time_str = request.parameters.get('endTime') or request.parameters.get('end_time')
                    location = request.parameters.get('location')
                    all_day = request.parameters.get('allDay') or request.parameters.get('all_day', False)
                    recurrence_rule = request.parameters.get('recurrenceRule') or request.parameters.get('recurrence_rule')
                    exception_dates = request.parameters.get('exceptionDates') or request.parameters.get('exception_dates')
                    auto_reminders = request.parameters.get('autoReminders') or request.parameters.get('auto_reminders')

                    # Validate required fields
                    if not title:
                        return ToolExecutionResponse(
                            status="error",
                            message="title is required",
                            result={}
                        )
                    if not start_time_str or not end_time_str:
                        return ToolExecutionResponse(
                            status="error",
                            message="start_time and end_time are required",
                            result={}
                        )

                    # Parse times
                    try:
                        start_time = date_parser.isoparse(start_time_str)
                        end_time = date_parser.isoparse(end_time_str)
                    except Exception as e:
                        return ToolExecutionResponse(
                            status="error",
                            message=f"Invalid datetime format: {e}",
                            result={}
                        )

                    # Insert calendar event
                    insert_query = """
                        INSERT INTO calendar_events
                            (title, description, start_time, end_time, location,
                             all_day, recurrence_rule, exception_dates, auto_reminders)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                    """
                    params = (
                        title, description, start_time, end_time, location,
                        all_day, recurrence_rule, exception_dates, auto_reminders
                    )

                    rows = db_client.execute_query(insert_query, params)
                    event_id = rows[0][0] if rows else None

                    result = {
                        'success': True,
                        'event_id': event_id,
                        'title': title,
                        'description': description,
                        'start_time': start_time.isoformat(),
                        'end_time': end_time.isoformat(),
                        'location': location,
                        'all_day': all_day,
                        'recurrence_rule': recurrence_rule
                    }

                    # Publish app interaction notification to calendar agent
                    try:
                        from app.app_interaction_manager import AppInteractionManager
                        app_interaction_manager = AppInteractionManager.get_instance()
                        if app_interaction_manager:
                            import asyncio
                            asyncio.create_task(app_interaction_manager.publish_notification(
                                agent_id='calendar_agent',
                                app_name='calendar_app',
                                action='event_created',
                                result=result
                            ))
                            logger.info(f"📱 Published event_created notification to calendar_agent")
                    except Exception as e:
                        logger.warning(f"Failed to publish app interaction notification: {e}")

                elif request.tool_name == "update_calendar_event":
                    # Update a calendar event
                    from dateutil import parser as date_parser
                    import json

                    event_id = request.parameters.get('eventId') or request.parameters.get('event_id')

                    logger.info(f"Update calendar event request parameters: {request.parameters}")

                    if not event_id:
                        logger.error("event_id is missing from update request")
                        return ToolExecutionResponse(
                            status="error",
                            message="event_id is required",
                            result={}
                        )

                    # Build update query
                    update_fields = []
                    update_values = []

                    if 'title' in request.parameters:
                        update_fields.append('title = %s')
                        update_values.append(request.parameters['title'])
                    if 'description' in request.parameters:
                        update_fields.append('description = %s')
                        update_values.append(request.parameters['description'])
                    if 'startTime' in request.parameters or 'start_time' in request.parameters:
                        start_time_str = request.parameters.get('startTime') or request.parameters.get('start_time')
                        try:
                            update_fields.append('start_time = %s')
                            update_values.append(date_parser.isoparse(start_time_str))
                        except Exception as e:
                            logger.error(f"Failed to parse start_time: {e}")
                            return ToolExecutionResponse(
                                status="error",
                                message=f"Invalid start_time format: {e}",
                                result={}
                            )
                    if 'endTime' in request.parameters or 'end_time' in request.parameters:
                        end_time_str = request.parameters.get('endTime') or request.parameters.get('end_time')
                        try:
                            update_fields.append('end_time = %s')
                            update_values.append(date_parser.isoparse(end_time_str))
                        except Exception as e:
                            logger.error(f"Failed to parse end_time: {e}")
                            return ToolExecutionResponse(
                                status="error",
                                message=f"Invalid end_time format: {e}",
                                result={}
                            )
                    if 'location' in request.parameters:
                        update_fields.append('location = %s')
                        update_values.append(request.parameters['location'])
                    if 'allDay' in request.parameters or 'all_day' in request.parameters:
                        all_day = request.parameters.get('allDay') or request.parameters.get('all_day')
                        update_fields.append('all_day = %s')
                        update_values.append(all_day)
                    if 'recurrenceRule' in request.parameters or 'recurrence_rule' in request.parameters:
                        recurrence_data = request.parameters.get('recurrenceRule') or request.parameters.get('recurrence_rule')
                        if isinstance(recurrence_data, dict):
                            logger.warning(f"recurrence_rule received as dict, storing as JSON: {recurrence_data}")
                            recurrence_str = json.dumps(recurrence_data)
                        else:
                            recurrence_str = recurrence_data
                        update_fields.append('recurrence_rule = %s')
                        update_values.append(recurrence_str)
                    if 'exceptionDates' in request.parameters or 'exception_dates' in request.parameters:
                        exception_dates = request.parameters.get('exceptionDates') or request.parameters.get('exception_dates')
                        update_fields.append('exception_dates = %s')
                        update_values.append(exception_dates)
                    if 'autoReminders' in request.parameters or 'auto_reminders' in request.parameters:
                        auto_reminders = request.parameters.get('autoReminders') or request.parameters.get('auto_reminders')
                        update_fields.append('auto_reminders = %s')
                        update_values.append(auto_reminders)

                    if not update_fields:
                        logger.error("No fields to update in event update request")
                        return ToolExecutionResponse(
                            status="error",
                            message="No fields to update",
                            result={}
                        )

                    # Add updated_at timestamp
                    update_fields.append('updated_at = NOW()')

                    update_values.append(event_id)
                    update_query = f"UPDATE calendar_events SET {', '.join(update_fields)} WHERE id = %s"

                    logger.info(f"Executing update query: {update_query} with values: {update_values}")
                    db_client.execute_query(update_query, tuple(update_values))

                    result = {'success': True, 'event_id': event_id}
                    logger.info(f"Successfully updated calendar event {event_id}")

                    # Publish app interaction notification to calendar agent
                    try:
                        from app.app_interaction_manager import AppInteractionManager
                        app_interaction_manager = AppInteractionManager.get_instance()
                        if app_interaction_manager:
                            import asyncio
                            asyncio.create_task(app_interaction_manager.publish_notification(
                                agent_id='calendar_agent',
                                app_name='calendar_app',
                                action='event_updated',
                                result=result
                            ))
                            logger.info(f"📱 Published event_updated notification to calendar_agent")
                    except Exception as e:
                        logger.warning(f"Failed to publish app interaction notification: {e}")

                elif request.tool_name == "delete_calendar_event":
                    # Delete a calendar event
                    event_id = request.parameters.get('eventId') or request.parameters.get('event_id')

                    if not event_id:
                        return ToolExecutionResponse(
                            status="error",
                            message="event_id is required",
                            result={}
                        )

                    # Delete calendar event
                    delete_query = "DELETE FROM calendar_events WHERE id = %s"
                    db_client.execute_query(delete_query, (event_id,))

                    result = {'success': True, 'event_id': event_id, 'message': f'Deleted calendar event {event_id}'}

                    # Publish app interaction notification to calendar agent
                    try:
                        from app.app_interaction_manager import AppInteractionManager
                        app_interaction_manager = AppInteractionManager.get_instance()
                        if app_interaction_manager:
                            import asyncio
                            asyncio.create_task(app_interaction_manager.publish_notification(
                                agent_id='calendar_agent',
                                app_name='calendar_app',
                                action='event_deleted',
                                result=result
                            ))
                            logger.info(f"📱 Published event_deleted notification to calendar_agent")
                    except Exception as e:
                        logger.warning(f"Failed to publish app interaction notification: {e}")

                # Notes agent tools
                elif request.agent_id == "notes_agent" and request.tool_name == "list_notes":
                    # Query notes
                    folder = request.parameters.get('folder')
                    tags = request.parameters.get('tags', [])
                    is_pinned = request.parameters.get('is_pinned')
                    is_archived = request.parameters.get('is_archived', False)
                    created_by = request.parameters.get('created_by')  # Optional - if not provided, returns all notes
                    limit = request.parameters.get('limit', 50)
                    offset = request.parameters.get('offset', 0)
                    sort_by = request.parameters.get('sort_by', 'updated_at')
                    sort_order = request.parameters.get('sort_order', 'desc')

                    # Build query
                    conditions = []
                    params = []

                    if folder:
                        conditions.append("folder = %s")
                        params.append(folder)
                    if tags and len(tags) > 0:
                        conditions.append("tags && %s")
                        params.append(tags)
                    if is_pinned is not None:
                        conditions.append("is_pinned = %s")
                        params.append(is_pinned)
                    if is_archived is not None:
                        conditions.append("is_archived = %s")
                        params.append(is_archived)
                    # Don't filter by created_by if not provided (makes it optional)
                    # This allows notes to be visible regardless of who created them
                    if created_by:
                        conditions.append("created_by = %s")
                        params.append(created_by)

                    # Add TRUE if no conditions to avoid empty WHERE clause
                    if not conditions:
                        conditions.append("TRUE")

                    # Count query
                    count_query = f"SELECT COUNT(*) FROM notes WHERE {' AND '.join(conditions)}"
                    count_result = db_client.execute_query(count_query, tuple(params))
                    total_count = count_result[0][0] if count_result else 0

                    # Main query
                    query = f"""
                        SELECT id, title, LEFT(content, 200) as content_preview,
                               tags, folder, color, content_type, content_length,
                               is_pinned, is_archived, gcs_path, created_by,
                               created_at, updated_at
                        FROM notes
                        WHERE {' AND '.join(conditions)}
                        ORDER BY {sort_by} {sort_order.upper()}
                        LIMIT %s OFFSET %s
                    """
                    params.extend([limit, offset])
                    rows = db_client.execute_query(query, tuple(params))

                    notes = []
                    for row in rows:
                        notes.append({
                            'id': row[0],
                            'title': row[1],
                            'content_preview': row[2],
                            'tags': row[3] or [],
                            'folder': row[4],
                            'color': row[5],
                            'content_type': row[6],
                            'content_length': row[7],
                            'is_pinned': row[8],
                            'is_archived': row[9],
                            'has_gcs_content': row[10] is not None,
                            'created_by': row[11],
                            'created_at': row[12].isoformat() if row[12] else None,
                            'updated_at': row[13].isoformat() if row[13] else None
                        })

                    result = {
                        'notes': notes,
                        'total_count': total_count,
                        'limit': limit,
                        'offset': offset,
                        'has_more': (offset + limit) < total_count
                    }

                elif request.agent_id == "notes_agent" and request.tool_name == "create_note":
                    # Create a note
                    title = request.parameters.get('title')
                    content = request.parameters.get('content')
                    tags = request.parameters.get('tags', [])
                    folder = request.parameters.get('folder')
                    color = request.parameters.get('color')
                    is_pinned = request.parameters.get('is_pinned', False)
                    created_by = request.parameters.get('created_by', 'user')

                    # Validate required fields
                    if not title or not content:
                        return ToolExecutionResponse(
                            status="error",
                            message="title and content are required",
                            result={}
                        )

                    # Insert note
                    insert_query = """
                        INSERT INTO notes
                            (title, content, tags, folder, color, is_pinned, created_by, content_length)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id, created_at, updated_at
                    """
                    content_length = len(content)
                    params = (title, content, tags, folder, color, is_pinned, created_by, content_length)

                    rows = db_client.execute_query(insert_query, params)
                    note_id = rows[0][0] if rows else None
                    created_at = rows[0][1] if rows else None
                    updated_at = rows[0][2] if rows else None

                    result = {
                        'id': note_id,
                        'title': title,
                        'content': content,
                        'tags': tags,
                        'folder': folder,
                        'color': color,
                        'is_pinned': is_pinned,
                        'created_by': created_by,
                        'content_length': content_length,
                        'created_at': created_at.isoformat() if created_at else None,
                        'updated_at': updated_at.isoformat() if updated_at else None,
                        'storage_location': 'database'
                    }

                    # Sync to Weaviate for semantic search
                    try:
                        weaviate_client = get_notes_weaviate_client()
                        if weaviate_client:
                            weaviate_client.index_note(
                                note_id=note_id,
                                title=title,
                                content=content,
                                tags=tags,
                                folder=folder,
                                created_at=created_at.isoformat() if created_at else None,
                                updated_at=updated_at.isoformat() if updated_at else None
                            )
                            logger.info(f"🔍 Indexed note {note_id} in Weaviate")
                    except Exception as e:
                        logger.warning(f"Failed to index note in Weaviate: {e}")

                    # Publish app interaction notification
                    try:
                        from app.app_interaction_manager import AppInteractionManager
                        app_interaction_manager = AppInteractionManager.get_instance()
                        if app_interaction_manager:
                            import asyncio
                            asyncio.create_task(app_interaction_manager.publish_notification(
                                agent_id='notes_agent',
                                app_name='notes',
                                action='note_created',
                                result=result
                            ))
                            logger.info(f"📱 Published note_created notification to notes_agent")
                    except Exception as e:
                        logger.warning(f"Failed to publish app interaction notification: {e}")

                elif request.agent_id == "notes_agent" and request.tool_name == "update_note":
                    # Update a note
                    note_id = request.parameters.get('note_id')
                    if not note_id:
                        return ToolExecutionResponse(
                            status="error",
                            message="note_id is required",
                            result={}
                        )

                    # Build update query
                    update_fields = []
                    update_values = []

                    if 'title' in request.parameters:
                        update_fields.append('title = %s')
                        update_values.append(request.parameters['title'])
                    if 'content' in request.parameters:
                        update_fields.append('content = %s')
                        update_values.append(request.parameters['content'])
                        update_fields.append('content_length = %s')
                        update_values.append(len(request.parameters['content']))
                    if 'tags' in request.parameters:
                        update_fields.append('tags = %s')
                        update_values.append(request.parameters['tags'])
                    if 'folder' in request.parameters:
                        update_fields.append('folder = %s')
                        update_values.append(request.parameters['folder'])
                    if 'color' in request.parameters:
                        update_fields.append('color = %s')
                        update_values.append(request.parameters['color'])
                    if 'is_pinned' in request.parameters:
                        update_fields.append('is_pinned = %s')
                        update_values.append(request.parameters['is_pinned'])

                    if not update_fields:
                        return ToolExecutionResponse(
                            status="error",
                            message="No fields to update",
                            result={}
                        )

                    update_fields.append('updated_at = NOW()')
                    update_values.append(note_id)
                    update_query = f"UPDATE notes SET {', '.join(update_fields)} WHERE id = %s RETURNING id, title"

                    rows = db_client.execute_query(update_query, tuple(update_values))

                    result = {'success': True, 'note_id': note_id}
                    if rows:
                        result['title'] = rows[0][1]

                    # Sync to Weaviate for semantic search
                    try:
                        weaviate_client = get_notes_weaviate_client()
                        if weaviate_client:
                            weaviate_client.update_note(
                                note_id=note_id,
                                title=request.parameters.get('title'),
                                content=request.parameters.get('content'),
                                tags=request.parameters.get('tags'),
                                folder=request.parameters.get('folder')
                            )
                            logger.info(f"🔍 Updated note {note_id} in Weaviate")
                    except Exception as e:
                        logger.warning(f"Failed to update note in Weaviate: {e}")

                    # Publish app interaction notification
                    try:
                        from app.app_interaction_manager import AppInteractionManager
                        app_interaction_manager = AppInteractionManager.get_instance()
                        if app_interaction_manager:
                            import asyncio
                            asyncio.create_task(app_interaction_manager.publish_notification(
                                agent_id='notes_agent',
                                app_name='notes',
                                action='note_updated',
                                result=result
                            ))
                    except Exception as e:
                        logger.warning(f"Failed to publish app interaction notification: {e}")

                elif request.agent_id == "notes_agent" and request.tool_name == "delete_note":
                    # Delete a note
                    note_id = request.parameters.get('note_id')
                    if not note_id:
                        return ToolExecutionResponse(
                            status="error",
                            message="note_id is required",
                            result={}
                        )

                    # Get note info before deleting
                    get_query = "SELECT title FROM notes WHERE id = %s"
                    rows = db_client.execute_query(get_query, (note_id,))
                    title = rows[0][0] if rows else None

                    # Delete note
                    delete_query = "DELETE FROM notes WHERE id = %s"
                    db_client.execute_query(delete_query, (note_id,))

                    result = {'success': True, 'note_id': note_id, 'title': title}

                    # Delete from Weaviate
                    try:
                        weaviate_client = get_notes_weaviate_client()
                        if weaviate_client:
                            weaviate_client.delete_note(note_id)
                            logger.info(f"🔍 Deleted note {note_id} from Weaviate")
                    except Exception as e:
                        logger.warning(f"Failed to delete note from Weaviate: {e}")

                    # Publish app interaction notification
                    try:
                        from app.app_interaction_manager import AppInteractionManager
                        app_interaction_manager = AppInteractionManager.get_instance()
                        if app_interaction_manager:
                            import asyncio
                            asyncio.create_task(app_interaction_manager.publish_notification(
                                agent_id='notes_agent',
                                app_name='notes',
                                action='note_deleted',
                                result=result
                            ))
                    except Exception as e:
                        logger.warning(f"Failed to publish app interaction notification: {e}")

                elif request.agent_id == "notes_agent" and request.tool_name == "pin_note":
                    # Pin/unpin a note
                    note_id = request.parameters.get('note_id')
                    is_pinned = request.parameters.get('is_pinned')

                    if note_id is None or is_pinned is None:
                        return ToolExecutionResponse(
                            status="error",
                            message="note_id and is_pinned are required",
                            result={}
                        )

                    update_query = "UPDATE notes SET is_pinned = %s, updated_at = NOW() WHERE id = %s RETURNING title"
                    rows = db_client.execute_query(update_query, (is_pinned, note_id))

                    result = {'success': True, 'note_id': note_id, 'is_pinned': is_pinned}
                    if rows:
                        result['title'] = rows[0][0]

                    # Publish app interaction notification
                    try:
                        from app.app_interaction_manager import AppInteractionManager
                        app_interaction_manager = AppInteractionManager.get_instance()
                        if app_interaction_manager:
                            import asyncio
                            asyncio.create_task(app_interaction_manager.publish_notification(
                                agent_id='notes_agent',
                                app_name='notes',
                                action='note_pinned' if is_pinned else 'note_unpinned',
                                result=result
                            ))
                    except Exception as e:
                        logger.warning(f"Failed to publish app interaction notification: {e}")

                elif request.agent_id == "notes_agent" and request.tool_name == "archive_note":
                    # Archive/unarchive a note
                    note_id = request.parameters.get('note_id')
                    is_archived = request.parameters.get('is_archived')

                    if note_id is None or is_archived is None:
                        return ToolExecutionResponse(
                            status="error",
                            message="note_id and is_archived are required",
                            result={}
                        )

                    update_query = "UPDATE notes SET is_archived = %s, updated_at = NOW() WHERE id = %s RETURNING title"
                    rows = db_client.execute_query(update_query, (is_archived, note_id))

                    result = {'success': True, 'note_id': note_id, 'is_archived': is_archived}
                    if rows:
                        result['title'] = rows[0][0]

                    # Publish app interaction notification
                    try:
                        from app.app_interaction_manager import AppInteractionManager
                        app_interaction_manager = AppInteractionManager.get_instance()
                        if app_interaction_manager:
                            import asyncio
                            asyncio.create_task(app_interaction_manager.publish_notification(
                                agent_id='notes_agent',
                                app_name='notes',
                                action='note_archived' if is_archived else 'note_unarchived',
                                result=result
                            ))
                    except Exception as e:
                        logger.warning(f"Failed to publish app interaction notification: {e}")

                elif request.agent_id == "notes_agent" and request.tool_name == "get_note":
                    # Get a specific note
                    note_id = request.parameters.get('note_id')
                    if not note_id:
                        return ToolExecutionResponse(
                            status="error",
                            message="note_id is required",
                            result={}
                        )

                    query = """
                        SELECT id, title, content, tags, folder, color, content_type,
                               content_length, is_pinned, is_archived, gcs_path, created_by,
                               created_at, updated_at
                        FROM notes
                        WHERE id = %s
                    """
                    rows = db_client.execute_query(query, (note_id,))

                    if not rows:
                        return ToolExecutionResponse(
                            status="error",
                            message=f"Note {note_id} not found",
                            result={}
                        )

                    row = rows[0]
                    result = {
                        'id': row[0],
                        'title': row[1],
                        'content': row[2],
                        'tags': row[3] or [],
                        'folder': row[4],
                        'color': row[5],
                        'content_type': row[6],
                        'content_length': row[7],
                        'is_pinned': row[8],
                        'is_archived': row[9],
                        'has_gcs_content': row[10] is not None,
                        'created_by': row[11],
                        'created_at': row[12].isoformat() if row[12] else None,
                        'updated_at': row[13].isoformat() if row[13] else None
                    }

                elif request.agent_id == "notes_agent" and request.tool_name == "search_notes":
                    # Search notes
                    query_text = request.parameters.get('query')
                    created_by = request.parameters.get('created_by')
                    limit = request.parameters.get('limit', 20)

                    if not query_text:
                        return ToolExecutionResponse(
                            status="error",
                            message="query is required",
                            result={}
                        )

                    # Build search query with full-text search
                    search_query = """
                        SELECT id, title, LEFT(content, 200) as content_preview,
                               tags, folder, color, is_pinned, is_archived,
                               created_at, updated_at,
                               ts_rank(to_tsvector('english', title || ' ' || COALESCE(content, '')),
                                      plainto_tsquery('english', %s)) as rank
                        FROM notes
                        WHERE (to_tsvector('english', title || ' ' || COALESCE(content, '')) @@ plainto_tsquery('english', %s))
                    """
                    params = [query_text, query_text]

                    if created_by:
                        search_query += " AND created_by = %s"
                        params.append(created_by)

                    search_query += " ORDER BY rank DESC, updated_at DESC LIMIT %s"
                    params.append(limit)

                    rows = db_client.execute_query(search_query, tuple(params))

                    notes = []
                    for row in rows:
                        notes.append({
                            'id': row[0],
                            'title': row[1],
                            'content_preview': row[2],
                            'tags': row[3] or [],
                            'folder': row[4],
                            'color': row[5],
                            'is_pinned': row[6],
                            'is_archived': row[7],
                            'created_at': row[8].isoformat() if row[8] else None,
                            'updated_at': row[9].isoformat() if row[9] else None,
                            'relevance_score': float(row[10]) if row[10] else 0.0
                        })

                    result = {
                        'notes': notes,
                        'query': query_text,
                        'count': len(notes)
                    }

                elif request.agent_id == "notes_agent" and request.tool_name == "semantic_search_notes":
                    # Semantic search using Weaviate
                    query_text = request.parameters.get('query')
                    tags = request.parameters.get('tags', [])
                    folder = request.parameters.get('folder')
                    limit = request.parameters.get('limit', 10)
                    fetch_full = request.parameters.get('fetch_full', False)

                    if not query_text:
                        return ToolExecutionResponse(
                            status="error",
                            message="query is required",
                            result={}
                        )

                    weaviate_client = get_notes_weaviate_client()
                    if not weaviate_client:
                        return ToolExecutionResponse(
                            status="error",
                            message="Weaviate client not available",
                            result={}
                        )

                    # Perform semantic search
                    search_results = weaviate_client.semantic_search(
                        query=query_text,
                        tags=tags if tags else None,
                        folder=folder,
                        limit=limit
                    )

                    # Optionally fetch full note data from PostgreSQL
                    if fetch_full and search_results:
                        note_ids = [r['note_id'] for r in search_results]
                        placeholders = ','.join(['%s'] * len(note_ids))
                        query = f"""
                            SELECT id, title, content, tags, folder, color, content_type,
                                   content_length, is_pinned, is_archived, created_at, updated_at
                            FROM notes
                            WHERE id IN ({placeholders})
                        """
                        rows = db_client.execute_query(query, tuple(note_ids))

                        # Create a map of full note data
                        notes_map = {}
                        for row in rows:
                            notes_map[row[0]] = {
                                'id': row[0],
                                'title': row[1],
                                'content': row[2],
                                'tags': row[3] or [],
                                'folder': row[4],
                                'color': row[5],
                                'content_type': row[6],
                                'content_length': row[7],
                                'is_pinned': row[8],
                                'is_archived': row[9],
                                'created_at': row[10].isoformat() if row[10] else None,
                                'updated_at': row[11].isoformat() if row[11] else None
                            }

                        # Merge with search results (keeping relevance scores)
                        for r in search_results:
                            if r['note_id'] in notes_map:
                                r.update(notes_map[r['note_id']])
                                # Remove duplicate fields
                                if 'content_preview' in r:
                                    del r['content_preview']

                    result = {
                        'notes': search_results,
                        'query': query_text,
                        'count': len(search_results),
                        'search_type': 'semantic'
                    }

                elif request.agent_id == "notes_agent" and request.tool_name == "hybrid_search_notes":
                    # Hybrid search combining vector and BM25
                    query_text = request.parameters.get('query')
                    tags = request.parameters.get('tags', [])
                    folder = request.parameters.get('folder')
                    limit = request.parameters.get('limit', 10)
                    alpha = request.parameters.get('alpha', 0.5)  # Balance between vector (1.0) and keyword (0.0)
                    fetch_full = request.parameters.get('fetch_full', False)

                    if not query_text:
                        return ToolExecutionResponse(
                            status="error",
                            message="query is required",
                            result={}
                        )

                    weaviate_client = get_notes_weaviate_client()
                    if not weaviate_client:
                        return ToolExecutionResponse(
                            status="error",
                            message="Weaviate client not available",
                            result={}
                        )

                    # Perform hybrid search
                    search_results = weaviate_client.hybrid_search(
                        query=query_text,
                        tags=tags if tags else None,
                        folder=folder,
                        limit=limit,
                        alpha=alpha
                    )

                    # Optionally fetch full note data from PostgreSQL
                    if fetch_full and search_results:
                        note_ids = [r['note_id'] for r in search_results]
                        placeholders = ','.join(['%s'] * len(note_ids))
                        query = f"""
                            SELECT id, title, content, tags, folder, color, content_type,
                                   content_length, is_pinned, is_archived, created_at, updated_at
                            FROM notes
                            WHERE id IN ({placeholders})
                        """
                        rows = db_client.execute_query(query, tuple(note_ids))

                        # Create a map of full note data
                        notes_map = {}
                        for row in rows:
                            notes_map[row[0]] = {
                                'id': row[0],
                                'title': row[1],
                                'content': row[2],
                                'tags': row[3] or [],
                                'folder': row[4],
                                'color': row[5],
                                'content_type': row[6],
                                'content_length': row[7],
                                'is_pinned': row[8],
                                'is_archived': row[9],
                                'created_at': row[10].isoformat() if row[10] else None,
                                'updated_at': row[11].isoformat() if row[11] else None
                            }

                        # Merge with search results
                        for r in search_results:
                            if r['note_id'] in notes_map:
                                r.update(notes_map[r['note_id']])
                                if 'content_preview' in r:
                                    del r['content_preview']

                    result = {
                        'notes': search_results,
                        'query': query_text,
                        'count': len(search_results),
                        'search_type': 'hybrid',
                        'alpha': alpha
                    }

                elif request.agent_id == "notes_agent" and request.tool_name == "bulk_sync_notes":
                    # Bulk sync notes to Weaviate for initial migration
                    limit = request.parameters.get('limit', 1000)
                    offset = request.parameters.get('offset', 0)

                    weaviate_client = get_notes_weaviate_client()
                    if not weaviate_client:
                        return ToolExecutionResponse(
                            status="error",
                            message="Weaviate client not available",
                            result={}
                        )

                    # Fetch notes from PostgreSQL
                    query = """
                        SELECT id, title, content, tags, folder, created_at, updated_at
                        FROM notes
                        WHERE is_archived = false
                        ORDER BY id
                        LIMIT %s OFFSET %s
                    """
                    rows = db_client.execute_query(query, (limit, offset))

                    notes = []
                    for row in rows:
                        notes.append({
                            'id': row[0],
                            'title': row[1],
                            'content': row[2] or '',
                            'tags': row[3] or [],
                            'folder': row[4] or '',
                            'created_at': row[5].isoformat() if row[5] else None,
                            'updated_at': row[6].isoformat() if row[6] else None
                        })

                    # Bulk index to Weaviate
                    if notes:
                        sync_result = weaviate_client.bulk_index(notes)
                        result = {
                            'synced_count': sync_result['success_count'],
                            'error_count': sync_result['error_count'],
                            'errors': sync_result.get('errors', []),
                            'offset': offset,
                            'limit': limit,
                            'has_more': len(notes) == limit
                        }
                    else:
                        result = {
                            'synced_count': 0,
                            'error_count': 0,
                            'errors': [],
                            'offset': offset,
                            'limit': limit,
                            'has_more': False
                        }

                elif request.agent_id == "notes_agent" and request.tool_name == "notes_search_stats":
                    # Get Weaviate collection stats
                    weaviate_client = get_notes_weaviate_client()
                    if not weaviate_client:
                        return ToolExecutionResponse(
                            status="error",
                            message="Weaviate client not available",
                            result={}
                        )

                    result = weaviate_client.get_stats()

                else:
                    return ToolExecutionResponse(
                        status="error",
                        message=f"Tool not implemented: {request.tool_name}",
                        result={}
                    )

                logger.info(f"✅ Tool {request.tool_name} executed successfully")
                return ToolExecutionResponse(
                    status="success",
                    result=result
                )

            except Exception as e:
                logger.error(f"Error executing tool {request.tool_name}: {e}")
                return ToolExecutionResponse(
                    status="error",
                    message=str(e),
                    result={}
                )

        # For other agents, use async messaging (original implementation)
        from app.main import rabbitmq_client

        if not rabbitmq_client:
            raise HTTPException(status_code=500, detail="RabbitMQ client not initialized")

        # Create notification message in the format agents expect
        from datetime import datetime
        import uuid

        tool_message = {
            "notification_id": str(uuid.uuid4()),
            "timestamp": datetime.utcnow().isoformat(),
            "recipient_agent_id": request.agent_id,
            "notification_type": "direct_tool_call",
            "source": "api_gateway_tools",
            "payload": {
                "tool_name": request.tool_name,
                "parameters": request.parameters,
                "request_id": f"tool_{request.agent_id}_{request.tool_name}"
            }
        }

        # Route to agent queue
        queue_name = f"{request.agent_id}_queue"
        success = rabbitmq_client.publish_message(queue_name, tool_message)

        if not success:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to route tool execution to {request.agent_id}"
            )

        logger.info(f"✅ Tool execution request sent to {request.agent_id}: {request.tool_name}")

        return ToolExecutionResponse(
            status="queued",
            message=f"Tool execution request sent to {request.agent_id}",
            result={}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error executing tool: {e}")
        raise HTTPException(status_code=500, detail=f"Tool execution failed: {str(e)}")


@router.get("/notes/search")
async def semantic_search_notes(
    q: str,
    limit: int = 10,
    tags: str = None,
    folder: str = None,
    alpha: float = 0.5,
    search_type: str = "hybrid",
    fetch_full: bool = False
):
    """
    Semantic search for notes using Weaviate.

    Args:
        q: Search query text
        limit: Maximum number of results (default 10)
        tags: Comma-separated list of tags to filter by
        folder: Folder to filter by
        alpha: Balance between vector (1.0) and keyword (0.0) for hybrid search
        search_type: Type of search - 'semantic', 'hybrid', or 'keyword' (default 'hybrid')
        fetch_full: Whether to fetch full note content from PostgreSQL

    Returns:
        List of matching notes with relevance scores
    """
    try:
        weaviate_client = get_notes_weaviate_client()
        if not weaviate_client:
            raise HTTPException(status_code=503, detail="Weaviate client not available")

        # Parse tags
        tag_list = [t.strip() for t in tags.split(',')] if tags else None

        # Perform search based on type
        if search_type == "semantic":
            search_results = weaviate_client.semantic_search(
                query=q,
                tags=tag_list,
                folder=folder,
                limit=limit
            )
        elif search_type == "hybrid":
            search_results = weaviate_client.hybrid_search(
                query=q,
                tags=tag_list,
                folder=folder,
                limit=limit,
                alpha=alpha
            )
        else:
            raise HTTPException(status_code=400, detail=f"Invalid search_type: {search_type}")

        # Optionally fetch full note data from PostgreSQL
        if fetch_full and search_results:
            from app.main import db_client
            if db_client:
                note_ids = [r['note_id'] for r in search_results]
                placeholders = ','.join(['%s'] * len(note_ids))
                query = f"""
                    SELECT id, title, content, tags, folder, color, content_type,
                           content_length, is_pinned, is_archived, created_at, updated_at
                    FROM notes
                    WHERE id IN ({placeholders})
                """
                rows = db_client.execute_query(query, tuple(note_ids))

                # Create a map of full note data
                notes_map = {}
                for row in rows:
                    notes_map[row[0]] = {
                        'id': row[0],
                        'title': row[1],
                        'content': row[2],
                        'tags': row[3] or [],
                        'folder': row[4],
                        'color': row[5],
                        'content_type': row[6],
                        'content_length': row[7],
                        'is_pinned': row[8],
                        'is_archived': row[9],
                        'created_at': row[10].isoformat() if row[10] else None,
                        'updated_at': row[11].isoformat() if row[11] else None
                    }

                # Merge with search results
                for r in search_results:
                    if r['note_id'] in notes_map:
                        r.update(notes_map[r['note_id']])
                        if 'content_preview' in r:
                            del r['content_preview']

        return {
            'notes': search_results,
            'query': q,
            'count': len(search_results),
            'search_type': search_type
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in semantic search: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@router.post("/notes/bulk-sync")
async def bulk_sync_notes(limit: int = 1000, offset: int = 0):
    """
    Bulk sync notes from PostgreSQL to Weaviate for initial migration.

    Args:
        limit: Number of notes to sync per batch (default 1000)
        offset: Starting offset for pagination

    Returns:
        Sync results with success/error counts
    """
    try:
        weaviate_client = get_notes_weaviate_client()
        if not weaviate_client:
            raise HTTPException(status_code=503, detail="Weaviate client not available")

        from app.main import db_client
        if not db_client:
            raise HTTPException(status_code=503, detail="Database client not available")

        # Fetch notes from PostgreSQL
        query = """
            SELECT id, title, content, tags, folder, created_at, updated_at
            FROM notes
            WHERE is_archived = false
            ORDER BY id
            LIMIT %s OFFSET %s
        """
        rows = db_client.execute_query(query, (limit, offset))

        notes = []
        for row in rows:
            notes.append({
                'id': row[0],
                'title': row[1],
                'content': row[2] or '',
                'tags': row[3] or [],
                'folder': row[4] or '',
                'created_at': row[5].isoformat() if row[5] else None,
                'updated_at': row[6].isoformat() if row[6] else None
            })

        # Bulk index to Weaviate
        if notes:
            sync_result = weaviate_client.bulk_index(notes)
            return {
                'synced_count': sync_result['success_count'],
                'error_count': sync_result['error_count'],
                'errors': sync_result.get('errors', []),
                'offset': offset,
                'limit': limit,
                'has_more': len(notes) == limit
            }
        else:
            return {
                'synced_count': 0,
                'error_count': 0,
                'errors': [],
                'offset': offset,
                'limit': limit,
                'has_more': False
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in bulk sync: {e}")
        raise HTTPException(status_code=500, detail=f"Bulk sync failed: {str(e)}")


@router.get("/notes/search-stats")
async def get_notes_search_stats():
    """
    Get statistics about the notes search collection in Weaviate.

    Returns:
        Collection statistics including total indexed notes
    """
    try:
        weaviate_client = get_notes_weaviate_client()
        if not weaviate_client:
            raise HTTPException(status_code=503, detail="Weaviate client not available")

        return weaviate_client.get_stats()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting search stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")

"""
Reminder Tools

Implements tools for managing reminders with virtual recurrence support.
"""

from vos_sdk.tools.base import BaseTool
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from dateutil import rrule, parser as date_parser
import logging
import requests
import os
import json
import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


class CalendarDatabaseClient:
    """Database client for calendar tools."""
    def __init__(self):
        self.conn = psycopg2.connect(
            host=os.getenv('DATABASE_HOST', os.getenv('POSTGRES_HOST', 'postgres')),
            port=os.getenv('DATABASE_PORT', os.getenv('POSTGRES_PORT', '5432')),
            database=os.getenv('DATABASE_NAME', os.getenv('POSTGRES_DB', 'vos_database')),
            user=os.getenv('DATABASE_USER', os.getenv('POSTGRES_USER', 'vos_user')),
            password=os.getenv('DATABASE_PASSWORD', os.getenv('POSTGRES_PASSWORD', 'vos_password'))
        )

    def execute_query(self, query, params=None, fetch=True):
        """Execute a query and optionally fetch results."""
        with self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(query, params)
            if fetch:
                return cur.fetchall()

    def commit(self):
        """Commit the transaction."""
        self.conn.commit()

    def close(self):
        """Close the connection."""
        self.conn.close()


def publish_app_interaction(agent_id: str, app_name: str, action: str, result: dict, session_id: str = None):
    """Publish notification to frontend via API Gateway"""
    api_gateway_url = os.getenv('API_GATEWAY_URL', 'http://api_gateway:8000')
    url = f"{api_gateway_url}/api/v1/notifications/app-interaction"

    # Read internal API key
    internal_api_key = os.getenv('INTERNAL_API_KEY', '')

    payload = {
        'agent_id': agent_id,
        'app_name': app_name,
        'action': action,
        'result': result,
        'session_id': session_id
    }

    headers = {
        'Content-Type': 'application/json',
        'X-Internal-Key': internal_api_key
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        response.raise_for_status()
    except Exception as e:
        logger.warning(f"Failed to publish app interaction: {e}")


def generate_reminder_instances(
    trigger_time: datetime,
    recurrence_rule: str,
    exception_dates: List[str],
    max_instances: int = 100
) -> List[datetime]:
    """
    Generate recurring reminder instances from RRULE

    Returns list of trigger times
    """
    try:
        # Parse RRULE
        rule = rrule.rrulestr(recurrence_rule, dtstart=trigger_time)

        # Generate instances
        instances = []
        exception_set = set(exception_dates)

        for idx, occurrence in enumerate(rule):
            if idx >= max_instances:
                break

            # Check if this date is in exceptions
            occurrence_date = occurrence.date().isoformat()
            if occurrence_date in exception_set:
                continue

            instances.append(occurrence)

        return instances
    except Exception as e:
        logger.error(f"Error generating reminder instances: {e}")
        return []


class CreateReminderTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="create_reminder",
            description=(
                "Create a reminder (standalone or event-attached). "
                "Standalone reminders support optional recurrence. "
                "Event-attached reminders are virtual (generated from event's auto_reminders)."
            )
        )

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "create_reminder",
            "description": (
                "Create a reminder (standalone or event-attached). "
                "Standalone reminders support optional recurrence. "
                "Event-attached reminders are virtual (generated from event's auto_reminders)."
            ),
            "parameters": [
                {
                    "name": "trigger_time",
                    "type": "str",
                    "description": "ISO 8601 datetime when reminder should trigger",
                    "required": True
                },
                {
                    "name": "title",
                    "type": "str",
                    "description": "Reminder title",
                    "required": False
                },
                {
                    "name": "description",
                    "type": "str",
                    "description": "Reminder description",
                    "required": False
                },
                {
                    "name": "event_id",
                    "type": "int",
                    "description": "Event ID for event-attached reminders",
                    "required": False
                },
                {
                    "name": "recurrence_rule",
                    "type": "str",
                    "description": "iCalendar RRULE format for standalone recurring reminders",
                    "required": False
                },
                {
                    "name": "target_agents",
                    "type": "list",
                    "description": "List of agent names to notify (defaults to ['primary_agent'])",
                    "required": False
                },
                {
                    "name": "created_by",
                    "type": "str",
                    "description": "Creator identifier",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Create a reminder

        Parameters:
            trigger_time (str): ISO 8601 datetime when reminder should trigger (required)
            title (str): Reminder title (optional)
            description (str): Reminder description (optional)
            event_id (int): Event ID for event-attached reminders (optional)
            recurrence_rule (str): iCalendar RRULE format for standalone recurring reminders (optional)
            target_agents (list): List of agent names to notify (optional, defaults to ['primary_agent'])
            created_by (str): Creator identifier (optional)
        """
        try:
            # Extract parameters
            trigger_time_str = arguments.get('trigger_time')
            title = arguments.get('title')
            description = arguments.get('description')
            event_id = arguments.get('event_id')
            recurrence_rule = arguments.get('recurrence_rule')
            target_agents = arguments.get('target_agents', ['primary_agent'])
            created_by = arguments.get('created_by', 'calendar_agent')

            # Validate required fields
            if not trigger_time_str:
                self.send_result_notification('FAILURE', error_message='trigger_time is required')
                return

            # Parse trigger time
            try:
                trigger_time = date_parser.isoparse(trigger_time_str)
            except Exception as e:
                self.send_result_notification('FAILURE', error_message=f'Invalid datetime format: {e}')
                return

            # Validate: if event_id is set, recurrence_rule should not be (recurrence comes from event)
            if event_id and recurrence_rule:
                self.send_result_notification('FAILURE', error_message='Event-attached reminders cannot have their own recurrence (inherit from event)')
                return

            # Validate recurrence rule has max 100 instances
            if recurrence_rule:
                try:
                    rule = rrule.rrulestr(recurrence_rule, dtstart=trigger_time)
                    instance_count = 0
                    for _ in rule:
                        instance_count += 1
                        if instance_count > 100:
                            self.send_result_notification('FAILURE', error_message='Recurrence rule would generate more than 100 instances. Please limit with COUNT or UNTIL.')
                            return
                except Exception as e:
                    self.send_result_notification('FAILURE', error_message=f'Invalid recurrence rule: {e}')
                    return

            # Validate target_agents is a list
            if not isinstance(target_agents, list):
                target_agents = [target_agents]

            # Database operations
            db = CalendarDatabaseClient()

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
                target_agents, created_by
            )

            result = db.execute_query(insert_query, params, fetch=True)
            db.commit()

            reminder_id = result[0]['id']

            # Prepare response
            response = {
                'success': True,
                'reminder_id': reminder_id,
                'title': title,
                'description': description,
                'trigger_time': trigger_time.isoformat(),
                'event_id': event_id,
                'recurrence_rule': recurrence_rule,
                'target_agents': target_agents
            }

            # Publish app interaction
            publish_app_interaction(
                agent_id='calendar_agent',
                app_name='reminders_app',
                action='reminder_created',
                result=response,
                session_id='user_session_default'
            )

            # Send success notification
            self.send_result_notification('SUCCESS', result=response)

        except Exception as e:
            logger.error(f"Error in CreateReminderTool: {e}", exc_info=True)
            self.send_result_notification('FAILURE', error_message=str(e))


class ListRemindersTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="list_reminders",
            description=(
                "List reminders within a date range. "
                "Generates virtual reminders from event auto_reminders and recurring standalone reminders. "
                "Returns chronological list with optional limit."
            )
        )

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "list_reminders",
            "description": (
                "List reminders within a date range. "
                "Generates virtual reminders from event auto_reminders and recurring standalone reminders. "
                "Returns chronological list with optional limit."
            ),
            "parameters": [
                {
                    "name": "start_date",
                    "type": "str",
                    "description": "ISO 8601 date/datetime",
                    "required": True
                },
                {
                    "name": "end_date",
                    "type": "str",
                    "description": "ISO 8601 date/datetime",
                    "required": True
                },
                {
                    "name": "limit",
                    "type": "int",
                    "description": "Maximum number of reminders to return (default 25, max 1000)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        List reminders

        Parameters:
            start_date (str): ISO 8601 date/datetime (required)
            end_date (str): ISO 8601 date/datetime (required)
            limit (int): Maximum number of reminders to return (optional, default 25, max 1000)
        """
        try:
            # Extract parameters
            start_date_str = arguments.get('start_date')
            end_date_str = arguments.get('end_date')
            limit = arguments.get('limit', 25)

            # Validate required fields
            if not start_date_str:
                self.send_result_notification('FAILURE', error_message='start_date is required')
                return
            if not end_date_str:
                self.send_result_notification('FAILURE', error_message='end_date is required')
                return

            # Validate limit
            if limit > 1000:
                limit = 1000

            # Parse dates
            try:
                start_date = date_parser.isoparse(start_date_str)
                end_date = date_parser.isoparse(end_date_str)
            except Exception as e:
                self.send_result_notification('FAILURE', error_message=f'Invalid date format: {e}')
                return

            # Database operations
            db = CalendarDatabaseClient()

            all_reminders = []

            # 1. Get standalone reminders (with and without recurrence)
            query = """
                SELECT *
                FROM reminders
                WHERE event_id IS NULL
                  AND (
                      -- Non-recurring in range
                      (recurrence_rule IS NULL AND trigger_time >= %s AND trigger_time <= %s)
                      OR
                      -- Recurring that might have instances in range
                      (recurrence_rule IS NOT NULL AND trigger_time <= %s)
                  )
                ORDER BY trigger_time ASC
            """
            params = (start_date, end_date, end_date)
            standalone_reminders = db.execute_query(query, params, fetch=True)

            for reminder in standalone_reminders:
                if reminder['recurrence_rule']:
                    # Generate recurring instances
                    exception_dates = json.loads(reminder['exception_dates']) if reminder['exception_dates'] else []
                    instances = generate_reminder_instances(
                        reminder['trigger_time'],
                        reminder['recurrence_rule'],
                        exception_dates,
                        max_instances=100
                    )

                    # Filter to date range
                    for instance_time in instances:
                        if start_date <= instance_time <= end_date:
                            all_reminders.append({
                                'id': reminder['id'],
                                'title': reminder['title'],
                                'description': reminder['description'],
                                'trigger_time': instance_time.isoformat(),
                                'type': 'standalone',
                                'is_recurring': True,
                                'recurrence_rule': reminder['recurrence_rule'],
                                'target_agents': reminder['target_agents']
                            })
                else:
                    # Single standalone reminder
                    all_reminders.append({
                        'id': reminder['id'],
                        'title': reminder['title'],
                        'description': reminder['description'],
                        'trigger_time': reminder['trigger_time'].isoformat(),
                        'type': 'standalone',
                        'is_recurring': False,
                        'target_agents': reminder['target_agents']
                    })

            # 2. Get event-attached reminders (virtual - generated from events)
            # Get events with auto_reminders in or before our date range
            event_query = """
                SELECT id, title, start_time, end_time, recurrence_rule,
                       exception_dates, auto_reminders
                FROM calendar_events
                WHERE auto_reminders IS NOT NULL
                  AND auto_reminders != '[]'::jsonb
                  AND (
                      -- Non-recurring events
                      (recurrence_rule IS NULL AND start_time >= %s AND start_time <= %s)
                      OR
                      -- Recurring events
                      (recurrence_rule IS NOT NULL AND start_time <= %s)
                  )
            """
            events = db.execute_query(event_query, (start_date, end_date, end_date), fetch=True)

            # Import generate_recurring_instances from calendar_event_tools
            from services.tools.calendar.calendar_event_tools import generate_recurring_instances

            for event in events:
                auto_reminders = json.loads(event['auto_reminders']) if event['auto_reminders'] else []

                if event['recurrence_rule']:
                    # Generate event instances
                    exception_dates = json.loads(event['exception_dates']) if event['exception_dates'] else []
                    event_instances = generate_recurring_instances(
                        event['start_time'],
                        event['end_time'],
                        event['recurrence_rule'],
                        exception_dates,
                        max_instances=100
                    )

                    # For each event instance, generate reminders
                    for event_instance in event_instances:
                        for minutes_before in auto_reminders:
                            reminder_time = event_instance['start_time'] - timedelta(minutes=minutes_before)
                            if start_date <= reminder_time <= end_date:
                                all_reminders.append({
                                    'id': None,  # Virtual reminder
                                    'event_id': event['id'],
                                    'event_title': event['title'],
                                    'title': f"Reminder: {event['title']}",
                                    'description': f"{minutes_before} minutes before event",
                                    'trigger_time': reminder_time.isoformat(),
                                    'event_start_time': event_instance['start_time'].isoformat(),
                                    'type': 'event_attached',
                                    'is_virtual': True,
                                    'target_agents': ['primary_agent']  # Default for event reminders
                                })
                else:
                    # Single event
                    for minutes_before in auto_reminders:
                        reminder_time = event['start_time'] - timedelta(minutes=minutes_before)
                        if start_date <= reminder_time <= end_date:
                            all_reminders.append({
                                'id': None,  # Virtual reminder
                                'event_id': event['id'],
                                'event_title': event['title'],
                                'title': f"Reminder: {event['title']}",
                                'description': f"{minutes_before} minutes before event",
                                'trigger_time': reminder_time.isoformat(),
                                'event_start_time': event['start_time'].isoformat(),
                                'type': 'event_attached',
                                'is_virtual': True,
                                'target_agents': ['primary_agent']
                            })

            # Sort by trigger_time
            all_reminders.sort(key=lambda x: x['trigger_time'])

            # Apply limit
            limited_reminders = all_reminders[:limit]

            result = {
                'success': True,
                'reminders': limited_reminders,
                'count': len(limited_reminders),
                'total_in_range': len(all_reminders)
            }

            # Send success notification
            self.send_result_notification('SUCCESS', result=result)

        except Exception as e:
            logger.error(f"Error in ListRemindersTool: {e}", exc_info=True)
            self.send_result_notification('FAILURE', error_message=str(e))


class EditReminderTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="edit_reminder",
            description=(
                "Edit a standalone reminder. "
                "Can update title, description, trigger_time, recurrence_rule, or target_agents. "
                "Event-attached reminders cannot be edited individually (they're virtual)."
            )
        )

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "edit_reminder",
            "description": (
                "Edit a standalone reminder. "
                "Can update title, description, trigger_time, recurrence_rule, or target_agents. "
                "Event-attached reminders cannot be edited individually (they're virtual)."
            ),
            "parameters": [
                {
                    "name": "reminder_id",
                    "type": "int",
                    "description": "Reminder ID to edit",
                    "required": True
                },
                {
                    "name": "title",
                    "type": "str",
                    "description": "New title",
                    "required": False
                },
                {
                    "name": "description",
                    "type": "str",
                    "description": "New description",
                    "required": False
                },
                {
                    "name": "trigger_time",
                    "type": "str",
                    "description": "New trigger time",
                    "required": False
                },
                {
                    "name": "recurrence_rule",
                    "type": "str",
                    "description": "New recurrence rule",
                    "required": False
                },
                {
                    "name": "target_agents",
                    "type": "list",
                    "description": "New target agents",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Edit a reminder

        Parameters:
            reminder_id (int): Reminder ID to edit (required)
            title (str): New title (optional)
            description (str): New description (optional)
            trigger_time (str): New trigger time (optional)
            recurrence_rule (str): New recurrence rule (optional)
            target_agents (list): New target agents (optional)
        """
        try:
            # Extract parameters
            reminder_id = arguments.get('reminder_id')

            # Validate
            if not reminder_id:
                self.send_result_notification('FAILURE', error_message='reminder_id is required')
                return

            # Database operations
            db = CalendarDatabaseClient()

            # Fetch reminder
            query = "SELECT * FROM reminders WHERE id = %s"
            reminders = db.execute_query(query, (reminder_id,), fetch=True)

            if not reminders:
                self.send_result_notification('FAILURE', error_message=f'Reminder {reminder_id} not found')
                return

            reminder = reminders[0]

            # Check if event-attached
            if reminder['event_id'] is not None:
                self.send_result_notification('FAILURE', error_message='Cannot edit event-attached reminders individually (they are virtual)')
                return

            # Build update query
            update_fields = []
            update_values = []

            if 'title' in arguments:
                update_fields.append('title = %s')
                update_values.append(arguments['title'])
            if 'description' in arguments:
                update_fields.append('description = %s')
                update_values.append(arguments['description'])
            if 'trigger_time' in arguments:
                update_fields.append('trigger_time = %s')
                update_values.append(date_parser.isoparse(arguments['trigger_time']))
            if 'recurrence_rule' in arguments:
                update_fields.append('recurrence_rule = %s')
                update_values.append(arguments['recurrence_rule'])
            if 'target_agents' in arguments:
                target_agents = arguments['target_agents']
                if not isinstance(target_agents, list):
                    target_agents = [target_agents]
                update_fields.append('target_agents = %s')
                update_values.append(target_agents)

            if not update_fields:
                self.send_result_notification('FAILURE', error_message='No fields to update')
                return

            update_values.append(reminder_id)
            update_query = f"UPDATE reminders SET {', '.join(update_fields)} WHERE id = %s"

            db.execute_query(update_query, tuple(update_values), fetch=False)
            db.commit()

            # Fetch updated reminder
            updated_reminder = db.execute_query(query, (reminder_id,), fetch=True)[0]

            response = {
                'success': True,
                'reminder_id': reminder_id,
                'updated_fields': list(arguments.keys()),
                'reminder': {
                    'id': updated_reminder['id'],
                    'title': updated_reminder['title'],
                    'description': updated_reminder['description'],
                    'trigger_time': updated_reminder['trigger_time'].isoformat(),
                    'recurrence_rule': updated_reminder['recurrence_rule'],
                    'target_agents': updated_reminder['target_agents']
                }
            }

            # Publish app interaction
            publish_app_interaction(
                agent_id='calendar_agent',
                app_name='reminders_app',
                action='reminder_updated',
                result=response,
                session_id='user_session_default'
            )

            # Send success notification
            self.send_result_notification('SUCCESS', result=response)

        except Exception as e:
            logger.error(f"Error in EditReminderTool: {e}", exc_info=True)
            self.send_result_notification('FAILURE', error_message=str(e))


class DeleteReminderTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="delete_reminder",
            description=(
                "Delete a standalone reminder (hard delete). "
                "Event-attached reminders cannot be deleted individually (edit the event's auto_reminders instead)."
            )
        )

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "delete_reminder",
            "description": (
                "Delete a standalone reminder (hard delete). "
                "Event-attached reminders cannot be deleted individually (edit the event's auto_reminders instead)."
            ),
            "parameters": [
                {
                    "name": "reminder_id",
                    "type": "int",
                    "description": "Reminder ID to delete",
                    "required": True
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Delete a reminder

        Parameters:
            reminder_id (int): Reminder ID to delete (required)
        """
        try:
            # Extract parameters
            reminder_id = arguments.get('reminder_id')

            # Validate
            if not reminder_id:
                self.send_result_notification('FAILURE', error_message='reminder_id is required')
                return

            # Database operations
            db = CalendarDatabaseClient()

            # Fetch reminder to check if it's standalone
            query = "SELECT * FROM reminders WHERE id = %s"
            reminders = db.execute_query(query, (reminder_id,), fetch=True)

            if not reminders:
                self.send_result_notification('FAILURE', error_message=f'Reminder {reminder_id} not found')
                return

            reminder = reminders[0]

            # Check if event-attached
            if reminder['event_id'] is not None:
                self.send_result_notification('FAILURE', error_message='Cannot delete event-attached reminders individually (edit event auto_reminders instead)')
                return

            # Hard delete
            delete_query = "DELETE FROM reminders WHERE id = %s"
            db.execute_query(delete_query, (reminder_id,), fetch=False)
            db.commit()

            response = {
                'success': True,
                'message': f'Deleted reminder {reminder_id}',
                'reminder_id': reminder_id
            }

            # Publish app interaction
            publish_app_interaction(
                agent_id='calendar_agent',
                app_name='reminders_app',
                action='reminder_deleted',
                result=response,
                session_id='user_session_default'
            )

            # Send success notification
            self.send_result_notification('SUCCESS', result=response)

        except Exception as e:
            logger.error(f"Error in DeleteReminderTool: {e}", exc_info=True)
            self.send_result_notification('FAILURE', error_message=str(e))

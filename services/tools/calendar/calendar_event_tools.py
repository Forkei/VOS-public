"""
Calendar Event Tools

Implements tools for managing calendar events with virtual recurrence.
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


def generate_recurring_instances(
    start_time: datetime,
    end_time: datetime,
    recurrence_rule: str,
    exception_dates: List[str],
    max_instances: int = 100
) -> List[Dict[str, Any]]:
    """
    Generate recurring event instances from RRULE

    Returns list of dicts with 'start_time' and 'end_time'
    """
    try:
        # Parse RRULE
        rule = rrule.rrulestr(recurrence_rule, dtstart=start_time)

        # Calculate event duration
        duration = end_time - start_time

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

            instances.append({
                'start_time': occurrence,
                'end_time': occurrence + duration
            })

        return instances
    except Exception as e:
        logger.error(f"Error generating recurring instances: {e}")
        return []


def detect_conflicts(
    start_time: datetime,
    end_time: datetime,
    all_day: bool,
    recurrence_rule: Optional[str],
    exception_dates: List[str],
    exclude_event_id: Optional[int],
    db: CalendarDatabaseClient
) -> Dict[str, Any]:
    """
    Detect conflicts for an event

    Returns dict with conflict summary
    """
    conflicts = {}

    # Get instances to check
    if recurrence_rule:
        # Check next 1 year of recurring instances
        instances = generate_recurring_instances(
            start_time, end_time, recurrence_rule, exception_dates, max_instances=100
        )
        # Limit to next 1 year
        one_year_from_now = datetime.now() + timedelta(days=365)
        instances = [i for i in instances if i['start_time'] <= one_year_from_now]
    else:
        instances = [{'start_time': start_time, 'end_time': end_time}]

    # For each instance, check for conflicts
    for instance in instances:
        inst_start = instance['start_time']
        inst_end = instance['end_time']

        # Adjust times for all-day events (00:01 - 23:59)
        if all_day:
            inst_start = inst_start.replace(hour=0, minute=1, second=0)
            inst_end = inst_end.replace(hour=23, minute=59, second=0)

        # Query for overlapping events
        query = """
            SELECT id, title, start_time, end_time, recurrence_rule, exception_dates, all_day
            FROM calendar_events
            WHERE (%s IS NULL OR id != %s)
              AND (
                  (start_time < %s AND end_time > %s) OR
                  (start_time < %s AND end_time > %s) OR
                  (start_time >= %s AND end_time <= %s)
              )
        """
        params = (
            exclude_event_id, exclude_event_id,
            inst_end, inst_start,
            inst_end, inst_start,
            inst_start, inst_end
        )

        existing_events = db.execute_query(query, params, fetch=True)

        for event in existing_events:
            # Generate instances for existing recurring events
            if event['recurrence_rule']:
                existing_instances = generate_recurring_instances(
                    event['start_time'],
                    event['end_time'],
                    event['recurrence_rule'],
                    json.loads(event['exception_dates']) if event['exception_dates'] else [],
                    max_instances=100
                )
                # Check if any instance overlaps with our instance
                for ex_inst in existing_instances:
                    ex_start = ex_inst['start_time']
                    ex_end = ex_inst['end_time']

                    if event['all_day']:
                        ex_start = ex_start.replace(hour=0, minute=1, second=0)
                        ex_end = ex_end.replace(hour=23, minute=59, second=0)

                    # Check overlap
                    if ex_start < inst_end and ex_end > inst_start:
                        event_title = event['title']
                        if event_title not in conflicts:
                            conflicts[event_title] = 0
                        conflicts[event_title] += 1
            else:
                # Single event
                ev_start = event['start_time']
                ev_end = event['end_time']

                if event['all_day']:
                    ev_start = ev_start.replace(hour=0, minute=1, second=0)
                    ev_end = ev_end.replace(hour=23, minute=59, second=0)

                # Check overlap
                if ev_start < inst_end and ev_end > inst_start:
                    event_title = event['title']
                    if event_title not in conflicts:
                        conflicts[event_title] = 0
                    conflicts[event_title] += 1

    # Format conflict summary
    if conflicts:
        summary_parts = []
        for title, count in conflicts.items():
            summary_parts.append(f"{title}: {count} conflicts")
        return {
            'has_conflicts': True,
            'summary': ', '.join(summary_parts),
            'details': conflicts
        }
    else:
        return {
            'has_conflicts': False,
            'summary': 'No conflicts detected'
        }


class CreateCalendarEventTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="create_calendar_event",
            description=(
                "Create a new calendar event with optional recurrence. "
                "Supports auto-reminders and automatic conflict detection. "
                "All-day events are treated as 00:01 - 23:59 for conflict detection."
            )
        )

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "create_calendar_event",
            "description": (
                "Create a new calendar event with optional recurrence. "
                "Supports auto-reminders and automatic conflict detection. "
                "All-day events are treated as 00:01 - 23:59 for conflict detection."
            ),
            "parameters": [
                {
                    "name": "title",
                    "type": "str",
                    "description": "Event title",
                    "required": True
                },
                {
                    "name": "start_time",
                    "type": "str",
                    "description": "ISO 8601 datetime",
                    "required": True
                },
                {
                    "name": "end_time",
                    "type": "str",
                    "description": "ISO 8601 datetime",
                    "required": True
                },
                {
                    "name": "description",
                    "type": "str",
                    "description": "Event description",
                    "required": False
                },
                {
                    "name": "location",
                    "type": "str",
                    "description": "Event location",
                    "required": False
                },
                {
                    "name": "all_day",
                    "type": "bool",
                    "description": "All-day event flag (default False)",
                    "required": False
                },
                {
                    "name": "recurrence_rule",
                    "type": "str",
                    "description": "iCalendar RRULE format (max 100 instances)",
                    "required": False
                },
                {
                    "name": "auto_reminders",
                    "type": "list",
                    "description": "List of minutes before event to set reminders, e.g. [15, 60, 1440]",
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
        Create a calendar event

        Parameters:
            title (str): Event title (required)
            start_time (str): ISO 8601 datetime (required)
            end_time (str): ISO 8601 datetime (required)
            description (str): Event description (optional)
            location (str): Event location (optional)
            all_day (bool): All-day event flag (optional, default False)
            recurrence_rule (str): iCalendar RRULE format (optional, max 100 instances)
            auto_reminders (list): List of minutes before event to set reminders, e.g. [15, 60, 1440] (optional)
            created_by (str): Creator identifier (optional)
        """
        try:
            # Extract parameters
            title = arguments.get('title')
            start_time_str = arguments.get('start_time')
            end_time_str = arguments.get('end_time')
            description = arguments.get('description')
            location = arguments.get('location')
            all_day = arguments.get('all_day', False)
            recurrence_rule = arguments.get('recurrence_rule')
            auto_reminders = arguments.get('auto_reminders', [])
            created_by = arguments.get('created_by', 'calendar_agent')

            # Validate required fields
            if not title:
                self.send_result_notification('FAILURE', error_message='Title is required')
                return
            if not start_time_str:
                self.send_result_notification('FAILURE', error_message='start_time is required')
                return
            if not end_time_str:
                self.send_result_notification('FAILURE', error_message='end_time is required')
                return

            # Parse times
            try:
                start_time = date_parser.isoparse(start_time_str)
                end_time = date_parser.isoparse(end_time_str)
            except Exception as e:
                self.send_result_notification('FAILURE', error_message=f'Invalid datetime format: {e}')
                return

            # Validate times
            if end_time <= start_time:
                self.send_result_notification('FAILURE', error_message='end_time must be after start_time')
                return

            # Validate recurrence rule has max 100 instances
            if recurrence_rule:
                try:
                    rule = rrule.rrulestr(recurrence_rule, dtstart=start_time)
                    instance_count = 0
                    for _ in rule:
                        instance_count += 1
                        if instance_count > 100:
                            self.send_result_notification('FAILURE', error_message='Recurrence rule would generate more than 100 instances. Please limit with COUNT or UNTIL.')
                            return
                except Exception as e:
                    self.send_result_notification('FAILURE', error_message=f'Invalid recurrence rule: {e}')
                    return

            # Database operations
            db = CalendarDatabaseClient()

            # Detect conflicts (before creating)
            conflict_info = detect_conflicts(
                start_time, end_time, all_day, recurrence_rule, [], None, db
            )

            # Insert event
            insert_query = """
                INSERT INTO calendar_events
                    (title, description, start_time, end_time, all_day, location,
                     recurrence_rule, auto_reminders, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """
            params = (
                title, description, start_time, end_time, all_day, location,
                recurrence_rule, json.dumps(auto_reminders), created_by
            )

            result = db.execute_query(insert_query, params, fetch=True)
            db.commit()

            event_id = result[0]['id']

            # Prepare response
            response = {
                'success': True,
                'event_id': event_id,
                'title': title,
                'start_time': start_time.isoformat(),
                'end_time': end_time.isoformat(),
                'all_day': all_day,
                'recurrence_rule': recurrence_rule,
                'auto_reminders': auto_reminders,
                'conflicts': conflict_info
            }

            # Publish app interaction
            publish_app_interaction(
                agent_id='calendar_agent',
                app_name='calendar_app',
                action='event_created',
                result=response,
                session_id='user_session_default'
            )

            # Send success notification
            self.send_result_notification('SUCCESS', result=response)

        except Exception as e:
            logger.error(f"Error in CreateCalendarEventTool: {e}", exc_info=True)
            self.send_result_notification('FAILURE', error_message=str(e))


class ListCalendarEventsTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="list_calendar_events",
            description=(
                "List calendar events within a date range. "
                "Generates recurring event instances on-demand. "
                "Returns chronological list with optional limit."
            )
        )

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "list_calendar_events",
            "description": (
                "List calendar events within a date range. "
                "Generates recurring event instances on-demand. "
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
                    "description": "Maximum number of events to return (default 25, max 1000)",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        List calendar events

        Parameters:
            start_date (str): ISO 8601 date/datetime (required)
            end_date (str): ISO 8601 date/datetime (required)
            limit (int): Maximum number of events to return (optional, default 25, max 1000)
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

            # Database query
            db = CalendarDatabaseClient()

            # Get all events that might have instances in this range
            # For recurring events, we check if they could possibly have instances in range
            query = """
                SELECT *
                FROM calendar_events
                WHERE (
                    -- Non-recurring events in range
                    (recurrence_rule IS NULL AND start_time >= %s AND start_time <= %s)
                    OR
                    -- Recurring events that might have instances in range
                    (recurrence_rule IS NOT NULL AND start_time <= %s)
                )
                ORDER BY start_time ASC
            """
            params = (start_date, end_date, end_date)

            events = db.execute_query(query, params, fetch=True)

            # Generate all instances
            all_instances = []

            for event in events:
                if event['recurrence_rule']:
                    # Generate recurring instances
                    exception_dates = json.loads(event['exception_dates']) if event['exception_dates'] else []
                    instances = generate_recurring_instances(
                        event['start_time'],
                        event['end_time'],
                        event['recurrence_rule'],
                        exception_dates,
                        max_instances=100
                    )

                    # Filter to date range and convert to response format
                    for instance in instances:
                        if start_date <= instance['start_time'] <= end_date:
                            all_instances.append({
                                'id': event['id'],
                                'title': event['title'],
                                'description': event['description'],
                                'start_time': instance['start_time'].isoformat(),
                                'end_time': instance['end_time'].isoformat(),
                                'all_day': event['all_day'],
                                'location': event['location'],
                                'is_recurring': True,
                                'recurrence_rule': event['recurrence_rule']
                            })
                else:
                    # Single event
                    all_instances.append({
                        'id': event['id'],
                        'title': event['title'],
                        'description': event['description'],
                        'start_time': event['start_time'].isoformat(),
                        'end_time': event['end_time'].isoformat(),
                        'all_day': event['all_day'],
                        'location': event['location'],
                        'is_recurring': False
                    })

            # Sort by start_time
            all_instances.sort(key=lambda x: x['start_time'])

            # Apply limit
            limited_instances = all_instances[:limit]

            result = {
                'success': True,
                'events': limited_instances,
                'count': len(limited_instances),
                'total_in_range': len(all_instances)
            }

            # Send success notification
            self.send_result_notification('SUCCESS', result=result)

        except Exception as e:
            logger.error(f"Error in ListCalendarEventsTool: {e}", exc_info=True)
            self.send_result_notification('FAILURE', error_message=str(e))


class UpdateCalendarEventTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="update_calendar_event",
            description=(
                "Update a calendar event. "
                "For recurring events, supports updating 'this', 'this_and_future', or 'all' instances. "
                "Automatically re-runs conflict detection if times are changed."
            )
        )

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "update_calendar_event",
            "description": (
                "Update a calendar event. "
                "For recurring events, supports updating 'this', 'this_and_future', or 'all' instances. "
                "Automatically re-runs conflict detection if times are changed."
            ),
            "parameters": [
                {
                    "name": "event_id",
                    "type": "int",
                    "description": "Event ID to update",
                    "required": True
                },
                {
                    "name": "update_mode",
                    "type": "str",
                    "description": "For recurring events: 'this', 'this_and_future', 'all' (required for recurring)",
                    "required": False
                },
                {
                    "name": "instance_date",
                    "type": "str",
                    "description": "ISO date of specific instance for 'this' or 'this_and_future' mode (required for those modes)",
                    "required": False
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
                    "name": "start_time",
                    "type": "str",
                    "description": "New start time",
                    "required": False
                },
                {
                    "name": "end_time",
                    "type": "str",
                    "description": "New end time",
                    "required": False
                },
                {
                    "name": "location",
                    "type": "str",
                    "description": "New location",
                    "required": False
                },
                {
                    "name": "all_day",
                    "type": "bool",
                    "description": "New all-day flag",
                    "required": False
                },
                {
                    "name": "auto_reminders",
                    "type": "list",
                    "description": "New auto-reminders array",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Update a calendar event

        Parameters:
            event_id (int): Event ID to update (required)
            update_mode (str): For recurring events: 'this', 'this_and_future', 'all' (required for recurring)
            instance_date (str): ISO date of specific instance for 'this' or 'this_and_future' mode (required for those modes)
            title (str): New title (optional)
            description (str): New description (optional)
            start_time (str): New start time (optional)
            end_time (str): New end time (optional)
            location (str): New location (optional)
            all_day (bool): New all-day flag (optional)
            auto_reminders (list): New auto-reminders array (optional)
        """
        try:
            # Extract parameters
            event_id = arguments.get('event_id')
            update_mode = arguments.get('update_mode', 'all')  # Default to 'all' for non-recurring
            instance_date_str = arguments.get('instance_date')

            # Validate required fields
            if not event_id:
                self.send_result_notification('FAILURE', error_message='event_id is required')
                return

            # Database operations
            db = CalendarDatabaseClient()

            # Fetch existing event
            query = "SELECT * FROM calendar_events WHERE id = %s"
            events = db.execute_query(query, (event_id,), fetch=True)

            if not events:
                self.send_result_notification('FAILURE', error_message=f'Event {event_id} not found')
                return

            event = events[0]
            is_recurring = event['recurrence_rule'] is not None

            # Validate update_mode for recurring events
            if is_recurring and update_mode not in ['this', 'this_and_future', 'all']:
                self.send_result_notification('FAILURE', error_message="For recurring events, update_mode must be 'this', 'this_and_future', or 'all'")
                return

            # Handle different update modes
            if update_mode == 'this' and is_recurring:
                # Add to exception_dates and create new standalone event
                if not instance_date_str:
                    self.send_result_notification('FAILURE', error_message='instance_date required for update_mode=this')
                    return

                exception_dates = json.loads(event['exception_dates']) if event['exception_dates'] else []
                if instance_date_str not in exception_dates:
                    exception_dates.append(instance_date_str)

                # Update parent event with exception
                update_query = "UPDATE calendar_events SET exception_dates = %s WHERE id = %s"
                db.execute_query(update_query, (json.dumps(exception_dates), event_id), fetch=False)

                # Create new standalone event with modifications
                instance_date = date_parser.isoparse(instance_date_str).date()
                original_start = event['start_time']
                new_start_time = datetime.combine(instance_date, original_start.time())
                new_end_time = new_start_time + (event['end_time'] - event['start_time'])

                # Apply any time updates
                if arguments.get('start_time'):
                    new_start_time = date_parser.isoparse(arguments.get('start_time'))
                if arguments.get('end_time'):
                    new_end_time = date_parser.isoparse(arguments.get('end_time'))

                insert_query = """
                    INSERT INTO calendar_events
                        (title, description, start_time, end_time, all_day, location, auto_reminders, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """
                params = (
                    arguments.get('title', event['title']),
                    arguments.get('description', event['description']),
                    new_start_time,
                    new_end_time,
                    arguments.get('all_day', event['all_day']),
                    arguments.get('location', event['location']),
                    json.dumps(arguments.get('auto_reminders', json.loads(event['auto_reminders']))),
                    event['created_by']
                )

                result = db.execute_query(insert_query, params, fetch=True)
                db.commit()

                response = {
                    'success': True,
                    'message': f'Created exception for {instance_date_str} and new standalone event',
                    'new_event_id': result[0]['id']
                }

                self.send_result_notification('SUCCESS', result=response)
                return

            elif update_mode == 'this_and_future' and is_recurring:
                # Split into two series
                if not instance_date_str:
                    self.send_result_notification('FAILURE', error_message='instance_date required for update_mode=this_and_future')
                    return

                instance_date = date_parser.isoparse(instance_date_str)

                # Update original event's RRULE to end before this date
                original_rule = event['recurrence_rule']
                day_before = (instance_date - timedelta(days=1)).strftime('%Y%m%dT235959Z')

                # Add or update UNTIL parameter
                if 'UNTIL=' in original_rule:
                    # Replace existing UNTIL
                    import re
                    new_rule = re.sub(r'UNTIL=[^;]+', f'UNTIL={day_before}', original_rule)
                else:
                    # Add UNTIL
                    new_rule = f"{original_rule};UNTIL={day_before}"

                update_query = "UPDATE calendar_events SET recurrence_rule = %s WHERE id = %s"
                db.execute_query(update_query, (new_rule, event_id), fetch=False)

                # Create new recurring event starting from instance_date
                new_start_time = arguments.get('start_time')
                if new_start_time:
                    new_start_time = date_parser.isoparse(new_start_time)
                else:
                    new_start_time = datetime.combine(instance_date.date(), event['start_time'].time())

                new_end_time = arguments.get('end_time')
                if new_end_time:
                    new_end_time = date_parser.isoparse(new_end_time)
                else:
                    duration = event['end_time'] - event['start_time']
                    new_end_time = new_start_time + duration

                # Create new recurrence rule starting from this date
                new_recurrence = event['recurrence_rule']

                insert_query = """
                    INSERT INTO calendar_events
                        (title, description, start_time, end_time, all_day, location,
                         recurrence_rule, auto_reminders, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                """
                params = (
                    arguments.get('title', event['title']),
                    arguments.get('description', event['description']),
                    new_start_time,
                    new_end_time,
                    arguments.get('all_day', event['all_day']),
                    arguments.get('location', event['location']),
                    new_recurrence,
                    json.dumps(arguments.get('auto_reminders', json.loads(event['auto_reminders']))),
                    event['created_by']
                )

                result = db.execute_query(insert_query, params, fetch=True)
                db.commit()

                response = {
                    'success': True,
                    'message': f'Split series at {instance_date_str}',
                    'original_event_id': event_id,
                    'new_series_event_id': result[0]['id']
                }

                self.send_result_notification('SUCCESS', result=response)
                return

            else:  # update_mode == 'all' or non-recurring event
                # Build update query dynamically
                update_fields = []
                update_values = []

                if 'title' in arguments:
                    update_fields.append('title = %s')
                    update_values.append(arguments['title'])
                if 'description' in arguments:
                    update_fields.append('description = %s')
                    update_values.append(arguments['description'])
                if 'start_time' in arguments:
                    update_fields.append('start_time = %s')
                    update_values.append(date_parser.isoparse(arguments['start_time']))
                if 'end_time' in arguments:
                    update_fields.append('end_time = %s')
                    update_values.append(date_parser.isoparse(arguments['end_time']))
                if 'location' in arguments:
                    update_fields.append('location = %s')
                    update_values.append(arguments['location'])
                if 'all_day' in arguments:
                    update_fields.append('all_day = %s')
                    update_values.append(arguments['all_day'])
                if 'auto_reminders' in arguments:
                    update_fields.append('auto_reminders = %s')
                    update_values.append(json.dumps(arguments['auto_reminders']))

                if not update_fields:
                    self.send_result_notification('FAILURE', error_message='No fields to update')
                    return

                update_values.append(event_id)
                update_query = f"UPDATE calendar_events SET {', '.join(update_fields)} WHERE id = %s"

                db.execute_query(update_query, tuple(update_values), fetch=False)
                db.commit()

                # Check for conflicts if times changed
                conflict_info = {'has_conflicts': False}
                if 'start_time' in arguments or 'end_time' in arguments:
                    new_start = date_parser.isoparse(arguments.get('start_time')) if 'start_time' in arguments else event['start_time']
                    new_end = date_parser.isoparse(arguments.get('end_time')) if 'end_time' in arguments else event['end_time']
                    new_all_day = arguments.get('all_day', event['all_day'])

                    conflict_info = detect_conflicts(
                        new_start, new_end, new_all_day,
                        event['recurrence_rule'],
                        json.loads(event['exception_dates']) if event['exception_dates'] else [],
                        event_id, db
                    )

                response = {
                    'success': True,
                    'event_id': event_id,
                    'updated_fields': list(arguments.keys()),
                    'conflicts': conflict_info
                }

                # Publish app interaction
                publish_app_interaction(
                    agent_id='calendar_agent',
                    app_name='calendar_app',
                    action='event_updated',
                    result=response,
                    session_id='user_session_default'
                )

                # Send success notification
                self.send_result_notification('SUCCESS', result=response)

        except Exception as e:
            logger.error(f"Error in UpdateCalendarEventTool: {e}", exc_info=True)
            self.send_result_notification('FAILURE', error_message=str(e))


class DeleteCalendarEventTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="delete_calendar_event",
            description=(
                "Delete a calendar event (hard delete). "
                "For recurring events, supports deleting 'this', 'this_and_future', or 'all' instances. "
                "Cascades to delete event-attached reminders."
            )
        )

    def get_tool_info(self) -> Dict[str, Any]:
        """Get tool information for system prompt generation."""
        return {
            "command": "delete_calendar_event",
            "description": (
                "Delete a calendar event (hard delete). "
                "For recurring events, supports deleting 'this', 'this_and_future', or 'all' instances. "
                "Cascades to delete event-attached reminders."
            ),
            "parameters": [
                {
                    "name": "event_id",
                    "type": "int",
                    "description": "Event ID to delete",
                    "required": True
                },
                {
                    "name": "delete_mode",
                    "type": "str",
                    "description": "For recurring events: 'this', 'this_and_future', 'all' (required for recurring)",
                    "required": False
                },
                {
                    "name": "instance_date",
                    "type": "str",
                    "description": "ISO date of specific instance for 'this' or 'this_and_future' mode",
                    "required": False
                }
            ]
        }

    def execute(self, arguments: Dict[str, Any]) -> None:
        """
        Delete a calendar event

        Parameters:
            event_id (int): Event ID to delete (required)
            delete_mode (str): For recurring events: 'this', 'this_and_future', 'all' (required for recurring)
            instance_date (str): ISO date of specific instance for 'this' or 'this_and_future' mode
        """
        try:
            # Extract parameters
            event_id = arguments.get('event_id')
            delete_mode = arguments.get('delete_mode', 'all')
            instance_date_str = arguments.get('instance_date')

            # Validate
            if not event_id:
                self.send_result_notification('FAILURE', error_message='event_id is required')
                return

            # Database operations
            db = CalendarDatabaseClient()

            # Fetch event
            query = "SELECT * FROM calendar_events WHERE id = %s"
            events = db.execute_query(query, (event_id,), fetch=True)

            if not events:
                self.send_result_notification('FAILURE', error_message=f'Event {event_id} not found')
                return

            event = events[0]
            is_recurring = event['recurrence_rule'] is not None

            if delete_mode == 'this' and is_recurring:
                # Add to exception_dates
                if not instance_date_str:
                    self.send_result_notification('FAILURE', error_message='instance_date required for delete_mode=this')
                    return

                exception_dates = json.loads(event['exception_dates']) if event['exception_dates'] else []
                if instance_date_str not in exception_dates:
                    exception_dates.append(instance_date_str)

                update_query = "UPDATE calendar_events SET exception_dates = %s WHERE id = %s"
                db.execute_query(update_query, (json.dumps(exception_dates), event_id), fetch=False)
                db.commit()

                response = {
                    'success': True,
                    'message': f'Added {instance_date_str} to exception dates',
                    'event_id': event_id
                }

                publish_app_interaction(
                    agent_id='calendar_agent',
                    app_name='calendar_app',
                    action='event_deleted',
                    result=response,
                    session_id='user_session_default'
                )
                self.send_result_notification('SUCCESS', result=response)

            elif delete_mode == 'this_and_future' and is_recurring:
                # Update RRULE with UNTIL
                if not instance_date_str:
                    self.send_result_notification('FAILURE', error_message='instance_date required for delete_mode=this_and_future')
                    return

                instance_date = date_parser.isoparse(instance_date_str)
                day_before = (instance_date - timedelta(days=1)).strftime('%Y%m%dT235959Z')

                original_rule = event['recurrence_rule']
                if 'UNTIL=' in original_rule:
                    import re
                    new_rule = re.sub(r'UNTIL=[^;]+', f'UNTIL={day_before}', original_rule)
                else:
                    new_rule = f"{original_rule};UNTIL={day_before}"

                update_query = "UPDATE calendar_events SET recurrence_rule = %s WHERE id = %s"
                db.execute_query(update_query, (new_rule, event_id), fetch=False)
                db.commit()

                response = {
                    'success': True,
                    'message': f'Ended recurrence before {instance_date_str}',
                    'event_id': event_id
                }

                publish_app_interaction(
                    agent_id='calendar_agent',
                    app_name='calendar_app',
                    action='event_deleted',
                    result=response,
                    session_id='user_session_default'
                )
                self.send_result_notification('SUCCESS', result=response)

            else:  # delete_mode == 'all'
                # Hard delete (CASCADE will handle reminders)
                delete_query = "DELETE FROM calendar_events WHERE id = %s"
                db.execute_query(delete_query, (event_id,), fetch=False)
                db.commit()

                response = {
                    'success': True,
                    'message': f'Deleted event {event_id}',
                    'event_id': event_id
                }

                publish_app_interaction(
                    agent_id='calendar_agent',
                    app_name='calendar_app',
                    action='event_deleted',
                    result=response,
                    session_id='user_session_default'
                )
                self.send_result_notification('SUCCESS', result=response)

        except Exception as e:
            logger.error(f"Error in DeleteCalendarEventTool: {e}", exc_info=True)
            self.send_result_notification('FAILURE', error_message=str(e))

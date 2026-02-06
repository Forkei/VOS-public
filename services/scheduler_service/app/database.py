"""
Database client for scheduler service.
Handles all database interactions for standalone and virtual event reminders.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from typing import List, Dict, Optional
import os
import json


class DatabaseClient:
    def __init__(self):
        self.connection_params = {
            'host': os.getenv('DATABASE_HOST', 'postgres'),
            'port': int(os.getenv('DATABASE_PORT', 5432)),
            'database': os.getenv('DATABASE_NAME', 'vos_database'),
            'user': os.getenv('DATABASE_USER', 'vos_user'),
            'password': os.getenv('DATABASE_PASSWORD'),
        }
        self.conn = None
        self.connect()

    def connect(self):
        """Establish database connection"""
        try:
            self.conn = psycopg2.connect(**self.connection_params)
            print(f"✓ Connected to database: {self.connection_params['database']}")
        except Exception as e:
            print(f"✗ Database connection failed: {e}")
            raise

    def ensure_connection(self):
        """Ensure database connection is alive"""
        if self.conn is None or self.conn.closed:
            self.connect()

    def execute_query(self, query: str, params: tuple = None, fetch: bool = True) -> Optional[List[Dict]]:
        """Execute a query with automatic reconnection"""
        self.ensure_connection()
        try:
            with self.conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(query, params)
                if fetch:
                    return cursor.fetchall()
                self.conn.commit()
                return None
        except Exception as e:
            self.conn.rollback()
            print(f"Query error: {e}")
            raise

    # ============================================================================
    # STANDALONE REMINDERS
    # ============================================================================

    def get_due_standalone_reminders(self, current_time: datetime) -> List[Dict]:
        """Get all standalone non-recurring reminders that should trigger now"""
        query = """
            SELECT
                r.id, r.title, r.description, r.trigger_time,
                r.target_agents, r.created_by, r.created_at
            FROM reminders r
            WHERE r.event_id IS NULL
              AND r.recurrence_rule IS NULL
              AND r.trigger_time <= %s
            ORDER BY r.trigger_time ASC
        """
        return self.execute_query(query, (current_time,))

    def get_due_recurring_reminders(self, current_time: datetime) -> List[Dict]:
        """Get all standalone recurring reminders"""
        query = """
            SELECT
                r.id, r.title, r.description, r.trigger_time,
                r.recurrence_rule, r.exception_dates,
                r.target_agents, r.created_by, r.created_at
            FROM reminders r
            WHERE r.event_id IS NULL
              AND r.recurrence_rule IS NOT NULL
            ORDER BY r.trigger_time ASC
        """
        results = self.execute_query(query)

        # Parse JSON fields
        for result in results:
            if result.get('exception_dates'):
                try:
                    result['exception_dates'] = json.loads(result['exception_dates'])
                except:
                    result['exception_dates'] = []
            else:
                result['exception_dates'] = []

        return results

    def delete_reminder(self, reminder_id: int):
        """Hard delete a reminder after triggering"""
        query = "DELETE FROM reminders WHERE id = %s"
        self.execute_query(query, (reminder_id,), fetch=False)

    # ============================================================================
    # EVENT REMINDERS (Virtual - from auto_reminders)
    # ============================================================================

    def get_events_with_auto_reminders(self, current_time: datetime) -> List[Dict]:
        """
        Get all events that have auto_reminders set
        Returns events that might have reminders due soon
        """
        query = """
            SELECT
                e.id, e.title, e.start_time, e.end_time,
                e.recurrence_rule, e.exception_dates, e.auto_reminders
            FROM calendar_events e
            WHERE e.auto_reminders IS NOT NULL
              AND e.auto_reminders != '[]'::jsonb
              AND (
                  -- Non-recurring events within next 24 hours
                  (e.recurrence_rule IS NULL AND e.start_time >= %s AND e.start_time <= %s + INTERVAL '24 hours')
                  OR
                  -- Recurring events (check all instances)
                  (e.recurrence_rule IS NOT NULL AND e.start_time <= %s + INTERVAL '24 hours')
              )
            ORDER BY e.start_time ASC
        """
        results = self.execute_query(query, (current_time, current_time, current_time))

        # Parse JSON fields
        for result in results:
            if result.get('auto_reminders'):
                try:
                    result['auto_reminders'] = json.loads(result['auto_reminders'])
                except:
                    result['auto_reminders'] = []
            else:
                result['auto_reminders'] = []

            if result.get('exception_dates'):
                try:
                    result['exception_dates'] = json.loads(result['exception_dates'])
                except:
                    result['exception_dates'] = []
            else:
                result['exception_dates'] = []

        return results

    def close(self):
        """Close database connection"""
        if self.conn and not self.conn.closed:
            self.conn.close()
            print("✓ Database connection closed")

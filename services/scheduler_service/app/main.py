"""
Scheduler Service - Main Application
Polls database for due standalone reminders and generates virtual event reminders
Sends notifications to agents and frontend
"""

import time
import signal
import sys
import json
from datetime import datetime, timedelta
from dateutil import rrule
from database import DatabaseClient
from rabbitmq_client import RabbitMQClient
from api_client import APIClient


class SchedulerService:
    def __init__(self):
        self.db = DatabaseClient()
        self.rabbitmq = RabbitMQClient()
        self.api = APIClient()
        self.check_interval = 30  # seconds
        self.running = True

        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

    def shutdown(self, signum, frame):
        """Handle graceful shutdown"""
        print("\n⚠ Shutdown signal received. Cleaning up...")
        self.running = False
        self.db.close()
        self.rabbitmq.close()
        sys.exit(0)

    def generate_reminder_instances(self, trigger_time, recurrence_rule, exception_dates, max_instances=100):
        """Generate recurring reminder instances from RRULE"""
        try:
            rule = rrule.rrulestr(recurrence_rule, dtstart=trigger_time)
            instances = []
            exception_set = set(exception_dates)

            for idx, occurrence in enumerate(rule):
                if idx >= max_instances:
                    break
                occurrence_date = occurrence.date().isoformat()
                if occurrence_date in exception_set:
                    continue
                instances.append(occurrence)

            return instances
        except Exception as e:
            print(f"  ✗ Error generating reminder instances: {e}")
            return []

    def generate_event_instances(self, start_time, end_time, recurrence_rule, exception_dates, max_instances=100):
        """Generate recurring event instances from RRULE"""
        try:
            rule = rrule.rrulestr(recurrence_rule, dtstart=start_time)
            duration = end_time - start_time
            instances = []
            exception_set = set(exception_dates)

            for idx, occurrence in enumerate(rule):
                if idx >= max_instances:
                    break
                occurrence_date = occurrence.date().isoformat()
                if occurrence_date in exception_set:
                    continue
                instances.append({
                    'start_time': occurrence,
                    'end_time': occurrence + duration
                })

            return instances
        except Exception as e:
            print(f"  ✗ Error generating event instances: {e}")
            return []

    def check_due_reminders(self):
        """Check for standalone reminders and virtual event reminders that should trigger"""
        now = datetime.utcnow()
        triggered_count = 0

        try:
            # 1. Check standalone reminders (non-recurring)
            standalone_reminders = self.db.get_due_standalone_reminders(now)

            for reminder in standalone_reminders:
                try:
                    target_agents = reminder.get('target_agents', ['primary_agent'])

                    # Send notification to each target agent
                    for agent in target_agents:
                        notification = {
                            "notification_type": "system_alert",
                            "recipient_agent_id": agent,
                            "source": "scheduler_service",
                            "payload": {
                                "alert_type": "REMINDER",
                                "reminder_id": reminder['id'],
                                "title": reminder['title'],
                                "description": reminder['description'],
                                "trigger_time": str(reminder['trigger_time']),
                                "type": "standalone"
                            }
                        }
                        self.rabbitmq.publish_to_agent(agent, notification)

                    # Send to frontend with complete reminder data
                    self.api.send_app_interaction(
                        agent_id="calendar_agent",
                        app_name="reminders_app",
                        action="reminder_triggered",
                        result={
                            "reminder": {
                                "id": str(reminder['id']),
                                "title": reminder['title'],
                                "description": reminder.get('description', ''),
                                "trigger_time": reminder['trigger_time'].isoformat() + 'Z' if reminder['trigger_time'] else None,
                                "event_id": None,
                                "event_title": None,
                                "recurrence_rule": None,
                                "target_agents": target_agents,
                                "created_at": reminder['created_at'].isoformat() + 'Z' if reminder.get('created_at') else None
                            }
                        },
                        session_id="user_session_default"
                    )

                    # Delete reminder after triggering (hard delete)
                    self.db.delete_reminder(reminder['id'])

                    print(f"  ✓ Triggered standalone reminder #{reminder['id']}: {reminder['title']}")
                    triggered_count += 1

                except Exception as e:
                    print(f"  ✗ Error processing reminder #{reminder['id']}: {e}")

            # 2. Check standalone recurring reminders
            recurring_reminders = self.db.get_due_recurring_reminders(now)

            for reminder in recurring_reminders:
                try:
                    exception_dates = reminder.get('exception_dates', [])
                    instances = self.generate_reminder_instances(
                        reminder['trigger_time'],
                        reminder['recurrence_rule'],
                        exception_dates,
                        max_instances=100
                    )

                    # Check if any instance is due now (within 30 second window)
                    for instance_time in instances:
                        time_diff = (now - instance_time).total_seconds()
                        if 0 <= time_diff <= 30:  # Due within last 30 seconds
                            target_agents = reminder.get('target_agents', ['primary_agent'])

                            for agent in target_agents:
                                notification = {
                                    "notification_type": "system_alert",
                                    "recipient_agent_id": agent,
                                    "source": "scheduler_service",
                                    "payload": {
                                        "alert_type": "REMINDER",
                                        "reminder_id": reminder['id'],
                                        "title": reminder['title'],
                                        "description": reminder['description'],
                                        "trigger_time": str(instance_time),
                                        "type": "standalone_recurring"
                                    }
                                }
                                self.rabbitmq.publish_to_agent(agent, notification)

                            # Send to frontend with complete reminder data
                            self.api.send_app_interaction(
                                agent_id="calendar_agent",
                                app_name="reminders_app",
                                action="reminder_triggered",
                                result={
                                    "reminder": {
                                        "id": str(reminder['id']),
                                        "title": reminder['title'],
                                        "description": reminder.get('description', ''),
                                        "trigger_time": instance_time.isoformat() + 'Z' if instance_time else None,
                                        "event_id": None,
                                        "event_title": None,
                                        "recurrence_rule": reminder.get('recurrence_rule'),
                                        "target_agents": target_agents,
                                        "created_at": reminder['created_at'].isoformat() + 'Z' if reminder.get('created_at') else None
                                    }
                                },
                                session_id="user_session_default"
                            )

                            print(f"  ✓ Triggered recurring reminder #{reminder['id']}: {reminder['title']}")
                            triggered_count += 1

                except Exception as e:
                    print(f"  ✗ Error processing recurring reminder #{reminder['id']}: {e}")

            # 3. Check virtual event reminders (from event auto_reminders)
            events_with_reminders = self.db.get_events_with_auto_reminders(now)

            for event in events_with_reminders:
                try:
                    auto_reminders = event.get('auto_reminders', [])

                    if event.get('recurrence_rule'):
                        # Recurring event - generate instances
                        exception_dates = event.get('exception_dates', [])
                        event_instances = self.generate_event_instances(
                            event['start_time'],
                            event['end_time'],
                            event['recurrence_rule'],
                            exception_dates,
                            max_instances=100
                        )

                        # Check each instance
                        for event_instance in event_instances:
                            for minutes_before in auto_reminders:
                                reminder_time = event_instance['start_time'] - timedelta(minutes=minutes_before)
                                time_diff = (now - reminder_time).total_seconds()

                                if 0 <= time_diff <= 30:  # Due within last 30 seconds
                                    notification = {
                                        "notification_type": "system_alert",
                                        "recipient_agent_id": "primary_agent",
                                        "source": "scheduler_service",
                                        "payload": {
                                            "alert_type": "REMINDER",
                                            "event_id": event['id'],
                                            "event_title": event['title'],
                                            "title": f"Reminder: {event['title']}",
                                            "description": f"{minutes_before} minutes before event",
                                            "event_start_time": str(event_instance['start_time']),
                                            "trigger_time": str(reminder_time),
                                            "type": "event_attached"
                                        }
                                    }
                                    self.rabbitmq.publish_to_agent("primary_agent", notification)

                                    # Send to frontend with complete reminder data
                                    self.api.send_app_interaction(
                                        agent_id="calendar_agent",
                                        app_name="reminders_app",
                                        action="reminder_triggered",
                                        result={
                                            "reminder": {
                                                "id": f"event_{event['id']}_{minutes_before}min",
                                                "title": f"Reminder: {event['title']}",
                                                "description": f"{minutes_before} minutes before event",
                                                "trigger_time": reminder_time.isoformat() + 'Z' if reminder_time else None,
                                                "event_id": str(event['id']),
                                                "event_title": event['title'],
                                                "recurrence_rule": event.get('recurrence_rule'),
                                                "target_agents": ["primary_agent"],
                                                "created_at": None
                                            }
                                        },
                                        session_id="user_session_default"
                                    )

                                    print(f"  ✓ Triggered event reminder: {event['title']} ({minutes_before}min before)")
                                    triggered_count += 1

                    else:
                        # Single event
                        for minutes_before in auto_reminders:
                            reminder_time = event['start_time'] - timedelta(minutes=minutes_before)
                            time_diff = (now - reminder_time).total_seconds()

                            if 0 <= time_diff <= 30:  # Due within last 30 seconds
                                notification = {
                                    "notification_type": "system_alert",
                                    "recipient_agent_id": "primary_agent",
                                    "source": "scheduler_service",
                                    "payload": {
                                        "alert_type": "REMINDER",
                                        "event_id": event['id'],
                                        "event_title": event['title'],
                                        "title": f"Reminder: {event['title']}",
                                        "description": f"{minutes_before} minutes before event",
                                        "event_start_time": str(event['start_time']),
                                        "trigger_time": str(reminder_time),
                                        "type": "event_attached"
                                    }
                                }
                                self.rabbitmq.publish_to_agent("primary_agent", notification)

                                # Send to frontend with complete reminder data
                                self.api.send_app_interaction(
                                    agent_id="calendar_agent",
                                    app_name="reminders_app",
                                    action="reminder_triggered",
                                    result={
                                        "reminder": {
                                            "id": f"event_{event['id']}_{minutes_before}min",
                                            "title": f"Reminder: {event['title']}",
                                            "description": f"{minutes_before} minutes before event",
                                            "trigger_time": reminder_time.isoformat() + 'Z' if reminder_time else None,
                                            "event_id": str(event['id']),
                                            "event_title": event['title'],
                                            "recurrence_rule": None,
                                            "target_agents": ["primary_agent"],
                                            "created_at": None
                                        }
                                    },
                                    session_id="user_session_default"
                                )

                                print(f"  ✓ Triggered event reminder: {event['title']} ({minutes_before}min before)")
                                triggered_count += 1

                except Exception as e:
                    print(f"  ✗ Error processing event #{event['id']} reminders: {e}")

            if triggered_count > 0:
                print(f"  ✓ Triggered {triggered_count} reminder(s)")

        except Exception as e:
            print(f"  ✗ Error checking reminders: {e}")

    def run(self):
        """Main loop"""
        print("=" * 60)
        print("  SCHEDULER SERVICE STARTED")
        print("=" * 60)
        print(f"  Check interval: {self.check_interval} seconds")
        print(f"  Monitoring: standalone reminders + virtual event reminders")
        print("=" * 60)

        while self.running:
            try:
                print(f"\n[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC] Checking...")

                self.check_due_reminders()

                print(f"  ✓ Check complete. Sleeping for {self.check_interval}s...")
                time.sleep(self.check_interval)

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"  ✗ Error in scheduler loop: {e}")
                print(f"  Retrying in {self.check_interval}s...")
                time.sleep(self.check_interval)

        print("\n✓ Scheduler service stopped gracefully")


if __name__ == "__main__":
    service = SchedulerService()
    service.run()

"""
Call Manager Service

Manages voice call lifecycle, state transitions, and agent routing.
"""

import asyncio
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any
from uuid import UUID, uuid4
from dataclasses import dataclass, field

import os
import pika
import httpx
from pika.exceptions import AMQPConnectionError, AMQPChannelError

logger = logging.getLogger(__name__)

# Twilio Gateway URL for terminating calls
TWILIO_GATEWAY_URL = os.getenv("TWILIO_GATEWAY_URL", "http://twilio_gateway:8200")


class CallStatus(str, Enum):
    """Call lifecycle states"""
    RINGING_OUTBOUND = "ringing_outbound"  # User calling agent
    RINGING_INBOUND = "ringing_inbound"    # Agent calling user
    CONNECTED = "connected"                 # Active call
    ON_HOLD = "on_hold"                    # Call paused
    TRANSFERRING = "transferring"          # Handoff in progress
    ENDED = "ended"                        # Call terminated


class CallEndReason(str, Enum):
    """Reasons for call termination"""
    USER_HANGUP = "user_hangup"
    AGENT_HANGUP = "agent_hangup"
    USER_DECLINED = "user_declined"
    AGENT_DECLINED = "agent_declined"
    TRANSFER_COMPLETE = "transfer_complete"
    TIMEOUT = "timeout"
    ERROR = "error"
    DISCONNECTED = "disconnected"


@dataclass
class Call:
    """In-memory representation of an active call"""
    call_id: UUID
    session_id: str
    initiated_by: str  # 'user' or agent_id
    initial_target: str  # Target agent
    current_agent_id: str
    status: CallStatus
    started_at: datetime
    ringing_at: Optional[datetime] = None
    connected_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    end_reason: Optional[CallEndReason] = None
    ended_by: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Twilio-specific fields
    twilio_call_sid: Optional[str] = None
    caller_phone_number: Optional[str] = None
    call_source: str = "web"  # "web", "twilio_inbound", "twilio_outbound"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return {
            "call_id": str(self.call_id),
            "session_id": self.session_id,
            "initiated_by": self.initiated_by,
            "initial_target": self.initial_target,
            "current_agent_id": self.current_agent_id,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "ringing_at": self.ringing_at.isoformat() if self.ringing_at else None,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "end_reason": self.end_reason.value if self.end_reason else None,
            "ended_by": self.ended_by,
            "duration_seconds": self.get_duration(),
            "metadata": self.metadata,
            "twilio_call_sid": self.twilio_call_sid,
            "caller_phone_number": self.caller_phone_number,
            "call_source": self.call_source
        }

    def get_duration(self) -> Optional[int]:
        """Get call duration in seconds"""
        if not self.connected_at:
            return None
        end_time = self.ended_at or datetime.utcnow()
        # Ensure both datetimes are naive (strip timezone info if present)
        connected = self.connected_at.replace(tzinfo=None) if self.connected_at.tzinfo else self.connected_at
        ended = end_time.replace(tzinfo=None) if end_time.tzinfo else end_time
        return int((ended - connected).total_seconds())


class CallManager:
    """
    Manages voice call lifecycle and state.

    Responsibilities:
    - Track active calls in memory
    - Persist call records to database
    - Handle state transitions
    - Route audio between user and agents
    - Manage agent handoffs
    - Publish call events
    - Enforce call timeouts
    """

    # Ringing timeout in seconds (no answer = auto-end)
    RINGING_TIMEOUT = 30
    # Hold timeout in seconds (on hold too long = auto-end)
    HOLD_TIMEOUT = 300  # 5 minutes
    # Maximum call duration in seconds (prevent zombie calls)
    MAX_CALL_DURATION = 1800  # 30 minutes
    # Timeout check interval in seconds
    TIMEOUT_CHECK_INTERVAL = 5

    def __init__(self, db_client, rabbitmq_url: str):
        """
        Initialize Call Manager.

        Args:
            db_client: Database client for persistence
            rabbitmq_url: RabbitMQ connection URL
        """
        self.db = db_client
        self.rabbitmq_url = rabbitmq_url
        self.connection_params = pika.URLParameters(rabbitmq_url)

        # In-memory active calls: session_id -> Call
        self._active_calls: Dict[str, Call] = {}
        self._lock = asyncio.Lock()

        # Call event callbacks (for WebSocket notifications)
        # Keyed by session_id to prevent duplicate registrations
        self._event_callbacks: Dict[str, Any] = {}

        # Timeout monitor task
        self._timeout_task: Optional[asyncio.Task] = None
        self._running = False

        # Schedule state restoration
        asyncio.create_task(self._restore_calls())

        logger.info("CallManager initialized")

    async def _restore_calls(self):
        """Restore active calls from database on startup"""
        try:
            # Brief delay to ensure DB connection is ready if needed
            await asyncio.sleep(1)

            query = """
            SELECT call_id, session_id, initiated_by, initial_target,
                   current_agent_id, call_status, started_at, ringing_at,
                   connected_at, ended_at, metadata,
                   twilio_call_sid, caller_phone_number, call_source
            FROM calls
            WHERE call_status IN ('ringing_outbound', 'ringing_inbound', 'connected', 'on_hold', 'transferring')
            """

            results = self.db.execute_query_dict(query)

            restored_count = 0
            for row in results:
                try:
                    call_id = UUID(row['call_id'])
                    session_id = row['session_id']

                    # Parse metadata
                    metadata = {}
                    if row['metadata']:
                         if isinstance(row['metadata'], str):
                             metadata = json.loads(row['metadata'])
                         else:
                             metadata = row['metadata']

                    call = Call(
                        call_id=call_id,
                        session_id=session_id,
                        initiated_by=row['initiated_by'],
                        initial_target=row['initial_target'],
                        current_agent_id=row['current_agent_id'],
                        status=CallStatus(row['call_status']),
                        started_at=row['started_at'],
                        ringing_at=row['ringing_at'],
                        connected_at=row['connected_at'],
                        ended_at=row['ended_at'],
                        metadata=metadata,
                        twilio_call_sid=row.get('twilio_call_sid'),
                        caller_phone_number=row.get('caller_phone_number'),
                        call_source=row.get('call_source', 'web')
                    )

                    self._active_calls[session_id] = call
                    restored_count += 1

                except Exception as e:
                    logger.error(f"Failed to restore call row: {e}")

            if restored_count > 0:
                logger.info(f"Restored {restored_count} active calls from database")
                
        except Exception as e:
            logger.error(f"Error restoring calls from database: {e}")

    def register_event_callback(self, session_id: str, callback):
        """Register a callback for call events (keyed by session_id to prevent duplicates)"""
        self._event_callbacks[session_id] = callback
        logger.debug(f"Registered call event callback for session {session_id}")

    def unregister_event_callback(self, session_id: str):
        """Unregister a callback for call events"""
        if session_id in self._event_callbacks:
            del self._event_callbacks[session_id]
            logger.debug(f"Unregistered call event callback for session {session_id}")

    def start_timeout_monitor(self):
        """Start the background timeout monitor task"""
        if self._timeout_task is None or self._timeout_task.done():
            self._running = True
            self._timeout_task = asyncio.create_task(self._timeout_monitor_loop())
            logger.info("Call timeout monitor started")

    def stop_timeout_monitor(self):
        """Stop the timeout monitor task"""
        self._running = False
        if self._timeout_task:
            self._timeout_task.cancel()
            logger.info("Call timeout monitor stopped")

    async def _timeout_monitor_loop(self):
        """Background loop to check for timed-out calls"""
        while self._running:
            try:
                await self._check_timeouts()
                await asyncio.sleep(self.TIMEOUT_CHECK_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in timeout monitor: {e}")
                await asyncio.sleep(self.TIMEOUT_CHECK_INTERVAL)

    def _to_naive(self, dt: datetime) -> datetime:
        """Convert datetime to naive (strip timezone) for consistent comparisons"""
        if dt is None:
            return None
        return dt.replace(tzinfo=None) if dt.tzinfo else dt

    async def _check_timeouts(self):
        """Check all active calls for timeouts"""
        now = datetime.utcnow()
        calls_to_end = []

        # Check each active call (without holding lock during iteration)
        for session_id, call in list(self._active_calls.items()):
            timeout_reason = None

            # Check ringing timeout
            if call.status in (CallStatus.RINGING_OUTBOUND, CallStatus.RINGING_INBOUND):
                if call.ringing_at:
                    ringing_at_naive = self._to_naive(call.ringing_at)
                    ringing_duration = (now - ringing_at_naive).total_seconds()
                    if ringing_duration > self.RINGING_TIMEOUT:
                        timeout_reason = f"ringing_timeout ({ringing_duration:.0f}s)"

            # Check hold timeout
            elif call.status == CallStatus.ON_HOLD:
                hold_started = call.metadata.get("hold_started_at")
                if hold_started:
                    try:
                        hold_start_time = self._to_naive(datetime.fromisoformat(hold_started))
                        hold_duration = (now - hold_start_time).total_seconds()
                        if hold_duration > self.HOLD_TIMEOUT:
                            timeout_reason = f"hold_timeout ({hold_duration:.0f}s)"
                    except ValueError:
                        pass

            # Check max call duration
            elif call.status == CallStatus.CONNECTED:
                if call.connected_at:
                    connected_at_naive = self._to_naive(call.connected_at)
                    call_duration = (now - connected_at_naive).total_seconds()
                    if call_duration > self.MAX_CALL_DURATION:
                        timeout_reason = f"max_duration ({call_duration:.0f}s)"

            if timeout_reason:
                calls_to_end.append((call, timeout_reason))

        # End timed-out calls
        for call, reason in calls_to_end:
            logger.warning(f"Call {call.call_id} timed out: {reason}")
            try:
                await self.end_call(call.call_id, "system", CallEndReason.TIMEOUT)
            except Exception as e:
                logger.error(f"Failed to end timed-out call {call.call_id}: {e}")

    async def _emit_event(self, event_type: str, call: Call, data: Dict[str, Any] = None):
        """Emit call event to all registered callbacks"""
        event = {
            "type": event_type,
            "call": call.to_dict(),
            "data": data or {},
            "timestamp": datetime.utcnow().isoformat()
        }
        for session_id, callback in list(self._event_callbacks.items()):
            try:
                await callback(event)
            except Exception as e:
                logger.error(f"Error in event callback for session {session_id}: {e}")

    # =========================================================================
    # Call Initiation
    # =========================================================================

    async def initiate_call(
        self,
        session_id: str,
        initiated_by: str,
        target_agent: str = "primary_agent",
        fast_mode: bool = False
    ) -> Call:
        """
        Initiate a new call.

        Args:
            session_id: User session ID
            initiated_by: 'user' or agent_id initiating the call
            target_agent: Target agent to call
            fast_mode: Enable fast mode with reduced latency model and limited tools

        Returns:
            The created Call object

        Raises:
            ValueError: If session already has an active call
        """
        async with self._lock:
            # Check for existing active call
            if session_id in self._active_calls:
                existing = self._active_calls[session_id]
                if existing.status != CallStatus.ENDED:
                    raise ValueError(f"Session {session_id} already has an active call")

            # Determine call direction
            if initiated_by == "user":
                status = CallStatus.RINGING_OUTBOUND
            else:
                status = CallStatus.RINGING_INBOUND

            # Create call with fast_mode in metadata
            call = Call(
                call_id=uuid4(),
                session_id=session_id,
                initiated_by=initiated_by,
                initial_target=target_agent,
                current_agent_id=target_agent,
                status=status,
                started_at=datetime.utcnow(),
                ringing_at=datetime.utcnow(),
                metadata={"fast_mode": fast_mode} if fast_mode else {}
            )

            # Store in memory
            self._active_calls[session_id] = call

            # Persist to database
            await self._persist_call(call)

            # Log event
            await self._log_event(call, "call_initiated", {
                "initiated_by": initiated_by,
                "target": target_agent
            })

            # Notify target agent via RabbitMQ
            await self._notify_agent_incoming_call(call)

            # Emit event for WebSocket
            await self._emit_event("call_ringing", call)

            logger.info(f"Call initiated: {call.call_id} ({initiated_by} -> {target_agent})")

            return call

    async def create_twilio_inbound_call(
        self,
        twilio_call_sid: str,
        caller_phone_number: str,
        target_agent: str = "primary_agent",
        call_id: Optional[str] = None
    ) -> Call:
        """
        Create a call record for an incoming Twilio phone call.

        Args:
            twilio_call_sid: Twilio's call SID
            caller_phone_number: Caller's phone number (E.164 format)
            target_agent: Target agent to handle the call
            call_id: Optional call_id from twilio_gateway (use this to maintain consistency)

        Returns:
            The created Call object
        """
        session_id = f"twilio_{twilio_call_sid}"

        async with self._lock:
            # Check for existing call with this Twilio SID
            for existing in self._active_calls.values():
                if existing.twilio_call_sid == twilio_call_sid:
                    logger.warning(f"Twilio call already exists: {twilio_call_sid}")
                    return existing

            # Use provided call_id if given, otherwise generate new one
            # IMPORTANT: Using the same call_id as twilio_gateway ensures voice_gateway
            # can route TTS audio correctly to Twilio
            actual_call_id = UUID(call_id) if call_id else uuid4()
            call = Call(
                call_id=actual_call_id,
                session_id=session_id,
                initiated_by="phone_user",
                initial_target=target_agent,
                current_agent_id=target_agent,
                status=CallStatus.RINGING_INBOUND,
                started_at=datetime.utcnow(),
                ringing_at=datetime.utcnow(),
                twilio_call_sid=twilio_call_sid,
                caller_phone_number=caller_phone_number,
                call_source="twilio_inbound",
                metadata={"phone_number": caller_phone_number}
            )

            self._active_calls[session_id] = call
            await self._persist_call(call)
            await self._log_event(call, "twilio_call_initiated", {
                "twilio_call_sid": twilio_call_sid,
                "caller_phone_number": caller_phone_number
            })
            await self._notify_agent_incoming_call(call)
            await self._emit_event("call_ringing", call)

            logger.info(f"Twilio inbound call created: {call.call_id} from {caller_phone_number}")
            return call

    async def create_twilio_outbound_call(
        self,
        session_id: str,
        twilio_call_sid: str,
        to_phone_number: str,
        target_agent: str = "primary_agent"
    ) -> Call:
        """
        Create a call record for an outbound Twilio phone call.

        Args:
            session_id: VOS session ID for the call
            twilio_call_sid: Twilio's call SID
            to_phone_number: Phone number being called (E.164 format)
            target_agent: Agent initiating the call

        Returns:
            The created Call object
        """
        async with self._lock:
            call = Call(
                call_id=uuid4(),
                session_id=session_id,
                initiated_by=target_agent,
                initial_target="phone_user",
                current_agent_id=target_agent,
                status=CallStatus.RINGING_OUTBOUND,
                started_at=datetime.utcnow(),
                ringing_at=datetime.utcnow(),
                twilio_call_sid=twilio_call_sid,
                caller_phone_number=to_phone_number,
                call_source="twilio_outbound",
                metadata={"phone_number": to_phone_number}
            )

            self._active_calls[session_id] = call
            await self._persist_call(call)
            await self._log_event(call, "twilio_outbound_initiated", {
                "twilio_call_sid": twilio_call_sid,
                "to_phone_number": to_phone_number
            })
            await self._emit_event("call_ringing", call)

            logger.info(f"Twilio outbound call created: {call.call_id} to {to_phone_number}")
            return call

    def get_call_by_twilio_sid(self, twilio_call_sid: str) -> Optional[Call]:
        """Get call by Twilio call SID"""
        for call in self._active_calls.values():
            if call.twilio_call_sid == twilio_call_sid:
                return call
        return None

    # =========================================================================
    # Call Answer / Decline
    # =========================================================================

    async def answer_call(self, call_id: UUID, answered_by: str) -> bool:
        """
        Answer an incoming call.

        This method is idempotent - if the call is already CONNECTED, it returns
        True. This handles Twilio calls where the media stream starts before the
        agent explicitly answers.

        Args:
            call_id: Call to answer
            answered_by: Agent answering the call

        Returns:
            True if successful (or call already connected)
        """
        async with self._lock:
            call = self._find_call_by_id(call_id)
            if not call:
                logger.warning(f"Call not found: {call_id}")
                return False

            # Idempotent: if already connected, just update agent if needed
            if call.status == CallStatus.CONNECTED:
                logger.info(f"Call {call_id} already connected, updating handler to {answered_by}")
                # Update current handler if an agent is answering (not 'user')
                if answered_by != "user" and call.current_agent_id != answered_by:
                    call.current_agent_id = answered_by
                    await self._update_call(call)
                return True

            if call.status == CallStatus.ENDED:
                logger.warning(f"Call {call_id} has already ended")
                return False

            if call.status not in (CallStatus.RINGING_OUTBOUND, CallStatus.RINGING_INBOUND):
                logger.warning(f"Call {call_id} is in unexpected status: {call.status}")
                return False

            # Update state
            call.status = CallStatus.CONNECTED
            call.connected_at = datetime.utcnow()
            call.current_agent_id = answered_by

            # Persist
            await self._update_call(call)
            await self._log_event(call, "call_answered", {"answered_by": answered_by})

            # Add participant record
            await self._add_participant(call, answered_by, "receiver")

            # Emit event
            await self._emit_event("call_connected", call)

            # Notify the agent that initiated the call that user answered
            if call.initiated_by != "user" and answered_by == "user":
                await self._notify_agent_call_answered(call)

            logger.info(f"Call answered: {call.call_id} by {answered_by}")
            return True

    async def decline_call(
        self,
        call_id: UUID,
        declined_by: str,
        reason: str = None
    ) -> bool:
        """
        Decline an incoming call.

        Args:
            call_id: Call to decline
            declined_by: 'user' or agent_id declining
            reason: Optional reason for declining

        Returns:
            True if successful
        """
        async with self._lock:
            call = self._find_call_by_id(call_id)
            if not call:
                return False

            if call.status not in (CallStatus.RINGING_OUTBOUND, CallStatus.RINGING_INBOUND):
                return False

            # Determine end reason
            if declined_by == "user":
                end_reason = CallEndReason.USER_DECLINED
            else:
                end_reason = CallEndReason.AGENT_DECLINED

            # End the call
            call.status = CallStatus.ENDED
            call.ended_at = datetime.utcnow()
            call.end_reason = end_reason
            call.ended_by = declined_by

            # Persist
            await self._update_call(call)
            await self._log_event(call, "call_declined", {
                "declined_by": declined_by,
                "reason": reason
            })

            # Remove from active calls
            del self._active_calls[call.session_id]

            # Emit event
            await self._emit_event("call_ended", call, {"reason": "declined"})

            logger.info(f"Call declined: {call.call_id} by {declined_by}")
            return True

    # =========================================================================
    # Call End
    # =========================================================================

    async def end_call(
        self,
        call_id: UUID,
        ended_by: str,
        reason: CallEndReason = None
    ) -> bool:
        """
        End an active call.

        Args:
            call_id: Call to end
            ended_by: 'user' or agent_id ending the call
            reason: End reason (defaults based on ended_by)

        Returns:
            True if successful
        """
        async with self._lock:
            call = self._find_call_by_id(call_id)
            if not call:
                return False

            if call.status == CallStatus.ENDED:
                return False

            # Determine reason if not provided
            if reason is None:
                reason = CallEndReason.USER_HANGUP if ended_by == "user" else CallEndReason.AGENT_HANGUP

            # Update state
            call.status = CallStatus.ENDED
            call.ended_at = datetime.utcnow()
            call.end_reason = reason
            call.ended_by = ended_by

            # Persist
            await self._update_call(call)
            await self._log_event(call, "call_ended", {
                "ended_by": ended_by,
                "reason": reason.value
            })

            # Mark participant as left
            await self._participant_left(call, call.current_agent_id)

            # Remove from active calls
            if call.session_id in self._active_calls:
                del self._active_calls[call.session_id]

            # Emit event
            await self._emit_event("call_ended", call, {"reason": reason.value})

            # Notify the agent that the call ended
            await self._notify_agent_call_ended(call)

            # Notify voice_gateway to cleanup call audio bridge session
            await self._notify_voice_gateway_call_ended(call)

            # If this is a Twilio call, terminate it via Twilio API
            if call.twilio_call_sid:
                await self._terminate_twilio_call(call.twilio_call_sid)

            logger.info(f"Call ended: {call.call_id} by {ended_by} ({reason.value})")
            return True

    # =========================================================================
    # Hold / Resume
    # =========================================================================

    async def hold_call(self, call_id: UUID, reason: str = "manual") -> bool:
        """
        Put a call on hold.

        Args:
            call_id: Call to hold
            reason: Reason for hold - "manual" or "user_disconnected"

        Returns:
            True if successful
        """
        async with self._lock:
            call = self._find_call_by_id(call_id)
            if not call or call.status != CallStatus.CONNECTED:
                return False

            call.status = CallStatus.ON_HOLD
            call.metadata["hold_started_at"] = datetime.utcnow().isoformat()
            call.metadata["hold_reason"] = reason

            await self._update_call(call)
            await self._log_event(call, "call_hold", {"reason": reason})
            await self._emit_event("call_on_hold", call, {"reason": reason})

            # Notify agent that call is on hold
            await self._notify_agent_call_on_hold(call, reason)

            logger.info(f"Call on hold: {call.call_id} (reason: {reason})")
            return True

    async def resume_call(self, call_id: UUID) -> bool:
        """
        Resume a held call.

        Args:
            call_id: Call to resume

        Returns:
            True if successful
        """
        async with self._lock:
            call = self._find_call_by_id(call_id)
            if not call or call.status != CallStatus.ON_HOLD:
                return False

            previous_reason = call.metadata.get("hold_reason", "manual")
            call.status = CallStatus.CONNECTED
            call.metadata.pop("hold_started_at", None)
            call.metadata.pop("hold_reason", None)

            await self._update_call(call)
            await self._log_event(call, "call_resumed", {"previous_hold_reason": previous_reason})
            await self._emit_event("call_connected", call)

            # Notify agent that call has resumed
            await self._notify_agent_call_resumed(call, previous_reason)

            logger.info(f"Call resumed: {call.call_id}")
            return True

    # =========================================================================
    # Transfer
    # =========================================================================

    async def transfer_call(
        self,
        call_id: UUID,
        from_agent: str,
        to_agent: str,
        announcement: str = None
    ) -> bool:
        """
        Transfer a call to another agent.

        Args:
            call_id: Call to transfer
            from_agent: Agent transferring the call
            to_agent: Agent receiving the call
            announcement: Optional message to speak before transfer

        Returns:
            True if successful
        """
        async with self._lock:
            call = self._find_call_by_id(call_id)
            if not call or call.status != CallStatus.CONNECTED:
                return False

            if call.current_agent_id != from_agent:
                logger.warning(f"Agent {from_agent} cannot transfer call owned by {call.current_agent_id}")
                return False

            # Set transferring state
            call.status = CallStatus.TRANSFERRING
            call.metadata["transfer_to"] = to_agent
            call.metadata["transfer_announcement"] = announcement

            await self._update_call(call)
            await self._log_event(call, "call_transferring", {
                "from_agent": from_agent,
                "to_agent": to_agent
            })
            await self._emit_event("call_transferring", call, {
                "from_agent": from_agent,
                "to_agent": to_agent,
                "announcement": announcement
            })

            # Mark previous agent as left
            await self._participant_left(call, from_agent)

            # Update current agent
            call.current_agent_id = to_agent
            call.status = CallStatus.CONNECTED
            call.metadata.pop("transfer_to", None)
            call.metadata.pop("transfer_announcement", None)

            # Add new participant
            await self._add_participant(call, to_agent, "transferred", from_agent)

            # Notify new agent
            await self._notify_agent_transfer(call, from_agent, to_agent)

            await self._update_call(call)
            await self._emit_event("call_connected", call, {"transferred_from": from_agent})

            logger.info(f"Call transferred: {call.call_id} from {from_agent} to {to_agent}")
            return True

    async def recall_phone(self, call_id: UUID, by_agent: str) -> bool:
        """
        Recall the phone back to the primary agent.

        Args:
            call_id: Call to recall
            by_agent: Agent recalling (usually primary_agent)

        Returns:
            True if successful
        """
        # This is essentially a transfer back
        call = self._find_call_by_id(call_id)
        if not call:
            return False

        return await self.transfer_call(call_id, call.current_agent_id, by_agent)

    # =========================================================================
    # Query Methods
    # =========================================================================

    def get_active_call(self, session_id: str) -> Optional[Call]:
        """Get active call for a session"""
        call = self._active_calls.get(session_id)
        if call and call.status != CallStatus.ENDED:
            return call
        return None

    def get_call_by_id(self, call_id: UUID) -> Optional[Call]:
        """Get call by ID"""
        return self._find_call_by_id(call_id)

    def _find_call_by_id(self, call_id: UUID) -> Optional[Call]:
        """Find call by ID in active calls"""
        for call in self._active_calls.values():
            if call.call_id == call_id:
                return call
        return None

    def is_on_call(self, session_id: str) -> bool:
        """Check if session has an active call"""
        call = self.get_active_call(session_id)
        return call is not None and call.status == CallStatus.CONNECTED

    def get_call_for_agent(self, agent_id: str) -> Optional[Call]:
        """Get active call handled by an agent"""
        for call in self._active_calls.values():
            if call.current_agent_id == agent_id and call.status == CallStatus.CONNECTED:
                return call
        return None

    # =========================================================================
    # Database Persistence
    # =========================================================================

    async def _persist_call(self, call: Call):
        """Persist a new call to database"""
        query = """
        INSERT INTO calls (call_id, session_id, initiated_by, initial_target,
                          current_agent_id, call_status, started_at, ringing_at, metadata,
                          twilio_call_sid, caller_phone_number, call_source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        try:
            self.db.execute_query(query, (
                str(call.call_id),
                call.session_id,
                call.initiated_by,
                call.initial_target,
                call.current_agent_id,
                call.status.value,
                call.started_at,
                call.ringing_at,
                json.dumps(call.metadata),
                call.twilio_call_sid,
                call.caller_phone_number,
                call.call_source
            ))
        except Exception as e:
            logger.error(f"Failed to persist call: {e}")

    async def _update_call(self, call: Call):
        """Update call in database"""
        query = """
        UPDATE calls SET
            current_agent_id = %s,
            call_status = %s,
            connected_at = %s,
            ended_at = %s,
            end_reason = %s,
            ended_by = %s,
            metadata = %s,
            twilio_call_sid = %s,
            caller_phone_number = %s,
            call_source = %s
        WHERE call_id = %s
        """
        try:
            self.db.execute_query(query, (
                call.current_agent_id,
                call.status.value,
                call.connected_at,
                call.ended_at,
                call.end_reason.value if call.end_reason else None,
                call.ended_by,
                json.dumps(call.metadata),
                call.twilio_call_sid,
                call.caller_phone_number,
                call.call_source,
                str(call.call_id)
            ))
        except Exception as e:
            logger.error(f"Failed to update call: {e}")

    async def _log_event(self, call: Call, event_type: str, data: Dict[str, Any]):
        """Log call event to database"""
        query = """
        INSERT INTO call_events (call_id, event_type, event_data, triggered_by)
        VALUES (%s, %s, %s, %s)
        """
        try:
            self.db.execute_query(query, (
                str(call.call_id),
                event_type,
                json.dumps(data),
                data.get("initiated_by") or data.get("ended_by") or call.current_agent_id
            ))
        except Exception as e:
            logger.error(f"Failed to log call event: {e}")

    async def _add_participant(
        self,
        call: Call,
        agent_id: str,
        role: str,
        transferred_from: str = None
    ):
        """Add participant record"""
        query = """
        INSERT INTO call_participants (call_id, agent_id, role, transferred_from)
        VALUES (%s, %s, %s, %s)
        """
        try:
            self.db.execute_query(query, (
                str(call.call_id),
                agent_id,
                role,
                transferred_from
            ))
        except Exception as e:
            logger.error(f"Failed to add participant: {e}")

    async def _participant_left(self, call: Call, agent_id: str):
        """Mark participant as left"""
        query = """
        UPDATE call_participants SET left_at = NOW()
        WHERE call_id = %s AND agent_id = %s AND left_at IS NULL
        """
        try:
            self.db.execute_query(query, (str(call.call_id), agent_id))
        except Exception as e:
            logger.error(f"Failed to update participant: {e}")

    # =========================================================================
    # RabbitMQ Agent Notifications
    # =========================================================================

    async def _notify_agent_incoming_call(self, call: Call):
        """Notify agent of incoming call via RabbitMQ"""
        notification = {
            "notification_id": str(uuid4()),
            "timestamp": datetime.utcnow().timestamp(),
            "recipient_agent_id": call.current_agent_id,
            "notification_type": "incoming_call",
            "source": "call_manager",
            "payload": {
                "call_id": str(call.call_id),
                "session_id": call.session_id,
                "initiated_by": call.initiated_by,
                "content_type": "call",
                # Include twilio_call_sid for phone calls (used by call tools for fallback)
                "twilio_call_sid": call.twilio_call_sid,
                "caller_phone_number": call.caller_phone_number
            }
        }
        await self._publish_to_agent_queue(call.current_agent_id, notification)

    async def _notify_agent_transfer(self, call: Call, from_agent: str, to_agent: str):
        """Notify agent of call transfer"""
        notification = {
            "notification_id": str(uuid4()),
            "timestamp": datetime.utcnow().timestamp(),
            "recipient_agent_id": to_agent,
            "notification_type": "call_transferred",
            "source": from_agent,
            "payload": {
                "call_id": str(call.call_id),
                "session_id": call.session_id,
                "transferred_from": from_agent,
                "content_type": "call"
            }
        }
        await self._publish_to_agent_queue(to_agent, notification)

    async def _notify_agent_call_answered(self, call: Call):
        """Notify agent that their outbound call was answered by user"""
        notification = {
            "notification_id": str(uuid4()),
            "timestamp": datetime.utcnow().timestamp(),
            "recipient_agent_id": call.initiated_by,
            "notification_type": "call_answered",
            "source": "call_manager",
            "payload": {
                "call_id": str(call.call_id),
                "session_id": call.session_id,
                "answered_by": "user",
                "content_type": "call"
            }
        }
        await self._publish_to_agent_queue(call.initiated_by, notification)
        logger.info(f"Notified {call.initiated_by} that call {call.call_id} was answered")

    async def _notify_agent_call_on_hold(self, call: Call, reason: str):
        """Notify agent that the call is on hold"""
        instruction = "The call is now on hold."
        if reason == "user_disconnected":
            instruction = "The user has disconnected. The call is on hold. If they don't reconnect within 5 minutes, the call will automatically end. Do NOT use the speak tool while on hold."
        else:
            instruction = "The call has been put on hold. Do NOT use the speak tool while on hold."

        notification = {
            "notification_id": str(uuid4()),
            "timestamp": datetime.utcnow().timestamp(),
            "recipient_agent_id": call.current_agent_id,
            "notification_type": "call_on_hold",
            "source": "call_manager",
            "payload": {
                "call_id": str(call.call_id),
                "session_id": call.session_id,
                "reason": reason,
                "content_type": "call",
                "_instruction": instruction
            }
        }
        await self._publish_to_agent_queue(call.current_agent_id, notification)
        logger.info(f"Notified {call.current_agent_id} that call {call.call_id} is on hold (reason: {reason})")

    async def _notify_agent_call_resumed(self, call: Call, previous_reason: str):
        """Notify agent that the call has resumed"""
        instruction = "The call has resumed. You can now use the speak tool again."
        if previous_reason == "user_disconnected":
            instruction = "The user has reconnected. The call has resumed. You can now use the speak tool again."

        notification = {
            "notification_id": str(uuid4()),
            "timestamp": datetime.utcnow().timestamp(),
            "recipient_agent_id": call.current_agent_id,
            "notification_type": "call_resumed",
            "source": "call_manager",
            "payload": {
                "call_id": str(call.call_id),
                "session_id": call.session_id,
                "previous_hold_reason": previous_reason,
                "content_type": "call",
                "_instruction": instruction
            }
        }
        await self._publish_to_agent_queue(call.current_agent_id, notification)
        logger.info(f"Notified {call.current_agent_id} that call {call.call_id} has resumed")

    async def _notify_agent_call_ended(self, call: Call):
        """Notify agent that the call has ended"""
        notification = {
            "notification_id": str(uuid4()),
            "timestamp": datetime.utcnow().timestamp(),
            "recipient_agent_id": call.current_agent_id,
            "notification_type": "call_ended",
            "source": "call_manager",
            "payload": {
                "call_id": str(call.call_id),
                "session_id": call.session_id,
                "ended_by": call.ended_by,
                "end_reason": call.end_reason.value if call.end_reason else "unknown",
                "duration_seconds": int((call.ended_at - call.connected_at).total_seconds()) if call.ended_at and call.connected_at else 0,
                "content_type": "call",
                "_instruction": "The call has ended. You are no longer on a call. Use send_user_message (not speak) for any follow-up."
            }
        }
        await self._publish_to_agent_queue(call.current_agent_id, notification)
        logger.info(f"Notified {call.current_agent_id} that call {call.call_id} ended")

    async def _notify_voice_gateway_call_ended(self, call: Call):
        """Notify voice_gateway to cleanup call audio bridge session"""
        notification = {
            "notification_id": str(uuid4()),
            "timestamp": datetime.utcnow().timestamp(),
            "recipient_agent_id": "voice_gateway",
            "notification_type": "call_ended",
            "source": "call_manager",
            "payload": {
                "call_id": str(call.call_id),
                "session_id": call.session_id
            }
        }

        try:
            connection = pika.BlockingConnection(self.connection_params)
            channel = connection.channel()
            channel.queue_declare(queue="call_audio_queue", durable=True)

            channel.basic_publish(
                exchange='',
                routing_key="call_audio_queue",
                body=json.dumps(notification),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type='application/json'
                )
            )

            channel.close()
            connection.close()
            logger.info(f"Notified voice_gateway to cleanup call {call.call_id}")

        except Exception as e:
            logger.error(f"Failed to notify voice_gateway of call end: {e}")

    async def _terminate_twilio_call(self, twilio_call_sid: str):
        """
        Terminate a Twilio phone call via the Twilio Gateway.

        Args:
            twilio_call_sid: Twilio call SID to terminate
        """
        try:
            # Read internal API key
            internal_key = ""
            try:
                with open("/shared/internal_api_key", "r") as f:
                    internal_key = f.read().strip()
            except FileNotFoundError:
                pass

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{TWILIO_GATEWAY_URL}/twilio/call/{twilio_call_sid}/end",
                    headers={"X-Internal-Key": internal_key},
                    timeout=10.0
                )

                if response.status_code == 200:
                    logger.info(f"Terminated Twilio call: {twilio_call_sid}")
                else:
                    logger.warning(f"Failed to terminate Twilio call {twilio_call_sid}: {response.status_code}")

        except Exception as e:
            logger.error(f"Error terminating Twilio call {twilio_call_sid}: {e}")

    async def _publish_to_agent_queue(self, agent_id: str, notification: dict):
        """Publish notification to agent's RabbitMQ queue"""
        queue_name = f"{agent_id}_queue"

        try:
            connection = pika.BlockingConnection(self.connection_params)
            channel = connection.channel()

            # Declare queue (idempotent)
            channel.queue_declare(queue=queue_name, durable=True)

            # Publish
            channel.basic_publish(
                exchange='',
                routing_key=queue_name,
                body=json.dumps(notification),
                properties=pika.BasicProperties(
                    delivery_mode=2,
                    content_type='application/json'
                )
            )

            logger.info(f"Published call notification to {queue_name}")

            channel.close()
            connection.close()

        except (AMQPConnectionError, AMQPChannelError) as e:
            logger.error(f"RabbitMQ error publishing to {queue_name}: {e}")
        except Exception as e:
            logger.error(f"Failed to publish to {queue_name}: {e}")

    # =========================================================================
    # Transcription Storage
    # =========================================================================

    async def add_transcript(
        self,
        call_id: UUID,
        speaker_type: str,
        speaker_id: Optional[str],
        content: str,
        audio_file_path: str = None,
        audio_duration_ms: int = None,
        stt_confidence: float = None
    ):
        """
        Add a transcript entry for the call.

        Args:
            call_id: Call ID
            speaker_type: 'user' or 'agent'
            speaker_id: Agent ID if speaker is agent
            content: Transcribed text
            audio_file_path: Optional path to audio file
            audio_duration_ms: Optional audio duration
            stt_confidence: Optional STT confidence score
        """
        query = """
        INSERT INTO call_transcripts
            (call_id, speaker_type, speaker_id, content, audio_file_path,
             audio_duration_ms, stt_confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        try:
            self.db.execute_query(query, (
                str(call_id),
                speaker_type,
                speaker_id,
                content,
                audio_file_path,
                audio_duration_ms,
                stt_confidence
            ))
        except Exception as e:
            logger.error(f"Failed to add transcript: {e}")


# Global instance
_call_manager: Optional[CallManager] = None


def initialize_call_manager(db_client, rabbitmq_url: str) -> CallManager:
    """Initialize the global CallManager instance"""
    global _call_manager
    _call_manager = CallManager(db_client, rabbitmq_url)
    return _call_manager


def get_call_manager() -> Optional[CallManager]:
    """Get the global CallManager instance"""
    return _call_manager

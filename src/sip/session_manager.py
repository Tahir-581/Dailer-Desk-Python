

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .config import (
    CallDirection,
    CallState,
    SIPCallSession,
    SIPParticipantInfo,
    get_sip_config,
)
from .errors import (
    SIPError,
    SIPSessionAlreadyExistsError,
    SIPSessionNotFoundError,
)

logger = logging.getLogger("SIP.SessionManager")


SessionCallback = Callable[[SIPCallSession, CallState, CallState], None]


class SIPSessionManager:

    def __init__(self, max_sessions: int = 1000, retention_seconds: Optional[int] = None):
        self._sessions: Dict[str, SIPCallSession] = {}
        self._room_to_session: Dict[str, str] = {}
        self._phone_to_session: Dict[str, str] = {}
        self._max_sessions = max_sessions

        if retention_seconds is not None:
            self._retention_seconds = retention_seconds
        else:
            config = get_sip_config()

            import os
            env_retention = os.getenv("SIP_SESSION_RETENTION_SECONDS")
            if env_retention:
                try:
                    self._retention_seconds = int(env_retention)
                except ValueError:
                    logger.warning(
                        f"Invalid SIP_SESSION_RETENTION_SECONDS value: {env_retention}, "
                        f"using default 3600"
                    )
                    self._retention_seconds = 3600
            else:
                self._retention_seconds = 3600

        self._callbacks: List[SessionCallback] = []
        self._lock = asyncio.Lock()

    def register_callback(self, callback: SessionCallback) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def _notify_callbacks(
        self, session: SIPCallSession, old_state: CallState, new_state: CallState
    ) -> None:
        for callback in self._callbacks:
            try:
                callback(session, old_state, new_state)
            except Exception as e:
                logger.error(f"Error in session callback: {e}", exc_info=True)

    async def _cleanup_old_sessions(self) -> None:
        async with self._lock:
            cutoff = datetime.now()
            to_remove = []

            for session_id, session in self._sessions.items():
                if session.state in [CallState.DISCONNECTED, CallState.FAILED]:
                    if session.participant_info.disconnected_at:
                        age = (
                            cutoff - session.participant_info.disconnected_at
                        ).total_seconds()
                        if age > self._retention_seconds:
                            to_remove.append(session_id)

            for session_id in to_remove:
                session = self._sessions.pop(session_id, None)
                if session:
                    self._room_to_session.pop(session.room_name, None)
                    if session.participant_info.phone_number:
                        self._phone_to_session.pop(
                            session.participant_info.phone_number, None
                        )

            if to_remove:
                logger.info(
                    f"Cleaned up {len(to_remove)} old sessions "
                    f"(retention: {self._retention_seconds}s)"
                )

    def get_statistics(self) -> Dict[str, Any]:
        states = {}
        for session in self._sessions.values():
            state_name = session.state.value
            states[state_name] = states.get(state_name, 0) + 1

        active = self.list_active_sessions()

        return {
            "total_sessions": len(self._sessions),
            "active_sessions": len(active),
            "states": states,
            "max_sessions": self._max_sessions,
            "retention_seconds": self._retention_seconds,
        }

    async def create_session(
        self,
        room_name: str,
        participant_info: SIPParticipantInfo,
        direction: CallDirection,
        agent_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> SIPCallSession:
        async with self._lock:
            if room_name in self._room_to_session:
                existing_session_id = self._room_to_session[room_name]
                raise SIPSessionAlreadyExistsError(existing_session_id)

            if len(self._sessions) >= self._max_sessions:
                logger.warning(
                    f"Max sessions limit reached ({self._max_sessions}), "
                    f"cannot create new session"
                )
                await self._cleanup_old_sessions()
                if len(self._sessions) >= self._max_sessions:
                    raise SIPError(
                        f"Cannot create session: max sessions limit ({self._max_sessions}) reached"
                    )

            session_id = str(uuid.uuid4())
            session = SIPCallSession(
                session_id=session_id,
                room_name=room_name,
                participant_info=participant_info,
                direction=direction,
                state=CallState.INITIALIZING,
                agent_id=agent_id,
                workflow_id=workflow_id,
                org_id=org_id,
            )

            self._sessions[session_id] = session
            self._room_to_session[room_name] = session_id
            if participant_info.phone_number:
                self._phone_to_session[participant_info.phone_number] = session_id

            logger.info(
                f"Created SIP session: {session_id} for room {room_name} "
                f"(direction: {direction.value})"
            )

            return session

    def get_session_by_room(self, room_name: str) -> Optional[SIPCallSession]:
        session_id = self._room_to_session.get(room_name)
        if session_id:
            return self._sessions.get(session_id)
        return None

    def get_session_by_id(self, session_id: str) -> Optional[SIPCallSession]:
        return self._sessions.get(session_id)

    def get_session_by_phone(self, phone_number: str) -> Optional[SIPCallSession]:
        session_id = self._phone_to_session.get(phone_number)
        if session_id:
            return self._sessions.get(session_id)
        return None

    def list_active_sessions(self) -> List[SIPCallSession]:
        active_states = {
            CallState.INITIALIZING,
            CallState.RINGING,
            CallState.CONNECTING,
            CallState.ACTIVE,
            CallState.ON_HOLD,
            CallState.TRANSFERRING,
        }
        return [
            session
            for session in self._sessions.values()
            if session.state in active_states
        ]

    async def update_session_state(
        self, session_id: str, new_state: CallState
    ) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise SIPSessionNotFoundError(session_id)

            old_state = session.state
            session.update_state(new_state)

            if new_state == CallState.ACTIVE and not session.participant_info.connected_at:
                session.participant_info.connected_at = datetime.now()
            elif new_state in [CallState.DISCONNECTED, CallState.FAILED]:
                if not session.participant_info.disconnected_at:
                    session.participant_info.disconnected_at = datetime.now()

            logger.info(
                f"Session {session_id} state changed: {old_state.value} -> {new_state.value}"
            )

            self._notify_callbacks(session, old_state, new_state)

    async def add_transfer_record(
        self,
        session_id: str,
        transfer_to: str,
        transfer_type: str = "cold",
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise SIPSessionNotFoundError(session_id)

            transfer_record = {
                "transfer_to": transfer_to,
                "transfer_type": transfer_type,
                "success": success,
                "error": error,
                "timestamp": datetime.now().isoformat(),
            }
            session.transfer_history.append(transfer_record)
            logger.info(
                f"Added transfer record to session {session_id}: "
                f"{transfer_type} transfer to {transfer_to} ({'success' if success else 'failed'})"
            )

    async def delete_session(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                raise SIPSessionNotFoundError(session_id)

            self._sessions.pop(session_id, None)
            self._room_to_session.pop(session.room_name, None)
            if session.participant_info.phone_number:
                self._phone_to_session.pop(session.participant_info.phone_number, None)

            logger.info(f"Deleted SIP session: {session_id}")



_session_manager: Optional[SIPSessionManager] = None


def get_session_manager() -> SIPSessionManager:
    global _session_manager
    if _session_manager is None:
        _session_manager = SIPSessionManager()
    return _session_manager

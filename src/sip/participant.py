

import json
import logging
import uuid
from typing import Any, List, Optional

from livekit import api, rtc
from livekit.protocol import sip as sip_proto

from .config import (
    CallDirection,
    CallState,
    OutboundCallRequest,
    SIPParticipantInfo,
    TransferRequest,
    TransferType,
    get_sip_config,
)
from .errors import (
    SIPCallInitiationError,
    SIPCallTransferError,
    SIPError,
    SIPParticipantError,
)
from .session_manager import get_session_manager

logger = logging.getLogger("SIP.Participant")


class SIPParticipantHandler:

    def __init__(self, livekit_api: Optional[api.LiveKitAPI] = None):
        self._api = livekit_api
        self._config = get_sip_config()

    async def _get_api(self) -> api.LiveKitAPI:
        if self._api:
            return self._api


    def is_sip_participant(self, participant: rtc.RemoteParticipant) -> bool:
        return participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP

    def extract_sip_info(
        self, participant: rtc.RemoteParticipant, room_name: str = ""
    ) -> Optional[SIPParticipantInfo]:
        if not self.is_sip_participant(participant):
            return None


        phone_number = None
        call_from = None
        call_to = None
        trunk_id = None
        call_status = None
        call_id = None
        call_id_full = None
        rule_id = None
        direction = CallDirection.UNKNOWN

        if hasattr(participant, "attributes") and participant.attributes:
            attrs = participant.attributes

            phone_number = attrs.get("sip.phoneNumber")
            call_status = attrs.get("sip.callStatus")
            trunk_id = attrs.get("sip.trunkId") or attrs.get("sip.trunkID")
            call_id = attrs.get("sip.callID")
            call_id_full = attrs.get("sip.callIDFull")
            rule_id = attrs.get("sip.ruleID")

            call_from = attrs.get("sip.callFrom")
            call_to = attrs.get("sip.callTo")


            if call_from:
                direction = CallDirection.INBOUND

                if not phone_number:
                    phone_number = call_from
            elif call_to:
                direction = CallDirection.OUTBOUND

                if not phone_number:
                    phone_number = call_to
            elif phone_number:
                direction = CallDirection.UNKNOWN


        if participant.metadata:
            try:
                metadata = json.loads(participant.metadata)
                phone_number = metadata.get("phoneNumber") or phone_number
                trunk_id = metadata.get("trunkId") or trunk_id
                if metadata.get("callDirection"):
                    direction = CallDirection(metadata["callDirection"])
            except json.JSONDecodeError:
                pass


        state = CallState.ACTIVE
        if call_status:
            call_status_lower = call_status.lower()
            if call_status_lower == "active":
                state = CallState.ACTIVE
            elif call_status_lower == "ringing":
                state = CallState.RINGING
            elif call_status_lower == "dialing":
                state = CallState.CONNECTING
            elif call_status_lower == "hangup":
                state = CallState.DISCONNECTED
            elif call_status_lower == "automation":
                state = CallState.CONNECTING

        return SIPParticipantInfo(
            participant_id=participant.sid,
            identity=participant.identity,
            room_name=room_name,
            phone_number=phone_number,
            call_from=call_from,
            call_to=call_to,
            trunk_id=trunk_id,
            direction=direction,
            state=state,
            metadata={
                "call_status": call_status,
                "call_id": call_id,
                "call_id_full": call_id_full,
                "rule_id": rule_id,
            },
        )

    def find_sip_participant(self, room: rtc.Room) -> Optional[rtc.RemoteParticipant]:
        for participant in room.remote_participants.values():
            if self.is_sip_participant(participant):
                return participant
        return None

    def find_all_sip_participants(self, room: rtc.Room) -> List[rtc.RemoteParticipant]:
        return [
            p for p in room.remote_participants.values() if self.is_sip_participant(p)
        ]





    async def create_outbound_call(
        self, request: OutboundCallRequest
    ) -> SIPParticipantInfo:
        try:
            lk_api = await self._get_api()

            room_name = request.room_name or f"outbound-{uuid.uuid4().hex[:12]}"
            participant_identity = (
                request.participant_identity or f"sip-{uuid.uuid4().hex[:8]}"
            )

            phone_number = self._format_phone_number(request.phone_number)

            metadata = {
                "callType": "outbound",
                "phoneNumber": phone_number,
                "orgId": request.org_id,
                "agentId": request.agent_id,
                "workflowId": request.workflow_id,
                **request.metadata,
            }

            display_name = request.display_name or request.caller_id

            create_request = sip_proto.CreateSIPParticipantRequest(
                sip_trunk_id=request.trunk_id,
                sip_call_to=phone_number,
                room_name=room_name,
                participant_identity=participant_identity,
                participant_name=request.participant_name or "Outbound Call",
                participant_metadata=json.dumps(metadata),
                dtmf=request.dtmf or "",
                play_ringtone=request.play_ringtone,
                krisp_enabled=request.krisp_enabled,
                wait_until_answered=request.wait_until_answered,
            )

            if display_name:
                create_request.display_name = display_name

            if request.max_call_duration:
                from google.protobuf.duration_pb2 import Duration
                duration = Duration()
                duration.seconds = request.max_call_duration
                create_request.max_call_duration.CopyFrom(duration)

            if request.headers:
                for key, value in request.headers.items():
                    create_request.headers[key] = value

            if request.participant_attributes:
                for key, value in request.participant_attributes.items():
                    create_request.participant_attributes[key] = value

            logger.info(
                f"Creating outbound call: trunk={request.trunk_id}, "
                f"to={phone_number}, room={room_name}"
            )

            response = await lk_api.sip.create_sip_participant(create_request)

            participant_info = SIPParticipantInfo(
                participant_id=response.participant.sid if response.participant else "",
                identity=response.participant.identity
                if response.participant
                else participant_identity,
                room_name=room_name,
                phone_number=phone_number,
                call_to=phone_number,
                trunk_id=request.trunk_id,
                direction=CallDirection.OUTBOUND,
                state=CallState.RINGING,
            )

            session_manager = get_session_manager()
            await session_manager.create_session(
                room_name=room_name,
                participant_info=participant_info,
                direction=CallDirection.OUTBOUND,
                agent_id=request.agent_id,
                workflow_id=request.workflow_id,
                org_id=request.org_id,
            )

            logger.info(
                f"Outbound call initiated: {participant_info.participant_id} "
                f"to {phone_number}"
            )

            return participant_info

        except (SIPParticipantError, SIPError):
            raise
        except api.TwirpError as e:
            sip_status_code = None
            sip_status = None
            if hasattr(e, "metadata") and e.metadata:
                sip_status_code = e.metadata.get("sip_status_code")
                sip_status = e.metadata.get("sip_status")

            error_msg = f"Failed to create outbound call: {e}"
            if sip_status_code:
                error_msg = f"SIP error {sip_status_code}: {sip_status or 'Unknown'}"

            logger.error(error_msg)
            raise SIPCallInitiationError(
                error_msg,
                sip_status_code=int(sip_status_code) if sip_status_code else None,
                sip_status=sip_status,
            )
        except Exception as e:
            logger.error(f"Error creating outbound call: {e}", exc_info=True)
            raise SIPCallInitiationError(f"Failed to create outbound call: {e}")





    async def transfer_call(
        self,
        room: rtc.Room,
        participant: rtc.RemoteParticipant,
        request: TransferRequest,
        job_ctx: Optional[Any] = None,
    ) -> bool:
        if request.transfer_type == TransferType.WARM:
            if not self._config.enable_warm_transfer:
                logger.warning(
                    "Warm transfer requested but not enabled, falling back to cold"
                )
                request.transfer_type = TransferType.COLD
            else:
                return await self._execute_warm_transfer(
                    room, participant, request, job_ctx
                )

        return await self._execute_cold_transfer(room, participant, request, job_ctx)

    async def _execute_cold_transfer(
        self,
        room: rtc.Room,
        participant: rtc.RemoteParticipant,
        request: TransferRequest,
        job_ctx: Optional[Any] = None,
    ) -> bool:
        try:
            if job_ctx and hasattr(job_ctx, "api"):
                lk_api = job_ctx.api
            else:
                lk_api = await self._get_api()


            transfer_to = self._format_transfer_destination(request.transfer_to)




            transfer_request = sip_proto.TransferSIPParticipantRequest(
                room_name=room.name,
                participant_identity=participant.identity,
                transfer_to=transfer_to,
                play_dialtone=request.play_dialtone,
            )

            logger.info(
                f"Executing cold transfer: {participant.identity} -> {transfer_to}"
            )

            await lk_api.sip.transfer_sip_participant(transfer_request)

            session_manager = get_session_manager()
            session = session_manager.get_session_by_room(room.name)
            if session:
                await session_manager.add_transfer_record(
                    session.session_id,
                    transfer_to=transfer_to,
                    transfer_type="cold",
                    success=True,
                )
                await session_manager.update_session_state(
                    session.session_id, CallState.TRANSFERRING
                )

            logger.info(
                f"Cold transfer successful: {participant.identity} -> {transfer_to}"
            )
            return True

        except api.TwirpError as e:
            sip_status_code = None
            sip_status = None
            if hasattr(e, "metadata") and e.metadata:
                sip_status_code = e.metadata.get("sip_status_code")
                sip_status = e.metadata.get("sip_status")

            error_msg = f"Transfer failed: {e}"
            if sip_status_code:
                error_msg = f"SIP error {sip_status_code}: {sip_status or 'Unknown'}"

            logger.error(error_msg)
            raise SIPCallTransferError(
                error_msg,
                sip_status_code=int(sip_status_code) if sip_status_code else None,
                sip_status=sip_status,
            )
        except Exception as e:
            logger.error(f"Error in cold transfer: {e}", exc_info=True)
            raise SIPCallTransferError(f"Transfer failed: {e}")

    async def _execute_warm_transfer(
        self,
        room: rtc.Room,
        participant: rtc.RemoteParticipant,
        request: TransferRequest,
        job_ctx: Optional[Any] = None,
    ) -> bool:
        try:
            try:
                from livekit.agents.beta.workflows import WarmTransferTask

                trunk_id = self._get_outbound_trunk_id(job_ctx)
                if not trunk_id:
                    raise SIPCallTransferError(
                        "Warm transfer requires an outbound trunk ID. "
                        "Configure SIP_OUTBOUND_TRUNK_ID or provide it in the request."
                    )

                # Get chat context if available from job context
                chat_ctx = None
                if job_ctx and hasattr(job_ctx, 'agent') and hasattr(job_ctx.agent, 'chat_ctx'):
                    chat_ctx = job_ctx.agent.chat_ctx

                # Format transfer destination
                transfer_to = self._format_transfer_destination(request.transfer_to)
                # Remove tel: prefix for phone number
                phone_number = transfer_to.replace("tel:", "").replace("sip:", "").split("@")[0]

                logger.info(f"Executing warm transfer to {phone_number} using WarmTransferTask")

                # Execute warm transfer task
                result = await WarmTransferTask(
                    target_phone_number=phone_number,
                    sip_trunk_id=trunk_id,
                    chat_ctx=chat_ctx,
                )

                session_manager = get_session_manager()
                session = session_manager.get_session_by_room(room.name)
                if session:
                    await session_manager.add_transfer_record(
                        session.session_id,
                        transfer_to=transfer_to,
                        transfer_type="warm",
                        success=True,
                    )

                logger.info(f"Warm transfer completed successfully to {phone_number}")
                return True

            except ImportError:
                logger.warning(
                    "WarmTransferTask not available (requires livekit.agents.beta.workflows). "
                    "Falling back to cold transfer. "
                    "Upgrade livekit-agents to enable warm transfers."
                )
                return await self._execute_cold_transfer(room, participant, request, job_ctx)

        except SIPCallTransferError:
            raise
        except Exception as e:
            logger.error(f"Error in warm transfer: {e}", exc_info=True)
            raise SIPCallTransferError(f"Warm transfer failed: {e}")

    def _get_outbound_trunk_id(self, job_ctx: Optional[Any] = None) -> Optional[str]:
        import os

        trunk_id = os.environ.get("SIP_OUTBOUND_TRUNK_ID")
        if trunk_id:
            return trunk_id

        if hasattr(self._config, 'outbound_trunk_id') and self._config.outbound_trunk_id:
            return self._config.outbound_trunk_id

        if job_ctx and hasattr(job_ctx, 'room'):
            room = job_ctx.room
            for p in room.remote_participants.values():
                if p.metadata:
                    try:
                        metadata = json.loads(p.metadata)
                        if metadata.get("trunkId"):
                            return metadata.get("trunkId")
                    except json.JSONDecodeError:
                        pass

        return None





    async def send_dtmf(
        self,
        room: Optional[rtc.Room] = None,
        room_name: Optional[str] = None,
        participant_identity: Optional[str] = None,
        digits: str = "",
    ) -> bool:
        if not self._config.enable_dtmf:
            logger.warning("DTMF is disabled in configuration")
            return False

        if not room:
            logger.warning(
                "send_dtmf() called without room object. "
                "DTMF sending requires access to room.local_participant. "
                "Please use room.local_participant.publish_dtmf() directly."
            )
            return False

        try:






            local_participant = room.local_participant
            if not local_participant:
                logger.error("Local participant not available in room")
                return False

            dtmf_code_map = {
                "0": 0, "1": 1, "2": 2, "3": 3, "4": 4,
                "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
                "*": 10, "#": 11
            }


            for digit in digits:
                if digit not in dtmf_code_map:
                    logger.warning(f"Invalid DTMF digit: {digit}, skipping")
                    continue
                code = dtmf_code_map[digit]
                await local_participant.publish_dtmf(code=code, digit=digit)
                logger.info(f"Sent DTMF: {digit} (code: {code})")

            return True

        except Exception as e:
            logger.error(f"Error sending DTMF: {e}", exc_info=True)
            return False





    def _format_phone_number(self, number: str) -> str:
        from .config import normalize_phone_number, validate_e164_phone_number

        number = number.strip()

        if number.startswith("tel:") or number.startswith("sip:"):
            if number.startswith("tel:"):
                phone_part = number[4:]
                is_valid, error = validate_e164_phone_number(phone_part)
                if not is_valid:
                    raise SIPParticipantError(f"Invalid phone number in tel: URI: {error}")
            return number

        try:
            return normalize_phone_number(number)
        except ValueError as e:
            error_msg = str(e)
            if "Invalid phone number format" in error_msg:
                raise SIPParticipantError(error_msg)
            raise SIPParticipantError(f"Invalid phone number format: {error_msg}")

    def _format_transfer_destination(self, destination: str) -> str:
        if destination.startswith("sip:") or destination.startswith("tel:"):
            return destination
        try:
            normalized = self._format_phone_number(destination)
            return f"sip:{normalized}"
        except SIPParticipantError:
            return destination


_participant_handler: Optional[SIPParticipantHandler] = None


def get_participant_handler() -> SIPParticipantHandler:
    global _participant_handler
    if _participant_handler is None:
        _participant_handler = SIPParticipantHandler()
    return _participant_handler

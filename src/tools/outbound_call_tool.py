import json
import logging
import os
import uuid
from typing import Any, Dict, Optional

from livekit import api, rtc
from livekit.agents import ToolError, get_job_context
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext

from .tool_config_loader import get_tool_config

logger = logging.getLogger("Tool.OutboundCall")


def _format_to_e164(phone_number: str) -> str:
    phone_number = phone_number.strip()

    if phone_number.startswith("tel:"):
        return phone_number

    cleaned = phone_number.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")")

    if not cleaned.isdigit():
        raise ToolError(f"Invalid phone number format: {phone_number}")

    return f"tel:+{cleaned}"


def _get_org_and_assistant_ids(room: rtc.Room) -> tuple[Optional[str], Optional[str]]:
    org_id = None
    assistant_id = None

    try:
        for participant in room.remote_participants.values():
            if participant.metadata:
                try:
                    metadata = json.loads(participant.metadata)
                    org_id = metadata.get("orgId") or org_id
                    assistant_id = metadata.get("assistantId") or assistant_id
                    if org_id and assistant_id:
                        break
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        logger.warning(f"Could not fetch participant metadata: {e}")

    return org_id, assistant_id


@function_tool()
async def make_outbound_call(  context: RunContext, phone_number: str, trunk_id: Optional[str] = None, assistant_id: Optional[str] = None, workflow_id: Optional[str] = None, metadata: Optional[str] = None ) -> str:
    try:
        job_ctx = get_job_context()
        room = job_ctx.room

        org_id, default_assistant_id = _get_org_and_assistant_ids(room)
        final_assistant_id = assistant_id or default_assistant_id

        tool_config = None
        if org_id and final_assistant_id:
            tool_config = await get_tool_config(
                tool_name="outbound_call", org_id=org_id, assistant_id=final_assistant_id
            )

        if not trunk_id and tool_config:
            trunk_id = tool_config.get("trunkId")
        if not trunk_id:
            trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID", "").strip() or None

        if not trunk_id:
            raise ToolError(
                "SIP trunk ID is required. Set trunk_id, configure outbound_call tool with trunkId, or set SIP_OUTBOUND_TRUNK_ID in .env.local."
            )

        if not org_id:
            logger.info(
                "orgId not in participant metadata (e.g. call from LiveKit free number); using SIP_OUTBOUND_TRUNK_ID only."
            )

        try:
            formatted_number = _format_to_e164(phone_number)
        except ToolError:
            raise
        except Exception as e:
            raise ToolError(f"Invalid phone number format: {e!s}")

        call_metadata: Dict[str, Any] = {}
        if metadata:
            try:
                call_metadata = json.loads(metadata)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON in metadata parameter, ignoring: {metadata}")

        room_name = f"outbound-call-{uuid.uuid4().hex[:12]}"

        participant_identity = f"sip-outbound-{uuid.uuid4().hex[:8]}"

        logger.info( f"Making outbound call: trunk_id={trunk_id}, phone_number={formatted_number}, room={room_name}" )

        try:
            create_request = api.CreateSIPParticipantRequest(
                sip_trunk_id=trunk_id,
                sip_call_to=formatted_number,
                room_name=room_name,
                participant_identity=participant_identity,
                participant_name="Outbound Call",
                dtmf="",
                play_ringtone=True
            )

            response = await job_ctx.api.sip.create_sip_participant(create_request)

            logger.info(f"Outbound call initiated successfully: {response.participant.identity}")

            if org_id and (final_assistant_id or workflow_id):
                room_metadata = {
                    "assistantId": final_assistant_id,
                    "workflowId": workflow_id,
                    "orgId": org_id,
                    "callType": "outbound",
                    "phoneNumber": formatted_number,
                    **call_metadata
                }
                logger.info(f"Call metadata prepared: {room_metadata}")

            return (
                f"Outbound call initiated to {formatted_number}. Call is connecting in room {room_name}."
            )

        except api.TwirpError as e:
            sip_status_code = None
            sip_status = None

            if hasattr(e, "metadata") and e.metadata:
                sip_status_code = e.metadata.get("sip_status_code")
                sip_status = e.metadata.get("sip_status")

            if sip_status_code:
                status_detail = f" - {sip_status}" if sip_status else ""
                error_msg = f"Outbound call failed (SIP status: {sip_status_code}{status_detail})"
            else:
                error_msg = f"Outbound call failed: {getattr(e, 'message', str(e))}"

            logger.error(f"Outbound call error: {error_msg}")
            raise ToolError(error_msg)

        except Exception as e:
            logger.error(f"Unexpected error making outbound call: {e}", exc_info=True)
            raise ToolError(f"Unable to make outbound call: {e!s}")

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in make_outbound_call: {e}", exc_info=True)
        raise ToolError(f"Unable to make outbound call: {e!s}")


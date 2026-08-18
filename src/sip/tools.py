

import json
import logging
from typing import Any, Dict, Optional

from livekit import rtc
from livekit.agents import ToolError, get_job_context
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext

from .audio_bridge import get_audio_manager
from .config import (
    CallState,
    OutboundCallRequest,
    TransferRequest,
    TransferType,
    normalize_phone_number,
    validate_e164_phone_number,
)
from .errors import (
    SIPCallTransferError,
    SIPError,
)
from .participant import get_participant_handler
from .session_manager import get_session_manager

logger = logging.getLogger("SIP.Tools")


def _find_sip_participant(room: rtc.Room) -> Optional[rtc.RemoteParticipant]:
    for participant in room.remote_participants.values():
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            return participant
    return None


def _get_metadata_from_room(room: rtc.Room) -> Dict[str, Any]:
    org_id = None
    assistant_id = None
    trunk_id = None

    for participant in room.remote_participants.values():
        if participant.metadata:
            try:
                metadata = json.loads(participant.metadata)
                org_id = metadata.get("orgId") or org_id
                assistant_id = metadata.get("assistantId") or assistant_id
                trunk_id = metadata.get("trunkId") or trunk_id
            except json.JSONDecodeError:
                pass

    return {
        "org_id": org_id,
        "assistant_id": assistant_id,
        "trunk_id": trunk_id,
    }


@function_tool()
async def sip_transfer(
    context: RunContext,
    destination: str,
    transfer_type: str = "cold",
    play_dialtone: bool = True,
) -> str:
    try:
        job_ctx = get_job_context()
        room = job_ctx.room


        sip_participant = _find_sip_participant(room)
        if not sip_participant:
            raise ToolError(
                "I can only transfer phone calls. This doesn't appear to be a phone call."
            )

        destination = destination.strip()
        if not destination:
            raise ToolError("Please provide a phone number or SIP address to transfer to.")

        if not destination.startswith("sip:"):
            is_valid, error = validate_e164_phone_number(destination)
            if not is_valid:
                try:
                    destination = normalize_phone_number(destination)
                except ValueError:
                    raise ToolError(
                        f"I couldn't understand that phone number. {error}. "
                        f"Please provide a number like +14155551234."
                    )

        try:
            t_type = TransferType(transfer_type.lower())
        except ValueError:
            t_type = TransferType.COLD
            logger.warning(
                f"Unknown transfer type '{transfer_type}', using cold transfer"
            )

        request = TransferRequest(
            transfer_to=destination,
            transfer_type=t_type,
            play_dialtone=play_dialtone,
        )

        await context.session.generate_reply(
            instructions="Inform the user that you are transferring them to another party. Be brief and professional."
        )

        handler = get_participant_handler()
        await handler.transfer_call(
            room=room,
            participant=sip_participant,
            request=request,
            job_ctx=job_ctx,
        )

        display_dest = destination.replace("tel:", "").replace("sip:", "")
        return f"Call transferred successfully to {display_dest}"

    except ToolError:
        raise
    except SIPCallTransferError as e:
        logger.error(f"SIP transfer error: {e}")

        if e.sip_status_code:
            raise ToolError(
                f"I couldn't complete the transfer. "
                f"The phone system returned an error (code {e.sip_status_code}). "
                f"Please try again or use a different number."
            )
        raise ToolError(f"Transfer failed: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error in sip_transfer: {e}", exc_info=True)
        raise ToolError("I encountered an error while transferring the call. Please try again.")


@function_tool()
async def sip_hold(
    context: RunContext,
    on_hold: bool = True,
) -> str:
    try:
        job_ctx = get_job_context()
        room = job_ctx.room

        session_manager = get_session_manager()
        session = session_manager.get_session_by_room(room.name)

        if not session:
            raise ToolError(
                "I couldn't find an active phone call session. "
                "This tool only works during phone calls."
            )

        audio_manager = get_audio_manager()
        bridge = audio_manager.get_bridge(session.session_id)

        success = await bridge.set_hold(
            session_id=session.session_id,
            agent_session=context.session,
            on_hold=on_hold,
        )

        if success:
            if on_hold:
                await session_manager.update_session_state(
                    session.session_id, CallState.ON_HOLD
                )
                return "Call placed on hold."
            else:
                await session_manager.update_session_state(
                    session.session_id, CallState.ACTIVE
                )
                return "Call resumed from hold."
        else:
            raise ToolError("Failed to change hold state.")

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error in sip_hold: {e}", exc_info=True)
        raise ToolError(f"Unable to change hold state: {e!s}")


@function_tool()
async def sip_dial(
    context: RunContext,
    phone_number: str,
    trunk_id: Optional[str] = None,
) -> str:
    try:
        job_ctx = get_job_context()
        room = job_ctx.room

        phone_number = phone_number.strip()
        if not phone_number:
            raise ToolError("Please provide a phone number to call.")

        is_valid, error = validate_e164_phone_number(phone_number)
        if not is_valid:
            try:
                phone_number = normalize_phone_number(phone_number)
            except ValueError:
                raise ToolError(
                    f"I couldn't understand that phone number. {error}. "
                        f"Please provide a number like +14155551234."
                    )

        metadata = _get_metadata_from_room(room)

        final_trunk_id = trunk_id or metadata.get("trunk_id")
        if not final_trunk_id:
            raise ToolError(
                "I need a SIP trunk configuration to make calls. "
                "Please configure a default outbound trunk or provide a trunk_id."
            )

        request = OutboundCallRequest(
            phone_number=phone_number,
            trunk_id=final_trunk_id,
            org_id=metadata.get("org_id"),
            agent_id=metadata.get("assistant_id"),
        )

        handler = get_participant_handler()
        participant_info = await handler.create_outbound_call(request)

        display_number = phone_number.replace("tel:", "").replace("+", "")
        return (
            f"Calling {display_number}. The call is connecting now."
        )

    except ToolError:
        raise
    except SIPError as e:
        logger.error(f"SIP dial error: {e}")

        if hasattr(e, "sip_status_code") and e.sip_status_code:
            raise ToolError(
                f"I couldn't place the call. "
                f"The phone system returned an error (code {e.sip_status_code}). "
                f"Please check the number and try again."
            )
        raise ToolError(f"Unable to place call: {e.message}")
    except Exception as e:
        logger.error(f"Error in sip_dial: {e}", exc_info=True)
        raise ToolError("I encountered an error while placing the call. Please try again.")


@function_tool()
async def sip_send_dtmf(
    context: RunContext,
    digits: str,
) -> str:
    try:
        job_ctx = get_job_context()
        room = job_ctx.room


        sip_participant = _find_sip_participant(room)
        if not sip_participant:
            raise ToolError(
                "No SIP participant found. This tool only works with phone calls."
            )

        if not digits:
            raise ToolError("Please provide DTMF digits to send (0-9, *, #).")

        if len(digits) > 64:
            raise ToolError(
                f"Too many digits ({len(digits)}). Maximum is 64 digits per request."
            )

        valid_chars = set("0123456789*#")
        invalid_chars = [c for c in digits if c not in valid_chars]
        if invalid_chars:
            raise ToolError(
                f"Invalid DTMF characters: {', '.join(set(invalid_chars))}. "
                f"Valid characters are 0-9, *, and #."
            )




        local_participant = room.local_participant
        if not local_participant:
            raise ToolError("Local participant not available in room.")

        dtmf_code_map = {
            "0": 0, "1": 1, "2": 2, "3": 3, "4": 4,
            "5": 5, "6": 6, "7": 7, "8": 8, "9": 9,
            "*": 10, "#": 11
        }


        for digit in digits:
            code = dtmf_code_map[digit]
            await local_participant.publish_dtmf(code=code, digit=digit)
            logger.info(f"Sent DTMF: {digit} (code: {code})")

        return f"Successfully sent DTMF tones: {digits}"

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error in sip_send_dtmf: {e}", exc_info=True)
        raise ToolError(f"Unable to send DTMF: {e!s}")


@function_tool()
async def sip_get_call_info(context: RunContext) -> Dict[str, Any]:
    try:
        job_ctx = get_job_context()
        room = job_ctx.room


        sip_participant = _find_sip_participant(room)
        if not sip_participant:
            return {
                "is_phone_call": False,
                "message": "This is not a phone call (no SIP participant found).",
            }

        handler = get_participant_handler()
        sip_info = handler.extract_sip_info(sip_participant, room.name)

        if not sip_info:
            return {
                "is_phone_call": True,
                "message": "SIP participant found but unable to extract details.",
            }

        session_manager = get_session_manager()
        session = session_manager.get_session_by_room(room.name)

        result = {
            "is_phone_call": True,
            "phone_number": sip_info.phone_number,
            "call_from": sip_info.call_from,
            "call_to": sip_info.call_to,
            "direction": sip_info.direction.value,
            "state": sip_info.state.value,
            "trunk_id": sip_info.trunk_id,
            "participant_identity": sip_info.identity,
            "room_name": sip_info.room_name,
        }

        if session:
            result["session_id"] = session.session_id
            result["org_id"] = session.org_id
            result["agent_id"] = session.agent_id
            if sip_info.duration_seconds is not None:
                result["duration_seconds"] = round(sip_info.duration_seconds, 1)

        return result

    except Exception as e:
        logger.error(f"Error in sip_get_call_info: {e}", exc_info=True)
        return {
            "is_phone_call": False,
            "error": str(e),
        }



SIP_TOOLS = [
    sip_transfer,
    sip_hold,
    sip_dial,
    sip_send_dtmf,
    sip_get_call_info,
]

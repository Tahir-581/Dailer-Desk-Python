import json
import logging
from typing import Any, Dict, Optional, Tuple

from livekit import api, rtc
from livekit.agents import ToolError, get_job_context
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext

from .custom_function_dispatcher import get_dispatcher
from .tool_config_loader import get_transfer_call_config

logger = logging.getLogger("Tool Call")


def _extract_metadata_from_participant(participant) -> Optional[Dict[str, str]]:

    if participant.metadata:
        try:
            metadata = json.loads(participant.metadata)
            logger.info(f"Participant metadata: {metadata}")
            return { "orgId": metadata.get("orgId"), "assistantId": metadata.get("assistantId") }

        except json.JSONDecodeError:
            logger.warning(f"Failed to parse participant metadata: {participant.metadata}")
    return None


def _get_org_and_assistant_ids(room: rtc.Room) -> Tuple[Optional[str], Optional[str]]:

    org_id = None
    assistant_id = None

    try:
        for participant in room.remote_participants.values():
            extracted = _extract_metadata_from_participant(participant)
            if extracted:
                if extracted.get("orgId"):
                    org_id = extracted.get("orgId")
                    logger.info(f"Org ID from participant metadata: {org_id}")
                if extracted.get("assistantId"):
                    assistant_id = extracted.get("assistantId")
                    logger.info(f"Assistant ID from participant metadata: {assistant_id}")
                if org_id and assistant_id:
                    break
    except Exception as e:
        logger.warning(f"Could not fetch participant metadata: {e}")

    return org_id, assistant_id


def _format_to_e164(destination: str) -> str:

    destination = destination.strip()

    if destination.startswith("tel:"):
        return destination

    cleaned = destination.replace("+", "").replace("-", "").replace(" ", "").replace("(", "").replace(")")

    if not cleaned.isdigit():
        raise ToolError(f"Invalid phone number format: {destination}")

    return f"tel:+{cleaned}"


async def _execute_cold_transfer( job_ctx, room: rtc.Room, participant_identity: str, transfer_to: str, play_dialtone: bool ) -> str:
    try:
        transfer_request = api.TransferSIPParticipantRequest(
            room_name=room.name,
            participant_identity=participant_identity,
            transfer_to=transfer_to,
            play_dialtone=play_dialtone
        )

        await job_ctx.api.sip.transfer_sip_participant(transfer_request)

        logger.info(f"Cold transfer successful: {participant_identity} -> {transfer_to}")
        return f"Call transferred successfully to {transfer_to}"

    except api.TwirpError as e:
        sip_status_code = None
        sip_status = None

        if hasattr(e, "metadata") and e.metadata:
            sip_status_code = e.metadata.get("sip_status_code")
            sip_status = e.metadata.get("sip_status")

        if sip_status_code:
            status_detail = f" - {sip_status}" if sip_status else ""
            error_msg = f"Transfer failed (SIP status: {sip_status_code}{status_detail})"
        else:
            error_msg = f"Transfer failed: {getattr(e, 'message', str(e))}"

        logger.error(f"Cold transfer error: {error_msg}")
        raise ToolError(error_msg)

    except Exception as e:
        logger.error(f"Unexpected error in cold transfer: {e}", exc_info=True)
        raise ToolError(f"Transfer failed: {e!s}")



@function_tool()
async def end_call(context: RunContext) -> None:

    logger.info("Ending call session")
    context.session.shutdown(drain=True)


@function_tool()
async def transfer_call( context: RunContext, play_dialtone: bool = True, destination: Optional[str] = None ) -> str:

    try:
        job_ctx = get_job_context()
        room = job_ctx.room

        org_id, assistant_id = _get_org_and_assistant_ids(room)

        if not org_id:
            raise ToolError("Organization ID (orgId) is required but not found in participant metadata. Cannot load transfer call configuration.")

        if not assistant_id:
            raise ToolError("Assistant ID (assistantId) is required but not found in participant metadata. Cannot load transfer call configuration.")


        tool_config = await get_transfer_call_config( org_id=org_id, assistant_id=assistant_id )
        if not tool_config:
            if not destination:
                 raise ToolError(f"Transfer call tool configuration not found in database for org_id={org_id}, assistant_id={assistant_id}. Please configure the transfer_call tool in the database before using this feature.")
            tool_config = {}


        final_destination = ""

        if destination:
            final_destination = destination.strip()
            logger.info(f"Using provided destination: {final_destination}")
        else:
            routing_type = tool_config.get("routingType")
            if not routing_type:
                raise ToolError("routingType is required in tool configuration but not found in database")

            if routing_type != "static":
                raise ToolError(f"Only static routing is currently supported. Found routingType: {routing_type}")

            static_dest = tool_config.get("staticDestination")
            if not static_dest:
                raise ToolError("staticDestination is required for static routing but not found in tool configuration")

            final_destination = static_dest.strip()

        if not final_destination:
            raise ToolError("No destination found (neither provided explicitly nor in static configuration)")


        format_to_e164 = tool_config.get("formatToE164", True)
        if format_to_e164:
            transfer_to = _format_to_e164(final_destination)
        else:
            transfer_to = final_destination.strip()
            if not transfer_to.startswith("tel:"):
                raise ToolError("Destination must be in format 'tel:+123456789' when formatToE164 is disabled. Got: {transfer_to}")


        sip_participant = None
        for participant in room.remote_participants.values():
            if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
                sip_participant = participant
                break

        if not sip_participant:
            raise ToolError( "No SIP participant found to transfer. This tool only works with phone calls." )


        participant_identity = sip_participant.identity

        transfer_type = tool_config.get("transferType", "cold")



        logger.info( f"Transfer config: type={transfer_type}, formatE164={format_to_e164}" )

        await context.session.generate_reply( instructions="Inform the user that you are transferring them to another number or agent. Be brief and professional." )

        result = await _execute_cold_transfer(
            job_ctx=job_ctx,
            room=room,
            participant_identity=participant_identity,
            transfer_to=transfer_to,
            play_dialtone=play_dialtone
        )

        logger.info( f"Call transferred successfully from {participant_identity} to {transfer_to}" )
        return result

    except ToolError:
        raise
    except Exception as e:
        logger.error( f"Unexpected error transferring call: {e}" )
        raise ToolError(f"Unable to transfer call: {e!s}")


@function_tool()
async def generic_service( context: RunContext, operation: str, args: str = "{}" ) -> Dict[str, Any]:

    if not operation:
        raise ToolError("operation parameter is required")

    try:
        args_dict = json.loads(args) if args else {}
        if not isinstance(args_dict, dict):
            raise ToolError("args must be a valid JSON object")
    except json.JSONDecodeError as e:
        raise ToolError(f"Invalid JSON in args parameter: {e}")

    try:
        job_ctx = get_job_context()
        room = job_ctx.room

        org_id, assistant_id = _get_org_and_assistant_ids(room)

        if not org_id:
            raise ToolError("Organization ID (orgId) is required but not found in participant metadata. Cannot execute custom function.")

        if not assistant_id:
            raise ToolError("Assistant ID (assistantId) is required but not found in participant metadata. Cannot execute custom function.")

        logger.info( f"Executing generic_service: operation='{operation}', org_id={org_id}, assistant_id={assistant_id}, args={args_dict}" )

        dispatcher = get_dispatcher()
        result = await dispatcher.dispatch( operation=operation, args=args_dict, org_id=org_id, assistant_id=assistant_id, context=context )

        if not result.get("success", False):
            error_msg = result.get("error", "Unknown error")
            logger.error(f"Custom function '{operation}' failed: {error_msg}")
            raise ToolError(f"Operation '{operation}' failed: {error_msg}")

        logger.info(f"Custom function '{operation}' executed successfully")
        return result

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in generic_service: {e}", exc_info=True)
        raise ToolError(f"Unable to execute operation '{operation}': {e!s}")

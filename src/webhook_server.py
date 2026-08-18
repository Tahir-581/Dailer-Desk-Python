import asyncio
import logging
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

try:
    import nest_asyncio
    nest_asyncio.apply()
    logger = logging.getLogger("WebhookServer")
    logger.info("nest_asyncio enabled for Flask + async compatibility")
except ImportError:
    logger = logging.getLogger("WebhookServer")
    logger.warning(
        "nest_asyncio not installed. Install it with: pip install nest-asyncio"
    )

import threading
import time

from dotenv import load_dotenv
from flask import Flask, Response, request

from src.sip.dispatcher import get_dispatcher

env_file = project_root / ".env.local"
load_dotenv(env_file, override=True)

if 'logger' not in locals():
    logger = logging.getLogger("WebhookServer")
logger.info(f"Loading env vars from: {env_file} (exists: {env_file.exists()})")

app = Flask(__name__)

@app.before_request
def log_request_info():
    request.start_time = time.time()
    if request.path not in ['/health']:
        logger.debug(f"Incoming {request.method} request to {request.path}")

@app.after_request
def log_response_info(response):
    if hasattr(request, 'start_time') and request.path not in ['/health']:
        duration = time.time() - request.start_time
        logger.debug(f"Response for {request.method} {request.path}: {response.status_code} ({duration:.3f}s)")
    return response

LIVEKIT_SIP_DOMAIN = os.getenv("LIVEKIT_SIP_DOMAIN", "")
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "5000"))
SIP_INBOUND_USERNAME = os.getenv("SIP_INBOUND_TRUNK_USERNAME", "")
SIP_INBOUND_PASSWORD = os.getenv("SIP_INBOUND_TRUNK_PASSWORD", "")

# LiveKit API credentials for manual agent dispatch
LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")


def _normalize_phone_for_sip_uri(phone: str) -> str:
    s = (phone or "").strip()
    for prefix in ("sip:", "tel:"):
        if s.lower().startswith(prefix):
            s = s[len(prefix) :].strip()
    if "@" in s:
        s = s.split("@")[0].strip()
    if s and not s.startswith("+"):
        s = "+" + s
    return s or "+"


def generate_twiml_response(
    room_name: str,
    sip_domain: str,
    to_number: str = "",
    sip_username: str = "",
    sip_password: str = "",
) -> str:
    logger.debug("generate_twiml_response called with:")
    logger.debug(f"  room_name: {room_name}")
    logger.debug(f"  sip_domain: {sip_domain}")
    logger.debug(f"  to_number: {to_number}")
    logger.debug(f"  sip_username: {'SET' if sip_username else 'NOT SET'}")
    logger.debug(f"  sip_password: {'SET' if sip_password else 'NOT SET'}")

    if sip_username and sip_password and sip_domain and to_number:
        phone_in_uri = _normalize_phone_for_sip_uri(to_number)
        sip_uri = f"sip:{phone_in_uri}@{sip_domain}"
        logger.debug(f"Generating LiveKit routing TwiML with SIP URI: {sip_uri}")
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Dial>
    <Sip username="{sip_username}" password="{sip_password}">{sip_uri}</Sip>
  </Dial>
</Response>"""
        logger.debug("Generated LiveKit routing TwiML")
        return twiml

    logger.warning("Generating fallback test TwiML (LiveKit routing not configured)")
    return """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Hello, this is a test of your Twilio and webhook configuration. LiveKit SIP routing is temporarily disabled for this test.</Say>
    <Pause length="30"/>
</Response>"""


@app.route("/twilio/sip-webhook", methods=["GET", "POST"])
def handle_sip_invite():
    logger.info("=" * 80)
    logger.info("INCOMING WEBHOOK REQUEST")
    logger.info(f"Method: {request.method}")
    logger.info(f"URL: {request.url}")
    logger.info(f"Headers: {dict(request.headers)}")
    logger.info(f"Remote Address: {request.remote_addr}")

    form_data = dict(request.form) if request.form else {}
    logger.info(f"Form Data Keys: {list(form_data.keys())}")
    for key, value in form_data.items():
        if "password" in key.lower() or "secret" in key.lower():
            logger.info(f"  {key}: {'*' * len(str(value))}")
        else:
            logger.info(f"  {key}: {value}")

    try:
        from_number = request.form.get("From", "")
        to_number = request.form.get("To", "")
        call_sid = request.form.get("CallSid", "")

        logger.info("-" * 80)
        logger.info(f"EXTRACTED CALL INFO - From: {from_number}, To: {to_number}, CallSid: {call_sid}")

        logger.info("-" * 80)
        logger.info("VALIDATING CONFIGURATION")
        logger.info(f"LIVEKIT_SIP_DOMAIN: {'SET' if LIVEKIT_SIP_DOMAIN else 'NOT SET'}")
        if LIVEKIT_SIP_DOMAIN:
            logger.info(f"  Value: {LIVEKIT_SIP_DOMAIN}")

        if not LIVEKIT_SIP_DOMAIN:
            logger.error("LIVEKIT_SIP_DOMAIN environment variable is not set - ABORTING")
            error_response = generate_error_twiml(
                "Configuration error: LiveKit SIP domain not configured"
            )
            logger.error(f"Returning error TwiML: {error_response}")
            # Return 200 OK with error TwiML - Twilio will play the error message
            return Response(error_response, mimetype="text/xml", status=200)

        logger.info("-" * 80)
        logger.info("GETTING DISPATCHER")
        dispatcher = get_dispatcher()
        logger.info("Dispatcher retrieved successfully")

        logger.info("-" * 80)
        logger.info("EXTRACTING PHONE NUMBER FOR DISPATCH RULE LOOKUP")
        # Use To (the number that was called = your Twilio number), not From (caller).
        # LiveKit dispatch rules have inbound_numbers = the numbers that receive calls (your Twilio numbers).
        logger.info(f"Original 'To' field (number that was called): {to_number}")
        phone_number = to_number
        logger.info(f"Step 1 - Initial: {phone_number}")

        if "@" in (phone_number or ""):
            phone_number = phone_number.split("@")[0]
            logger.info(f"Step 2 - After splitting @: {phone_number}")

        if phone_number.startswith("sip:"):
            phone_number = phone_number[4:]
            logger.info(f"Step 3 - After removing 'sip:': {phone_number}")

        if phone_number.startswith("tel:"):
            phone_number = phone_number[4:]
            logger.info(f"Step 4 - After removing 'tel:': {phone_number}")

        logger.info(f"Final extracted phone number for lookup (called number): {phone_number}")

        logger.info("-" * 80)
        logger.info("LOOKING UP DISPATCH RULE")
        logger.info(f"Searching for rule matching called number (To): {phone_number}")

        try:
            logger.info("Setting up event loop for async operations...")
            try:
                loop = asyncio.get_event_loop()
                logger.info(f"Retrieved existing event loop: {loop}")
                if loop.is_closed():
                    logger.warning("Event loop is closed, creating new one")
                    raise RuntimeError("Loop is closed")
                logger.info("Using existing event loop")
            except RuntimeError as e:
                logger.info(f"No event loop available ({e}), creating new one...")
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                logger.info(f"Created new event loop: {loop}")

            logger.info("Calling dispatcher.find_rule_for_number()...")
            rule = loop.run_until_complete(
                dispatcher.find_rule_for_number(phone_number)
            )
            logger.info(f"Dispatch rule lookup completed. Rule found: {rule is not None}")

            if not rule:
                logger.warning(f"No dispatch rule found for number: {phone_number}")
                room_name = f"call-{call_sid[:12]}" if call_sid else "default-room"
                logger.info(f"Using default room name: {room_name}")
            else:
                logger.info("Found dispatch rule:")
                logger.info(f"  Rule ID: {rule.rule_id}")
                logger.info(f"  Rule Name: {rule.name}")
                logger.info(f"  Rule Type: {rule.rule_type}")
                logger.info(f"  Room Prefix: {rule.room_prefix}")
                logger.info(f"  Room Name: {rule.room_name}")
                logger.info(f"  Inbound Numbers: {rule.inbound_numbers}")
                logger.info(f"  Trunk IDs: {rule.trunk_ids}")
                logger.info(f"  Agent Name: {rule.agent_name or 'NOT SET'}")
                if rule.room_config:
                    logger.info(f"  Room Config: {rule.room_config}")
                else:
                    logger.warning("  Room Config: NOT SET - Agent will NOT be dispatched!")

                # Critical warning if no agent configured
                if not rule.agent_name and not rule.room_config:
                    logger.error("=" * 80)
                    logger.error("⚠️  CRITICAL: This dispatch rule does NOT have an agent configured!")
                    logger.error("⚠️  LiveKit will create the room, but NO AGENT will join the call.")
                    logger.error("⚠️  The call will ring but never be answered.")
                    logger.error("=" * 80)

                logger.info("Generating room name from rule...")
                room_name = dispatcher.generate_room_name(rule, call_sid)
                logger.info(
                    f"Using dispatch rule '{rule.name}' (ID: {rule.rule_id}) "
                    f"for room: {room_name}"
                )
                if not rule.agent_name and not rule.room_config:
                    logger.error(
                        "=" * 80
                    )
                    logger.error(
                        "WARNING: Dispatch rule does not have an agent configured!"
                    )
                    logger.error(
                        "LiveKit will create the room but NO AGENT will be dispatched."
                    )
                    logger.error(
                        "To fix this, update the dispatch rule to include an agent_name."
                    )
                    logger.error(
                        "=" * 80
                    )
        except Exception as e:
            logger.error(f"Error finding dispatch rule: {e}", exc_info=True)
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Exception message: {e!s}")
            room_name = f"call-{call_sid[:12]}" if call_sid else "default-room"
            logger.info(f"Using fallback room name: {room_name}")

        logger.info("-" * 80)
        logger.info("GENERATING TWIML RESPONSE")
        to_number = request.form.get("To", "")
        logger.info(f"Target room name: {room_name}")
        logger.info(f"To number (for SIP URI): {to_number}")
        logger.info(f"SIP Domain: {LIVEKIT_SIP_DOMAIN}")
        logger.info(f"SIP Username: {'SET' if SIP_INBOUND_USERNAME else 'NOT SET'}")
        logger.info(f"SIP Password: {'SET' if SIP_INBOUND_PASSWORD else 'NOT SET'}")

        has_credentials = bool(SIP_INBOUND_USERNAME and SIP_INBOUND_PASSWORD and LIVEKIT_SIP_DOMAIN and to_number)
        logger.info(f"All credentials present for LiveKit routing: {has_credentials}")

        if not has_credentials:
            missing = []
            if not SIP_INBOUND_USERNAME:
                missing.append("SIP_INBOUND_TRUNK_USERNAME")
            if not SIP_INBOUND_PASSWORD:
                missing.append("SIP_INBOUND_TRUNK_PASSWORD")
            if not LIVEKIT_SIP_DOMAIN:
                missing.append("LIVEKIT_SIP_DOMAIN")
            if not to_number:
                missing.append("To number")
            logger.warning(f"Missing credentials: {missing}")

        twiml = generate_twiml_response(
            room_name,
            LIVEKIT_SIP_DOMAIN,
            to_number=to_number,
            sip_username=SIP_INBOUND_USERNAME,
            sip_password=SIP_INBOUND_PASSWORD,
        )

        logger.info("-" * 80)
        logger.info("TWIML RESPONSE GENERATED")
        if SIP_INBOUND_USERNAME and SIP_INBOUND_PASSWORD:
            logger.info(f"Routing call to LiveKit room: {room_name} (Dial/Sip)")
            normalized_phone = _normalize_phone_for_sip_uri(to_number)
            sip_uri = f"sip:{normalized_phone}@{LIVEKIT_SIP_DOMAIN}"
            logger.info(f"SIP URI: {sip_uri}")
            logger.info(f"SIP Username: {SIP_INBOUND_USERNAME}")
        else:
            logger.warning(
                "SIP_INBOUND_TRUNK_USERNAME/PASSWORD not set; returning test TwiML. "
                "Set them to route calls to LiveKit."
            )

        logger.info(f"TwiML Content (first 500 chars): {twiml[:500]}")
        logger.info("-" * 80)
        logger.info("RETURNING RESPONSE TO TWILIO")
        logger.info("Response Status: 200")
        logger.info("Content-Type: text/xml")
        logger.info("=" * 80)

        # If dispatch rule has agent config, try to manually dispatch agent as fallback
        # (in case LiveKit SIP service doesn't apply room_config automatically)
        if rule and rule.agent_name:
            logger.info(f"Dispatch rule has agent '{rule.agent_name}' - scheduling manual dispatch check")
            # Schedule a background task to check and dispatch agent if needed
            def dispatch_agent_background():
                import asyncio
                import time

                from livekit import api

                # Wait a bit for room to be created
                time.sleep(2)

                try:
                    if not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET]):
                        logger.warning("Cannot dispatch agent - missing LiveKit API credentials")
                        return

                    # Create event loop for async operations
                    try:
                        loop = asyncio.get_event_loop()
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)

                    async def check_and_dispatch():
                        lk_api = api.LiveKitAPI(
                            url=LIVEKIT_URL,
                            api_key=LIVEKIT_API_KEY,
                            api_secret=LIVEKIT_API_SECRET
                        )

                        # Check if room exists
                        try:
                            rooms = await lk_api.room.list_rooms(names=[room_name])
                            if not rooms:
                                logger.warning(f"Room {room_name} not found yet - agent dispatch skipped")
                                return

                            room = rooms[0]
                            logger.info(f"Found room {room_name}, checking for agent participants...")

                            # Check if agent is already in room
                            participants = await lk_api.room.list_participants(
                                api.ListParticipantsRequest(room=room_name)
                            )

                            has_agent = False
                            for participant in participants:
                                if participant.agent:
                                    has_agent = True
                                    logger.info(f"Agent already in room: {participant.identity}")
                                    break

                            if not has_agent:
                                logger.info("No agent found in room - attempting manual dispatch...")
                                # Try to dispatch agent
                                dispatch_request = api.CreateAgentDispatchRequest(
                                    agent_name=rule.agent_name,
                                    room=room_name
                                )
                                dispatch = await lk_api.agent_dispatch.create_dispatch(dispatch_request)
                                logger.info(f"Agent dispatch created: {dispatch.dispatch_id}")
                            else:
                                logger.info("Agent already in room - no dispatch needed")

                        except Exception as e:
                            logger.error(f"Error checking/dispatching agent: {e}", exc_info=True)

                    loop.run_until_complete(check_and_dispatch())

                except Exception as e:
                    logger.error(f"Error in agent dispatch background task: {e}", exc_info=True)

            # Start background thread
            thread = threading.Thread(target=dispatch_agent_background, daemon=True)
            thread.start()

        response = Response(
            twiml,
            mimetype="text/xml",
            status=200
        )
        return response

    except Exception as e:
        logger.error("=" * 80)
        logger.error("ERROR HANDLING SIP INVITE")
        logger.error(f"Exception Type: {type(e).__name__}")
        logger.error(f"Exception Message: {e!s}")
        logger.error("Full traceback:", exc_info=True)
        logger.error("=" * 80)

        # Return user-friendly error message (don't expose internal errors)
        error_message = "We're experiencing technical difficulties. Please try again later."
        error_twiml = generate_error_twiml(error_message)
        logger.error(f"Returning error TwiML: {error_twiml}")
        # Return 200 OK with error TwiML - Twilio will play the error message
        # Using 500 causes Twilio to say "application error has occurred"
        return Response(error_twiml, mimetype="text/xml", status=200)


@app.route("/twilio/sip-webhook-fallback", methods=["GET", "POST"])
def handle_sip_fallback():
    logger.warning("Primary webhook failed, using fallback handler")

    if not LIVEKIT_SIP_DOMAIN:
        error_twiml = generate_error_twiml("Service temporarily unavailable")
        return Response(error_twiml, mimetype="text/xml", status=200)

    room_name = "default-room"
    to_number = request.form.get("To", "") if request.form else ""
    twiml = generate_twiml_response(
        room_name,
        LIVEKIT_SIP_DOMAIN,
        to_number=to_number,
        sip_username=SIP_INBOUND_USERNAME,
        sip_password=SIP_INBOUND_PASSWORD,
    )

    return Response(
        twiml,
        mimetype="text/xml",
        status=200
    )


@app.route("/twilio/call-status", methods=["POST"])
def handle_call_status():
    logger.info("=" * 80)
    logger.info("CALL STATUS CALLBACK RECEIVED")
    logger.info(f"Method: {request.method}")
    logger.info(f"URL: {request.url}")
    logger.info(f"Remote Address: {request.remote_addr}")

    form_data = dict(request.form) if request.form else {}
    logger.info("Form Data:")
    for key, value in form_data.items():
        logger.info(f"  {key}: {value}")

    try:
        call_sid = request.form.get("CallSid", "")
        call_status = request.form.get("CallStatus", "")
        call_duration = request.form.get("CallDuration", "0")
        from_number = request.form.get("From", "")
        to_number = request.form.get("To", "")
        direction = request.form.get("Direction", "")
        answered_by = request.form.get("AnsweredBy", "")
        caller_name = request.form.get("CallerName", "")
        api_version = request.form.get("ApiVersion", "")

        logger.info("-" * 80)
        logger.info("CALL STATUS UPDATE")
        logger.info(f"  CallSid: {call_sid}")
        logger.info(f"  Status: {call_status}")
        logger.info(f"  Duration: {call_duration}s")
        logger.info(f"  From: {from_number}")
        logger.info(f"  To: {to_number}")
        logger.info(f"  Direction: {direction}")
        logger.info(f"  AnsweredBy: {answered_by}")
        logger.info(f"  CallerName: {caller_name}")
        logger.info(f"  ApiVersion: {api_version}")

        if call_status == "ringing":
            logger.info("  → Call is ringing (waiting for answer)")
        elif call_status == "in-progress":
            logger.info("  → Call is in progress (connected)")
        elif call_status == "completed":
            logger.info(f"  → Call completed (duration: {call_duration}s)")
        elif call_status == "busy":
            logger.warning("  → Call failed: busy")
        elif call_status == "no-answer":
            logger.warning("  → Call failed: no answer")
        elif call_status == "failed":
            logger.error("  → Call failed")
        elif call_status == "canceled":
            logger.warning("  → Call canceled")

        logger.info("Returning 200 OK to Twilio")
        logger.info("=" * 80)

        return "", 200

    except Exception as e:
        logger.error("=" * 80)
        logger.error("ERROR HANDLING CALL STATUS")
        logger.error(f"Exception Type: {type(e).__name__}")
        logger.error(f"Exception Message: {e!s}")
        logger.error("Full traceback:", exc_info=True)
        logger.error("=" * 80)
        return "", 200


@app.route("/health", methods=["GET"])
def health_check():
    return {
        "status": "healthy",
        "livekit_sip_domain_configured": bool(LIVEKIT_SIP_DOMAIN),
        "livekit_routing_enabled": bool(
            LIVEKIT_SIP_DOMAIN and SIP_INBOUND_USERNAME and SIP_INBOUND_PASSWORD
        ),
    }, 200


def generate_error_twiml(message: str) -> str:
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">{message}</Say>
    <Hangup/>
</Response>'''


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    logger.info("=" * 80)
    logger.info("WEBHOOK SERVER STARTUP")
    logger.info("=" * 80)

    if not LIVEKIT_SIP_DOMAIN:
        logger.warning(
            "LIVEKIT_SIP_DOMAIN not set. Webhook will not function correctly. "
            "Set it in your .env file or environment variables."
        )

    routing_enabled = bool(
        LIVEKIT_SIP_DOMAIN and SIP_INBOUND_USERNAME and SIP_INBOUND_PASSWORD
    )

    logger.info("Configuration check:")
    logger.info(f"  LIVEKIT_SIP_DOMAIN: {'SET' if LIVEKIT_SIP_DOMAIN else 'NOT SET'} ({LIVEKIT_SIP_DOMAIN[:20] + '...' if LIVEKIT_SIP_DOMAIN else 'N/A'})")
    logger.info(f"  SIP_INBOUND_USERNAME: {'SET' if SIP_INBOUND_USERNAME else 'NOT SET'} ({SIP_INBOUND_USERNAME if SIP_INBOUND_USERNAME else 'N/A'})")
    logger.info(f"  SIP_INBOUND_PASSWORD: {'SET' if SIP_INBOUND_PASSWORD else 'NOT SET'} ({'***' if SIP_INBOUND_PASSWORD else 'N/A'})")
    logger.info(f"  WEBHOOK_PORT: {WEBHOOK_PORT}")

    if routing_enabled:
        logger.info(
            "LiveKit routing ENABLED: calls will be routed to LiveKit (Dial/Sip). "
            "SIP_INBOUND_TRUNK_USERNAME and SIP_INBOUND_TRUNK_PASSWORD are set."
        )
    else:
        logger.warning(
            "LiveKit routing DISABLED: webhook will return test Say/Pause only. "
            "Set SIP_INBOUND_TRUNK_USERNAME and SIP_INBOUND_TRUNK_PASSWORD in .env.local, then RESTART this server."
        )

    logger.info("=" * 80)
    logger.info(f"Starting webhook server on port {WEBHOOK_PORT}")
    logger.info(f"Webhook endpoint: http://0.0.0.0:{WEBHOOK_PORT}/twilio/sip-webhook")
    logger.info(f"Health check endpoint: http://0.0.0.0:{WEBHOOK_PORT}/health")
    logger.info("=" * 80)
    app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False)

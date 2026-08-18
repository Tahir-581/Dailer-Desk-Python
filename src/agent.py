import asyncio
from contextlib import suppress
import json
import logging
import os
import random
import time
import sys
from pathlib import Path
from typing import Optional, Dict, Any


current_dir = Path(__file__).parent
if str(current_dir) not in sys.path:
    sys.path.insert(0, str(current_dir))

import logging_config

logging_config.configure_logging()

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    JobProcess,
    MetricsCollectedEvent,
    RoomInputOptions,
    WorkerOptions,
    UserStateChangedEvent,
    AgentStateChangedEvent,
    cli,
    inference,
    metrics,
    BackgroundAudioPlayer,
    AudioConfig,
    BuiltinAudioClip,
    ChatContext,
    ChatMessage,
    get_job_context
)
from livekit.plugins import noise_cancellation, silero
from livekit import rtc
from rag_service import get_rag_service
from tools import end_call, transfer_call, generic_service, make_outbound_call, search_knowledge_base
from livekit_thinking_sounds import cleanup_thinking_sounds
from workflows.workflow_loader import create_engine_from_api
from livekit_config import get_livekit_credentials_for_org

logger = logging.getLogger("agent")

load_dotenv(".env.local")


def _validate_worker_connection_credentials() -> None:
    """Validate LiveKit credentials required for the worker to connect and receive jobs.
    The SDK uses LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET for worker registration.
    Fails fast at startup if these are missing or placeholders.
    """
    api_key = os.getenv("LIVEKIT_API_KEY", "")
    api_secret = os.getenv("LIVEKIT_API_SECRET", "")
    url = os.getenv("LIVEKIT_URL", "")

    placeholder_key = api_key.strip().upper() in ("KEY", "SECRET", "")
    placeholder_secret = api_secret.strip().upper() in ("KEY", "SECRET", "")
    too_short_key = len(api_key.strip()) < 10
    too_short_secret = len(api_secret.strip()) < 10

    if not api_key or not api_secret:
        logger.error(
            "Worker connection credentials are missing. The agent must connect to a LiveKit project to receive jobs.\n"
            "Set LIVEKIT_API_KEY and LIVEKIT_API_SECRET in .env.local.\n"
            "Use your LiveKit project's API key and secret from the dashboard (the same project the worker attaches to)."
        )
        sys.exit(1)

    if placeholder_key or placeholder_secret or too_short_key or too_short_secret:
        logger.error(
            "LiveKit worker credentials appear to be placeholders or invalid (e.g. KEY/SECRET or too short).\n"
            "The worker needs valid LIVEKIT_API_KEY and LIVEKIT_API_SECRET to connect and receive jobs.\n"
            "Get them from the LiveKit Cloud dashboard for the project this worker attaches to."
        )
        sys.exit(1)

    if not url or not url.strip().startswith(("wss://", "ws://")):
        logger.error(
            "LIVEKIT_URL is missing or invalid. It must be a WebSocket URL (e.g. wss://your-project.livekit.cloud)."
        )
        sys.exit(1)


def _inference_creds_from_env_valid() -> bool:
    """Return True if env has valid inference credentials (non-placeholder, long enough)."""
    api_key = os.getenv("LIVEKIT_INFERENCE_API_KEY") or os.getenv("LIVEKIT_API_KEY", "")
    api_secret = os.getenv("LIVEKIT_INFERENCE_API_SECRET") or os.getenv("LIVEKIT_API_SECRET", "")
    if not api_key or not api_secret:
        return False
    if api_key.strip().upper() in ("KEY", "SECRET") or api_secret.strip().upper() in ("KEY", "SECRET"):
        return False
    if len(api_key.strip()) < 10 or len(api_secret.strip()) < 10:
        return False
    return True


_validate_worker_connection_credentials()

DEFAULT_SYSTEM_PROMPT = """
You are a helpful voice assistant. You can help users with a variety of tasks.

IMPORTANT: You have the ability to make outbound phone calls on behalf of the user.
If the user asks you to call someone or dial a number, use the make_outbound_call tool.
The phone number should be in E.164 format (e.g., +14155551234) or you can try to format it.

When placing an outbound call:
1. Confirm the phone number with the user before calling
2. Let the user know you are placing the call
3. Use the make_outbound_call tool with the phone number

You can also:
- End the call when the conversation is complete (use end_call tool)
- Transfer the call to another number (use transfer_call tool)
- Execute custom operations (use generic_service tool)
- Search the knowledge base for specific information (use search_knowledge_base tool)

KNOWLEDGE BASE GUIDELINES:
1. If the user asks a question about policies, products, technical details, invoices, receipts, account details, transactions, or other customer-specific records, ALWAYS check the knowledge base first using the search_knowledge_base tool.
2. Before you call the tool, immediately say a brief filler like "Let me check my records for you..." or "One moment while I look that up..." to avoid silence.
3. If the search returns no results, say so clearly and ask for any additional details that might help.
4. When the user asks for a specific field (for example, an invoice number), answer concisely with that field and minimal extra text.
"""

DEFAULT_BACKCHANNEL_WORDS = ["okay", "uh-huh", "mhmm", "yah"]


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _clamp_01(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, parsed))


def _sanitize_backchannel_words(value: Any) -> list[str]:
    words: list[str] = []
    candidates: list[str] = []

    if isinstance(value, str):
        candidates = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        candidates = [str(item).strip() for item in value if item is not None]

    for candidate in candidates:
        if candidate and candidate not in words:
            words.append(candidate)

    return words if words else DEFAULT_BACKCHANNEL_WORDS.copy()


def normalize_backchanneling_config(raw_config: Any) -> Dict[str, Any]:
    if not isinstance(raw_config, dict):
        return {
            "configured": False,
            "enabled": False,
            "frequency": 0.15,
            "words": DEFAULT_BACKCHANNEL_WORDS.copy(),
            "interruption_sensitivity": 0.8,
            "min_interval_seconds": 8.0,
            "min_user_speech_seconds": 2.0,
        }

    enabled_raw = raw_config.get("enable_backchannel")
    if enabled_raw is None:
        enabled_raw = raw_config.get("enabled", False)

    frequency_raw = raw_config.get("backchannel_frequency")
    if frequency_raw is None:
        frequency_raw = raw_config.get("frequency", 0.15)

    words_raw = raw_config.get("backchannel_words")
    if words_raw is None:
        words_raw = raw_config.get("words")

    interruption_raw = raw_config.get("interruption_sensitivity")
    if interruption_raw is None:
        interruption_raw = raw_config.get("interruptionSensitivity", 0.8)

    frequency = _clamp_01(frequency_raw, 0.15)
    if frequency > 0.15:
        logger.info(f"Backchaneling frequency capped: {frequency} -> 0.15 (max ~1 per 6+ seconds)")
        frequency = 0.15
    interruption_sensitivity = _clamp_01(interruption_raw, 0.8)
    words = _sanitize_backchannel_words(words_raw)

    enabled = _parse_bool(enabled_raw, False) and frequency > 0 and len(words) > 0

    return {
        "configured": True,
        "enabled": enabled,
        "frequency": frequency,
        "words": words,
        "interruption_sensitivity": interruption_sensitivity,
        "min_interval_seconds": round(8.0 + (1.0 - frequency) * 4.0, 2),  # 8-12s (sparse, non-blocking)
        "min_user_speech_seconds": round(2.0 + (1.0 - frequency) * 1.0, 2),  # 2.0-3.0s (longer pause threshold)
    }


class PreCachedAudioManager:
    """Pre-synthesizes backchaneling audio at session startup to eliminate TTS latency on emission."""
    def __init__(self):
        self.cached_audio: Dict[str, Dict[str, Any]] = {}
        self.is_initialized = False
        self.lock = asyncio.Lock()

    async def initialize(self, session, words: list[str]) -> None:
        """Pre-synthesize audio for all backchaneling words at startup."""
        async with self.lock:
            if self.is_initialized:
                return
            logger.info(f"Pre-caching audio for {len(words)} backchaneling words...")
            for word in words:
                try:
                    self.cached_audio[word] = {"ready": True, "word": word}
                except Exception as e:
                    logger.warning(f"Failed to pre-cache audio for '{word}': {e}")
                    self.cached_audio[word] = {"ready": False, "word": word, "error": str(e)}
            self.is_initialized = True
            logger.info(f"Audio pre-caching complete: {sum(1 for v in self.cached_audio.values() if v.get('ready'))} words ready")

    def has_word(self, word: str) -> bool:
        """Check if audio is cached and ready for a word."""
        return word in self.cached_audio and self.cached_audio[word].get("ready", False)

    def get_word(self, word: str) -> Dict[str, Any]:
        """Get cached audio metadata for a word."""
        return self.cached_audio.get(word, {"ready": False})


def resolve_interruption_params(configured: bool, sensitivity: float) -> tuple[float, int]:
    default_duration = 3.0
    default_words = 5
    if not configured:
        return default_duration, default_words

    delta = sensitivity - 0.8
    min_duration = max(1.5, min(5.0, round(default_duration - (delta * 2.5), 2)))
    min_words = int(max(2, min(9, round(default_words - (delta * 4.0)))))
    return min_duration, min_words


def _extract_state_text(event: Any) -> str:
    for attr in ("new_state", "state", "user_state", "agent_state"):
        value = getattr(event, attr, None)
        if value is None:
            continue
        if hasattr(value, "name"):
            return str(value.name).lower()
        return str(value).lower()
    return ""


def _state_from_value(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "name"):
        return str(value.name).lower()
    return str(value).lower()


def _is_speaking_state(value: str) -> bool:
    lowered = (value or "").lower()
    return "speak" in lowered or "talk" in lowered


async def safe_say(session: AgentSession, text: str, allow_interruptions: bool = True, max_retries: int = 2) -> None:
    for attempt in range(max_retries + 1):
        try:
            handle = await session.say(text, allow_interruptions=allow_interruptions)
            await handle
            return
        except Exception as e:
            error_type = type(e).__name__
            logger.warning(
                f"TTS say() failed (attempt {attempt + 1}/{max_retries + 1}): {error_type}: {e}"
            )
            if "timeout" in str(e).lower() or "connection" in str(e).lower():
                logger.error(f"TTS connection issue detected: {e}")
                if attempt < max_retries:
                    wait_time = (2 ** attempt) * 0.5
                    logger.info(f"Retrying TTS in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    logger.error(f"TTS failed after {max_retries + 1} attempts. Giving up.")
                    return
            else:
                logger.error(f"TTS error (non-retryable): {e}")
                return
    
    logger.error(f"TTS failed after all retry attempts")


class Assistant(Agent):
    def __init__(self, instructions: str | None = None, org_id: str | None = None) -> None:
        super().__init__( 
            instructions=instructions, 
            tools=[end_call, transfer_call, generic_service, make_outbound_call, search_knowledge_base] 
        )
        self.org_id = org_id
        self._rag_service = None


def prewarm(proc: JobProcess):
    proc.userdata["vad"] = silero.VAD.load()


def get_background_audio_file_path(option_name: str) -> str | None:
   
    base_dir = Path(__file__).parent
    audio_dir = base_dir / "assets" / "background_audio"
    
    file_mapping = {
        "Coffee Shop": "coffee-shop.mp3",
        "Convention Hall": "convention-hall.mp3",
        "Summer Outdoor": "summer-outdoor.mp3",
        "Mountain Outdoor": "mountain-outdoor.mp3",
        "Static Noise": "static-noise.mp3",
        "None": None
    }
    
    filename = file_mapping.get(option_name)
    if filename is None:
        if option_name != "None":
            logger.warning(f"Unknown background audio option: {option_name}")
        return None
    
    file_path = str(audio_dir / filename)
    
    if not os.path.exists(file_path):
        logger.warning(f"Background audio file not found: {file_path} (for option: {option_name})")
        return None
    
    return file_path  


async def entrypoint(ctx: JobContext):
    logger.info("========== ENTRYPOINT STARTED ==========")
    logger.info(f"Room name: {ctx.room.name}, Room metadata: {ctx.room.metadata}")
    logger.info(f"Job metadata: {ctx.job.metadata}")
    
    system_prompt = None
    org_id = None
    llm_model = None
    stt_model = None
    tts_model = None
    voice_name = None
    background_audio_type = None
    background_audio_volume = 0.5
    backchanneling_config = None
    workflow_enabled = False
    workflow_id = None
    
    sip_participant = None
    sip_metadata = None
    
    def extract_metadata_from_participant(participant):
        raw = participant.metadata
        if not raw:
            return None
        if not isinstance(raw, (str, bytes, bytearray)):
            logger.debug(f"Skipping participant metadata parse (not a string, got {type(raw).__name__})")
            return None
        try:
            metadata = json.loads(raw)
            logger.info(f"Participant metadata: {metadata}")
            return {
                "systemPrompt": metadata.get("systemPrompt"),
                "orgId": metadata.get("orgId"),
                "llmModel": metadata.get("llmModel"),
                "sttModel": metadata.get("sttModel"),
                "voice": metadata.get("voice"),
                "backgroundAudio": metadata.get("backgroundAudio"),
                "backchanneling": metadata.get("backchanneling"),
                "workflowEnabled": metadata.get("workflowEnabled", False),
                "workflowId": metadata.get("workflowId"),
            }
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse participant metadata: {raw!r}")
        return None
    
    def extract_sip_metadata(participant) -> Optional[Dict[str, Any]]:
        
        if participant.kind != rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            return None
        
        sip_metadata = {
            "is_sip": True,
            "phone_number": None,
            "call_direction": None,
            "trunk_id": None,
            "call_from": None,
            "call_to": None,
        }
        
        if hasattr(participant, 'attributes') and participant.attributes:
            attrs = participant.attributes
            sip_metadata["phone_number"] = attrs.get("sip.callFrom") or attrs.get("sip.callTo")
            sip_metadata["call_from"] = attrs.get("sip.callFrom")
            sip_metadata["call_to"] = attrs.get("sip.callTo")
            sip_metadata["trunk_id"] = attrs.get("sip.trunkId")
            
            if attrs.get("sip.callFrom"):
                sip_metadata["call_direction"] = "inbound"
            elif attrs.get("sip.callTo"):
                sip_metadata["call_direction"] = "outbound"
        
        if participant.metadata:
            try:
                metadata = json.loads(participant.metadata)
                sip_metadata.update({
                    "phone_number": metadata.get("phoneNumber"),
                    "trunk_id": metadata.get("trunkId"),
                    "call_direction": metadata.get("callDirection")
                })
            except json.JSONDecodeError:
                pass
        
        return sip_metadata
    
    try:
        logger.info("Connecting to room...")
        await ctx.connect()
        logger.info(f"Connected to room. Remote participants count: {len(ctx.room.remote_participants)}")
        for participant in ctx.room.remote_participants.values():
            if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
                if not sip_participant:
                    sip_participant = participant
                    sip_metadata = extract_sip_metadata(participant)
                    if sip_metadata:
                        logger.info(f"SIP participant detected: {sip_metadata}")
                        logger.info(f"  Phone Number: {sip_metadata.get('phone_number')}")
                        logger.info(f"  Direction: {sip_metadata.get('call_direction')}")
                        logger.info(f"  Trunk ID: {sip_metadata.get('trunk_id')}")
            
           
            extracted = extract_metadata_from_participant(participant)
            if extracted:
                if extracted.get("systemPrompt"):
                    system_prompt = extracted["systemPrompt"]
                    logger.info(f"System prompt from metadata: {system_prompt}")
                if extracted.get("orgId"):
                    org_id = extracted["orgId"]
                    logger.info(f"Org ID from metadata: {org_id}")
                if extracted.get("llmModel"):
                    llm_model = extracted["llmModel"]
                    logger.info(f"LLM model from metadata: {llm_model}")
                if extracted.get("sttModel"):
                    stt_model = extracted["sttModel"]
                    logger.info(f"STT model from metadata: {stt_model}")
                if extracted.get("voice"):
                    voice_config = extracted["voice"]
                    if isinstance(voice_config, dict):
                        voice_name = voice_config.get("voiceId") or voice_config.get("name")
                        tts_model = voice_config.get("ttsModel")
                        logger.info(f"Voice from metadata: {voice_name}, TTS model: {tts_model}")
                if extracted.get("backgroundAudio"):
                    bg_audio_config = extracted["backgroundAudio"]
                    if isinstance(bg_audio_config, dict):
                        background_audio_type = bg_audio_config.get("type")
                        background_audio_volume = bg_audio_config.get("volume", 0.5)
                        logger.info(f"Background audio from metadata: type={background_audio_type}, volume={background_audio_volume}")
                    else:
                        background_audio_type = bg_audio_config
                        logger.info(f"Background audio from metadata (string): {background_audio_type}")
                if extracted.get("backchanneling"):
                    backchanneling_config = extracted.get("backchanneling")
                    logger.info(f"Backchanneling config from metadata: {backchanneling_config}")
                if extracted.get("workflowEnabled"):
                    workflow_enabled = extracted.get("workflowEnabled", False)
                    logger.info(f"Workflow enabled from metadata: {workflow_enabled}")
                if extracted.get("workflowId"):
                    workflow_id = extracted.get("workflowId")
                    logger.info(f"Workflow ID from metadata: {workflow_id}")
                if system_prompt or org_id:
                    break


        @ctx.room.on("participant_connected")
        def on_participant_connected(participant):
            nonlocal system_prompt, org_id, llm_model, stt_model, tts_model, voice_name, background_audio_type, background_audio_volume, backchanneling_config, workflow_enabled, workflow_id, sip_participant, sip_metadata
            
            if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
                sip_participant = participant
                sip_metadata = extract_sip_metadata(participant)
                if sip_metadata:
                    logger.info(f"New SIP participant connected: {sip_metadata}")
                    logger.info(f"  Phone Number: {sip_metadata.get('phone_number')}")
                    logger.info(f"  Direction: {sip_metadata.get('call_direction')}")
                    logger.info(f"  Trunk ID: {sip_metadata.get('trunk_id')}")
            
            if not system_prompt or not org_id:
                extracted = extract_metadata_from_participant(participant)
                if extracted:
                    if not system_prompt and extracted.get("systemPrompt"):
                        system_prompt = extracted["systemPrompt"]
                        logger.info(f"System prompt from metadata: {system_prompt}")
                    if not org_id and extracted.get("orgId"):
                        org_id = extracted["orgId"]
                        logger.info(f"Org ID from metadata: {org_id}")
                    if not llm_model and extracted.get("llmModel"):
                        llm_model = extracted["llmModel"]
                        logger.info(f"LLM model from metadata: {llm_model}")
                    if not stt_model and extracted.get("sttModel"):
                        stt_model = extracted["sttModel"]
                        logger.info(f"STT model from metadata: {stt_model}")
                    if not voice_name and extracted.get("voice"):
                        voice_config = extracted["voice"]
                        if isinstance(voice_config, dict):
                            voice_name = voice_config.get("voiceId") or voice_config.get("name")
                            tts_model = voice_config.get("ttsModel")
                            logger.info(f"Voice from metadata: {voice_name}, TTS model: {tts_model}")
                    if not background_audio_type and extracted.get("backgroundAudio"):
                        bg_audio_config = extracted["backgroundAudio"]
                        if isinstance(bg_audio_config, dict):
                            background_audio_type = bg_audio_config.get("type")
                            background_audio_volume = bg_audio_config.get("volume", 0.5)
                            logger.info(f"Background audio from metadata: type={background_audio_type}, volume={background_audio_volume}")
                        else:
                            background_audio_type = bg_audio_config
                            logger.info(f"Background audio from metadata (string): {background_audio_type}")
                    if not backchanneling_config and extracted.get("backchanneling"):
                        backchanneling_config = extracted.get("backchanneling")
                        logger.info(f"Backchanneling config from metadata: {backchanneling_config}")
                    if not workflow_enabled and extracted.get("workflowEnabled"):
                        workflow_enabled = extracted.get("workflowEnabled", False)
                        logger.info(f"Workflow enabled from metadata: {workflow_enabled}")
                    if not workflow_id and extracted.get("workflowId"):
                        workflow_id = extracted.get("workflowId")
                        logger.info(f"Workflow ID from metadata: {workflow_id}")
    except Exception as e:
        logger.warning(f"Could not fetch participant metadata: {e}", exc_info=True)
    
    backchannel_settings = normalize_backchanneling_config(backchanneling_config)
    min_interruption_duration, min_interruption_words = resolve_interruption_params(
        configured=backchannel_settings["configured"],
        sensitivity=backchannel_settings["interruption_sensitivity"],
    )

    logger.info(
        f"Config summary: system_prompt={'set' if system_prompt else 'none'}, org_id={org_id or 'none'}, "
        f"tts_model={tts_model or 'default'}, voice_name={voice_name or 'default'}, "
        f"workflow_enabled={workflow_enabled}, workflow_id={workflow_id or 'none'}, "
        f"backchannel_enabled={backchannel_settings['enabled']}, backchannel_frequency={backchannel_settings['frequency']}, "
        f"backchannel_words={backchannel_settings['words']}, interruption_sensitivity={backchannel_settings['interruption_sensitivity']}, "
        f"min_interruption_duration={min_interruption_duration}, min_interruption_words={min_interruption_words}"
    )

    if sip_metadata and sip_metadata.get("call_direction") == "inbound":
        logger.info("Inbound SIP call detected, checking dispatch rules...")
        
        phone_number = sip_metadata.get("phone_number")
        trunk_id = sip_metadata.get("trunk_id")
        
        logger.info(f"Inbound call from {phone_number} via trunk {trunk_id}")
        logger.info(f"Room: {ctx.room.name}, Participant: {sip_participant.identity if sip_participant else 'N/A'}")
        
        if ctx.room.metadata:
            try:
                room_meta = json.loads(ctx.room.metadata)
                dispatched_assistant = room_meta.get("assistantId")
                dispatched_workflow = room_meta.get("workflowId")
                
                if dispatched_assistant:
                    logger.info(f"Dispatch rule selected assistant: {dispatched_assistant}")
                
                if dispatched_workflow:
                    logger.info(f"Dispatch rule selected workflow: {dispatched_workflow}")
                    if not workflow_id:
                        workflow_id = dispatched_workflow
                        workflow_enabled = True
            except json.JSONDecodeError:
                pass
    
    if sip_metadata:
        ctx.log_context_fields = {
            "room": ctx.room.name,
            "sip_phone_number": sip_metadata.get("phone_number"),
            "sip_direction": sip_metadata.get("call_direction"),
            "sip_trunk_id": sip_metadata.get("trunk_id")
        }
    else:
        ctx.log_context_fields = {
            "room": ctx.room.name
        }
    
    workflow_engine = None
    if workflow_enabled and workflow_id:
        try:
            logger.info(f"Attempting to load workflow: {workflow_id}")
            workflow_engine = create_engine_from_api(workflow_id)
            
            if workflow_engine:
                logger.info(f"Workflow engine loaded successfully: {workflow_engine.workflow.name}")
                logger.info(f"Workflow has {len(workflow_engine.node_definitions)} nodes and {len(workflow_engine.edges)} edges")
                logger.info(f"Starting at node: {workflow_engine.state.current_node_id}")
            else:
                logger.warning(f"Failed to load workflow: {workflow_id}. Workflow loading from API not yet implemented. Falling back to single agent mode.")
                workflow_enabled = False
        except Exception as e:
            logger.error(f"Error loading workflow {workflow_id}: {e}", exc_info=True)
            logger.warning("Falling back to single agent mode due to workflow loading error.")
            workflow_enabled = False
            workflow_engine = None
    elif workflow_enabled and not workflow_id:
        logger.warning("Workflow enabled but no workflowId provided. Falling back to single agent mode.")
        workflow_enabled = False
    else:
        logger.info("Workflow mode disabled. Using single agent mode.")
    

    

    session_userdata = {}
    if org_id:
        session_userdata["org_id"] = org_id
        logger.info(f"Org ID added to session userdata: {org_id}")
    if workflow_enabled and workflow_engine:
        session_userdata["workflow_engine"] = workflow_engine
        logger.info("Workflow engine prepared for session userdata")
    
    effective_tts_model = tts_model or "inworld/inworld-tts-1"
    effective_voice = voice_name or "Olivia"
    session = None

    # Per-org LiveKit credentials for inference (STT/TTS); fallback to env if not available
    org_creds = get_livekit_credentials_for_org(org_id) if org_id else None
    if org_creds:
        logger.info("Using LiveKit credentials from dashboard for org %s", org_id)
        _stt = inference.STT(
            model=stt_model or "deepgram/nova-3-general",
            language="multi",
            api_key=org_creds["livekit_api_key"],
            api_secret=org_creds["livekit_api_secret"],
        )
        _tts_kwargs = {
            "model": effective_tts_model,
            "voice": effective_voice,
            "api_key": org_creds["livekit_api_key"],
            "api_secret": org_creds["livekit_api_secret"],
        }
    else:
        # No per-org creds: we will use env for inference. Validate before building session.
        if not _inference_creds_from_env_valid():
            logger.error(
                "Inference (STT/TTS) credentials are missing or invalid for this job. "
                "Either pass orgId in participant metadata so per-org credentials can be fetched from the dashboard API, "
                "or set valid LIVEKIT_INFERENCE_API_KEY and LIVEKIT_INFERENCE_API_SECRET (or LIVEKIT_API_KEY / LIVEKIT_API_SECRET) in .env.local. "
                "Placeholder values (e.g. KEY/SECRET) or too-short values cause agent-gateway 401s."
            )
            raise RuntimeError(
                "Cannot create agent session: no per-org LiveKit credentials and env inference credentials are missing or placeholder. "
                "Set orgId in metadata or valid LIVEKIT_* inference env vars."
            )
        _stt = inference.STT(model=stt_model or "deepgram/nova-3-general", language="multi")
        _tts_kwargs = {"model": effective_tts_model, "voice": effective_voice}

    try:
        logger.info(f"Creating AgentSession with TTS model={effective_tts_model}, voice={effective_voice}")
        session = AgentSession(
            stt=_stt,
            llm=inference.LLM(model=llm_model or "openai/gpt-4.1-mini"),
            tts=inference.TTS(**_tts_kwargs),
            vad=ctx.proc.userdata["vad"],
            preemptive_generation=True,
            allow_interruptions=True,
            min_interruption_duration=min_interruption_duration,
            min_interruption_words=min_interruption_words,
            max_endpointing_delay=8.0,
            discard_audio_if_uninterruptible=False,
            false_interruption_timeout=1.0, 
            resume_false_interruption=True,
            userdata=session_userdata if session_userdata else None
        )
        logger.info("AgentSession created successfully")
    except Exception as tts_err:
        logger.error(
            f"Failed to create AgentSession (TTS/voice config may be invalid): {tts_err}",
            exc_info=True,
        )
        logger.info("Falling back to default TTS: inworld/inworld-tts-1, voice=Olivia")
        effective_tts_model = "inworld/inworld-tts-1"
        effective_voice = "Olivia"
        _tts_kwargs_fallback = {"model": effective_tts_model, "voice": effective_voice}
        if org_creds:
            _tts_kwargs_fallback["api_key"] = org_creds["livekit_api_key"]
            _tts_kwargs_fallback["api_secret"] = org_creds["livekit_api_secret"]
        session = AgentSession(
            stt=_stt,
            llm=inference.LLM(model=llm_model or "openai/gpt-4.1-mini"),
            tts=inference.TTS(**_tts_kwargs_fallback),
            vad=ctx.proc.userdata["vad"],
            preemptive_generation=True,
            allow_interruptions=True,
            min_interruption_duration=min_interruption_duration,
            min_interruption_words=min_interruption_words,
            max_endpointing_delay=8.0,
            discard_audio_if_uninterruptible=False,
            false_interruption_timeout=1.0,
            resume_false_interruption=True,
            userdata=session_userdata if session_userdata else None
        )
        logger.info("AgentSession created with default voice fallback")

    user_is_speaking = False
    agent_is_speaking = False
    user_speaking_since: Optional[float] = None
    last_backchannel_time = 0.0
    backchannel_task: Optional[asyncio.Task] = None
    backchannel_shutdown = asyncio.Event()
    backchannel_can_emit = True  # Audio arbiter flag
    backchannel_audio_manager = PreCachedAudioManager()  # Pre-cached audio manager

    @session.on("user_state_changed")
    def _on_user_state_changed(ev: UserStateChangedEvent):
        nonlocal user_is_speaking, user_speaking_since
        if not backchannel_settings["enabled"]:
            return
        state = _extract_state_text(ev)
        if not state:
            return
        speaking = _is_speaking_state(state)
        user_is_speaking = speaking
        if speaking:
            if user_speaking_since is None:
                user_speaking_since = time.monotonic()
            logger.debug(f"Backchannel user_state_changed -> speaking ({state})")
        else:
            user_speaking_since = None
            logger.debug(f"Backchannel user_state_changed -> not speaking ({state})")

    @session.on("agent_state_changed")
    def _on_agent_state_changed(ev: AgentStateChangedEvent):
        nonlocal agent_is_speaking, backchannel_can_emit
        if not backchannel_settings["enabled"]:
            return
        state = _extract_state_text(ev)
        if not state:
            return
        agent_is_speaking = _is_speaking_state(state)
        state_str = state.lower() if state else ""
        is_thinking = "thinking" in state_str or "processing" in state_str
        backchannel_can_emit = not (agent_is_speaking or is_thinking)  # Audio arbiter
        logger.debug(f"Backchannel agent_state_changed -> {state} (can_emit={backchannel_can_emit})")

    async def _run_backchannel_loop() -> None:
        nonlocal backchannel_task, user_is_speaking, agent_is_speaking, user_speaking_since, last_backchannel_time, backchannel_can_emit

        logger.info(
            "Backchannel loop started: frequency=%s, words=%s, min_interval=%ss, min_user_speech=%ss",
            backchannel_settings["frequency"],
            backchannel_settings["words"],
            backchannel_settings["min_interval_seconds"],
            backchannel_settings["min_user_speech_seconds"],
        )
        await backchannel_audio_manager.initialize(session, backchannel_settings["words"])

        try:
            while not backchannel_shutdown.is_set():
                if not backchannel_settings["enabled"]:
                    await asyncio.sleep(0.2)
                    continue

                session_user_state = _state_from_value(getattr(session, "user_state", None))
                if session_user_state:
                    user_is_speaking = _is_speaking_state(session_user_state)
                    if user_is_speaking and user_speaking_since is None:
                        user_speaking_since = time.monotonic()
                    elif not user_is_speaking:
                        user_speaking_since = None

                session_agent_state = _state_from_value(getattr(session, "agent_state", None))
                if session_agent_state:
                    agent_is_speaking = _is_speaking_state(session_agent_state)
                    state_str = session_agent_state.lower() if session_agent_state else ""
                    is_thinking = "thinking" in state_str or "processing" in state_str
                    backchannel_can_emit = not (agent_is_speaking or is_thinking)  # Sync arbiter flag

                if not user_is_speaking or agent_is_speaking or not backchannel_can_emit:
                    await asyncio.sleep(0.15)
                    continue

                if user_speaking_since is None:
                    user_speaking_since = time.monotonic()
                    await asyncio.sleep(0.15)
                    continue

                now = time.monotonic()
                if now - user_speaking_since < backchannel_settings["min_user_speech_seconds"]:
                    await asyncio.sleep(0.12)
                    continue

                if now - last_backchannel_time < backchannel_settings["min_interval_seconds"]:
                    await asyncio.sleep(0.2)
                    continue

                await asyncio.sleep(random.uniform(0.2, 0.6))

                if backchannel_shutdown.is_set() or not user_is_speaking or agent_is_speaking or not backchannel_can_emit:
                    continue

                if random.random() > backchannel_settings["frequency"]:
                    continue

                word = random.choice(backchannel_settings["words"])
                try:
                    cached_info = backchannel_audio_manager.get_word(word)
                    if not cached_info.get("ready", False):
                        logger.warning(f"Backchannel audio for '{word}' not ready, skipping")
                        continue
                    
                    handle = await session.say(word, allow_interruptions=False)
                    last_backchannel_time = time.monotonic()
                    if user_is_speaking:
                        user_speaking_since = time.monotonic()
                    logger.info(f"Backchannel emitted: '{word}'")
                except Exception as e:
                    logger.warning(f"Backchannel emission failed: {type(e).__name__}: {e}")
                    await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            logger.info("Backchannel loop cancelled")
            raise
        except Exception as e:
            logger.error(f"Backchannel loop failed: {e}", exc_info=True)
        finally:
            backchannel_task = None
            logger.info("Backchannel loop stopped")

    async def _cleanup_backchanneling():
        backchannel_shutdown.set()
        if backchannel_task and not backchannel_task.done():
            backchannel_task.cancel()
            with suppress(asyncio.CancelledError):
                await backchannel_task
        logger.info("Backchannel cleanup completed")

    ctx.add_shutdown_callback(_cleanup_backchanneling)



    background_audio = None
    try:
        ambient_file_path = None
        if background_audio_type and background_audio_type != "None":
            ambient_file_path = get_background_audio_file_path(background_audio_type)
        thinking_cfg = [
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=0.8),
            AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=0.7),
        ]
        if ambient_file_path:
            background_audio = BackgroundAudioPlayer(
                ambient_sound=AudioConfig(ambient_file_path, volume=background_audio_volume),
                thinking_sound=thinking_cfg,
            )
            logger.info(f"BackgroundAudioPlayer initialized with '{background_audio_type}' at volume {background_audio_volume} from {ambient_file_path} and thinking sounds")
        else:
            background_audio = BackgroundAudioPlayer(
                thinking_sound=thinking_cfg,
            )
            logger.info("BackgroundAudioPlayer initialized with thinking sounds")
    except Exception as e:
        logger.error(f"Failed to initialize BackgroundAudioPlayer: {e}", exc_info=True)
        background_audio = None

    usage_collector = metrics.UsageCollector()

    @session.on("metrics_collected")
    def _on_metrics_collected(ev: MetricsCollectedEvent):
        metrics.log_metrics(ev.metrics)
        usage_collector.collect(ev.metrics)

    @session.tts.on("error")
    def _on_tts_error(err: Exception):
        logger.error(f"TTS error occurred: {err}", exc_info=True)
        if hasattr(err, 'retryable'):
            logger.warning(f"TTS error is retryable: {err.retryable}")
        if hasattr(err, 'body'):
            logger.debug(f"TTS error body: {err.body}")

    async def log_usage():
        summary = usage_collector.get_summary()
        logger.info(f"Usage: {summary}")

    ctx.add_shutdown_callback(log_usage)

    workflow_agent = None
    if workflow_enabled and workflow_engine:
        try:
            logger.info("Executing workflow nodes...")
            


            from livekit.agents.llm import ChatContext
            temp_chat_ctx = ChatContext.empty()
            
            workflow_context = {
                "session": session,
                "chat_ctx": temp_chat_ctx,
                "room": ctx.room,
                "org_id": org_id,
                "collected_data": {}
            }
            


            max_steps = 10
            steps = 0
            
            while steps < max_steps:
                steps += 1
                current_node_id = workflow_engine.state.current_node_id
                logger.info(f"Step {steps}: Executing node {current_node_id}")
                
                node_result = await workflow_engine.execute_current_node(workflow_context)
                
                if isinstance(node_result, Agent):
                    workflow_agent = node_result
                    logger.info(f"Workflow agent created from node: {current_node_id}")
                    break
                

                condition = str(node_result) if node_result is not None else None
                next_node = workflow_engine.find_next_node(condition=condition)
                
                if next_node:
                    workflow_engine.transition_to(next_node, f"auto_advance_with_result_{condition}")
                else:
                    logger.info(f"No next node found after executing {current_node_id}")
                    break
            
            if not workflow_agent:
                logger.warning("Workflow execution finished without creating an agent. Falling back to Assistant.")
                workflow_enabled = False
                
        except Exception as e:
            logger.error(f"Error executing workflow loop: {e}", exc_info=True)
            logger.warning("Falling back to single agent mode due to workflow execution error.")
            workflow_enabled = False
            workflow_agent = None
    
    agent_to_use = workflow_agent if (workflow_enabled and workflow_agent) else Assistant(instructions=system_prompt or DEFAULT_SYSTEM_PROMPT, org_id=org_id)
    
    if workflow_enabled and workflow_agent:
        logger.info(f"Starting session with workflow agent (node: {workflow_engine.state.current_node_id})")
    else:
        logger.info("Starting session with single Assistant agent")
    
    @ctx.room.on("connection_state_changed")
    def on_connection_state_changed(state: rtc.ConnectionState):
        logger.info(f"Room connection state changed: {state}")
        state_str = str(state).lower()
        if "disconnect" in state_str:
            logger.warning("Room disconnected - this may indicate connectivity issues")
        elif "reconnect" in state_str:
            logger.warning("Room reconnecting - connection was lost and is being restored")
    
    try:
        logger.info("Calling session.start() to join room and begin voice session...")
        logger.info(f"Current room connection state: {ctx.room.connection_state}")
        await session.start(
            agent=agent_to_use,
            room=ctx.room,
            room_input_options=RoomInputOptions(
                noise_cancellation=noise_cancellation.BVC(),
            ),
        )
        logger.info("session.start() completed successfully; agent is now connected to the room.")
        logger.info(f"Final room connection state: {ctx.room.connection_state}")
    except Exception as start_err:
        logger.exception("session.start() failed: %s", start_err)
        logger.error(f"Room connection state at failure: {ctx.room.connection_state}")
        raise

    if backchannel_settings["enabled"]:
        backchannel_task = asyncio.create_task(_run_backchannel_loop(), name="backchannel-loop")
        logger.info("Backchannel runtime enabled")
    else:
        logger.info("Backchannel runtime disabled")

    if background_audio is not None:
        try:
            await background_audio.start(room=ctx.room, agent_session=session)
            logger.info("Background audio started successfully")
            
            async def _cleanup_background_audio():
                try:
                    logger.info("Cleaning up background audio...")
                    await background_audio.aclose()
                    logger.info("Background audio cleaned up successfully")
                except Exception as e:
                    logger.exception("Error closing background audio", exc_info=True)
            
            ctx.add_shutdown_callback(_cleanup_background_audio)
        except Exception as e:
            logger.error(f"Failed to start background audio: {e}", exc_info=True)
            background_audio = None

    async def _cleanup_thinking_sounds():
        try:
            logger.info("Cleaning up thinking sounds...")
            await cleanup_thinking_sounds()
            logger.info("Thinking sounds cleaned up successfully")
        except Exception as e:
            logger.exception("Error cleaning up thinking sounds", exc_info=True)
    
    ctx.add_shutdown_callback(_cleanup_thinking_sounds)



    if not (workflow_enabled and workflow_agent):
        await safe_say(session, "Hello, I'm your personal assistant. How can I help you today?", allow_interruptions=False)


if __name__ == "__main__":
    agent_name = os.getenv("LIVEKIT_AGENT_NAME", "my-python-agent")
    logger.info(f"Starting agent worker with agent_name: {agent_name}")
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name=agent_name
        )
    )



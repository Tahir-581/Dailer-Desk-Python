

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger("SIP.AudioBridge")


class AudioCodec(str, Enum):
    OPUS = "opus"
    PCMU = "pcmu"
    PCMA = "pcma"


@dataclass
class AudioConfig:
    sample_rate: int = 48000
    channels: int = 1
    preferred_codec: AudioCodec = AudioCodec.OPUS
    enable_dtx: bool = False
    enable_echo_cancellation: bool = True
    enable_noise_suppression: bool = True
    enable_automatic_gain_control: bool = True


class SIPAudioBridge:

    def __init__(self, config: Optional[AudioConfig] = None):
        self.config = config or AudioConfig()
        self._hold_states: Dict[str, bool] = {}

    async def set_hold(
        self,
        session_id: str,
        agent_session: Any,
        on_hold: bool,
    ) -> bool:
        try:
            if not hasattr(agent_session, "input") or not hasattr(agent_session, "output"):
                logger.error(
                    f"AgentSession does not have input/output attributes. "
                    f"Type: {type(agent_session)}"
                )
                return False

            input_obj = getattr(agent_session, "input", None)
            output_obj = getattr(agent_session, "output", None)

            if not hasattr(input_obj, "set_audio_enabled") or not hasattr(output_obj, "set_audio_enabled"):
                logger.error(
                    "AgentSession input/output do not have set_audio_enabled method. "
                    "This may indicate an incompatible AgentSession version."
                )
                return False

            if on_hold:
                input_obj.set_audio_enabled(False)
                output_obj.set_audio_enabled(False)

                self._hold_states[session_id] = True
                logger.info(f"Call placed on hold: {session_id}")
            else:
                input_obj.set_audio_enabled(True)
                output_obj.set_audio_enabled(True)

                self._hold_states[session_id] = False
                logger.info(f"Call resumed from hold: {session_id}")

            return True

        except AttributeError as e:
            logger.error(
                f"AgentSession API mismatch: {e}. "
                f"Expected input/output.set_audio_enabled() methods."
            )
            return False
        except Exception as e:
            logger.error(f"Error setting hold state: {e}", exc_info=True)
            return False

    def is_on_hold(self, session_id: str) -> bool:
        return self._hold_states.get(session_id, False)


class AudioBridgeManager:
    def __init__(self, default_config: Optional[AudioConfig] = None):
        self._bridges: Dict[str, SIPAudioBridge] = {}
        self._default_config = default_config or AudioConfig()

    def get_bridge(self, session_id: str) -> SIPAudioBridge:
        if session_id not in self._bridges:
            self._bridges[session_id] = SIPAudioBridge(self._default_config)
        return self._bridges[session_id]

    def remove_bridge(self, session_id: str) -> None:
        if session_id in self._bridges:
            del self._bridges[session_id]


_audio_manager: Optional[AudioBridgeManager] = None


def get_audio_manager() -> AudioBridgeManager:
    global _audio_manager
    if _audio_manager is None:
        _audio_manager = AudioBridgeManager()
    return _audio_manager

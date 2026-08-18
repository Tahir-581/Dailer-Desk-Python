"""
Proper LiveKit thinking sounds implementation using built-in audio clips
as per LiveKit documentation: https://docs.livekit.io/agents/logic/external-data/#-thinking-sounds
"""

import logging
from typing import Optional

from livekit import rtc
from livekit.agents import AudioConfig, BackgroundAudioPlayer, BuiltinAudioClip

logger = logging.getLogger("thinking_sounds")

class LiveKitThinkingSounds:
    """Proper implementation using LiveKit's built-in BackgroundAudioPlayer"""

    def __init__(self):
        self.audio_player: Optional[BackgroundAudioPlayer] = None
        self.is_playing = False

    def initialize(self, volume: float = 0.5):
        """Initialize thinking sounds with LiveKit's built-in audio clips"""
        try:
            # Use LiveKit's built-in thinking sounds as per documentation
            self.audio_player = BackgroundAudioPlayer(
                thinking_sound=[
                    AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING, volume=volume),
                    AudioConfig(BuiltinAudioClip.KEYBOARD_TYPING2, volume=volume * 0.8),
                ],
            )
            logger.info(f"LiveKit thinking sounds initialized with built-in clips at volume {volume}")
        except Exception as e:
            logger.error(f"Failed to initialize LiveKit thinking sounds: {e}", exc_info=True)

    async def start_thinking_sound(self, room: rtc.Room, agent_session):
        """Start playing thinking sounds"""
        if not self.audio_player or self.is_playing:
            return

        try:
            await self.audio_player.start(room=room, agent_session=agent_session)
            self.is_playing = True
            logger.info("Started LiveKit thinking sounds")
        except Exception as e:
            logger.error(f"Failed to start LiveKit thinking sounds: {e}", exc_info=True)

    async def stop_thinking_sound(self):
        """Stop playing thinking sounds"""
        if not self.audio_player or not self.is_playing:
            return

        try:
            await self.audio_player.aclose()
            self.is_playing = False
            logger.info("Stopped LiveKit thinking sounds")
        except Exception as e:
            logger.error(f"Failed to stop LiveKit thinking sounds: {e}", exc_info=True)

# Global instance for easy access
_thinking_sounds_manager: Optional[LiveKitThinkingSounds] = None

def get_thinking_sounds_manager() -> LiveKitThinkingSounds:
    """Get the global thinking sounds manager instance"""
    global _thinking_sounds_manager
    if _thinking_sounds_manager is None:
        _thinking_sounds_manager = LiveKitThinkingSounds()
        _thinking_sounds_manager.initialize(volume=0.3)  # Subtle volume
    return _thinking_sounds_manager

async def cleanup_thinking_sounds():
    """Cleanup function for shutdown"""
    global _thinking_sounds_manager
    if _thinking_sounds_manager:
        try:
            await _thinking_sounds_manager.stop_thinking_sound()
            _thinking_sounds_manager = None
            logger.info("LiveKit thinking sounds cleanup completed")
        except Exception as e:
            logger.error(f"Error during LiveKit thinking sounds cleanup: {e}", exc_info=True)

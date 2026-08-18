import logging
from pathlib import Path
from typing import Optional

from livekit.agents import AudioConfig, BackgroundAudioPlayer

logger = logging.getLogger("thinking_sounds")

class ThinkingSoundsManager:
    """Manages thinking sounds that play during RAG operations and tool calls"""

    def __init__(self):
        self.thinking_audio_player: Optional[BackgroundAudioPlayer] = None
        self.is_playing = False
        self._audio_file_path = None

    def initialize(self, audio_file_path: str, volume: float = 0.3):
        """Initialize the thinking sounds player with audio file"""
        self._audio_file_path = audio_file_path
        try:
            self.thinking_audio_player = BackgroundAudioPlayer(
                ambient_sound=AudioConfig(audio_file_path, volume=volume)
            )
            logger.info(f"Thinking sounds initialized with {audio_file_path} at volume {volume}")
        except Exception as e:
            logger.error(f"Failed to initialize thinking sounds: {e}", exc_info=True)
            self.thinking_audio_player = None

    async def start_thinking_sound(self, room, agent_session):
        """Start playing thinking sound during RAG/tool operations"""
        if not self.thinking_audio_player or self.is_playing:
            return

        try:
            await self.thinking_audio_player.start(room=room, agent_session=agent_session)
            self.is_playing = True
            logger.info("Thinking sound started")
        except Exception as e:
            logger.error(f"Failed to start thinking sound: {e}", exc_info=True)

    async def stop_thinking_sound(self):
        """Stop playing thinking sound when RAG/tool operation completes"""
        if not self.thinking_audio_player or not self.is_playing:
            return

        try:
            await self.thinking_audio_player.aclose()
            self.is_playing = False
            logger.info("Thinking sound stopped")
        except Exception as e:
            logger.error(f"Failed to stop thinking sound: {e}", exc_info=True)

    async def __aenter__(self):
        """Async context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - cleanup resources"""
        if self.thinking_audio_player and self.is_playing:
            try:
                await self.thinking_audio_player.aclose()
                self.is_playing = False
            except Exception as e:
                logger.error(f"Error cleaning up thinking sounds: {e}", exc_info=True)

def get_thinking_sound_file_path() -> Optional[str]:
    """Get the path to the thinking sound audio file"""
    try:
        # Use a subtle, ambient thinking sound
        # You can replace this with any suitable audio file
        base_dir = Path(__file__).parent
        thinking_sounds_dir = base_dir / "assets" / "thinking_sounds"

        # Try to find a thinking sound file
        possible_files = [
            "thinking-ambient.mp3",
            "subtle-click.mp3",
            "processing-soft.mp3",
            "typing-subtle.mp3"
        ]

        for filename in possible_files:
            file_path = thinking_sounds_dir / filename
            if file_path.exists():
                return str(file_path)

        # Fallback to a default background audio if no thinking sound found
        background_audio_dir = base_dir / "assets" / "background_audio"
        if background_audio_dir.exists():
            # Use a very subtle background sound as fallback
            fallback_files = ["coffee-shop.mp3", "convention-hall.mp3"]
            for filename in fallback_files:
                file_path = background_audio_dir / filename
                if file_path.exists():
                    return str(file_path)

        logger.warning("No thinking sound audio files found")
        return None

    except Exception as e:
        logger.error(f"Error finding thinking sound file: {e}", exc_info=True)
        return None

# Global thinking sounds manager instance
_thinking_sounds_manager: Optional[ThinkingSoundsManager] = None

def get_thinking_sounds_manager() -> ThinkingSoundsManager:
    """Get the global thinking sounds manager instance"""
    global _thinking_sounds_manager
    if _thinking_sounds_manager is None:
        _thinking_sounds_manager = ThinkingSoundsManager()
        # Initialize with default audio file if available
        audio_file_path = get_thinking_sound_file_path()
        if audio_file_path:
            _thinking_sounds_manager.initialize(audio_file_path, volume=0.2)  # Lower volume for subtlety
    return _thinking_sounds_manager

async def cleanup_thinking_sounds():
    """Cleanup the global thinking sounds manager"""
    global _thinking_sounds_manager
    if _thinking_sounds_manager:
        try:
            await _thinking_sounds_manager.stop_thinking_sound()
            _thinking_sounds_manager = None
            logger.info("Thinking sounds manager cleaned up")
        except Exception as e:
            logger.error(f"Error cleaning up thinking sounds manager: {e}", exc_info=True)

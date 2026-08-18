import logging
import os
from typing import Dict, Optional

from livekit import api
from livekit.protocol import egress as egress_proto
from livekit.protocol.egress import AudioMixing, DirectFileOutput

logger = logging.getLogger("SIP.EgressHelper")


def get_supabase_s3_config() -> Dict[str, str]:
    access_key = os.getenv("SUPABASE_S3_ACCESS_KEY_ID")
    secret = os.getenv("SUPABASE_S3_SECRET_ACCESS_KEY")
    bucket = os.getenv("SUPABASE_STORAGE_BUCKET_NAME")
    region = os.getenv("SUPABASE_S3_REGION")
    endpoint = os.getenv("SUPABASE_S3_ENDPOINT")

    if not all([access_key, secret, bucket, endpoint]):
        raise ValueError(
            "Supabase S3 configuration is incomplete. Required: "
            "SUPABASE_S3_ACCESS_KEY_ID, SUPABASE_S3_SECRET_ACCESS_KEY, "
            "SUPABASE_STORAGE_BUCKET_NAME, SUPABASE_S3_ENDPOINT"
        )

    return {
        "access_key": access_key,
        "secret": secret,
        "bucket": bucket,
        "region": region,
        "endpoint": endpoint,
    }


async def start_track_egress(
    lk_api: api.LiveKitAPI,
    room_name: str,
    track_id: Optional[str] = None,
) -> Optional[str]:
    """
    Start manual egress for a specific track or all tracks in a room.
    
    Note: This is for manual/on-demand recording. For automatic recording,
    use auto-egress via CreateRoom API (handled in TypeScript for voice assistant rooms).
    
    Args:
        lk_api: LiveKit API client
        room_name: Name of the room to record
        track_id: Optional specific track ID to record. If None, records all tracks.
    
    Returns:
        Egress ID if successful, None otherwise
    """
    if os.getenv("LIVEKIT_EGRESS_ENABLED") != "true":
        logger.debug("Egress is disabled, skipping recording")
        return None

    try:
        s3_config = get_supabase_s3_config()

        # Create S3 upload configuration
        s3_upload = egress_proto.S3Upload()
        s3_upload.access_key = s3_config["access_key"]
        s3_upload.secret = s3_config["secret"]
        s3_upload.bucket = s3_config["bucket"]
        s3_upload.region = s3_config["region"]
        s3_upload.endpoint = s3_config["endpoint"]
        s3_upload.force_path_style = True  # Required for Supabase S3-compatible

        # Create file output (DirectFileOutput for track egress; one file per track)
        # See: https://docs.livekit.io/reference/other/egress/api/#starttrackegress
        # Track egress does NOT support audio_only/audio_mixing/file_outputs; those are for Room Composite only.
        direct_file = DirectFileOutput()
        direct_file.filepath = "recordings/tracks/{room_name}-{publisher_identity}-{time}"
        direct_file.s3.CopyFrom(s3_upload)

        request = egress_proto.TrackEgressRequest(
            room_name=room_name,
            file=direct_file,
        )
        if track_id:
            request.track_id = track_id

        # Start egress (records a single track per request → separate files per participant/agent)
        response = await lk_api.egress.start_track_egress(request)
        egress_id = response.egress_id

        logger.info(f"Started track egress for room {room_name}, egress_id: {egress_id}")
        return egress_id

    except Exception as e:
        logger.error(f"Failed to start track egress for room {room_name}: {e}")
        return None


async def start_room_composite_egress(
    lk_api: api.LiveKitAPI,
    room_name: str,
    output_format: str = "mp4",
    audio_only: bool = True,
) -> Optional[str]:
    """
    Start manual egress for room composite (all participants in one output).

    When audio_only=True (default), uses audio_only + DEFAULT_MIXING so agent and
    all participants are mixed into a single audio file (per LiveKit Egress API).

    Args:
        lk_api: LiveKit API client
        room_name: Name of the room to record
        output_format: 'mp4', 'hls', or 'both'
        audio_only: If True, record a single mixed audio (all users mixed together).
            If False, record video composite (layout + preset).

    Returns:
        Egress ID if successful, None otherwise
    """
    if os.getenv("LIVEKIT_EGRESS_ENABLED") != "true":
        logger.debug("Egress is disabled, skipping recording")
        return None

    try:
        s3_config = get_supabase_s3_config()

        s3_upload = egress_proto.S3Upload()
        s3_upload.access_key = s3_config["access_key"]
        s3_upload.secret = s3_config["secret"]
        s3_upload.bucket = s3_config["bucket"]
        s3_upload.region = s3_config["region"]
        s3_upload.endpoint = s3_config["endpoint"]
        s3_upload.force_path_style = True

        # Room composite: audio_only + DEFAULT_MIXING = single mixed audio (all participants)
        # https://docs.livekit.io/reference/other/egress/api/#startroomcompositeegress
        # https://docs.livekit.io/reference/other/egress/api/#audiomixing
        request = egress_proto.RoomCompositeEgressRequest(
            room_name=room_name,
            audio_only=audio_only,
            layout="" if audio_only else "grid",
        )
        if audio_only:
            request.audio_mixing = AudioMixing.DEFAULT_MIXING
        else:
            request.preset = egress_proto.EncodingOptionsPreset.H264_720P_30

        if output_format in ["mp4", "both"]:
            file_output = egress_proto.EncodedFileOutput()
            file_output.filepath = f"recordings/composites/{room_name}-{{time}}.mp4"
            file_output.s3.CopyFrom(s3_upload)
            request.file_outputs.append(file_output)

        if output_format in ["hls", "both"]:
            segment_output = egress_proto.SegmentedFileOutput()
            segment_output.filename_prefix = f"recordings/composites/{room_name}"
            segment_output.playlist_name = f"{room_name}.m3u8"
            segment_output.segment_duration = 2
            segment_output.s3.CopyFrom(s3_upload)
            request.segment_outputs.append(segment_output)

        response = await lk_api.egress.start_room_composite_egress(request)
        egress_id = response.egress_id

        logger.info(
            f"Started room composite egress for room {room_name}, "
            f"egress_id: {egress_id}, format: {output_format}"
        )
        return egress_id

    except Exception as e:
        logger.error(f"Failed to start room composite egress for room {room_name}: {e}")
        return None


async def stop_egress(lk_api: api.LiveKitAPI, egress_id: str) -> bool:
    """
    Stop an active egress recording.
    
    Args:
        lk_api: LiveKit API client
        egress_id: ID of the egress to stop
    
    Returns:
        True if successful, False otherwise
    """
    try:
        request = egress_proto.StopEgressRequest(egress_id=egress_id)
        await lk_api.egress.stop_egress(request)
        logger.info(f"Stopped egress: {egress_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to stop egress {egress_id}: {e}")
        return False




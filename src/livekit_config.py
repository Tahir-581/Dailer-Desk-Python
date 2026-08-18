"""
Fetch LiveKit credentials per organization from the dashboard API.
Used by the agent when org_id is present in job metadata so STT/TTS use org-specific credentials.
"""

import json
import logging
import os
import urllib.request

logger = logging.getLogger("agent")

# Default timeout for the credentials request
CREDENTIALS_REQUEST_TIMEOUT = 5.0


def get_livekit_credentials_for_org(org_id: str) -> dict[str, str] | None:
    """
    Fetch LiveKit credentials for the given org_id from the dashboard API.
    Returns dict with keys livekit_url, livekit_api_key, livekit_api_secret, or None on failure.
    """
    base_url = os.getenv("NEXTJS_API_URL", "http://localhost:3000").rstrip("/")
    secret = os.getenv("AGENT_SERVICE_SECRET")
    if not secret:
        logger.debug("AGENT_SERVICE_SECRET not set, skipping per-org credentials fetch")
        return None

    url = f"{base_url}/api/internal/livekit-credentials"
    headers = {"X-Agent-Service-Secret": secret}

    try:
        req = urllib.request.Request(
            f"{url}?orgId={org_id}",
            headers=headers,
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=CREDENTIALS_REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode())
            if not data.get("livekitApiKey") or not data.get("livekitApiSecret"):
                return None
            return {
                "livekit_url": data.get("livekitUrl", ""),
                "livekit_api_key": data["livekitApiKey"],
                "livekit_api_secret": data["livekitApiSecret"],
            }
    except Exception as e:
        logger.warning("Failed to fetch LiveKit credentials for org %s: %s", org_id, e)
        return None

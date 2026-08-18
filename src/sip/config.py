

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


class SIPTransport(str, Enum):
    AUTO = "SIP_TRANSPORT_AUTO"
    UDP = "SIP_TRANSPORT_UDP"
    TCP = "SIP_TRANSPORT_TCP"
    TLS = "SIP_TRANSPORT_TLS"


class CallDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    UNKNOWN = "unknown"


class CallState(str, Enum):
    INITIALIZING = "initializing"
    RINGING = "ringing"
    CONNECTING = "connecting"
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    TRANSFERRING = "transferring"
    DISCONNECTING = "disconnecting"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


class TransferType(str, Enum):
    COLD = "cold"
    WARM = "warm"


class DispatchRuleType(str, Enum):
    INDIVIDUAL = "individual"
    DIRECT = "direct"


@dataclass
class SIPProviderConfig:
    name: str
    address: str
    auth_username: Optional[str] = None
    auth_password: Optional[str] = None
    transport: SIPTransport = SIPTransport.AUTO
    region: Optional[str] = None
    destination_country: Optional[str] = None
    allowed_source_ips: List[str] = field(default_factory=list)
    custom_headers: Dict[str, str] = field(default_factory=dict)


class SIPMediaEncryption(Enum):
    DISABLE = 0
    REQUIRE = 1
    ALLOW = 2


@dataclass
class InboundTrunkConfig:
    trunk_id: str
    name: str
    numbers: List[str]
    allowed_source_ips: List[str] = field(default_factory=list)
    allowed_numbers: List[str] = field(default_factory=list)
    auth_username: Optional[str] = None
    auth_password: Optional[str] = None
    krisp_enabled: bool = False
    media_encryption: Optional[SIPMediaEncryption] = None
    ringing_timeout: Optional[int] = None
    headers_to_attributes: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundTrunkConfig:
    trunk_id: str
    name: str
    address: str
    numbers: List[str]
    auth_username: str = ""
    auth_password: str = ""
    transport: SIPTransport = SIPTransport.AUTO
    destination_country: Optional[str] = None
    media_encryption: Optional[SIPMediaEncryption] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DispatchRule:
    rule_id: str
    name: str
    rule_type: DispatchRuleType
    room_prefix: Optional[str] = None
    room_name: Optional[str] = None
    pin: Optional[str] = None
    trunk_ids: List[str] = field(default_factory=list)
    inbound_numbers: List[str] = field(default_factory=list)
    agent_name: Optional[str] = None
    hide_phone_number: bool = False
    attributes: Dict[str, str] = field(default_factory=dict)
    room_config: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SIPParticipantInfo:
    participant_id: str
    identity: str
    room_name: str
    phone_number: Optional[str] = None
    call_from: Optional[str] = None
    call_to: Optional[str] = None
    trunk_id: Optional[str] = None
    direction: CallDirection = CallDirection.UNKNOWN
    state: CallState = CallState.INITIALIZING
    created_at: datetime = field(default_factory=datetime.now)
    connected_at: Optional[datetime] = None
    disconnected_at: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> Optional[float]:
        if not self.connected_at:
            return None
        end_time = self.disconnected_at or datetime.now()
        return (end_time - self.connected_at).total_seconds()


@dataclass
class SIPCallSession:
    session_id: str
    room_name: str
    participant_info: SIPParticipantInfo
    direction: CallDirection = CallDirection.UNKNOWN
    state: CallState = CallState.INITIALIZING
    transfer_history: List[Dict[str, Any]] = field(default_factory=list)
    agent_id: Optional[str] = None
    workflow_id: Optional[str] = None
    org_id: Optional[str] = None
    last_error: Optional[str] = None
    error_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def update_state(self, new_state: CallState) -> None:
        self.state = new_state
        self.updated_at = datetime.now()
        self.participant_info.state = new_state


@dataclass
class OutboundCallRequest:
    phone_number: str
    trunk_id: str
    room_name: Optional[str] = None
    participant_identity: Optional[str] = None
    participant_name: Optional[str] = None
    display_name: Optional[str] = None
    caller_id: Optional[str] = None
    dtmf: Optional[str] = None
    wait_until_answered: bool = True
    play_ringtone: bool = True
    krisp_enabled: bool = True
    max_call_duration: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)
    participant_attributes: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    agent_id: Optional[str] = None
    workflow_id: Optional[str] = None
    org_id: Optional[str] = None


@dataclass
class TransferRequest:
    transfer_to: str
    transfer_type: TransferType = TransferType.COLD
    play_dialtone: bool = False
    whisper_message: Optional[str] = None
    hold_music_enabled: bool = True
    custom_headers: Dict[str, str] = field(default_factory=dict)


class SIPConfig:
    def __init__(self):
        self.livekit_url = os.getenv("LIVEKIT_URL", "")
        self.livekit_api_key = os.getenv("LIVEKIT_API_KEY", "")
        self.livekit_api_secret = os.getenv("LIVEKIT_API_SECRET", "")
        self.default_inbound_trunk_id = os.getenv("SIP_INBOUND_TRUNK_ID", "")
        self.default_outbound_trunk_id = os.getenv("SIP_OUTBOUND_TRUNK_ID", "")
        self.sip_provider = os.getenv("SIP_PROVIDER", "")
        self.sip_domain = os.getenv("SIP_DOMAIN", "")
        self.enable_warm_transfer = (
            os.getenv("SIP_ENABLE_WARM_TRANSFER", "false").lower() == "true"
        )
        self.enable_dtmf = os.getenv("SIP_ENABLE_DTMF", "true").lower() == "true"
        self.audio_sample_rate = int(os.getenv("SIP_AUDIO_SAMPLE_RATE", "48000"))
        self.audio_channels = int(os.getenv("SIP_AUDIO_CHANNELS", "1"))

    @property
    def is_configured(self) -> bool:
        return bool(
            self.livekit_url and self.livekit_api_key and self.livekit_api_secret
        )

    def validate(self) -> List[str]:
        issues = []

        if not self.livekit_url:
            issues.append("LIVEKIT_URL environment variable is not set")
        elif not validate_url(self.livekit_url):
            issues.append(f"LIVEKIT_URL is not a valid URL: {self.livekit_url}")

        if not self.livekit_api_key:
            issues.append("LIVEKIT_API_KEY environment variable is not set")
        elif len(self.livekit_api_key) < 10:
            issues.append("LIVEKIT_API_KEY appears to be invalid (too short)")

        if not self.livekit_api_secret:
            issues.append("LIVEKIT_API_SECRET environment variable is not set")
        elif len(self.livekit_api_secret) < 10:
            issues.append("LIVEKIT_API_SECRET appears to be invalid (too short)")

        if self.default_outbound_trunk_id and not self.default_outbound_trunk_id.strip():
            issues.append("SIP_OUTBOUND_TRUNK_ID is set but empty")

        if self.default_inbound_trunk_id and not self.default_inbound_trunk_id.strip():
            issues.append("SIP_INBOUND_TRUNK_ID is set but empty")

        if self.audio_sample_rate not in [8000, 16000, 24000, 48000]:
            issues.append(
                f"SIP_AUDIO_SAMPLE_RATE should be one of [8000, 16000, 24000, 48000], got {self.audio_sample_rate}"
            )

        if self.audio_channels not in [1, 2]:
            issues.append(
                f"SIP_AUDIO_CHANNELS should be 1 or 2, got {self.audio_channels}"
            )

        return issues


_sip_config: Optional[SIPConfig] = None


def get_sip_config() -> SIPConfig:
    global _sip_config
    if _sip_config is None:
        _sip_config = SIPConfig()
    return _sip_config








SUPPORTED_SIP_REGIONS = ["eu", "india", "sa", "us", "aus", "uk"]



COUNTRY_TO_REGION_MAP = {

    "fr": "eu",
    "de": "eu",
    "ch": "eu",
    "gb": "uk",

    "in": "india",
    "au": "aus",

    "sa": "sa",

    "us": "us",
    "ca": "us",
}


def validate_country_code(country_code: str) -> tuple[bool, Optional[str]]:
    if not country_code:
        return True, None

    country_code = country_code.strip().lower()

    if len(country_code) != 2:
        return False, f"Country code must be exactly 2 letters (ISO 3166-1 alpha-2), got '{country_code}'"

    if not country_code.isalpha():
        return False, f"Country code must contain only letters, got '{country_code}'"

    return True, None







E164_PATTERN = re.compile(r"^\+[1-9]\d{1,14}$")


def validate_e164_phone_number(phone_number: str) -> tuple[bool, Optional[str]]:


    cleaned = phone_number.strip()
    if cleaned.startswith("tel:"):
        cleaned = cleaned[4:]
    elif cleaned.startswith("sip:"):
        return False, "SIP URI provided instead of phone number"

    cleaned = re.sub(r"[\s\-\(\)]", "", cleaned)

    if not cleaned.startswith("+"):
        return False, "Phone number must start with '+' (E.164 format)"


    if not E164_PATTERN.match(cleaned):
        return False, f"Invalid E.164 format: {cleaned}. Expected format: +[country code][number] (max 15 digits)"

    if len(cleaned) < 8:
        return False, f"Phone number too short: {cleaned}. Minimum length is 8 characters (including +)"

    return True, None


def normalize_phone_number(phone_number: str) -> str:
    cleaned = phone_number.strip()

    if cleaned.startswith("tel:"):
        return cleaned
    if cleaned.startswith("sip:"):
        return cleaned

    cleaned = re.sub(r"[\s\-\(\)\.]", "", cleaned)

    if cleaned.startswith("tel:"):
        cleaned = cleaned[4:]

    if cleaned.startswith("+"):
        if len(cleaned) > 16:
            raise ValueError(f"Phone number too long: {cleaned}")
        if len(cleaned) < 8:
            raise ValueError(f"Phone number too short: {cleaned}")
        return f"tel:{cleaned}"

    if cleaned.isdigit():
        if len(cleaned) == 10:
            return f"tel:+1{cleaned}"

        elif (len(cleaned) == 11 and cleaned.startswith("1")) or (len(cleaned) >= 7 and len(cleaned) <= 15):
            return f"tel:+{cleaned}"
        else:
            raise ValueError(f"Cannot normalize phone number: {phone_number}")

    raise ValueError(f"Invalid phone number format: {phone_number}")


def validate_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except Exception:
        return False

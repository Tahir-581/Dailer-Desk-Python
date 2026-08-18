

from typing import Optional


class SIPError(Exception):
    pass


class SIPTrunkError(SIPError):
    pass


class SIPTrunkNotFoundError(SIPTrunkError):
    def __init__(self, trunk_id: str):
        super().__init__(f"SIP trunk not found: {trunk_id}")
        self.trunk_id = trunk_id


class SIPTrunkConfigurationError(SIPTrunkError):
    pass


class SIPCallError(SIPError):
    pass


class SIPCallInitiationError(SIPCallError):
    def __init__(self, message: str, sip_status_code: Optional[int] = None, sip_status: Optional[str] = None):
        super().__init__(message)
        self.sip_status_code = sip_status_code
        self.sip_status = sip_status


class SIPCallTransferError(SIPCallError):
    def __init__(self, message: str, sip_status_code: Optional[int] = None, sip_status: Optional[str] = None):
        super().__init__(message)
        self.sip_status_code = sip_status_code
        self.sip_status = sip_status


class SIPParticipantError(SIPError):
    pass


class SIPParticipantNotFoundError(SIPParticipantError):
    pass


class SIPDispatchError(SIPError):
    pass


class SIPDispatchRuleNotFoundError(SIPDispatchError):
    pass


class SIPRegistrationError(SIPError):
    pass


class SIPSessionError(SIPError):
    pass


class SIPSessionNotFoundError(SIPSessionError):
    def __init__(self, session_id: str):
        super().__init__(f"SIP session not found: {session_id}")
        self.session_id = session_id


class SIPSessionAlreadyExistsError(SIPSessionError):
    def __init__(self, session_id: str):
        super().__init__(f"SIP session already exists: {session_id}")
        self.session_id = session_id


class SIPAudioBridgeError(SIPError):
    pass

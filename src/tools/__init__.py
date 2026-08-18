from .call_tools import end_call, generic_service, transfer_call
from .outbound_call_tool import make_outbound_call
from .rag_tool import search_knowledge_base
from .workflow_tools import get_workflow_state, transition_to_next_node

try:
    from sip.tools import (
        SIP_TOOLS,
        sip_dial,
        sip_get_call_info,
        sip_hold,
        sip_send_dtmf,
        sip_transfer,
    )
    _sip_available = True
except ImportError:

    _sip_available = False
    SIP_TOOLS = []

__all__ = [
    "end_call",
    "generic_service",
    "get_workflow_state",
    "make_outbound_call",
    "search_knowledge_base",
    "transfer_call",
    "transition_to_next_node",
]


if _sip_available:
    __all__.extend([
        "SIP_TOOLS",
        "sip_dial",
        "sip_get_call_info",
        "sip_hold",
        "sip_send_dtmf",
        "sip_transfer",
    ])

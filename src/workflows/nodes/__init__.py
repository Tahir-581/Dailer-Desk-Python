

from .agent_transfer_node import AgentTransferNode
from .call_transfer_node import CallTransferNode
from .conversation_node import ConversationNode
from .end_call_node import EndCallNode
from .extract_variable_node import ExtractVariableNode
from .logic_split_node import LogicSplitNode
from .sms_node import SMSNode

try:
    from .press_digit_node import PressDigitNode
except ImportError:

    class PressDigitNode:
        """Fallback stub when optional press_digit_node is unavailable."""

        def __init__(self, *args, **kwargs):
            """Initialize a no-op PressDigitNode."""
            pass

        def execute(self, *args, **kwargs):
            """Execute a no-op press digit action."""
            pass

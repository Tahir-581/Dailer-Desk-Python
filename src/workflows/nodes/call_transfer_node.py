import logging
from typing import Any, Dict

from livekit.agents.llm import ToolError
from livekit.agents.voice import RunContext

from tools.call_tools import transfer_call

from ..node_base import WorkflowNode

logger = logging.getLogger("workflow.node.call_transfer")


class CallTransferNode(WorkflowNode):

    def __init__(self, node_id: str, config: Dict[str, Any]):

        super().__init__(node_id, "call-transfer", config)
        self.play_dialtone = config.get("play_dialtone", True)
        logger.info(f"Call transfer node {node_id} initialized")

    async def execute(self, context: Dict[str, Any]) -> str:
        session = context.get("session")

        if not session:
            raise ValueError("Session is required in context for call transfer node")

        destination = self.config.get("destination")
        if not destination:
             logger.warning(f"No destination configured for CallTransferNode {self.node_id}. Will attempt to use tool default if available.")

        run_context = RunContext(session=session)

        try:
            result = await transfer_call(context=run_context, play_dialtone=self.play_dialtone, destination=destination)
            logger.info(f"Call transfer completed for node {self.node_id}: {result}")
            return result

        except ToolError as e:
            logger.error(f"Call transfer failed for node {self.node_id}: {e}")
            raise

        except Exception as e:
            logger.error(f"Unexpected error in call transfer node {self.node_id}: {e}", exc_info=True)
            raise ValueError(f"Call transfer failed: {e!s}")
